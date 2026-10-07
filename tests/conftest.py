"""所有测试共用：绝不真调 AI（会员额度 / API 都不碰）。要测 AI 那一步的测试自己再 monkeypatch。"""

import pytest

from jobapply import llm


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    def refuse(**kw):
        raise RuntimeError("测试里不许真调 AI：请在测试里 monkeypatch 掉这一步")
    monkeypatch.setattr(llm, "_call", refuse)
    # 写完自查默认「没问题」：大部分测试只关心别的环节
    monkeypatch.setattr(llm, "self_review", lambda jd_text, result, **kw: ({"changes": [], "email_subject": "", "email_body": ""}, {}))
