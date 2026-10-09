"""所有测试共用：
- 绝不真调 AI（会员额度 / API 都不碰）。要测 AI 那一步的测试自己再 monkeypatch。
- 绝不碰真实文件：桌面「自动投递」（Excel 镜像、网申上传）、面板目录里的数据（看板、队列、网申、底稿、对话、网站笔记）、
  Gmail 令牌、面板访问密钥。每个测试自动把这些路径换到临时目录；会话开始和结束各记一次真实文件的修改时间，
  有任何变化就判失败并列出是哪些文件（守卫）。
- 要读真实简历 / 网申资料包的测试照常读（只读，不在守卫里算改动）。"""

import os
import tempfile
from pathlib import Path

import pytest

# 进程内的测试一律按正式环境的样子跑（端口 5001、路径由下面的隔离夹具换走）；测试环境本身由 test_env.py 起子进程测
for _k in ("JOBAPPLY_ENV", "JOBAPPLY_DATA_DIR", "JOBAPPLY_PORT", "JOBAPPLY_CLAUDE_BIN"):
    os.environ.pop(_k, None)

from jobapply import config, llm  # noqa: E402

# 面板一 import 就会在自己目录里生成访问密钥：import app 之前先指到临时目录
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="jobapply-test-"))
config.PANEL_KEY_PATH = _TMP_ROOT / ".panel_key"

# ── 守卫：真实文件一个都不许动 ───────────────────────────────────
_DATA = getattr(config, "DATA_DIR", config.BASE_DIR)   # 面板会写的数据（正式环境就是项目目录）
_REAL_FILES = [_DATA / n for n in ("records.json", ".records.lock", "queue.json", "wangshen_tasks.json",
                                   "wangshen_profile.json", "wangshen_sites.json", "gmail_state.json", "token.json",
                                   ".wsreadback_key", ".panel_key")] + \
              [config.BASE_DIR / n for n in ("application_kit.json", "candidate_settings.json", "candidate_profile.md",
                                             "email_rules.md", "wangshen_rules.md")]
_REAL_DIRS = [_DATA / "agent_chats", _DATA / "backups", _DATA / "uploads", config.MATERIALS_DIR]


def _snapshot():
    snap = {}
    for p in _REAL_FILES:
        if p.exists():
            st = p.stat()
            snap[str(p)] = (st.st_mtime_ns, st.st_size)
    for d in _REAL_DIRS:
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.name != ".DS_Store" and not p.name.startswith("~$"):
                    st = p.stat()
                    snap[str(p)] = (st.st_mtime_ns, st.st_size)
    return snap


def pytest_sessionstart(session):
    session.config._real_files_before = _snapshot()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_real_files_before", None)
    if before is None or os.environ.get("JOBAPPLY_SKIP_GUARD"):
        return
    after = _snapshot()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        print("\n\n【守卫】测试期间这些真实文件被改动了（测试不许碰真实文件；如果正在用的面板同时在写，也可能是它写的）：")
        for k in changed[:30]:
            print("  -", k, "（新出现）" if k not in before else "（被删）" if k not in after else "")
        session.exitstatus = 1


# ── 每个测试：AI 不真调、真实文件不碰 ───────────────────────────────

@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    def refuse(**kw):
        raise RuntimeError("测试里不许真调 AI：请在测试里 monkeypatch 掉这一步")
    monkeypatch.setattr(llm, "_call", refuse)
    # 写完自查默认「没问题」：大部分测试只关心别的环节
    monkeypatch.setattr(llm, "self_review", lambda jd_text, result, **kw: ({"changes": [], "email_subject": "", "email_body": ""}, {}))


@pytest.fixture(autouse=True)
def isolate_real_files(tmp_path_factory, monkeypatch):
    """所有会写的路径都换到这个测试自己的临时目录（不放进 tmp_path，免得打扰测试自己的目录）。
    测试里再自己指定路径的，以测试为准（monkeypatch 后来者赢）。"""
    from jobapply import agent, jobqueue, uploads, wsprofile, wstasks
    iso = tmp_path_factory.mktemp("iso")
    mat = iso / "materials"
    mat.mkdir(parents=True)
    for name, value in {
        "DATA_DIR": iso, "RECORDS_PATH": iso / "records.json", "BACKUP_DIR": iso / "backups", "UPLOADS_DIR": iso / "uploads",
        "GMAIL_STATE_PATH": iso / "gmail_state.json", "TOKEN_PATH": iso / "token.json",
        "CREDENTIALS_PATH": iso / "credentials.json", "EXCEL_MIRROR_PATH": mat / "投递记录（自动同步，勿手改）.xlsx",
        "PANEL_KEY_PATH": iso / ".panel_key",
    }.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(jobqueue, "QUEUE_PATH", iso / "queue.json")
    monkeypatch.setattr(wstasks, "PATH", iso / "wangshen_tasks.json")
    monkeypatch.setattr(wstasks, "KEY_PATH", iso / ".wsreadback_key")
    monkeypatch.setattr(wsprofile, "PATH", iso / "wangshen_profile.json")
    monkeypatch.setattr(agent, "CHATS_DIR", iso / "agent_chats")
    monkeypatch.setattr(agent, "SITES_PATH", iso / "wangshen_sites.json")
    monkeypatch.setattr(uploads, "DIR", mat / "网申上传")
    return iso
