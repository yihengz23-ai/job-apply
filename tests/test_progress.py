"""WP18 进展口述 + 网申待办同步到申请（过渡期适配层）。AI 那一步用假的返回值，不真调。"""

from datetime import datetime

import pytest

from jobapply import apps, config, llm, progress, records, wstasks

NOW = datetime(2026, 10, 9, 23, 10)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def fake_ai(monkeypatch, events):
    seen = {}

    def _call(**kw):
        seen.update(kw)
        return {"events": events}, {}
    monkeypatch.setattr(llm, "_call", _call)
    return seen


def ev(app_id="", company="", event="其他", quote="", position="", due="", round_="", at=""):
    return {"app_id": app_id, "company": company, "position": position, "event": event, "round": round_,
            "happened_at": at, "due": due, "quote": quote}


def web_app(company, jobs, mode=""):
    a = apps.create("网申", company)
    ids = []
    for i, (job, status) in enumerate(jobs, 1):
        ids.append(records.add(records.new_record(company_name=company, job_title=job, status=status, app_id=a["id"],
                                                  choice_no=i, send_mode="未发邮件", apply_channel="网申/链接")))
    if mode:
        apps.set_volunteer_mode(a["id"], mode)
    return a, ids


# ── 口述 ─────────────────────────────────────────────────────

def test_progress_moves_stages_and_records_quote(store, monkeypatch):
    catl, (c1,) = web_app("甲能源", [("投资专员", "笔试")])
    mt, (m1,) = web_app("乙科技", [("战略与投资分析师", "已投递")])
    seen = fake_ai(monkeypatch, [ev(catl["id"], "甲能源", "AI面完成", "今天晚上 11 点完成了甲能源的 AI 面试", at="2026-10-09 23:00"),
                                 ev(mt["id"], "乙科技", "笔试邀请", "收到乙科技的笔试，周日截止", due="2026-10-11")])
    out = progress.apply("今天晚上 11 点完成了甲能源的 AI 面试；收到乙科技的笔试，周日截止", now=NOW)
    assert "甲能源" in seen["content"] and "星期五" in seen["content"]          # 给了 AI 申请清单和今天是星期几
    assert len(out["applied"]) == 2 and not out["ask"]
    assert records.get(c1)["status"] == "面试中" and records.get(m1)["status"] == "笔试"
    assert any("AI面完成" in e["text"] and "甲能源的 AI 面试" in e["text"] for e in apps.get(catl["id"])["timeline"])
    nxt = apps.get(mt["id"])["next_step"]
    assert nxt["due"] == "2026-10-11" and nxt["source"] == "本人口述" and "笔试" in nxt["text"]
    assert progress.recent()[0]["id"] == out["id"]


def test_done_does_not_move_backwards_and_closes_next_step(store, monkeypatch):
    a, (r1,) = web_app("丙集团", [("投资专员", "面试中")])
    apps.set_next_step(a["id"], "测评（2026-10-10 截止）", due="2026-10-10", source="本人口述")
    fake_ai(monkeypatch, [ev(a["id"], "丙集团", "测评完成", "昨天晚上完成了丙集团的测评")])
    progress.apply("昨天晚上完成了丙集团的测评", now=NOW)
    assert records.get(r1)["status"] == "面试中"                               # 不往回改
    assert apps.get(a["id"])["next_step"]["done"] is True


def test_ambiguous_company_asks_instead_of_guessing(store, monkeypatch):
    web_app("华某证券", [("研究员", "已投递")])
    web_app("华某资产", [("投资经理", "已投递")])
    fake_ai(monkeypatch, [ev("", "华某", "面试完成", "华某一面面完了", round_="一面")])
    out = progress.apply("华某一面面完了", now=NOW)
    assert not out["applied"] and len(out["ask"][0]["candidates"]) == 2
    assert all(r["status"] == "已投递" for r in records.load())                # 没瞎挂
    pick = out["ask"][0]["candidates"][0]["app_id"]                           # 本人点了一个候选
    out2 = progress.apply("华某一面面完了", now=NOW, events=[{**out["ask"][0], "app_id": pick}])
    assert out2["applied"] and records.get(apps.positions(pick)[0]["id"])["status"] == "面试中"


def test_serial_volunteer_and_named_volunteer(store, monkeypatch):
    a, (v1, v2) = web_app("丁社区", [("科技投资", "笔试"), ("财务管培生", "笔试")], mode="串行")
    fake_ai(monkeypatch, [ev(a["id"], "丁社区", "拒绝", "丁社区一志愿挂了", position="一志愿")])
    progress.apply("丁社区一志愿挂了", now=NOW)
    assert records.get(v1)["status"] == "拒绝" and records.get(v2)["status"] == "笔试"
    fake_ai(monkeypatch, [ev(a["id"], "丁社区", "面试邀请", "丁社区约我面试了", round_="一面")])
    progress.apply("丁社区约我面试了", now=NOW)                                 # 串行：落在当前志愿（志愿二）
    assert records.get(v2)["status"] == "面试中"


def test_undo_restores(store, monkeypatch):
    a, (r1,) = web_app("戊电器", [("投资管理", "已投递")])
    fake_ai(monkeypatch, [ev(a["id"], "戊电器", "测评邀请", "戊电器的测评还没做", due="2026-10-12")])
    out = progress.apply("戊电器的测评还没做，12 号截止", now=NOW)
    assert records.get(r1)["status"] == "笔试"
    assert progress.undo(out["id"]) >= 2
    assert records.get(r1)["status"] == "已投递" and not apps.get(a["id"])["next_step"].get("due")
    assert progress.undo(out["id"]) == 0 and progress.recent()[0].get("undone")


def test_unknown_app_id_falls_back_to_company(store, monkeypatch):
    a, (r1,) = web_app("己汽车", [("融资经理", "已投递")])
    fake_ai(monkeypatch, [ev("不存在的id", "己汽车", "拒绝", "己汽车拒了")])
    out = progress.apply("己汽车拒了", now=NOW)
    assert out["applied"][0]["app_id"] == a["id"] and records.get(r1)["status"] == "拒绝"


def test_empty_text_rejected(store):
    with pytest.raises(ValueError):
        progress.apply("  ")


# ── 网申待办 → 申请（适配层）──────────────────────────────────────

def test_task_changes_mirror_into_app(store):
    t = wstasks.add("https://jobs.example.com/m", "镜像资本", "分析师")
    wstasks.update(t["id"], status="助手在填")
    assert apps.get(t["id"])["web_phase"] == "在填"
    wstasks.set_status(t["id"], "等你处理")
    wstasks.update(t["id"], todo="在画了蓝框的网页里登录")
    a = apps.get(t["id"])
    assert a["web_phase"] == "等你" and a["need"]["kind"] == "登录" and "蓝框" in a["need"]["text"]
    assert apps.view(a, {"positions": []})["turn"] == "轮到你"
    wstasks.set_status(t["id"], "已填待提交")
    assert apps.get(t["id"])["web_phase"] == "待你提交" and apps.get(t["id"])["need"] is None
    wstasks.update(t["id"], account="（页面上没显示）")
    assert apps.get(t["id"])["account"]["login"] == ""
    t = wstasks.set_status(t["id"], "已提交")
    a = apps.get(t["id"])
    assert a["web_phase"] == "已提交" and a["first_submitted_at"]
    wstasks.update(t["id"], readback_state="读回中")
    assert apps.get(t["id"])["readback"]["state"] == "读回中"
    wstasks.save_readback(t["id"], "status", "我的投递：分析师 已投递。投递后志愿将按顺序依次流转。", status="已投递",
                          positions="分析师（上海）")
    a = apps.get(t["id"])
    assert a["readback"]["state"] == "不完整" and "实际提交的简历" in a["readback"]["missing"]
    assert a["site_progress"]["text"] == "已投递" and a["volunteer_mode"] == "串行" and "依次流转" in a["volunteer_note"]
    assert a["snapshots"] and apps.snapshot_text(a["snapshots"][0]).startswith("我的投递")
    wstasks.save_readback(t["id"], "resume", "实际提交的简历原文")
    wstasks.save_readback(t["id"], "jd", "分析师 JD 原文", position="分析师", location="上海")
    assert apps.get(t["id"])["readback"]["state"] == "完成"
    v = apps.view(apps.get(t["id"]))
    assert v["turn"] == "等对方"


def test_give_up_reason_from_status(store):
    t = wstasks.add("https://jobs.example.com/g", "放弃资本", "分析师")
    wstasks.set_status(t["id"], "不投了", note="地点不合适")
    a = apps.get(t["id"])
    assert a["web_phase"] == "已放弃" and any("地点不合适" in e["text"] for e in a["timeline"])


def test_halted_shows_as_stopped(store):
    t = wstasks.add("https://jobs.example.com/h", "停资本", "分析师")
    wstasks.update(t["id"], status="助手在填")
    wstasks.update(t["id"], halted="面板重启把它打断了")
    v = apps.view(apps.get(t["id"]), {"positions": [], "agent": {}})
    assert v["turn"] == "停了" and v["why"] == "面板重启把它打断了"


def test_marker_ranks_become_choice_no(store):
    t = wstasks.add("https://jobs.example.com/r", "某银行", "")
    wstasks.set_status(t["id"], "已填待提交")
    wstasks.apply_markers("【网申记录】公司：某银行｜岗位：管培生（第一志愿）、研究岗（第二志愿）｜状态：已提交", task_id=t["id"])
    by = {r["job_title"]: r for r in apps.positions(t["id"])}
    assert by["管培生"]["choice_no"] == 1 and by["研究岗"]["choice_no"] == 2


def test_invite_without_due_is_your_turn_until_done(store, monkeypatch):
    a, (r1,) = web_app("乙科技", [("战略与投资分析师", "已投递")])
    apps.set_web_phase(a["id"], "已提交", by="本人")                               # 网申早就交了：只剩测评这件事
    fake_ai(monkeypatch, [ev(a["id"], "乙科技", "测评邀请", "乙科技的测评还没做")])
    out = progress.apply("乙科技的测评还没做", now=NOW)
    nxt = apps.get(a["id"])["next_step"]
    assert nxt["text"] == "做测评（截止没说）" and nxt["source"] == "本人口述" and not nxt["due"] and not nxt["done"]
    v = apps.view(apps.get(a["id"]))
    assert v["turn"] == "轮到你" and v["todo"] == "做测评（截止没说）" and v["button"]["action"] == "step_done"
    fake_ai(monkeypatch, [ev(a["id"], "乙科技", "测评完成", "乙科技测评做完了")])
    out2 = progress.apply("乙科技测评做完了", now=NOW)
    assert apps.get(a["id"])["next_step"]["done"] is True and apps.view(apps.get(a["id"]))["turn"] == "等对方"
    assert any(e["text"] == "做完了：做测评（截止没说）" for e in apps.get(a["id"])["timeline"])
    progress.undo(out2["id"])                                                     # 撤销「做完了」：又轮到你
    assert apps.view(apps.get(a["id"]))["turn"] == "轮到你"
    progress.undo(out["id"])
    assert not apps.get(a["id"])["next_step"].get("text") and records.get(r1)["status"] == "已投递"


def test_interview_invite_text_and_due_not_repeated(store, monkeypatch):
    a, _ = web_app("某证券", [("研究员", "已投递")])
    apps.set_web_phase(a["id"], "已提交", by="本人")
    fake_ai(monkeypatch, [ev(a["id"], "某证券", "面试邀请", "某证券约我周一下午三点一面", round_="一面", due="2026-10-12 15:00")])
    progress.apply("某证券约我周一下午三点一面", now=NOW)
    assert apps.get(a["id"])["next_step"]["text"] == "参加面试（一面）（2026-10-12 15:00）"
    v = apps.view(apps.get(a["id"]), {"now": NOW})
    assert v["turn"] == "轮到你" and v["todo"] == "参加面试（一面）（2026-10-12 15:00）"     # 截止不重复写两遍


def test_undo_give_up_puts_back_what_was_there(store, monkeypatch):
    a, (r1,) = web_app("撤放资本", [("分析师", "已投递")])
    apps.set_web_phase(a["id"], "已提交", by="本人")
    fake_ai(monkeypatch, [ev(a["id"], "撤放资本", "放弃", "撤放资本不投了")])
    out = progress.apply("撤放资本不投了", now=NOW)
    assert apps.view(apps.get(a["id"]))["turn"] == "已结束"
    progress.undo(out["id"])
    assert apps.get(a["id"])["web_phase"] == "已提交"                                 # 不是「待开始」（不会叫助手去填一家已经交了的）


def test_undo_does_not_clobber_a_newer_next_step(store, monkeypatch):
    a, _ = web_app("新步资本", [("分析师", "已投递")])
    apps.set_web_phase(a["id"], "已提交", by="本人")
    fake_ai(monkeypatch, [ev(a["id"], "新步资本", "笔试邀请", "收到新步资本的笔试")])
    first = progress.apply("收到新步资本的笔试", now=NOW)
    fake_ai(monkeypatch, [ev(a["id"], "新步资本", "笔试邀请", "新步资本笔试周日截止", due="2026-10-11")])
    progress.apply("新步资本笔试周日截止", now=NOW)
    progress.undo(first["id"])                                                        # 撤销第一句：下一步已经被第二句改过
    assert apps.get(a["id"])["next_step"]["due"] == "2026-10-11"


def test_first_choice_uses_readback_order_and_never_all_positions(store, monkeypatch):
    text = "我的投递\n第1志愿　科技投资｜某社区\n第2志愿　财务管培生｜某社区"
    a, (v1, v2) = web_app("某社区", [("科技投资", "笔试"), ("财务管培生", "已投递")])
    for rid in (v1, v2):
        records.update(rid, {"ws_submitted": text, "choice_no": 0})
    fake_ai(monkeypatch, [ev(a["id"], "某社区", "拒绝", "某社区一志愿挂了", position="一志愿")])
    progress.apply("某社区一志愿挂了", now=NOW)
    assert records.get(v1)["status"] == "拒绝" and records.get(v2)["status"] == "已投递"   # 从读回原文认出志愿一
    b, (w1, w2) = web_app("另一社区", [("投资岗", "笔试"), ("战略岗", "已投递")])
    for rid in (w1, w2):
        records.update(rid, {"choice_no": 0})                                       # 迁移来的：没记志愿号，原文里也没有
    fake_ai(monkeypatch, [ev(b["id"], "另一社区", "拒绝", "另一社区二志愿挂了", position="二志愿")])
    out = progress.apply("另一社区二志愿挂了", now=NOW)
    assert records.get(w1)["status"] == "笔试" and records.get(w2)["status"] == "已投递"   # 认不出：一个都不改
    assert "阶段没改" in out["applied"][0]["note"]


def test_corrections_by_chat_fix_a_wrong_detection_and_can_be_undone(store, monkeypatch):
    """来信认错了：本人一句「那封不是拒信，是笔试，13 号截止」→ 建议不用 + 记成笔试邀请（带截止）；撤销能整句退回。"""
    a, (r1,) = web_app("更正科技", [("分析师", "已投递")])
    apps.set_web_phase(a["id"], "已提交", by="本人")
    s = apps.suggest(a["id"], "阶段", "来信像是拒信：把「分析师」改成「未通过」？", {"record_id": r1, "to": "拒绝"})
    seen = fake_ai(monkeypatch, [ev(a["id"], "更正科技", "不用建议", "那封不是拒信"),
                                 {**ev(a["id"], "更正科技", "笔试邀请", "是笔试，13 号截止", due="2026-10-13"), "value": ""}])
    out = progress.apply("更正科技那封不是拒信，是笔试，13 号截止", now=NOW)
    assert "待确认：来信像是拒信" in seen["content"]                                   # AI 看得到哪条建议待确认
    app = apps.get(a["id"])
    assert [x["state"] for x in app["suggestions"]] == ["不用"] and records.get(r1)["status"] == "笔试"
    assert app["next_step"]["due"] == "2026-10-13"
    progress.undo(out["id"])
    app = apps.get(a["id"])
    assert [x["state"] for x in app["suggestions"]] == ["待定"] and records.get(r1)["status"] == "已投递"


def test_correction_events_stage_back_next_step_mode(store, monkeypatch):
    a, (v1, v2) = web_app("回改资本", [("投资岗", "面试中"), ("研究岗", "拒绝")])
    fake_ai(monkeypatch, [{**ev(a["id"], "回改资本", "改阶段", "研究岗改回已投递", position="研究岗"), "value": "已投递"},
                          {**ev(a["id"], "回改资本", "改下一步", "下一步改成二面，15 号", due="2026-10-15"), "value": "二面"},
                          {**ev(a["id"], "回改资本", "志愿方式", "他们是平行的"), "value": "平行"}])
    out = progress.apply("回改资本研究岗改回已投递，下一步改成二面 15 号，他们是平行的", now=NOW)
    assert records.get(v2)["status"] == "已投递" and records.get(v1)["status"] == "面试中"     # 往回改只动说到的那个岗位
    app = apps.get(a["id"])
    assert app["next_step"]["text"] == "二面" and app["next_step"]["due"] == "2026-10-15" and app["volunteer_mode"] == "平行"
    progress.undo(out["id"])
    app = apps.get(a["id"])
    assert records.get(v2)["status"] == "拒绝" and app["volunteer_mode"] == "" and not app["next_step"].get("text")
