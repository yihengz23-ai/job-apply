"""抓取招聘链接：微信公众号（含图片 JD 的 OCR）和普通网页。"""

import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import checks, config, llm

QR_TOOL = config.BASE_DIR / "tools" / "qrdecode"
QR_HINTS = ("二维码", "扫码", "长按", "识别", "小程序", "网申", "投递链接", "报名")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
JD_SIGNALS = ["岗位职责", "任职要求", "工作职责", "职位描述", "工作内容", "岗位要求", "投递", "简历", "邮箱", "@"]
WECHAT_BLOCKED = ["环境异常", "完成验证后即可继续访问", "当前环境异常", "参数错误", "该内容已被发布者删除",
                  "此内容因违规无法查看", "该公众号已迁移"]


class FetchError(Exception):
    pass


SHARE_URL = re.compile(r"https?://[^\s<>\"'\u3000-\u303f\u4e00-\u9fff\uff00-\uffef)】]+")
_JD_PARAGRAPHS = ("岗位职责", "任职要求", "职位描述", "工作职责", "工作内容", "岗位要求", "任职资格", "职位要求")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def share_links(text):
    """微信「复制链接」/ 分享出来的「标题 + 链接」（可以一次好几条）→ 链接列表；
    像 JD 正文（有「岗位职责」这类段落、写着投递邮箱、或者文字很长）的返回 []。"""
    text = text or ""
    urls = list(dict.fromkeys(u.rstrip(".,;:!?") for u in SHARE_URL.findall(text)))
    if not urls:
        return []
    rest = SHARE_URL.sub("", text)
    lines = [l.strip() for l in rest.splitlines() if l.strip()]
    if any(h in text for h in _JD_PARAGRAPHS) or _EMAIL.search(rest):
        return []
    if len(lines) > 2 * len(urls) or sum(len(l) for l in lines) > 120 * len(urls):
        return []
    return urls


def _join_split_emails(text):
    """公众号排版常把邮箱拆进几个 <span>，按行取文字时会断成「hr\n@abc.com」：接回去。"""
    text = re.sub(r"(?<=[\w.+-])\s*\n\s*(?=@[\w-])", "", text)
    text = re.sub(r"(?<=[\w.+-]@)\s*\n\s*(?=[\w-])", "", text)
    return re.sub(r"(?<=@[\w-])([\w-]*)\s*\n\s*(?=\.(?:com|cn|net|org|edu|hk|io|co)\b)", r"\1", text)


def _get(url):
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=25)
    except requests.RequestException as e:
        raise FetchError(f"打不开这个链接：{e}") from e
    if resp.status_code >= 400:
        raise FetchError(f"网页返回错误 {resp.status_code}")
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp


def fetch_url(url):
    """url 可以是纯链接，也可以是微信分享出来的「标题 + 链接」。"""
    m = SHARE_URL.search(url or "")
    if not m:
        raise FetchError("请粘贴完整链接（以 http 开头）。")
    url = m.group(0).rstrip(".,;:!?")
    resp = _get(url)
    if "mp.weixin.qq.com" in url:
        return _wechat(url, resp)
    return _generic(url, resp)


def _wechat(url, resp):
    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    title_el = soup.find("h1", id="activity-name") or soup.find("h1", class_="rich_media_title")
    title = title_el.get_text(strip=True) if title_el else ""
    content_el = soup.find("div", id="js_content") or soup.find("div", class_="rich_media_content")
    if not content_el:
        hit = next((s for s in WECHAT_BLOCKED if s in html), "")
        raise FetchError(f"微信不让自动抓取这篇文章（{hit or '页面结构异常'}）。"
                         "请在手机或电脑微信里打开文章，全选复制正文，粘贴到下面的 JD 框。")

    account = ""
    for tag, attrs in (("a", {"id": "js_name"}), ("span", {"class": "rich_media_meta_nickname"}),
                       ("strong", {"class": "profile_nickname"})):
        el = soup.find(tag, attrs)
        if el and el.get_text(strip=True):
            account = el.get_text(strip=True)
            break
    if not account:
        m = re.search(r'var\s+nickname\s*=\s*(?:htmlDecode\()?["\']([^"\']+)["\']', html)
        account = m.group(1) if m else ""

    publish_date = ""
    m = re.search(r'var\s+(?:ct|create_time)\s*=\s*["\']?(\d{10})', html)
    if m:
        publish_date = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")

    for tag in content_el.find_all(["script", "style"]):
        tag.decompose()
    text = _join_split_emails(content_el.get_text(separator="\n", strip=True))
    ocr_used, qr_urls, imgs = False, [], None
    if len(text) < 200 or not any(s in text for s in JD_SIGNALS):
        imgs = _download_images(html)
        big = [(d, mt) for d, mt in imgs if len(d) >= 15000][:8]
        if big:
            ocr = llm.ocr_images(big)
            if ocr:
                block = checks.OCR_MARKER + "\n" + ocr
                text = (text + "\n\n" + block).strip() if text else block
                ocr_used = True
    # 文章里有二维码投递 / 没写邮箱时，扫一遍图片里的二维码，找网申链接
    if any(h in text for h in QR_HINTS) or "@" not in text:
        if imgs is None:
            imgs = _download_images(html)
        qr_urls = decode_qr(imgs)
        if qr_urls:
            text += "\n\n【文章图片里的二维码链接（系统自动识别）】\n" + "\n".join(qr_urls)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 30:
        raise FetchError("文章里没读到招聘文字（可能全是图片且识别失败）。请复制文字粘贴到 JD 框。")
    return {"title": title, "content": text, "source": "wechat",
            "source_label": f"{account}（公众号）" if account else "微信公众号",
            "account_name": account, "publish_date": publish_date, "url": url, "ocr_used": ocr_used,
            "qr_urls": qr_urls}


def _image_type(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] == b"RIFF":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return ""


def _download_images(html, limit=20, min_size=2000):
    """下载文章里的图片（按出现顺序），返回 [(bytes, media_type)]。"""
    urls, seen = [], set()
    for m in re.finditer(r'(https://mmbiz\.qpic\.cn/[^\s"<>\'\\]+)', html):
        u = m.group(1).replace("&amp;", "&")
        key = u.split("?")[0]
        if key not in seen:
            seen.add(key)
            urls.append(u)
    out = []
    for u in urls[:30]:
        fetch = u if "wx_fmt=" in u else u + ("&" if "?" in u else "?") + "wx_fmt=png"
        try:
            r = requests.get(fetch, headers={"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/"}, timeout=15)
        except requests.RequestException:
            continue
        mt = _image_type(r.content)
        if r.status_code == 200 and mt and min_size <= len(r.content) <= 4_500_000:
            out.append((r.content, mt))
            if len(out) >= limit:
                break
    return out


def decode_qr(images):
    """用 macOS Vision（tools/qrdecode）识别图片里的二维码，返回网申 / 报名链接（去掉公众号关注码）。"""
    if not images or not _ensure_qr_tool():
        return []
    with tempfile.TemporaryDirectory(prefix="jobapply-qr-") as tmp:
        paths = []
        for i, (data, mt) in enumerate(images):
            p = Path(tmp) / f"img{i}.{mt.split('/')[-1]}"
            p.write_bytes(data)
            paths.append(str(p))
        try:
            out = subprocess.run([str(QR_TOOL), *paths], capture_output=True, text=True, timeout=60).stdout
        except (subprocess.TimeoutExpired, OSError):
            return []
    urls = []
    for line in out.splitlines():
        payload = line.split("\t", 1)[-1].strip()
        if payload.startswith(("http://", "https://")) and "weixin.qq.com" not in payload and payload not in urls:
            urls.append(payload)
    return urls


def _ensure_qr_tool():
    if QR_TOOL.exists():
        return True
    src = QR_TOOL.with_suffix(".swift")
    if not src.exists():
        return False
    try:
        subprocess.run(["swiftc", "-O", str(src), "-o", str(QR_TOOL)], capture_output=True, timeout=300, check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    return QR_TOOL.exists()


def _generic(url, resp):
    soup = BeautifulSoup(resp.text, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    domain = re.search(r"//([^/]+)", url).group(1)
    for selector in ("article", "main", ".job-detail", ".position-detail", ".job-description",
                     ".content", ".post-content", "#content", ".entry-content"):
        el = soup.select_one(selector)
        if el:
            for tag in el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return {"title": title, "content": re.sub(r"\n{3,}", "\n\n", text), "source": "webpage",
                        "source_label": domain, "url": url, "publish_date": ""}
    body = soup.find("body") or soup
    for tag in body.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", body.get_text(separator="\n", strip=True))
    if len(text) < 100:
        raise FetchError("这个网页的内容是动态加载的，抓不到正文。请直接复制网页上的 JD 文字粘贴进来。")
    return {"title": title, "content": text, "source": "generic", "source_label": domain, "url": url,
            "publish_date": ""}


def count_job_signals(text):
    """粗判是不是多岗位文章（决定要不要让 AI 列岗位）。"""
    multi = re.findall(r"岗位[一二三四五六七八九十\d]|职位[一二三四五六七八九十\d]|方向[一二三四五六七八九十\d]|"
                       r"^[一二三四五六七八九十]、.{0,20}(?:实习|岗位|招聘|方向|分析师|经理)", text, re.M)
    emails = set(re.findall(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+", text))
    return len(multi), len(emails)
