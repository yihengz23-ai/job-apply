"""所有 Claude 调用都在这里：JD 分析写信、多岗位识别、图片 OCR、已发送邮件分类。

两个通道（config.LLM_BACKEND）：
  claude_code —— 调本机已登录的 Claude Code（claude -p），用 Claude Max 会员额度，不扣 API 余额（默认）
  api         —— 用 .env 里的 ANTHROPIC_API_KEY，按量从 API 账号扣费
会员通道失败（没登录 / 额度用完 / 超时）时，若有 API Key 会自动改走 API，并在结果里注明。"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import anthropic

from . import config

COMPANY_TYPES = [
    "美元VC", "人民币VC", "双币VC/PE", "PE/并购基金", "国资/政府引导基金",
    "产业资本/CVC/战投", "券商/投行/FA", "险资/银行系/资管", "二级/对冲",
    "咨询/研究", "企业/大厂", "其他",
]
POSITION_TYPES = ["全职", "留用实习", "实习", "不明确"]
RESUME_VERSIONS = ["中文", "双语", "英文", "中英两份"]
REPORT_HINTS = ["附上", "不附"]   # 本人指定附不附研究样本（不指定 = 按 JD 判断）


def _obj(props, order=None):
    """结构化输出要求：每个对象都要 additionalProperties=false 且字段全部 required。"""
    return {"type": "object", "properties": props,
            "required": list(order or props.keys()), "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}

# 字段顺序有意义：先提取 JD 信息，最后才写邮件
ANALYSIS_SCHEMA = _obj({
    "is_jd": {"type": "boolean"},
    "not_jd_reason": _STR,
    "company_name": _STR,
    "company_type": {"type": "string", "enum": COMPANY_TYPES},
    "job_title": _STR,
    "position_type": {"type": "string", "enum": POSITION_TYPES},
    "job_location": _STR,
    "focus_industry": _STR,
    "job_post_date": _STR,
    "deadline": _STR,
    "source_name": _STR,
    "jd_language": {"type": "string", "enum": ["中文", "英文"]},
    "apply_channel": {"type": "string", "enum": ["邮箱", "网申/链接", "邮箱+网申", "不明确"]},
    "to_emails": _STRS,
    "cc_emails": _STRS,
    "apply_url": _STR,
    "contact_in_jd": _STR,
    "jd_rules": _obj({
        "subject_format": _STR,
        "resume_filename_format": _STR,
        "report_filename_format": _STR,
        "body_requirements": _STRS,
        "requested_materials": _STRS,
    }),
    "fit_warnings": _STRS,
    "missing_info": _STRS,
    "resume_version": {"type": "string", "enum": RESUME_VERSIONS},
    "resume_filename": _STR,
    "resume_filename_en": _STR,
    "attach_report": {"type": "boolean"},
    "report_filename": _STR,
    "email_subject": _STR,
    "email_body": _STR,
})

JOBS_SCHEMA = _obj({
    "jobs": {"type": "array", "items": _obj({
        "title": _STR,
        "location": _STR,
        "emails": _STRS,
        "summary": _STR,
    })},
})

PLATFORMS = ["飞书", "Moka", "北森", "牛客", "Boss直聘", "官网", "问卷/表单", "微信小程序", "扫码", "邮箱", "其他", "不明确"]
WANGSHEN_SCHEMA = _obj({
    "platform": {"type": "string", "enum": PLATFORMS},
    "apply_steps": _STRS,
    "self_intro_short": _STR,
    "self_intro": _STR,
    "why_this_role": _STR,
    "fit_points": _STR,
    "custom_answers": {"type": "array", "items": _obj({"question": _STR, "answer": _STR})},
    "notes": _STRS,
})

SENT_SCHEMA = _obj({
    "items": {"type": "array", "items": _obj({
        "index": {"type": "integer"},
        "is_application": {"type": "boolean"},
        "company_name": _STR,
        "company_type": {"type": "string", "enum": COMPANY_TYPES},
        "job_title": _STR,
        "job_location": _STR,
        "position_type": {"type": "string", "enum": POSITION_TYPES},
    })},
})

# 美元 / 百万 token（用于在界面上显示大概花费）
_PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_write": 5.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_write": 2.5, "cache_read": 0.20},
}

WEEKDAYS = "一二三四五六日"


class LLMError(Exception):
    """给用户看的中文错误。"""


_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3, timeout=300)
    return _client


def _friendly(e):
    proxy = config.PROXY or "未检测到系统代理"
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic API Key 无效或已失效，请在 job_apply/.env 里更新 ANTHROPIC_API_KEY。"
    if isinstance(e, anthropic.PermissionDeniedError):
        return f"API Key 没有权限调用 {config.CLAUDE_MODEL}：{getattr(e, 'message', e)}"
    if isinstance(e, anthropic.RateLimitError):
        return "请求太频繁，被限流了，过一分钟再试。"
    if isinstance(e, anthropic.BadRequestError):
        msg = str(getattr(e, "message", e))
        if "credit balance" in msg.lower():
            return "Anthropic API 余额不足，请到 console.anthropic.com 充值后再试。"
        return f"请求参数有误：{msg}"
    if isinstance(e, anthropic.APIConnectionError):
        return f"连不上 Anthropic API（代理：{proxy}）。请确认代理软件开着、能访问外网。"
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code >= 500:
            return f"Anthropic 服务暂时繁忙（{e.status_code}），稍后重试。"
        return f"API 错误（{e.status_code}）：{getattr(e, 'message', e)}"
    return f"{type(e).__name__}: {e}"


def _cost(usage, model):
    p = _PRICES.get(model)
    if not p or usage is None:
        return None
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    return round((usage.input_tokens * p["in"] + usage.output_tokens * p["out"]
                  + cw * p["cache_write"] + cr * p["cache_read"]) / 1e6, 4)


class BackendUnavailable(Exception):
    """会员通道（Claude Code）这次用不了。"""


def _claude_bin():
    return shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")


def _image_blocks(images):
    return [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                         "data": base64.standard_b64encode(raw).decode()}}
            for raw, mt in images or []]


def _call_cc(*, system, content, schema, effort, images=None, **_):
    """Claude Code 无界面模式：用本机 claude.ai 登录（Max 会员），结构化输出。
    一律禁用全部工具：--tools "" 关掉内置工具，--strict-mcp-config 不加载任何 MCP（包括 claude.ai 账号里的
    Gmail / Drive 等连接器）。图片直接放进消息里（stream-json 输入），模型碰不到本机文件、命令和你的账号。"""
    t0 = time.time()
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    msg = {"type": "user", "message": {"role": "user",
                                       "content": _image_blocks(images) + [{"type": "text", "text": content}]}}
    with tempfile.TemporaryDirectory(prefix="jobapply-cc-") as tmp:
        sp = Path(tmp) / "system.md"
        sp.write_text(system, encoding="utf-8")
        args = [_claude_bin(), "-p", "--model", config.CLAUDE_MODEL, "--effort", effort,
                "--system-prompt-file", str(sp), "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence", "--setting-sources", "", "--tools", "", "--strict-mcp-config"]
        try:
            proc = subprocess.run(args, input=json.dumps(msg, ensure_ascii=False) + "\n", capture_output=True,
                                  text=True, cwd=tmp, env=env, timeout=300)
        except FileNotFoundError as e:
            raise BackendUnavailable("本机没找到 Claude Code") from e
        except subprocess.TimeoutExpired as e:
            raise BackendUnavailable("Claude Code 超过 5 分钟没返回") from e
    out = None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            out = ev
    if out is None:
        raise BackendUnavailable(f"Claude Code 没有正常返回：{(proc.stderr or proc.stdout).strip()[-200:]}")
    if out.get("is_error") or out.get("subtype") != "success" or out.get("structured_output") is None:
        raise BackendUnavailable(f"Claude Code 出错：{str(out.get('result') or out.get('subtype'))[:200]}")
    usage = out.get("usage") or {}
    meta = {
        "model": config.CLAUDE_MODEL, "backend": "会员额度", "seconds": round(time.time() - t0, 1),
        "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_input_tokens", 0), "cost_usd": None,
    }
    return out["structured_output"], meta


def _call(**kw):
    """按 config.LLM_BACKEND 选通道；会员通道失败且有 API Key 时自动改走 API。返回 (dict, meta)。"""
    if config.LLM_BACKEND == "claude_code":
        try:
            return _call_cc(**kw)
        except BackendUnavailable as e:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMError(f"会员通道用不了（{e}），也没配置 API Key。") from e
            data, meta = _call_api(**kw)
            meta["backend"] = f"API（会员通道失败：{e}）"
            return data, meta
    return _call_api(**kw)


def _call_api(*, system, content, schema, effort, max_tokens=16000, images=None):
    """API 通道：结构化 JSON 输出 + 系统提示缓存 + 拒答时自动换模型兜底。"""
    t0 = time.time()
    if images:
        content = _image_blocks(images) + [{"type": "text", "text": content}]
    try:
        resp = client().beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        raise LLMError(_friendly(e)) from e

    if resp.stop_reason == "refusal":
        raise LLMError("模型拒绝处理这段内容（安全策略）。请检查粘贴的内容是不是 JD。")
    if resp.stop_reason == "max_tokens":
        raise LLMError("模型输出被截断（内容太长）。请只粘贴单个岗位的 JD 再试。")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"模型返回的不是合法 JSON：{text[:200]}") from e
    meta = {
        "model": resp.model,
        "backend": "API",
        "seconds": round(time.time() - t0, 1),
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "cost_usd": _cost(resp.usage, resp.model),
    }
    return data, meta


# ── JD 分析 + 写信 ──────────────────────────────────────────

_system_cache = {"key": None, "text": None}


def build_system_prompt():
    """候选人档案 + 规则，按文件修改时间缓存（改了 md 文件自动生效，无需重启）。"""
    key = (config.PROFILE_PATH.stat().st_mtime, config.RULES_PATH.stat().st_mtime)
    if _system_cache["key"] != key:
        profile = config.PROFILE_PATH.read_text(encoding="utf-8")
        rules = config.RULES_PATH.read_text(encoding="utf-8")
        _system_cache["text"] = (
            f"你是{config.CANDIDATE_NAME}的求职投递助手。下面先给候选人档案（事实），再给投递规则。"
            "输出必须是符合 schema 的 JSON。\n\n"
            f"<候选人档案>\n{profile}\n</候选人档案>\n\n"
            f"<投递规则>\n{rules}\n</投递规则>"
        )
        _system_cache["key"] = key
    return _system_cache["text"]


def today_line(now=None):
    now = now or datetime.now()
    return f"今天是 {now:%Y-%m-%d}（星期{WEEKDAYS[now.weekday()]}）。"


def analyze_jd(jd_text, *, target_job="", position_hint="", resume_hint="", report_hint="", extra="", now=None):
    """分析 JD 并生成投递邮件。返回 (result_dict, meta)。
    招聘信息来源不告诉 AI：邮件里一律不写来源，来源只进本人的投递记录。"""
    head = [today_line(now)]
    if target_job:
        head.append(f"目标岗位：{target_job}（文章里有多个岗位，只针对这个岗位）")
    if position_hint:
        head.append(f"本人指定：岗位类型按「{position_hint}」处理。")
    if resume_hint:
        head.append(f"本人指定：简历版本用「{resume_hint}」。")
    if report_hint == "附上":
        head.append("本人指定：这次附上研究样本（attach_report 填 true，正文按「研究样本」的规则提一句）。")
    elif report_hint == "不附":
        head.append("本人指定：这次不附研究样本（attach_report 填 false，正文不要提研究样本）。")
    if extra:
        head.append(f"本人补充要求（优先满足，但不能违反候选人档案里的事实）：{extra}")
    content = "\n".join(head) + "\n\n【JD 原文】\n" + jd_text.strip()
    data, meta = _call(system=build_system_prompt(), content=content,
                       schema=ANALYSIS_SCHEMA, effort=config.CLAUDE_EFFORT)
    return data, meta


REVIEW_SCHEMA = _obj({
    "changes": {"type": "array", "items": _obj({"problem": _STR, "before": _STR, "after": _STR})},
    "email_subject": _STR,
    "email_body": _STR,
})


def self_review(jd_text, result, *, attachments, notes="", now=None):
    """写完自查：让 AI 当审稿人，对着档案和 JD 逐条挑错（投递规则第 12 节），只改有问题的地方。返回 (dict, meta)。"""
    head = [today_line(now), "下面是已经写好的一封投递邮件，请按投递规则第 12 节「写完自查」检查。",
            "附件：" + ("、".join(attachments) if attachments else "无")]
    if notes:
        head.append(f"本人这次的补充要求（写信时已经照做，自查时别改掉）：{notes}")
    content = ("\n".join(head) + f"\n\n【邮件标题】{result.get('email_subject', '')}\n【邮件正文】\n{result.get('email_body', '')}"
               + "\n\n【JD 原文】\n" + (jd_text or "").strip())
    return _call(system=build_system_prompt(), content=content, schema=REVIEW_SCHEMA, effort=config.CLAUDE_EFFORT)


def wangshen_kit(jd_text, *, result=None, source_label="", target_job="", now=None):
    """网申岗位：生成投递步骤 + 填表要用的自我介绍 / 为什么申请 / 匹配点 / JD 里明确列出的问题的回答。"""
    r = result or {}
    head = [today_line(now), "这个岗位需要网申（在招聘网站、招聘系统、问卷表单或小程序里填表投递）。"
            "请按投递规则第 11 节生成网申资料包。"]
    if source_label:
        head.append(f"招聘信息来源：{source_label}")
    if target_job:
        head.append(f"目标岗位：{target_job}（文章里有多个岗位，只针对这个岗位）")
    if r:
        head.append(f"已识别：机构「{r.get('company_name', '')}」，岗位「{r.get('job_title', '')}」，"
                    f"类型「{r.get('position_type', '')}」，网申链接「{r.get('apply_url', '') or '无'}」，"
                    f"截止「{r.get('deadline', '') or '未写'}」。")
    content = "\n".join(head) + "\n\n【JD 原文】\n" + jd_text.strip()
    return _call(system=build_system_prompt(), content=content, schema=WANGSHEN_SCHEMA, effort=config.CLAUDE_EFFORT)


# ── 多岗位识别（只列岗位，不改写 JD 原文）────────────────────

def detect_jobs(article_text):
    system = ("你从招聘文章中识别所有不同的岗位。只列出岗位，不要改写原文。"
              "emails 只填文中明确写出的投递邮箱；summary 用一句话概括岗位方向和要求（30字内）。"
              "文章只有一个岗位也返回一个元素；不是招聘文章则返回空数组。")
    data, _ = _call(system=system, content=article_text, schema=JOBS_SCHEMA,
                    effort=config.CLAUDE_EFFORT_LIGHT, max_tokens=8000)
    return data.get("jobs", [])


# ── 图片 OCR（公众号把 JD 做成图片时）──────────────────────────

def ocr_images(images):
    """images: [(bytes, media_type)]，一次请求读完，返回按顺序拼好的招聘文字。"""
    if not images:
        return ""
    prompt = ("这些图片按顺序来自一篇公众号招聘文章。请把图片中的招聘相关文字（公司介绍、岗位名称、职责、要求、"
              "投递邮箱、邮件标题格式、截止时间等）按原文顺序完整转写，不要总结、不要省略。"
              "广告、二维码、公众号介绍等非招聘内容跳过。若没有任何招聘信息，text 输出空字符串。")
    schema = _obj({"text": _STR})
    data, _ = _call(system="你是精确的中文 OCR 转写助手，只输出 JSON。", content=prompt, images=images,
                    schema=schema, effort=config.CLAUDE_EFFORT_LIGHT, max_tokens=12000)
    return data.get("text", "").strip()


EMAIL_PICK_SCHEMA = _obj({"email": _STR, "sure": {"type": "boolean"}})


def read_email_from_images(images, candidates):
    """两种识别方法对图片里的投递邮箱认得不一样：盯着图片逐个字符再认一次。返回 {"email", "sure"}。"""
    prompt = ("这些图片来自一篇招聘文章。两种文字识别对图片里的投递邮箱认得不一样，候选：" + "、".join(candidates) +
              "。请只看图片，逐个字符核对（特别注意 rn 和 m、l 和 1 和 I、0 和 o、- 和 _ 和 .），"
              "写出图片里实际印着的那个邮箱地址。候选都不对就写你看到的；看不清就 sure 填 false。")
    data, _ = _call(system="你是逐字核对邮箱地址的助手，只输出 JSON。", content=prompt, images=images,
                    schema=EMAIL_PICK_SCHEMA, effort=config.CLAUDE_EFFORT_LIGHT, max_tokens=600)
    return data


# ── Gmail 已发送邮件分类（同步历史投递用）──────────────────────

def classify_sent_emails(emails):
    """emails: [{'to','subject','body'}]，返回同长度的分类结果列表。"""
    lines = []
    for i, e in enumerate(emails):
        lines.append(f"--- 邮件 {i} ---\n收件人：{e['to']}\n标题：{e['subject']}\n正文：{e['body'][:800]}")
    system = ("判断每封已发送邮件是不是求职投递邮件（发简历申请岗位）。"
              "是的话提取机构名、机构类型、岗位、地点、岗位类型；不是就 is_application=false，其他字段给空值。"
              "index 对应邮件编号。")
    data, _ = _call(system=system, content="\n\n".join(lines), schema=SENT_SCHEMA,
                    effort=config.CLAUDE_EFFORT_LIGHT, max_tokens=8000)
    by_index = {it["index"]: it for it in data.get("items", [])}
    return [by_index.get(i, {"is_application": False}) for i in range(len(emails))]
