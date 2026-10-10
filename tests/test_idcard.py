"""证件号：只存钥匙串、页面只见打码、复制走本机剪贴板并定时清掉、手机隧道上不能存和复制。
钥匙串和剪贴板都换成假的（不碰本人的钥匙串）；测试用的号码在运行时现算校验位，文件里没有完整号码。"""

import pytest

import app as panel
from jobapply import idcard

LOCAL = {"Host": "localhost:5001", "X-Requested-With": "jobapply"}
FIRST17 = "12345619900101123"
REAL_SECURITY = idcard._security          # conftest 默认把它换成「不许碰」；超时那条测试要用真的封装（底下的 subprocess 是假的）


@pytest.fixture
def fake_mac(monkeypatch):
    st = {"kc": "", "clip": "", "timers": []}

    class R:
        def __init__(self, rc=0, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    def security(*args, input_text=None):
        if args[0] == "add-generic-password":
            st["kc"] = args[args.index("-w") + 1]
            return R()
        if args[0] == "find-generic-password":
            return R(0, st["kc"] + "\n") if st["kc"] else R(44)
        if args[0] == "delete-generic-password":
            st["kc"] = ""
            return R()
        raise AssertionError(args)
    monkeypatch.setattr(idcard, "_security", security)
    monkeypatch.setattr(idcard, "_pbcopy", lambda t: st.__setitem__("clip", t))
    monkeypatch.setattr(idcard, "_pbpaste", lambda: st["clip"])
    monkeypatch.setattr(idcard, "_clear_later", lambda n, d: st["timers"].append((n, d)))
    return st


def good():
    return FIRST17 + idcard.check_digit(FIRST17)


def test_normalize_and_checksum():
    assert idcard.normalize(" " + good()[:6] + " " + good()[6:].lower() + " ") == good()
    with pytest.raises(idcard.Invalid):
        idcard.normalize(good()[:-1])                                            # 少一位
    bad = FIRST17 + ("0" if idcard.check_digit(FIRST17) != "0" else "1")
    with pytest.raises(idcard.Invalid, match="校验位"):
        idcard.normalize(bad)
    assert idcard.mask(good()) == good()[:4] + "*" * 10 + good()[-4:]


def test_save_status_copy_forget(fake_mac):
    c = panel.app.test_client()
    assert c.get("/api/idcard", headers=LOCAL).get_json() == {"saved": False, "masked": ""}
    assert c.post("/api/idcard/copy", headers=LOCAL).status_code == 404                 # 没存过
    r = c.put("/api/idcard", json={"number": good()}, headers=LOCAL)
    assert r.status_code == 200 and good() not in r.get_data(as_text=True)               # 回来的只有打码的
    st = c.get("/api/idcard", headers=LOCAL)
    assert st.get_json()["saved"] and good() not in st.get_data(as_text=True)
    r = c.post("/api/idcard/copy", headers=LOCAL)
    assert r.status_code == 200 and good() not in r.get_data(as_text=True)
    assert fake_mac["clip"] == good() and fake_mac["timers"] == [(good(), idcard.CLEAR_AFTER)]   # 进了剪贴板，定好了清
    assert c.put("/api/idcard", json={"number": "123"}, headers=LOCAL).status_code == 400
    assert c.delete("/api/idcard", headers=LOCAL).get_json()["saved"] is False


def test_clear_only_if_still_ours(monkeypatch):
    clip = {"v": ""}
    monkeypatch.setattr(idcard, "_pbcopy", lambda t: clip.__setitem__("v", t))
    monkeypatch.setattr(idcard, "_pbpaste", lambda: clip["v"])
    fired = []

    class T:
        def __init__(self, delay, fn):
            fired.append(fn)
        daemon = False

        def start(self):
            pass
    monkeypatch.setattr(idcard.threading, "Timer", T)
    idcard._clear_later("N1", 60)
    clip["v"] = "本人后来复制的别的东西"
    fired[0]()
    assert clip["v"] == "本人后来复制的别的东西"                                     # 剪贴板已经换了：不清
    clip["v"] = "N1"
    fired[0]()
    assert clip["v"] == ""


def test_tunnel_cannot_save_or_copy(fake_mac):
    c = panel.app.test_client()
    tun = {**LOCAL, "Host": "x.trycloudflare.com", "Cf-Ray": "1"}
    key = panel.PANEL_KEY
    assert c.put(f"/api/idcard?k={key}", json={"number": good()}, headers=tun).status_code == 403
    assert c.post(f"/api/idcard/copy?k={key}", headers=tun).status_code == 403
    assert c.delete(f"/api/idcard?k={key}", headers=tun).status_code == 403
    assert fake_mac["kc"] == "" and fake_mac["clip"] == ""


def test_keychain_timeout_does_not_leak_the_number(monkeypatch):
    """钥匙串卡住（弹了解锁框没点）超时：报错和返回里都不能带命令行（号码在参数里）。"""
    import subprocess

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(idcard, "_security", REAL_SECURITY)
    monkeypatch.setattr(idcard.subprocess, "run", slow)
    c = panel.app.test_client()
    r = c.put("/api/idcard", json={"number": good()}, headers=LOCAL)
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and good() not in body and "钥匙串没响应" in body
    assert idcard.status() == {"saved": False, "masked": ""}                       # 读的时候卡住：当没存


@pytest.mark.parametrize("text", ["证件号码 1234561990****1233", "身份证号：123456 19900101 1233", "读回：123456199001 以后"])
def test_mask_truncated_and_starred_ids(text):
    from jobapply import apps
    out = apps.mask(text)
    assert "[证件号已隐去]" in out and "1990" not in out


def test_mask_leaves_phones_and_long_numbers():
    from jobapply import apps
    t = "电话 13800000000，订单 202610091200，编号 1791568678339"
    assert apps.mask(t) == t


def test_copy_jumps_to_the_application_tab(fake_mac, monkeypatch, tmp_path):
    from jobapply import apps, config, records
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scripts = []
    monkeypatch.setattr(idcard, "_osascript", scripts.append)
    a = apps.create("网申", "跳页资本", entry_url="https://jobs.example.com/apply/9")
    c = panel.app.test_client()
    c.put("/api/idcard", json={"number": good()}, headers=LOCAL)
    r = c.post("/api/idcard/copy", json={"app_id": a["id"]}, headers=LOCAL).get_json()
    assert r["jumped"] is True and 'contains "jobs.example.com"' in scripts[-1] and "activate" in scripts[-1]
    assert fake_mac["clip"] == good()


def test_bring_tab_only_takes_hostname_characters(monkeypatch):
    scripts = []
    monkeypatch.setattr(idcard, "_osascript", scripts.append)
    idcard.bring_tab('evil" & do shell script "x')
    assert "contains" not in scripts[-1] and "do shell script" not in scripts[-1]          # 不像域名的一律不拼进去
