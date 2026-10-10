"""第 2 批的网页接口：按申请成组的看板、单家详情、今天、进展口述（点候选、撤销）、建议、志愿方式、做完了、来信看过了。
不调 AI（口述都带 events，或者把 AI 换成假的）、不发信。"""

import pytest

import app as panel
from jobapply import apps, config, llm, records

LOCAL = {"Host": "localhost:5001"}
POST = {**LOCAL, "X-Requested-With": "jobapply"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"), ("DATA_DIR", tmp_path),
                 ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


def email_app(company, job="分析师"):
    rid = records.add(records.new_record(company_name=company, job_title=job, to_email="hr@x.com"))
    return records.get(rid)["app_id"], rid


def test_overview_today_detail(client):
    aid, rid = email_app("某资本")
    apps.set_next_step(aid, "做测评（截止没说）", source="本人口述")
    cards = client.get("/api/apps/overview", headers=LOCAL).get_json()["apps"]
    assert [c["company"] for c in cards] == ["某资本"] and cards[0]["turn"] == "轮到你"
    t = client.get("/api/apps/today", headers=LOCAL).get_json()
    assert [c["id"] for c in t["your_turn"]] == [aid] and t["mail_review"] == 0
    d = client.get(f"/api/apps/{aid}", headers=LOCAL).get_json()
    assert d["card"]["id"] == aid and d["positions"][0]["id"] == rid
    assert client.get("/api/apps/nope", headers=LOCAL).status_code == 404
    assert client.post(f"/api/apps/{aid}/step-done", headers=LOCAL).status_code == 403      # 没带防跨站的头
    assert client.post(f"/api/apps/{aid}/step-done", headers=POST).get_json()["ok"]
    assert client.get("/api/apps/today", headers=LOCAL).get_json()["your_turn"] == []


def test_suggestion_and_volunteer_mode(client):
    aid, rid = email_app("某银行")
    s = apps.suggest(aid, "阶段", "来信像是测评邀请", {"record_id": rid, "to": "笔试"})
    r = client.post(f"/api/apps/{aid}/suggestions/{s['id']}", json={"action": "accept"}, headers=POST)
    assert r.status_code == 200 and records.get(rid)["status"] == "笔试"
    again = client.post(f"/api/apps/{aid}/suggestions/{s['id']}", json={"action": "accept"}, headers=POST).get_json()
    assert again["result"]["already"] is True                                    # 连点两下：不重复改
    assert sum(e["kind"] == "建议" for e in apps.get(aid)["timeline"]) == 1
    assert client.post(f"/api/apps/{aid}/suggestions/{s['id']}", json={"action": "x"}, headers=POST).status_code == 400
    assert client.post(f"/api/apps/{aid}/suggestions/nope", json={"action": "accept"}, headers=POST).status_code == 404
    assert client.post(f"/api/apps/{aid}/volunteer-mode", json={"mode": "串行"}, headers=POST).status_code == 200
    assert apps.get(aid)["volunteer_mode"] == "串行"
    assert client.post(f"/api/apps/{aid}/volunteer-mode", json={"mode": "乱写"}, headers=POST).status_code == 400


def test_progress_pick_candidate_and_undo(client, monkeypatch):
    a1, r1 = email_app("华某证券")
    a2, _ = email_app("华某资产")
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": [{"app_id": "", "company": "华某", "position": "", "event": "面试完成",
                                                                "round": "一面", "happened_at": "", "due": "", "quote": "华某一面面完了"}]}, {}))
    out = client.post("/api/progress", json={"text": "华某一面面完了"}, headers=POST).get_json()
    assert not out["applied"] and {c["app_id"] for c in out["ask"][0]["candidates"]} == {a1, a2}
    ev = {**out["ask"][0], "app_id": a1, "event": "面试完成"}
    out2 = client.post("/api/progress", json={"text": "华某一面面完了", "events": [ev, {"event": "乱写"}]}, headers=POST).get_json()
    assert [x["app_id"] for x in out2["applied"]] == [a1] and records.get(r1)["status"] == "面试中"   # 乱写的事件丢掉
    items = client.get("/api/progress/recent", headers=LOCAL).get_json()["items"]
    assert items[0]["id"] == out2["id"]
    assert client.post(f"/api/progress/{out2['id']}/undo", headers=POST).get_json()["undone"] >= 1
    assert records.get(r1)["status"] == "已投递"
    assert client.post("/api/progress/nope/undo", headers=POST).status_code == 404
    assert client.post("/api/progress", json={"text": "  "}, headers=POST).status_code == 400
    assert client.post("/api/progress", json={"text": "x", "events": "不是列表"}, headers=POST).status_code == 400


def test_progress_ai_failure_is_friendly(client, monkeypatch):
    email_app("某资本")

    def boom(**kw):
        raise llm.LLMError("会员通道用不了")
    monkeypatch.setattr(llm, "_call", boom)
    r = client.post("/api/progress", json={"text": "某资本拒了"}, headers=POST)
    assert r.status_code == 502 and "没拆出来" in r.get_json()["error"]


def test_bounce_handled(client):
    aid, _ = email_app("退信资本")
    apps.set_reply(aid, status="退信", at="2026-10-10 08:00", snippet="地址不存在")
    assert client.get("/api/apps/overview", headers=LOCAL).get_json()["apps"][0]["turn"] == "轮到你"
    assert client.post(f"/api/apps/{aid}/reply-handled", headers=POST).get_json()["ok"]
    assert client.get("/api/apps/overview", headers=LOCAL).get_json()["apps"][0]["turn"] == "等对方"


def test_step_done_refuses_a_step_that_changed_meanwhile(client):
    aid, _ = email_app("变更资本")
    apps.set_next_step(aid, "笔试（按来信推算：10-11 前）", due="2026-10-11", source="邮件", inferred=True)
    seen = apps.get(aid)["next_step"]["text"]
    apps.set_next_step(aid, "AI面（按来信推算：10-12 前）", due="2026-10-12", source="邮件", inferred=True)   # 后台来信换了下一步
    r = client.post(f"/api/apps/{aid}/step-done", json={"text": seen}, headers=POST)
    assert r.status_code == 409 and apps.get(aid)["next_step"]["done"] is False
    assert client.post(f"/api/apps/{aid}/step-done", json={"text": apps.get(aid)["next_step"]["text"]}, headers=POST).status_code == 200


def test_candidates_show_jobs_and_stage_and_timeline_only_undo_counts(client, monkeypatch):
    a1, r1 = email_app("同名社区", "投资分析")
    a2, r2 = email_app("同名社区", "战略分析")
    apps.advance(r1, "面试中", by="本人", manual=True)
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": [{"app_id": "", "company": "同名社区", "position": "", "event": "面试完成",
                                                                "round": "一面", "happened_at": "", "due": "", "quote": "同名社区一面面完了"}]}, {}))
    out = client.post("/api/progress", json={"text": "同名社区一面面完了"}, headers=POST).get_json()
    labels = sorted(c["label"] for c in out["ask"][0]["candidates"])
    assert labels == ["同名社区 · 战略分析（已投递）", "同名社区 · 投资分析（面试）"]
    done = client.post("/api/progress", json={"text": "同名社区一面面完了", "events": [{**out["ask"][0], "app_id": a1}]}, headers=POST).get_json()
    assert client.post(f"/api/progress/{done['id']}/undo", headers=POST).get_json()["undone"] >= 1   # 只记了时间线也算撤销了一处
