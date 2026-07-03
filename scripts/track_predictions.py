#!/usr/bin/env python3
"""
预测追踪脚本 — 验证过期的预测

用法:
    python scripts/track_predictions.py
    python scripts/track_predictions.py --target 000001
"""

import sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.data.prediction_store import PredictionStore
from src.utils.logger import setup_logging, get_logger


def main():
    import argparse
    parser = argparse.ArgumentParser(description="验证过期预测")
    parser.add_argument("--target", "-t", default=None)
    args = parser.parse_args()

    setup_logging()
    logger = get_logger("track")

    store = PredictionStore()

    if args.target:
        verified = store.verify_predictions(args.target)
        logger.info(f"✅ {args.target}: 验证了 {verified} 条预测")
    else:
        result = store.verify_all()
        logger.info(f"✅ 全部标的: 验证了 {result['verified']} 条预测")

    # 显示最新统计
    stats = store.get_accuracy_stats()
    if stats["total"] > 0:
        logger.info(
            f"当前方向准确率: {stats['direction_accuracy']:.1%} "
            f"({stats['total']} 次已验证)"
        )


if __name__ == "__main__":
    main()
