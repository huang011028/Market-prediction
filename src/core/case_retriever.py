"""
RAG 历史案例检索器

将已验证的历史预测案例索引到 ChromaDB，
在汇总分析前检索相似案例作为参考。
"""

import json
import logging
from typing import Optional

from src.data.prediction_store import PredictionStore

logger = logging.getLogger(__name__)


class CaseRetriever:
    """RAG 历史案例检索器

    将历史预测提取特征文本 → LLM 嵌入 → ChromaDB 存储，
    新预测时检索相似案例。
    """

    def __init__(self, store: Optional[PredictionStore] = None):
        self.store = store or PredictionStore()
        self._collection = None

    # ================================================================
    # 索引
    # ================================================================

    def index_predictions(self, force_rebuild: bool = False):
        """将所有已验证的预测索引到 ChromaDB

        对每条已验证预测：
        1. 提取特征描述文本
        2. 生成嵌入向量（使用 LLM 或简单文本）
        3. 存入向量库
        """
        try:
            import chromadb
            client = chromadb.PersistentClient(
                path=str(self.store.db_path).replace(".db", "_chroma")
            )
            self._collection = client.get_or_create_collection("prediction_cases")
        except ImportError:
            logger.warning("chromadb 未安装，RAG 检索不可用。pip install chromadb")
            return
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败: {e}")
            return

        # 获取已验证的预测
        records = self.store.get_predictions(verified_only=True, limit=500)
        if not records:
            logger.info("没有已验证的预测可供索引")
            return

        indexed = 0
        for rec in records:
            pid = rec.id
            # 检查是否已索引
            existing = self._collection.get(ids=[pid])
            if existing and existing["ids"] and not force_rebuild:
                continue

            feat_text = self._extract_features(rec)
            if not feat_text:
                continue

            try:
                self._collection.add(
                    ids=[pid],
                    documents=[feat_text],
                    metadatas=[{
                        "target": rec.target,
                        "timeframe": rec.timeframe,
                        "predicted_direction": rec.direction,
                        "actual_direction": rec.actual_direction or "",
                        "actual_change_pct": rec.actual_change_pct or 0,
                        "direction_correct": rec.direction_correct or 0,
                    }],
                )
                indexed += 1
            except Exception as e:
                logger.debug(f"索引 {pid} 失败: {e}")

        logger.info(f"RAG 索引完成: {indexed} 条案例")

    # ================================================================
    # 检索
    # ================================================================

    def retrieve_similar(
        self,
        target: str,
        timeframe: str,
        current_features: dict,
        top_k: int = 3,
    ) -> list[dict]:
        """检索与当前情况最相似的历史案例

        Returns:
            [{"target": "...", "direction_correct": True, "similarity": 0.87, ...}, ...]
        """
        if self._collection is None:
            try:
                import chromadb
                client = chromadb.PersistentClient(
                    path=str(self.store.db_path).replace(".db", "_chroma")
                )
                self._collection = client.get_or_create_collection("prediction_cases")
            except Exception:
                return []

        feat_text = self._features_to_text(target, timeframe, current_features)

        try:
            results = self._collection.query(
                query_texts=[feat_text],
                n_results=top_k,
            )

            cases = []
            if results and results["ids"] and results["ids"][0]:
                for i, pid in enumerate(results["ids"][0]):
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    dist = results["distances"][0][i] if results["distances"] else 1.0
                    cases.append({
                        "prediction_id": pid,
                        "target": meta.get("target", ""),
                        "timeframe": meta.get("timeframe", ""),
                        "predicted_direction": meta.get("predicted_direction", ""),
                        "actual_direction": meta.get("actual_direction", ""),
                        "actual_change_pct": meta.get("actual_change_pct", 0),
                        "direction_correct": meta.get("direction_correct", 0),
                        "similarity": round(1 - min(dist, 1), 3),
                    })
            return cases

        except Exception as e:
            logger.debug(f"RAG 检索失败: {e}")
            return []

    # ================================================================
    # 特征提取
    # ================================================================

    def _extract_features(self, record) -> str:
        """从 PredictionRecord 提取特征文本"""
        parts = [
            f"标的: {record.target}",
            f"方向: {record.direction}",
            f"置信度: {record.confidence:.0%}",
            f"时间: {record.predicted_at[:10]}",
        ]
        if record.actual_change_pct is not None:
            parts.append(f"实际涨跌: {record.actual_change_pct:+.1f}%")
            parts.append(f"方向正确: {'是' if record.direction_correct else '否'}")
        return " | ".join(parts)

    def _features_to_text(self, target: str, timeframe: str, features: dict) -> str:
        """将当前特征转为检索文本"""
        parts = [f"标的: {target}", f"周期: {timeframe}"]
        for k, v in features.items():
            parts.append(f"{k}: {v}")
        return " | ".join(parts)

    # ================================================================
    # 上下文构建
    # ================================================================

    def build_case_context(self, cases: list[dict]) -> str:
        """将检索到的案例格式化为 LLM 可读的上下文"""
        if not cases:
            return ""

        lines = [
            "",
            "## 📚 历史相似案例参考",
            "",
            f"以下是与当前分析情况最相似的 {len(cases)} 个历史案例及其验证结果。",
            "这些案例来自系统过去的预测，事后已经验证了对错。",
            "",
        ]

        for i, c in enumerate(cases, 1):
            dir_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(
                c["predicted_direction"], "❓"
            )
            actual_emoji = "✅" if c["direction_correct"] else "❌"

            lines.append(
                f"### 案例 {i}: {c['target']} — {c.get('timeframe', 'N/A')}"
                f"（相似度 {c['similarity']:.0%}）"
            )
            lines.append(
                f"- 当时预测: {dir_emoji} {c['predicted_direction']}，"
                f"实际: {c['actual_direction']} {c['actual_change_pct']:+.1f}% → {actual_emoji}"
            )
            lines.append("")

        lines.append("> ⚠️ 历史案例仅供参考。市场环境可能不同，请以当前实际分析为准。")
        lines.append("")

        return "\n".join(lines)
