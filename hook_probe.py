#!/usr/bin/env python3
# hook_probe.py —— Claude Code hook 探活脚本
#
# 把 stdin 收到的整条 hook payload + 时间戳 append 到 ~/.claude_buddy/probe.jsonl
# 永远返回空 {}，不阻塞任何工具，不打印到 stderr 影响主进程。
#
# 注册：在项目级 .claude/settings.json 把 28+ 个 hook 全部指向
#   python <abs path>/hook_probe.py
#
# 用途：跑真实 session 攒数据，确认哪些 hook 真触发、字段长啥样，
# 为后续 hook_layer.py 字段规整提供依据。一次性工具，跑够样本就下线。
#
# ⚠️ 安全提醒：probe.jsonl 会原样落盘 Bash command / Write content / Read 路径等，
# 跑到 .env、密钥、私钥相关命令时会泄到本文件。完成 probe 后请立即清理：
#   rm ~/.claude_buddy/probe.jsonl
# 不要把这个文件提交到任何 repo。

import json
import os
import sys
import time
import traceback

LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude_buddy")
LOG_PATH = os.path.join(LOG_DIR, "probe.jsonl")
MAX_LOG_BYTES = 50 * 1024 * 1024  # 50MB 上限，超出就停写避免长跑爆盘


def _safe_append(record: dict) -> None:
    """原子 append 一行 JSON。Windows 上 open(..,'a') 不保证 O_APPEND 原子，
    多 hook 并发可能撕字节。改用 os.open(O_APPEND) + 一次 os.write 的组合，
    POSIX 和 Windows 都保证 PIPE_BUF 以下单 write 原子。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        try:
            if os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
                return
        except OSError:
            pass  # 文件还不存在，正常

        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY  # Windows：禁止 \n→\r\n 翻译，保 jsonl 干净
        fd = os.open(LOG_PATH, flags, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception:
        # 落盘失败也不能影响 Claude Code 主流程，吞掉
        pass


def _emit_empty() -> None:
    """打印空 {} 给 stdout 并显式 flush。Windows buffered stdout 不 flush 可能丢。"""
    try:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
    except Exception:
        pass


def main() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        _safe_append({
            "ts": time.time(),
            "stage": "stdin_read_failed",
            "err": traceback.format_exc(limit=3),
        })
        _emit_empty()
        return

    record = {"ts": time.time(), "raw_len": len(raw)}

    if not raw.strip():
        record["stage"] = "empty_stdin"
        _safe_append(record)
        _emit_empty()
        return

    try:
        event = json.loads(raw)
    except Exception:
        record["stage"] = "json_parse_failed"
        record["raw"] = raw[:2000]
        _safe_append(record)
        _emit_empty()
        return

    record["stage"] = "ok"
    record["event_name"] = event.get("hook_event_name", "")
    record["payload"] = event
    _safe_append(record)
    _emit_empty()


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
        _emit_empty()
