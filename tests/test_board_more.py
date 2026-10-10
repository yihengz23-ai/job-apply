"""看板筛选和口述改看板（第二批，2026-10-11）：
- 本人挑岗位时助手会看好几个岗位的 JD——看了没投的不进看板；交了以后、网站岗位单还没读到时读的 JD 才算投了的岗位。
- 还没交的时候，助手报的【网申记录】岗位单就是本人现在挑定的：先前报过、这次没报的去掉（草稿记录一起删）。
- 口述还能：加岗位、改岗位名、改投递时间、改公司名、把收起的岗位 / 删掉的申请放回来，都能撤销。"""

import app as panel
from jobapply import apps, llm, progress, records, wstasks

LOCAL = {"Host": "localhost:5001"}
NOW = progress._now()


def ev(app_id="", company="", event="其他", quote="", position="", value=""):
    return {"app_id": app_id, "company": company, "position": position, "event": event, "round": "",
            "happened_at": "", "due": "", "quote": quote, "value": value}


def fake_ai(monkeypatch, events):
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": events}, {}))


def marker(t, company, jobs, status="已填待提交"):
    return wstasks.apply_markers(f"【网申记录】公司：{company}｜岗位：{jobs}｜网址：{t['url']}｜状态：{status}", task_id=t["id"])


def submitted_task(company, jobs, url):
    t = wstasks.add(url, company, "")
    marker(t, company, jobs)
    wstasks.set_status(t["id"], "已提交")
    return wstasks.get(t["id"])


def titles(app_id):
    return sorted(r["job_title"] for r in apps.positions(app_id) if not r.get("deleted_at"))


# ── 筛选：看了 JD 不算投、岗位单以最新为准 ─────────────────────────────

def test_reading_jds_while_choosing_does_not_add_positions():
    t = wstasks.add("https://jobs.example.com/m1", "甲资本", "")
    marker(t, "甲资本", "投资经理")
    for name in ("投资经理", "行业研究员", "投后管理"):                       # 本人挑的时候助手把三个都看了一遍
        wstasks.save_readback(t["id"], "jd", f"{name}的 JD 原文", position=name, location="上海")
    assert titles(t["id"]) == ["投资经理"]
    rec = apps.positions(t["id"])[0]
    assert rec["jd_text"] == "投资经理的 JD 原文"                              # 投了的那个照样记上 JD


def test_jds_read_after_submitting_count_until_the_site_list_is_read():
    t = submitted_task("乙基金", "（岗位见网站）", "https://jobs.example.com/m2")
    wstasks.save_readback(t["id"], "jd", "A 的 JD", position="研究员（权益）", location="深圳")
    wstasks.save_readback(t["id"], "jd", "B 的 JD", position="研究员（固收）", location="深圳")
    assert titles(t["id"]) == ["研究员（固收）", "研究员（权益）"]
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "研究员（权益）", "location": "深圳"}])
    wstasks.save_readback(t["id"], "jd", "C 的 JD", position="研究员（量化）", location="深圳")   # 网站岗位单读到以后：只认单子上的
    assert titles(t["id"]) == ["研究员（固收）", "研究员（权益）"]


def test_latest_job_list_before_submitting_is_the_pick():
    t = wstasks.add("https://jobs.example.com/m3", "丙集团", "")
    marker(t, "丙集团", "管培生（金融方向）、管培生（科技方向）、研究员")
    assert titles(t["id"]) == ["研究员", "管培生（科技方向）", "管培生（金融方向）"]
    marker(t, "丙集团", "管培生（科技方向）")                                  # 本人挑定：只投这一个
    assert titles(t["id"]) == ["管培生（科技方向）"]
    assert wstasks.get(t["id"])["job"] == "管培生（科技方向）"
    wstasks.set_status(t["id"], "已提交")
    assert titles(t["id"]) == ["管培生（科技方向）"]
    marker(t, "丙集团", "研究员", status="已提交")                              # 交了以后助手再报：不删，只能加
    assert "管培生（科技方向）" in titles(t["id"])


def test_summary_job_text_is_not_a_pick():
    t = wstasks.add("https://jobs.example.com/m4", "丁银行", "")
    marker(t, "丁银行", "A岗、B岗、C岗")
    marker(t, "丁银行", "A岗等 3 个志愿")
    assert titles(t["id"]) == ["A岗", "B岗", "C岗"]


# ── 口述：加岗位、改岗位名、改投递时间、改公司名、放回来 ───────────────────────

def test_add_position_by_voice_and_undo(monkeypatch):
    t = submitted_task("戊证券", "研究助理", "https://jobs.example.com/m5")
    fake_ai(monkeypatch, [ev(t["id"], "戊证券", "加岗位", "戊证券我还投了投资助理", position="投资助理")])
    out = progress.apply("戊证券我还投了投资助理", now=NOW)
    assert titles(t["id"]) == ["投资助理", "研究助理"]
    added = next(r for r in apps.positions(t["id"]) if r["job_title"] == "投资助理")
    assert added["status"] == "已投递"
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "投资助理", "location": "北京"},
                                                                     {"name": "研究助理", "location": "北京"}])
    assert titles(t["id"]) == ["投资助理", "研究助理"]                       # 读回不会再多建一条
    progress.undo(out["id"])
    assert titles(t["id"]) == ["研究助理"]


def test_rename_position_by_voice_sticks_and_undo(monkeypatch):
    t = submitted_task("己资管", "分析师", "https://jobs.example.com/m6")
    fake_ai(monkeypatch, [ev(t["id"], "己资管", "改岗位名", "己资管那个岗位应该叫 FOF 研究员", position="分析师", value="FOF研究员")])
    out = progress.apply("己资管那个岗位应该叫 FOF 研究员", now=NOW)
    assert titles(t["id"]) == ["FOF研究员"]
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "FOF研究员（资管）", "location": "深圳"}])
    assert titles(t["id"]) == ["FOF研究员"]                                  # 本人定的名字，读回不改
    progress.undo(out["id"])
    assert titles(t["id"]) == ["分析师"]


def test_change_sent_time_and_undo(monkeypatch):
    t = submitted_task("庚银行", "管培生", "https://jobs.example.com/m7")
    rid = t["record_id"]
    old = records.get(rid)["sent_at"]
    fake_ai(monkeypatch, [ev(t["id"], "庚银行", "改投递时间", "庚银行是 10 号晚上 11 点 28 交的", value="2026-10-10 23:28")])
    out = progress.apply("庚银行是 10 号晚上 11 点 28 交的", now=NOW)
    assert records.get(rid)["sent_at"] == "2026-10-10 23:28" and apps.get(t["id"])["first_submitted_at"] == "2026-10-10 23:28"
    progress.undo(out["id"])
    assert records.get(rid)["sent_at"] == old


def test_rename_company_and_undo(monkeypatch):
    t = submitted_task("辛", "分析师", "https://jobs.example.com/m8")
    fake_ai(monkeypatch, [ev(t["id"], "辛", "改公司名", "辛的公司名应该是辛资本管理有限公司", value="辛资本管理有限公司")])
    out = progress.apply("辛的公司名应该是辛资本管理有限公司", now=NOW)
    assert apps.get(t["id"])["company"] == "辛资本管理有限公司" and wstasks.get(t["id"])["company"] == "辛资本管理有限公司"
    assert records.get(t["record_id"])["company_name"] == "辛资本管理有限公司"
    progress.undo(out["id"])
    assert apps.get(t["id"])["company"] == "辛"


def test_put_back_a_hidden_position_and_a_deleted_application(monkeypatch):
    t = submitted_task("壬投资", "投资经理、行业研究员", "https://jobs.example.com/m9")
    fake_ai(monkeypatch, [ev(t["id"], "壬投资", "删岗位", "壬投资行业研究员那条删掉", position="行业研究员")])
    progress.apply("壬投资行业研究员那条删掉", now=NOW)
    fake_ai(monkeypatch, [ev(t["id"], "壬投资", "恢复岗位", "壬投资行业研究员删错了，放回来", position="行业研究员")])
    progress.apply("壬投资行业研究员删错了，放回来", now=NOW)
    assert titles(t["id"]) == ["投资经理", "行业研究员"]
    fake_ai(monkeypatch, [ev(t["id"], "壬投资", "删申请", "壬投资全删了")])
    progress.apply("壬投资全删了", now=NOW)
    assert "壬投资" in progress._context(NOW) and "已删" in progress._context(NOW)   # 删掉的也给记录员看，才能说「恢复」
    fake_ai(monkeypatch, [ev(t["id"], "壬投资", "恢复申请", "壬投资删错了，恢复")])
    out = progress.apply("壬投资删错了，恢复", now=NOW)
    assert not apps.get(t["id"]).get("deleted_at") and titles(t["id"]) == ["投资经理", "行业研究员"]
    progress.undo(out["id"])
    assert apps.get(t["id"]).get("deleted_at")


def test_other_events_on_a_deleted_application_are_refused(monkeypatch):
    t = submitted_task("癸资本", "分析师", "https://jobs.example.com/m10")
    apps.soft_delete(t["id"], by="本人")
    fake_ai(monkeypatch, [ev(t["id"], "癸资本", "改阶段", "癸资本进面试了", value="面试")])
    out = progress.apply("癸资本进面试了", now=NOW)
    assert "删了" in out["applied"][0]["note"] and records.get(t["record_id"])["status"] == "已投递"


def test_accepting_a_count_suggestion_by_voice_can_be_undone(monkeypatch):
    t = submitted_task("子基金", "投资分析师、行业研究员", "https://jobs.example.com/m11")
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions="投资分析师")
    fake_ai(monkeypatch, [ev(t["id"], "子基金", "采纳建议", "子基金那条岗位数的建议对")])
    out = progress.apply("子基金那条岗位数的建议对", now=NOW)
    assert titles(t["id"]) == ["投资分析师"]
    progress.undo(out["id"])
    assert titles(t["id"]) == ["投资分析师", "行业研究员"]


# ── 审查查出来的（2026-10-11）──────────────────────────────────────

def test_email_record_is_never_unsubmitted_by_the_web_side(monkeypatch):
    """「邮箱+网申」：读回以后第一个岗位用的是 Gmail 发出去的那条，网申待办退回、口述「其实没投」都不动它。"""
    t = wstasks.add("https://jobs.example.com/r1", "丑资本", "投资经理")
    rid = records.add(records.new_record(company_name="丑资本", job_title="投资经理", to_email="hr@example.com",
                                         send_mode="发送", sent_at="2026-10-09 10:00", gmail_message_id="m1"))
    wstasks.update(t["id"], email_record_id=rid)
    wstasks.set_status(t["id"], "已提交")
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "投资经理", "location": "上海"}])
    wstasks.set_status(t["id"], "已填待提交")
    r = records.get(rid)
    assert r["status"] == "已投递" and r["sent_at"] == "2026-10-09 10:00"
    a = records.get(rid)["app_id"]
    fake_ai(monkeypatch, [ev(a, "丑资本", "其实没投", "丑资本其实没投")])
    out = progress.apply("丑资本其实没投", now=NOW)
    assert records.get(rid)["status"] == "已投递" and "Gmail" in out["applied"][0]["note"]


def test_unbound_chat_reports_with_a_placeholder_job_do_not_pile_up():
    url = "https://jobs.example.com/r2"
    for job in ("（待读回）", "（待读回）", "（岗位见网站）", "（还没选）"):
        wstasks.apply_markers(f"【网申记录】公司：寅资管｜岗位：{job}｜网址：{url}｜状态：已提交")
    mine = [t for t in wstasks.list_tasks() if t.get("company") == "寅资管"]
    assert len(mine) == 1 and mine[0]["status"] == "已填待提交" and not mine[0].get("job")
    assert all(r["status"] == "草稿" for r in records.load() if r.get("company_name") == "寅资管")


def test_directions_differ_even_when_both_say_head_office():
    a = {"name": "管理培训生（投行与金融市场方向）", "location": "总行，地点网站未显示"}
    b = {"name": "管理培训生（公司金融方向）", "location": "总行，地点网站未显示"}
    assert not wstasks.same_position(a, b)
    t = submitted_task("卯银行", "管理培训生（投行与金融市场方向）、管理培训生（公司金融方向）", "https://jobs.example.com/r3")
    ids = {r["job_title"]: r["id"] for r in apps.positions(t["id"])}
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "【2027校招】管理培训生（公司金融方向）", "location": "总行"}])
    names = {r["job_title"]: r["id"] for r in apps.positions(t["id"]) if not r.get("deleted_at")}
    assert names == {"管理培训生（投行与金融市场方向）": ids["管理培训生（投行与金融市场方向）"],          # 网站的写法只落在公司金融那条上
                     "【2027校招】管理培训生（公司金融方向）": ids["管理培训生（公司金融方向）"]}
    sug = next(s for s in apps.get(t["id"])["suggestions"] if s["kind"] == "岗位数")      # 网站这页只列了一个：提醒的是投行那条
    assert sug["payload"]["hide"] == [ids["管理培训生（投行与金融市场方向）"]]


def test_restoring_an_application_keeps_positions_hidden_on_their_own(monkeypatch):
    t = submitted_task("辰投资", "投资经理、行业研究员", "https://jobs.example.com/r4")
    hid = next(r for r in apps.positions(t["id"]) if r["job_title"] == "行业研究员")
    apps.hide_position(hid["id"], by="本人")
    fake_ai(monkeypatch, [ev(t["id"], "辰投资", "删申请", "辰投资全删了")])
    out = progress.apply("辰投资全删了", now=NOW)
    progress.undo(out["id"])
    assert titles(t["id"]) == ["投资经理"]


def test_one_position_said_not_submitted_stays_draft_after_readback(monkeypatch):
    t = submitted_task("巳证券", "研究员、分析师", "https://jobs.example.com/r5")
    fake_ai(monkeypatch, [ev(t["id"], "巳证券", "其实没投", "巳证券分析师其实没交", position="分析师")])
    progress.apply("巳证券分析师其实没交", now=NOW)
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "研究员", "location": "北京"}])
    st = {r["job_title"]: r["status"] for r in apps.positions(t["id"])}
    assert st == {"研究员": "已投递", "分析师": "草稿"}


def test_jd_with_a_slightly_different_name_reaches_the_record():
    t = submitted_task("午集团", "产业投资岗", "https://jobs.example.com/r6")
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "产业投资岗（午集团）", "location": "深圳"}])
    wstasks.save_readback(t["id"], "jd", "产业投资岗的 JD", position="产业投资岗（午集团有限公司）", location="深圳南山区")
    r = next(r for r in apps.positions(t["id"]) if not r.get("deleted_at"))
    assert r["jd_text"] == "产业投资岗的 JD"
    assert not [m for m in apps.get(t["id"])["readback"].get("missing") or [] if "JD" in m]


def test_giving_up_after_submitting_keeps_what_was_submitted():
    one = submitted_task("未资本", "分析师", "https://jobs.example.com/r7")
    two = submitted_task("申资本", "分析师、研究员", "https://jobs.example.com/r8")
    for t in (one, two):
        wstasks.set_status(t["id"], "不投了")
    assert {r["status"] for r in records.load() if r.get("company_name") in ("未资本", "申资本")} == {"已投递"}


def test_wrong_name_on_a_single_position_changes_nothing(monkeypatch):
    t = submitted_task("酉基金", "研究员", "https://jobs.example.com/r9")
    fake_ai(monkeypatch, [ev(t["id"], "酉基金", "删岗位", "酉基金投后那条删掉", position="投后管理")])
    out = progress.apply("酉基金投后那条删掉", now=NOW)
    assert titles(t["id"]) == ["研究员"] and "没认出" in out["applied"][0]["note"]


def test_unsubmitting_clears_first_submitted_and_resubmitting_sets_it_again():
    t = submitted_task("戌银行", "管培生", "https://jobs.example.com/r10")
    assert apps.get(t["id"])["first_submitted_at"]
    wstasks.set_status(t["id"], "已填待提交")
    assert not apps.get(t["id"])["first_submitted_at"]
    wstasks.set_status(t["id"], "已提交")
    assert apps.get(t["id"])["first_submitted_at"] and records.get(t["record_id"])["status"] == "已投递"


# ── 第二轮审查（2026-10-11）────────────────────────────────────────

def email_web_task(company, job, url):
    """「邮箱+网申」：Gmail 已经发出去一封（研究助理），网申那边也交了。"""
    t = wstasks.add(url, company, job)
    rid = records.add(records.new_record(company_name=company, job_title=job, to_email="hr@example.com", send_mode="发送",
                                         sent_at="2026-10-09 10:00", gmail_message_id="m-" + company))
    wstasks.update(t["id"], email_record_id=rid)
    wstasks.set_status(t["id"], "已提交")
    return wstasks.get(t["id"]), rid, records.get(rid)["app_id"]


def test_adding_a_position_never_takes_over_the_email_record(monkeypatch):
    t, rid, aid = email_web_task("亥资本", "研究助理", "https://jobs.example.com/s1")
    fake_ai(monkeypatch, [ev(aid, "亥资本", "加岗位", "亥资本我还投了投资助理", position="投资助理")])
    out = progress.apply("亥资本我还投了投资助理", now=NOW)
    assert records.get(rid)["job_title"] == "研究助理"
    assert titles(aid) == ["投资助理", "研究助理"]
    progress.undo(out["id"])
    assert titles(aid) == ["研究助理"] and not records.get(rid).get("deleted_at")


def test_a_new_pick_never_renames_or_hides_the_email_record():
    t = wstasks.add("https://jobs.example.com/s2", "甲乙资本", "")
    rid = records.add(records.new_record(company_name="甲乙资本", job_title="研究助理", to_email="hr@example.com", send_mode="发送",
                                         gmail_message_id="m-s2"))
    wstasks.update(t["id"], email_record_id=rid)
    marker(t, "甲乙资本", "研究助理、投资助理")
    marker(t, "甲乙资本", "投资助理、行业研究员")
    assert records.get(rid)["job_title"] == "研究助理" and not records.get(rid).get("deleted_at")


def test_positions_that_were_ever_submitted_are_never_dropped(monkeypatch):
    t = submitted_task("乙丙证券", "研究员、分析师、投资助理", "https://jobs.example.com/s3")
    fake_ai(monkeypatch, [ev(t["id"], "乙丙证券", "其实没投", "乙丙证券其实没交")])
    progress.apply("乙丙证券其实没交", now=NOW)
    marker(t, "乙丙证券", "研究员")                                          # 助手补交时只报了一个
    assert titles(t["id"]) == ["分析师", "投资助理", "研究员"]


def test_dropped_drafts_are_only_hidden_and_can_be_put_back(monkeypatch):
    t = wstasks.add("https://jobs.example.com/s4", "丙丁集团", "")
    marker(t, "丙丁集团", "管培生、研究员")
    marker(t, "丙丁集团", "研究员")
    hidden = [r for r in apps.positions(t["id"]) if r.get("deleted_at")]
    assert titles(t["id"]) == ["研究员"] and [r["job_title"] for r in hidden] == ["管培生"]
    fake_ai(monkeypatch, [ev(t["id"], "丙丁集团", "恢复岗位", "丙丁集团管培生也要投，放回来", position="管培生")])
    progress.apply("丙丁集团管培生也要投，放回来", now=NOW)
    assert titles(t["id"]) == ["研究员", "管培生"]


def test_the_submitted_line_does_not_drop_positions():
    t = wstasks.add("https://jobs.example.com/s5", "丁戊基金", "")
    marker(t, "丁戊基金", "研究员、分析师、投资助理")
    marker(t, "丁戊基金", "研究员", status="已提交")
    assert titles(t["id"]) == ["分析师", "投资助理", "研究员"]


def test_several_marker_lines_in_one_reply_count_together():
    t = wstasks.add("https://jobs.example.com/s6", "戊己银行", "")
    marker(t, "戊己银行", "A岗、B岗、C岗")
    reply = "\n".join([f"【网申记录】公司：戊己银行｜岗位：{j}｜网址：{t['url']}｜状态：已填待提交" for j in ("A岗", "B岗", "C岗")]
                      + [f"【网申记录】公司：戊己银行｜岗位：校招简历（3个岗位：A岗、B岗、C岗）｜网址：{t['url']}｜状态：已填待提交"])
    wstasks.apply_markers(reply, task_id=t["id"])
    assert titles(t["id"]) == ["A岗", "B岗", "C岗"]
    reply = (f"【网申记录】公司：戊己银行｜岗位：A岗、B岗｜网址：{t['url']}｜状态：已填待提交\n"
             f"【网申记录】公司：戊己银行｜岗位：C岗｜网址：{t['url']}｜状态：已填待提交")
    wstasks.apply_markers(reply, task_id=t["id"])                              # 两组分两行写
    assert titles(t["id"]) == ["A岗", "B岗", "C岗"]


def test_undoing_a_rename_lets_the_site_name_in_again(monkeypatch):
    t = submitted_task("己庚资管", "分析师", "https://jobs.example.com/s7")
    fake_ai(monkeypatch, [ev(t["id"], "己庚资管", "改岗位名", "己庚资管那个岗位叫研究员", position="分析师", value="研究员")])
    out = progress.apply("己庚资管那个岗位叫研究员", now=NOW)
    progress.undo(out["id"])
    wstasks.save_readback(t["id"], "status", "我的投递", positions=[{"name": "分析师（权益投资部）", "location": "上海"}])
    assert titles(t["id"]) == ["分析师（权益投资部）"]
