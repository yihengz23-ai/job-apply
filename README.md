# Job Application Automation System

> 基于 Claude AI + Gmail API 的求职投递自动化系统，将单次投递耗时从 15-20 分钟压缩至约 10 秒。

**[在线演示 (Interactive Demo)](https://yihengz23-ai.github.io/job-apply/demo/)**

## Features

- **AI 邮件生成** — 粘贴 JD，Claude 自动分析并生成个性化投递邮件（标题、正文、附件命名全部按 JD 要求）
- **微信公众号抓取** — 支持链接自动抓取，含图片 OCR（Claude Vision），自动识别多岗位并选择
- **一键发送** — Gmail API 直接发送，附件与预览完全一致
- **网站投递记录** — 不走邮箱的岗位（飞书/官网）也能记录追踪
- **投递看板** — 全部投递记录可筛选、搜索、编辑状态/来源/行业/备注
- **数据分析** — 投递趋势、状态分布、公司性质/城市/行业分布图表
- **手机适配** — Cloudflare Tunnel 远程访问 + 响应式布局

## Tech Stack

| Component | Technology |
|-----------|-----------|
| AI Engine | Claude API (Sonnet 4.6) |
| Email | Gmail REST API + OAuth 2.0 |
| Backend | Python / Flask |
| Frontend | Tailwind CSS + Vanilla JS |
| Data | JSON (records.json) |
| OCR | Claude Vision API |
| Scraping | BeautifulSoup + lxml |
| Remote Access | Cloudflare Tunnel |

## Quick Start

### Prerequisites

- Python 3.8+
- [Anthropic API Key](https://console.anthropic.com/keys)
- [Google Cloud OAuth Credentials](https://developers.google.com/gmail/api/quickstart/python) (Gmail API)

### Installation

```bash
git clone https://github.com/yihengz23-ai/job-apply.git
cd job-apply

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API key and email

# Set up Gmail OAuth
# Place your credentials.json from Google Cloud Console in the project root
# First run will open browser for authorization

# Run
python app.py
# Open http://localhost:5001
```

### CLI Mode

```bash
# Copy JD to clipboard, then:
python apply.py
```

## Project Structure

```
job-apply/
├── apply.py              # Core: Claude API, Gmail API, URL scraping, OCR
├── app.py                # Flask web server + API endpoints
├── templates/
│   └── index.html        # Web dashboard (SPA)
├── demo/
│   └── index.html        # Interactive demo (no backend needed)
├── candidate_profile.md  # Candidate profile template
├── requirements.txt
├── .env.example          # Environment variables template
├── LICENSE
└── README.md
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/records` | List all application records |
| GET | `/api/stats` | Dashboard statistics |
| POST | `/api/fetch-url` | Scrape URL for job descriptions |
| POST | `/api/analyze` | AI analysis of JD |
| POST | `/api/send` | Send application email |
| POST | `/api/record` | Record non-email application |
| POST | `/api/gmail-sync` | Sync sent emails from Gmail |
| PUT | `/api/records/:id` | Update record fields |
| DELETE | `/api/records/:id` | Delete record |

## How It Works

```
1. Input JD (paste text / URL / screenshot OCR)
      ↓
2. Claude AI analyzes: company, position, requirements
      ↓
3. Generates personalized email (subject, body, attachments)
      ↓
4. User reviews & edits in preview panel
      ↓
5. One-click send via Gmail API
      ↓
6. Auto-saved to records.json → Dashboard
```

## Key Technical Challenges Solved

- **WeChat Article OCR**: Public account articles often embed JDs as images. System scans all image URLs from HTML source, downloads each, and uses Claude Vision to extract text — filtering out ads and navigation by keyword detection.
- **Multi-format Email Parsing**: Handles semicolon-separated recipients, various subject format requirements, and automatic placeholder replacement.
- **Smart Deduplication**: Dedup by email address intersection (handles multi-recipient fields).
- **Responsive Design**: Same HTML serves desktop (sidebar + table) and mobile (bottom nav + card list).

## Security

- API keys stored in `.env` (never committed)
- Gmail OAuth tokens stored locally
- No data leaves your machine except API calls
- Cloudflare Tunnel encrypted (HTTPS)

## License

MIT License — see [LICENSE](LICENSE)

## Author

**YOUR_NAME** — YOUR_UNIVERSITY

Built with [Claude Code](https://claude.ai/code) + [Anthropic API](https://www.anthropic.com/)
