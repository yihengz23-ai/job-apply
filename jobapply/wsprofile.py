"""网申底稿（wangshen_profile.json）：网申表格会问、简历上没有的个人信息（出生日期、户口、家庭、高中、偏好……）。

简历上的经历、学历、技能在 application_kit.json（和简历逐字一致）；这里只放简历以外的。
证件号码一律不存（每次本人自己填）：保存时发现像身份证号 / 银行卡号的数字就拒绝。"""

import copy
import json
import os
import re
import shutil
import tempfile
from datetime import datetime

from . import config

PATH = config.DATA_DIR / "wangshen_profile.json"
EXAMPLE = config.BASE_DIR / "wangshen_profile.example.json"
KIT_PATH = config.BASE_DIR / "application_kit.json"
KIT_EXAMPLE = config.BASE_DIR / "application_kit.example.json"
SECTIONS = ("基本信息", "联系方式", "高中", "家庭成员", "经历精确日期", "项目经历", "资格与考试", "求职偏好与声明", "其他")
LONG_NUMBER = re.compile(r"(?<![\dA-Za-z])\d{15,19}[Xx]?(?![\dA-Za-z])")   # 身份证号 / 银行卡号
KEEP_BACKUPS = 20


class Invalid(ValueError):
    pass


def _read(path, example):
    p = path if path.exists() else example
    if not p.exists():
        return {}, True
    return json.loads(p.read_text(encoding="utf-8")), p == example


def load():
    """(底稿, 是否示例)。还没建自己的底稿时返回示例。"""
    return _read(PATH, EXAMPLE)


def load_kit():
    return _read(KIT_PATH, KIT_EXAMPLE)[0]


def missing(profile):
    """还空着的栏目（「家庭成员：父亲的出生年月」这种说法）。"""
    out = []
    for sec in SECTIONS:
        for item in profile.get(sec) or []:
            if isinstance(item, list) and len(item) == 2 and not str(item[1]).strip():
                out.append(f"{sec}：{item[0]}")
            elif isinstance(item, dict):
                who = item.get("关系") or item.get("姓名") or ""
                out += [f"{sec}：{who}的{k}" for k, v in item.items() if not str(v).strip()]
    return out


def _clean_str(v, where):
    if not isinstance(v, str):
        raise Invalid(f"{where} 的内容格式不对")
    v = v.strip()
    if LONG_NUMBER.search(v):
        raise Invalid(f"{where} 里像是身份证号或银行卡号：这类号码底稿里不存，网申时你自己填。")
    if len(v) > 3000:
        raise Invalid(f"{where} 太长了（超过 3000 字）")
    return v


def validate(profile):
    """只认识的几段、每段是 [栏目, 内容] 或 {栏目: 内容}（家庭成员）；返回整理好的底稿。"""
    if not isinstance(profile, dict):
        raise Invalid("底稿格式不对")
    out = {"_说明": str(profile.get("_说明") or "")}
    for sec in SECTIONS:
        items = profile.get(sec)
        if items is None:
            continue
        if not isinstance(items, list):
            raise Invalid(f"「{sec}」格式不对")
        clean = []
        for item in items:
            if isinstance(item, list) and len(item) == 2:
                k = _clean_str(item[0], sec)
                if k:
                    clean.append([k, _clean_str(item[1], f"{sec}：{k}")])
            elif isinstance(item, dict):
                clean.append({_clean_str(k, sec): _clean_str(v, f"{sec}：{k}") for k, v in item.items() if str(k).strip()})
            else:
                raise Invalid(f"「{sec}」里有一项格式不对")
        out[sec] = clean
    notes = profile.get("_核对提示") or []
    out["_核对提示"] = [str(n).strip() for n in notes if str(n).strip()] if isinstance(notes, list) else []
    return out


def save(profile):
    clean = validate(profile)
    if PATH.exists():
        config.BACKUP_DIR.mkdir(exist_ok=True)
        shutil.copy2(PATH, config.BACKUP_DIR / f"wangshen_profile-{datetime.now():%Y%m%d-%H%M%S}.json")
        for old in sorted(config.BACKUP_DIR.glob("wangshen_profile-*.json"))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PATH.parent, prefix=".wsprofile-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return clean


def _cells(profile):
    """底稿拆成一格一格：(段, 栏目名或第几项, 子栏目) → 内容。"""
    out = {}
    for sec in SECTIONS:
        for i, it in enumerate(profile.get(sec) or []):
            if isinstance(it, list) and len(it) == 2:
                out[(sec, it[0], None)] = it[1]
            elif isinstance(it, dict):
                for k, v in it.items():
                    out[(sec, i, k)] = v
    return out


def merge(base, edited, current):
    """页面上改的只是 base → edited 之间变了的那几格：把这几格写进服务器上最新的 current。
    这样页面开着的时候别处（助手、另一个窗口）改过的内容不会被整份旧底稿冲掉。"""
    b = _cells(base or {})
    changed = {k: v for k, v in _cells(edited or {}).items() if b.get(k) != v}
    out = copy.deepcopy(current or {})
    for (sec, key, sub), v in changed.items():
        items = out.setdefault(sec, [])
        if sub is None:
            hit = next((it for it in items if isinstance(it, list) and it and it[0] == key), None)
            if hit:
                hit[1] = v
            else:
                items.append([key, v])
        elif isinstance(key, int) and key < len(items) and isinstance(items[key], dict):
            items[key][sub] = v
    return out


def as_text(profile):
    """给助手看的纯文本版本。空着的写「（空）」，免得它以为漏看了。"""
    lines = []
    for sec in SECTIONS:
        items = profile.get(sec) or []
        if not items:
            continue
        lines.append(f"## {sec}")
        for item in items:
            if isinstance(item, list):
                lines.append(f"- {item[0]}：{item[1] or '（空）'}")
            elif isinstance(item, dict):
                lines.append("- " + "；".join(f"{k}：{v or '（空）'}" for k, v in item.items()))
    return "\n".join(lines)


def kit_text(kit):
    """application_kit.json（简历内容）的纯文本版本。"""
    lines = []
    for sec, items in kit.items():
        if sec.startswith("_"):
            continue
        lines.append(f"## {sec}")
        for item in items or []:
            if isinstance(item, list):
                lines.append(f"- {item[0]}：{item[1]}")
            elif isinstance(item, dict):
                head = "｜".join(str(item.get(k, "")) for k in ("公司", "学校", "职位", "专业", "学历", "部门/方向", "地点", "起止时间") if item.get(k))
                lines.append(f"- {head}")
                for k, v in item.items():
                    if k in ("精简描述", "补充") and v:
                        lines.append(f"  - {k}：{v}")
                    elif k == "完整描述" and v:
                        lines.append("  - 完整描述：" + " / ".join(v))
    return "\n".join(lines)
