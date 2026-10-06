"""Gmail：授权、组信（附件中文名兼容各家邮箱）、发送 / 存草稿、查回复、同步历史已发送。"""

import base64
import json
import re
from datetime import datetime, timedelta
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config


class GmailAuthError(Exception):
    pass


AUTH_HINT = "Gmail 授权已过期：双击桌面「Gmail重新授权.command」，在弹出的网页里点「允许」即可。"


def get_service(interactive=False):
    creds = None
    if config.TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), config.GMAIL_SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            config.TOKEN_PATH.write_text(creds.to_json())
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
        config.TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def auth_status():
    try:
        svc = get_service()
        prof = svc.users().getProfile(userId="me").execute()
        return {"ok": True, "email": prof.get("emailAddress")}
    except GmailAuthError as e:
        return {"ok": False, "error": str(e)}
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


def _raw(msg):
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def send(**kw):
    svc = get_service()
    sent = svc.users().messages().send(userId="me", body={"raw": _raw(build_mime(**kw))}).execute()
    return {"message_id": sent["id"], "thread_id": sent.get("threadId", "")}


def create_draft(**kw):
    svc = get_service()
    d = svc.users().drafts().create(userId="me", body={"message": {"raw": _raw(build_mime(**kw))}}).execute()
    return {"draft_id": d["id"], "message_id": d["message"]["id"], "thread_id": d["message"].get("threadId", "")}


def delete_draft(draft_id):
    get_service().users().drafts().delete(userId="me", id=draft_id).execute()


# ── 查回复 ─────────────────────────────────────────────────

def _headers(msg):
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _reply_kind(h, snippet=""):
    """退信（没送达）/ 自动回复 / 有回复"""
    sender = h.get("from", "").lower()
    subject = h.get("subject", "")
    if re.search(r"mailer-daemon|postmaster|mail delivery", sender) or \
            re.search(r"Delivery Status Notification|Undeliverable|Undelivered|退信|无法投递|无法递送|未送达", subject + snippet, re.I):
        return "退信"
    if h.get("auto-submitted", "no").lower() not in ("", "no") or "x-autoreply" in h or "x-autorespond" in h:
        return "自动回复"
    if re.search(r"自动回复|auto[- ]?reply|automatic reply|out of office|已收到您的", subject, re.I):
        return "自动回复"
    return "有回复"


def _ts(msg):
    return datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000)


def check_replies(records, progress=None):
    """检查每条记录：①草稿是否已从 Gmail 发出；②同一线程里是否有对方回信；③对方是否另起新邮件联系。
    返回 {record_id: 要更新的字段}。"""
    svc = get_service()
    me = config.SENDER_EMAIL.lower()
    updates = {}
    targets = [r for r in records if r.get("send_mode") in ("发送", "草稿") and r.get("to_email")]
    for n, r in enumerate(targets, 1):
        if progress and n % 10 == 0:
            progress(f"已检查 {n}/{len(targets)}")
        upd = {}
        try:
            tid = r.get("gmail_thread_id") or _find_thread(svc, r)
            if tid and not r.get("gmail_thread_id"):
                upd["gmail_thread_id"] = tid
            sent_at = _parse(r.get("sent_at"))
            reply = None
            if tid:
                th = svc.users().threads().get(userId="me", id=tid, format="metadata",
                                               metadataHeaders=["From", "Subject", "Date", "Auto-Submitted",
                                                                "X-Autoreply", "X-Autorespond"]).execute()
                for m in th.get("messages", []):
                    labels, h = m.get("labelIds", []), _headers(m)
                    if "SENT" in labels:
                        if r.get("send_mode") == "草稿" and "DRAFT" not in labels:
                            upd.update(send_mode="发送", status="已投递", sent_at=_ts(m).strftime("%Y-%m-%d %H:%M"))
                        continue
                    if "DRAFT" in labels or me in parseaddr(h.get("from", ""))[1].lower():
                        continue
                    reply = (m, h)
                    break
            if not reply:
                reply = _find_new_mail(svc, r, sent_at)
            if reply:
                m, h = reply
                upd.update(reply_status=_reply_kind(h, m.get("snippet") or ""),
                           reply_at=_ts(m).strftime("%Y-%m-%d %H:%M"),
                           reply_from=parseaddr(h.get("from", ""))[1] or h.get("from", ""),
                           reply_snippet=(m.get("snippet") or "")[:160])
            upd["reply_checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        except HttpError:
            continue
        updates[r["id"]] = upd
    return updates


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
    sent = _parse(r.get("sent_at"))
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
    who = f"from:{to}" if domain in PUBLIC_DOMAINS else f"from:{domain}"
    q = f"{who} -in:sent -in:drafts after:{sent_at:%Y/%m/%d}"
    res = svc.users().messages().list(userId="me", q=q, maxResults=3).execute()
    for item in res.get("messages", []):
        m = svc.users().messages().get(userId="me", id=item["id"], format="metadata",
                                       metadataHeaders=["From", "Subject", "Auto-Submitted",
                                                        "X-Autoreply", "X-Autorespond"]).execute()
        if _ts(m) >= sent_at:
            return m, _headers(m)
    return None


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
    config.GMAIL_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


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
                    "sent_at": sent.strftime("%Y-%m-%d %H:%M"), "body": _extract_text(m.get("payload", {}))})
    return out


def mark_processed(message_ids):
    state = _load_state()
    ids = set(state.get("processed_ids", [])) | set(message_ids)
    state["processed_ids"] = sorted(ids)
    _save_state(state)
