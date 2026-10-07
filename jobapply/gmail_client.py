"""Gmail：授权、组信（附件中文名兼容各家邮箱）、发送 / 存草稿、查回复、同步历史已发送。"""

import base64
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta
from email.header import Header, decode_header, make_header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.exceptions import ProxyError as Urllib3ProxyError

from . import config

API = "https://gmail.googleapis.com/gmail/v1/users/me"
# 发信 / 存草稿走上传接口（整封邮件原样上传，上限 35MB）：普通接口整封超过约 5MB 会 413，带 4MB 研究样本的信发不出去
UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"


class GmailAuthError(Exception):
    pass


class SendUncertain(Exception):
    """请求发出去了、但没拿到结果（超时 / 断线 / Gmail 5xx）：邮件可能已经发出，不能自动重发。"""


class SendFailed(Exception):
    """肯定没发出（连不上 Gmail，或 Gmail 明确拒绝）：可以放心改完再发。"""


class PartialAuthError(GmailAuthError):
    """查回复查到一半授权失效：前面查到的结果在 updates 里，照样保存。"""

    def __init__(self, msg, updates):
        super().__init__(msg)
        self.updates = updates


AUTH_HINT = "Gmail 授权已过期：双击桌面「Gmail重新授权.command」，在弹出的网页里点「允许」即可。"
_token_lock = threading.Lock()


def _write_atomic(path, text, mode=0o600):
    """先写同目录下的唯一临时文件（建的时候就是 600 权限）再换名：几个进程同时写也不会互相踩。"""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_service(interactive=False):
    """读邮件用的客户端（查回复、同步）。发信 / 存草稿不走它，见 _post_once。"""
    return build("gmail", "v1", credentials=_creds(interactive), cache_discovery=False)


def _creds(interactive=False):
    with _token_lock:  # 面板多个请求同时刷新令牌时别把 token.json 写坏
        return _load_creds(interactive)


def _load_creds(interactive):
    creds = None
    if config.TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), config.GMAIL_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_atomic(config.TOKEN_PATH, creds.to_json(), 0o600)
        except RefreshError:
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise GmailAuthError(AUTH_HINT)
        if not config.CREDENTIALS_PATH.exists():
            raise GmailAuthError(f"找不到 {config.CREDENTIALS_PATH}（Google Cloud 的 OAuth 凭证）。")
        flow = InstalledAppFlow.from_client_secrets_file(str(config.CREDENTIALS_PATH), config.GMAIL_SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                      prompt="consent", access_type="offline",
                                      authorization_prompt_message="正在浏览器里打开 Google 授权页面…",
                                      success_message="授权成功，可以关闭这个网页了。")
        _write_atomic(config.TOKEN_PATH, creds.to_json(), 0o600)
    return creds


def _session():
    """发信 / 存草稿用的连接（requests：不会自己重发 POST，走系统代理）。"""
    return AuthorizedSession(_creds())


def auth_status():
    """检查 Gmail 能不能用：走的是和发信同一条路，所以「已连接」= 真能发。"""
    try:
        resp = _session().get(API + "/profile", timeout=(15, 30))
        if resp.status_code == 401:
            return {"ok": False, "error": AUTH_HINT}
        resp.raise_for_status()
        return {"ok": True, "email": resp.json().get("emailAddress")}
    except (GmailAuthError, RefreshError):
        return {"ok": False, "error": AUTH_HINT}
    except Exception as e:
        return {"ok": False, "error": f"Gmail 连接失败：{e}"}


# ── 组信 ───────────────────────────────────────────────────

def _rfc2047(text):
    return Header(text, "utf-8", maxlinelen=10000).encode()


def build_mime(*, to, cc, subject, body, attachments):
    """attachments: [(文件名, bytes)]。附件名同时写在 name / filename 参数里（RFC2047），和 Gmail 网页版发信一致。"""
    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((_rfc2047(config.SENDER_NAME), config.SENDER_EMAIL))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for name, data in attachments:
        part = MIMEApplication(data, _subtype="pdf")
        encoded = _rfc2047(name)
        part.set_param("name", encoded)
        part.add_header("Content-Disposition", "attachment", filename=encoded)
        msg.attach(part)
    return msg


def _never_connected(e):
    """requests 的连接错误里，哪些说明请求根本没到 Gmail（DNS / 连不上 / 代理 / 连接超时）。"""
    reason = getattr(e.args[0], "reason", None) if e.args else None
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError, Urllib3ProxyError, NameResolutionError))


def _post_once(path, mime):
    """发信 / 建草稿：整封邮件（bytes）走上传接口。"""
    return _request_once(UPLOAD + path, params={"uploadType": "media"}, data=mime, headers={"Content-Type": "message/rfc822"})


def _request_once(url, **kw):
    """发信类请求只发一次，绝不重试（googleapiclient 底下的 httplib2 遇到断线会悄悄重发 POST，所以不用它）。
    连不上 / 4xx → SendFailed（肯定没发出）；读超时 / 中途断线 / 5xx → SendUncertain（可能已经发出）。"""
    try:
        resp = _session().post(url, timeout=(20, 180), **kw)
    except RefreshError as e:
        raise GmailAuthError(AUTH_HINT) from e
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ProxyError, requests.exceptions.SSLError) as e:
        raise SendFailed(f"连不上 Gmail（{type(e).__name__}），这封没有发出。请检查网络 / 代理后再发。") from e
    except requests.exceptions.ConnectionError as e:
        if _never_connected(e):
            raise SendFailed(f"连不上 Gmail（{type(e).__name__}），这封没有发出。请检查网络 / 代理后再发。") from e
        raise SendUncertain(f"{type(e).__name__}: {e}") from e
    except requests.exceptions.RequestException as e:  # 读超时、分块传输中断等：请求已经到了 Gmail
        raise SendUncertain(f"{type(e).__name__}: {e}") from e
    if resp.status_code == 401:
        raise GmailAuthError(AUTH_HINT)
    if resp.status_code >= 500:
        raise SendUncertain(f"Gmail 返回 {resp.status_code}")
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.text[:200]
        raise SendFailed(f"Gmail 拒绝了这封（{resp.status_code}）：{msg}")
    return resp.json()


def send(**kw):
    sent = _post_once("/messages/send", build_mime(**kw).as_bytes())
    return {"message_id": sent["id"], "thread_id": sent.get("threadId", "")}


def send_draft(draft_id):
    """把 Gmail 里已经存着的草稿发出去（草稿在 Gmail 里改过的话，发的是改过的）。"""
    sent = _request_once(API + "/drafts/send", json={"id": draft_id})
    return {"message_id": sent["id"], "thread_id": sent.get("threadId", "")}


def create_draft(**kw):
    d = _post_once("/drafts", build_mime(**kw).as_bytes())
    return {"draft_id": d["id"], "message_id": d["message"]["id"], "thread_id": d["message"].get("threadId", "")}


def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def find_sent(to, subject, since):
    """发送结果不明时：去「已发送」里找 since（时间戳）之后、同收件人同标题的那封。找到返回 ids，找不到返回 None。"""
    svc = get_service()
    first = (to or [""])[0]
    q = f"in:sent to:{first} after:{int(since) - 120}"
    res = svc.users().messages().list(userId="me", q=q, maxResults=10).execute()
    for item in res.get("messages", []):
        m = svc.users().messages().get(userId="me", id=item["id"], format="metadata",
                                       metadataHeaders=["Subject"]).execute()
        if _decode(_headers(m).get("subject", "")).strip() == (subject or "").strip():
            return {"message_id": m["id"], "thread_id": m.get("threadId", "")}
    return None


def delete_draft(draft_id):
    get_service().users().drafts().delete(userId="me", id=draft_id).execute()


# ── 查回复 ─────────────────────────────────────────────────

def _headers(msg):
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


# 回信分类。思路：先看「是不是系统自动发的」，再看「是不是明确的邀请」；人写的回信默认算「有回复」。
# 标题里就写着面试 / 笔试 / offer：不管是不是系统发的，都是进展
SUBJECT_INVITE = re.compile(r"面试|笔试|测评|offer|录用|interview|assessment|online test", re.I)
# 正文里「明确在邀请你」的说法（不含「将收到面试通知」这种确认信套话）
BODY_INVITE = re.compile(r"诚邀您|邀请您参加|邀您参加|邀请你参加|安排您|安排你|(?:面试|笔试|测评)(?:时间|链接|地址)[:：]|"
                         r"interview invitation|invite you to|would like to invite|schedule (?:an |your )?interview", re.I)
# 「简历通过筛选者将收到面试通知」这类条件句：是确认信，不是邀请
CONDITIONAL = re.compile(r"(?:如|若|如果|一旦|待)[^。！？\n]{0,15}(?:筛选|评估|审核|通过)|"
                         r"(?:通过|合适|符合)(?:者|的同学|的候选人)[^。！？\n]{0,6}(?:将|会)|"
                         r"(?:筛选|评估|审核|通过)[^。！？\n]{0,4}后[^。！？\n]{0,12}(?:会|将)|"
                         r"将在[^。！？\n]{0,8}内[^。！？\n]{0,6}(?:通知|联系|安排)|"
                         r"we will (?:contact|reach out|be in touch)|we'll be in touch|should we|"
                         r"if (?:you are|your)[^.\n]{0,40}(?:selected|shortlisted|suitable)", re.I)
# 标题里的系统确认 / 自动回复（正文里的「感谢您的投递」不算：人写的回信也常这么开头）
SUBJECT_AUTO = re.compile(r"自动回复|auto[- ]?reply|automatic reply|out of office|投递成功|申请成功|已收到您?的?(?:申请|简历|投递)|"
                          r"感谢您?的?(?:投递|申请)|application (?:has been )?received|thank you for (?:your )?appl", re.I)
# 正文里只认明确的系统用语
BODY_AUTO = re.compile(r"系统自动发送|请勿(?:直接)?回复|此邮件由系统|do not reply|this is an automated|automatically generated", re.I)
NOREPLY = re.compile(r"no-?reply|do-?not-?reply|mailer@|notifications?@", re.I)
# 按对方域名找到的「新邮件」必须像是招聘相关的，免得把订阅、通知当成回复
JOB_WORDS = re.compile(r"面试|笔试|测评|简历|投递|申请|应聘|实习|岗位|职位|招聘|邀请|offer|录用|interview|application|"
                       r"intern|position|candidate", re.I)
# 按机构名在整个收件箱里搜时更严：只认和「我的申请」直接相关的词
APPLICATION_WORDS = re.compile(r"面试|笔试|测评|简历|投递|申请|应聘|offer|录用|interview|assessment|application", re.I)
# 招聘网站的职位推送，不是机构的来信
JOB_BOARDS = ("zhipin.com", "shixiseng.com", "liepin.com", "lagou.com", "linkedin.com", "yingjiesheng.com",
              "51job.com", "zhaopin.com", "kanzhun.com", "maimai.cn")
REPLY_RANK = {"有回复": 4, "退信": 3, "来信": 2, "自动回复": 1}


def is_invite(subject, snippet=""):
    """标题写着面试 / 笔试 / offer，或正文明确邀请（排除「通过者将收到面试通知」这类条件句）。"""
    return bool(SUBJECT_INVITE.search(subject)) or (bool(BODY_INVITE.search(snippet)) and not CONDITIONAL.search(snippet))


def _is_auto(h, snippet=""):
    return (h.get("auto-submitted", "no").lower() not in ("", "no") or "x-autoreply" in h or "x-autorespond" in h
            or bool(NOREPLY.search(h.get("from", ""))) or bool(SUBJECT_AUTO.search(h.get("subject", "")))
            or bool(BODY_AUTO.search(snippet)))


def _reply_kind(h, snippet=""):
    """退信（没送达）/ 自动回复 / 有回复"""
    sender, subject = h.get("from", "").lower(), h.get("subject", "")
    if re.search(r"mailer-daemon|postmaster|mail delivery", sender) or \
            re.search(r"Delivery Status Notification|Undeliverable|Undelivered|退信|无法投递|无法递送|未送达", subject + " " + snippet, re.I):
        return "退信"
    if is_invite(subject, snippet):
        return "有回复"
    if _is_auto(h, snippet):
        return "自动回复"
    return "有回复"  # 人写的回复（包括拒信：看板里可以自己把状态改成「拒绝」）


def _ts(msg):
    return datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000)


def check_replies(records, progress=None):
    """检查每条记录：①草稿是否已从 Gmail 发出；②同一线程里是否有对方回信；③对方是否另起新邮件联系；
    ④网申（没发邮件）的：收件箱里有没有提到这家机构的招聘来信。
    返回 {record_id: 要更新的字段}。单条出错（网络抖动等）跳过，连续出错太多就停下报错。"""
    svc = get_service()
    progress = progress or (lambda msg: None)
    updates, fails = {}, 0
    targets = [r for r in records if (r.get("send_mode") in ("发送", "草稿") and r.get("to_email"))
               or (r.get("send_mode") == "未发邮件" and r.get("company_name"))]
    for n, r in enumerate(targets, 1):
        if n % 10 == 0:
            progress(f"已检查 {n}/{len(targets)}")
        name = r.get("company_name") or r.get("id")
        try:
            updates[r["id"]] = _check_one(svc, r, progress)
            fails = 0
        except RefreshError as e:  # 查到一半授权失效：前面查到的照样保存
            raise PartialAuthError(AUTH_HINT, updates) from e
        except HttpError as e:
            if e.resp.status == 404:  # 草稿 / 邮件在 Gmail 里被删了：不算网络问题
                updates[r["id"]] = {"gmail_thread_id": "", "reply_checked_at": _now()}
                progress(f"{name}：Gmail 里找不到这封了（可能已删除），跳过")
                fails = 0
                continue
            fails += 1
            progress(f"跳过 {name}：Gmail 返回 {e.resp.status}")
        except Exception as e:  # 超时 / 断网
            fails += 1
            progress(f"跳过 {name}：{type(e).__name__}")
        if fails >= 5:
            progress(f"连续 {fails} 条查询失败，已停下（可能是网络或代理有问题）；前面查到的结果照常保存。")
            break
    return updates


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _kind_of(reply, by_company=False):
    m, h = reply
    kind = _reply_kind(h, m.get("snippet") or "")
    # 按机构名搜到的信：只有面试 / 笔试这类明确信号才算「有回复」，其余记成「来信」请本人看一眼
    if by_company and kind == "有回复" and not is_invite(h.get("subject", ""), m.get("snippet") or ""):
        kind = "来信"
    return kind


def _best(cands, by_company=False):
    """几封来信里挑最有意义的：有回复 > 退信 > 来信 > 自动回复，同级取最新的。"""
    cands = [c for c in cands if c]
    if not cands:
        return None
    return max(cands, key=lambda c: (REPLY_RANK[_kind_of(c, by_company)], _ts(c[0])))


def sent_time(r):
    """投递时间：新记录用绝对时间戳 sent_ts（换时区也准），旧记录按本机时间解析 sent_at。"""
    ts = r.get("sent_ts")
    return datetime.fromtimestamp(ts) if isinstance(ts, (int, float)) and ts > 0 else _parse(r.get("sent_at"))


def _check_one(svc, r, progress=None):
    by_company = r.get("send_mode") == "未发邮件"
    if by_company:
        upd, reply = {}, _find_company_mail(svc, r, sent_time(r), progress)
    else:
        upd, reply = _check_thread(svc, r)
    if reply:
        m, h = reply
        upd.update(reply_status=_kind_of(reply, by_company), reply_at=_ts(m).strftime("%Y-%m-%d %H:%M"),
                   reply_from=parseaddr(h.get("from", ""))[1] or h.get("from", ""),
                   reply_snippet=(m.get("snippet") or "")[:160])
    upd["reply_checked_at"] = _now()
    return upd


def _check_thread(svc, r):
    """邮件投递：原线程里的全部来信（顺便发现草稿已经从 Gmail 发出），只有自动回复 / 没有来信时再找对方另发的新邮件。"""
    me = config.SENDER_EMAIL.lower()
    upd, inbound, sent_out = {}, [], r.get("send_mode") != "草稿"
    tid = r.get("gmail_thread_id") or _find_thread(svc, r)
    if tid and not r.get("gmail_thread_id"):
        upd["gmail_thread_id"] = tid
    if tid:
        th = svc.users().threads().get(userId="me", id=tid, format="metadata",
                                       metadataHeaders=["From", "Subject", "Date", "Auto-Submitted",
                                                        "X-Autoreply", "X-Autorespond"]).execute()
        for m in th.get("messages", []):
            labels, h = m.get("labelIds", []), _headers(m)
            if "SENT" in labels:
                if r.get("send_mode") == "草稿" and "DRAFT" not in labels:  # 草稿已从 Gmail 里发出
                    sent_out = True
                    upd.update(send_mode="发送", sent_at=_ts(m).strftime("%Y-%m-%d %H:%M"),
                               sent_ts=int(m.get("internalDate", "0")) / 1000)
                    if r.get("status") in ("草稿", "", None):  # 手动改过的状态（如面试中）不动
                        upd["status"] = "已投递"
                continue
            if "DRAFT" in labels or me in parseaddr(h.get("from", ""))[1].lower():
                continue
            inbound.append((m, h))
    best = _best(inbound)
    if not sent_out:  # 草稿还没发出去：别把这个机构别的来信记到它头上
        return upd, best
    if not best or _kind_of(best) == "自动回复":
        best = _best([best, _find_new_mail(svc, r, sent_time({**r, **upd}))])
    return upd, best


def _parse(s):
    try:
        return datetime.strptime((s or "")[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _first_email(value):
    m = re.search(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", value or "")
    return m.group(0).lower() if m else ""


def _find_thread(svc, r):
    """旧记录没存线程 ID：按收件人 + 发送日期在已发送里找回来。"""
    to = _first_email(r.get("to_email"))
    sent = sent_time(r)
    if not to or not sent:
        return ""
    q = f"in:sent to:{to} after:{(sent - timedelta(days=1)):%Y/%m/%d} before:{(sent + timedelta(days=2)):%Y/%m/%d}"
    res = svc.users().messages().list(userId="me", q=q, maxResults=5).execute()
    msgs = res.get("messages", [])
    return msgs[0]["threadId"] if msgs else ""


def _find_new_mail(svc, r, sent_at):
    """对方没在原线程回复、而是另发邮件（常见于约面试）：按对方邮箱 / 机构域名在收件箱里找。"""
    from .checks import PUBLIC_DOMAINS
    to = _first_email(r.get("to_email"))
    if not to or not sent_at:
        return None
    domain = to.split("@")[1]
    exact = domain in PUBLIC_DOMAINS
    # Gmail 的 after:日期 按美国太平洋时间算，会漏掉北京时间当天下午之前的信；用秒级时间戳最准
    q = f"{'from:' + to if exact else 'from:' + domain} -in:sent -in:drafts after:{int(sent_at.timestamp())}"
    return _best(_matches(svc, q, sent_at, None if exact else JOB_WORDS))


def _matches(svc, q, since, words, limit=5):
    """搜到的信里，since 之后、（要求关键词时）像招聘相关的那些。"""
    res = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
    out = []
    for item in res.get("messages", []):
        m = svc.users().messages().get(userId="me", id=item["id"], format="metadata",
                                       metadataHeaders=["From", "Subject", "Auto-Submitted",
                                                        "X-Autoreply", "X-Autorespond"]).execute()
        h = _headers(m)
        if _ts(m) < since:
            continue
        if words is not None and not words.search(h.get("subject", "") + " " + (m.get("snippet") or "")):
            continue
        out.append((m, h))
    return out


def _company_terms(name):
    """在收件箱里搜这家机构用的词 → (全文搜的词, 只在发件人里搜的短名字)。
    「红杉中国（HongShan）」→ ['红杉中国', 'HongShan']；括号里的中文（多是城市、「有限合伙」）不要；
    太短的名字（腾讯、IDG）全文搜会搜到别家的信，只在发件人里找。"""
    from .records import norm_company
    parts = [p.strip() for p in re.split(r"[（()）]", name or "") if p.strip()]
    if not parts:
        return [], []
    main = parts[0]
    cands = [main, norm_company(main) if re.search(r"[\u4e00-\u9fff]", main) else ""]
    cands += [p for p in parts[1:] if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&'-]{3,}", p)]
    full, short = [], []
    for t in cands:
        if not t:
            continue
        cjk = re.search(r"[\u4e00-\u9fff]", t)
        bucket = full if len(t) >= (3 if cjk else 4) else short if len(t) >= 2 else None
        if bucket is not None and t not in full + short:
            bucket.append(t)
    return full[:3], short[:2]


def _find_company_mail(svc, r, sent_at, progress=None):
    """网申岗位没有邮件线程：在收件箱里找投递之后、提到这家机构、而且和「我的申请」直接相关的来信。"""
    full, short = _company_terms(r.get("company_name"))
    if not (full or short) or not sent_at:
        if progress and not (full or short):
            progress(f"{r.get('company_name') or r.get('id')}：机构名太短或缺失，没法按名字查来信")
        return None
    names = " OR ".join([f'"{t}"' for t in full] + [f'from:"{t}"' for t in short])
    boards = " ".join(f"-from:{d}" for d in JOB_BOARDS)
    since = sent_at - timedelta(hours=1)
    q = f"({names}) -in:sent -in:drafts -in:spam {boards} after:{int(since.timestamp())}"
    return _best(_matches(svc, q, since, APPLICATION_WORDS), by_company=True)


# ── 同步：Gmail 里发过、但没进记录的投递（例如直接在 Gmail 里发的）──────

def _extract_text(payload):
    def dec(data):
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    if "parts" not in payload:
        data = payload.get("body", {}).get("data", "")
        return dec(data) if data else ""
    for mime in ("text/plain", "text/html"):
        for part in payload["parts"]:
            if part.get("mimeType") == mime and part.get("body", {}).get("data"):
                text = dec(part["body"]["data"])
                return re.sub(r"<[^>]+>", " ", text) if mime == "text/html" else text
    for part in payload["parts"]:
        if "parts" in part:
            text = _extract_text(part)
            if text:
                return text
    return ""


def _load_state():
    try:
        return json.loads(config.GMAIL_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_ids": []}


def _save_state(state):
    _write_atomic(config.GMAIL_STATE_PATH, json.dumps(state, ensure_ascii=False))


def find_unrecorded_sent(records, since="2026/01/01", progress=None):
    """返回 Gmail 已发送里带 PDF、但不在记录里的邮件（还没判断是不是投递）。"""
    svc = get_service()
    known_msgs = {r.get("gmail_message_id") for r in records if r.get("gmail_message_id")}
    known_threads = {r.get("gmail_thread_id") for r in records if r.get("gmail_thread_id")}
    known_pairs = {(_first_email(r.get("to_email")), (r.get("sent_at") or "")[:10]) for r in records}
    state = _load_state()
    processed = set(state.get("processed_ids", []))
    ids, token = [], None
    while True:
        res = svc.users().messages().list(userId="me", q=f"in:sent has:attachment filename:pdf after:{since}",
                                          maxResults=100, pageToken=token).execute()
        ids += res.get("messages", [])
        token = res.get("nextPageToken")
        if not token:
            break
    if progress:
        progress(f"Gmail 已发送里共有 {len(ids)} 封带 PDF 的邮件")
    out = []
    for item in ids:
        if item["id"] in known_msgs or item["id"] in processed or item.get("threadId") in known_threads:
            continue
        m = svc.users().messages().get(userId="me", id=item["id"], format="full").execute()
        h = _headers(m)
        to = h.get("to", "")
        sent = _ts(m)
        if (_first_email(to), sent.strftime("%Y-%m-%d")) in known_pairs:
            continue
        out.append({"id": item["id"], "thread_id": item.get("threadId", ""), "to": to,
                    "cc": h.get("cc", ""), "subject": h.get("subject", ""),
                    "sent_at": sent.strftime("%Y-%m-%d %H:%M"), "sent_ts": int(m.get("internalDate", "0")) / 1000,
                    "body": _extract_text(m.get("payload", {}))})
    return out


def mark_processed(message_ids):
    state = _load_state()
    ids = set(state.get("processed_ids", [])) | set(message_ids)
    state["processed_ids"] = sorted(ids)
    _save_state(state)
