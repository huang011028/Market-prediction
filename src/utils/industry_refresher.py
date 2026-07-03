"""
行业参考值定期刷新器

定期从东方财富行业板块获取所有行业实时估值数据，
更新缓存文件，替代可能过时的硬编码常量。

使用方式:
1. 手动调用: await refresher.refresh()
2. 定时任务: 配置 cron 每周执行一次
"""

import logging
import json
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class IndustryReferenceRefresher:
    """行业参考值刷新器"""

    CACHE_FILE = "config/industry_reference_cache.json"

    async def refresh(self) -> dict:
        """
        从东方财富行业板块获取所有行业实时估值。

        Returns:
            所有行业的参考值字典
            {"银行": {"pe": 5.8, "pe_median": 5.5, "pb": 0.65, "stock_count": 42}, ...}
        """
        try:
            import akshare as ak

            # 获取所有行业板块名称
            df_names = ak.stock_board_industry_name_em()
            if df_names is None or df_names.empty:
                logger.warning("获取行业板块列表失败")
                return {}

            if "板块名称" not in df_names.columns:
                logger.warning("行业板块列表格式异常")
                return {}

            industry_names = df_names["板块名称"].tolist()
            reference = {}
            success_count = 0

            for ind_name in industry_names:
                try:
                    df = ak.stock_board_industry_cons_em(symbol=ind_name)
                    if df is not None and not df.empty:
                        # 提取 PE/PB
                        pe_values = []
                        pb_values = []

                        for _, row in df.iterrows():
                            pe = _safe_float(row.get("市盈率-动态"))
                            pb = _safe_float(row.get("市净率"))
                            if pe and pe > 0:
                                pe_values.append(pe)
                            if pb and pb > 0:
                                pb_values.append(pb)

                        if pe_values:
                            pe_sorted = sorted(pe_values)
                            reference[ind_name] = {
                                "pe": round(sum(pe_values) / len(pe_values), 2),
                                "pe_median": round(pe_sorted[len(pe_sorted) // 2], 2),
                                "pb": round(sum(pb_values) / len(pb_values), 2) if pb_values else None,
                                "stock_count": len(pe_values),
                            }
                            success_count += 1

                except Exception as e:
                    logger.debug(f"刷新行业 {ind_name} 失败: {e}")
                    continue

            # 保存缓存
            if reference:
                cache_data = {
                    "updated_at": datetime.now().isoformat(),
                    "data": reference,
                }
                cache_dir = os.path.dirname(self.CACHE_FILE)
                if cache_dir and not os.path.exists(cache_dir):
                    os.makedirs(cache_dir, exist_ok=True)
                with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)

                logger.info(
                    f"行业参考值刷新完成: {success_count}/{len(industry_names)} 个行业"
                )
            else:
                logger.warning("行业参考值刷新: 未获取到任何数据")

            return reference

        except Exception as e:
            logger.error(f"行业参考值刷新异常: {e}")
            return {}

    async def refresh_specific(self, industry_names: list[str]) -> dict:
        """
        刷新指定行业的参考值。

        Args:
            industry_names: 行业名称列表，如 ["银行", "白酒"]

        Returns:
            刷新后的参考值字典
        """
        try:
            import akshare as ak

            reference = {}

            for ind_name in industry_names:
                try:
                    df = ak.stock_board_industry_cons_em(symbol=ind_name)
                    if df is not None and not df.empty:
                        pe_values = []
                        pb_values = []

                        for _, row in df.iterrows():
                            pe = _safe_float(row.get("市盈率-动态"))
                            pb = _safe_float(row.get("市净率"))
                            if pe and pe > 0:
                                pe_values.append(pe)
                            if pb and pb > 0:
                                pb_values.append(pb)

                        if pe_values:
                            pe_sorted = sorted(pe_values)
                            reference[ind_name] = {
                                "pe": round(sum(pe_values) / len(pe_values), 2),
                                "pe_median": round(pe_sorted[len(pe_sorted) // 2], 2),
                                "pb": round(sum(pb_values) / len(pb_values), 2) if pb_values else None,
                                "stock_count": len(pe_values),
                            }
                except Exception as e:
                    logger.debug(f"刷新行业 {ind_name} 失败: {e}")
                    continue

            # 更新缓存（合并而非覆盖）
            if reference:
                existing = self._load_cache()
                existing.update(reference)
                cache_data = {
                    "updated_at": datetime.now().isoformat(),
                    "data": existing,
                }
                with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)

            return reference

        except Exception as e:
            logger.error(f"指定行业刷新异常: {e}")
            return {}

    def _load_cache(self) -> dict:
        """加载当前缓存"""
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("data", {})
        except Exception:
            pass
        return {}


def _safe_float(value) -> Optional[float]:
    """安全转换为 float"""
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None
    except (ValueError, TypeError):
        return None
