"""数据备份（4.3）：
- 数据文件（records.json、queue.json、wangshen_tasks.json、wangshen_profile.json 和 meta、wangshen_sites.json）
  每小时滚动一份（留 48 份）、每天一份（留 60 份）；
- 对话（agent_chats，不含 _run）和快照（ws_snapshots）每天打一个包（跟着每天那份）；
- 批量操作、迁移之前调 backup_now(标签) 另备一份（不自动清）。
各模块保存时调 tick()：这个小时 / 今天已经备过就什么都不做（只看一眼目录在不在），很便宜。备份失败不影响保存。"""

import shutil
import tarfile
import threading
from datetime import datetime

from . import config

HOURLY_KEEP = 48
DAILY_KEEP = 60
_lock = threading.Lock()


def _data_files():
    """要备份的数据文件（按各模块当前的路径取，测试里换了路径也跟着走）。"""
    from . import jobqueue, wsprofile, wstasks, agent
    files = [config.RECORDS_PATH, jobqueue.QUEUE_PATH, wstasks.PATH, wsprofile.PATH,
             wsprofile.PATH.with_name("wangshen_profile.meta.json"), agent.SITES_PATH]
    return [f for f in files if f.exists()]


def _copy_files(dst):
    dst.mkdir(parents=True, exist_ok=True)
    for f in _data_files():
        shutil.copy2(f, dst / f.name)


def _pack_chats(dst):
    """对话和快照打成一个包（对话不含 _run 运行目录）。"""
    from . import agent
    out = dst / "chats_snapshots.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        if agent.CHATS_DIR.exists():
            for p in sorted(agent.CHATS_DIR.glob("*.json")):
                tar.add(p, arcname=f"agent_chats/{p.name}")
        snaps = config.DATA_DIR / "ws_snapshots"
        if snaps.exists():
            tar.add(snaps, arcname="ws_snapshots")
    return out


def _prune(folder, keep):
    olds = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.exists() else []
    for p in olds[:-keep]:
        shutil.rmtree(p, ignore_errors=True)


def tick(now=None):
    """保存前调一下：这个小时、今天还没备份就备一份。返回这次新建了哪些备份目录。"""
    now = now or datetime.now()
    made = []
    try:
        with _lock:
            root = config.BACKUP_DIR
            hourly = root / "hourly" / now.strftime("%Y%m%d-%H")
            if not hourly.exists():
                _copy_files(hourly)
                _prune(root / "hourly", HOURLY_KEEP)
                made.append(hourly)
            daily = root / "daily" / now.strftime("%Y%m%d")
            if not daily.exists():
                _copy_files(daily)
                _pack_chats(daily)
                _prune(root / "daily", DAILY_KEEP)
                made.append(daily)
    except Exception:   # 备份出错不能挡住保存
        pass
    return made


def backup_now(label):
    """批量操作、迁移之前另备一份：backups/manual/<时间>-<标签>/（不自动清理）。返回目录。"""
    safe = "".join(c for c in str(label) if c.isalnum() or c in "-_")[:40] or "manual"
    dst = config.BACKUP_DIR / "manual" / f"{datetime.now():%Y%m%d-%H%M%S}-{safe}"
    with _lock:
        _copy_files(dst)
        _pack_chats(dst)
    return dst
