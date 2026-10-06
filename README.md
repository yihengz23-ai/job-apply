# Job Application Automation System · 求职投递系统

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Claude API](https://img.shields.io/badge/Claude-Opus_5.5-orange.svg)](https://www.anthropic.com/)
[![Gmail API](https://img.shields.io/badge/Gmail-API-red.svg)](https://developers.google.com/gmail/api)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Demo](https://img.shields.io/badge/Demo-Live-brightgreen.svg)](https://yihengz23-ai.github.io/job-apply/demo/)

> PE / VC 投递自动化：贴一份 JD（或公众号链接）→ AI 读懂 JD 的每一条格式要求 → 生成标题、正文、附件命名 → **代码层逐项核对** → Gmail 发送或存草稿 → 自动记账、查回复、查退信。单次投递从 15–20 分钟压缩到约 15 秒。

**[>>> 在线交互演示（虚构数据，不连 AI、不发邮件）<<<](https://yihengz23-ai.github.io/job-apply/demo/)**

| 新投递：JD 要求 + 邮件 + 发信前检查 | 投递看板：批次 / 类型 / 回复 / 退信 | 数据分析 |
|:---:|:---:|:---:|
| ![New](demo/screenshots/new.png) | ![Board](demo/screenshots/board.png) | ![Analytics](demo/screenshots/analytics.png) |

## 为什么做

PE/VC 投递的麻烦不在写信，而在**每家要求都不一样**：

- 标题格式五花八门：`【投研实习生+姓名+学校+202X届+每周x天共x月】`、`姓名-学校-毕业时间-最早可入职时间`……
- 简历文件名也要按格式改；有的要中英文简历，有的只要英文；有的要研究报告，有的「可附」
- 实习、留用实习、校招全职，邮件里该写的信息不一样（到岗时间 / 每周天数 / 毕业时间 / 届别）
- 投了几十家之后，谁回了、谁约了面试、哪封被退信，全靠记忆

## 核心设计

**1. AI 只负责「读懂 + 起草」，对错由代码把关。** Claude 用结构化输出（JSON Schema）一次给出：JD 硬性要求（标题格式、命名格式、正文须写信息、所需材料、联系人、截止日期）、岗位类型（全职 / 留用实习 / 实习）、简历版本、文件名和正文。然后由确定性规则逐项检查：

| 检查 | 级别 |
|---|---|
| 收件 / 抄送邮箱必须在 JD 原文里出现（防 AI 编造或抄错） | 必须处理 |
| 称呼里的名字必须在 JD 原文里有依据（不许从邮箱前缀猜「X总」） | 自动改成「您好」 |
| 未填占位符 `[期望薪资]`、已从简历删掉的旧经历、写错的毕业时间 | 必须处理 |
| 简历 PDF 里的毕业时间与档案不一致 | 必须处理 |
| 正文出现简历和 JD 都没有的数字、资历词（主导 / CFA / 建模…） | 注意 |
| 以前投过同一邮箱 / 同一域名 / 相近机构名（不拦截，但列出历史） | 注意 |
| JD 硬性条件不符（届别、城市、学历、英文样本…） | 注意 |

**2. 事实和规则分离，单一来源。** `candidate_profile.md` 只放与简历逐条一致的事实；`email_rules.md` 只放写信规则和示例；`candidate_settings.json` 放姓名、文件名、批次等设置。改简历只改这三处，提示词自动重载。

**3. 发出去之后也管。** 每封信记录 Gmail 线程 ID；一键检查回复（含对方另起新邮件约面试的情况），自动识别 **退信 / 自动回复 / 真回复**，发送 90 秒后自动查一次退信。

**4. 数据不丢。** 记录文件加锁、原子写入、每日备份；文件损坏时拒绝覆盖。看板每次变动同步一份 Excel。

## 功能

- **AI 写信**：Claude Opus 5.5 + 结构化输出 + 系统提示缓存（单次约 $0.03）；支持「按补充要求重写」
- **三类岗位**：全职（届别、入职时间）/ 留用实习 / 普通实习，到岗信息只写 JD 问到的
- **简历版本**：中英双页原件 / 只发中文页 / 只发英文页 / 中英各一份，自动按页拆分；网申可一键下载改好名的简历
- **公众号抓取**：正文抓取 + 图片 JD 的 Vision OCR + 多岗位文章选岗
- **发送或存草稿**：凌晨提醒先存草稿；附件中文名与 Gmail 网页版同编码，各家邮箱不乱码
- **看板 / 分析**：按投递批次、岗位类型、状态、机构性质、城市筛选；回复、退信、待跟进一目了然
- **剪贴板模式**：复制 JD 或链接 → 双击运行 → 检查全部通过才倒计时自动发送，否则停下来问
- **手机访问**：Cloudflare Tunnel + 访问密钥；只允许本机做 Gmail 授权；防跨站请求、防 DNS rebinding

## 技术栈

| 组件 | 技术 |
|---|---|
| AI | Claude API（Opus 5.5，结构化输出、提示缓存、拒答自动兜底） |
| 邮件 | Gmail REST API + OAuth 2.0 |
| 后端 | Python 3.10+ / Flask |
| 前端 | Tailwind CSS + 原生 JS（单页） |
| PDF | PyMuPDF（按语言拆页、校验简历） |
| 数据 | JSON（原子写入 + 备份）→ Excel 镜像 |
| 抓取 | requests + BeautifulSoup + Claude Vision |

## 目录

```
├── app.py                     # 网页面板（Flask）
├── apply.py                   # 剪贴板一键投递（--dry-run 只生成不发送）
├── jobapply/
│   ├── config.py              # 路径、模型、系统代理
│   ├── llm.py                 # Claude 调用（JD 分析、选岗、OCR、邮件分类）
│   ├── checks.py              # 发信前确定性检查 + 安全自动修正
│   ├── pipeline.py            # 分析 → 检查 → 发送 / 草稿 → 记录
│   ├── gmail_client.py        # 授权、组信、发送、草稿、回复 / 退信检测、同步
│   ├── records.py             # 记录存储、查重、统计、Excel 镜像
│   ├── resume.py              # 简历版本与校验
│   └── fetch.py               # 公众号 / 网页抓取
├── templates/index.html       # 面板界面
├── candidate_profile.md       # 候选人事实（示例：虚构人物「张三」）
├── email_rules.md             # 写信规则 + 示例
├── candidate_settings.example.json
├── demo/                      # 静态演示：真实界面 + mock.js 虚构数据
├── scripts/                   # Gmail 重新授权、生成演示页
└── tests/                     # pytest
```

## 快速开始

```bash
git clone https://github.com/yihengz23-ai/job-apply.git && cd job-apply
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                   # 填 ANTHROPIC_API_KEY
cp candidate_settings.example.json candidate_settings.json
# 改 candidate_settings.json / candidate_profile.md / email_rules.md 为你自己的信息
# 把 Google Cloud 下载的 OAuth 桌面应用凭证存为 credentials.json
.venv/bin/python scripts/gmail_auth.py                 # 首次授权 Gmail
.venv/bin/python app.py                                # 打开 http://localhost:5001
.venv/bin/python -m pytest                             # 跑测试
```

> Google Cloud 的 OAuth 同意屏幕如果停在「测试」状态，刷新令牌 7 天就会失效；个人使用可以把应用发布为「生产」状态。

## License

MIT — see [LICENSE](LICENSE)

## Author

**Yiheng Zhang（章益恒）** — University of Chicago, Harris School of Public Policy

Built with [Claude Code](https://claude.com/claude-code) + [Claude API](https://www.anthropic.com/)
