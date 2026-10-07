"""「这批的设置」、研究样本附不附、招聘信息来源只进记录（不写进邮件）的回归测试。不调 AI、不碰 Gmail、不发信。"""

import pytest

import app as panel
from jobapply import checks, config, fetch, jobqueue, llm, pipeline
from tests.test_review import add_item, result

N = config.CANDIDATE_NAME
LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD = "投资实习生招聘，每周实习5天，简历发送至 hr@abc-capital.com，欢迎附上研究样本。" * 2


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    return jobqueue


@pytest.fixture
def store(tmp_path, monkeypatch):
    for k, v in (("RECORDS_PATH", tmp_path / "records.json"), ("BACKUP_DIR", tmp_path / "backups"),
                 ("MATERIALS_DIR", tmp_path), ("EXCEL_MIRROR_PATH", tmp_path / "mirror.xlsx")):
        monkeypatch.setattr(config, k, v)
    return tmp_path


# ── 这批的设置 ─────────────────────────────────────────────

def test_batch_settings_cleaned(q):
    new = q.enqueue(JD, opts={"position_hint": "全职", "resume_hint": "英文", "report_hint": "附上",
                              "extra": "  写短一点 ", "bogus": "x"})
    assert new[0]["opts"] == {"position_hint": "全职", "resume_hint": "英文", "report_hint": "附上", "extra": "写短一点"}
    bad = q.enqueue(JD + "二", opts={"position_hint": "不明确", "resume_hint": "法文", "report_hint": "yes", "extra": 3})
    assert bad[0]["opts"] == {}
    assert q.enqueue(JD + "三")[0]["opts"] == {}
    assert q.enqueue(JD + "四", opts="乱传")[0]["opts"] == {}


def test_settings_reach_ai_and_split_jobs_inherit(q, monkeypatch):
    page = {"title": "某资本招聘", "content": JD, "source_label": "某号（公众号）", "url": "https://mp.weixin.qq.com/s/x",
            "publish_date": "", "ocr_used": False, "qr_urls": []}
    monkeypatch.setattr(fetch, "fetch_url", lambda u: page)
    monkeypatch.setattr(fetch, "count_job_signals", lambda t: (2, 2))
    monkeypatch.setattr(llm, "detect_jobs", lambda t: [{"title": "投资实习生", "location": ""}, {"title": "研究助理", "location": ""}])
    seen = []

    def fake_analyze(jd, **kw):
        seen.append(kw)
        return {"ok": True, "result": result(), "issues": [], "fixes": [], "meta": {}}
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    opts = {"report_hint": "不附", "extra": "别提某段经历"}
    first = q.enqueue("https://mp.weixin.qq.com/s/x", opts=opts)[0]
    q._process(first["id"])
    sib = [it for it in q.list_items() if it["id"] != first["id"]]
    assert len(sib) == 1 and sib[0]["opts"] == opts          # 拆出来的岗位也按这批的设置
    assert q._get(first["id"])["multi_job"] == 2 and sib[0]["multi_job"] == 2
    q._process(sib[0]["id"])
    assert len(seen) == 2 and all(k["report_hint"] == "不附" and k["extra"] == "别提某段经历" for k in seen)
    assert seen[0]["source_label"] == "某号（公众号）"


def test_retry_keeps_settings(q):
    it = q.enqueue(JD, opts={"report_hint": "附上"})[0]
    q._update(it["id"], status="失败")
    assert q.retry(it["id"]) and q._get(it["id"])["opts"] == {"report_hint": "附上"}


def test_old_queue_items_without_settings_still_work(q, monkeypatch):
    it = q._new_item("text", JD)
    del it["opts"]                                            # 改版前存下的条目
    q._save([it])
    seen = []
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: seen.append(kw) or {"ok": False, "error": "x"})
    q._process(it["id"])
    assert seen and "report_hint" not in seen[0]


def test_one_click_never_sends_split_jobs(q, monkeypatch):
    iid = add_item(q)
    q._update(iid, multi_job=3)
    monkeypatch.setattr(pipeline, "deliver", lambda *a, **k: pytest.fail("拆出来的多岗位不该自动发"))
    out = q.process_ready("send")
    assert out["done"] == [] and "拆出了 3 个岗位" in out["skipped"][0]


# ── 研究样本：指定附 / 不附 ───────────────────────────────────

@pytest.mark.parametrize("hint,ai,expected", [("附上", False, True), ("不附", True, False), ("", True, True), ("乱写", False, False)])
def test_report_hint_overrides_ai(monkeypatch, hint, ai, expected):
    calls = {}

    def fake(jd, **kw):
        calls.update(kw)
        return result(attach_report=ai, report_filename="样本.pdf" if ai else ""), {}
    monkeypatch.setattr(llm, "analyze_jd", fake)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    r = pipeline.analyze(JD, report_hint=hint)["result"]
    assert r["attach_report"] is expected and calls["report_hint"] == (hint if hint in llm.REPORT_HINTS else "")
    assert bool(r["report_filename"]) is expected             # 附上就有文件名，不附就清空


def test_prompt_has_hint_but_never_the_source(monkeypatch):
    seen = {}

    def fake_call(**kw):
        seen.update(kw)
        return result(), {}
    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setattr(llm, "build_system_prompt", lambda: "")
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    pipeline.analyze(JD, source_label="某号（公众号）", report_hint="附上")
    assert "附上研究样本" in seen["content"] and "某号" not in seen["content"]
    pipeline.analyze(JD, report_hint="不附")
    assert "不附研究样本" in seen["content"]


@pytest.mark.parametrize("body,attach,level", [
    ("另附一份过往公司研究样本，供参考。", False, "error"),   # 说附了，其实没附
    ("另附一份过往公司研究样本，供参考。", True, None),
    ("简历见附件。", True, "warn"),                            # 附了，正文没提
    ("简历已附上，研究经历包括撰写行业报告。", False, None),   # 讲经历，不是说附件
    ("I have also attached a writing sample for reference.", False, "error"),
    ("另附一份我做的 AI 基础设施行业研究样本（节选），供参考。", True, None),
    ("另附一份 AI 基础设施行业研究（节选）供参考。", False, "error"),   # AI 换了说法也认得出
    ("I have also attached an excerpt of my research on AI infrastructure.", True, None),
    ("Please find my resume attached; my research focuses on AI.", False, None),
])
def test_report_attachment_matches_body(body, attach, level):
    r = result(attach_report=attach, report_filename="样本.pdf" if attach else "",
               email_body=f"您好，\n\n我是{N}，硕士在读，两周内可到岗，每周5天，可以连续实习6个月以上。{body}\n\n{N}\n")
    got = [i["level"] for i in checks.run(r, JD) if i["field"] == "report" and "找不到" not in i["msg"]]
    assert got == ([level] if level else [])


# ── 招聘信息来源：只进记录，邮件里不写 ─────────────────────────

def test_source_never_written_into_email():
    r = result(email_subject=f"投资实习生-{N}-某大学-[招聘信息来源]",
               email_body=f"您好，\n\n我是{N}，硕士在读，两周内可到岗，每周5天。\n信息来源：[招聘信息来源]\n\n简历见附件。\n\n{N}\n")
    fixes = checks.autofix(r, JD)
    assert r["email_subject"] == f"投资实习生-{N}-某大学"
    assert "来源" not in r["email_body"] and "简历见附件" in r["email_body"]
    assert any("标题里的「信息来源」" in f for f in fixes) and any("正文里的「信息来源」" in f for f in fixes)


def test_subject_without_source_untouched():
    r = result(email_subject=f"投资实习生-{N}-")
    fixes = checks.autofix(r, JD)
    assert r["email_subject"] == f"投资实习生-{N}-" and not [f for f in fixes if "来源" in f]


@pytest.mark.parametrize("fmt,warn", [("【岗位】姓名（信息来源）", False), ("岗位-姓名-获取渠道", False), ("【岗位】姓名（学校）", True)])
def test_format_check_ignores_source_field(fmt, warn):
    r = result(email_subject=f"【投资实习生】{N}" if "【" in fmt else f"投资实习生-{N}", jd_rules={"subject_format": fmt})
    assert bool([i for i in checks.run(r, JD) if "JD 格式里有" in i["msg"]]) is warn


def test_record_source_from_page_then_ai():
    kw = dict(source_url="", target_job="", publish_date="", source_type="x")
    assert pipeline._record_fields(result(source_name="某招聘号"), JD, source_label="", **kw)["job_source"] == "某招聘号"
    assert pipeline._record_fields(result(source_name="某招聘号"), JD, source_label="某号（公众号）", **kw)["job_source"] == "某号（公众号）"
    assert pipeline._record_fields(result(), JD, source_label="", **kw)["job_source"] == ""


# ── 面板接口 ───────────────────────────────────────────────

@pytest.fixture
def client(q, store):
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


def test_api_queue_add_with_settings(client):
    r = client.post("/api/queue", json={"text": JD, "opts": {"report_hint": "附上", "resume_hint": "中文"}}, headers={**LOCAL, **XRW})
    assert r.status_code == 200 and jobqueue.list_items()[0]["opts"] == {"report_hint": "附上", "resume_hint": "中文"}


def test_api_analyze_passes_report_hint(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline, "analyze",
                        lambda jd, **kw: seen.update(kw) or {"ok": True, "result": result(), "issues": [], "fixes": [], "meta": {}})
    client.post("/api/analyze", json={"jd_text": JD, "report_hint": "不附"}, headers={**LOCAL, **XRW})
    assert seen["report_hint"] == "不附"


def test_config_lists_report_hints(client):
    assert client.get("/api/config", headers=LOCAL).get_json()["report_hints"] == llm.REPORT_HINTS


# ── 复查后补的 ─────────────────────────────────────────────

def test_send_and_draft_use_upload_endpoint(monkeypatch):
    """普通接口整封超过约 5MB 会 413：发信 / 存草稿一律走上传接口，整封邮件原样上传。"""
    from jobapply import gmail_client
    from tests.test_review import FakeResp, FakeSession
    sess = FakeSession(FakeResp(200, {"id": "d1", "message": {"id": "m1", "threadId": "t1"}, "threadId": "t1"}))
    monkeypatch.setattr(gmail_client, "_session", lambda: sess)
    big = b"%PDF" + b"0" * (6 * 1024 * 1024)
    gmail_client.send(to=["a@b.com"], cc=[], subject="s", body="b", attachments=[("样本.pdf", big)])
    gmail_client.create_draft(to=["a@b.com"], cc=[], subject="s", body="b", attachments=[])
    (u1, k1), (u2, k2) = sess.calls
    assert u1.endswith("/upload/gmail/v1/users/me/messages/send") and u2.endswith("/upload/gmail/v1/users/me/drafts")
    assert k1["params"] == {"uploadType": "media"} and k1["headers"]["Content-Type"] == "message/rfc822"
    assert isinstance(k1["data"], bytes) and b"Subject:" in k1["data"] and "json" not in k1


@pytest.mark.parametrize("subject,company,source,bad", [
    (f"投资实习生-{N}-某大学-Case Mock", "某资本", "Case Mock（公众号）", True),     # AI 没守规矩，把来源写进标题
    (f"某资本投资实习生申请 - {N}", "某资本", "某资本（公众号）", False),          # 机构自己的公众号：名字本来就该出现
    (f"投资实习生申请 - {N}", "某资本", "微信公众号", False),                       # 抓不到号名时的通用叫法
    (f"投资实习生-{N}（信息来源）", "某资本", "", True),                            # 照抄了格式里的「（信息来源）」
    (f"募资经理（渠道）申请 - {N}", "某资本", "", False),                          # 岗位名里的「（渠道）」不算
])
def test_source_never_in_email_check(subject, company, source, bad):
    r = result(email_subject=subject, company_name=company)
    issues = checks.run(r, JD, source_label=source)
    assert bool([i for i in issues if "来源" in i["msg"] and i["level"] == "error"]) is bad


def test_job_title_with_channel_not_mangled():
    r = result(email_subject=f"募资经理（渠道）申请 - {N}", email_body=f"您好，\n\n我申请募资经理（渠道）岗位，两周内可到岗。\n\n{N}\n")
    checks.autofix(r, JD)
    assert "募资经理（渠道）" in r["email_subject"] and "募资经理（渠道）" in r["email_body"]


def test_format_check_whole_bracket_source_field():
    r = result(email_subject=f"{N}-某大学-投资实习生", jd_rules={"subject_format": "姓名-学校-岗位（注明信息来源）"})
    assert not [i for i in checks.run(r, JD) if "JD 格式里有" in i["msg"]]


@pytest.mark.parametrize("body,expect", [
    ("如需研究样本，可随时提供。", False), ("A writing sample is available upon request.", False),
    ("随信附上简历申请贵司行业研究员岗位。", False), ("简历和行业研究报告见附件。", True),
    ("My resume and an excerpt of my research on AI infrastructure are attached.", True),
    ("另附一份我做的 AI 基础设施行业研究样本（节选），供参考。", True),
])
def test_mentions_report_wording(body, expect):
    assert checks.mentions_report(body) is expect


def test_jd_wants_sample_but_not_attached_warns():
    r = result(attach_report=False, jd_rules={"requested_materials": ["中英文简历", "研究报告（必须）"]})
    assert [i["level"] for i in checks.run(r, JD) if "这封没附研究样本" in i["msg"]] == ["warn"]
    r = result(attach_report=True, report_filename="样本.pdf", jd_rules={"requested_materials": ["研究报告"]},
               email_body=f"您好，\n\n我是{N}，两周内可到岗。另附一份过往行业研究样本，供参考。\n\n{N}\n")
    assert not [i for i in checks.run(r, JD) if "这封没附研究样本" in i["msg"]]


# ── 图片里的邮箱：自动双重核对，不用人看图 ─────────────────────

@pytest.fixture
def ocr(monkeypatch):
    """vision：Vision 每张图认出的文字；pick：第二次 Claude 盯着图认的结果（None = 不该被调用）。"""
    state = {"vision": [], "pick": None, "asked": []}
    monkeypatch.setattr(fetch, "vision_ocr", lambda images: state["vision"])

    def pick(images, candidates):
        state["asked"].append(candidates)
        if state["pick"] is None:
            pytest.fail("两种识别一致时不该再问一遍")
        return state["pick"]
    monkeypatch.setattr(llm, "read_email_from_images", pick)
    return state


IMGS = [(b"img", "image/png")]


def test_ocr_email_both_methods_agree(ocr):
    ocr["vision"] = ["简历投递：HR@abc-capital.com"]
    text, ok, bad = fetch.verify_ocr_emails(IMGS, "投递邮箱：hr@abc-capital.com")
    assert ok == ["hr@abc-capital.com"] and bad == [] and ocr["asked"] == []


def test_ocr_email_disagreement_resolved_by_third_read(ocr):
    ocr["vision"] = ["简历投递：hr@abc-capital.com"]
    ocr["pick"] = {"email": "hr@abc-capital.com", "sure": True}         # 第二次 Claude 和 Vision 一致
    text, ok, bad = fetch.verify_ocr_emails(IMGS, "投递邮箱：hr@abc-capita1.com")
    assert ok == ["hr@abc-capital.com"] and "capital.com" in text and "capita1" not in text and bad == []


def test_ocr_email_vision_missed_but_two_reads_agree(ocr):
    ocr["vision"] = ["（这张图 Vision 没认出邮箱）"]
    ocr["pick"] = {"email": "hr@abc-capital.com", "sure": True}
    assert fetch.verify_ocr_emails(IMGS, "投递邮箱：hr@abc-capital.com")[1] == ["hr@abc-capital.com"]


@pytest.mark.parametrize("pick", [{"email": "hr@abc-capital.com", "sure": False}, {"email": "xx@abc-capital.com", "sure": True}])
def test_ocr_email_still_unsure(ocr, pick):
    ocr["vision"] = ["简历投递：hr@abc-capitaI.com"]
    ocr["pick"] = pick
    assert fetch.verify_ocr_emails(IMGS, "投递邮箱：hr@abc-capital.com")[2] == ["hr@abc-capital.com"]


def test_checks_trust_verified_ocr_email_and_block_unsure():
    base = "某资本招聘，详见下图。\n\n" + checks.OCR_MARKER + "\n投递邮箱：hr@abc-capital.com\n"
    ok = base + checks.OCR_VERIFIED + "：hr@abc-capital.com"
    assert not [i for i in checks.run(result(), ok) if i["field"] == "to"]          # 核对过：不用你去看图
    bad = base + checks.OCR_UNSURE + "：hr@abc-capital.com"
    assert [i["level"] for i in checks.run(result(), bad) if i["field"] == "to"] == ["error"]
