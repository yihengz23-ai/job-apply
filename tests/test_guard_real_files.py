"""守卫本身也要测：隔离夹具真的把会写的路径都换走了，守卫真的能发现真实文件被改。"""

from pathlib import Path

from jobapply import agent, config, jobqueue, records, uploads, wsprofile, wstasks
from tests import conftest


def test_every_writable_path_is_isolated():
    real_base, real_mat = Path(conftest.config.BASE_DIR), Path.home() / "Desktop" / "自动投递"
    for p in (config.DATA_DIR, config.RECORDS_PATH, config.BACKUP_DIR, config.UPLOADS_DIR, config.GMAIL_STATE_PATH, config.TOKEN_PATH,
              config.CREDENTIALS_PATH, config.EXCEL_MIRROR_PATH, config.PANEL_KEY_PATH, jobqueue.QUEUE_PATH, wstasks.PATH,
              wstasks.KEY_PATH, wsprofile.PATH, agent.CHATS_DIR, agent.SITES_PATH, uploads.DIR):
        assert Path(p) != real_base and real_base not in Path(p).parents and real_mat not in Path(p).parents, p


def test_saving_records_never_touches_desktop(tmp_path):
    records.save([records.new_record(company_name="测试资本", job_title="实习生")])   # 会顺手导出 Excel
    assert config.EXCEL_MIRROR_PATH.exists() and Path.home() / "Desktop" not in config.EXCEL_MIRROR_PATH.parents


def test_guard_spots_a_changed_file(tmp_path, monkeypatch):
    f = tmp_path / "real.json"
    f.write_text("a")
    monkeypatch.setattr(conftest, "_REAL_DIRS", [tmp_path])
    monkeypatch.setattr(conftest, "_REAL_FILES", [])
    before = conftest._snapshot()
    f.write_text("bb")
    after = conftest._snapshot()
    assert before != after and str(f) in before
