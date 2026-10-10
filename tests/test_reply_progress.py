"""WP5 邮件识别：来信是哪一类进展（笔试 / 测评 / AI 面 / 面试邀请、拒信、offer）、截止时间；落到申请上只当建议、
截止写进下一步、退信轮到本人；白天每两小时自动查一次。不连 Gmail（check_replies 换成假的）。"""

from datetime import datetime

import pytest

from jobapply import apps, checks, config, gmail_client, jobqueue, notify, pipeline, records

NOW = datetime(2026, 10, 10, 9, 0)


@pytest.mark.parametrize("subject,snippet,kind,due", [
    ("【某银行】在线测评通知", "您已进入测评环节，请于10月12日前完成在线测评", "测评邀请", "2026-10-12"),
    ("测评邀请", "请在48小时内完成测评，逾期链接失效", "测评邀请", "2026-10-12 09:00"),
    ("AI面试邀请｜某公司", "请于2026年10月15日 23:59前完成 AI 视频面试", "AI面邀请", "2026-10-15 23:59"),
    ("笔试通知", "诚邀您参加10月18日 14:00 的在线笔试，请于10月17日前确认", "笔试邀请", "2026-10-17"),
    ("面试邀请：投资分析师", "邀请您参加一面，时间：10月20日 15:00", "面试邀请", ""),
    ("感谢您的关注", "很遗憾，您的简历未能进入下一轮", "拒信", ""),
    ("录用通知", "恭喜您！现向您发放录用通知", "offer", ""),
    ("投递成功", "感谢您的投递，我们已收到您的简历", "", ""),
    ("关于您的申请", "简历通过筛选者将收到面试通知，请耐心等待", "", ""),
])
def test_classify_progress(subject, snippet, kind, due):
    c = gmail_client.classify_progress(subject, snippet, NOW)
    assert c["kind"] == kind
    assert c["due"] == due
    if kind:
        assert c["evidence"]


def test_due_needs_a_deadline_word():
    c = gmail_client.classify_progress("面试邀请", "面试时间：10月20日 15:00，地点：上海", NOW)
    assert c["kind"] == "面试邀请" and c["due"] == ""                      # 只是面试时间、不是截止：不当截止写


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(notify, "_mac", lambda t, x: sent.append(t))
    monkeypatch.setattr(notify, "_quiet", lambda now: False)                  # 测试不受「夜里静默」影响
    notify._recent.clear()
    return sent


def fake_replies(monkeypatch, by_id):
    monkeypatch.setattr(gmail_client, "check_replies", lambda recs, progress=None: {k: dict(v) for k, v in by_id.items()})


def test_reply_becomes_suggestion_next_step_and_timeline(store, monkeypatch):
    rid = records.add(records.new_record(company_name="测评资本", job_title="投资专员", to_email="hr@x.com"))
    aid = records.get(rid)["app_id"]
    fake_replies(monkeypatch, {rid: {"reply_status": "有回复", "reply_at": "2026-10-10 08:00", "reply_from": "hr@x.com",
                                     "reply_subject": "在线测评通知", "reply_snippet": "请于10月12日前完成在线测评",
                                     "reply_checked_at": "2026-10-10 09:00"}})
    pipeline.refresh_replies(progress=lambda m: None)
    a = apps.get(aid)
    assert records.get(rid)["status"] == "已投递" and records.get(rid)["reply_kind"] == "测评邀请"   # 只建议，不直接改
    sug = [s for s in a["suggestions"] if s["state"] == "待定"]
    assert len(sug) == 1 and sug[0]["payload"]["to"] == "笔试" and "笔试/测评" in sug[0]["text"]
    assert "」的阶段改成「笔试/测评」？" in sug[0]["text"] and "把「" not in sug[0]["text"]   # 说的是改阶段，不是改岗位名
    assert a["next_step"]["due"] == "2026-10-12" and a["next_step"]["inferred"] is True and a["next_step"]["source"] == "邮件"
    assert any(e["kind"] == "来信" and "测评邀请" in e["text"] for e in a["timeline"])
    assert apps.view(a)["turn"] == "轮到你"
    pipeline.refresh_replies(progress=lambda m: None)                          # 同一封信再查一遍：不重复
    assert len([s for s in apps.get(aid)["suggestions"] if s["state"] == "待定"]) == 1
    apps.accept(aid, sug[0]["id"])                                            # 本人点了「采纳」才改阶段
    assert records.get(rid)["status"] == "笔试"


def test_explicit_next_step_not_overwritten(store, monkeypatch):
    rid = records.add(records.new_record(company_name="口述资本", job_title="分析师", to_email="hr@y.com"))
    aid = records.get(rid)["app_id"]
    apps.set_next_step(aid, "笔试（本人说周日截止）", due="2026-10-11", source="本人口述")
    fake_replies(monkeypatch, {rid: {"reply_status": "有回复", "reply_at": "2026-10-10 08:00", "reply_subject": "笔试通知",
                                     "reply_snippet": "请于10月13日前完成笔试"}})
    pipeline.refresh_replies(progress=lambda m: None)
    assert apps.get(aid)["next_step"]["due"] == "2026-10-11"                 # 本人说的优先


def test_rejection_suggests_and_bounce_is_your_turn(store, monkeypatch):
    r1 = records.add(records.new_record(company_name="拒信资本", job_title="分析师", to_email="hr@z.com"))
    r2 = records.add(records.new_record(company_name="退信资本", job_title="实习生", to_email="hr@w.com"))
    fake_replies(monkeypatch, {
        r1: {"reply_status": "有回复", "reply_at": "2026-10-10 08:00", "reply_subject": "感谢关注", "reply_snippet": "很遗憾，您未能进入下一轮"},
        r2: {"reply_status": "退信", "reply_at": "2026-10-10 08:01", "reply_subject": "Delivery Status Notification", "reply_snippet": "地址不存在"}})
    pipeline.refresh_replies(progress=lambda m: None)
    s1 = [s for s in apps.get(records.get(r1)["app_id"])["suggestions"] if s["state"] == "待定"]
    assert s1 and s1[0]["payload"]["to"] == "拒绝" and records.get(r1)["status"] == "已投递"
    a2 = apps.get(records.get(r2)["app_id"])
    assert a2["reply"]["status"] == "退信" and apps.view(a2)["turn"] == "轮到你"
    assert any(t.startswith("退信") or "退信" in t for t in store)


def test_refresh_lock_skips_concurrent_runs(store, monkeypatch):
    assert pipeline._refresh_lock.acquire(blocking=False)
    try:
        assert pipeline.refresh_replies(progress=lambda m: None)["busy"] is True
    finally:
        pipeline._refresh_lock.release()


def test_auto_refresh_every_two_hours_in_daytime(monkeypatch):
    calls = []
    monkeypatch.setattr(jobqueue, "auto_refresh", lambda record_ids=None: calls.append(record_ids))
    monkeypatch.setattr(jobqueue.threading, "Thread", lambda target, daemon, name: type("T", (), {"start": lambda self: target()})())
    monkeypatch.setitem(jobqueue._auto, "at", 0.0)
    monkeypatch.setattr(checks, "beijing_now", lambda: datetime(2026, 10, 10, 2, 0))
    assert not jobqueue.maybe_auto_refresh(10_000)                           # 夜里不查
    monkeypatch.setattr(checks, "beijing_now", lambda: datetime(2026, 10, 10, 10, 0))
    assert jobqueue.maybe_auto_refresh(10_000) and calls == [None]
    assert not jobqueue.maybe_auto_refresh(10_000 + 3600)                    # 两小时内不重复
    assert jobqueue.maybe_auto_refresh(10_000 + 7300) and len(calls) == 2


def test_same_letter_is_handled_once_even_when_not_recorded(store, monkeypatch):
    """本人手动改过回复状态（reply_locked）：新来的信不覆盖记录，但也只处理一次——不会每两小时又记一遍时间线、弹一遍通知。"""
    rid = records.add(records.new_record(company_name="锁定资本", job_title="分析师", to_email="hr@l.com"))
    records.update(rid, {"reply_status": "有回复", "reply_locked": True})
    aid = records.get(rid)["app_id"]
    fake_replies(monkeypatch, {rid: {"reply_status": "来信", "reply_at": "2026-10-10 08:00", "reply_subject": "感谢关注",
                                     "reply_snippet": "很遗憾，您未能进入下一轮"}})
    for _ in range(3):
        pipeline.refresh_replies(progress=lambda m: None)
    assert sum(e["kind"] == "来信" for e in apps.get(aid)["timeline"]) == 1
    assert len(store) == 1 and records.get(rid)["reply_status"] == "有回复"          # 通知一次；本人改过的不覆盖
    s = [x for x in apps.get(aid)["suggestions"] if x["state"] == "待定"]
    apps.dismiss(aid, s[0]["id"])
    pipeline.refresh_replies(progress=lambda m: None)
    assert not [x for x in apps.get(aid)["suggestions"] if x["state"] == "待定"]    # 点过「不用」的不再冒出来


def test_one_letter_for_five_positions_is_recorded_once(store, monkeypatch):
    aid = apps.create("网申", "五岗银行")["id"]
    rids = [records.add(records.new_record(company_name="五岗银行", job_title=f"岗位{i}", app_id=aid, send_mode="未发邮件",
                                           apply_channel="网申/链接")) for i in range(5)]
    letter = {"reply_status": "有回复", "reply_at": "2026-10-10 08:00", "reply_subject": "笔试通知",
              "reply_snippet": "请于10月13日前完成在线笔试"}
    fake_replies(monkeypatch, {rid: letter for rid in rids})
    pipeline.refresh_replies(progress=lambda m: None)
    a = apps.get(aid)
    assert sum(e["kind"] == "来信" for e in a["timeline"]) == 1 and sum(e["kind"] == "下一步" for e in a["timeline"]) == 1
    assert len(store) == 1 and a["next_step"]["due"] == "2026-10-13"
