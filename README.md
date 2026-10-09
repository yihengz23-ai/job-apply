# 求职投递系统 · Job Application Copilot

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Claude](https://img.shields.io/badge/Claude-Opus_5.5-orange.svg)](https://www.anthropic.com/)
[![Gmail API](https://img.shields.io/badge/Gmail-API-red.svg)](https://developers.google.com/gmail/api)
[![Tests](https://img.shields.io/badge/tests-425-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Demo](https://img.shields.io/badge/Demo-Live-brightgreen.svg)](https://yihengz23-ai.github.io/job-apply/demo/)

**一个人用的求职投递系统，我自己秋招每天在用。** 贴一份 JD 或公众号链接 → AI 读懂格式要求、起草邮件 → **代码逐项核对**（邮箱必须出现在 JD 原文里、占位没填不许发……）→ 发送或定时发送；只能网申的岗位，交给面板里的**助手在浏览器里代填**，本人提交后它把网站上**实际提交的内容读回来**记进看板。全程 AI 只负责起草和操作，最后一下由人点。

> *A personal job-application system I use daily: Claude drafts tailored emails from each JD, deterministic code checks every hard requirement before anything is sent, a browser agent fills online application forms (the human always clicks submit), and the tracker stores what was actually submitted — read back from the website, not what the AI drafted.*

### **[>>> 在线演示：打开就能点（虚构数据，不连 AI、不发邮件）<<<](https://yihengz23-ai.github.io/job-apply/demo/)**

演示里建议先做两件事：① 在「网申」页点「某硬科技基金 W」那行的 **让助手填**，右下角看助手一步步填表、停下来把证件号留给你；填好后点 **我已提交**，看它去网站读回实际提交的内容。② 在「投递」页看 AI 写的信和右边的**发信前检查**。

![网申助手：左边每家网申一行一个颜色，右边是助手填完的汇报](demo/screenshots/ws_agent.png)

---

## 30 秒看懂

| | 做什么 | 关键设计 |
|---|---|---|
| **写邮件** | 贴链接或 JD（一次可以贴十几个），后台并行写好一封打开一封，人审核后发送 / 存草稿 / 定时发 | AI 用结构化输出给出 JD 的硬性要求和草稿；**发不发由代码决定**：十几项确定性检查，硬问题点「仍然发送」也发不出去 |
| **网申** | 只能网申的岗位，面板里的助手（Claude Code + Claude in Chrome）在你自己的浏览器里代填 | 一家一个网页、一个颜色框；要登录就停下等你；照片自己传、**证件号留给本人**、**最后提交本人点**；提交后读回网站上真交上去的内容 |
| **跟进** | 投递看板、查回复 / 退信、数据分析，自动同步一份 Excel | 看板里网申记录存的是**从网站读回的原文**，不是面板生成的稿子；状态只往前推，投递时间只写一次 |

写一封按 JD 格式定制的信：手工 15–20 分钟 → 后台约 20 秒写好，人只负责审核。

## 为什么做

PE / VC / 战投的投递，麻烦不在「写」，而在**每家要求都不一样、而且量大**：

- 标题格式五花八门：`【投研实习生+姓名+学校+202X届+每周x天共x月】`、`姓名-学校-毕业时间-最早可入职时间`……简历文件名也要跟着改；
- 实习、留用实习、校招全职，邮件里该交代的信息不一样（到岗时间 / 每周天数 / 届别）；
- 大公司、银行只收网申：几十个网站、每个都要从头填一遍，还有登录、验证码、志愿顺序；
- 投了几十家以后，谁回了、谁约了笔试面试、哪封被退信、在网站上到底交了什么，全靠记忆。

## 怎么工作

```mermaid
flowchart LR
    A[贴链接 / JD] --> B[抓取正文<br/>图片 OCR · 二维码]
    B --> C[Claude 结构化分析<br/>硬性要求 + 草稿]
    C --> D{代码逐项核对}
    D -- 有硬问题 --> E[必须改，<br/>强制也发不出去]
    D -- 通过 --> F[本人审核]
    F --> G[Gmail 发送 / 草稿 / 定时]
    G --> H[(投递看板<br/>+ Excel 镜像)]
    C -- 只能网申 --> I[网申页：一家一行一个颜色]
    I --> J[助手在浏览器里代填<br/>（独立宿主进程托管）]
    J -- 要登录 / 证件号 --> K[停下等本人]
    K --> J
    J --> L[本人提交]
    L --> M[助手读回网站上<br/>实际提交的内容]
    M --> H
    H --> N[查回复 / 退信 · 数据分析]
```

### 1. 写邮件：AI 起草，代码把关

Claude 用 JSON Schema 结构化输出，一次给出：JD 的硬性要求（标题格式、简历命名、正文必须交代的信息、需附材料、联系人、截止日期）、岗位类型、简历版本和草稿。然后由确定性规则逐项检查：

| 检查 | 级别 |
|---|---|
| 收件 / 抄送邮箱必须和 JD 原文**完全一致**（防 AI 编造或抄错）；图片里的邮箱 Claude 和 macOS Vision 各认一遍，对不上不放行 | 硬问题 |
| 未填占位 `[期望薪资]`、写错的毕业时间、简历上没有的经历、收件人是自己、标题为空、正文说附了但附件里没有 | 硬问题（强制发送也跳不过） |
| 简历 PDF 里的毕业时间和档案对不上 | 硬问题 |
| 称呼里的名字必须在 JD 原文里有依据（不从邮箱前缀猜「X 总」） | 自动改成「您好」 |
| 正文出现简历和 JD 里都没有的数字、资历词（主导 / CFA / 建模……） | 注意 |
| 以前投过同一邮箱 / 同一域名 / 相近机构 | 注意（列出历史，不拦） |

另外：晚上点发送默认排到第二天 10:00 由面板自己发；发送前先「认领」，同一封信不会被两个窗口发两次；网络超时这种「不知道发没发出去」的情况，先去 Gmail「已发送」里核对，绝不自动重发。

![JD 的要求、发信前检查、按 JD 格式改好名的附件](demo/screenshots/checks.png)

### 2. 网申：面板里的助手代填

只能网申的岗位自动进「网申」页，一家一行、一个颜色。点「让助手填」，面板起一个 Claude Code 进程（带 Claude in Chrome），在**本人自己的浏览器**里填表：

- **一家一个网页、一个颜色框**：助手在网页四周画上和面板这一行同色的框，几家同时填也分得清哪个网页是哪个对话在填；
- **填表靠脚本，不靠截图猜**：`wsfill.js` 认得常见招聘系统的表单组件（Moka、antd、Element UI，以及大厂自研的组件库……；新网站的坑记进网站笔记），按「网申底稿」逐栏填，日期、下拉、级联都处理；
- **只在真正需要本人时停下**：要登录就请本人在框住的网页里登录，**留在这一轮里等**，登录好了自己接着填；照片、单页简历自己传；**证件号一律留给本人**；**最后的提交按钮本人点**；
- **报状态有固定格式**：助手每次停下都写一行 `【网申记录】……｜状态：已填待提交 / 等你处理`，面板据此更新这一行和看板，「要你做」只认助手明确写出的那句；
- **提交后读回**：本人点「我已提交」，助手去网站把投了哪些岗位、每个岗位的 JD、实际提交的简历读回来（证件号自动打码），看板里存的就是网站上真交上去的版本。

![本人提交后，助手去网站读回实际提交的内容](demo/screenshots/ws_readback.png)

助手的权限是收紧过的：只放行浏览器工具；读文件只许读「网申上传」文件夹；上传文件经过 PreToolUse 钩子校验，只许传这个文件夹里的照片和简历；不给关标签页（关掉组里最后一个标签页会丢整个标签组）；不带 API Key、不带本机的其他 Claude 会话变量和长期记忆。

### 3. 跟进：看板和数据分析

![投递看板：网申记录里是从网站读回的实际提交内容](demo/screenshots/board.png)

- 按批次、岗位类型、状态、机构性质、城市筛选；回复、退信、待跟进一目了然；
- 查回复：邮件看原线程里所有来信 + 对方另起的新邮件；网申按机构名查收件箱（排除招聘网站推送）；系统确认信算自动回复，面试 / 笔试邀请算真回复；判断错了可以手动改，改过的不再被覆盖；
- 每次变动同步一份 Excel 镜像（控制字符清洗，`=` 开头按文字存）。

![数据分析](demo/screenshots/analytics.png)

## 可靠性

这是每天在用的工具，出错的代价是「信发错了」「网申白填了」，所以可靠性是按「数据不能丢、信不能重发、助手不能断」来设计的：

- **助手不随面板断**：每个助手进程交给一个独立的小宿主进程托管（自己一个会话），面板重启、甚至被 `kill -9`，助手和它开的网页都还在；面板起来后从记下的位置接着读输出，消息不重也不丢（有测试：强杀面板后再起来，还是原来的进程）；
- **「停下」只停这一轮**：用 stream-json 的中断请求，不杀进程（杀进程它开的网页就失控了，银行网站还得重新登录）；
- **状态只往前推**：已提交、不投了是终态，助手的上报只能补账号和进度；投递时间只写一次，笔试、面试中不会被改回已投递；
- **数据**：线程锁 + 文件锁 + 原子写入，文件损坏时拒绝覆盖；滚动备份；不认识的顶层数据原样保留；
- **测试**：425 个 pytest，覆盖检查规则、发信认领、定时发送、网申状态流转、读回、助手宿主（用假的 claude 进程端到端跑）、填表脚本静态检查；测试一律在隔离目录里跑，**守卫测试**保证测试不碰真实数据，碰了就判失败；
- **测试环境**：`JOBAPPLY_ENV=test` 起在 5002 端口，独立数据目录、假的 claude、不连 Gmail、不开定时发送，随便点不会碰到正在用的面板。

## 隐私（这个公开仓库）

- 这里是**脱敏的公开版**：代码和我自己用的一样，人物、机构、邮箱、账号全部虚构（候选人「张三」、「A 资本」……）；简历、投递记录、令牌、网申底稿都不在仓库里；
- 每次推送前跑隐私扫描：真实姓名 / 学校 / 实习单位等关键词，**18 位身份证号（核对校验位）、11 位手机号、打码的手机号**——扫描不过就不推；
- 网申读回的原文里，证件号在存进看板之前就打码。

## 技术栈

| 组件 | 技术 |
|---|---|
| AI | Claude（Opus 5.5）：结构化输出写信、OCR、分类；Claude Code 无界面模式 + Claude in Chrome 做网申助手 |
| 调用通道 | 本机已登录的 Claude Code（会员额度），或 Anthropic API Key（按量）；会员通道失败自动回退 |
| 邮件 | Gmail REST API + OAuth 2.0 |
| 后端 | Python 3.10+ / Flask；助手宿主进程（文件通道：in.jsonl / out.jsonl） |
| 前端 | Tailwind CSS + 原生 JS（单页） |
| 网申填表 | `wsfill.js`（注入到网申页面里跑的填表 / 读回脚本） |
| PDF / 图片 | PyMuPDF（按语言拆页、校验简历）；macOS Vision（OCR、二维码）；sips（照片规格） |
| 数据 | JSON（锁 + 原子写入 + 滚动备份）→ Excel 镜像 |

## 目录

```
├── app.py                     # 网页面板（Flask）
├── apply.py                   # 剪贴板模式（--dry-run 只生成不发送）
├── wsfill.js                  # 网申填表 / 读回脚本（助手在网申页面里加载）
├── wangshen_rules.md          # 网申助手的规则（唯一一份）
├── jobapply/
│   ├── llm.py                 # Claude 调用（JD 分析、写信、OCR、分类）
│   ├── checks.py              # 发信前确定性检查（含强制发送也跳不过的硬问题）
│   ├── pipeline.py            # 分析 → 检查 → 发送 / 草稿 → 记录
│   ├── jobqueue.py            # 批量队列：后台并行、认领防重发、定时发送、中断恢复
│   ├── gmail_client.py        # 授权、组信、发送、草稿、查回复 / 退信
│   ├── records.py             # 投递记录、查重、统计、Excel 镜像
│   ├── wstasks.py             # 网申待办：状态流转、【网申记录】解析、读回、多岗位
│   ├── agent.py               # 面板里的助手：对话、并行与排队、同站锁、寿命
│   ├── agent_host.py          # 助手宿主：claude 进程脱离面板（面板重启不断）
│   ├── agent_runner.py        # 宿主托管 / 面板直起两种跑法，接口一致
│   ├── upload_guard.py        # 上传钩子：只许传「网申上传」文件夹里的文件
│   ├── uploads.py             # 照片规格自动生成、单页简历
│   ├── wsprofile.py           # 网申底稿（简历以外的个人信息）
│   └── fetch.py               # 公众号 / 网页抓取、图片 OCR、二维码
├── templates/index.html       # 面板界面
├── demo/                      # 在线演示：真实界面 + mock.js 虚构数据和剧本
├── scripts/                   # 启动 / 管理面板、Gmail 授权、生成演示页
└── tests/                     # pytest（含假的 claude 进程 fake_claude.py）
```

## 快速开始

```bash
git clone https://github.com/yihengz23-ai/job-apply.git && cd job-apply
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp candidate_settings.example.json candidate_settings.json
cp application_kit.example.json application_kit.json
cp wangshen_profile.example.json wangshen_profile.json
# 把这几份和 candidate_profile.md、email_rules.md 换成你自己的信息

JOBAPPLY_ENV=test scripts/panel.sh      # 先在测试环境里点点看：5002 端口、假助手、不连 Gmail
.venv/bin/python -m pytest              # 跑测试

# 真用：把 Google Cloud 的 OAuth 桌面应用凭证存为 credentials.json
.venv/bin/python scripts/gmail_auth.py  # 首次授权 Gmail
scripts/panel.sh                        # 打开 http://localhost:5001（面板在后台运行，重复打开不会重启它）
```

写信默认用本机已登录的 Claude Code（`claude -p`）；也可以在 `.env` 里放 `ANTHROPIC_API_KEY` 走 API。网申助手需要 Claude Code + Claude in Chrome 扩展。

## 接下来

- **申请 + 岗位**：一次申请下面挂多个岗位 / 志愿，分清平行和串行（「志愿将按顺序依次流转」），看板一行一次申请；
- **今天页**：只列「轮到你」的事（审信、登录、提交、测评截止），每条一个按钮；
- **进展口述**：一句「收到 A 公司的笔试，周日截止」「B 基金一面面完了」，看板自动更新，可撤销；
- **邮件自动识别**：从来信里认出笔试 / 测评 / AI 面 / 面试邀请、拒信和截止时间，先当建议给本人确认。

## License

MIT — see [LICENSE](LICENSE)

## Author

**Yiheng Zhang（章益恒）** — University of Chicago, Harris School of Public Policy

Built with [Claude Code](https://claude.com/claude-code) + [Claude API](https://www.anthropic.com/)
