"""WP1 热修：已提交是终态、投递时间只写一次、多岗位不丢、账号不收占位话、未知顶层键保留、
强制发送跳不过硬问题、不写假网申内容、面板重启 / 停下不冒充本人、防重复开对话、一次性数据小修。不调 AI、不碰 Gmail。"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

import app as panel
from jobapply import agent, checks, config, jobqueue, pipeline, records, wstasks
from tests.test_review import add_item, result
from tests.test_schedule import JD, LOCAL, XRW, clean_review, client, q  # noqa: F401  （夹具）

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(agent, "CHATS_DIR", tmp_path / "chats")
    return tmp_path


def say(chat_id, text):
    agent.apply_event(chat_id, {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


# ── 1. 已提交、不投了是终态 ─────────────────────────────────────

def test_submitted_is_terminal(store):
    t = wstasks.add("https://jobs.example.com/t1", "T资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    t = wstasks.set_status(t["id"], "已提交")
    say(chat["id"], "要登录才能读回。\n【网申记录】公司：T资本｜状态：等你处理｜要你做：在蓝框网页里登录")
    x = wstasks.get(t["id"])
    assert x["status"] == "已提交" and x["readback_state"] == "读回等你登录" and not x.get("todo")
    say(chat["id"], "【网申记录】公司：T资本｜状态：已填待提交")
    assert wstasks.get(t["id"])["status"] == "已提交"
    say(chat["id"], "【网申记录】公司：T资本｜账号：19800000000｜进度：笔试｜状态：等你处理")   # 补账号、进度可以
    x = wstasks.get(t["id"])
    assert x["status"] == "已提交" and x["account"] == "19800000000" and x["site_status"] == "笔试"
    assert records.get(x["record_id"])["site_status"] == "笔试"
    u = wstasks.set_status(wstasks.add("https://jobs.example.com/t2", "U资本")["id"], "不投了")
    wstasks.apply_markers("【网申记录】公司：U资本｜状态：已填待提交", task_id=u["id"])
    assert wstasks.get(u["id"])["status"] == "不投了"
    assert wstasks.set_status(u["id"], "待填")["status"] == "待填"          # 本人在网申页点按钮仍然可以改


def test_status_message_only_when_it_changes(store):
    t = wstasks.add("https://jobs.example.com/m1", "M资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    for _ in range(3):
        say(chat["id"], "【网申记录】公司：M资本｜岗位：实习生｜状态：已填待提交")
    notes = [m["text"] for m in agent.get(chat["id"])["messages"] if m["role"] == "system"]
    assert len([n for n in notes if "「网申」页" in n]) == 1 and not any("看板已同步" in n for n in notes)


# ── 2. 投递时间只写一次 ────────────────────────────────────────

def test_first_submitted_once(store):
    t = wstasks.set_status(wstasks.add("https://jobs.example.com/s1", "S资本", "分析师")["id"], "已填待提交")
    rid = t["record_id"]
    assert records.get(rid)["status"] == "草稿"
    t = wstasks.set_status(t["id"], "已提交")
    rec = records.get(rid)
    first_at, first_ts, sub_at = rec["sent_at"], rec["sent_ts"], t["submitted_at"]
    assert rec["status"] == "已投递"
    time.sleep(0.01)
    records.update(rid, {"sent_at": "2026-10-08 21:29"})                 # 当成真实投递时间
    wstasks.update(t["id"], status="等你处理")                            # 读回时被打回过（旧数据）
    t = wstasks.set_status(t["id"], "已提交")                              # 再报一次已提交
    assert records.get(rid)["sent_at"] == "2026-10-08 21:29" and t["submitted_at"] == sub_at
    records.update(rid, {"status": "笔试"})
    wstasks.update(t["id"], status="等你处理")
    wstasks.set_status(t["id"], "已提交")
    assert records.get(rid)["status"] == "笔试"                           # 不往回改
    assert first_ts


# ── 7. 多个岗位不再丢 ─────────────────────────────────────────

def test_volunteers_split_into_positions(store):
    t = wstasks.add("https://jobs.example.com/v1", "某银行", "")
    chat = agent.new_chat(task_id=t["id"])
    say(chat["id"], "【网申记录】公司：某银行｜岗位：总行管培生（北京）（第一志愿）、理财子公司研究岗（第二志愿）｜状态：已填待提交")
    x = wstasks.get(t["id"])
    pos = {p["name"]: p for p in x["positions"]}
    assert set(pos) == {"总行管培生", "理财子公司研究岗"}
    assert pos["总行管培生"]["rank"] == 1 and pos["总行管培生"]["location"] == "北京" and pos["理财子公司研究岗"]["rank"] == 2
    assert x["job"] == "总行管培生、理财子公司研究岗" and x["status"] == "已填待提交"
    assert wstasks.split_jobs("投资经理") == [] and wstasks.split_jobs("A；B")[1]["name"] == "B"


# ── 8. 账号 ───────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["（页面上没显示）", "见网页", "照抄", "未知", "无", "（）", "页面上看不到账号"])
def test_account_placeholders_rejected(value):
    assert wstasks.clean_account(value) == "" and wstasks.is_placeholder_account(value)


def test_account_upgrade_rules():
    assert wstasks.clean_account("19800000000") == "19800000000"
    assert wstasks.clean_account("a@b.com", "") == "a@b.com"
    assert wstasks.clean_account("Moka 账号 Abc123", "（页面上没显示）") == "Moka 账号 Abc123"   # 原来是占位话：换
    assert wstasks.clean_account("19800000000", "198****0000") == "19800000000"                 # 同一个号码更完整：换
    assert wstasks.clean_account("198****0000", "19800000000") == ""                            # 打码的不覆盖完整的
    assert wstasks.clean_account("13800000000", "19800000000") == ""                            # 别的号码不覆盖


def test_marker_placeholder_account_not_stored(store):
    t = wstasks.add("https://jobs.example.com/a1", "A资本", "实习生")
    wstasks.apply_markers("【网申记录】公司：A资本｜账号：（页面上没显示）｜状态：已填待提交", task_id=t["id"])
    assert wstasks.get(t["id"])["account"] == ""
    wstasks.apply_markers("【网申记录】公司：A资本｜账号：198****0000｜状态：已填待提交", task_id=t["id"])
    assert wstasks.get(t["id"])["account"] == "198****0000"


# ── 10. 保存保留不认识的顶层键 ─────────────────────────────────

def test_save_keeps_unknown_top_level_keys(store):
    config.RECORDS_PATH.write_text(json.dumps({"records": [], "schema": 3, "applications": [{"id": "a1"}], "future_key": {"x": 1}}),
                                   encoding="utf-8")
    records.save([records.new_record(company_name="测试资本")])          # 只换岗位列表的老接口
    data = json.loads(config.RECORDS_PATH.read_text(encoding="utf-8"))
    assert data["future_key"] == {"x": 1} and data["applications"] == [{"id": "a1"}] and data["schema"] == 3 and len(data["records"]) == 1


def test_save_refuses_to_overwrite_corrupt_file(store):
    config.RECORDS_PATH.write_text("{坏了", encoding="utf-8")
    with pytest.raises(records.RecordsCorrupt):
        records.save([])
    assert config.RECORDS_PATH.read_text(encoding="utf-8") == "{坏了"


# ── 11. 不写假网申内容 ────────────────────────────────────────

def test_record_web_application_keeps_kit_out_of_wangshen(store):
    kit = {"self_intro": "面板生成的自我介绍", "platform": "Moka"}
    out = pipeline.record_web_application(result(apply_channel="网申/链接", to_emails=[]), JD, kit=kit)
    rec = records.get(out["record_id"])
    assert not rec.get("wangshen") and rec["wangshen_unused"]["self_intro"] == "面板生成的自我介绍"
    rid = records.add(records.new_record(company_name="邮件资本", notes=""))
    pipeline.record_web_application({}, "", kit=kit, record_id=rid, upload_version="中文")
    rec = records.get(rid)
    assert not rec.get("wangshen") and rec["notes"].endswith("已同时网申") and "上传" not in rec["notes"]


# ── 12. 强制发送跳不过硬问题 ───────────────────────────────────

def test_unskippable_classification():
    hard = {"level": "error", "field": "body", "msg": "还有没填的占位：[是否接受出差]"}
    soft = {"level": "error", "field": "to", "msg": "邮箱 a@b.com 在 JD 原文里找不到，可能是 AI 编的或抄错了，请核对。"}
    assert checks.unskippable(hard) and not checks.unskippable(soft)
    assert checks.unskippable({"level": "error", "field": "resume", "msg": "简历打不开"})
    assert not checks.unskippable({"level": "warn", "field": "body", "msg": "还有没填的占位：x"})


def test_force_send_and_schedule_blocked_by_placeholder(client, clean_review, monkeypatch):
    clean_review["issues"] = [{"level": "error", "field": "body", "msg": "还有没填的占位：[是否接受出差及可接受频率]"}]
    iid = add_item(jobqueue)
    r = client.post("/api/schedule", json={"result": result(), "jd_text": JD, "queue_id": iid, "force": True}, headers={**LOCAL, **XRW})
    assert r.status_code == 409 and "不能跳过" in r.get_json()["error"] and jobqueue._get(iid)["status"] == "待审核"
    monkeypatch.setattr(pipeline.gmail_client, "send", lambda **kw: pytest.fail("不该发出"))
    r = client.post("/api/send", json={"result": result(), "jd_text": JD, "queue_id": iid, "mode": "send", "force": True},
                    headers={**LOCAL, **XRW})
    assert r.status_code == 409 and "不能跳过" in r.get_json()["error"] and r.get_json()["hard"]
    with pytest.raises(pipeline.Blocked) as e:
        pipeline.deliver(result(), JD, mode="send", force=True)
    assert e.value.hard
    clean_review["issues"] = [{"level": "error", "field": "to", "msg": "邮箱 hr@abc-capital.com 在 JD 原文里找不到"}]
    r = client.post("/api/schedule", json={"result": result(), "jd_text": JD, "queue_id": iid, "force": True}, headers={**LOCAL, **XRW})
    assert r.status_code == 200                                          # 能确认的那几类，强制还是可以


def test_scheduled_force_mail_still_blocked_at_send_time(q, clean_review, monkeypatch):
    iid = add_item(q)
    q.schedule(iid, result(), force=True, send_at="2026-10-09 10:00")
    clean_review["issues"] = [{"level": "error", "field": "body", "msg": "还有没填的占位：[XX]"}]
    monkeypatch.setattr(pipeline.gmail_client, "send", lambda **kw: pytest.fail("不该发出"))
    monkeypatch.setattr(q, "_notify", lambda *a: None)
    from datetime import datetime
    out = q.send_due(datetime(2026, 10, 9, 10, 1), gap=0)
    assert out["failed"] and q._get(iid)["status"] == "需处理" and "必须处理" in q._get(iid)["error"]


# ── 3 / 4 / 5. 面板重启、停下、接着做：不冒充本人 ─────────────────

def test_recover_after_restart(store):
    t = wstasks.add("https://jobs.example.com/r1", "R资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    wstasks.update(t["id"], status="助手在填", chat_id=chat["id"])
    q2 = wstasks.add("https://jobs.example.com/r2", "Q资本", "实习生")
    qchat = agent.new_chat(task_id=q2["id"])
    c = agent._load(qchat["id"])
    c["waiting"] = "已经有 3 个助手在同时干活"
    c["messages"].append({"role": "user", "text": "排着的话", "at": "x", "queued": True})
    agent._save(c)
    rb = wstasks.set_status(wstasks.add("https://jobs.example.com/r3", "B资本")["id"], "已提交")
    wstasks.update(rb["id"], readback_state="读回中")
    agent.recover()
    x = wstasks.get(t["id"])
    assert x["status"] == "助手在填" and x["halted"] == "面板重启把它打断了" and not x.get("todo")
    from app import _continue_message
    assert "我弄好了" not in _continue_message(x) and "面板重启把它打断了" in _continue_message(x)
    c = agent._load(qchat["id"])
    assert "waiting" not in c and not any(m.get("queued") for m in c["messages"])
    assert "面板重启前它在排队" in c["messages"][-1]["text"]
    assert wstasks.get(rb["id"])["readback_state"] == "没读回（面板重启）"


def test_stop_without_status_keeps_todo_explicit(store):
    t = wstasks.add("https://jobs.example.com/x1", "X资本", "实习生")
    chat = agent.new_chat(task_id=t["id"])
    agent._task_started(chat["id"])
    agent._append(chat["id"], "assistant", "Let me check the page first.")
    agent._task_turn_done(chat["id"], stopped=True)
    x = wstasks.get(t["id"])
    assert x["status"] == "助手在填" and x["halted"] == "本人点了停下" and not x.get("todo")


def test_panel_messages_prefixed(store):
    from app import _continue_message, _fill_message
    t = wstasks.add("https://jobs.example.com/p1", "P资本", "实习生", jd_text="JD" * 3000)
    fill = _fill_message(t)
    assert fill.startswith("（面板）本人在网申页点了「让助手填」") and len(fill) > 5000      # JD 不再截成 800 字
    assert "要本人在网页上做的事" in fill and "替本人做的选择" in fill
    assert _continue_message(wstasks.get(t["id"])).startswith("（面板）")
    assert agent.readback_message(t).startswith("（面板）")


def test_system_prompt_matches_rules_on_login_and_photos(store, monkeypatch):
    monkeypatch.setattr(agent, "_uploads_text", lambda: "（测试）")
    text = agent.system_prompt({"messages": []})
    assert "留在这一轮里等" in text and "照片、简历这些附件自己传" in text
    assert "证件号（身份证号、护照号）一律不填" in text                    # 证件号留给本人
    assert "要你做：在画了绿框的" not in text and "传照片、最后提交）先跳过" not in text


# ── 14. 快速点两次「让助手填」只开一个对话 ─────────────────────────

def test_double_click_opens_one_chat(store, monkeypatch):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    t = wstasks.add("https://jobs.example.com/d1", "D资本", "实习生")

    def slow_send(cid, text):
        time.sleep(0.2)
        return {"id": cid, "messages": [], "total": 0, "running": True}
    monkeypatch.setattr(agent, "send", slow_send)
    outs = []
    th = [threading.Thread(target=lambda: outs.append(c.post(f"/api/wstasks/{t['id']}/agent", headers={**LOCAL, **XRW}).get_json()))
          for _ in range(2)]
    [x.start() for x in th]
    [x.join() for x in th]
    assert len({o["id"] for o in outs}) == 1 and len(agent.list_chats()) == 1


# ── 13. 前端三处（静态检查）──────────────────────────────────────

def test_frontend_fixes_present():
    html = "\n".join(p.read_text(encoding="utf-8") for p in    # 前端拆成了模板和 static/js 下的脚本：拼起来查
                     sorted((ROOT / "templates").rglob("*.html")) + sorted((ROOT / "static" / "js").glob("*.js")))
    assert "agAppend(d.messages.slice(AG.n))" in html                       # 插话后不重复
    assert "await agOpenChat(d.task.chat_id)" in html                        # 我已提交后打开这一家的对话
    assert "我弄好了" not in html and "wsWriteBack(" in html and "WT.inflight" in html
    assert "function noteWsOpened() {}" in html
