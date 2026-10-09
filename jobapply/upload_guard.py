"""助手的上传钩子（claude 的 PreToolUse 钩子，只挂在 file_upload 上）：只许上传「网申上传」文件夹里的文件。

    python upload_guard.py <允许的文件夹>      （stdin 是 claude 给的钩子输入 JSON）

退出码 2 = 拦下（stderr 那句话会回给助手）；0 = 放行。钩子自己出错一律放行（退出码 1 不拦），宁可多传不卡住填表。
2026-10-09 实测：--settings 里的钩子在 --setting-sources "" 下照样生效。单独运行、不导入面板的模块。"""

import json
import os
import sys


def paths_in(value):
    """工具参数里所有像本机文件路径的字符串。"""
    if isinstance(value, str):
        return [value] if value.startswith(("/", "~")) else []
    if isinstance(value, dict):
        return [p for v in value.values() for p in paths_in(v)]
    if isinstance(value, list):
        return [p for v in value for p in paths_in(v)]
    return []


def check(hook_input, allowed_dir):
    """返回不许传的路径列表。"""
    allowed = os.path.realpath(os.path.expanduser(allowed_dir))
    bad = []
    for p in paths_in((hook_input or {}).get("tool_input") or {}):
        real = os.path.realpath(os.path.expanduser(p))
        if real != allowed and not real.startswith(allowed + os.sep):
            bad.append(p)
    return bad


def main(argv):
    try:
        data = json.loads(sys.stdin.read() or "{}")
        bad = check(data, argv[1])
    except Exception:
        return 0
    if bad:
        sys.stderr.write("只能上传「网申上传」文件夹里的文件（照片、简历、研究样本都在那里）：" + "、".join(bad) + " 不在里面，没传。")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
