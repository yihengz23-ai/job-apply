"""助手宿主：替面板托管一个 claude 进程，面板重启、被杀都不影响它（它自己一个会话，不挂在面板下面）。

    python -m jobapply.agent_host <运行目录>

宿主只搬运字节，不写任何数据文件（对话、待办、看板都由面板写）。运行目录里：
  host.json     面板写：cmd、env、cwd；宿主补写：host_pid、claude_pid、started_at，结束时 exit_code、ended_at
  in.jsonl      面板追加要递给 claude 的行（stream-json 的 user 行、中断请求）；宿主每 0.3 秒看一次，整行转给 claude
  out.jsonl     claude 的标准输出，宿主直接接到这个文件（面板从 panel.offset 记的位置接着读）
  stderr.log    claude 的错误输出
  alive         宿主每 5 秒碰一下（面板看修改时间判断宿主还活着）
  ctl           面板写 close（关掉 claude 的输入，让它做完手头的就退出）或 kill
  panel.offset  面板读 out.jsonl 读到了哪里（面板自己写）

2026-10-09 用真 claude CLI 实测（claude-haiku-4-5，--input-format/--output-format stream-json，没开浏览器）：
- 中断：往 stdin 写 {"type":"control_request","request_id":…,"request":{"subtype":"interrupt"}}，0.1 秒内收到
  control_response（success）和一条 result（subtype=error_during_execution，is_error=true）；进程还活着，之后照样能收话、回话。
  所以「停下」只中断这一轮，不用杀进程（杀进程它开的网页就不归它管了）。
- total_cost_usd 是这个进程的累计值（第二轮 0.00586，第三轮 0.00733）；被中断的那轮是 0。额度只能按增量加。
- --resume 一个不存在的会话：退出码 1，stdout 一条 result（error_during_execution，num_turns=0），
  stderr「No conversation found with session ID: …」。要清掉 session_id、换新会话。
- --settings 里写的 PreToolUse 钩子在 --setting-sources "" 下照样生效（退出码 2 = 拦下，stderr 原文回给模型），
  所以 file_upload 可以用钩子限定只能传「网申上传」文件夹里的文件（jobapply/upload_guard.py）。
- 宿主托管时 claude 不是面板的子进程：面板被杀（kill -9）后 claude 还活着，它和 Chrome 的连接、它开的标签组都还在；
  面板起来后从 panel.offset 接着读 out.jsonl，消息不重也不丢（tests/test_agent_host.py 用假 claude 验证）。
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

POLL = 0.3
ALIVE_EVERY = 5.0


def _write_json(path, data):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _touch(path):
    path.touch()
    os.utime(path, None)


def run(run_dir):
    run = Path(run_dir)
    spec_path = run / "host.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    (run / "in.jsonl").touch()
    out = open(run / "out.jsonl", "ab")
    err = open(run / "stderr.log", "ab")
    try:
        proc = subprocess.Popen(spec["cmd"], stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=spec.get("cwd") or None,
                                env=spec.get("env") or None)
    except OSError as e:
        spec.update(host_pid=os.getpid(), exit_code=127, error=str(e), ended_at=datetime.now().isoformat(timespec="seconds"))
        _write_json(spec_path, spec)
        return 127
    spec.update(host_pid=os.getpid(), claude_pid=proc.pid, started_at=datetime.now().isoformat(timespec="seconds"))
    _write_json(spec_path, spec)

    pos, buf, closed, killed_at, last_alive = 0, b"", False, 0.0, 0.0
    inp = run / "in.jsonl"
    while proc.poll() is None:
        try:   # 新的整行转给 claude（半行留着等写完）
            with open(inp, "rb") as f:
                f.seek(pos)
                chunk = f.read()
            pos += len(chunk)
            buf += chunk
            while b"\n" in buf and not closed:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    proc.stdin.write(line + b"\n")
                    proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        cmd = ""
        try:
            cmd = (run / "ctl").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if cmd == "close" and not closed:
            closed = True
            try:
                proc.stdin.close()
            except OSError:
                pass
        elif cmd == "kill":
            if not killed_at:
                killed_at = time.time()
                proc.terminate()
            elif time.time() - killed_at > 5:
                proc.kill()
        now = time.time()
        if now - last_alive >= ALIVE_EVERY:
            _touch(run / "alive")
            last_alive = now
        time.sleep(POLL)
    code = proc.wait()
    out.close()
    err.close()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.update(exit_code=code, ended_at=datetime.now().isoformat(timespec="seconds"))
    _write_json(spec_path, spec)
    _touch(run / "alive")
    return code


if __name__ == "__main__":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)   # 万一被带着终端起来：关终端也不退
    sys.exit(run(sys.argv[1]))
