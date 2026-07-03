#!/usr/bin/env python3
"""
准确率统计仪表盘

用法:
    python scripts/show_stats.py
    python scripts/show_stats.py --timeframe 短期
"""

import sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.data.prediction_store import PredictionStore


def main():
    import argparse
    parser = argparse.ArgumentParser(description="预测准确率仪表盘")
    parser.add_argument("--timeframe", "-f", default=None, help="筛选时间维度")
    args = parser.parse_args()

    store = PredictionStore()

    # 总览
    total = store.get_unverified_count()
    verified = store.get_predictions(verified_only=True, limit=10000)

    print()
    print("╔" + "═" * 58 + "╗")
    print("║" + "  📊 预测准确率统计仪表盘".ljust(48) + "║")
    print("╠" + "═" * 58 + "╣")

    summary = store.get_accuracy_stats(timeframe=args.timeframe)
    print(f"║  已验证预测: {summary['total']:>4} 次"
          f"  |  待验证: {total:>4} 次".ljust(49) + "║")
    print("╠" + "═" * 58 + "╣")

    if summary["total"] == 0:
        print("║  尚无已验证的预测数据".ljust(49) + "║")
    else:
        print(f"║  综合方向准确率: {summary['direction_accuracy']:.1%}".ljust(49) + "║")
        print(f"║  综合幅度命中率: {summary['magnitude_accuracy']:.1%}".ljust(49) + "║")
        print(f"║  平均置信度:     {summary['avg_confidence']:.1%}".ljust(49) + "║")
        print(f"║  平均误差:       {summary['avg_error_pct']:.2f}%".ljust(49) + "║")

    print("╠" + "═" * 58 + "╣")

    # 按时间维度
    by_tf = store.get_stats_by_timeframe()
    if any(s["total"] > 0 for s in by_tf.values()):
        print("║  按时间维度:".ljust(49) + "║")
        for tf, stats in by_tf.items():
            if stats["total"] > 0:
                print(f"║    {tf}: 方向{stats['direction_accuracy']:.0%} | "
                      f"幅度{stats['magnitude_accuracy']:.0%} | "
                      f"{stats['total']}次".ljust(49) + "║")

    print("╠" + "═" * 58 + "╣")

    # 按 Agent
    by_agent = store.get_stats_by_agent(timeframe=args.timeframe)
    if any(s["total"] > 0 for s in by_agent.values()):
        print("║  各 Agent 方向准确率:".ljust(49) + "║")
        for name, stats in sorted(by_agent.items(), key=lambda x: -x[1].get("direction_accuracy", 0)):
            if stats["total"] > 0:
                print(f"║    {name}: {stats['direction_accuracy']:.0%}"
                      f" ({stats['total']}次)".ljust(49) + "║")

    print("╚" + "═" * 58 + "╝")
    print()


if __name__ == "__main__":
    main()
