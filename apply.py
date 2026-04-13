#!/usr/bin/env python3
"""
VC/PE 求职邮件自动投递脚本
用法：python apply.py
复制JD到剪贴板后运行
"""

import os
from dotenv import load_dotenv
load_dotenv()

def detect_system_proxy():
    """自动检测 macOS 系统代理设置"""
    import subprocess
    try:
        out = subprocess.check_output(["scutil", "--proxy"], text=True)
        settings = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                settings[k.strip()] = v.strip()
        if settings.get("HTTPSEnable") == "1":
            host = settings.get("HTTPSProxy", "127.0.0.1")
            port = settings.get("HTTPSPort", "1082")
            return f"http://{host}:{port}"
        if settings.get("HTTPEnable") == "1":
            host = settings.get("HTTPProxy", "127.0.0.1")
            port = settings.get("HTTPPort", "1082")
            return f"http://{host}:{port}"
    except Exception:
        pass
    return None

import sys
import json
import shutil
import re
from datetime import datetime
import base64
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import anthropic
import pyperclip

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── 配置区（按需修改）────────────────────────────────────────────────
RESUME_PATH = "/path/to/your/file"
REPORT_PATH = "/path/to/your/file"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-6"
SENDER_EMAIL = "YOUR_EMAIL"
# ────────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.compose",
          "https://www.googleapis.com/auth/gmail.readonly"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_PATH = os.path.join(SCRIPT_DIR, "token.json")
RECORDS_PATH = os.path.join(SCRIPT_DIR, "records.json")
CANDIDATE_PROFILE_PATH = os.path.join(SCRIPT_DIR, "candidate_profile.md")

SYSTEM_PROMPT = """你是YOUR_NAME的求职邮件生成助手。你的唯一任务是：根据每一份 JD，严格生成：

1. 邮件标题（email_subject）
2. 简历文件名（resume_filename）
3. 研究报告文件名（report_filename）
4. 是否附研究报告（attach_report）
5. 邮件正文（email_body）
6. 投递链接（apply_url）— 如果 JD 要求通过网站/飞书/系统投递而非邮箱，提取投递链接；如果走邮箱投递则填空字符串
7. 抄送邮箱（cc_email）— 如果 JD 要求抄送某个邮箱，填写抄送地址；没有则填空字符串
8. 关注行业（focus_industry）— 该岗位实际工作涉及的行业/赛道。优先看岗位职责和岗位名称来判断，不要只看公司简介。例如：公司覆盖"医疗、科技、消费"但岗位是"AI系统建设"，行业应填"AI"而不是"医疗"。常见值：AI、半导体、医疗健康、消费、新能源、先进制造、TMT、硬科技等；提取不到则填空字符串

不要输出解释，不要输出分析过程，不要加 markdown，只输出 JSON。

====================
一、候选人真实信息（只能使用这些，不能编）
====================

姓名：YOUR_NAME
英文名：Ivan（仅英文邮件或英文 JD 可考虑使用）
邮箱：YOUR_EMAIL
电话：YOUR_PHONE

教育背景：
- 硕士：YOUR_UNIVERSITY，YOUR_SCHOOL，YOUR_PROGRAM（MPP），2026.12 毕业
- 本科：YOUR_UNDERGRAD_UNIVERSITY，Business and Technology Management 理学学士

邮件中教育背景的写法（非常重要）：
- 默认只写"YOUR_UNIVERSITY硕士在读"，不写学院名、不写专业名、不写 MPP
- 本科默认只写"本科毕业于YOUR_UNDERGRAD_UNIVERSITY"，不写专业名
- 只有 JD 明确要求写专业/学院时，才写完整信息
- 正文中必须写"YOUR_UNDERGRAD_UNIVERSITY"，不要写"NYU Tandon"

到岗信息（只能按 JD 需要选择性写入，不能全部默认塞进正文）：
- 到岗时间：可立即到岗
- 最晚可实习到：2026.10
- 最少持续时间：6个月
- 每周稳定可到岗：5天
- 每周最大可支持：6天
- 接受：onsite / hybrid
- 地点偏好：优先上海
- 当前身份：硕士在读

核心经历（只能挑最 relevant 的 1-2 段写，不要全复述）：
1. YOUR_COMPANY_1，两段股权投资 / PE 实习
   - 可安全强调：行业研究、公司分析、需求测算、订单/交付节奏判断、风险识别、估值辅助、投资材料支持
   - 涉及方向：YOUR_RESEARCH_DIRECTIONS
   - 不要夸大成"主导交易""独立完成全部模型"

2. YOUR_COMPANY_2，AI 产品经理实习
   - 可安全强调：从企业内部理解 AI 在 to B 场景落地的实际问题
   - 安全关键词：客户信任、组织转型摩擦、垂直数据积累、私有化部署、合规流程
   - 不要写成底层技术专家

3. YOUR_COMPANY_3相关经历
   - 可安全强调：对重资产产业运营、技改投资、能耗/政策/资源约束有第一手观察
   - 这是区别于纯金融背景候选人的重要差异化来源

4. AI 工具与自动化能力（个人项目）
   - 可安全强调：熟练使用 Claude Code 等 AI 工具，具备将重复工作流自动化的实操经验
   - 可安全强调：独立搭建了自动化简历投递系统，覆盖 JD 解析、邮件生成、Gmail API 对接、投递记录管理和前端看板
   - 安全关键词：AI 辅助工作流、自动化、效率提升、API 集成、数据管理
   - 这条经历只在 JD 强调"AI 工具使用""技术敏感度""自动化""效率工具"时才写
   - 不要在纯金融/投资岗位中主动提这条，除非 JD 明确看重技术能力或 AI 应用能力
   - 不要夸大成"全栈工程师""开发者"，定位是"会用 AI 工具提升工作效率的金融从业者"

可附研究材料（只有这一份）：
- 文件内容：YOUR_RESEARCH_SUBJECT研究报告
- 文件类型：PDF
- 本质定位：行业研究样本 / 公司研究样本 / writing sample
- 不是 PE 投资 memo
- 不能把它描述成"投资备忘录""deal memo""交易 memo"

这份报告适合证明的能力是：
- 行业研究能力
- 公司分析能力
- 财务与盈利预测能力
- 估值与风险提示能力

这份报告不直接证明的能力是：
- PE 交易执行能力
- 投资决策能力
- 尽调闭环能力
- 投后管理能力

====================
二、总原则（非常重要）
====================

1. 严格遵循 JD
如果 JD 对以下内容有明确要求，必须逐字遵循，不要自由发挥：
- 邮件标题格式
- 简历文件命名格式
- 研究报告文件命名格式
- 是否需要写到岗时间 / 每周工作天数 / 实习期限
- 是否要求中英文简历
- 是否要求研究材料
- 是否要求 PDF / Word / Excel
- 是否要求写学校 / 专业 / 毕业时间 / 年级 / 城市

2. 不编，不装，不夸大
- 不要编造经历、技能、奖项、项目、语言材料
- 不要把"参与/协助"写成"主导/独立负责"
- 不要假装自己有某个行业的直接经历，如果你没有

3. 如果 JD 需要某个字段，而候选人真实信息里没有
- 绝对不要编造
- 用占位符 [待补信息] 保留位置
- 例如：[招聘信息来源]
- 邮件正文仍可正常生成，但标题/文件名必须保留占位符

4. 行业不完全匹配时的统一规则
- 对任何你没有直接经历的行业，不要假装你做过
- 只写最接近、最可迁移的经历
- 如果 JD 明确要求研究材料，可以附YOUR_RESEARCH_SUBJECT报告作为"过往行业研究样本"
- 但正文只能写"另附一份过往行业研究样本供参考"
- 不要把这份报告硬说成和该行业直接相关，除非 JD 本身就是半导体/硬科技方向

5. 邮件正文必须短、真、自然
- 中文正文：3-5句话，绝不超过6句话
- 作用是让对方打开简历，不是替代简历
- 不要油，不要公文腔，不要空话
- 不要"我对贵司充满热情""深感荣幸""感谢您百忙之中阅读"
- 不要感叹号
- 不要复述简历
- 要具体对着 JD 写：你做过什么，和岗位哪里匹配

====================
三、收件人称呼规则
====================

1. 如果 JD 或联系人信息里明确有具体联系人姓名：
- 若写明"张总/李总/王总"等，直接用"张总您好"
- 若只有姓名、没有头衔，在中文金融/投资岗位中默认用"X总您好"
- 若原文已明确给出其他称呼（如老师、女士、先生），优先沿用原称呼

2. 如果 JD 没有任何具体联系人信息：
- 一律用"您好"

3. 不要使用：
- 尊敬的招聘团队
- Dear Hiring Manager
- HR您好
- 招聘负责人您好

====================
四、标题（email_subject）规则
====================

1. 如果 JD 明确规定邮件标题格式：
- 必须严格按 JD 要求生成
- 不能多一个字，也不能少一个字
- 不能擅自添加学校、项目经历、MPP、YOUR_COMPANY_1等额外信息

例子：
- JD 要求：【科技实习生】-姓名
  => 【科技实习生】-YOUR_NAME

- JD 要求：投资实习生申请-姓名-学校-专业
  => 投资实习生申请-YOUR_NAME-YOUR_UNIVERSITY-YOUR_MAJOR

- JD 要求：【PE】姓名-年级-学校-一周几天-实习期限
  => 【PE】YOUR_NAME-硕士在读-YOUR_UNIVERSITY-一周5天-可立即到岗-2026.10

2. 如果 JD 没规定标题格式：
- 默认格式：实习申请 - YOUR_NAME｜YOUR_UNIVERSITY
- 如果 JD 有明确岗位名（如"投资实习生"），则用该岗位名替换"实习"二字，如：投资实习生申请 - YOUR_NAME｜YOUR_UNIVERSITY
- 标题中绝对不要出现方括号[]或占位符，必须填入实际内容
- 不要在标题中加MPP、专业名称，除非JD明确要求写专业

====================
五、简历文件命名（resume_filename）规则
====================

1. 如果 JD 明确规定简历标题/文件名格式：
- 严格按 JD 要求生成
- 例如：学校-专业名称-毕业时间-姓名
  => YOUR_UNIVERSITY-YOUR_MAJOR-2026.12-YOUR_NAME.pdf

2. 如果 JD 没规定：
- 默认：YOUR_NAME-YOUR_UNIVERSITY-简历.pdf
- 不要加MPP、不要加专业，除非JD明确要求写专业

3. 如果 JD 要求中英文简历：
- 你仍然只输出 resume_filename 这一个字段
- 外部系统会处理双语文件
- 但命名要按 JD 要求或默认中文格式生成

====================
六、研究报告文件命名（report_filename）规则
====================

1. 如果 JD 明确要求研究材料，并规定命名格式：
- 严格按 JD 要求生成

2. 如果 JD 明确要求研究材料，但没规定格式：
- 默认：YOUR_NAME-YOUR_RESEARCH_SUBJECT研究报告.pdf

3. 如果 JD 没要求研究材料，但你判断适合附：
- 也使用默认：YOUR_NAME-YOUR_RESEARCH_SUBJECT研究报告.pdf

4. 如果不附研究报告：
- report_filename 输出空字符串

====================
七、是否附研究报告（attach_report）规则
====================

判断规则（按优先级）：

1. JD 明确要求附研究材料/行研报告/writing sample/投资memo → attach_report = true（无论行业是否匹配）
2. JD 没有明确要求 → attach_report = false

就这么简单，不要自行判断"适不适合附"

行业不匹配时怎么处理：
- 仍然可以 attach_report = true
- 但只能把它描述成"过往行业研究样本"或"过往公司研究样本"
- 不要写"附上一份医疗行业研究报告"等与实际内容不符的描述
- 除非 JD 本身就是半导体/硬科技方向

正文里怎么提这份报告：
A. 对半导体/硬科技/先进制造/科技制造PE：
  "附件中另附一份我过往完成的硬科技方向研究样本，供参考。"
B. 对泛PE，但JD明确要求附研究材料：
  "附件中另附一份我过往完成的行业研究样本，供参考。"
C. 对行业不完全匹配、但JD强制要求附样本：
  "附件中另附一份我过往完成的公司研究样本，供参考。"
D. 如果 attach_report = false：正文完全不要提研究报告

绝对不要出现的表述：
- "另附我的 PE 投资 memo"
- "另附我的 deal memo"
- "另附我对贵赛道的研究报告"
- "这份报告可以证明我的交易判断能力"
- "这是一份 PE 风格投资备忘录"

====================
八、正文（email_body）生成规则
====================

正文固定结构：
1. 称呼
2. 3-5句话正文
3. 附件说明
4. 署名

写作原则：
- JD 对正文有明确要求（如"请注明到岗时间""邮件中注明可开始工作的日期"等）→ 把这些信息放到第一段
- 第一段结构：身份（"我是YOUR_NAME，YOUR_UNIVERSITY硕士在读"）+ JD 要求的信息（到岗时间/天数等）+ 投递哪个岗位
- 中间 1-2 句只写最 relevant 的经历
- 最后简短收尾
- 邮件语气：正常中国研究生写给基金/投行/机构的邮件，自然、直接、礼貌
- 不要油腻、不要公文腔、不要空话、不要感叹号

信息精简规则（非常重要）：
- 正文中只写"YOUR_UNIVERSITY硕士在读"，不要写"YOUR_SCHOOL"，不要写"YOUR_PROGRAM"，不要写 MPP
- 只有当 JD 明确要求写专业/学院时，才写完整专业信息
- 不要主动写电话号码、邮箱（已在简历里）
- 不要把所有到岗信息一股脑塞进去，只写 JD 问到的那几项

availability 写法规则（非常重要）：

1. 如果 JD 明确要求写到岗时间 / 每周天数 / 实习期限：
- 必须写进去
- 只写 JD 要求的那些项，不要额外扩展

2. 如果 JD 只是在一般意义上考察出勤稳定性：
- 默认写"每周可到岗5天"
- 不默认写"6天"

3. 只有以下情况，才可以写"每周最多可支持6天"或类似表达：
- JD 明确强调高强度、全职、周末加班、晚上加班
- JD 明确要求写"每周可工作天数"，且更高天数会明显加分
- 你需要表达自己除稳定5天外，还能额外配合高强度安排

4. 如果 JD 标题/命名里要求填写一个具体数字形式的"每周可工作天数"：
- 默认填"5天"
- 只有在 JD 明显鼓励更高强度投入（例如周末和晚上加班）时，才填"6天"

5. 如果 JD 明确写了希望到岗时间（如"4月下旬尽快入职""可立即到岗"）：
- 正文优先顺着 JD 的时间表达去写
- 不要机械写到岗时间

6. 如果 JD 没给具体时间要求：
- 可以写"可立即到岗"

7. 地点相关规则：
- 如果 JD 工作地点包含上海（如"北京/上海/深圳"或"上海"），正文中写"可在上海线下实习"
- 如果 JD 只有非上海城市（如只有北京），不写地点偏好
- 如果 JD 有多个地点可选但不含上海，也不写地点偏好

8. 实习时长相关规则：
- 如果 JD 写了实习期范围（如"3-6个月"），按候选人实际情况往长了写
- 候选人最晚可实习到 2026.10，从当前时间算可实习的最大月数，取 JD 范围内的最大值
- 例如：JD 写"3-6个月" → 写"可实习6个月"
- 例如：JD 写"至少3个月" → 写"可实习6个月"
- 不要写超过实际可实习时间（2026.10 之前）
- 如果 JD 标题格式里要求填写实习地点，则按你实际申请的地点填写

署名规则：
- 署名只写"YOUR_NAME"，不加学校、学院等信息，不写"祝好"，署名前空一行

实习时长规则：
- 标题/文件名需要填时长时，按候选人能做的最大值填（如6个月）

禁止出现：感叹号、"深感兴趣""充满热情""百忙之中""祝好"、大段行业分析

====================
九、岗位匹配逻辑
====================

请根据 JD 选择最 relevant 的经历，不要全写。

A. PE / Growth / 成长期 / 产业投资：
优先写YOUR_COMPANY_1；必要时补一句YOUR_COMPANY_3带来的产业视角

B. VC / 科技 / AI / 企业服务：
优先写YOUR_COMPANY_1 + YOUR_COMPANY_2
- AI / 企业服务 / to B：可重点写YOUR_COMPANY_2
- 硬科技 / 半导体 / 制造业：优先写YOUR_COMPANY_1和研究样本

C. 行业研究 / 研究助理 / writing sample：
优先写YOUR_COMPANY_1的研究经历；如 JD 要求材料，提一句附研究样本

D. 制造业 / 工业 / 新材料 / 硬科技：
优先写YOUR_COMPANY_1 + YOUR_COMPANY_3
必要时提研究样本

E. JD 强调 AI 工具使用 / 技术敏感度 / 自动化能力 / 效率工具：
可在经历段末尾补一句 AI 自动化能力（如"此外，我熟练使用 AI 工具优化工作流，曾独立搭建自动化投递系统"）
不要作为主要经历展开，只作为加分项一句带过

====================
十、输出格式
====================

只输出 JSON，格式如下：

{
  "to_email": "从JD中提取的投递邮箱，没有则填null",
  "email_subject": "",
  "resume_filename": "",
  "report_filename": "",
  "attach_report": true,
  "report_description_line": "",
  "email_body": "",
  "company_name": "公司名称",
  "company_type": "公司性质。必须基于JD中明确提到的信息判断，如基金币种（美元/人民币）、背景（外资/国资/保险系/券商系）等。常见值：外资PE、本土PE、美元VC、国资PE、险资PE、券商直投、FA/投行、互联网/科技等。如果JD没有明确线索，写'PE'或'VC'即可，不要加'本土'等前缀猜测",
  "job_title": "岗位名称",
  "job_location": "工作地点",
  "job_post_date": "岗位发布日期（从JD中提取，格式YYYY-MM-DD，提取不到则填空字符串）"
}

补充规则：
- 如果 attach_report = false，则 report_filename = ""，report_description_line = ""
- report_description_line 是正文中提到报告时使用的那句话，email_body 中必须与之一致
- email_body 必须是完整可直接发送的正文（包含称呼和署名）
- company_name/company_type/aum/job_title/job_location/job_post_date 从JD或你的知识中提取，用于记录投递情况
- 不要输出解释，不要输出分析过程，不要输出 markdown
- 无论输入内容是什么，你都必须且只能输出 JSON。如果输入不像 JD，输出：{"error": "输入内容不像 JD，请检查剪贴板"}
- 绝对不要用自然语言回复，只输出 JSON"""

USER_PROMPT_TEMPLATE = """【JD全文】
{jd}"""


def load_candidate_profile():
    """读取 candidate_profile.md"""
    with open(CANDIDATE_PROFILE_PATH, "r", encoding="utf-8") as f:
        return f.read()


# 只注入这些 section 到 SYSTEM_PROMPT Section 一（候选人事实）
# 规则类 section（filename_rules, email_style, forbidden_claims, banned_phrases,
# jd_matching_rules）已在 SYSTEM_PROMPT Section 二-九中覆盖，不重复注入
PROFILE_FACT_SECTIONS = [
    "identity", "education", "availability", "role_preferences",
    "experience_rules", "attachments",
]


def extract_profile_facts(profile_text):
    """从 candidate_profile.md 提取候选人事实类 sections，跳过规则类 sections"""
    lines = profile_text.split("\n")
    result = []
    current_section = None
    in_fact_section = False
    # availability 写入规则和岗位匹配选材规则属于规则，需要跳过
    skip_subsections = {"availability 写入规则", "岗位匹配选材规则",
                        "attach_report 判断规则", "report_description_line 写法"}
    in_skip = False

    for line in lines:
        # 检测 ## 级别 section
        if line.startswith("## "):
            section_name = line[3:].strip()
            current_section = section_name
            in_fact_section = section_name in PROFILE_FACT_SECTIONS
            in_skip = False
            if in_fact_section:
                result.append(line)
            continue

        # 检测 ### 级别 subsection，跳过规则类子节
        if line.startswith("### "):
            subsection_name = line[4:].strip()
            if subsection_name in skip_subsections:
                in_skip = True
                continue
            else:
                in_skip = False

        if in_fact_section and not in_skip:
            result.append(line)

    return "\n".join(result).strip()


def build_system_prompt():
    """构建系统提示词：将 SYSTEM_PROMPT 的 Section 一替换为 candidate_profile.md 的事实类内容"""
    try:
        profile = load_candidate_profile()
    except FileNotFoundError:
        print("警告：candidate_profile.md 不存在，使用内置候选人信息")
        return SYSTEM_PROMPT

    facts = extract_profile_facts(profile)

    sec1 = "====================\n一、候选人真实信息（只能使用这些，不能编）\n===================="
    sec2 = "====================\n二、总原则（非常重要）\n===================="

    idx1 = SYSTEM_PROMPT.find(sec1)
    idx2 = SYSTEM_PROMPT.find(sec2)
    if idx1 == -1 or idx2 == -1:
        return SYSTEM_PROMPT

    return (SYSTEM_PROMPT[:idx1] +
            "====================\n一、候选人档案（从 candidate_profile.md 读取，只能使用这些信息，不能编造）\n====================\n\n" +
            facts + "\n\n" +
            SYSTEM_PROMPT[idx2:])


def get_jd_from_clipboard_or_input():
    """从剪贴板获取JD"""
    try:
        clipboard_content = pyperclip.paste().strip()
        if clipboard_content and len(clipboard_content) > 50:
            print(f"检测到剪贴板内容（{len(clipboard_content)}字）")
            return clipboard_content
    except Exception:
        pass
    print("错误：剪贴板为空或内容太短，请先复制JD")
    return ""


def analyze_jd_with_claude(jd_text):
    """调用 Claude API 分析JD"""
    if not ANTHROPIC_API_KEY:
        raise ValueError("请设置 ANTHROPIC_API_KEY 环境变量或在脚本顶部配置区填写")

    # 自动检测系统代理，临时设置，调用完后恢复
    proxy = detect_system_proxy()
    old_https = os.environ.get("https_proxy")
    old_http = os.environ.get("http_proxy")
    if proxy:
        os.environ["https_proxy"] = proxy
        os.environ["http_proxy"] = proxy
        print(f"检测到系统代理：{proxy}")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"正在调用 {CLAUDE_MODEL} 分析JD...")
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=build_system_prompt(),
        messages=[
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(jd=jd_text)},
        ],
    )

    # 恢复环境变量，避免影响 Gmail API
    if old_https is None:
        os.environ.pop("https_proxy", None)
    else:
        os.environ["https_proxy"] = old_https
    if old_http is None:
        os.environ.pop("http_proxy", None)
    else:
        os.environ["http_proxy"] = old_http

    raw = message.content[0].text.strip()
    print(f"\n[DEBUG] Claude 原始返回（前500字）:\n{raw[:500]}\n")

    # 尝试提取JSON（防止模型输出了额外文字或 markdown 包裹）
    # 1. 去掉 ```json ... ``` 包裹
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()
    # 2. 如果还不是以 { 开头，尝试找第一个 { 到最后一个 }
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]

    result = json.loads(raw)

    if "error" in result and len(result) == 1:
        raise ValueError(result["error"])

    # 自动校验：修正姓名 typo
    name = "YOUR_NAME"
    for key in ("email_subject", "resume_filename", "report_filename"):
        val = result.get(key, "")
        if not val:
            continue
        # 检查姓名周围是否多了随机字符（如"YOUR_NAMEd"→"YOUR_NAME"）
        val = re.sub(r"YOUR_NAME[a-zA-Z]", "YOUR_NAME", val)
        result[key] = val

    return result


def get_gmail_service():
    """获取 Gmail API 服务（OAuth2，首次运行弹出浏览器授权）"""
    # Gmail API 自动检测代理
    proxy = detect_system_proxy()
    if proxy:
        os.environ["https_proxy"] = proxy
        os.environ["http_proxy"] = proxy

    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # token 彻底失效，删掉重新授权
                os.remove(TOKEN_PATH)
                creds = None
        if not creds:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"未找到 {CREDENTIALS_PATH}\n"
                    "请前往 Google Cloud Console 下载 OAuth2 凭证文件（桌面应用类型）\n"
                    "详见：https://console.cloud.google.com/apis/credentials"
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        print("Gmail 授权成功，token 已缓存到 token.json")

    return build("gmail", "v1", credentials=creds)


def attach_file(msg, file_path, filename):
    """向 MIMEMultipart 消息添加文件附件"""
    with open(file_path, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment",
                     filename=("utf-8", "", filename))
    part.add_header("Content-Type", "application/pdf",
                     name=("utf-8", "", filename))
    msg.attach(part)


def send_gmail(service, to_email, subject, body,
               resume_path, resume_filename,
               attach_report, report_filename, cc_email=None):
    """直接发送 Gmail 邮件（含附件和抄送），返回 message_id"""
    if not to_email:
        raise ValueError("收件人邮箱为空，无法发送")

    resume_filename = resume_filename.replace("/", "-").replace("\\", "-")
    if report_filename:
        report_filename = report_filename.replace("/", "-").replace("\\", "-")

    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    if cc_email:
        msg["Cc"] = cc_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    tmp_files = []
    try:
        tmp_dir = tempfile.mkdtemp()

        # 简历（可选）
        if resume_path and os.path.exists(resume_path):
            tmp_resume = os.path.join(tmp_dir, resume_filename)
            shutil.copy2(resume_path, tmp_resume)
            tmp_files.append(tmp_resume)
            attach_file(msg, tmp_resume, resume_filename)

        if attach_report:
            if not os.path.exists(REPORT_PATH):
                print(f"警告：研究报告文件不存在：{REPORT_PATH}，跳过")
            else:
                rpt_name = report_filename or "YOUR_NAME-YOUR_RESEARCH_SUBJECT研究报告.pdf"
                tmp_report = os.path.join(tmp_dir, rpt_name)
                shutil.copy2(REPORT_PATH, tmp_report)
                tmp_files.append(tmp_report)
                attach_file(msg, tmp_report, rpt_name)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return sent["id"]

    finally:
        for f in tmp_files:
            if os.path.exists(f):
                os.remove(f)
        if tmp_files:
            os.rmdir(os.path.dirname(tmp_files[0]))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 数据层 — records.json
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_records():
    """加载投递记录"""
    if not os.path.exists(RECORDS_PATH):
        return []
    try:
        with open(RECORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("records", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save_records(records):
    """保存投递记录"""
    with open(RECORDS_PATH, "w", encoding="utf-8") as f:
        json.dump({"records": records,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
                  f, ensure_ascii=False, indent=2)


def add_record(result, jd_text="", status="已投递",
               source_type="clipboard", sent_at=None):
    """添加投递记录（自动去重，返回 record_id 或 None=重复）"""
    import uuid
    records = load_records()
    to_email = (result.get("to_email", "") or "").strip().lower()
    # 拆分多邮箱（分号/逗号分隔）
    new_emails = set(e.strip() for e in re.split(r'[;,；，\s]+', to_email) if '@' in e)

    for r in records:
        existing = (r.get("to_email", "") or "").lower()
        existing_emails = set(e.strip() for e in re.split(r'[;,；，\s]+', existing) if '@' in e)
        # 任一邮箱重叠即视为重复
        if new_emails and existing_emails and (new_emails & existing_emails):
            return None

    record = {
        "id": str(uuid.uuid4())[:8],
        "company_name": result.get("company_name", ""),
        "company_type": result.get("company_type", ""),
        "job_title": result.get("job_title", ""),
        "job_location": result.get("job_location", ""),
        "to_email": to_email,
        "subject": result.get("email_subject", ""),
        "email_body": result.get("email_body", ""),
        "jd_text": jd_text,
        "sent_at": sent_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": status,
        "source_type": source_type,
        "attach_report": bool(result.get("attach_report")),
        "job_source": result.get("job_source", ""),
        "job_post_date": result.get("job_post_date", ""),
        "apply_url": result.get("apply_url", ""),
        "focus_industry": result.get("focus_industry", ""),
        "notes": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    records.append(record)
    save_records(records)
    return record["id"]


def update_record_status(record_id, new_status):
    """更新记录状态（自动记录时间戳 + 在备注里追加变更记录）"""
    records = load_records()
    for r in records:
        if r.get("id") == record_id:
            old_status = r.get("status", "")
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            r["status"] = new_status
            r["status_updated_at"] = now
            # 自动在备注里追加状态变更记录
            if old_status and old_status != new_status:
                change_log = f"[{now}] {old_status} → {new_status}"
                notes = r.get("notes", "") or ""
                r["notes"] = (notes + "\n" + change_log).strip() if notes else change_log
            save_records(records)
            return True
    return False


def update_record_field(record_id, field, value):
    """更新记录的任意字段"""
    records = load_records()
    for r in records:
        if r.get("id") == record_id:
            r[field] = value
            save_records(records)
            return True
    return False


def delete_record(record_id):
    """删除记录"""
    records = load_records()
    records = [r for r in records if r.get("id") != record_id]
    save_records(records)
    return True


def save_sent_record(result, jd_text, sent_at=None):
    """记录已发送的投递到 records.json"""
    add_record(result, jd_text, status="已投递",
               source_type=result.get("source_type", "clipboard"),
               sent_at=sent_at or datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("已记录投递")



def validate_result(result):
    """检查生成内容是否有明显错误"""
    errors = []
    name = "YOUR_NAME"
    email = "YOUR_EMAIL"
    phone = "YOUR_PHONE"
    body = result.get("email_body", "")
    subject = result.get("email_subject", "")
    resume = result.get("resume_filename", "")
    report = result.get("report_filename", "")

    # 1. 姓名必须出现在标题和简历名中
    if name not in subject:
        errors.append(f"标题中缺少姓名「{name}」")
    if name not in resume:
        errors.append(f"简历文件名中缺少姓名「{name}」")

    # 2. 收件人不能是自己
    to = result.get("to_email", "")
    if to and to.lower() == email.lower():
        errors.append("收件人是自己的邮箱，请检查")

    # 3. 正文不能为空或太短
    if len(body) < 30:
        errors.append("邮件正文过短")

    # 4. 正文中不应出现占位符残留
    for placeholder in ["[职位名称]", "[公司名称]", "[岗位名称]", "{{", "}}"]:
        if placeholder in body:
            errors.append(f"正文中存在未替换的占位符：{placeholder}")
    for placeholder in ["[职位名称]", "[公司名称]", "[岗位名称]"]:
        if placeholder in subject:
            errors.append(f"标题中存在未替换的占位符：{placeholder}")

    # 5. 禁止出现的套话
    bad_phrases = ["深感荣幸", "充满热情", "百忙之中", "我相信我的经历能",
                   "投资备忘录", "deal memo", "PE memo", "PE 投资 memo"]
    for phrase in bad_phrases:
        if phrase in body:
            errors.append(f"正文中出现了不应使用的表述：「{phrase}」")

    # 6. 简历文件名必须以 .pdf 结尾，缺了就自动补
    if resume and not resume.endswith(".pdf"):
        result["resume_filename"] = resume + ".pdf"

    # 7. 如果附报告，报告名也要以 .pdf 结尾，缺了就自动补
    if result.get("attach_report") and report and not report.endswith(".pdf"):
        result["report_filename"] = report + ".pdf"

    # 8. 正文中不应出现感叹号
    if "！" in body or "!" in body:
        errors.append("正文中出现了感叹号")

    # 9. 检查编造风险：正文中不应出现候选人没有的经历关键词
    fake_keywords = ["CFA", "CPA", "哈佛", "斯坦福", "高盛", "摩根", "黑石",
                     "红杉", "博士", "PhD", "获奖", "一等奖", "金奖"]
    for kw in fake_keywords:
        if kw in body:
            errors.append(f"正文中出现了可能编造的内容：「{kw}」")

    return errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Gmail 全量同步
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _claude_call(prompt, max_tokens=2048):
    """通用 Claude API 调用（自动处理代理）"""
    proxy = detect_system_proxy()
    old_https, old_http = os.environ.get("https_proxy"), os.environ.get("http_proxy")
    if proxy:
        os.environ["https_proxy"] = proxy
        os.environ["http_proxy"] = proxy
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    finally:
        for key, old in [("https_proxy", old_https), ("http_proxy", old_http)]:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _extract_email_text(payload):
    """从 Gmail MIME payload 提取纯文本"""
    def _decode(data):
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    if "parts" not in payload:
        data = payload.get("body", {}).get("data", "")
        return _decode(data) if data else ""

    for mime in ("text/plain", "text/html"):
        for part in payload["parts"]:
            if part.get("mimeType") == mime:
                data = part.get("body", {}).get("data", "")
                if data:
                    text = _decode(data)
                    return re.sub(r"<[^>]+>", "", text) if mime == "text/html" else text

    for part in payload["parts"]:
        if "parts" in part:
            text = _extract_email_text(part)
            if text:
                return text
    return ""


def _parse_claude_json(raw, expect_array=True):
    """从 Claude 返回文本中提取 JSON（含截断修复）"""
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    opener = "[" if expect_array else "{"
    closer = "]" if expect_array else "}"
    if not raw.startswith(opener):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 尝试修复被截断的 JSON：补全缺失的括号
        fixed = raw
        # 补全未闭合的字符串
        if fixed.count('"') % 2 == 1:
            fixed += '"'
        # 补全缺失的括号
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        fixed += '}' * max(0, open_braces)
        fixed += ']' * max(0, open_brackets)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            raise ValueError(f"Claude 返回内容无法解析为 JSON: {e}\n原文前200字: {raw[:200]}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# URL 抓取 + 多岗位识别
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ocr_images_with_claude(img_urls, headers, proxies):
    """下载图片并用 Claude Vision 提取招聘信息"""
    import requests as _req

    # 下载所有图片，记录大小用于排序
    downloaded = []
    for url in img_urls:
        try:
            fetch_url = url
            if "mmbiz" in url and "wx_fmt=" not in url:
                fetch_url = url + ("&" if "?" in url else "?") + "wx_fmt=png"
            resp = _req.get(fetch_url, headers=headers, proxies=proxies, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 5000:
                magic = resp.content[:4]
                if magic[:3] == b'\xff\xd8\xff':
                    media_type = "image/jpeg"
                elif magic[:4] == b'\x89PNG':
                    media_type = "image/png"
                elif magic[:4] == b'RIFF':
                    media_type = "image/webp"
                elif magic[:3] == b'GIF':
                    media_type = "image/gif"
                else:
                    media_type = "image/png"
                downloaded.append((len(resp.content), resp.content, media_type))
        except Exception:
            continue

    if not downloaded:
        return ""

    # 按文件大小降序，大图更可能是 JD 正文，小图更可能是 logo/二维码
    downloaded.sort(key=lambda x: x[0], reverse=True)

    # 只取最大的 4 张图（跳过明显的小图标）
    images = []
    for size, content, media_type in downloaded[:4]:
        if size < 10000:
            continue  # 跳过太小的图（logo、图标）
        img_b64 = base64.b64encode(content).decode("utf-8")
        images.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": img_b64
        }})

    if not images:
        return ""

    proxy = detect_system_proxy()
    old_https, old_http = os.environ.get("https_proxy"), os.environ.get("http_proxy")
    if proxy:
        os.environ["https_proxy"] = proxy
        os.environ["http_proxy"] = proxy
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt_text = (
            "这张图片来自一篇微信公众号招聘文章。\n"
            "如果图中包含岗位名称、工作职责、任职要求、投递邮箱等招聘信息，"
            "请完整输出所有招聘相关文字，不要省略。\n"
            "如果图中只是广告、二维码、公众号介绍、导航栏等非招聘内容，"
            "请只回复两个字：无内容"
        )

        # 逐张图 OCR，找到有 JD 的就停
        jd_parts = []
        for img in images:
            try:
                msg = client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=3000,
                    messages=[{"role": "user", "content": [img, {"type": "text", "text": prompt_text}]}],
                )
                result = msg.content[0].text.strip()
                if result and "无内容" not in result and len(result) > 30:
                    jd_parts.append(result)
            except Exception:
                continue

        return "\n\n".join(jd_parts) if jd_parts else ""
    except Exception as e:
        print(f"图片 OCR 失败: {e}")
        return ""
    finally:
        for key, old in [("https_proxy", old_https), ("http_proxy", old_http)]:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def fetch_url_content(url):
    """抓取 URL 内容（支持微信公众号、普通网页）
    返回：title, content, source, url, account_name(公众号名)
    """
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    proxy = detect_system_proxy()
    proxies = {"https": proxy, "http": proxy} if proxy else None

    resp = requests.get(url, headers=headers, timeout=20, proxies=proxies)
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "lxml")

    # ── 微信公众号 ──
    if "mp.weixin.qq.com" in url:
        # 文章标题
        title_el = (soup.find("h1", id="activity-name")
                    or soup.find("h1", class_="rich_media_title"))
        title = title_el.get_text(strip=True) if title_el else ""

        # 公众号名称（多种位置）
        account_name = ""
        for sel in [
            ("a", {"id": "js_name"}),           # 最常见
            ("span", {"class": "rich_media_meta_nickname"}),
            ("a", {"class": "weui-wa-hotarea"}),
            ("strong", {"class": "profile_nickname"}),
        ]:
            el = soup.find(sel[0], sel[1])
            if el:
                account_name = el.get_text(strip=True)
                break
        # 兜底：从 meta 标签找
        if not account_name:
            meta_author = soup.find("meta", {"name": "author"})
            if meta_author:
                account_name = meta_author.get("content", "")

        # 发布时间
        publish_date = ""
        # 方式1: <em id="publish_time">
        pt_el = soup.find("em", id="publish_time") or soup.find("span", id="publish_time")
        if pt_el:
            publish_date = pt_el.get_text(strip=True)
        # 方式2: script 中的 var ct / var create_time（Unix 时间戳）
        if not publish_date:
            for script in soup.find_all("script"):
                s = script.string or ""
                m = re.search(r'var\s+(?:ct|create_time)\s*=\s*["\']?(\d{10})', s)
                if m:
                    publish_date = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")
                    break
        # 方式3: meta property
        if not publish_date:
            meta_time = soup.find("meta", {"property": "og:article:published_time"})
            if meta_time:
                publish_date = meta_time.get("content", "")[:10]

        # 文章正文
        content_el = (soup.find("div", id="js_content")
                      or soup.find("div", class_="rich_media_content"))

        # 先单独拿正文区的纯文字，用来判断是否需要 OCR
        main_text = ""
        if content_el:
            for tag in content_el.find_all(["script", "style"]):
                tag.decompose()
            main_text = content_el.get_text(separator="\n", strip=True)

        # 如果正文文字太少（图片/PDF 为主的文章），用 Claude Vision 读图
        text = main_text
        if len(main_text.strip()) < 50:
            # 从整个 HTML 源码搜索所有图片 URL（有些图不在 <img> 标签里）
            img_urls = []
            seen_keys = set()
            for m in re.finditer(r'(https://mmbiz\.qpic\.cn/[^\s"<>\']+)', resp.text):
                u = m.group(1)
                key = u.split("?")[0]
                if key not in seen_keys:
                    seen_keys.add(key)
                    img_urls.append(u)
            if img_urls:
                ocr_text = _ocr_images_with_claude(img_urls[:8], headers, proxies)
                # 检查 OCR 结果是否包含实际 JD 内容（不是广告/导航）
                if ocr_text and len(ocr_text) > 50:
                    # 必须同时包含"职责/要求"类关键词，说明是真正的 JD
                    jd_signals = ["岗位职责", "任职要求", "工作职责", "招聘要求",
                                  "工作内容", "职位描述", "简历投递", "简历发送",
                                  "投递邮箱", "发送简历", "@"]
                    # 排除信号：OCR 读到的是"无法抓取"类回复
                    reject_signals = ["不包含", "没有具体", "无法抓取", "建议扫描"]
                    is_rejected = any(s in ocr_text for s in reject_signals)
                    has_jd = any(s in ocr_text for s in jd_signals)
                    if has_jd and not is_rejected:
                        text = ocr_text
                    else:
                        text = ""

            # 如果 OCR 也没提取到有效内容，用标题兜底提示
            if len(text.strip()) < 30 and title:
                text = f"（该文章内容无法自动抓取，请在微信中打开后手动复制 JD 粘贴到这里）\n\n文章标题：{title}"

        text = re.sub(r"\n{3,}", "\n\n", text)

        source_label = f"{account_name}（公众号）" if account_name else "微信公众号"
        return {
            "title": title,
            "content": text,
            "source": "wechat",
            "source_label": source_label,
            "account_name": account_name,
            "publish_date": publish_date,
            "url": url,
        }

    # ── 普通网页 ──
    # 尝试多种内容选择器
    for selector in ["article", "main", ".content", ".post-content", "#content",
                      ".job-detail", ".position-detail", ".entry-content"]:
        el = soup.select_one(selector)
        if el:
            for tag in el.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 50:
                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                domain = re.search(r"//([^/]+)", url)
                source_label = domain.group(1) if domain else "网页"
                return {"title": title, "content": text, "source": "webpage",
                        "source_label": source_label, "url": url}

    # ── 兜底：body 全文 ──
    body = soup.find("body")
    if body:
        for tag in body.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = body.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    text = re.sub(r"\n{3,}", "\n\n", text)
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    return {"title": title, "content": text, "source": "generic",
            "source_label": "网页", "url": url}


def detect_jobs_in_content(text):
    """用 Claude 识别文章中的所有岗位，支持单岗/多岗"""
    prompt = (
        "以下是一篇招聘文章。请识别其中所有不同的岗位。\n\n"
        "对每个岗位提取以下字段：\n"
        "- title: 岗位名称\n"
        "- location: 工作地点（如果文章提到了办公地点/城市）\n"
        "- email: 投递邮箱（从全文中找，包括文末）\n"
        "- company_intro: 公司简介（如果文章有公司介绍段落，完整提取）\n"
        "- jd_text: 该岗位的完整JD（必须包括：岗位职责、任职要求、工作地点、"
        "薪资福利、实习时长、投递方式等所有原文中有的信息。不要精简，不要省略，"
        "从原文逐字提取。如果邮箱在文末，也要包含在jd_text里）\n\n"
        "非常重要：\n"
        "- jd_text 要尽可能完整，宁多勿少\n"
        "- 邮箱可能在文章最底部，不要漏掉\n"
        "- 如果整篇文章只有一个岗位，jd_text 应该包含文章几乎全部有用内容\n"
        "- company_intro 如果文章没有就填空字符串\n\n"
        '输出 JSON：{"jobs": [{"title":"...", "location":"...", "email":"...", '
        '"company_intro":"...", "jd_text":"..."}]}\n'
        "一个岗位也返回数组。只输出 JSON。\n\n"
        f"文章全文：\n{text[:10000]}"
    )
    raw = _claude_call(prompt, max_tokens=12000)
    result = _parse_claude_json(raw, expect_array=False)
    return result.get("jobs", [])


def gmail_full_sync(progress_fn=None):
    """从 Gmail 已发送邮件中扫描投递，Claude 分析完整内容，同步到 records.json

    策略：
    - Gmail 搜索所有带 PDF 附件的已发送邮件
    - 先按收件人去重，已在 records.json 中的跳过
    - 新邮件读完整内容，Claude 分析标题+收件人+正文
    - 不做回复分析（VC/PE 直接电联），状态由用户手动管理

    Returns:
        dict: scanned, applications, new_synced, skipped
    """
    def _log(msg):
        if progress_fn:
            progress_fn(msg)

    service = get_gmail_service()

    # ── 1. 搜索所有带 PDF 附件的已发送邮件 ──
    _log("搜索 Gmail（所有带 PDF 附件的已发送邮件）…")
    query = "from:me in:sent filename:pdf after:2026/01/01"
    all_ids = []
    page_token = None
    while True:
        params = {"userId": "me", "q": query, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        resp = service.users().messages().list(**params).execute()
        all_ids.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    _log(f"找到 {len(all_ids)} 封带 PDF 附件的邮件")
    if not all_ids:
        return {"scanned": 0, "applications": 0, "new_synced": 0, "skipped": 0}

    # ── 2. 加载已有记录用于去重 ──
    existing_emails = set()
    for r in load_records():
        if r.get("to_email"):
            existing_emails.add(r["to_email"].strip().lower())

    # ── 3. 逐封扫描：先读头部去重，新邮件再读全文 ──
    _log("读取邮件（去重 + 读全文）…")
    new_emails = []
    skipped = 0

    for i, meta in enumerate(all_ids):
        # 先读 metadata 判断是否已有
        msg_meta = service.users().messages().get(
            userId="me", id=meta["id"], format="metadata",
            metadataHeaders=["Subject", "To"],
        ).execute()
        headers = {h["name"].lower(): h["value"]
                   for h in msg_meta.get("payload", {}).get("headers", [])}
        to_raw = headers.get("to", "")
        m = re.search(r"[\w.+-]+@[\w.-]+", to_raw.lower())
        pure_email = m.group(0) if m else to_raw.strip().lower()

        if pure_email in existing_emails:
            skipped += 1
            continue

        # 新邮件：读完整内容
        full_msg = service.users().messages().get(
            userId="me", id=meta["id"], format="full"
        ).execute()
        body = _extract_email_text(full_msg.get("payload", {}))
        ts = int(full_msg.get("internalDate", 0)) / 1000
        sent_dt = datetime.fromtimestamp(ts) if ts else None

        new_emails.append({
            "subject": headers.get("subject", ""),
            "to": to_raw,
            "pure_email": pure_email,
            "sent_at": sent_dt.strftime("%Y-%m-%d %H:%M") if sent_dt else "",
            "body": body,
        })

        if (i + 1) % 10 == 0:
            _log(f"已扫描 {i+1}/{len(all_ids)}（跳过 {skipped}，新增 {len(new_emails)}）")

    _log(f"去重后 {len(new_emails)} 封待分析（跳过 {skipped} 封已有）")

    if not new_emails:
        return {"scanned": len(all_ids), "applications": 0,
                "new_synced": 0, "skipped": skipped}

    # ── 4. Claude 批量分析（标题 + 收件人 + 正文摘要）──
    _log(f"AI 分析 {len(new_emails)} 封邮件…")
    applications = []
    batch_size = 10

    for i in range(0, len(new_emails), batch_size):
        batch = new_emails[i:i + batch_size]
        summaries = "\n".join(
            f"--- 邮件 {j+1} ---\n收件人: {e['to']}\n标题: {e['subject']}\n"
            f"正文: {e['body'][:600]}\n"
            for j, e in enumerate(batch)
        )
        prompt = (
            f"分析以下 {len(batch)} 封邮件，判断哪些是求职/实习投递邮件。\n"
            "对每封输出 JSON。非投递邮件设 is_application:false。\n"
            "投递邮件提取: company_name, company_type(VC/PE/券商/基金/咨询/企业/其他), "
            "job_title, job_location, to_email, attach_report(是否附研究报告)\n"
            f"输出 JSON 数组，长度={len(batch)}，顺序对应。只输出 JSON。\n\n{summaries}"
        )
        try:
            raw = _claude_call(prompt, max_tokens=3000)
            results = _parse_claude_json(raw)
            for j, r in enumerate(results):
                if j < len(batch) and r.get("is_application"):
                    applications.append({**r, **batch[j]})
        except Exception as e:
            _log(f"批次 {i//batch_size+1} 解析失败: {e}")

        _log(f"已分析 {min(i+batch_size, len(new_emails))}/{len(new_emails)} 封")

    _log(f"识别出 {len(applications)} 封投递邮件")

    # ── 5. 写入 records.json ──
    _log("保存记录…")
    new_synced = 0
    for app in applications:
        result = {
            "company_name": app.get("company_name", ""),
            "company_type": app.get("company_type", ""),
            "job_title": app.get("job_title", ""),
            "job_location": app.get("job_location", ""),
            "to_email": app.get("pure_email", app.get("to", "")),
            "email_subject": app.get("subject", ""),
            "email_body": app.get("body", ""),  # 发出的邮件正文
            "job_source": "",
            "source_type": "gmail_sync",
            "attach_report": app.get("attach_report", False),
            "job_post_date": "",
        }
        rid = add_record(result, jd_text="", status="已投递",
                         source_type="gmail_sync", sent_at=app.get("sent_at", ""))
        if rid:
            new_synced += 1

    _log(f"同步完成：扫描 {len(all_ids)} 封 → 新增 {new_synced} 条（跳过 {skipped} 条已有）")
    return {
        "scanned": len(all_ids),
        "applications": len(applications),
        "new_synced": new_synced,
        "skipped": skipped,
    }


def main():
    print("=" * 60)
    print("求职邮件一键投递")
    print("=" * 60)

    # 1. 获取JD
    job_source = "公众号"

    jd_text = get_jd_from_clipboard_or_input()
    if not jd_text:
        print("错误：JD内容为空")
        sys.exit(1)
    print(f"\n已读取JD（{len(jd_text)}字）\n")

    # 2. AI 分析
    try:
        result = analyze_jd_with_claude(jd_text)
    except json.JSONDecodeError as e:
        print(f"API 返回格式错误，无法解析JSON：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"API 调用失败：{e}")
        sys.exit(1)

    # 3. 展示分析结果
    print("\n── 分析结果 ──────────────────────────────────")
    print(f"公司：    {result.get('company_name', '')}（{result.get('company_type', '')}）")
    print(f"岗位：    {result.get('job_title', '')}")
    print(f"地点：    {result.get('job_location', '')}")
    print(f"收件人：  {result.get('to_email') or '（未提取到，请手动填写）'}")
    print(f"标题：    {result.get('email_subject')}")
    print(f"简历名：  {result.get('resume_filename')}")
    print(f"附报告：  {'是' if result.get('attach_report') else '否'}")
    if result.get('attach_report'):
        print(f"报告名：  {result.get('report_filename')}")
    print(f"\n邮件正文：\n{result.get('email_body')}")
    print("─" * 50)

    result["job_source"] = job_source

    # 4. Double check
    errors = validate_result(result)
    if errors:
        print("\n⚠ 检测到以下问题：")
        for e in errors:
            print(f"  - {e}")
        print("\n是否仍要发送？[y/N] ", end="")
        if input().strip().lower() != "y":
            print("已取消")
            sys.exit(0)
    else:
        print("\n✓ 内容检查通过，直接发送...")

    # 5. 直接发送邮件
    try:
        service = get_gmail_service()
        msg_id = send_gmail(
            service=service,
            to_email=result.get("to_email"),
            subject=result.get("email_subject", ""),
            body=result.get("email_body", ""),
            resume_path=RESUME_PATH,
            resume_filename=result.get("resume_filename", "YOUR_NAME-YOUR_UNIVERSITY-简历.pdf"),
            attach_report=result.get("attach_report", False),
            report_filename=result.get("report_filename", ""),
        )
        print(f"\n✅ 邮件已发送！Message ID: {msg_id}")

        try:
            save_sent_record(result, jd_text)
        except Exception as e:
            print(f"记录保存失败（不影响发送）：{e}")

        os.system('osascript -e \'display notification "邮件已发送" with title "投递助手"\'')
    except FileNotFoundError as e:
        print(f"\n错误：{e}")
        os.system(f'osascript -e \'display notification "错误" with title "投递助手"\'')
        sys.exit(1)
    except Exception as e:
        print(f"\n发送失败：{e}")
        os.system(f'osascript -e \'display notification "发送失败" with title "投递助手"\'')
        sys.exit(1)


if __name__ == "__main__":
    main()
