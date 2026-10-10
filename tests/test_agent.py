"""网申底稿 + 面板里的助手。不起 claude 进程、不碰浏览器、不调 AI。"""

import json

import pytest

import app as panel
from jobapply import agent, config, wsprofile, wstasks

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
    assert c.put("/api/wangshen-profile", json={"profile": SAMPLE}, headers={**LOCAL, **XRW}).status_code == 409   # 旧页面：要刷新
    r = c.put("/api/wangshen-profile", json={"profile": SAMPLE, "base": {}}, headers={**LOCAL, **XRW}).get_json()
    assert r["ok"] and r["missing"] == ["基本信息：出生日期", "家庭成员：父亲的出生年月"]
    d = c.get("/api/wangshen-profile", headers=LOCAL).get_json()
    assert not d["example"] and d["notes"] == ["牛客里有一处要改"]
    bad = c.put("/api/wangshen-profile", json={"profile": {"基本信息": [["x", "123456199901011234"]]}, "base": {}}, headers={**LOCAL, **XRW})
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
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    for s in (agent._runners, agent._starting, agent._pending, agent._waiting, agent._after_turn):
        s.clear()
    agent.started = started
    yield agent
    for s in (agent._runners, agent._starting, agent._pending, agent._waiting, agent._after_turn):
        s.clear()


def test_send_runs_in_parallel_then_queues(ag, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_PARALLEL", 2)
    c = ag.new_chat()
    out = ag.send(c["id"], "帮我填这个网申：https://careers.example.com/apply")
    assert out["running"] and out["messages"][0]["role"] == "user" and ag.started == [(c["id"], out["messages"][0]["text"])]
    assert ag.get(c["id"])["title"].startswith("帮我填这个网申")
    out = ag.send(c["id"], "实习描述用精简版")                      # 它正在做时也能说：先记着，进程起来后递进去
    assert out["messages"][-1]["midturn"] and ag._pending[c["id"]] == ["实习描述用精简版"] and len(ag.started) == 1
    b = ag.new_chat()
    out = ag.send(b["id"], "帮我填另一家")                            # 第二个助手同时干活
    assert out["running"] and not out.get("queued") and len(ag.started) == 2
    d = ag.new_chat()
    out = ag.send(d["id"], "再填一家")                                # 满了：排队，不报错
    assert "最多 2 个" in out["queued"] and len(ag.started) == 2 and out["messages"][-1]["queued"]
    assert ag.waiting_chats() == {d["id"]: out["queued"]} and ag.get(d["id"])["waiting"]
    assert "前面还有" in ag.send(d["id"], "学校填示例大学")["queued"]   # 排着队还能补充
    ag._starting.discard(c["id"])                                     # 空出一个
    ag._drain()
    assert ag.started[-1] == (d["id"], "再填一家\n\n学校填示例大学") and not ag.waiting_chats()
    chat = ag.get(d["id"])
    assert chat["running"] and not chat.get("waiting") and not any(m.get("queued") for m in chat["messages"])
    with pytest.raises(ValueError):
        ag.send(b["id"], "   ")


def test_same_site_waits_other_sites_run(ag, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_PARALLEL", 3)
    ts = [wstasks.add(u, n, "实习生") for u, n in (("https://app.mokahr.com/campus/a#/job/1", "A"),
                                                   ("https://app.mokahr.com/campus/b#/job/2", "B"), ("https://jobs.other.com/3", "C"))]
    c1, c2, c3 = (ag.new_chat(task_id=t["id"]) for t in ts)
    assert not ag.send(c1["id"], "填A").get("queued")
    q = ag.send(c2["id"], "填B")["queued"]
    assert "同一个网站" in q and "app.mokahr.com" in q                   # 同一个网站（同一个账号）不同时改
    assert not ag.send(c3["id"], "填C").get("queued")                    # 别的网站照样同时做
    ag._starting.discard(c1["id"])
    ag._drain()
    assert ag.started[-1][0] == c2["id"] and not ag.waiting_chats()


def test_stop_cancels_queue(ag, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_PARALLEL", 1)
    a, b = ag.new_chat(), ag.new_chat()
    ag.send(a["id"], "填一下")
    assert ag.send(b["id"], "填另一家")["queued"]
    assert ag.stop(b["id"]) is True and not ag.waiting_chats()
    msgs = ag.get(b["id"])["messages"]
    assert msgs[0]["cancelled"] and "排队取消了" in msgs[-1]["text"] and not ag.get(b["id"]).get("waiting")
    ag._starting.discard(a["id"])
    ag._drain()
    assert len(ag.started) == 1                                         # 取消了的不会再开始


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
    monkeypatch.setattr(ag, "system_prompt", lambda *a: "SYS")
    args = ag._args({"session_id": ""})
    assert args[args.index("--tools") + 1] == "Read" and args[args.index("--allowedTools") + 1] == "mcp__claude-in-chrome"   # 读文件只许读网申上传文件夹（见 test_uploads）
    assert "--chrome" in args and "--strict-mcp-config" in args and "--resume" not in args
    assert args[args.index("--disallowedTools") + 1] == "mcp__claude-in-chrome__tabs_close_mcp"   # 关不了标签页，标签组不会丢
    assert ag._args({"session_id": "s1"})[-2:] == ["--resume", "s1"]          # 第二句起接着同一个对话


def test_stop_and_recover(ag):
    c = ag.new_chat()
    assert ag.stop(c["id"]) is False                                 # 没在做
    ag.send(c["id"], "填一下")
    assert ag.stop(c["id"]) is True and ag.get(c["id"])["stopped"]
    ag._runners.clear(); ag._starting.clear()                         # 面板重启：进程都没了
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



def test_system_prompt_leaves_id_field_alone(ag):
    """证件号本人自己手动填：提示词和规则都叫助手空着不碰、不为它停，收尾提一句；只有网站不填就进不了后面才等。
    放光标、复制粘贴那一套都不在了。"""
    s = ag.system_prompt()
    assert "这一栏空着、什么都不做" in s and "证件号空着，你自己填" in s and "不要为它停下来" in s
    assert "不为了过校验往里写任何东西" in s and "不按 ⌘V" in s
    assert "不填证件号就进不了后面" in s and "__wsfill.hasValue" in s          # 唯一会等的情况
    assert "focusField" not in s and "复制证件号" not in s and "光标已经放好" not in s and "输证件号" not in s
    assert "不写进【网申记录】的「要你做」" in s and "不挡路的承诺 / 声明勾选不用等" in s and "还在输" in s


def test_agent_api(ag):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    chat = c.post("/api/agent/new", headers={**LOCAL, **XRW}).get_json()
    d = c.post(f"/api/agent/{chat['id']}/send", json={"text": "你好"}, headers={**LOCAL, **XRW}).get_json()
    assert d["running"] and d["total"] == 1
    assert c.post(f"/api/agent/{chat['id']}/send", json={"text": "再说"}, headers={**LOCAL, **XRW}).status_code == 200   # 做事时也能说
    other = c.post("/api/agent/new", headers={**LOCAL, **XRW}).get_json()
    d2 = c.post(f"/api/agent/{other['id']}/send", json={"text": "你好"}, headers={**LOCAL, **XRW}).get_json()
    assert d2["running"] and not d2.get("queued")                      # 第二个对话同时做
    lst = c.get("/api/agent", headers=LOCAL).get_json()
    assert set(lst["active"]) == {chat["id"], other["id"]} and lst["waiting"] == {} and lst["max_parallel"] >= 2
    assert any(x["title"] == "你好" and x["id"] == chat["id"] for x in lst["chats"])
    assert c.get(f"/api/agent/{chat['id']}?since=2", headers=LOCAL).get_json()["messages"] == []
    assert c.delete(f"/api/agent/{chat['id']}", headers={**LOCAL, **XRW}).status_code == 409   # 还在做：不能删
    assert c.get("/api/agent/nonexistent", headers=LOCAL).status_code == 404


def test_site_notes_only_for_sites_in_chat(ag):
    ag.save_site_notes("【网站笔记】careers.example.com：籍贯下拉要滚动找\n【网站笔记】a.zhiye.com：北森暂存会重排\n【网站笔记】jobs.other.cn：要先选城市")
    chat = {"messages": [{"role": "user", "text": "帮我填 https://b.zhiye.com/campus/123 这个"}]}
    s = ag.system_prompt(chat)
    assert "a.zhiye.com：北森暂存会重排" in s                          # 同一平台的另一家公司也附上
    assert "籍贯下拉要滚动找" not in s and "careers.example.com（1 条）" in s   # 没提到的只列目录
    assert "careers.example.com：籍贯下拉要滚动找" in ag.system_prompt()        # 不给对话就全附上


def test_profile_save_merges_concurrent_edits(prof):
    """页面开着的时候别处改了底稿：页面保存只写它改过的格子，不把别处的改动冲掉。"""
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    page_copy = {"基本信息": [["姓名", "张三"], ["出生日期", ""]], "家庭成员": [{"关系": "父亲", "出生年月": ""}],
                 "_核对提示": ["旧提示"]}
    wsprofile.save(page_copy)
    # 页面打开后，助手 / 另一个窗口改了两处、删了提示
    wsprofile.save({"基本信息": [["姓名", "张三"], ["出生日期", "2000-01-01"]], "家庭成员": [{"关系": "父亲", "出生年月": "1970-01"}],
                    "_核对提示": []})
    mine = {"基本信息": [["姓名", "张小三"], ["出生日期", ""]], "家庭成员": [{"关系": "父亲", "出生年月": ""}], "_核对提示": ["旧提示"]}
    r = c.put("/api/wangshen-profile", json={"profile": mine, "base": page_copy}, headers={**LOCAL, **XRW}).get_json()
    assert r["profile"]["基本信息"] == [["姓名", "张小三"], ["出生日期", "2000-01-01"]]      # 我改的姓名写进去，别处的生日留着
    assert r["profile"]["家庭成员"][0]["出生年月"] == "1970-01" and r["notes"] == []


def test_one_chat_per_wangshen_and_archived_hidden(ag):
    t = wstasks.add("https://jobs.example.com/one", "Z资本", "实习生")
    old1, old2, keep = ag.new_chat(task_id=t["id"]), ag.new_chat(task_id=t["id"]), ag.new_chat(task_id=t["id"])
    free = ag.new_chat()
    wstasks.update(t["id"], chat_id=keep["id"])
    assert ag.tidy_chats() == 2                                          # 同一家多出来的两个收起来
    ids = {c["id"] for c in ag.list_chats()}
    assert ids == {keep["id"], free["id"]}
    assert {c["id"] for c in ag.list_chats(include_archived=True)} >= {old1["id"], old2["id"]}   # 记录还在
    assert ag.get(old1["id"])["archived"] and ag.tidy_chats() == 0


def test_board_line_in_chat_goes_through_progress(ag, monkeypatch):
    """本人在助手对话里说「X 那封不是拒信，是笔试」：助手写一行【看板】原话 → 面板按口述那一套改看板，结果写回对话。"""
    from jobapply import apps, llm, progress, records
    monkeypatch.setattr(config, "DATA_DIR", ag.CHATS_DIR.parent)
    rid = records.add(records.new_record(company_name="聊改资本", job_title="分析师", to_email="hr@lg.com"))
    aid = records.get(rid)["app_id"]
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": [{"app_id": aid, "company": "聊改资本", "position": "", "event": "笔试邀请",
                                                                "round": "", "happened_at": "", "due": "2026-10-13", "quote": "是笔试，13 号截止",
                                                                "value": ""}]}, {}))
    monkeypatch.setattr(ag, "_bg", lambda fn, *a: fn(*a))                       # 测试里同步跑
    c = ag.new_chat()
    ag.apply_event(c["id"], {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "好的，记下。\n【看板】聊改资本那封不是拒信，是笔试，13 号截止"}]}})
    msgs = [(m["role"], m["text"]) for m in ag.get(c["id"])["messages"]]
    assert any(r == "system" and t.startswith("看板已改：聊改资本：笔试邀请，截止 2026-10-13") for r, t in msgs)
    assert records.get(rid)["status"] == "笔试" and apps.get(aid)["next_step"]["due"] == "2026-10-13"
    assert progress.recent()[0]["text"] == "聊改资本那封不是拒信，是笔试，13 号截止"


class _LiveStub:
    pid = 1

    def __init__(self):
        self.sent = []

    def alive(self):
        return True

    def write(self, text):
        self.sent.append(text)


def test_old_process_told_about_rule_changes_once(ag):
    """进程只在起来时拿一次提示词：上线前就在跑的老进程，递给它的第一句前面补上改了什么，之后不再补。"""
    c = ag.new_chat()
    r = ag._Runner(c["id"], _LiveStub())
    ag._runners[c["id"]] = r
    ag.send(c["id"], "接着填")
    ag.send(c["id"], "实习描述用精简版")
    first, second = r.runner.sent
    assert first.startswith("（面板）规则改了") and "证件号这一栏空着" in first and first.endswith("接着填")
    assert second == "实习描述用精简版"
    assert ag._load(c["id"])["prompt_version"] == ag.PROMPT_VERSION
    assert [m["text"] for m in ag.get(c["id"])["messages"]] == ["接着填", "实习描述用精简版"]   # 对话里只记本人的原话


def test_new_process_gets_current_rules_and_no_note(ag, monkeypatch):
    stub = _LiveStub()
    monkeypatch.setattr(ag.agent_runner, "start", lambda *a, **k: stub)
    monkeypatch.setattr(ag, "_ensure_watchdog", lambda: None)
    c = ag.new_chat()
    ag._spawn(c["id"], "帮我填")
    assert ag._load(c["id"])["prompt_version"] == ag.PROMPT_VERSION and stub.sent == ["帮我填"]
    ag.send(c["id"], "再改一下")
    assert stub.sent[-1] == "再改一下"
