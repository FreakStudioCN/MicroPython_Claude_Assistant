#!/usr/bin/env python3
# hook_probe.py —— Claude Code hook 探活脚本
# 把 stdin 收到的整条 hook payload + 时间戳 append 到 ~/.claude_buddy/probe.jsonl
# 永远返回空 {}，不阻塞任何工具，不打印到 stderr 影响主进程。
#
# 注册：在项目级 .claude/settings.json 把 28 个 hook 全部指向
#   python <abs path>/hook_probe.py
#
# 用途：跑真实 session 攒数据，确认哪些 hook 真触发、字段长啥样，
# 为后续 hook_layer.py 字段规整提供依据。一次性工具，跑够样本就下线。

import json
import os
import sys
import time
import traceback

LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude_buddy")
LOG_PATH = os.path.join(LOG_DIR, "probe.jsonl")


def _safe_append(record: dict) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 落盘失败也不能影响 Claude Code 主流程，吞掉
        pass


def main() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        # stdin 读不出来也要落一条
        _safe_append({
            "ts": time.time(),
            "stage": "stdin_read_failed",
            "err": traceback.format_exc(limit=3),
        })
        print("{}")
        return

    record = {"ts": time.time(), "raw_len": len(raw)}

    if not raw.strip():
        record["stage"] = "empty_stdin"
        _safe_append(record)
        print("{}")
        return

    try:
        event = json.loads(raw)
    except Exception:
        record["stage"] = "json_parse_failed"
        record["raw"] = raw[:2000]
        _safe_append(record)
        print("{}")
        return

    record["stage"] = "ok"
    record["event_name"] = event.get("hook_event_name", "")
    record["payload"] = event
    _safe_append(record)

    # 永远返回空对象，不影响 Claude Code 默认行为
    print("{}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 万一上面有漏网异常，最后一道兜底
        try:
            _safe_append({
                "ts": time.time(),
                "stage": "uncaught_exception",
                "err": traceback.format_exc(limit=5),
            })
        except Exception:
            pass
        print("{}")
