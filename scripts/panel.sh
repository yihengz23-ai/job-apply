#!/bin/bash
# 打开求职投递面板。
# - 面板已经在跑：只打开网页，绝不重启（重启会打断正在干活的助手）。
# - 面板没在跑：在后台启动它（自己一个会话，关掉终端窗口不影响面板），日志在 ~/Library/Logs/jobapply/。
# - 手机访问（Cloudflare 隧道）只有 ~/.jobapply_tunnel_on 存在时才开，默认关。
# 用法：scripts/panel.sh                     正式面板（5001）
#       JOBAPPLY_ENV=test scripts/panel.sh   测试环境（5002，data_test 数据，假助手，不连 Gmail）
#       加 --no-open：只启动，不打开网页
cd "$(dirname "$0")/.." || exit 1
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$HOME/job_apply/.venv/bin/python"
if [ "${JOBAPPLY_ENV:-}" = "test" ]; then URL="http://localhost:5002"; else URL="http://localhost:5001"; fi

"$PY" scripts/panelctl.py start || exit 1
[ "${JOBAPPLY_ENV:-}" = "test" ] || "$PY" scripts/panelctl.py tunnel >/dev/null 2>&1

# /tmp/jobapply_no_open：后台重启时留的记号，这次不再开一个新标签页（页面自己会重连）
if [ "$1" != "--no-open" ] && [ ! -f /tmp/jobapply_no_open ]; then open "$URL"; fi
rm -f /tmp/jobapply_no_open
