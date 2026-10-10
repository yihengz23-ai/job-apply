"""证件号（身份证号）：只存在这台 Mac 的「钥匙串」里——不进任何文件（底稿、记录、日志都不存）、不进助手对话、不给助手。
网申卡片上「等你·证件号」那条旁边点「复制证件号」：面板在本机直接把号码放进剪贴板（pbcopy），号码不经过网页；
60 秒后剪贴板里还是它就清掉。本人去网页那一栏粘贴（⌘V），助手不碰这一栏。
本人在「我的资料（网申）」页存一次；存、复制都只能在电脑本机做（手机隧道上不行）。"""

import re
import subprocess
import threading

from . import config

SERVICE = "jobapply.idcard" + (".test" if config.IS_TEST_ENV else "")   # 测试环境用另一条，碰不到本人存的那条
ACCOUNT = "本人"
CLEAR_AFTER = 60
W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]


class Invalid(ValueError):
    pass


def check_digit(first17):
    return "10X98765432"[sum(int(c) * k for c, k in zip(first17, W)) % 11]


def normalize(number):
    """去掉空格和横线，末位 x 统一成大写。不是 18 位、或者校验位对不上的报错（多半是输错了一位）。"""
    n = re.sub(r"[\s-]", "", str(number or "")).upper()
    if not re.fullmatch(r"\d{17}[\dX]", n):
        raise Invalid("身份证号要 18 位（最后一位可以是 X）")
    if check_digit(n[:17]) != n[17]:
        raise Invalid("校验位对不上：多半是哪一位输错了，再核对一下")
    return n


def mask(n):
    return f"{n[:4]}{'*' * 10}{n[-4:]}" if n else ""


def _security(*args, input_text=None):
    """调 macOS 的 security。出错时抛的异常里不能带命令行（存的时候号码在参数里）：一律换成不带细节的 RuntimeError。"""
    try:
        return subprocess.run(["security", *args], capture_output=True, text=True, input=input_text, timeout=10)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("钥匙串没响应（可能弹了解锁框没点）：再点一次") from None


def save(number):
    n = normalize(number)
    r = _security("add-generic-password", "-U", "-s", SERVICE, "-a", ACCOUNT, "-l", "求职面板：证件号", "-w", n)
    if r.returncode != 0:
        raise RuntimeError("存进钥匙串没成功（security 返回 %d）" % r.returncode)   # stderr 不原样转出去
    return mask(n)


def _read():
    try:
        r = _security("find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w")
    except RuntimeError:
        return ""
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def status():
    """{saved: 存没存, masked: 打码后的样子}——号码本身不出这个模块（除了放进剪贴板）。"""
    n = _read()
    return {"saved": bool(n), "masked": mask(n)}


def forget():
    try:
        _security("delete-generic-password", "-s", SERVICE, "-a", ACCOUNT)
    except RuntimeError:
        pass
    return status()


def _pbpaste():
    return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout


def _pbcopy(text):
    subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=True)


def _clear_later(n, delay):
    def _clear():
        try:
            if _pbpaste().strip() == n:
                _pbcopy("")
        except (OSError, subprocess.SubprocessError):
            pass
    t = threading.Timer(delay, _clear)
    t.daemon = True
    t.start()
    return t


def copy_to_clipboard(delay=CLEAR_AFTER):
    """把号码放进本机剪贴板，delay 秒后剪贴板里还是它就清掉。没存过返回 False。"""
    n = _read()
    if not n:
        return False
    _pbcopy(n)
    _clear_later(n, delay)
    return True
