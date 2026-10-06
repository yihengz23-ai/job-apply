"""批量队列测试（不调 AI、不发信：分析和发送都换成假的）。"""

import time

import pytest

from jobapply import jobqueue, pipeline


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(jobqueue, "_maybe_notify", lambda: None)

    def fake_analyze(jd, **kw):
        bad = "[期望日薪]" in jd
        issues = [{"level": "error", "field": "subject", "msg": "占位"}] if bad else []
        result = {"company_name": "某机构", "job_title": "实习生", "to_emails": ["hr@x.com"],
                  "email_subject": "主题", "email_body": "正文"}
        return {"ok": True, "result": result, "issues": issues, "fixes": [], "related": [], "meta": {}}
    monkeypatch.setattr(pipeline, "analyze", fake_analyze)
    return jobqueue


def wait_idle(q, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not any(it["status"] in q.ACTIVE for it in q.list_items()):
            return
        time.sleep(0.05)
    raise AssertionError("队列没处理完")


def test_split_input():
    assert jobqueue.split_input("https://a.com\nhttps://b.com\nhttps://a.com") == [("url", "https://a.com"), ("url", "https://b.com")]
    assert jobqueue.split_input("太短") == []
    jd = "岗位职责：" + "做研究" * 20
    assert jobqueue.split_input(jd) == [("text", jd)]


def test_enqueue_process_and_review_states(q):
    q.enqueue("JD 文字一" + "很长的岗位描述" * 10)
    q.enqueue("JD 文字二 标题要写[期望日薪]" + "很长的岗位描述" * 10)
    wait_idle(q)
    statuses = sorted(it["status"] for it in q.list_items())
    assert statuses == ["待审核", "需处理"]


def test_duplicate_links_not_added_twice(q, monkeypatch):
    monkeypatch.setattr(q, "_submit", lambda item_id: None)  # 只测入队
    assert len(q.enqueue("https://mp.weixin.qq.com/s/abc")) == 1
    assert len(q.enqueue("https://mp.weixin.qq.com/s/abc")) == 0


def test_process_ready_only_sends_clean_items(q, monkeypatch):
    q.enqueue("JD 文字一" + "很长的岗位描述" * 10)
    wait_idle(q)
    sent = []
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": []})
    monkeypatch.setattr(pipeline, "deliver", lambda result, jd, **kw: sent.append(kw["mode"]) or {"ok": True, "mode": "草稿", "record_id": "r1"})
    out = q.process_ready("draft")
    assert sent == ["draft"] and len(out["done"]) == 1
    assert q.list_items()[0]["status"] == "已存草稿"


def test_process_ready_skips_items_with_warnings(q, monkeypatch):
    q.enqueue("JD 文字一" + "很长的岗位描述" * 10)
    wait_idle(q)
    monkeypatch.setattr(pipeline, "review", lambda *a, **k: {"issues": [{"level": "warn", "msg": "以前投过"}]})
    monkeypatch.setattr(pipeline, "deliver", lambda *a, **k: pytest.fail("不该发送"))
    out = q.process_ready("send")
    assert out["done"] == [] and "以前投过" in out["skipped"][0]
