"""网页面板的安全拦截与只读接口测试（不调 AI、不发信）。"""

import pytest

import app as panel


@pytest.fixture
def client():
    panel.app.config["TESTING"] = True
    return panel.app.test_client()


LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}


def test_index_and_config(client):
    assert client.get("/", headers=LOCAL).status_code == 200
    cfg = client.get("/api/config", headers=LOCAL).get_json()
    assert cfg["model"] and cfg["local"] is True


def test_post_without_custom_header_rejected(client):
    r = client.post("/api/check", json={"result": {}, "jd_text": ""}, headers=LOCAL)
    assert r.status_code == 403


def test_foreign_host_rejected(client):
    assert client.get("/api/records", headers={"Host": "evil.example:5001"}).status_code == 403


def test_tunnel_needs_key(client):
    tun = {"Host": "abc.trycloudflare.com", "Cf-Ray": "x"}
    assert client.get("/api/records", headers=tun).status_code == 403
    assert client.get(f"/api/records?k={panel.PANEL_KEY}", headers=tun).status_code == 200
    assert client.post(f"/api/gmail/auth?k={panel.PANEL_KEY}", headers={**tun, **XRW}, json={}).status_code == 403


def test_check_endpoint(client):
    body = {"result": {"to_emails": "hr@x.com", "email_subject": "a", "email_body": "您好，\n\n短\n\n" + panel.config.CANDIDATE_NAME,
                       "resume_version": "双语", "resume_filename": "a", "jd_rules": {}},
            "jd_text": "投递 hr@x.com"}
    d = client.post("/api/check", json=body, headers={**LOCAL, **XRW}).get_json()
    assert any(i["level"] == "error" and i["field"] == "body" for i in d["issues"])


def test_records_and_stats(client):
    recs = client.get("/api/records?campaign=全部", headers=LOCAL).get_json()
    assert isinstance(recs, list) and all("_type" in r for r in recs)
    st = client.get("/api/stats?campaign=全部", headers=LOCAL).get_json()
    assert st["total"] == len(recs)


@pytest.mark.skipif(not panel.config.RESUME_PATH.exists(), reason="没有简历文件")
def test_resume_preview(client):
    r = client.get("/api/resume-preview?version=英文", headers=LOCAL)
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
