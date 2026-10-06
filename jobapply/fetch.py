"""抓取招聘链接：微信公众号（含图片 JD 的 OCR）和普通网页。"""

import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import llm

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
JD_SIGNALS = ["岗位职责", "任职要求", "工作职责", "职位描述", "工作内容", "岗位要求", "投递", "简历", "邮箱", "@"]
WECHAT_BLOCKED = ["环境异常", "完成验证后即可继续访问", "当前环境异常", "参数错误", "该内容已被发布者删除",
                  "此内容因违规无法查看", "该公众号已迁移"]


class FetchError(Exception):
    pass


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
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise FetchError("请粘贴完整链接（以 http 开头）。")
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
    text = content_el.get_text(separator="\n", strip=True)
    ocr_used = False
    if len(text) < 200 or not any(s in text for s in JD_SIGNALS):
        imgs = _wechat_images(html)
        if imgs:
            ocr = llm.ocr_images(imgs)
            if ocr:
                text = (text + "\n\n" + ocr).strip() if text else ocr
                ocr_used = True
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 30:
        raise FetchError("文章里没读到招聘文字（可能全是图片且识别失败）。请复制文字粘贴到 JD 框。")
    return {"title": title, "content": text, "source": "wechat",
            "source_label": f"{account}（公众号）" if account else "微信公众号",
            "account_name": account, "publish_date": publish_date, "url": url, "ocr_used": ocr_used}


def _wechat_images(html, limit=8):
    urls, seen = [], set()
    for m in re.finditer(r'(https://mmbiz\.qpic\.cn/[^\s"<>\'\\]+)', html):
        u = m.group(1).replace("&amp;", "&")
        key = u.split("?")[0]
        if key not in seen:
            seen.add(key)
            urls.append(u)
    images = []
    for u in urls[:20]:
        fetch = u if "wx_fmt=" in u else u + ("&" if "?" in u else "?") + "wx_fmt=png"
        try:
            r = requests.get(fetch, headers={"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/"}, timeout=15)
        except requests.RequestException:
            continue
        data = r.content
        if r.status_code != 200 or len(data) < 15000:  # 小图多是 logo / 二维码 / 分割线
            continue
        if data[:3] == b"\xff\xd8\xff":
            mt = "image/jpeg"
        elif data[:4] == b"\x89PNG":
            mt = "image/png"
        elif data[:4] == b"RIFF":
            mt = "image/webp"
        elif data[:3] == b"GIF":
            mt = "image/gif"
        else:
            continue
        if len(data) > 4_500_000:
            continue
        images.append((data, mt))
        if len(images) >= limit:
            break
    return images


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
