"""WP2 拆路由：拆之前就有的网址，上面的方法、视图函数名和拆之前一模一样（快照 tests/fixtures/url_map_v2.json，
拆之前从 0cc073d 的 app.py 生成，冻结不改）；拦截、响应头、出错处理照样管整个面板，不只管某个蓝图；
拆出来的文件过隐私扫描。不调 AI、不发信。"""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

import app as panel
from jobapply import agent, config, wstasks
from jobapply.web import board_routes, common, ws_routes

ROOT = Path(__file__).resolve().parent.parent
# 方案里写明要删的旧网址：整条删掉不算回归（还在就得和快照一样）。以后按方案删别的旧接口（比如 WP16 删过渡接口），
# 在同一个提交里把网址加进来、注明哪个包；快照不重新生成
PLANNED_GONE = {"/api/wangshen", "/api/fetch-url", "/api/analyze", "/api/queue/process-ready", "/api/queue/clear-done"}  # WP7（6.1）

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}
TUN = {"Host": "abc.trycloudflare.com", "Cf-Ray": "x"}
FOREIGN = {"Host": "evil.example:5001"}


@pytest.fixture
def client():
    panel.app.config["TESTING"] = True
    return panel.app.test_client(use_cookies=False)   # 每个请求只带自己写的头，不带前面请求留下的口令 cookie


def test_old_routes_same_as_before_split():
    """拆之前就有的网址：上面的方法、视图函数名和快照一样，不少、不多、不变（PLANNED_GONE 里的可以整条删掉）。
    新网址不在这里管：按方案 8.2 加接口（WP6、WP8b、WP12 等）不用改这个测试，也不用改快照。"""
    v2 = json.loads((ROOT / "tests" / "fixtures" / "url_map_v2.json").read_text(encoding="utf-8"))   # [网址, 方法, 视图函数名]
    rules = list(panel.app.url_map.iter_rules())
    twice = Counter((r.rule, m) for r in rules for m in r.methods - {"HEAD", "OPTIONS"})
    assert [k for k, n in twice.items() if n > 1] == []   # 同一网址同一方法只能有一个视图函数：后注册的永远调不到
    got = sorted([r.rule, sorted(r.methods), r.endpoint.split(".")[-1]] for r in rules)
    old = {r[0] for r in v2}
    on_old = [r for r in got if r[0] in old]
    want = [r for r in v2 if r[0] not in PLANNED_GONE or r in got]
    lost, extra = [r for r in want if r not in on_old], [r for r in on_old if r not in want]
    assert on_old == want, f"旧网址上的接口变了：少了 {lost}，多了 {extra}（方案里写明要删的旧网址，加进 PLANNED_GONE）"


def test_guard_blocks_foreign_host_and_post_without_header(client):
    assert client.get("/api/records", headers=FOREIGN).status_code == 403            # 防 DNS rebinding
    assert client.get("/api/health", headers={"Host": "localhost:5002"}).status_code == 403
    assert client.post("/api/no-such-route", json={}, headers={**FOREIGN, **XRW}).status_code == 403   # 不属于哪个蓝图的地址也拦
    # 每个蓝图挑一个写接口：没有 X-Requested-With 一律 403（拦在接口前面，不会真去干活）
    for method, url in (("POST", "/api/gmail/auth"), ("POST", "/api/check"), ("POST", "/api/queue"), ("POST", "/api/gmail-sync"),
                        ("DELETE", "/api/records/x"), ("POST", "/api/wstasks"), ("PUT", "/api/wstasks/x"),
                        ("POST", "/api/agent/new"), ("DELETE", "/api/agent/x"), ("PUT", "/api/wangshen-profile")):
        assert client.open(url, method=method, json={}, headers=LOCAL).status_code == 403, url


def test_tunnel_with_wrong_key_rejected(client):
    for bad in ("", "错的", panel.PANEL_KEY + "x"):
        assert client.get(f"/api/records?k={bad}", headers=TUN).status_code == 403
        assert client.get("/api/health", headers={**TUN, "X-Panel-Key": bad}).status_code == 403
        assert client.get("/api/health", headers={**TUN, "Cookie": f"panel_key={bad}"}).status_code == 403
    ok = client.get(f"/api/health?k={panel.PANEL_KEY}", headers=TUN)
    assert ok.status_code == 200 and f"panel_key={panel.PANEL_KEY}" in ok.headers["Set-Cookie"]   # 带对口令：记成 cookie
    assert client.post(f"/api/gmail/auth?k={panel.PANEL_KEY}", json={}, headers={**TUN, **XRW}).status_code == 403   # 授权只在本机


def test_wsreadback_reaches_its_own_key_check_without_header(client):
    url = "/api/wsreadback/t-none"
    text = {"Content-Type": "text/plain"}   # 网申网站页面跨域发来：带不了面板的请求头
    bad = client.post(url, data=json.dumps({"key": "猜的", "kind": "status", "text": "进度"}), headers={**LOCAL, **text})
    assert bad.status_code == 403 and "口令不对" in bad.get_json()["error"]   # 是接口自己的口令检查，不是拦截那一层
    assert bad.headers["Access-Control-Allow-Origin"] == "*"
    good = client.post(url, data=json.dumps({"key": wstasks.readback_key("t-none"), "kind": "status", "text": "进度"}),
                       headers={**LOCAL, **text})
    assert good.status_code == 404 and good.get_json()["error"] == "这条待办不存在了"
    assert client.open(url, method="OPTIONS", headers=LOCAL).status_code == 204
    assert client.post(url + f"?k={panel.PANEL_KEY}", data="{}", headers={**TUN, **text}).status_code == 403   # 隧道来的一律不收
    assert client.post(url, data="{}", headers={**FOREIGN, **text}).status_code == 403


def test_hooks_cover_whole_app(client, monkeypatch):
    assert common.guard in panel.app.before_request_funcs[None] and common.remember_key in panel.app.after_request_funcs[None]
    r = client.get("/api/no-such-route", headers=LOCAL)          # 不属于任何蓝图：照样是统一的出错格式和响应头
    assert r.status_code == 404 and r.get_json()["error"] and r.headers["X-Frame-Options"] == "DENY"
    assert client.delete("/api/health", headers={**LOCAL, **XRW}).status_code == 405
    for url in ("/api/health", "/api/queue", "/api/records", "/api/wstasks", "/api/agent", "/api/wangshen-profile"):
        r = client.get(url, headers=LOCAL)
        assert r.status_code == 200 and r.headers["X-Frame-Options"] == "DENY" and r.headers["Referrer-Policy"] == "no-referrer", url

    def boom():
        raise RuntimeError("坏了")
    monkeypatch.setattr(agent, "list_chats", boom)                # 蓝图里的接口出错：走 app 级的出错处理
    r = client.get("/api/agent", headers=LOCAL)
    assert r.status_code == 500 and r.get_json() == {"error": "RuntimeError: 坏了"}


def test_old_names_still_on_app_module():
    assert panel._gmail_job_lock is board_routes._gmail_job_lock
    assert panel._continue_message is ws_routes._continue_message and panel._fill_message is ws_routes._fill_message
    assert panel.PANEL_KEY == common.PANEL_KEY and panel.LOCAL_HOSTS is common.LOCAL_HOSTS and panel.config is config
    assert (panel.TEST_ENV_BANNER, panel.STARTED_AT, panel.VERSION) == (common.TEST_ENV_BANNER, common.STARTED_AT, common.VERSION)
    assert panel.app.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024 and panel.app.json.ensure_ascii is False


def test_split_files_pass_privacy_scan():
    """app.py、jobapply/web/ 下的接口文件、这个测试和快照：用公开版推送前同一套扫描（sync_public.scan）查一遍，
    不靠它的同步清单，以后新加的接口文件也照样查。公开版没有这个脚本，跳过。"""
    sp = ROOT / "scripts" / "sync_public.py"
    if not sp.exists():
        pytest.skip("公开版没有 sync_public.py")
    spec = importlib.util.spec_from_file_location("sync_public_for_routes", sp)
    sync_public = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync_public)
    files = ["app.py", "tests/test_routes_split.py", "tests/fixtures/url_map_v2.json"] + \
        [str(p.relative_to(ROOT)) for p in sorted((ROOT / "jobapply" / "web").glob("*.py"))]
    bad = sync_public.scan(ROOT, files)
    assert not bad, bad
