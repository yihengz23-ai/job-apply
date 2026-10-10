"""网申待办（「网申」页）：投递页转过来、状态和看板记录跟着走、助手回复里的【网申记录】。不调 AI、不发信、不起 claude。"""

import time

import pytest

import app as panel
from jobapply import agent, config, jobqueue, pipeline, records, wstasks

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(records, "_excel_mirror", lambda *a, **k: None, raising=False)
    return tmp_path


def test_add_dedupes_by_url(store):
    a = wstasks.add("https://jobs.example.com/apply/1?utm_source=wx", "A资本", "投资实习生")
    b = wstasks.add("https://jobs.example.com/apply/1/", "", "", note="又贴了一次")
    assert a["id"] == b["id"] and len(wstasks.list_tasks()) == 1 and b["note"] == "又贴了一次"
    assert wstasks.add("https://jobs.example.com/apply/2")["id"] != a["id"]
    assert wstasks.counts() == {"待填": 2}


def test_filled_then_submitted_goes_to_board(store):
    t = wstasks.add("https://jobs.example.com/a", "A资本", "投资实习生")
    t = wstasks.set_status(t["id"], "已填待提交")
    rec = records.get(t["record_id"])
    assert rec["status"] == "草稿" and rec["send_mode"] == "未发邮件" and rec["company_name"] == "A资本"
    assert rec["apply_url"] == "https://jobs.example.com/a"
    t = wstasks.set_status(t["id"], "已提交")
    rec = records.get(t["record_id"])
    assert rec["status"] == "已投递" and t["submitted_at"] and len(records.load()) == 1   # 同一条，不新建


def test_give_up_removes_draft(store):
    t = wstasks.set_status(wstasks.add("https://jobs.example.com/b", "B资本", "研究员")["id"], "已填待提交")
    rid = t["record_id"]
    t = wstasks.set_status(t["id"], "不投了")
    assert records.get(rid) is None and t["record_id"] == ""


def test_mail_plus_wangshen_merges_into_email_record(store):
    rec = records.new_record(company_name="C资本", job_title="分析师", status="已投递")
    rid = records.add(rec)
    t = wstasks.add("https://jobs.example.com/c", "C资本", "分析师", queue_id="q1")
    wstasks.link_email_record("q1", rid)
    t = wstasks.set_status(t["id"], "已填待提交")                     # 邮件那条已经在看板里：不另建草稿
    assert not t["record_id"] and len(records.load()) == 1
    wstasks.set_status(t["id"], "已提交")
    assert "已同时网申" in records.get(rid)["notes"] and len(records.load()) == 1


def test_markers_update_linked_task_or_create(store):
    t = wstasks.add("https://jobs.example.com/d", "D资本", "实习生")
    text = "填好了，提交由你来。\n【网申记录】公司：D资本｜岗位：实习生｜网址：https://jobs.example.com/d｜状态：已填待提交"
    out = wstasks.apply_markers(text, task_id=t["id"], chat_id="c1")
    assert out[0]["id"] == t["id"] and out[0]["status"] == "已填待提交" and out[0]["chat_id"] == "c1"
    out = wstasks.apply_markers("【网申记录】公司：D资本｜岗位：实习生｜网址：https://jobs.example.com/d｜状态：已提交")
    assert out[0]["id"] == t["id"] and out[0]["status"] == "已提交"
    out = wstasks.apply_markers("【网申记录】公司：E资本｜岗位：分析师｜网址：https://jobs.example.com/e｜状态：已填待提交")
    assert out[0]["company"] == "E资本" and out[0]["source"] == "助手记录" and out[0]["status"] == "已填待提交"
    assert wstasks.apply_markers("没有记录行") == []


@pytest.fixture
def q(store, tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)

    def fake_analyze(jd, **kw):
        if "只能网申" in jd:
            result = {"company_name": "F资本", "job_title": "投资实习生", "to_emails": [], "apply_channel": "网申/链接",
                      "apply_url": "https://jobs.example.com/f", "deadline": "2026-10-31"}
        else:
            result = {"company_name": "G资本", "job_title": "分析师", "to_emails": ["hr@g.com"], "apply_channel": "邮箱+网申",
                      "apply_url": "https://jobs.example.com/g", "email_subject": "主题", "email_body": "正文"}
        return {"ok": True, "result": result, "issues": [], "fixes": [], "related": [], "meta": {}}
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    monkeypatch.setattr(pipeline, "wangshen", lambda *a, **k: pytest.fail("不该再自动生成网申问答"))
    return jobqueue


def _wait(q, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not any(it["status"] in q.ACTIVE for it in q.list_items()):
            return
        time.sleep(0.05)
    raise AssertionError("队列没处理完")


def test_queue_routes_wangshen_only_jobs(q):
    q.enqueue("这个岗位只能网申，" + "岗位描述很长" * 20)
    q.enqueue("发邮件也要网申，" + "岗位描述很长" * 20)
    _wait(q)
    items = {it["analysis"]["result"]["company_name"]: it for it in q.list_items()}
    assert items["F资本"]["status"] == "转网申" and items["G资本"]["status"] == "待审核"
    tasks = {t["company"]: t for t in wstasks.list_tasks()}
    assert tasks["F资本"]["url"] == "https://jobs.example.com/f" and tasks["F资本"]["deadline"] == "2026-10-31"
    assert tasks["F资本"]["source"] == "投递页（只能网申）" and tasks["G资本"]["source"] == "投递页（邮件 + 网申）"
    q.mark_done(items["G资本"]["id"], "已发送", "rec-1")             # 邮件发出：网申那条记下邮件记录
    assert wstasks.get(tasks["G资本"]["id"])["email_record_id"] == "rec-1"


def test_api_and_agent_link(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    sent = []
    monkeypatch.setattr(agent, "send", lambda cid, text: sent.append((cid, text)) or {"id": cid, "messages": [], "total": 1, "running": True})
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    assert c.post("/api/wstasks", json={"url": "不是链接"}, headers={**LOCAL, **XRW}).status_code == 400
    t = c.post("/api/wstasks", json={"url": "https://jobs.example.com/h", "company": "H资本", "job": "实习生"},
               headers={**LOCAL, **XRW}).get_json()["task"]
    d = c.get("/api/wstasks", headers=LOCAL).get_json()
    assert d["counts"] == {"待填": 1} and d["tasks"][0]["id"] == t["id"]
    out = c.post(f"/api/wstasks/{t['id']}/agent", headers={**LOCAL, **XRW}).get_json()
    assert sent and "https://jobs.example.com/h" in sent[0][1] and "H资本" in sent[0][1]
    assert agent.get(out["id"])["task_id"] == t["id"] and wstasks.get(t["id"])["chat_id"] == out["id"]
    again = c.post(f"/api/wstasks/{t['id']}/agent", headers={**LOCAL, **XRW}).get_json()   # 中途停了再点：接着原来的对话
    assert again["id"] == out["id"] and sent[-1][0] == out["id"] and "原来那个画了颜色框的网页" in sent[-1][1] and "不要让本人拖标签页" in sent[-1][1]
    assert sent[0][1].startswith("（面板）") and sent[-1][1].startswith("（面板）本人在网申页点了「让助手接着做」")   # 面板发的话不冒充本人
    assert len(agent.list_chats()) == 1
    r = c.put(f"/api/wstasks/{t['id']}", json={"status": "已提交"}, headers={**LOCAL, **XRW}).get_json()["task"]
    assert r["status"] == "已提交" and records.get(r["record_id"])["status"] == "已投递"
    assert c.put(f"/api/wstasks/{t['id']}", json={"status": "乱写"}, headers={**LOCAL, **XRW}).status_code == 400
    assert c.delete(f"/api/wstasks/{t['id']}", headers={**LOCAL, **XRW}).get_json()["ok"]
    assert records.get(r["record_id"])["status"] == "已投递"            # 已提交的记录留在看板里


def test_agent_reply_marker_updates_task(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    t = wstasks.add("https://jobs.example.com/i", "I资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    agent.apply_event(chat["id"], {"type": "assistant", "message": {"content": [{"type": "text", "text":
        "都填好了，没点提交。\n【网申记录】公司：I资本｜岗位：实习生｜网址：https://jobs.example.com/i｜状态：已填待提交"}]}})
    task = wstasks.get(t["id"])
    assert task["status"] == "已填待提交" and records.get(task["record_id"])["status"] == "草稿"
    assert any("「网申」页：I资本｜实习生 → 已填待提交" in m["text"] for m in agent.get(chat["id"])["messages"])


def test_account_syncs_to_board_and_from_marker(store):
    t = wstasks.add("https://jobs.example.com/j", "J资本", "实习生")
    t = wstasks.set_status(t["id"], "已填待提交")
    wstasks.update(t["id"], account="180****0000")
    assert records.get(t["record_id"])["apply_account"] == "180****0000"       # 看板那条跟着改
    u = wstasks.add("https://jobs.example.com/k", "K资本", "实习生")
    wstasks.apply_markers("【网申记录】公司：K资本｜岗位：实习生｜网址：https://jobs.example.com/k｜账号：19800000000｜状态：已填待提交",
                          task_id=u["id"])
    u = wstasks.get(u["id"])
    assert u["account"] == "19800000000" and records.get(u["record_id"])["apply_account"] == "19800000000"


# ── 读回：提交以后网站上的真实内容 → 看板 ──────────────────────

def test_readback_key_is_per_task_and_secret(store):
    a, b = wstasks.add("https://jobs.example.com/r1"), wstasks.add("https://jobs.example.com/r2")
    ka = wstasks.readback_key(a["id"])
    assert ka == wstasks.readback_key(a["id"]) and ka != wstasks.readback_key(b["id"]) and len(ka) == 20
    assert wstasks.check_readback_key(a["id"], ka) and not wstasks.check_readback_key(b["id"], ka)
    assert not wstasks.check_readback_key(a["id"], "") and wstasks.KEY_PATH.exists()


def test_readback_before_submit_is_carried_to_record(store):
    t = wstasks.add("https://jobs.example.com/r3", "R资本", "投资实习生")
    t, rid = wstasks.save_readback(t["id"], "resume", "姓名：章三\n身份证号：110101200001011234\n实习：A资本",
                                   url="https://jobs.example.com/my/resume", account="199****0000")
    assert rid == "" and "[证件号已隐去]" in t["readback"]["resume"]["text"] and "110101" not in t["readback"]["resume"]["text"]
    assert t["account"] == "199****0000"
    t = wstasks.set_status(t["id"], "已提交")
    rec = records.get(t["record_id"])
    assert rec["status"] == "已投递" and "实际提交的简历" in rec["ws_submitted"] and "实习：A资本" in rec["ws_submitted"]
    assert rec["apply_account"] == "199****0000" and "1234" not in rec["ws_submitted"]


def test_readback_after_submit_updates_record_and_status(store):
    t = wstasks.set_status(wstasks.add("https://jobs.example.com/r4", "S资本", "分析师")["id"], "已提交")
    wstasks.update(t["id"], account="19900000000")
    t, rid = wstasks.save_readback(t["id"], "status", "投递岗位 分析师 当前状态 HR初筛", status="HR初筛", account="199****0000")
    rec = records.get(rid)
    assert rid == t["record_id"] and rec["site_status"] == "HR初筛" and rec["site_status_at"]
    assert rec["apply_account"] == "19900000000"                  # 已经记了的账号不被页面上打码的覆盖
    assert rec["ws_submitted"].startswith("【网站上的投递记录 / 进度")
    wstasks.save_readback(t["id"], "resume", "在线简历原文")
    rec = records.get(rid)
    assert "网站上的投递记录" in rec["ws_submitted"] and "在线简历原文" in rec["ws_submitted"]
    with pytest.raises(ValueError):
        wstasks.save_readback(t["id"], "resume", "   ")


def test_readback_endpoint_cross_origin_with_key(store, monkeypatch, tmp_path):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    t = wstasks.set_status(wstasks.add("https://jobs.example.com/r5", "T资本", "研究员")["id"], "已提交")
    url = f"/api/wsreadback/{t['id']}"
    pre = c.open(url, method="OPTIONS", headers={**LOCAL, "Origin": "https://jobs.example.com",
                                                 "Access-Control-Request-Private-Network": "true"})
    assert pre.status_code == 204 and pre.headers["Access-Control-Allow-Private-Network"] == "true"
    body = {"kind": "status", "text": "当前状态：笔试", "status": "笔试", "url": "https://jobs.example.com/my"}
    bad = c.post(url, data=__import__("json").dumps({**body, "key": "猜的"}), headers={**LOCAL, "Content-Type": "text/plain"})
    assert bad.status_code == 403 and bad.headers["Access-Control-Allow-Origin"] == "*"
    ok = c.post(url, data=__import__("json").dumps({**body, "key": wstasks.readback_key(t["id"])}),
                headers={**LOCAL, "Content-Type": "text/plain"})       # 网申网站页面发来的：没有面板的请求头
    assert ok.status_code == 200 and ok.get_json()["record"] == t["record_id"]
    assert records.get(t["record_id"])["site_status"] == "笔试"
    tunnel = c.post(url, data="{}", headers={**LOCAL, "Cf-Ray": "x", "Content-Type": "text/plain"})
    assert tunnel.status_code == 403                                    # 外网隧道进来的一律不收
    assert c.post("/api/wstasks", json={"url": "https://x.com"}, headers=LOCAL).status_code == 403   # 别的接口照旧要面板请求头


def test_submit_click_starts_readback_or_queues(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    sent = []
    monkeypatch.setattr(agent, "send", lambda cid, text: sent.append((cid, text)) or {"id": cid, "messages": [], "total": 1})
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    t = wstasks.add("https://jobs.example.com/r6", "U资本", "实习生")
    d = c.put(f"/api/wstasks/{t['id']}", json={"status": "已提交"}, headers={**LOCAL, **XRW}).get_json()
    assert "读回" in d["readback"] and d["task"]["readback_state"] == "读回中"
    cid, text = sent[-1]
    assert d["task"]["chat_id"] == cid and agent.get(cid)["task_id"] == t["id"]
    assert t["id"] in text and wstasks.readback_key(t["id"]) in text and "kind: 'status'" in text and "kind: 'jd'" in text
    assert "【网申读回】类型：岗位JD" in text and "不要点任何按钮" in text
    agent._task_turn_done(cid)                                          # 这一轮做完了也没读回来
    assert wstasks.get(t["id"])["readback_state"] == "没读回（看对话）"

    why = "已经有 3 个助手在同时干活（最多 3 个），空出一个就轮到它"
    monkeypatch.setattr(agent, "send", lambda cid, text: {"id": cid, "messages": [], "total": 1, "queued": why})
    u = wstasks.add("https://jobs.example.com/r7", "V资本", "实习生")
    d = c.put(f"/api/wstasks/{u['id']}", json={"status": "已提交"}, headers={**LOCAL, **XRW}).get_json()
    assert d["readback"].startswith("排队中") and wstasks.get(u["id"])["readback_state"] == "排队等读回"
    agent._task_started(wstasks.get(u["id"])["chat_id"])                # 轮到它了
    assert wstasks.get(u["id"])["readback_state"] == "读回中"


def test_marker_submitted_reads_back_after_turn(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    monkeypatch.setattr(agent, "_after_turn", {})
    t = wstasks.add("https://jobs.example.com/r8", "W资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    agent.apply_event(chat["id"], {"type": "assistant", "message": {"content": [{"type": "text", "text":
        "好的，记上了。\n【网申记录】公司：W资本｜岗位：实习生｜网址：https://jobs.example.com/r8｜状态：已提交"}]}})
    assert wstasks.get(t["id"])["status"] == "已提交" and agent._after_turn == {chat["id"]: [t["id"]]}


def test_task_list_shows_queue_state(store, monkeypatch):
    panel.app.config["TESTING"] = True
    t = wstasks.update(wstasks.add("https://jobs.example.com/r9", "X资本", "实习生")["id"], chat_id="c9")
    monkeypatch.setattr(agent, "waiting_chats", lambda: {"c9": "另一个助手正在填同一个网站（jobs.example.com）"})
    d = panel.app.test_client().get("/api/wstasks", headers=LOCAL).get_json()
    x = next(i for i in d["tasks"] if i["id"] == t["id"])
    assert x["agent_state"] == "排队中" and "同一个网站" in x["agent_wait"] and d["waiting"] == 1 and d["max_parallel"] >= 1


# ── 现在轮到谁：等你处理（要你做什么）────────────────────────

def test_wait_state_from_marker_and_from_silent_stop(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    t = wstasks.add("https://jobs.example.com/w1", "Y资本", "实习生")
    assert isinstance(t["color"], int)
    chat = agent.new_chat(task_id=t["id"])
    agent._task_started(chat["id"])
    assert wstasks.get(t["id"])["status"] == "助手在填"
    agent.apply_event(chat["id"], {"type": "assistant", "message": {"content": [{"type": "text", "text":
        "能填的都填了。\n【网申记录】公司：Y资本｜岗位：实习生｜网址：https://jobs.example.com/w1｜状态：等你处理｜要你做：在画了蓝框的网页里输证件号并保存"}]}})
    x = wstasks.get(t["id"])
    assert x["status"] == "等你处理" and "证件号" in x["todo"]
    agent._task_turn_done(chat["id"])                                   # 已经报了「等你处理」：不改
    assert wstasks.get(t["id"])["todo"] == x["todo"]
    from app import _continue_message
    msg = _continue_message(x)                                          # 面板发的，不替本人说「我弄好了」
    assert msg.startswith("（面板）本人在网申页点了「让助手接着做」") and "在画了蓝框的网页里输证件号并保存" in msg
    assert "我弄好了" not in msg and "先看一眼网页确认" in msg
    assert "__wsfill.hasValue" in msg and "不截那一栏、不点进去" in msg     # 证件号：只问填没填，不截图、不点
    assert "可以上传的文件（以这份为准）" in msg                            # 本人可能刚往文件夹里放了照片
    agent._task_started(chat["id"])                                     # 本人点了接着做，助手接着干
    x = wstasks.get(t["id"])
    assert x["status"] == "助手在填" and x["todo"]                       # todo 只在助手报新状态时更新
    agent._append(chat["id"], "assistant", "**卡住了**：网站要先登录。\n【网站笔记】jobs.example.com：要登录")
    agent._task_turn_done(chat["id"])                                   # 没报状态就停了：状态不改，记一句为什么停，不拿最后一句凑「要你做」
    x = wstasks.get(t["id"])
    assert x["status"] == "助手在填" and "没说要你做什么" in x["halted"] and "卡住了" not in x["todo"]
    assert "没说要你做什么" in _continue_message(x)
    agent._task_started(chat["id"])
    assert wstasks.get(t["id"])["halted"] == ""                        # 又干起来了：清掉
    wstasks.set_status(t["id"], "已填待提交")
    assert wstasks.get(t["id"])["todo"] == ""                           # 换到别的状态，要你做的清掉


def test_colors_spread_over_active_tasks(store):
    ts = [wstasks.add(f"https://jobs.example.com/c{i}", f"C{i}") for i in range(4)]
    assert len({t["color"] for t in ts}) == 4                            # 还没提交的几家不撞色
    assert wstasks.color_of(ts[0])[1].startswith("#") and wstasks.short_name({"url": "https://www.abc.com/x"}) == "abc.com"


# ── 每家记全三样：投了哪些岗位、每个岗位的 JD、实际提交的简历 ──────────

def test_multi_position_records_each_with_own_jd(store):
    t = wstasks.set_status(wstasks.add("https://job.example.com/bank", "某银行", "校招简历（岗位未选）")["id"], "已提交")
    base = t["record_id"]
    wstasks.save_readback(t["id"], "status", "我的投递：管培生 报名成功；研究员 报名成功", status="报名成功",
                          positions="总行管理培训生（北京）；理财子公司－研究交易岗（北京）")
    wstasks.save_readback(t["id"], "jd", "管培生 JD 原文：轮岗两年……", position="总行管理培训生", location="北京")
    wstasks.save_readback(t["id"], "jd", "研究岗 JD 原文：宏观与固收研究……", position="理财子公司－研究交易岗", location="北京")
    t, rid = wstasks.save_readback(t["id"], "resume", "教育：示例大学……\n身份证号 110101200001011234")
    recs = [r for r in records.load() if r.get("id") == base or r.get("ws_task_id") == t["id"]]
    assert len(recs) == 2 and rid == base
    by = {r["job_title"]: r for r in recs}
    assert set(by) == {"总行管理培训生", "理财子公司－研究交易岗"} and by["总行管理培训生"]["id"] == base   # 「岗位未选」那条改成第一个岗位
    assert by["总行管理培训生"]["jd_text"].startswith("管培生 JD") and by["理财子公司－研究交易岗"]["jd_text"].startswith("研究岗 JD")
    for r in recs:
        assert r["status"] == "已投递" and r["job_location"] == "北京" and "示例大学" in r["ws_submitted"]
        assert "110101200001011234" not in r["ws_submitted"] and r["site_status"] == "报名成功"
    wstasks.save_readback(t["id"], "jd", "管培生 JD 更新版", position="总行管理培训生")      # 再读一次不会多建
    assert len([r for r in records.load() if r.get("id") == base or r.get("ws_task_id") == t["id"]]) == 2
    assert records.get(base)["jd_text"] == "管培生 JD 更新版"


def test_jd_before_submit_kept_until_record_exists(store):
    t = wstasks.add("https://job.example.com/b2", "某券商", "")
    t, rid = wstasks.save_readback(t["id"], "jd", "投研岗 JD", position="投研岗", location="上海")
    assert rid == "" and t["jds"]["投研岗"]["text"] == "投研岗 JD" and t["positions"][0]["location"] == "上海"
    t = wstasks.set_status(t["id"], "已提交")
    r = records.get(t["record_id"])
    assert r["job_title"] == "投研岗" and r["jd_text"] == "投研岗 JD" and r["job_location"] == "上海"
    with pytest.raises(ValueError):
        wstasks.save_readback(t["id"], "jd", "没有岗位名的 JD")


def test_readback_blocks_in_reply_are_recorded(store, monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    t = wstasks.set_status(wstasks.add("https://job.example.com/b3", "某银行", "")["id"], "已提交")
    chat = agent.new_chat(task_id=t["id"])
    reply = ("这家银行网站连不上面板，原文如下。\n"
             "【网申读回】类型：投递记录｜岗位：管培生（北京）；投募资岗（江苏南京）｜进度：报名成功｜账号：199****0000\n我的投递\n管培生 报名成功\n投募资岗 报名成功\n【/网申读回】\n"
             "【网申读回】类型：岗位JD｜岗位：投募资岗｜地点：江苏南京\n负责股权投资项目的募资与投资……\n【/网申读回】\n"
             "【网申读回】类型：简历\n示例大学 公共政策 硕士\n【/网申读回】\n"
             "【网申记录】公司：某银行｜状态：已提交")
    agent.apply_event(chat["id"], {"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
    recs = {r["job_title"]: r for r in records.load() if r.get("id") == t["record_id"] or r.get("ws_task_id") == t["id"]}
    assert set(recs) == {"管培生", "投募资岗"} and recs["投募资岗"]["job_location"] == "江苏南京"
    assert recs["投募资岗"]["jd_text"].startswith("负责股权投资") and "示例大学" in recs["管培生"]["ws_submitted"]
    assert wstasks.get(t["id"])["account"] == "199****0000"
    sysmsgs = [m["text"] for m in agent.get(chat["id"])["messages"] if m["role"] == "system"]
    assert any("岗位 JD「投募资岗」" in m for m in sysmsgs) and any("实际提交的简历" in m for m in sysmsgs)
    assert agent._after_turn.get(chat["id"]) in (None, [])                # 已经读回过：不再另开一轮去读


def test_marker_back_to_filling_after_user_did_the_web_step(tmp_path, monkeypatch):
    """网站不填证件号就不让往下时，助手在这一轮里等本人填：先报「等你处理」（面板马上提醒），本人填好后它报「在填」，这一行回到「助手在填」。"""
    from jobapply import wstasks
    t = wstasks.add("https://jobs.example.com/idwait", "等号资本", "分析师")
    wstasks.set_status(t["id"], "助手在填")
    wstasks.apply_markers("【网申记录】公司：等号资本｜状态：等你处理｜要你做：证件号（网站不填不让往下：蓝框网页「基本信息」那一栏）", task_id=t["id"])
    assert wstasks.get(t["id"])["status"] == "等你处理" and "证件号" in wstasks.get(t["id"])["todo"]
    wstasks.apply_markers("【网申记录】公司：等号资本｜状态：在填", task_id=t["id"])
    assert wstasks.get(t["id"])["status"] == "助手在填" and wstasks.get(t["id"])["todo"] == ""


def test_assistant_reports_volunteer_mode_but_never_overrides_the_user(tmp_path):
    from jobapply import apps, wstasks
    t = wstasks.add("https://jobs.example.com/vol", "志愿社区", "")
    wstasks.apply_markers("【网申记录】公司：志愿社区｜岗位：科技投资（第一志愿）、财务管培生（第二志愿）｜志愿方式：串行（原话：投递后志愿将按顺序依次流转）",
                          task_id=t["id"])
    a = apps.get(t["id"])
    assert a["volunteer_mode"] == "串行" and "依次流转" in a["volunteer_note"]
    apps.set_volunteer_mode(t["id"], "平行", by="本人")                          # 本人改过
    wstasks.apply_markers("【网申记录】公司：志愿社区｜志愿方式：串行", task_id=t["id"])
    assert apps.get(t["id"])["volunteer_mode"] == "平行"                          # 不覆盖本人的


@pytest.mark.parametrize("word", ["在填", "在填（证件号你填好了，我接着填）", "在填（不用等你了）", "接着填", "继续填写"])
def test_marker_filling_wins_over_words_in_brackets(word):
    from jobapply import wstasks
    assert wstasks._marker_status(word) == "助手在填"
