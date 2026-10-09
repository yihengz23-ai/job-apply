"""防止草稿里那类错误再出现：写完自查（AI 审稿）、正文提到的附件必须真附上、链接带 https://、篇幅和提速数字提醒、自己加附件。
不调 AI、不碰 Gmail、不发信。"""

import io

import pytest

import app as panel
from jobapply import checks, config, gmail_client, llm, pipeline
from tests.test_review import result

N = config.CANDIDATE_NAME
LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD = "投资实习生招聘，每周实习5天，简历发送至 hr@abc-capital.com。" * 2


# ── 写完自查 ───────────────────────────────────────────────

def test_self_check_applies_editor_fixes(monkeypatch):
    seen = {}

    def review(jd, r, **kw):
        seen.update(kw)
        return {"changes": [{"problem": "把两条经历拼成了一句，漏了「协助」", "before": "搭建研究框架", "after": "协助MD搭建研究框架"}],
                "email_subject": r["email_subject"], "email_body": "您好，\n\n改好的正文。\n\n" + N}, {}
    monkeypatch.setattr(llm, "self_review", review)
    r = result(attach_report=True, report_filename="样本.pdf")
    fixes = pipeline.self_check(r, JD, notes="提一下文章")
    assert r["email_body"].startswith("您好，\n\n改好的正文") and "协助" in fixes[0]
    assert "样本.pdf" in seen["attachments"] and seen["notes"] == "提一下文章"


def test_self_check_no_change_and_failures(monkeypatch):
    r = result()
    body = r["email_body"]
    assert pipeline.self_check(r, JD) == [] and r["email_body"] == body          # conftest：默认没问题
    monkeypatch.setattr(llm, "self_review", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("超时")))
    assert "没跑成" in pipeline.self_check(r, JD)[0] and r["email_body"] == body  # 自查失败：按原稿，不丢信
    assert pipeline.self_check(result(to_emails=[]), JD) == []                     # 网申岗位没有邮件，不查


def test_analyze_runs_self_check(monkeypatch):
    monkeypatch.setattr(llm, "analyze_jd", lambda jd, **kw: (result(), {}))
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    monkeypatch.setattr(llm, "self_review", lambda jd, r, **kw: ({"changes": [{"problem": "太长", "before": "a", "after": "b"}],
                                                                  "email_subject": r["email_subject"], "email_body": "您好，\n\n短了。\n\n" + N}, {}))
    out = pipeline.analyze(JD)
    assert "短了" in out["result"]["email_body"] and any("AI 自查" in f for f in out["fixes"])


# ── 确定性检查 ─────────────────────────────────────────────

def test_mentioned_attachment_must_be_attached():
    body = f"您好，\n\n我是{N}，两周内可到岗。简历和这篇文章见附件，期待交流。\n\n{N}\n"
    errs = [i for i in checks.run(result(email_body=body), JD) if i["field"] == "attach" and i["level"] == "error"]
    assert errs and "文章" in errs[0]["msg"]
    ok = result(email_body=body, extra_attachments=[{"name": "文章.pdf", "path": "x-文章.pdf"}])
    assert not [i for i in checks.run(ok, JD) if i["field"] == "attach" and i["level"] == "error"]


def test_bare_link_gets_https():
    r = result(email_body=f"您好，\n\nDemo：example-user.github.io/job-apply/demo/；邮箱 hr@abc-capital.com。\n\n{N}\n")
    fixes = checks.autofix(r, JD)
    assert "https://example-user.github.io/job-apply/demo/" in r["email_body"] and "hr@abc-capital.com" in r["email_body"]
    assert any("https" in f for f in fixes)
    checks.autofix(r, JD)
    assert r["email_body"].count("https://") == 1                                   # 不会补两遍


def test_speedup_and_long_paragraph_warn():
    long_para = "我目前在某机构实习，" + "参与行业研究和项目工作，" * 20
    body = f"您好，\n\n{long_para}\n\n我搭了投递系统，把单次投递从15–20分钟压到约10秒。\n\n{N}\n"
    msgs = " ".join(i["msg"] for i in checks.run(result(email_body=body), JD))
    assert "批量自动发" in msgs and "120 字左右" in msgs


# ── 自己加附件 ─────────────────────────────────────────────

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "uploads")
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    return tmp_path / "uploads"


def test_upload_and_send_extra_attachment(uploads, monkeypatch):
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    d = c.post("/api/attachment", data={"file": (io.BytesIO(b"%PDF-1.4 article"), "范式都还没定.pdf")},
               headers={**LOCAL, **XRW}, content_type="multipart/form-data").get_json()
    assert d["ok"] and d["name"] == "范式都还没定.pdf" and (uploads / d["path"]).read_bytes() == b"%PDF-1.4 article"
    sent = {}
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    monkeypatch.setattr(pipeline.resume, "build_resume_files", lambda v, f, fe="": [(f, b"%PDF resume")])
    monkeypatch.setattr(gmail_client, "send", lambda **kw: sent.update(kw) or {"message_id": "m", "thread_id": "t"})
    out = pipeline.deliver(result(extra_attachments=[{"name": d["name"], "path": d["path"]}]), JD, mode="send")
    assert [n for n, _ in sent["attachments"]][-1] == "范式都还没定.pdf" and "范式都还没定.pdf" in out["attachments"]


def test_extra_attachment_outside_uploads_refused(uploads):
    uploads.mkdir()
    assert pipeline.extra_path({"path": "../records.json"}) is None and pipeline.extra_path({"path": "nope.pdf"}) is None


def test_attachment_mime_type_follows_file():
    msg = gmail_client.build_mime(to=["a@b.com"], cc=[], subject="s", body="b",
                                  attachments=[("图.png", b"\x89PNG"), ("简历.pdf", b"%PDF")])
    types = [p.get_content_type() for p in msg.get_payload()[1:]]
    assert types == ["image/png", "application/pdf"]


# ── 重复投递：本轮同一个邮箱、同一个岗位 → 拦下 ─────────────────

@pytest.mark.parametrize("old_title,campaign,level", [
    ("战投海外组实习生", config.CURRENT_CAMPAIGN, "error"),   # 本轮已经投过 / 存了草稿：重复
    ("战投海外组实习生", "2026春·实习", "warn"),             # 上一轮投过：提醒就行
    ("投后实习生", config.CURRENT_CAMPAIGN, "warn"),          # 同一个邮箱、别的岗位：提醒
])
def test_same_mailbox_same_job_is_blocked(old_title, campaign, level):
    related = [{"match": "同一邮箱", "campaign": campaign, "job_title": old_title, "sent_at": "2026-10-08 01:00", "status": "草稿", "company_name": "某司"}]
    r = result(job_title="某司战投实习生（海外）")
    lv = [i["level"] for i in checks.run(r, JD, related=related) if i["field"] == "duplicate"]
    assert lv == [level]
