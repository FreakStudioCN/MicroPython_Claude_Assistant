#!/usr/bin/env python3
# analyze_probe.py —— 离线分析 hook_probe.py 收的 jsonl
#
# 目的：回答 v1 doc §A-5 / Fix-2/3/4：
#   "用户在 Stop 后 < COMPLETED_HOLD_S(2s) 内发新 prompt 的占比是多少？"
#   - > 20% → Fix-2/3/4 是真问题，PR commit 6e81675
#   - < 5%  → 修空气，撤掉 commit
#
# 顺便统计 StopFailure 实战触发次数（A-1 已经在 docs 层 CONFIRMED 需要修，这里只看频率参考）。
#
# 用法：
#   python scripts/analyze_probe.py
#   python scripts/analyze_probe.py --log ~/.claude_buddy/probe.jsonl --threshold 2.0

import argparse
import json
import os
import sys
from collections import defaultdict

DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".claude_buddy", "probe.jsonl")
DEFAULT_THRESHOLD_S = 2.0  # COMPLETED_HOLD_S in ble_daemon.py


def _iter_events(log_path):
    """逐行读 jsonl，跳过解析失败行。返回 (ts, event_name, session_id, payload) 元组流。"""
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("stage") != "ok":
                continue
            ts = record.get("ts")
            payload = record.get("payload", {})
            event_name = payload.get("hook_event_name", record.get("event_name", ""))
            session_id = payload.get("session_id", "")
            yield ts, event_name, session_id, payload


def analyze(log_path, threshold_s):
    if not os.path.exists(log_path):
        print(f"ERROR: log file not found: {log_path}", file=sys.stderr)
        print("Hint: 先把 hooks/probe_hooks.json 临时启用，跑日常工作攒数据。", file=sys.stderr)
        return 1

    events_by_session = defaultdict(list)  # session_id -> [(ts, name)]
    stop_count = 0
    stop_failure_count = 0
    stop_failure_matchers = defaultdict(int)
    user_prompt_count = 0

    for ts, name, sid, payload in _iter_events(log_path):
        if not ts or not name:
            continue
        events_by_session[sid].append((ts, name))
        if name == "Stop":
            stop_count += 1
        elif name == "StopFailure":
            stop_failure_count += 1
            matcher = payload.get("matcher", payload.get("error_type", "unknown"))
            stop_failure_matchers[matcher] += 1
        elif name == "UserPromptSubmit":
            user_prompt_count += 1

    if user_prompt_count == 0 and stop_count == 0:
        print("WARNING: 0 events captured. probe 没装上或者还没产生过 turn。", file=sys.stderr)
        return 1

    # A-5: 算 Stop → UserPromptSubmit 间隔
    short_interval_count = 0
    total_intervals = 0
    intervals = []
    for sid, evs in events_by_session.items():
        evs.sort()
        last_stop_ts = None
        for ts, name in evs:
            if name in ("Stop", "StopFailure"):
                last_stop_ts = ts
            elif name == "UserPromptSubmit" and last_stop_ts is not None:
                delta = ts - last_stop_ts
                if delta < 0:
                    continue  # 时钟乱序，跳过
                intervals.append(delta)
                total_intervals += 1
                if delta < threshold_s:
                    short_interval_count += 1
                last_stop_ts = None  # 一对一配对，避免一个 stop 匹配多个 prompt

    pct = (short_interval_count / total_intervals * 100) if total_intervals else 0.0

    # 区间分布
    bucket_edges = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0]
    buckets = [0] * (len(bucket_edges) + 1)
    for d in intervals:
        placed = False
        for i, edge in enumerate(bucket_edges):
            if d < edge:
                buckets[i] += 1
                placed = True
                break
        if not placed:
            buckets[-1] += 1

    # 输出
    print("=" * 60)
    print(f"Probe Analysis — {log_path}")
    print("=" * 60)
    print(f"events: UserPromptSubmit={user_prompt_count}, Stop={stop_count}, StopFailure={stop_failure_count}")
    print()

    print(f"--- A-1 StopFailure 实战触发频率（顺便测，A-1 修复已不依赖此） ---")
    if stop_failure_count == 0:
        print("  自然累积期间 StopFailure 触发次数: 0 (没自然撞到，但 docs 已证它存在)")
    else:
        print(f"  StopFailure 总数: {stop_failure_count}")
        for matcher, cnt in sorted(stop_failure_matchers.items(), key=lambda x: -x[1]):
            print(f"    {matcher:>25}: {cnt}")
        if stop_count > 0:
            ratio = stop_failure_count / (stop_count + stop_failure_count) * 100
            print(f"  StopFailure / (Stop + StopFailure) = {ratio:.1f}%")
    print()

    print(f"--- A-5 / Fix-2/3/4 连发 prompt 间隔（阈值 {threshold_s}s = COMPLETED_HOLD_S） ---")
    if total_intervals == 0:
        print("  WARNING: 没采集到 Stop→UserPromptSubmit 配对。再跑一段时间。")
    else:
        print(f"  配对数 (Stop/StopFailure → UserPromptSubmit): {total_intervals}")
        print(f"  间隔 < {threshold_s}s 的占比: {short_interval_count}/{total_intervals} = {pct:.1f}%")
        print()
        print(f"  分布:")
        edges_print = ["<0.5s", "<1s", "<2s", "<5s", "<10s", "<30s", "<60s", "<5min", ">=5min"]
        for label, cnt in zip(edges_print, buckets):
            bar = "█" * min(40, cnt * 40 // max(buckets) if max(buckets) else 1)
            print(f"    {label:>10}: {cnt:>4}  {bar}")
        print()

        # 决策建议
        print(f"--- 决策 ---")
        if pct >= 20.0:
            print(f"  ✅ 占比 {pct:.1f}% ≥ 20% → Fix-2/3/4 修真问题，建议 PR commit 6e81675")
        elif pct <= 5.0:
            print(f"  ❌ 占比 {pct:.1f}% ≤ 5% → 修空气，建议撤掉 commit 6e81675")
        else:
            print(f"  ⚠ 占比 {pct:.1f}% 在灰区 (5%-20%)，继续采或者按产品意愿决定")

    print()
    print(f"sample size note: 配对 < 100 时占比噪声大；建议跑到 200+ pair 再下定论。")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"probe jsonl path (default: {DEFAULT_LOG})")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_S,
                        help=f"短间隔阈值（秒），默认 {DEFAULT_THRESHOLD_S} = COMPLETED_HOLD_S")
    args = parser.parse_args()
    sys.exit(analyze(args.log, args.threshold))


if __name__ == "__main__":
    main()
