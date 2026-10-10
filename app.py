#!/usr/bin/env python3
"""求职投递面板（Flask）。启动：双击桌面「投递面板.command」，或 .venv/bin/python app.py
接口按功能放在 jobapply/web/ 的蓝图里（清单见 jobapply/web/__init__.py）；这里只建 app、注册蓝图、启动。"""

import threading

from flask import Flask

from jobapply import agent, config, jobqueue, learn, records
from jobapply.web import agent_routes, apps_routes, board_routes, common, mail_routes, profile_routes, ws_routes
# 测试和别处还从 app 模块上取这些名字：和蓝图里用的是同一个对象
from jobapply.web.board_routes import _gmail_job_lock  # noqa: F401
from jobapply.web.common import LOCAL_HOSTS, PANEL_KEY, STARTED_AT, TEST_ENV_BANNER, VERSION  # noqa: F401
from jobapply.web.ws_routes import _continue_message, _fill_message  # noqa: F401

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = mail_routes.MAX_ATTACHMENT + 1024 * 1024
app.json.ensure_ascii = False

# common 的拦截、响应头、出错处理挂在整个 app 上（不只它自己的蓝图）
for _bp in (common.bp, profile_routes.bp, mail_routes.bp, ws_routes.bp, agent_routes.bp, board_routes.bp, apps_routes.bp):
    app.register_blueprint(_bp)


if __name__ == "__main__":
    try:
        records.migrate()
    except records.RecordsCorrupt as e:  # 面板照样打开（能看到提示），只是写记录会被拦下
        print(f"⚠️  {e}")
    resumed = jobqueue.resume_pending()
    if resumed:
        print(f"批量队列：接着处理上次没做完的 {resumed} 条")
    if not config.IS_TEST_ENV:   # 测试环境不发信、不碰 Gmail 草稿
        jobqueue.start_scheduler()   # 定时发送：到点由面板自己发
    agent.recover()              # 上次没做完就关了面板的助手对话：标成已停止
    agent.tidy_chats()           # 一家网申只留一个对话：多出来的旧对话收起来
    if not config.IS_TEST_ENV:
        threading.Thread(target=jobqueue.startup_tasks, daemon=True).start()   # 定时草稿对齐 + 旧条目按新规则重查
        threading.Thread(target=learn.backfill, daemon=True).start()         # 交过、读回过、还没学过的几家：补学进底稿
    env = f"【测试环境】数据 {config.DATA_DIR}  " if config.IS_TEST_ENV else ""
    print(f"投递面板：{config.PANEL_BASE}   {env}模型：{config.CLAUDE_MODEL}   代理：{config.PROXY or '无'}", flush=True)
    app.run(host="127.0.0.1", port=config.PORT, debug=False, threaded=True)
