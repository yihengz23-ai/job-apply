"""WP8a 助手宿主：claude 交给独立宿主托管，面板重启不断；停下只中断一轮；进程寿命跟着申请走；排队落盘；
会话接不上自动换新；额度按增量记；上传钩子。用真的宿主进程 + 假 claude（tests/fake_claude.py），不联网、不碰浏览器。"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jobapply import agent, agent_runner, config, upload_guard, wstasks

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_claude.py"


def wait_until(pred, timeout=15, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


@pytest.fixture
def live(tmp_path, monkeypatch):
    """面板侧用临时目录；claude 换成假的；不弹通知、不开后台巡检；系统提示用短的。结束时把宿主都关掉。"""
    chats = tmp_path / "chats"
    monkeypatch.setattr(agent, "CHATS_DIR", chats)
    monkeypatch.setattr(agent, "SITES_PATH", tmp_path / "sites.json")
    monkeypatch.setattr(agent, "_notify", lambda *a: None)
    monkeypatch.setattr(agent, "system_prompt", lambda chat=None: "SYS")
    monkeypatch.setattr(config, "CLAUDE_BIN", str(FAKE))
    monkeypatch.setattr(config, "AGENT_DETACHED", True)
    monkeypatch.setitem(agent._watchdog, "on", True)
    for s in (agent._runners, agent._starting, agent._pending, agent._waiting, agent._after_turn):
        s.clear()
    yield chats
    for d in (chats / "_run").glob("*") if (chats / "_run").exists() else []:
        if d.is_dir() and not d.name.startswith("_"):
            r = agent_runner.HostRunner.attach(d)
            if r.alive():
                r.kill()
    real = [r for r in agent._runners.values() if isinstance(r.runner, (agent_runner.HostRunner, agent_runner.LocalRunner))]
    for r in real:
        try:
            r.runner.kill()
        except Exception:
            pass
    wait_until(lambda: not any(r.runner.alive() for r in real), timeout=10)
    for s in (agent._runners, agent._starting, agent._pending, agent._waiting, agent._after_turn):
        s.clear()


def texts(chat_id, role=None):
    return [m["text"] for m in agent.get(chat_id)["messages"] if role is None or m["role"] == role]


def idle(chat_id):
    return not agent.get(chat_id)["running"] and chat_id not in agent.active_chats()


# ── 起进程、说话、读回复 ─────────────────────────────────────

def test_host_runs_fake_claude_end_to_end(live):
    c = agent.new_chat()
    agent.send(c["id"], "第一句")
    assert wait_until(lambda: "（测试环境假助手）收到：第一句" in texts(c["id"], "assistant"))
    assert wait_until(lambda: idle(c["id"]))
    r = agent._runners[c["id"]]
    assert isinstance(r.runner, agent_runner.HostRunner) and r.runner.alive()
    info = r.runner.info()
    assert info["claude_pid"] and info["host_pid"] != os.getpid()
    agent.send(c["id"], "第二句")                                         # 同一个进程接着说
    assert wait_until(lambda: "（测试环境假助手）收到：第二句" in texts(c["id"], "assistant"))
    assert r.runner.info()["claude_pid"] == info["claude_pid"]
    assert agent.get(c["id"])["session_id"]


# ── 面板被杀（像 kill -9）再起来：进程还是原来那个，消息不重不丢 ──────────────

PANEL1 = r'''
import os, sys, time
sys.path.insert(0, {root!r})
from jobapply import agent, config
agent.CHATS_DIR = __import__("pathlib").Path({chats!r})
agent._notify = lambda *a: None
agent.system_prompt = lambda chat=None: "SYS"
config.CLAUDE_BIN = {fake!r}
config.AGENT_DETACHED = True
agent._watchdog["on"] = True
c = agent.new_chat()
agent.send(c["id"], "慢慢想的第一句")
t0 = time.time()
while time.time() - t0 < 10 and not agent._runners.get(c["id"]):
    time.sleep(0.05)
time.sleep(0.5)
print(c["id"], flush=True)
os._exit(0)          # 不收尾就死：像面板被 kill -9
'''


def test_panel_killed_then_reattaches(live, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLOW", "2")
    out = subprocess.run([sys.executable, "-c", PANEL1.format(root=str(ROOT), chats=str(live), fake=str(FAKE))],
                         capture_output=True, text=True, timeout=60, cwd=ROOT,
                         env={k: v for k, v in os.environ.items() if not k.startswith("JOBAPPLY_")})
    cid = out.stdout.strip().splitlines()[-1]
    run_dir = live / "_run" / cid
    pid = json.loads((run_dir / "host.json").read_text())["claude_pid"]
    assert agent_runner.HostRunner.attach(run_dir).alive()                # 面板死了，宿主和 claude 还在
    agent.recover()                                                       # 新面板起来：接上
    assert cid in agent._runners
    assert wait_until(lambda: any("慢慢想的第一句" in t for t in texts(cid, "assistant")), timeout=20)
    assert wait_until(lambda: idle(cid))
    agent.send(cid, "接上以后的第二句")
    assert wait_until(lambda: any("接上以后的第二句" in t for t in texts(cid, "assistant")), timeout=20)
    assert json.loads((run_dir / "host.json").read_text())["claude_pid"] == pid   # 还是原来那个进程
    lines = [json.loads(x) for x in (run_dir / "out.jsonl").read_text().splitlines()]
    replies = [x for x in lines if x.get("type") == "assistant"]
    assert len(replies) == len(texts(cid, "assistant")) == 2             # 不重也不丢
    assert not any("面板重启过" in t for t in texts(cid))                  # 接上了的不会被当成「没做完」


# ── 停下只中断这一轮，进程和网页都留着 ──────────────────────────────

def test_stop_interrupts_only_this_turn(live, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLOW", "3")
    t = wstasks.add("https://jobs.example.com/stop", "停资本", "实习生")
    c = agent.new_chat(task_id=t["id"])
    agent.send(c["id"], "填一下")
    assert wait_until(lambda: c["id"] in agent._runners and agent._runners[c["id"]].turn)
    r = agent._runners[c["id"]]
    pid = r.runner.pid
    time.sleep(0.5)
    assert agent.stop(c["id"]) is True
    assert wait_until(lambda: idle(c["id"]), timeout=8)
    assert r.runner.alive() and r.runner.pid == pid                      # 进程还在
    assert any("已停下" in x for x in texts(c["id"], "system")) and not any("出错了" in x for x in texts(c["id"], "system"))
    assert wstasks.get(t["id"])["halted"] == "本人点了停下"
    agent.send(c["id"], "接着做")                                          # 还能接着说
    assert wait_until(lambda: any("接着做" in x for x in texts(c["id"], "assistant")), timeout=10)


# ── 会话接不上：自动换新会话，带上前情 ─────────────────────────────

def test_dead_session_restarts_fresh(live):
    t = wstasks.add("https://jobs.example.com/dead", "旧会话资本", "分析师")
    c = agent.new_chat(task_id=t["id"])
    chat = agent._load(c["id"])
    chat["session_id"] = "0000dead-0000-4000-8000-000000000000"
    chat["messages"].append({"role": "assistant", "text": "上次填到实习经历了", "at": "x"})
    agent._save(chat)
    agent.send(c["id"], "接着填")
    assert wait_until(lambda: any("原来的会话接不上了" in x for x in texts(c["id"], "system")))
    assert wait_until(lambda: any("收到：（面板）原来的会话接不上了" in x for x in texts(c["id"], "assistant")), timeout=20)
    reply = next(x for x in texts(c["id"], "assistant") if "原来的会话接不上了" in x)
    assert "旧会话资本" in reply
    new_sid = agent.get(c["id"])["session_id"]
    assert new_sid and not new_sid.startswith("0000dead") and not any("出错了" in x for x in texts(c["id"], "system"))


# ── 额度：total_cost_usd 是累计值，只加新增的部分 ───────────────────

def test_cost_counted_as_delta(live, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.01")
    c = agent.new_chat()
    for i in range(3):
        agent.send(c["id"], f"第{i}句")
        assert wait_until(lambda i=i: any(f"第{i}句" in x for x in texts(c["id"], "assistant")))
        assert wait_until(lambda: idle(c["id"]))
    assert agent.get(c["id"])["cost_usd"] == pytest.approx(0.03)


def test_cost_delta_survives_reattach():
    class R:
        class runner:
            pid = 4242
    chat = {}
    assert agent._cost_delta(chat, {"total_cost_usd": 0.02}, R) == pytest.approx(0.02)
    assert agent._cost_delta(chat, {"total_cost_usd": 0.05}, R) == pytest.approx(0.03)
    assert agent._cost_delta(chat, {"total_cost_usd": 0}, R) == 0                       # 被中断的那轮报 0：不倒扣
    assert agent._cost_delta(chat, {"total_cost_usd": 0.06}, R) == pytest.approx(0.01)
    assert agent._cost_delta(chat, {"total_cost_usd": 0.5}, None) == pytest.approx(0.5)  # 没有进程信息：照单全收


# ── 排队落盘，重启后接着排 ────────────────────────────────────

def test_queue_survives_restart(live, monkeypatch):
    c = agent.new_chat()
    monkeypatch.setattr(agent, "_blocked", lambda cid: "已经有 3 个助手在同时干活")
    out = agent.send(c["id"], "排着的话")
    assert out["queued"] and json.loads((live / "_run" / "queue.json").read_text())[0][:2] == [c["id"], "排着的话"]
    agent._waiting.clear()                                               # 面板重启：内存里的队列没了
    monkeypatch.setattr(agent, "_blocked", lambda cid: "还满着")
    agent.recover()
    assert agent.waiting_chats() == {c["id"]: "已经有 3 个助手在同时干活"}
    assert any(m.get("queued") for m in agent.get(c["id"])["messages"])  # 排队标记还在，没被当成取消
    monkeypatch.setattr(agent, "_blocked", lambda cid: "")
    agent._drain()
    assert wait_until(lambda: any("排着的话" in x for x in texts(c["id"], "assistant")))


# ── 进程收尾按进程对象核对（B35）────────────────────────────────

def test_finish_does_not_drop_newer_runner(live):
    c = agent.new_chat()

    class Stub:
        dir = live / "nothing"

        def alive(self):
            return True
    old, new = agent._Runner(c["id"], Stub()), agent._Runner(c["id"], Stub())
    agent._runners[c["id"]] = new
    chat = agent._load(c["id"])
    chat["running"] = True
    agent._save(chat)
    agent._finish(c["id"], old)                                          # 旧进程的收尾晚到了
    assert agent._runners[c["id"]] is new and agent.get(c["id"])["running"]


# ── 寿命：没做完的不因闲置关；做完的 10 分钟关；12 小时一律关；60 分钟提醒、3 小时自动中断 ───

class StubRunner:
    def __init__(self):
        self.closed = self.interrupted = False
        self.pid = 1

    def alive(self):
        return True

    def close(self):
        self.closed = True

    def interrupt(self):
        self.interrupted = True


def make(live, status="助手在填", **task_fields):
    t = wstasks.add(f"https://jobs.example.com/life-{len(agent._runners)}", "寿命资本", "实习生")   # 网址里不放中文（会被清掉，几条撞成一条）
    wstasks.update(t["id"], status=status, **task_fields)
    c = agent.new_chat(task_id=t["id"])
    r = agent._Runner(c["id"], StubRunner())
    agent._runners[c["id"]] = r
    return c["id"], r


def test_lifetime_follows_the_application(live):
    now = 1_000_000.0
    unfinished, r1 = make(live, "等你处理")
    finished, r2 = make(live, "已提交", readback={"status": {"text": "x"}}, readback_state="")
    reading, r3 = make(live, "已提交", readback={}, readback_state="读回等你登录")
    for r in (r1, r2, r3):
        r.last_event = now - 4 * 3600
    agent.watchdog_tick(now)
    assert not r1.runner.closed and r2.runner.closed and not r3.runner.closed   # 没做完 / 读回没完：不关
    r1.last_event = now - 13 * 3600
    agent.watchdog_tick(now)
    assert r1.runner.closed                                               # 12 小时没动静：一律关


def test_long_turn_warns_then_interrupts(live):
    now = 2_000_000.0
    cid, r = make(live, "助手在填")
    r.turn, r.turn_started, r.last_event = True, now - 61 * 60, now - 61 * 60
    agent.watchdog_tick(now)
    assert r.warned and any("60 分钟没有新进展" in x for x in texts(cid, "system")) and not r.runner.interrupted
    agent.watchdog_tick(now)
    assert sum("60 分钟没有新进展" in x for x in texts(cid, "system")) == 1   # 只提醒一次
    r.turn_started = now - 3 * 3600 - 1
    agent.watchdog_tick(now)
    assert r.runner.interrupted and agent._load(cid).get("stopped")      # 满 3 小时：中断这一轮（不杀进程）


def test_make_room_closes_finished_first(live, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_LIVE", 2)
    a, ra = make(live, "已提交", readback={"status": {"text": "x"}}, readback_state="")
    b, rb = make(live, "等你处理")
    agent._make_room(keep="new")
    assert ra.runner.closed and not rb.runner.closed
    rb2 = agent._runners[b]
    assert "都还没做完" in agent._blocked("brand-new-chat") or len([1 for x in agent._runners.values() if not x.closed]) < 2
    assert rb2 is rb


# ── 兜底开关：面板自己带着进程（老办法）也能跑 ─────────────────────────

def test_local_runner_fallback(live, monkeypatch):
    monkeypatch.setattr(config, "AGENT_DETACHED", False)
    monkeypatch.setenv("FAKE_CLAUDE_SLOW", "2")
    c = agent.new_chat()
    agent.send(c["id"], "老办法")
    assert wait_until(lambda: c["id"] in agent._runners and agent._runners[c["id"]].turn)
    assert isinstance(agent._runners[c["id"]].runner, agent_runner.LocalRunner)
    time.sleep(0.3)
    agent.stop(c["id"])
    assert wait_until(lambda: idle(c["id"]), timeout=8) and agent._runners[c["id"]].runner.alive()
    agent.send(c["id"], "再来")
    assert wait_until(lambda: any("再来" in x for x in texts(c["id"], "assistant")), timeout=10)


# ── 上传钩子 ─────────────────────────────────────────────────

def test_upload_guard(tmp_path):
    ok_dir = tmp_path / "网申上传"
    ok_dir.mkdir()
    (ok_dir / "证件照.jpg").write_text("x")
    inside = {"tool_input": {"paths": [str(ok_dir / "证件照.jpg")], "ref": "ref_1"}}
    outside = {"tool_input": {"paths": [str(tmp_path / "别的.pdf"), str(ok_dir / "../网申上传/../x.pdf")]}}
    assert upload_guard.check(inside, str(ok_dir)) == []
    assert len(upload_guard.check(outside, str(ok_dir))) == 2
    run = lambda data: subprocess.run([sys.executable, str(ROOT / "jobapply" / "upload_guard.py"), str(ok_dir)],
                                      input=json.dumps(data), capture_output=True, text=True)
    assert run(inside).returncode == 0
    bad = run(outside)
    assert bad.returncode == 2 and "只能上传「网申上传」文件夹里的文件" in bad.stderr
    assert run({"oops": 1}).returncode == 0 and subprocess.run([sys.executable, str(ROOT / "jobapply" / "upload_guard.py"), str(ok_dir)],
                                                               input="不是json", capture_output=True, text=True).returncode == 0


def test_args_carry_upload_hook(live):
    args = agent._args({"session_id": ""})
    settings = json.loads(args[args.index("--settings") + 1])
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "mcp__claude-in-chrome__file_upload" and "upload_guard.py" in hook["hooks"][0]["command"]


def test_child_env_drops_claude_code_session(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    env = agent._child_env()
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env and "ANTHROPIC_API_KEY" not in env and "PATH" in env
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"     # 本人全局记忆不进助手的上下文


def test_llm_call_env_has_no_memory_and_no_key(monkeypatch):
    from jobapply import llm
    seen = {}

    def fake_run(args, input, **kw):
        seen.update(kw["env"])
        class P:
            stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": {"a": 1},
                                 "usage": {}}) + "\n"
            stderr = ""
        return P()
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    llm._call_cc(system="s", content="c", schema={"type": "object"}, effort="low")
    assert seen["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and "CLAUDECODE" not in seen and "ANTHROPIC_API_KEY" not in seen
