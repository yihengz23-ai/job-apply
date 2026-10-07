"""按补充要求重写：放到后台做，写好存回原来那一条（中途去看别的也不会丢）。不调 AI、不碰 Gmail、不发信。"""

import pytest

import app as panel
from jobapply import jobqueue, pipeline
from tests.test_review import add_item, result

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
JD2 = "（审核时改过的 JD）投资实习生招聘，每周实习5天，简历发送至 hr@abc-capital.com。" * 2


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)
    monkeypatch.setattr(jobqueue, "_submit", lambda item_id: None)
    submitted = []
    monkeypatch.setattr(jobqueue, "_submit_regen", submitted.append)
    jobqueue.submitted = submitted
    return jobqueue


def new_out(**kw):
    return {"ok": True, "result": result(email_subject="重写后的标题", **kw), "issues": [], "fixes": [], "meta": {}}


def test_regen_runs_in_background_and_replaces_mail(q, monkeypatch):
    iid = add_item(q)
    q.save_edits(iid, result(email_subject="改到一半的旧标题"))
    old_rev = q._get(iid)["rev"]
    it = q.regen(iid, jd_text=JD2, target_job="投资实习生", opts={"report_hint": "附上", "extra": "再短一点", "bogus": 1})
    assert it["status"] == "重写中" and it["regen_req"] == {"report_hint": "附上", "extra": "再短一点"} and q.submitted == [iid]
    seen = {}
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: seen.update(kw, jd=jd) or new_out())
    q._regen(iid)
    it = q._get(iid)
    assert seen["jd"] == JD2 and seen["report_hint"] == "附上" and seen["extra"] == "再短一点"
    assert it["status"] == "待审核" and it["edited"] is None and it["rev"] != old_rev
    assert it["analysis"]["result"]["email_subject"] == "重写后的标题"


def test_regen_failure_keeps_old_mail(q, monkeypatch):
    iid = add_item(q, status="需处理")
    q.regen(iid, jd_text="短")
    assert q._get(iid)["jd_text"] != "短"                       # 太短的 JD 不拿来覆盖原文
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: (_ for _ in ()).throw(RuntimeError("AI 超时")))
    q._regen_safe(iid)
    it = q._get(iid)
    assert it["status"] == "需处理" and it["error"].startswith("重写失败") and it["analysis"]["result"]["email_subject"]


def test_regen_only_for_reviewable_and_survives_delete(q, monkeypatch):
    iid = add_item(q, status="已发送")
    assert q.regen(iid) is None
    iid2 = add_item(q)
    q.regen(iid2, jd_text=JD2)
    q.delete(iid2)
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: new_out())
    q._regen(iid2)                                                # 期间被删了：不报错
    assert q._get(iid2) is None


def test_regen_resumed_after_restart(q):
    iid = add_item(q)
    q.regen(iid, jd_text=JD2)
    q.submitted.clear()
    q.resume_pending()
    assert q.submitted == [iid] and q._get(iid)["status"] == "重写中"


def test_api_regen(q):
    iid = add_item(q)
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    d = c.post(f"/api/queue/{iid}/regen", json={"jd_text": JD2, "extra": "再短一点", "report_hint": "不附"}, headers={**LOCAL, **XRW}).get_json()
    assert d["ok"] and jobqueue._get(iid)["status"] == "重写中" and jobqueue._get(iid)["regen_req"]["report_hint"] == "不附"
    assert not c.post(f"/api/queue/{iid}/regen", json={}, headers={**LOCAL, **XRW}).get_json()["ok"]   # 重写中不能再点


def test_skip_moves_to_end_of_line(q):
    a, b = add_item(q), add_item(q)
    assert q.skip(a) and q._get(a)["skipped_at"] and not q._get(b).get("skipped_at")
    q._update(b, status="已发送")
    assert not q.skip(b)                                          # 发出去的不用排队
    panel.app.config["TESTING"] = True
    assert panel.app.test_client().post(f"/api/queue/{a}/skip", json={}, headers={**LOCAL, **XRW}).get_json()["ok"]


def test_refresh_issues_drops_stale_warnings(q, monkeypatch):
    iid = add_item(q)
    q._update(iid, analysis={**q._get(iid)["analysis"], "issues": [{"level": "warn", "field": "to", "msg": "旧规则的提示"}]})
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    assert q.refresh_issues() == 1 and q._get(iid)["live_issues"] == []


def test_ocr_retry_with_all_images_when_unsure(monkeypatch):
    from jobapply import fetch, llm
    calls = []
    monkeypatch.setattr(fetch, "vision_ocr", lambda images: ["无关文字", "投递：hr@abc-capitaI.com"])

    def pick(images, candidates):
        calls.append(len(images))
        return {"email": "hr@abc-capital.com", "sure": len(images) == 2}   # 只给一张图时没看清，全给就看清了
    monkeypatch.setattr(llm, "read_email_from_images", pick)
    text, ok, bad = fetch.verify_ocr_emails([(b"a", "image/png"), (b"b", "image/png")], "投递邮箱：hr@abc-capital.com")
    assert calls == [1, 2] and ok == ["hr@abc-capital.com"] and bad == []


@pytest.mark.parametrize("ai,ctype,edited,expect", [
    ("双语", "企业/大厂", None, "中文"),          # 改版前 AI 写的双语：按现在的规则改成中文
    ("双语", "企业/大厂", "双语", "中文"),        # 审核时没动过版本（和 AI 一样）：也改
    ("双语", "双币VC/PE", None, "双语"),          # 双币基金本来就该双语
    ("中文", "企业/大厂", "双语", "双语"),        # 你自己改成双语的：不动
])
def test_refresh_switches_old_bilingual_resume(q, monkeypatch, ai, ctype, edited, expect):
    iid = add_item(q, resume_version=ai, company_type=ctype)
    if edited:
        q.save_edits(iid, result(resume_version=edited, company_type=ctype))
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [], "related": [], "resume": {}})
    q.refresh_issues()
    it = q._get(iid)
    assert (it.get("edited") or it["analysis"]["result"])["resume_version"] == expect


def test_fetch_retried_before_failing(q, monkeypatch):
    from jobapply import fetch
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) < 3:
            raise fetch.FetchError("微信不让自动抓取这篇文章（页面结构异常）")
        return {"title": "t", "content": "投资实习生招聘，每周实习5天，简历发送至 hr@abc-capital.com。" * 3, "source_label": "某号",
                "url": url, "publish_date": "", "ocr_used": False, "qr_urls": []}
    monkeypatch.setattr(fetch, "fetch_url", flaky)
    monkeypatch.setattr(q.time, "sleep", lambda s: None)
    monkeypatch.setattr(fetch, "count_job_signals", lambda t: (0, 1))
    monkeypatch.setattr(pipeline, "analyze", lambda jd, **kw: {"ok": True, "result": result(), "issues": [], "fixes": [], "meta": {}})
    it = q.enqueue("https://mp.weixin.qq.com/s/flaky")[0]
    q._process(it["id"])
    assert len(calls) == 3 and q._get(it["id"])["status"] == "待审核"
