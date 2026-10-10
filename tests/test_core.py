"""核心逻辑测试（不调 AI、不碰 Gmail）：.venv/bin/python -m pytest tests -q"""

import email
import json
from email.header import decode_header, make_header
from pathlib import Path

import pymupdf
import pytest

from jobapply import checks, config, gmail_client, records, resume

N = config.CANDIDATE_NAME
JD = """【投研实习生】正势达招聘 上海长宁区
请发送简历至jessica.tsai@mtxpartners.com，可附上过往产出的投研报告。
联系人：李女士。截止 2026-12-31"""


def base_result(**kw):
    r = {
        "is_jd": True, "company_name": "正势达", "company_type": "PE/并购基金", "job_title": "投研实习生",
        "position_type": "留用实习", "job_location": "上海", "jd_language": "中文", "apply_channel": "邮箱",
        "to_emails": ["jessica.tsai@mtxpartners.com"], "cc_emails": [], "apply_url": "", "contact_in_jd": "",
        "jd_rules": {"subject_format": "", "resume_filename_format": "", "report_filename_format": "",
                     "body_requirements": [], "requested_materials": []},
        "fit_warnings": [], "missing_info": [], "resume_version": "双语",
        "resume_filename": f"{N}-简历.pdf", "resume_filename_en": "", "attach_report": False,
        "report_filename": "", "email_subject": f"投研实习生申请 - {N}",
        "email_body": f"您好，\n\n我是{N}，硕士在读，两周内可到岗，每周5天。\n\n我目前在一家基金做股权投资实习。\n\n简历见附件。\n\n{N}\n",
        "deadline": "", "job_post_date": "",
    }
    r.update(kw)
    return r


def run(result, jd=JD, **kw):
    return checks.run(result, jd, **kw)


def levels(issues, field=None):
    return [(i["level"], i["field"]) for i in issues if field is None or i["field"] == field]


# ── 邮箱 ───────────────────────────────────────────────────

def test_split_emails_formats():
    assert checks.split_emails("A@x.com; b@y.cn，c@z.com mailto:d@w.org") == ["a@x.com", "b@y.cn", "c@z.com", "d@w.org"]
    assert checks.split_emails(["hr＠abc.com", "hr@abc.com"]) == ["hr@abc.com"]
    assert checks.split_emails("") == []


def test_email_must_come_from_jd():
    issues = run(base_result(to_emails=["hr@mtxpartners.com"]))
    assert ("error", "to") in levels(issues)


def test_obfuscated_email_is_warning_not_error():
    jd = "简历请发送至 talent[at]k2vc.com"
    issues = run(base_result(to_emails=["talent@k2vc.com"]), jd=jd)
    assert ("warn", "to") in levels(issues) and ("error", "to") not in levels(issues)


def test_no_email_but_web_apply_is_info():
    issues = run(base_result(to_emails=[], apply_channel="网申/链接"))
    assert ("info", "to") in levels(issues)


# ── 称呼 ───────────────────────────────────────────────────

def test_greeting_guessed_from_email_is_fixed():
    r = base_result(email_body=f"Jessica总您好，\n\n正文内容足够长足够长足够长足够长足够长足够长足够长。\n\n{N}\n")
    fixes = checks.autofix(r, JD)
    assert r["email_body"].startswith("您好，") and fixes


def test_greeting_from_jd_contact_is_kept():
    r = base_result(contact_in_jd="李女士", email_body=f"李女士您好，\n\n正文内容足够长足够长足够长足够长足够长。\n\n{N}\n")
    checks.autofix(r, JD)
    assert r["email_body"].startswith("李女士您好，")


def test_english_greeting_fixed_to_hello():
    r = base_result(jd_language="英文", email_body=f"Hi Ran,\n\nBody text that is long enough for the check.\n\nBest regards,\n{config.CANDIDATE_NAME_EN}\n")
    checks.autofix(r, "Send CV to ran.chen@nrl-capital.com")
    assert r["email_body"].startswith("Hello,")


@pytest.mark.skipif(not config.SPELLING_FIXES, reason="没配置统一写法")
def test_spelling_fixed():
    wrong, right = next(iter(config.SPELLING_FIXES.items()))
    r = base_result(email_body=f"您好，\n\n本科毕业于{wrong}，正文足够长足够长足够长足够长。\n\n{N}\n")
    checks.autofix(r, JD)
    assert right in r["email_body"] and wrong not in r["email_body"]


# ── 内容 ───────────────────────────────────────────────────

@pytest.mark.parametrize("bad", config.NOT_ON_RESUME[:3])
def test_experience_not_on_resume_blocked(bad):
    r = base_result(email_body=f"您好，\n\n我曾在{bad}相关岗位实习，正文足够长足够长足够长足够长。\n\n{N}\n")
    assert ("error", "body") in levels(run(r))


@pytest.mark.parametrize("bad", config.WRONG_GRADUATION[:3])
def test_wrong_graduation_blocked(bad):
    r = base_result(email_body=f"您好，\n\n我是{N}，{bad}毕业，正文足够长足够长足够长足够长足够长。\n\n{N}\n")
    assert ("error", "body") in levels(run(r))


def test_placeholder_blocked():
    r = base_result(email_subject=f"实习生-{N}-某大学-[期望日薪]")
    assert ("error", "subject") in levels(run(r))


def test_invented_number_and_claim_warned():
    body = f"您好，\n\n我主导了37个项目的建模，正文足够长足够长足够长足够长。\n\n{N}\n"
    msgs = " ".join(i["msg"] for i in run(base_result(email_body=body), profile_text="A资本 30 页"))
    assert "37" in msgs and "主导" in msgs and "建模" in msgs


def test_numbers_from_resume_text_allowed():
    body = f"Hello,\n\nI supported a growth financing (RMB 100M) at a PE fund, long enough body.\n\nBest regards,\n{config.CANDIDATE_NAME_EN}\n"
    issues = run(base_result(email_body=body, jd_language="英文", email_subject=f"Application - {config.CANDIDATE_NAME_EN}"),
                 resume_status={"ok": True, "grad_problems": [], "text": "proposed investment: RMB 100M"})
    assert not [i for i in issues if "数字" in i["msg"]]


@pytest.mark.skipif(not config.CURRENT_EMPLOYER_KEYWORDS, reason="没配置现单位")
def test_current_employer_and_deadline_warned():
    issues = run(base_result(company_name=config.CURRENT_EMPLOYER_KEYWORDS[0], deadline="2020-01-01"))
    assert ("warn", "company") in levels(issues) and ("warn", "deadline") in levels(issues)


def test_subject_requires_name_only_when_format_says_so():
    r = base_result(email_subject="投研实习生-某大学",
                    jd_rules={**base_result()["jd_rules"], "subject_format": "岗位-学校"})
    assert not [i for i in run(r) if i["field"] == "subject" and i["level"] == "error"]
    r["jd_rules"]["subject_format"] = "岗位-姓名-学校"
    assert ("error", "subject") in levels(run(r))


def test_filename_sanitized():
    assert checks.sanitize_filename("a/b:c", "x.pdf") == "a-b-c.pdf"
    assert checks.sanitize_filename("", "默认.pdf") == "默认.pdf"


# ── 简历 ───────────────────────────────────────────────────

@pytest.mark.skipif(not config.RESUME_PATH.exists(), reason="没有简历文件")
def test_resume_status_and_versions():
    st = resume.resume_status()
    assert st["ok"] and st["pages"] == 2 and st["zh_pages"] == [0] and st["en_pages"] == [1]
    assert st["grad_problems"] == []
    for version, n_files, pages in (("双语", 1, 2), ("中文", 1, 1), ("英文", 1, 1), ("中英两份", 2, 1)):
        files = resume.build_resume_files(version, "a.pdf", "b.pdf")
        assert len(files) == n_files
        for _, data in files:
            assert pymupdf.open(stream=data, filetype="pdf").page_count == pages
    en = resume.build_resume_files("英文", "a.pdf")[0][1]
    must = config.RESUME_MUST_CONTAIN.get("英文")
    assert not must or must in pymupdf.open(stream=en, filetype="pdf")[0].get_text()


def test_old_resume_with_wrong_date_detected():
    old = next(config.MATERIALS_DIR.glob("_旧版/*误写2026.12*.pdf"), None)
    if old is None:
        pytest.skip("旧简历不在")
    assert resume.resume_status(old)["grad_problems"]


# ── 邮件组装 ───────────────────────────────────────────────

def test_mime_chinese_attachment_names():
    msg = gmail_client.build_mime(to=["a@b.com"], cc=["c@d.com"], subject=f"投资实习生申请 - {N}｜某大学",
                                  body=f"您好，\n\n正文\n\n{N}\n",
                                  attachments=[(f"{N}-某大学-简历.pdf", b"%PDF-1.4 test"),
                                               (f"【投研实习生+{N}+某大学+2027届+每周5天共6月】.pdf", b"%PDF")])
    parsed = email.message_from_bytes(msg.as_bytes())
    assert str(make_header(decode_header(parsed["Subject"]))) == f"投资实习生申请 - {N}｜某大学"
    assert config.SENDER_NAME in str(make_header(decode_header(parsed["From"])))
    names = []
    for part in parsed.walk():
        if part.get_content_type() == "application/pdf":
            assert len(part.get_all("Content-Type")) == 1
            names.append(str(make_header(decode_header(part.get_filename()))))
    assert names == [f"{N}-某大学-简历.pdf", f"【投研实习生+{N}+某大学+2027届+每周5天共6月】.pdf"]


# ── 记录存储 ───────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path)
    monkeypatch.setattr(config, "EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")
    return tmp_path


def test_records_roundtrip_and_excel(tmp_store):
    rid = records.add(records.new_record(company_name="红杉中国", to_email="hr@hongshan.com"))
    rid2 = records.add(records.new_record(company_name="红杉资本中国基金", to_email="a@qq.com"))
    assert len(records.load()) == 2
    assert (tmp_store / "mirror.xlsx").exists()
    rel = records.find_related("红杉中国", ["jobs@hongshan.com"])
    assert {r["id"] for r in rel} == {rid, rid2}
    rel_public = records.find_related("别的机构", ["b@qq.com"])
    assert rel_public == []
    assert records.update(rid, {"status": "面试中"})
    rec = records.get(rid)
    assert rec["status"] == "面试中" and "→" not in rec["notes"]              # 状态变化不再写进备注（B89）
    assert [(h["from"], h["to"]) for h in rec["history"]] == [("已投递", "面试中")]
    from jobapply import apps
    assert any("已投递 → 面试" in e["text"] for e in apps.get(rec["app_id"])["timeline"])   # 写进了申请的时间线


def test_duplicate_send_is_still_recorded(tmp_store):
    records.add(records.new_record(company_name="A", to_email="hr@a.com"))
    records.add(records.new_record(company_name="A", to_email="hr@a.com"))
    assert len(records.load()) == 2


def test_corrupt_records_never_overwritten(tmp_store):
    config.RECORDS_PATH.write_text('{"records": [ {"id": "x"', encoding="utf-8")
    with pytest.raises(records.RecordsCorrupt):
        records.add(records.new_record(company_name="B"))
    assert config.RECORDS_PATH.read_text(encoding="utf-8").startswith('{"records": [ {"id": "x"')


def test_old_records_get_campaign(tmp_store):
    config.RECORDS_PATH.write_text(json.dumps({"records": [{"id": "1", "sent_at": "2026-04-05 18:47", "to_email": "a@b.com"}]}), encoding="utf-8")
    r = records.load()[0]
    assert r["campaign"] == config.OLD_CAMPAIGN and r["position_type"] == "实习" and r["send_mode"] == "发送"


def test_company_type_normalization():
    assert records.norm_company_type("本土美元VC/PE") == "双币VC/PE"
    assert records.norm_company_type("精品FA/投行（一级市场）") == "券商/投行/FA"
    assert records.norm_company_type("美元VC") == "美元VC"
    assert records.norm_company_type("国资PE") == "国资/政府引导基金"
