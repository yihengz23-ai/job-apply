"""网申资料包（application_kit.json）必须和简历 PDF 一致：经历原文、日期、数字都要能在简历里找到。"""

import json
import re

import pytest

from jobapply import config, resume

KIT_PATH = config.BASE_DIR / "application_kit.json"
pytestmark = pytest.mark.skipif(not KIT_PATH.exists() or not config.RESUME_PATH.exists(),
                                reason="没有私人资料包或简历文件")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


@pytest.fixture(scope="module")
def kit_and_resume():
    kit = json.loads(KIT_PATH.read_text(encoding="utf-8"))
    return kit, _norm(resume.resume_status()["text"])


def test_full_descriptions_are_resume_text(kit_and_resume):
    kit, text = kit_and_resume
    for exp in kit["实习经历"]:
        for bullet in exp["完整描述"]:
            assert _norm(bullet) in text, f"简历里找不到：{bullet[:30]}…"


def test_dates_and_contacts_in_resume(kit_and_resume):
    kit, text = kit_and_resume
    for exp in kit["实习经历"] + kit["教育经历"]:
        assert _norm(exp["起止时间"]) in text, exp["起止时间"]
    for label, value in kit["基本信息"]:
        if label in ("手机", "微信", "邮箱", "姓名"):
            assert _norm(value) in text, value


def test_short_descriptions_add_no_new_numbers(kit_and_resume):
    kit, text = kit_and_resume
    for exp in kit["实习经历"]:
        for n in re.findall(r"\d+(?:\.\d+)?", exp["精简描述"]):
            assert n in text, f"精简描述里的数字 {n} 简历上没有"
