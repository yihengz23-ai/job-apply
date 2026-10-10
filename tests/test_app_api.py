"""第 2 批界面的数据：看板一张卡片一次申请（同一家几个志愿合在一起，串行志愿显示「在看 / 排队 / 已流转」）、
「今天」区、单家详情、助手状态按申请归。不起 Flask、不起助手进程。"""

from datetime import datetime, timedelta

import pytest

from jobapply import agent, app_api, apps, config, jobqueue, records, wstasks

NOW = datetime(2026, 10, 10, 9, 0)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_PATH", tmp_path / "records.json")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_api, "_agent_states", lambda: {})
    return tmp_path


def rec(**kw):
    return records.new_record(company_name=kw.pop("company_name", "F集团"), job_title=kw.pop("job_title", "分析师"),
                              to_email=kw.pop("to_email", "hr@f.com"), **kw)


def three_volunteers():
    ids = [records.add(rec(app_id="fx01", job_title=t, choice_no=i)) for i, t in enumerate(["投资岗", "战略岗", "财务岗"], 1)]
    return ids


def test_serial_volunteers_current_queued_passed(store):
    ids = three_volunteers()
    apps.set_volunteer_mode("fx01", "串行")
    apps.advance(ids[0], "拒绝", by="本人", manual=True)
    pos = app_api.positions_view(apps.get("fx01"), apps.positions("fx01"))
    assert [p["job_title"] for p in pos] == ["投资岗", "战略岗", "财务岗"]                # 按志愿顺序
    assert [p["serial_state"] for p in pos] == ["未通过 · 已流转", "在看", "排队：前面的志愿有结果后才看"]
    apps.set_volunteer_mode("fx01", "平行")                                         # 平行：几个岗位同时看，不标排队
    assert all(p["serial_state"] == "" for p in app_api.positions_view(apps.get("fx01"), apps.positions("fx01")))


def test_overview_one_card_per_app_sorted_by_turn(store):
    three_volunteers()                                                             # 等对方
    done = records.add(rec(company_name="R资本", to_email="hr@r.com"))
    apps.advance(done, "拒绝", by="本人", manual=True)                                # 已结束
    t = wstasks.add("https://jobs.example.com/w1", "W银行", "管培生")               # 网申还没开始填：轮到你
    cards = app_api.overview(now=NOW)
    assert [c["company"] for c in cards] == ["W银行", "F集团", "R资本"]
    assert [c["turn"] for c in cards] == ["轮到你", "等对方", "已结束"]
    f = cards[1]
    assert f["id"] == "fx01" and len(f["positions"]) == 3 and f["stage_label"] == "已投递"
    assert cards[0]["id"] == t["id"] and cards[0]["button"]["action"] == "run"
    assert app_api.overview(campaign="别的批次", now=NOW) == []                      # 按批次筛
    apps.soft_delete("fx01", by="本人")
    assert "fx01" not in [c["id"] for c in app_api.overview(now=NOW)]               # 删了的不显示


def test_mail_draft_shows_on_card(store):
    rid = records.add(rec(company_name="D资本", status="草稿", to_email="hr@d.com"))
    jobqueue._save([{"id": "q1", "status": "已存草稿", "record_id": rid, "updated_at": "2026-10-10 08:00:00"}])
    c = next(c for c in app_api.overview(now=NOW) if c["company"] == "D资本")
    assert c["mail_status"] == "已存草稿" and "Gmail 草稿" in c["sub"]


def test_today_your_turn_soon_and_mail_review(store):
    a = records.get(records.add(rec(company_name="S资本", to_email="hr@s.com")))["app_id"]
    b = records.get(records.add(rec(company_name="T资本", to_email="hr@t.com")))["app_id"]
    c = records.get(records.add(rec(company_name="U资本", to_email="hr@u.com")))["app_id"]
    apps.set_next_step(a, "测评", due=(NOW + timedelta(days=2)).strftime("%Y-%m-%d"), source="邮件")     # 72 小时内：轮到你
    apps.set_next_step(b, "笔试", due=(NOW + timedelta(days=5)).strftime("%Y-%m-%d"), source="本人口述")  # 一周内：快到期
    apps.set_next_step(c, "面试", due=(NOW + timedelta(days=20)).strftime("%Y-%m-%d"), source="邮件")    # 太远：不列
    jobqueue._save([{"id": "q1", "status": "待审核"}, {"id": "q2", "status": "需处理"}, {"id": "q3", "status": "已发送"},
                    {"id": "q4", "status": "已存草稿", "send_uncertain": True}])
    d = app_api.today(now=NOW)
    assert [x["company"] for x in d["your_turn"]] == ["S资本"]
    assert [x["company"] for x in d["soon"]] == ["T资本"]
    assert d["mail_review"] == 3 and d["waiting"] == 2 and d["recent_progress"] == []


def test_detail_has_positions_jd_snapshots_timeline(store):
    ids = three_volunteers()
    records.update(ids[1], {"jd_text": "负责行业研究", "jd_url": "https://jobs.example.com/jd/2"})
    apps.add_snapshot("fx01", "提交页", "姓名：某某\n身份证号：123456200001010000\n志愿：投资岗、战略岗、财务岗")
    apps.add_timeline("fx01", "备注", "本人说先看第一志愿", "本人")
    d = app_api.detail("fx01")
    assert [p["job_title"] for p in d["positions"]] == ["投资岗", "战略岗", "财务岗"]
    assert d["positions"][1]["jd_text"] == "负责行业研究" and d["positions"][1]["has_jd"]
    assert "123456200001010000" not in d["snapshots"][0]["text"] and "志愿：投资岗" in d["snapshots"][0]["text"]
    assert d["timeline"][0]["text"] == "本人说先看第一志愿"                              # 新的在前
    apps.advance(ids[0], "笔试", by="本人", manual=True)
    h = app_api.detail("fx01")["positions"][0]["history"]
    assert h[-1]["text"] == "已投递 → 笔试/测评（本人）"                                  # 岗位改动有文字（不只是时间）
    with pytest.raises(apps.NotFound):
        app_api.detail("nope")


def test_suggestion_accept_and_volunteer_mode_from_ui(store):
    rid = records.add(rec(company_name="E资本", to_email="hr@e.com"))
    aid = records.get(rid)["app_id"]
    s = apps.suggest(aid, "阶段", "来信像是测评邀请", {"record_id": rid, "to": "笔试"})
    c = next(c for c in app_api.overview(now=NOW) if c["id"] == aid)
    assert [x["id"] for x in c["suggestions"]] == [s["id"]] and c["turn"] == "轮到你"
    app_api.accept_suggestion(aid, s["id"])
    assert records.get(rid)["status"] == "笔试"
    app_api.set_volunteer_mode(aid, "单个")
    assert apps.get(aid)["volunteer_mode"] == "单个"
    with pytest.raises(ValueError):
        app_api.set_volunteer_mode(aid, "随便")


def test_agent_state_by_app(monkeypatch):
    chats = [{"id": "c3", "task_id": "t1", "updated_at": "3"}, {"id": "c2", "task_id": "t1", "updated_at": "2"},
             {"id": "c1", "task_id": "t2", "updated_at": "1"}, {"id": "c0", "task_id": "", "updated_at": "0"}]
    monkeypatch.setattr(agent, "list_chats", lambda include_archived=False: chats)
    monkeypatch.setattr(agent, "_active", lambda: {"c2"})
    monkeypatch.setattr(agent, "waiting_chats", lambda: {"c1": "另一个助手正在填同一个网站"})
    monkeypatch.setattr(agent, "last_line", lambda cid: "在填教育经历")
    st = agent.state_by_app()
    assert st["t1"] == {"chat_id": "c2", "working": True, "queued_why": "", "alive": False, "last": "在填教育经历"}  # 在做的优先
    assert st["t2"]["queued_why"] and not st["t2"]["working"] and set(st) == {"t1", "t2"}


@pytest.mark.parametrize("text,titles,expect", [
    ("第1志愿　投资岗｜某集团…｜10月09日　提交申请\n第2志愿　战投岗｜某财富｜10月09日\n第3志愿　管培生｜某商城\n"
     "第4志愿　战投岗｜某医药", ["投资岗", "战投岗", "管培生", "战投岗（第4志愿）"], [1, 2, 3, 4]),
    ("志愿一\n职位名称\n【2027校招】财务管培生 (财务/投资)\n所属项目\n志愿二\n职位名称\n【2027校招】科技投资\n",
     ["【2027校招】财务管培生 (财务/投资)", "【2027校招】科技投资"], [1, 2]),
    ("2\n选择志愿\n3\n简历填写\n申请成功！\n第1志愿：投资业务岗-某资产投资公司\n第2志愿：权益研究员-某基金公司\n",
     ["投资业务岗-某资产投资公司", "权益研究员-某基金公司", "管理培训生-上海总部"], [1, 2, 0]),     # 页面步骤里的「选择志愿 3」不算
    ("第1志愿：投资岗\n第2志愿：投资岗（上海）", ["投资岗", "投资岗（上海）"], [1, 2]),               # 一个名字是另一个的前缀：取最长的
])
def test_choice_order_from_readback(text, titles, expect):
    pos = [{"id": f"r{i}", "job_title": t} for i, t in enumerate(titles)]
    got = apps.choice_order(text, pos)
    assert [got.get(f"r{i}", 0) for i in range(len(titles))] == expect


def test_positions_sorted_by_guessed_choice(store):
    text = "【网站上的投递记录】\n第1志愿　战略岗｜某集团\n第2志愿　投资岗｜某集团\n"
    ids = [records.add(rec(app_id="gx01", job_title=t, ws_submitted=text)) for t in ("投资岗", "战略岗")]
    apps.set_volunteer_mode("gx01", "串行")
    pos = app_api.positions_view(apps.get("gx01"), apps.positions("gx01"))
    assert [(p["job_title"], p["choice_no"], p["choice_guessed"], p["serial_state"]) for p in pos] == \
        [("战略岗", 1, True, "在看"), ("投资岗", 2, True, "排队：前面的志愿有结果后才看")]
    records.update(ids[0], {"choice_no": 1})                                      # 记了序号的以记的为准
    records.update(ids[1], {"choice_no": 2})
    assert [p["job_title"] for p in app_api.positions_view(apps.get("gx01"), apps.positions("gx01"))] == ["投资岗", "战略岗"]


def test_step_done_button(store):
    aid = records.get(records.add(rec(company_name="K资本", to_email="hr@k.com")))["app_id"]
    apps.set_next_step(aid, "做测评（截止没说）", source="本人口述")
    assert next(c for c in app_api.overview(now=NOW) if c["id"] == aid)["turn"] == "轮到你"
    app_api.step_done(aid)
    assert next(c for c in app_api.overview(now=NOW) if c["id"] == aid)["turn"] == "等对方"
    assert app_api.step_done(aid)["next_step"]["done"] is True                    # 再点一次：不出错、不重复记
    assert sum(e["text"].startswith("做完了") for e in apps.get(aid)["timeline"]) == 1


def test_card_stage_for_serial_and_counts_for_parallel(store):
    ids = three_volunteers()
    apps.set_volunteer_mode("fx01", "平行")
    apps.advance(ids[1], "笔试", by="本人", manual=True)
    c = next(c for c in app_api.overview(now=NOW) if c["id"] == "fx01")
    assert c["stage_label"] == "笔试/测评" and c["stage_counts"] == {"已投递": 2, "笔试/测评": 1}   # 平行：走得最远的 + 各阶段几个
    assert "笔试/测评" in c["last_text"]
    apps.set_volunteer_mode("fx01", "串行")                                         # 串行：看当前志愿（志愿一还在已投递）
    c = next(c for c in app_api.overview(now=NOW) if c["id"] == "fx01")
    assert c["stage_label"] == "已投递" and [p["serial_state"] for p in c["positions"]][0] == "在看"


@pytest.mark.parametrize("text,mode", [
    ("投递后志愿将按顺序依次流转。", "串行"),
    ("本次招聘可同时投递多个职位", "平行"),
    ("一招聘计划下，您只有2次机会修改志愿（包括修改志愿信息及调整志愿顺序）", ""),          # 只是能改顺序：不猜
    ("志愿按顺序依次流转，不同机构间为平行志愿", ""),                                     # 一句话里两种都有：不猜
])
def test_volunteer_guess_only_on_clear_words(text, mode):
    assert apps.guess_volunteer_mode(text)[0] == mode


def test_volunteer_hints_for_the_user_to_judge():
    t = "温馨提示：点击图标可以取消岗位申请；志愿顺序为应聘某一机构的顺序，不同机构间为平行志愿。\n姓名：某某"
    assert apps.volunteer_hints(t) == ["志愿顺序为应聘某一机构的顺序，不同机构间为平行志愿"]


def test_given_up_is_finished_and_first_submitted_falls_back(store):
    rid = records.add(rec(company_name="B公司", status="草稿", to_email="", apply_channel="网申/链接", send_mode="未发邮件"))
    aid = records.get(rid)["app_id"]
    apps.set_web_phase(aid, "已放弃", by="本人")
    c = next(c for c in app_api.overview(now=NOW) if c["id"] == aid)
    assert c["turn"] == "已结束" and c["stage_label"] == "放弃"
    rid2 = records.add(rec(company_name="W公司", status="已投递", to_email="", apply_channel="网申/链接", send_mode="未发邮件",
                           sent_at="2026-10-08 21:29"))
    aid2 = records.get(rid2)["app_id"]
    records.mutate_all(lambda d: next(a for a in d["applications"] if a["id"] == aid2).update(first_submitted_at=""))   # 迁移前的老申请没记
    c2 = next(c for c in app_api.overview(now=NOW) if c["id"] == aid2)
    assert c2["first_submitted_at"] == "2026-10-08 21:29" and c2["first_submitted_guess"] is True


def test_step_undo(store):
    aid = records.get(records.add(rec(company_name="U2资本", to_email="hr@u2.com")))["app_id"]
    apps.set_next_step(aid, "做测评（截止没说）", source="本人口述")
    app_api.step_done(aid)
    app_api.step_undo(aid)
    assert apps.get(aid)["next_step"]["done"] is False and app_api.overview(now=NOW)[0]["turn"] == "轮到你"
    assert [e["text"] for e in apps.get(aid)["timeline"]][-1] == "改回没做完：做测评（截止没说）"


def test_id_number_need_says_i_filled_it(store):
    t = wstasks.add("https://jobs.example.com/id1", "证件资本", "分析师")
    wstasks.set_status(t["id"], "助手在填")
    wstasks.update(t["id"], status="等你处理", todo="蓝框网页「基本信息」页的证件号这一栏要你填")
    c = next(c for c in app_api.overview(now=NOW) if c["id"] == t["id"])
    assert c["need"]["kind"] == "证件号" and c["turn"] == "轮到你"
    assert c["button"] == {"label": "我填好了", "action": "need_done"}


def test_ghost_cards_are_not_listed(store):
    t = wstasks.add("https://jobs.example.com/g", "空卡资本", "分析师")
    assert t["id"] in [c["id"] for c in app_api.overview(now=NOW)]
    wstasks.delete(t["id"])                                                         # 在网申页删了这条待办
    assert t["id"] not in [c["id"] for c in app_api.overview(now=NOW)]             # 不再挂着一张点了 404 的卡
    rid = records.add(rec(company_name="删记录资本", to_email="hr@del.com"))
    aid = records.get(rid)["app_id"]
    records.delete(rid)
    assert aid not in [c["id"] for c in app_api.overview(now=NOW)]
