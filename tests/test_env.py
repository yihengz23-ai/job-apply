"""测试环境（JOBAPPLY_ENV=test）：端口、数据目录、假 claude、不连 Gmail、面板地址注入。
config 在导入时就定下运行环境，所以这里都起子进程测，不影响别的测试。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_claude.py"


def run_py(code, tmp_path, **env):
    full = {k: v for k, v in os.environ.items() if not k.startswith("JOBAPPLY_")}
    full.update({"JOBAPPLY_DATA_DIR": str(tmp_path / "data")} if env.get("JOBAPPLY_ENV") == "test" else {})
    full.update(env)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_test_env_paths_and_switches(tmp_path):
    got = run_py("""
import json, os
from jobapply import config, jobqueue, wstasks, wsprofile, agent, uploads, llm
print(json.dumps({"port": config.PORT, "base": config.PANEL_BASE, "data": str(config.DATA_DIR),
  "paths": [str(p) for p in (config.RECORDS_PATH, config.BACKUP_DIR, config.UPLOADS_DIR, config.GMAIL_STATE_PATH,
            config.TOKEN_PATH, config.PANEL_KEY_PATH, config.MATERIALS_DIR, config.EXCEL_MIRROR_PATH, config.TUNNEL_URL_PATH,
            jobqueue.QUEUE_PATH, wstasks.PATH, wstasks.KEY_PATH, wsprofile.PATH, agent.CHATS_DIR, agent.SITES_PATH, uploads.DIR)],
  "token_exists": config.TOKEN_PATH.exists(), "bin": llm._claude_bin(), "backend": config.LLM_BACKEND,
  "api_key": os.environ.get("ANTHROPIC_API_KEY")}))
""", tmp_path, JOBAPPLY_ENV="test", ANTHROPIC_API_KEY="sk-should-be-dropped", JOBAPPLY_BACKEND="api")
    data = str(tmp_path / "data")
    assert got["port"] == 5002 and got["base"] == "http://localhost:5002" and got["data"] == data
    assert all(p.startswith(data + "/") for p in got["paths"]), got["paths"]
    assert not got["token_exists"]
    assert got["bin"] == str(FAKE) and got["backend"] == "claude_code" and got["api_key"] is None


def test_prod_env_keeps_old_paths(tmp_path):
    got = run_py("""
import json
from jobapply import config, wstasks
print(json.dumps({"port": config.PORT, "data": str(config.DATA_DIR), "base": str(config.BASE_DIR),
                  "tasks": str(wstasks.PATH), "bin": config.CLAUDE_BIN}))
""", tmp_path)
    assert got["port"] == 5001 and got["data"] == got["base"] == str(ROOT)
    assert got["tasks"] == str(ROOT / "wangshen_tasks.json") and got["bin"] == ""


def test_unknown_env_refuses_to_start(tmp_path):
    out = subprocess.run([sys.executable, "-c", "from jobapply import config"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "JOBAPPLY_ENV": "testing"})
    assert out.returncode != 0 and "JOBAPPLY_ENV" in out.stderr


def test_test_env_never_reaches_gmail(tmp_path):
    got = run_py("""
import json
from jobapply import gmail_client
errs = []
for fn in (lambda: gmail_client.get_service(), lambda: gmail_client._session(), lambda: gmail_client.get_service(interactive=True)):
    try:
        fn(); errs.append("通了")
    except gmail_client.GmailAuthError as e:
        errs.append(str(e))
print(json.dumps({"status": gmail_client.auth_status(), "errs": errs}))
""", tmp_path, JOBAPPLY_ENV="test")
    assert got["status"] == {"ok": False, "error": "测试环境不连 Gmail"}
    assert got["errs"] == ["测试环境不连 Gmail"] * 3


def test_test_env_panel_serves_its_own_address(tmp_path):
    got = run_py("""
import json
import app
c = app.app.test_client()
h = {"Host": "localhost:5002"}
page = c.get("/", headers=h).get_data(as_text=True)
js = c.get("/wsfill.js", headers=h).get_data(as_text=True)
print(json.dumps({"banner": "测试环境" in page and "<title>【测试环境】" in page,
                  "js_base": "const PANEL_INJECTED = 'http://localhost:5002'" in js, "placeholder_left": "__PANEL_BASE__" in js,
                  "prod_host": c.get("/", headers={"Host": "localhost:5001"}).status_code,
                  "health": c.get("/api/health", headers=h).get_json()}))
""", tmp_path, JOBAPPLY_ENV="test")
    assert got["banner"] and got["js_base"] and not got["placeholder_left"]
    assert got["prod_host"] == 403   # 测试面板不认正式面板的地址
    assert got["health"]["env"] == "test" and got["health"]["port"] == 5002 and got["health"]["app"] == "jobapply"


def test_prod_panel_has_no_banner_and_injects_5001(tmp_path):
    got = run_py("""
import json, os
import app
from jobapply import config
c = app.app.test_client()
h = {"Host": "localhost:5001"}
print(json.dumps({"banner": "测试环境" in c.get("/", headers=h).get_data(as_text=True),
                  "js": "const PANEL_INJECTED = 'http://localhost:5001'" in c.get("/wsfill.js", headers=h).get_data(as_text=True)}))
""", tmp_path, JOBAPPLY_DATA_DIR=str(tmp_path / "prod_data"))
    assert got == {"banner": False, "js": True}


def test_wsfill_falls_back_to_prod_panel_when_not_injected():
    src = (ROOT / "wsfill.js").read_text(encoding="utf-8")
    assert src.count("__PANEL_BASE__") == 1
    assert "const PANEL = /^https?:\\/\\//.test(PANEL_INJECTED) ? PANEL_INJECTED : 'http://localhost:5001';" in src


def test_fake_claude_structured_output(tmp_path):
    schema = {"type": "object", "properties": {"subject": {"type": "string"}, "score": {"type": "integer", "minimum": 1},
                                               "ok": {"type": "boolean"}, "kind": {"enum": ["a", "b"]},
                                               "items": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
              "required": ["subject"]}
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}
    out = subprocess.run([str(FAKE), "-p", "--json-schema", json.dumps(schema), "--output-format", "stream-json"],
                         input=json.dumps(msg) + "\n", capture_output=True, text=True, timeout=30)
    events = [json.loads(x) for x in out.stdout.splitlines()]
    res = [e for e in events if e["type"] == "result"][-1]
    assert res["subtype"] == "success" and not res["is_error"]
    so = res["structured_output"]
    assert so["score"] == 1 and so["ok"] is False and so["kind"] == "a" and len(so["items"]) == 1 and "测试环境" in so["subject"]


def test_fake_claude_chat_turns():
    proc = subprocess.Popen([str(FAKE), "-p", "--chrome", "--input-format", "stream-json", "--resume", "1" * 8 + "-1111-1111-1111-" + "1" * 12],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    for word in ("第一句", "第二句"):
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": word}]}}) + "\n")
        proc.stdin.flush()
    proc.stdin.close()
    events = [json.loads(x) for x in proc.stdout.read().splitlines()]
    proc.wait(timeout=30)
    texts = [b["text"] for e in events if e["type"] == "assistant" for b in e["message"]["content"]]
    assert texts == ["（测试环境假助手）收到：第一句", "（测试环境假助手）收到：第二句"]
    assert [e["type"] for e in events].count("result") == 2 and events[0]["session_id"].startswith("11111111")


@pytest.mark.parametrize("fn", ["_notify"])
def test_test_env_does_not_pop_notifications(tmp_path, fn):
    got = run_py(f"""
import json, subprocess
from jobapply import agent, jobqueue
calls = []
subprocess.run = lambda *a, **k: calls.append(a)
agent.{fn}("标题", "内容"); jobqueue.{fn}("标题", "内容")
print(json.dumps({{"calls": len(calls)}}))
""", tmp_path, JOBAPPLY_ENV="test")
    assert got["calls"] == 0
