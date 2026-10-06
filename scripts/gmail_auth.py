#!/usr/bin/env python3
"""重新授权 Gmail：会打开浏览器，点「允许」即可。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobapply import config, gmail_client  # noqa: E402

print(f"正在打开浏览器里的 Google 授权页面……（选 {config.SENDER_EMAIL}，点「继续」「允许」）")
gmail_client.get_service(interactive=True)
st = gmail_client.auth_status()
print("授权成功：" + st["email"] if st["ok"] else "授权失败：" + st["error"])
sys.exit(0 if st["ok"] else 1)
