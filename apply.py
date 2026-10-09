#!/usr/bin/env python3
"""剪贴板一键投递（双击桌面「投递.command」）：
复制 JD 文字或招聘链接 → 运行 → 自动分析、检查、发送并记录。
有必须处理的问题（收件邮箱存疑、占位没填、重复投递、JD 硬性要求不符等）会停下来问你。"""

import subprocess
import sys
import time

import pyperclip

from jobapply import checks, config, fetch, gmail_client, llm, pipeline, records

AUTO_SEND_COUNTDOWN = 5
RED, YELLOW, GREEN, DIM, BOLD, END = "\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[1m", "\033[0m"


def notify(text, title="投递助手"):
    text = text.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}"'],
                   capture_output=True)


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def ensure_gmail():
    st = gmail_client.auth_status()
    if st["ok"]:
        return True
    print(f"{YELLOW}{st['error']}{END}")
    if ask("现在打开浏览器重新授权 Gmail？[y/回车取消] ") == "y":
        gmail_client.get_service(interactive=True)
        return gmail_client.auth_status()["ok"]
    return False


def read_input():
    text = (pyperclip.paste() or "").strip()
    if len(text) < 20:
        print(f"{RED}剪贴板是空的或太短，请先复制 JD 文字或招聘链接。{END}")
        return None
    meta = {"source_label": "", "source_url": "", "publish_date": "", "target_job": ""}
    links = fetch.share_links(text)  # 纯链接，或微信分享出来的「标题 + 链接」
    if links:
        if len(links) > 1:
            print(f"{YELLOW}剪贴板里有 {len(links)} 个链接，这里只处理第一个；一次投多个请用面板的「批量队列」。{END}")
        print(f"检测到链接，正在抓取：{links[0]}")
        page = fetch.fetch_url(links[0])
        meta.update(source_label=page["source_label"], source_url=page["url"], publish_date=page.get("publish_date", ""))
        content = page["content"]
        n_multi, n_emails = fetch.count_job_signals(content)
        if n_multi >= 2 or n_emails >= 2:
            jobs = llm.detect_jobs(content)
            if len(jobs) > 1:
                print(f"\n这篇文章有 {len(jobs)} 个岗位：")
                for i, j in enumerate(jobs, 1):
                    print(f"  {i}. {j['title']}  {j.get('location', '')}  {', '.join(j.get('emails', []))}")
                pick = ask("投哪个？输入编号（回车取消）：")
                if not pick.isdigit() or not 1 <= int(pick) <= len(jobs):
                    return None
                j = jobs[int(pick) - 1]
                same = [x for x in jobs if x["title"] == j["title"]]
                meta["target_job"] = f"{j['title']}（{j['location']}）" if len(same) > 1 and j.get("location") else j["title"]
        return content, meta
    print(f"读取到剪贴板 JD（{len(text)} 字）")
    return text, meta


def show(out):
    r = out["result"]
    print("\n" + "─" * 64)
    print(f"{BOLD}{r['company_name']}｜{r['job_title']}{END}  [{r['position_type']}｜{r['company_type']}｜{r['job_location']}]")
    print(f"收件人：{', '.join(r['to_emails']) or '（无）'}" + (f"   抄送：{', '.join(r['cc_emails'])}" if r["cc_emails"] else ""))
    print(f"标题：{r['email_subject']}")
    att = f"简历（{r['resume_version']}）{r['resume_filename']}"
    if r.get("resume_filename_en"):
        att += f" + {r['resume_filename_en']}"
    if r["attach_report"]:
        att += f"；研究样本 {r['report_filename']}"
    print(f"附件：{att}")
    print("─" * 64)
    print(r["email_body"])
    print("─" * 64)
    for f in out.get("fixes", []):
        print(f"{GREEN}已自动修正：{f}{END}")
    for i in out["issues"]:
        color = RED if i["level"] == "error" else YELLOW if i["level"] == "warn" else DIM
        print(f"{color}[{ {'error': '必须处理', 'warn': '注意', 'info': '提示'}[i['level']] }] {i['msg']}{END}")
    m = out["meta"]
    how = "走 Claude Max 会员额度" if m.get("backend") == "会员额度" else \
        f"{m.get('backend', 'API')}｜约 ${m.get('cost_usd')}"
    print(f"{DIM}{m['model']}｜{m['seconds']} 秒｜{how}{END}")


def main():
    dry = "--dry-run" in sys.argv
    print("=" * 64 + "\n剪贴板一键投递" + ("（预览模式：只生成不发送）" if dry else "") + "\n" + "=" * 64)
    if not dry and not ensure_gmail():
        return 1
    got = read_input()
    if not got:
        return 1
    jd_text, meta = got
    print(f"正在用 {config.CLAUDE_MODEL} 分析 JD、写邮件…")
    out = pipeline.analyze(jd_text, source_label=meta["source_label"], target_job=meta["target_job"])
    if not out.get("ok"):
        print(f"{RED}{out.get('error')}{END}")
        return 1
    show(out)
    result = out["result"]
    if dry:
        return 0

    if not result["to_emails"]:
        url = checks.safe_url(result.get("apply_url"))
        print(f"\n{YELLOW}这个岗位要网申。网申资料（填表字段、自我介绍、本岗位问答）在面板里："
              f"{config.PANEL_BASE} → 把同一份 JD 贴进「新投递」。{END}")
        if url:
            print(f"网申链接：{url}")
            subprocess.run(["open", url])
            if ask("投完后输入 y 记录这次网申（回车跳过）：") == "y":
                pipeline.record_web_application(result, jd_text, source_type="剪贴板", **meta)
                print(f"{GREEN}已记录。{END}")
            return 0
        print(f"{RED}没有收件邮箱，也没有网申链接（可能要扫码），请到面板里处理。{END}")
        return 1

    issues = out["issues"]
    must_stop = [i for i in issues if i["level"] == "error"]
    both = result.get("apply_channel") == "邮箱+网申"
    # 剪贴板模式会倒计时自动发：有任何「注意」、或者除了发邮件还要网申，都先停下问
    warns = [i for i in issues if i["level"] == "warn"]
    should_ask = must_stop or warns or both
    force = False
    mode = "send"
    if should_ask:
        tip = ("有必须处理的问题。" if must_stop else "有需要你确认的情况（见上方黄色提示）。" if warns else "") + \
              ("这个岗位除了发邮件还要网申（发完会打开网申链接）。" if both else "")
        verb = "仍然发送" if must_stop or warns else "发送"
        choice = ask(f"\n{tip}输入 y {verb} / d 存为 Gmail 草稿再改 / 回车取消：")
        if choice not in ("y", "d"):
            print("已取消。可以把 JD 粘到网页面板里修改后再发。")
            return 0
        mode = "draft" if choice == "d" else "send"
        force = True
    else:
        print(f"\n检查通过，{AUTO_SEND_COUNTDOWN} 秒后自动发送（按 Ctrl+C 取消）…", end="", flush=True)
        try:
            for s in range(AUTO_SEND_COUNTDOWN, 0, -1):
                print(f" {s}", end="", flush=True)
                time.sleep(1)
            print()
        except KeyboardInterrupt:
            print("\n已取消。")
            return 0

    res = pipeline.deliver(result, jd_text, mode=mode, force=force, source_type="剪贴板", **meta)
    verb = "已存为 Gmail 草稿" if res["mode"] == "草稿" else "已发送"
    print(f"\n{GREEN}{verb}：{', '.join(result['to_emails'])}｜附件：{'、'.join(res['attachments'])}{END}")
    if res.get("record_error"):
        print(f"{RED}{res['record_error']}{END}")
        return 1
    notify(f"{verb}：{result['company_name']} {result['job_title']}")
    if both:
        url = checks.safe_url(result.get("apply_url"))
        print(f"\n{YELLOW}别忘了还要网申{('：' + url) if url else '（JD 里没找到链接，可能要扫码）'}。"
              f"网申资料（自我介绍、本岗位问答）在面板里：{config.PANEL_BASE}{END}")
        if url:
            subprocess.run(["open", url])
        if ask("网申投完后输入 y，补记在刚才这条投递记录上（回车跳过）：") == "y":
            pipeline.record_web_application(result, jd_text, source_type="剪贴板", record_id=res.get("record_id", ""), **meta)
            print(f"{GREEN}已补记网申。{END}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 0
    except gmail_client.SendUncertain as e:  # 可能已经发出：千万别再跑一遍
        print(f"{RED}{e}{END}")
        notify("发送结果不确定：先去 Gmail「已发送」看看，别重复发")
        code = 1
    except (llm.LLMError, fetch.FetchError, gmail_client.GmailAuthError, gmail_client.SendFailed,
            pipeline.Blocked, records.RecordsCorrupt, ValueError) as e:
        print(f"{RED}{e}{END}")
        notify("出错了，请看终端窗口")
        code = 1
    sys.exit(code)
