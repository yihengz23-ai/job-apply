"""第二轮审稿（40 封待审核）查出来的问题：渠道写进标题 / 文件名、「研二」、格式里的填写说明、角色降级、
「负责」升级、替对方许诺转正、文章发布日期、旧稿按新规则重查、英文多岗位、英文邮件照英文简历写。
不调 AI、不碰 Gmail、不发信。"""

from datetime import datetime

import pytest

import app as panel
from jobapply import checks, config, fetch, jobqueue, llm, pipeline, records, resume
from tests.test_review import JD, add_item, result

N = config.CANDIDATE_NAME
LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
NOW = datetime(2026, 10, 8, 12, 0)


@pytest.fixture(autouse=True)
def rules(monkeypatch):
    monkeypatch.setattr(config, "GRADE_LABEL", "2027届硕士")
    monkeypatch.setattr(config, "ROLE_PHRASES", [{"keys": ["投资概览", "研究框架"], "must": "协助MD"}])


def warns(r, jd=JD, **kw):
    return [i["msg"] for i in checks.run(r, jd, now=NOW, **kw) if i["level"] in ("warn", "error")]


# ── 渠道 / 平台不写进标题和文件名 ─────────────────────────────

@pytest.mark.parametrize("text,names,want", [
    ("投资实习生-张三-CaseMock平台", [], "投资实习生-张三"),               # 「XX平台」一项
    ("[CaseMock]投资实习生-张三", ["CaseMock"], "投资实习生-张三"),        # 方括号里的渠道名
    ("张三-简历-实习僧.pdf", ["实习僧"], "张三-简历.pdf"),                 # 文件名里的渠道
    ("募资经理（渠道）-张三", [], "募资经理（渠道）-张三"),               # 岗位名里的「渠道」不能动
    ("实习申请-张三-", [], "实习申请-张三-"),                              # 什么都没删：原样返回
])
def test_strip_channel(text, names, want):
    assert checks.strip_channel(text, names) == want


def test_autofix_strips_channel_and_grade():
    r = result(email_subject=f"投资实习生-{N}-研二-CaseMock平台", resume_filename=f"{N}-研二-简历-CaseMock平台.pdf")
    fixes = checks.autofix(r, JD, source=["CaseMock"])
    assert r["email_subject"] == f"投资实习生-{N}-2027届硕士"
    assert r["resume_filename"] == f"{N}-2027届硕士-简历.pdf" and len(fixes) == 2


def test_format_notes_and_source_field_not_compared():
    assert checks.fmt_clean("姓名-学校-岗位（注明信息来源）") == "姓名-学校-岗位"
    assert checks.fmt_clean("姓名-到岗时间（年月）-城市（如上海或者广州）") == "姓名-到岗时间-城市"
    assert checks.fmt_clean("应聘方向（消费基金/AI基金）-姓名+过往实习（投行 / 投资）") == "应聘方向-姓名+过往实习"   # 选项括号
    assert checks.fmt_clean("CaseMock - FoF Internship - Name - Location (BJ / HK)", ["Case Mock（公众号）"]) == "FoF Internship - Name - Location"
    r = result(email_subject=f"{N}-某大学-2026年11月-上海", jd_rules={"subject_format": "姓名-学校-到岗时间（年月）-城市（如上海）"})
    assert not [m for m in warns(r) if "JD 格式里有" in m]


def test_resume_filename_follows_jd_format():
    r = result(resume_filename=f"{N}-简历.pdf", jd_rules={"resume_filename_format": "姓名+学校+岗位"})
    assert any(m.startswith("简历文件名：JD 格式里有「+」") for m in warns(r))
    r = result(resume_filename=f"{N}+某大学+投资实习生.pdf", jd_rules={"resume_filename_format": "姓名+学校+岗位"})
    assert not any(m.startswith("简历文件名") for m in warns(r))


def test_paragraph_length_ignores_links():
    para = "我也用Claude Code搭了一套简历投递系统，包括JD解析、邮件生成、附件检查、投递记录入库和筛选看板。" * 2
    body = f"您好，\n\n我是{N}，两周内可到岗。\n\n{para}（Demo：https://example-user.github.io/job-apply/demo/）\n\n{N}\n"
    assert not any("经历段 120 字左右" in m for m in warns(result(email_body=body)))
    assert any("经历段 120 字左右" in m for m in warns(result(email_body=body.replace("（Demo", "，" + "再多写一点。" * 6 + "（Demo"))))


def test_fullwidth_slash_in_filename_counts():
    r = result(resume_filename=f"{N}-某大学-每周5天／两周内到岗.pdf", jd_rules={"resume_filename_format": "姓名-学校-每周天数/到岗日期"})
    assert not any(m.startswith("简历文件名") for m in warns(r))


def test_format_notes_in_fit_warnings_dropped():
    r = result(fit_warnings=["标题格式里的「[CaseMock]」看起来是转发渠道的标签，已按规则去掉", "文件名里的“/”已改成全角“／”",
                             "岗位只在北京，需要确认能否去北京"])
    msgs = [m for m in warns(r, source_label="Case Mock（公众号）") if m.startswith("JD 要求")]
    assert msgs == ["JD 要求：岗位只在北京，需要确认能否去北京"]


# ── 正文：照档案原话，不升级、不许诺 ───────────────────────────

@pytest.mark.parametrize("sentence,flag", [
    ("我完成了一份 AI 基础设施投资概览。", "协助MD"),
    ("我协助MD完成了一份 AI 基础设施投资概览。", None),
    ("我负责技术尽调和行业研究。", "不是「负责」"),
    ("毕业后可转全职。", "替对方许诺"),
])
def test_body_sticks_to_profile_wording(sentence, flag):
    body = f"您好，\n\n我是{N}，两周内可到岗。{sentence}\n\n简历见附件。\n\n{N}\n"
    msgs = " ".join(warns(result(email_body=body)))
    for f in ("协助MD", "不是「负责」", "替对方许诺"):
        assert (f in msgs) == (f == flag)


def test_automation_system_only_when_jd_values_it():
    body = f"您好，\n\n我是{N}，两周内可到岗。我用 Claude Code 搭了一套投递系统。\n\n简历见附件。\n\n{N}\n"
    flag = "不提自动化投递系统"
    assert any(flag in m for m in warns(result(email_body=body)))
    assert not any(flag in m for m in warns(result(email_body=body), jd=JD + "加分项：熟悉 AI Agent / vibe coding。"))


# ── 岗位发布日期：JD 里没写就看文章发布日期 ─────────────────────

@pytest.mark.parametrize("post,publish,stale", [
    ("", "2026-07-20", True),            # 文章发了 80 天
    ("", "2026-09-20", False),
    ("2026-09-30", "2026-07-20", False),  # JD 自己写了日期：以 JD 为准
])
def test_post_date_falls_back_to_publish_date(post, publish, stale):
    msgs = warns(result(job_post_date=post), publish_date=publish)
    assert any("可能已招满" in m for m in msgs) == stale


# ── 旧稿按新规则重查（AI 审稿 + 自动修正），写好存回原条目 ───────────

@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    submitted = []
    monkeypatch.setattr(jobqueue, "_submit_regen", submitted.append)
    jobqueue.submitted = submitted
    return jobqueue


def test_recheck_applies_rules_and_editor_notes(q, monkeypatch):
    iid = add_item(q, email_subject=f"投资实习生申请-{N}-研二")
    it = q.recheck(iid, notes="「长期留下来」改成「也希望争取留用机会」")
    assert it["status"] == "重写中" and it["regen_mode"] == "check" and q.submitted == [iid]
    seen = {}

    def review(jd, r, **kw):
        seen.update(kw)
        return {"changes": [{"problem": "留用措辞", "before": "a", "after": "b"}],
                "email_subject": r["email_subject"], "email_body": f"您好，\n\n重查后的正文，也希望争取留用机会。\n\n{N}\n"}, {}
    monkeypatch.setattr(llm, "self_review", review)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    q._regen(iid)
    it = q._get(iid)
    assert seen["notes"].startswith("「长期留下来」") and it["status"] == "待审核" and it["check_v"] == q.CHECK_VERSION
    assert it["edited"]["email_subject"] == f"投资实习生申请-{N}-2027届硕士" and "留用机会" in it["edited"]["email_body"]
    assert it["analysis"]["result"]["email_subject"].endswith("研二")        # 原稿留着，改动记在 fixes 里
    assert any("留用措辞" in f for f in it["analysis"]["fixes"]) and not it.get("regen_notes")


def test_recheck_with_errors_goes_to_needs_action(q, monkeypatch):
    iid = add_item(q)
    q.recheck(iid)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [{"level": "error", "field": "duplicate", "msg": "重复投递"}]})
    q._regen(iid)
    assert q._get(iid)["status"] == "需处理" and q._get(iid)["live_issues"][0]["msg"] == "重复投递"
    assert q.recheck(add_item(q, status="已发送")) is None                 # 发出去的不重查


def test_recheck_stale_only_old_versions(q):
    old, new = add_item(q), add_item(q)
    q._update(new, check_v=q.CHECK_VERSION)
    assert q.recheck_stale(delay=0) == 1
    assert q._get(old)["status"] == "重写中" and q._get(new)["status"] == "待审核"


def test_scheduled_draft_shows_what_gmail_will_send(q, monkeypatch):
    iid = add_item(q, status="已定时")
    q._update(iid, send_draft=True, record_id="rec1")
    monkeypatch.setattr(records, "get", lambda rid: {"subject": "润色后的标题", "email_body": "润色后的正文"} if rid == "rec1" else None)
    assert q.sync_scheduled_drafts() == 1 and q._get(iid)["edited"]["email_subject"] == "润色后的标题"
    assert q.sync_scheduled_drafts() == 0                                     # 已经对齐：不再动


def test_api_recheck(q):
    iid = add_item(q)
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    d = c.post(f"/api/queue/{iid}/recheck", json={"notes": "删掉近存计算那句"}, headers={**LOCAL, **XRW}).get_json()
    assert d["ok"] and q._get(iid)["regen_notes"] == "删掉近存计算那句"
    assert not c.post(f"/api/queue/{iid}/recheck", json={}, headers={**LOCAL, **XRW}).get_json()["ok"]   # 重查中不能再点


# ── 英文 JD ──────────────────────────────────────────────────

def test_english_multi_job_detected():
    text = "We are hiring.\nPosition 1: Investment Analyst\nPosition 2: Associate\nPlease send CV to hr@abc-capital.com"
    assert fetch.count_job_signals(text) == (2, 1)


def test_english_mail_written_from_english_resume(monkeypatch):
    monkeypatch.setitem(llm._system_cache, "key", None)
    monkeypatch.setattr(resume, "resume_status", lambda path=None: {"ok": True, "en_text": "WORK EXPERIENCE\nPE Intern"})
    assert "<英文简历原文>" in llm.build_system_prompt() and "PE Intern" in llm.build_system_prompt()
    monkeypatch.setitem(llm._system_cache, "key", None)
    monkeypatch.setattr(resume, "resume_status", lambda path=None: {"ok": False, "error": "找不到简历"})
    assert "<英文简历原文>" not in llm.build_system_prompt()
    llm._system_cache["key"] = None                                            # 别把假简历缓存留给后面的测试
