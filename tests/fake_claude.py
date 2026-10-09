#!/usr/bin/env python3
"""测试环境（JOBAPPLY_ENV=test）用的假 claude：不联网、不花额度、不碰浏览器。只认面板用到的两种用法，
输出和真 claude 一样是 stream-json（一行一个事件）：
- 写信 / 分析（带 --json-schema）：读一条消息，按 schema 编一份合法的 JSON 当 structured_output 返回。
- 助手对话（不带 --json-schema）：每收到一条消息回一句「（测试环境假助手）收到：…」，直到输入关掉。
  也认中断请求（control_request / interrupt）：正在「想」的这一轮马上结束（result 是 error_during_execution），进程留着。

环境变量（测试用）：
  FAKE_CLAUDE_REPLY   回复原文（拿来测【网申记录】这类标记）
  FAKE_CLAUDE_SLOW    每轮先「想」这么多秒再回（期间能被中断）
  FAKE_CLAUDE_COST    每轮花多少美元；total_cost_usd 和真 claude 一样报这个进程的累计值
--resume 的会话号以 0000dead 开头：照真 claude 的样子报「No conversation found」并退出（退出码 1）。"""

import json
import os
import queue
import sys
import threading
import time
import uuid


def fake_value(schema, root, name=""):
    """按 JSON Schema 编一个最简单的合法值：字符串写明是假内容，数字取下限，数组按最少个数。"""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref", "")
    if ref.startswith("#/"):
        node = root
        for part in ref[2:].split("/"):
            node = node.get(part, {})
        return fake_value(node, root, name)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return fake_value(schema[key][0], root, name)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or (t is None and "properties" in schema):
        return {k: fake_value(v, root, k) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [fake_value(schema.get("items") or {}, root, name) for _ in range(schema.get("minItems", 0))]
    if t == "string":
        return f"（测试环境假内容：{name}）" if name else "（测试环境假内容）"
    if t in ("integer", "number"):
        return schema.get("minimum", 0)
    if t == "boolean":
        return False
    return None


def emit(ev):
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def text_of(msg):
    content = (msg.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def main(argv):
    schema = json.loads(argv[argv.index("--json-schema") + 1]) if "--json-schema" in argv else None
    resume = argv[argv.index("--resume") + 1] if "--resume" in argv else ""
    sid = resume or str(uuid.uuid4())
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    if resume.startswith("0000dead"):
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0, "session_id": sid,
              "total_cost_usd": 0, "usage": usage})
        sys.stderr.write(f"No conversation found with session ID: {sid}\n")
        return 1
    emit({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-claude", "tools": []})
    slow = float(os.environ.get("FAKE_CLAUDE_SLOW") or 0)
    per_turn = float(os.environ.get("FAKE_CLAUDE_COST") or 0)
    total = 0.0
    inbox = queue.Queue()

    def read_stdin():
        for line in sys.stdin:
            try:
                inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        inbox.put(None)
    threading.Thread(target=read_stdin, daemon=True).start()

    while True:
        msg = inbox.get()
        if msg is None:
            return 0
        if msg.get("type") == "control_request":   # 没在想的时候收到中断：回个确认就行
            emit({"type": "control_response", "response": {"subtype": "success", "request_id": msg.get("request_id")}})
            continue
        if schema is not None:
            emit({"type": "result", "subtype": "success", "is_error": False, "session_id": sid, "result": "",
                  "structured_output": fake_value(schema, schema), "usage": usage, "total_cost_usd": per_turn})
            return 0
        interrupted, extra = False, []
        t0 = time.time()
        while time.time() - t0 < slow:              # 「想」的时候也在听：中断请求马上结束这一轮
            try:
                m = inbox.get(timeout=0.05)
            except queue.Empty:
                continue
            if m is None:
                break
            if m.get("type") == "control_request" and (m.get("request") or {}).get("subtype") == "interrupt":
                emit({"type": "control_response", "response": {"subtype": "success", "request_id": m.get("request_id")}})
                interrupted = True
                break
            extra.append(m)
        if interrupted:
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 1,
                  "session_id": sid, "usage": usage, "total_cost_usd": total})
            for m in extra:
                inbox.put(m)
            continue
        total = round(total + per_turn, 6)
        words = [text_of(msg)] + [text_of(m) for m in extra if m.get("type") == "user"]
        reply = os.environ.get("FAKE_CLAUDE_REPLY") or "（测试环境假助手）收到：" + "；".join(w.strip()[:80] for w in words)
        emit({"type": "assistant", "session_id": sid,
              "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "session_id": sid, "result": reply,
              "usage": usage, "total_cost_usd": total})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
