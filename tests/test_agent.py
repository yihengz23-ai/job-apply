"""网申底稿 + 面板里的助手。不起 claude 进程、不碰浏览器、不调 AI。"""

import json

import pytest

import app as panel
from jobapply import agent, config, wsprofile

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}


# ── 网申底稿 ─────────────────────────────────────────────────

@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setattr(wsprofile, "PATH", tmp_path / "wangshen_profile.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    return tmp_path


SAMPLE = {"_说明": "x", "基本信息": [["姓名", "张三"], ["出生日期", ""]],
          "家庭成员": [{"关系": "父亲", "姓名": "张大", "出生年月": ""}], "_核对提示": ["牛客里有一处要改"]}


def test_profile_missing_and_save(prof):
    assert wsprofile.missing(SAMPLE) == ["基本信息：出生日期", "家庭成员：父亲的出生年月"]
    saved = wsprofile.save(SAMPLE)
    assert json.loads((prof / "wangshen_profile.json").read_text(encoding="utf-8")) == saved
    assert saved["_核对提示"] == ["牛客里有一处要改"]
    wsprofile.save(SAMPLE)                                     # 再存一次：旧的进备份
    assert list((prof / "backups").glob("wangshen_profile-*.json"))


@pytest.mark.parametrize("value", ["123456199901011234", "11010119900307331X", "6222021234567890123"])
def test_profile_refuses_id_and_card_numbers(prof, value):
    bad = {"基本信息": [["备注", f"号码 {value}"]]}
    with pytest.raises(wsprofile.Invalid, match="身份证号"):
        wsprofile.save(bad)
    assert not (prof / "wangshen_profile.json").exists()
    wsprofile.save({"联系方式": [["手机", "13800000000"], ["邮编", "200000"]]})     # 手机号、邮编不算


def test_profile_text_for_agent():
    text = wsprofile.as_text(SAMPLE)
    assert "- 姓名：张三" in text and "出生日期：（空）" in text and "关系：父亲；姓名：张大" in text
    kit = {"_说明": "x", "实习经历": [{"公司": "A资本", "职位": "投资实习生", "起止时间": "2025.03 - 至今",
                                     "精简描述": "精简", "完整描述": ["第一条", "第二条"]}]}
    kt = wsprofile.kit_text(kit)
    assert "A资本｜投资实习生｜2025.03 - 至今" in kt and "完整描述：第一条 / 第二条" in kt and "_说明" not in kt


def test_profile_api(prof):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    d = c.get("/api/wangshen-profile", headers=LOCAL).get_json()
    assert d["example"] and d["profile"]["基本信息"]                        # 还没建：给示例
    r = c.put("/api/wangshen-profile", json={"profile": SAMPLE}, headers={**LOCAL, **XRW}).get_json()
    assert r["ok"] and r["missing"] == ["基本信息：出生日期", "家庭成员：父亲的出生年月"]
    d = c.get("/api/wangshen-profile", headers=LOCAL).get_json()
    assert not d["example"] and d["notes"] == ["牛客里有一处要改"]
    bad = c.put("/api/wangshen-profile", json={"profile": {"基本信息": [["x", "123456199901011234"]]}}, headers={**LOCAL, **XRW})
    assert bad.status_code == 400


# ── 助手 ─────────────────────────────────────────────────────

@pytest.fixture
def ag(tmp_path, monkeypatch, prof):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    monkeypatch.setattr(agent, "SITES_PATH", tmp_path / "sites.json")
    monkeypatch.setattr(agent, "_notify", lambda *a: None)
    started = []

    class FakeThread:
        def __init__(self, target, args, daemon):
            started.append(args)

        def start(self):
            pass
    monkeypatch.setattr(agent.threading, "Thread", FakeThread)
    agent._procs.clear()
    agent.started = started
    yield agent
    agent._procs.clear()


def test_send_queues_one_turn_at_a_time(ag):
    c = ag.new_chat()
    out = ag.send(c["id"], "帮我填这个网申：https://careers.example.com/apply")
    assert out["running"] and out["messages"][0]["role"] == "user" and ag.started == [(c["id"], out["messages"][0]["text"])]
    assert ag.get(c["id"])["title"].startswith("帮我填这个网申")
    with pytest.raises(ag.Busy):
        ag.send(c["id"], "再来一句")                                 # 上一句还没做完
    other = ag.new_chat()
    with pytest.raises(ag.Busy, match="另一个对话"):
        ag.send(other["id"], "你好")                                 # 浏览器只有一个：同时只跑一个对话
    with pytest.raises(ValueError):
        ag.send(other["id"], "   ")


def test_stream_events_become_messages(ag):
    c = ag.new_chat()
    cid = c["id"]
    ag.apply_event(cid, {"type": "system", "subtype": "init", "session_id": "sess-1"})
    ag.apply_event(cid, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__claude-in-chrome__navigate", "input": {"url": "https://careers.example.com/apply"}},
        {"type": "tool_use", "name": "mcp__claude-in-chrome__computer", "input": {"action": "left_click", "action_summary": "打开民族下拉框"}},
        {"type": "text", "text": "个人信息填好了。\n【网站笔记】careers.example.com：省份下拉框不能搜索，要滚动找"}]}})
    ag.apply_event(cid, {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "Element not found"}]}})
    assert ag.apply_event(cid, {"type": "result", "subtype": "success", "session_id": "sess-1", "total_cost_usd": 0.5}) is True
    chat = ag.get(cid)
    texts = [(m["role"], m["text"]) for m in chat["messages"]]
    assert ("tool", "打开 https://careers.example.com/apply") in texts and ("tool", "点击：打开民族下拉框") in texts
    assert any(r == "assistant" and t.startswith("个人信息填好了") for r, t in texts)
    assert any(r == "tool" and "Element not found" in t for r, t in texts)
    assert chat["session_id"] == "sess-1" and chat["cost_usd"] == 0.5
    assert ag.load_site_notes() == {"careers.example.com": ["省份下拉框不能搜索，要滚动找"]}
    ag.save_site_notes("【网站笔记】careers.example.com：省份下拉框不能搜索，要滚动找")    # 重复的不再记
    assert len(ag.load_site_notes()["careers.example.com"]) == 1


def test_batch_summary_and_args(ag, monkeypatch):
    s = ag.tool_summary("mcp__claude-in-chrome__browser_batch", {"actions": [
        {"name": "form_input", "input": {"action_summary": "填手机"}}, {"name": "computer", "input": {"action": "screenshot"}}]})
    assert s == "连续 2 步：填写：填手机；截图看一眼"
    monkeypatch.setattr(ag, "system_prompt", lambda: "SYS")
    args = ag._args({"session_id": ""})
    assert args[args.index("--tools") + 1] == "" and args[args.index("--allowedTools") + 1] == "mcp__claude-in-chrome"
    assert "--chrome" in args and "--strict-mcp-config" in args and "--resume" not in args
    assert ag._args({"session_id": "s1"})[-2:] == ["--resume", "s1"]          # 第二句起接着同一个对话


def test_stop_and_recover(ag):
    c = ag.new_chat()
    assert ag.stop(c["id"]) is False                                 # 没在做
    ag.send(c["id"], "填一下")
    assert ag.stop(c["id"]) is True and ag.get(c["id"])["stopped"]
    ag._procs.clear()                                                 # 面板重启：进程都没了
    ag.recover()
    chat = ag.get(c["id"])
    assert not chat["running"] and "面板重启过" in chat["messages"][-1]["text"]


def test_system_prompt_has_profile_rules_and_records(ag, monkeypatch):
    wsprofile.save(SAMPLE)
    monkeypatch.setattr(agent.records, "load", lambda: [{"sent_at": "2026-10-08 10:00", "company_name": "A资本", "job_title": "投资实习生",
                                                         "status": "已投递", "campaign": config.CURRENT_CAMPAIGN}])
    ag.save_site_notes("【网站笔记】careers.example.com：日期会晚一天")
    s = ag.system_prompt()
    assert "姓名：张三" in s and "出生日期：（空）" in s and "A资本｜投资实习生" in s
    assert "careers.example.com：日期会晚一天" in s and "不点最终的「提交" in s


def test_agent_api(ag):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    chat = c.post("/api/agent/new", headers={**LOCAL, **XRW}).get_json()
    d = c.post(f"/api/agent/{chat['id']}/send", json={"text": "你好"}, headers={**LOCAL, **XRW}).get_json()
    assert d["running"] and d["total"] == 1
    assert c.post(f"/api/agent/{chat['id']}/send", json={"text": "再说"}, headers={**LOCAL, **XRW}).status_code == 409
    lst = c.get("/api/agent", headers=LOCAL).get_json()
    assert lst["running"] == chat["id"] and lst["chats"][0]["title"] == "你好"
    assert c.get(f"/api/agent/{chat['id']}?since=1", headers=LOCAL).get_json()["messages"] == []
    assert c.delete(f"/api/agent/{chat['id']}", headers={**LOCAL, **XRW}).status_code == 409   # 还在做：不能删
    assert c.get("/api/agent/nonexistent", headers=LOCAL).status_code == 404
