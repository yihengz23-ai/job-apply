"""助手进程的两种跑法，接口一样（agent.py 只认这几个方法）：

- HostRunner（默认，config.AGENT_DETACHED）：claude 交给独立的宿主进程托管（jobapply/agent_host.py），
  面板和它之间只通过运行目录里的文件说话。面板重启、被杀都不影响 claude；面板起来后 attach() 接着读。
- LocalRunner（开关关掉时的兜底）：claude 直接是面板的子进程（改造前的做法）。

方法：write(文字) 说一句；interrupt() 只中断这一轮；close() 关掉输入（做完手头的就退出）；kill() 结束进程；
alive() 进程还在不在；events() 一条条读 stream-json 事件（读完一条、下一次取之前才记进度，面板中途死了也不丢不重）；
wait() 等它结束、返回退出码；stderr_tail() 最后几行错误输出。"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config

ALIVE_STALE = 30          # 宿主超过这么多秒没碰 alive，就当它死了
_ENDED_KEEP = 20          # 结束了的运行目录留最近几个（排查用）


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # 已经退出、还没被收尸的子进程也算死了
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(int(pid))], capture_output=True, text=True).stdout.strip()
        return bool(out) and not out.startswith("Z")
    except Exception:
        return True


def _user_line(text):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"


def _interrupt_line():
    return json.dumps({"type": "control_request", "request_id": "panel-" + uuid.uuid4().hex[:8],
                       "request": {"subtype": "interrupt"}}) + "\n"


class HostRunner:
    def __init__(self, run_dir):
        self.dir = Path(run_dir)
        self._lock = threading.Lock()

    # ── 起 / 接 ──
    @classmethod
    def start(cls, run_root, chat_id, cmd, *, cwd, env):
        run_root = Path(run_root)
        run_dir = run_root / chat_id
        if run_dir.exists():               # 上一个进程的目录：还活着就先结束它（一个对话只能有一个进程），再挪去 _ended
            old = cls(run_dir)
            if old.alive():
                old.kill()
                for _ in range(50):
                    if not old.alive():
                        break
                    time.sleep(0.1)
            cls.retire(run_dir)
        run_dir.mkdir(parents=True)
        (run_dir / "host.json").write_text(json.dumps({"cmd": cmd, "env": env, "cwd": str(cwd), "chat_id": chat_id},
                                                      ensure_ascii=False), encoding="utf-8")
        (run_dir / "in.jsonl").touch()
        (run_dir / "out.jsonl").touch()
        (run_dir / "panel.offset").write_text("0")
        host = subprocess.Popen([sys.executable, "-m", "jobapply.agent_host", str(run_dir)], cwd=str(config.BASE_DIR),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)   # 自己一个会话：面板死了、终端关了都不影响
        r = cls(run_dir)
        r._host_proc = host                # 只用来收尸，免得留僵尸进程
        for _ in range(100):               # 等宿主把 claude 起来（最多 10 秒）
            info = r.info()
            if info.get("claude_pid") or "exit_code" in info:
                break
            if host.poll() is not None:
                break
            time.sleep(0.1)
        return r

    @classmethod
    def attach(cls, run_dir):
        return cls(run_dir)

    @staticmethod
    def retire(run_dir):
        run_dir = Path(run_dir)
        ended = run_dir.parent / "_ended"
        ended.mkdir(exist_ok=True)
        dst = ended / f"{run_dir.name}-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        shutil.move(str(run_dir), str(dst))
        old = sorted(ended.iterdir(), key=lambda p: p.stat().st_mtime)
        for p in old[:-_ENDED_KEEP]:
            shutil.rmtree(p, ignore_errors=True)

    def info(self):
        try:
            return json.loads((self.dir / "host.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @property
    def pid(self):
        return self.info().get("claude_pid")

    def host_alive(self):
        info = self.info()
        if "exit_code" in info or not _pid_alive(info.get("host_pid")):
            return False
        try:
            return time.time() - (self.dir / "alive").stat().st_mtime < ALIVE_STALE
        except OSError:   # 刚起来还没碰过
            return time.time() - (self.dir / "host.json").stat().st_mtime < ALIVE_STALE

    def alive(self):
        """claude 还在跑（宿主活着；或者宿主被杀了、claude 还没退）。"""
        info = self.info()
        if "exit_code" in info:
            return False
        return self.host_alive() or _pid_alive(info.get("claude_pid"))

    # ── 说话 / 控制 ──
    def _append(self, line):
        with self._lock:
            with open(self.dir / "in.jsonl", "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()

    def write(self, text):
        if not self.alive():
            raise BrokenPipeError("助手进程已经结束")
        self._append(_user_line(text))

    def interrupt(self):
        self._append(_interrupt_line())

    def close(self):
        (self.dir / "ctl").write_text("close", encoding="utf-8")

    def kill(self):
        (self.dir / "ctl").write_text("kill", encoding="utf-8")
        if not self.host_alive():          # 宿主没了、claude 还在：直接结束它
            pid = self.info().get("claude_pid")
            if _pid_alive(pid):
                try:
                    os.kill(int(pid), 15)
                except OSError:
                    pass

    # ── 读 ──
    def offset(self):
        try:
            return int((self.dir / "panel.offset").read_text().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _commit(self, pos):
        tmp = self.dir / ".panel.offset.tmp"
        tmp.write_text(str(pos))
        os.replace(tmp, self.dir / "panel.offset")

    def events(self, poll=0.2):
        """从上次读到的地方接着读，一直读到进程结束、文件读完。每条事件交出去、下一次来取时才记进度。"""
        pos, buf, pending = self.offset(), b"", None
        path = self.dir / "out.jsonl"
        idle = 0
        while True:
            with open(path, "rb") as f:
                f.seek(pos + len(buf))
                chunk = f.read()
            buf += chunk
            got = False
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                end = pos + len(line) + 1
                if pending is not None:
                    self._commit(pending)
                pos, pending = end, end
                try:
                    ev = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                got = True
                yield ev
            if pending is not None:
                self._commit(pending)
                pending = None
            if got or chunk:
                idle = 0
                continue
            idle += 1
            if idle % 5 == 0 and not self.alive():
                with open(path, "rb") as f:   # 结束前最后再看一眼有没有没读的
                    f.seek(pos + len(buf))
                    if not f.read():
                        return
            time.sleep(poll)

    def wait(self, timeout=30):
        t0 = time.time()
        while self.alive() and time.time() - t0 < timeout:
            time.sleep(0.2)
        hp = getattr(self, "_host_proc", None)
        if hp is not None:
            try:
                hp.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return self.info().get("exit_code")

    def stderr_tail(self, n=300):
        try:
            return (self.dir / "stderr.log").read_text(encoding="utf-8", errors="ignore").strip()[-n:]
        except OSError:
            return ""


class LocalRunner:
    """兜底：claude 直接当面板的子进程（面板重启就断）。stderr 写进文件，免得管道写满把进程卡住。"""

    def __init__(self, proc, err_path):
        self.proc, self.err_path, self._lock = proc, Path(err_path), threading.Lock()

    @classmethod
    def start(cls, run_root, chat_id, cmd, *, cwd, env):
        run_dir = Path(run_root) / chat_id
        run_dir.mkdir(parents=True, exist_ok=True)
        err = open(run_dir / "stderr.log", "wb")
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=cwd, env=env)
        err.close()
        return cls(proc, run_dir / "stderr.log")

    @property
    def pid(self):
        return self.proc.pid

    def alive(self):
        return self.proc.poll() is None

    def _send(self, line):
        with self._lock:
            self.proc.stdin.write(line.encode("utf-8"))
            self.proc.stdin.flush()

    def write(self, text):
        self._send(_user_line(text))

    def interrupt(self):
        try:
            self._send(_interrupt_line())
        except (BrokenPipeError, OSError, ValueError):
            pass

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def kill(self):
        self.proc.terminate()

    def events(self, poll=0.2):
        for line in self.proc.stdout:
            try:
                yield json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

    def wait(self, timeout=30):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_tail(self, n=300):
        try:
            return self.err_path.read_text(encoding="utf-8", errors="ignore").strip()[-n:]
        except OSError:
            return ""


def start(run_root, chat_id, cmd, *, cwd, env):
    cls = HostRunner if config.AGENT_DETACHED else LocalRunner
    return cls.start(run_root, chat_id, cmd, cwd=cwd, env=env)
