"""全局配置：路径、模型、代理。其他模块都从这里取配置。"""

import json
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── 个人设置（candidate_settings.json；公开版用 .example）────────────
_settings_file = BASE_DIR / "candidate_settings.json"
if not _settings_file.exists():
    _settings_file = BASE_DIR / "candidate_settings.example.json"
SETTINGS = json.loads(_settings_file.read_text(encoding="utf-8"))

# ── 材料（桌面「自动投递」文件夹）──────────────────────────────
MATERIALS_DIR = Path(os.environ.get("MATERIALS_DIR", Path.home() / "Desktop" / "自动投递"))
RESUME_PATH = MATERIALS_DIR / SETTINGS["resume_file"]
REPORT_PATH = MATERIALS_DIR / SETTINGS["report_file"]
REPORT_DEFAULT_NAME = SETTINGS["report_default_name"]
REPORT_LABEL = SETTINGS.get("report_label", "研究样本")
RESUME_DEFAULT_ZH = SETTINGS["resume_default_name_zh"]
RESUME_DEFAULT_EN = SETTINGS["resume_default_name_en"]
EXCEL_MIRROR_PATH = MATERIALS_DIR / "投递记录（自动同步，勿手改）.xlsx"

# 简历里必须出现的毕业时间（档案事实），用于防止发出写错毕业时间的旧简历
RESUME_MUST_CONTAIN = SETTINGS.get("resume_must_contain", {})
# 已不在现行简历上的经历 / 写错的毕业时间：邮件里出现就拦截
NOT_ON_RESUME = SETTINGS.get("not_on_resume", [])
WRONG_GRADUATION = SETTINGS.get("wrong_graduation", [])
# 统一写法（如学校译名），发信前自动替换
SPELLING_FIXES = SETTINGS.get("spelling_fixes", {})

# ── 程序数据 ────────────────────────────────────────────────
PROFILE_PATH = BASE_DIR / "candidate_profile.md"
RULES_PATH = BASE_DIR / "email_rules.md"
RECORDS_PATH = BASE_DIR / "records.json"
UPLOADS_DIR = BASE_DIR / "uploads"   # 审核时自己加的附件（文章、作品、成绩单……）
BACKUP_DIR = BASE_DIR / "backups"
GMAIL_STATE_PATH = BASE_DIR / "gmail_state.json"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
PANEL_KEY_PATH = BASE_DIR / ".panel_key"
TUNNEL_URL_PATH = Path("/tmp/tunnel_url.txt")

# ── 身份 ────────────────────────────────────────────────────
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", SETTINGS["sender_email"])
SENDER_NAME = SETTINGS.get("sender_name", SETTINGS["name_zh"])
CANDIDATE_NAME = SETTINGS["name_zh"]
CANDIDATE_NAME_EN = SETTINGS["name_en"]
CURRENT_EMPLOYER_KEYWORDS = [k.lower() for k in SETTINGS.get("current_employer_keywords", [])]

# 本轮投递批次（看板、统计默认只看本轮）；早于 OLD_CAMPAIGN_BEFORE 的旧记录归到 OLD_CAMPAIGN
CURRENT_CAMPAIGN = SETTINGS["current_campaign"]
# 晚上点「发送」：排到第二天这个时间自动发（北京时间，HR 在国内）；night_hours = [几点起算晚上, 几点结束]
SEND_AT = SETTINGS.get("scheduled_send_time", "10:00")
NIGHT_HOURS = SETTINGS.get("night_hours", [21, 7])
OLD_CAMPAIGN = SETTINGS.get("old_campaign", "旧记录")
OLD_CAMPAIGN_BEFORE = SETTINGS.get("old_campaign_before", "1900-01-01")

# ── 模型 ────────────────────────────────────────────────────
# 旧 .env 里的 CLAUDE_MODEL（sonnet-4-6）不再使用；要换模型就在 .env 里写 JOBAPPLY_MODEL=...
CLAUDE_MODEL = os.environ.get("JOBAPPLY_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("JOBAPPLY_EFFORT", "medium")      # JD 分析与写信
CLAUDE_EFFORT_LIGHT = "low"                                      # 识别岗位 / OCR / 邮件分类
# 调用通道：claude_code = 本机 Claude Code 登录（Max 会员额度，默认）；api = .env 里的 API Key（按量扣费）
LLM_BACKEND = os.environ.get("JOBAPPLY_BACKEND", SETTINGS.get("llm_backend", "claude_code"))

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def detect_system_proxy():
    """读取 macOS 系统代理（Clash 等），返回 http://host:port 或 None。"""
    try:
        out = subprocess.check_output(["scutil", "--proxy"], text=True, timeout=5)
    except Exception:
        return None
    settings = {}
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            settings[k.strip()] = v.strip()
    for kind in ("HTTPS", "HTTP"):
        if settings.get(f"{kind}Enable") == "1" and settings.get(f"{kind}Proxy"):
            return f"http://{settings[f'{kind}Proxy']}:{settings.get(f'{kind}Port', '80')}"
    return None


def setup_proxy():
    """进程启动时调用一次：把系统代理写进环境变量，Anthropic / Gmail / requests 都会自动走它。"""
    if os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"):
        return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    proxy = detect_system_proxy()
    if proxy:
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ[key] = proxy
        no_proxy = "localhost,127.0.0.1,::1"
        os.environ.setdefault("NO_PROXY", no_proxy)
        os.environ.setdefault("no_proxy", no_proxy)
    return proxy


PROXY = setup_proxy()
