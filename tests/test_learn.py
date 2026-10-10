"""底稿学习（资料回流）：交完读回的简历和底稿比，本人改的 / 补的学回底稿，拿不准的出「记进底稿？」，像是填错的提醒，
对话里的【底稿】行照原话记；都能撤销，撤销过、点过不用的不再学；证件号这类永远不记。AI 一律用假的。"""

import copy
import json

import pytest

import app as panel
from jobapply import apps, config, learn, llm, progress, wsprofile, wstasks

LOCAL = {"Host": "localhost:5001"}
XRW = {"X-Requested-With": "jobapply"}

BASE = {"_说明": "x", "基本信息": [["姓名", "张三"], ["姓（拼音）", "Zhang"], ["所在城市", "上海"]],
        "高中": [["所在城市", "某市"]],
        "经历精确日期": [["B资本证明人", "王五，董事总经理，13900000000"]],
        "资格与考试": [["证券 / 基金 / 期货从业资格", "无"]],
        "求职偏好与声明": [["期望薪资", "面议"], ["是否受过处分 / 被开除 / 被辞退", "否"]],
        "其他": [["自我评价", "旧的一段" * 30], ["自我评价（精简版，字数有限时用）", "短的一段，字数有限时用"], ["兴趣爱好", "跑步"]]}


def item(section, key, new, who, scope, old=""):
    return {"section": section, "key": key, "old": old, "new": new, "who": who, "scope": scope, "evidence": new[:20], "why": "测试"}


@pytest.fixture
def setup(monkeypatch):
    wsprofile.save(copy.deepcopy(BASE))
    t = wstasks.add("https://jobs.example.com/learn", "某银行 X", "管培生")
    apps.ensure_for_task(wstasks.get(t["id"]))
    wstasks.save_readback(t["id"], "resume", "【实际提交的简历】……证明人：李四 投资经理 13800000000……自我评价：新写的一段……")
    seen = {"calls": 0}

    def fake_call(**kw):
        seen["calls"] += 1
        seen["content"] = kw["content"]
        if seen.get("during"):
            seen["during"]()
        return {"items": seen.get("items", [])}, {}
    monkeypatch.setattr(llm, "_call", fake_call)
    return t["id"], seen


def cell(sec, key):
    profile, _ = wsprofile.load()
    return next((v for k, v in profile.get(sec) or [] if k == key), None)


def reread(tid, text):
    wstasks.save_readback(tid, "resume", text)


# ── 读回以后学 ───────────────────────────────────────────────

def test_readback_learns_what_the_user_changed_and_asks_when_unsure(setup):
    tid, seen = setup
    seen["items"] = [
        item("经历精确日期", "A资本证明人", "李四，投资经理，13800000000", "本人补的", "以后都这样"),
        item("其他", "兴趣爱好", "跑步、游泳", "本人改的", "以后都这样", old="跑步"),
        item("其他", "学号", "S2026001", "看不出", "以后都这样"),                     # 拿不准：问
        item("求职偏好与声明", "期望薪资", "20-25 万", "本人改的", "只这家这样"),       # 这家特有：不动
        item("其他", "兴趣爱好", "跑步、游泳、看书", "助手按网站限制改的", "看不出"),     # 助手改的：不动
        item("基本信息", "证件号码", "[证件号已隐去]", "本人补的", "以后都这样"),        # 证件号：永远不记
    ]
    out = learn.from_readback(tid)
    assert cell("经历精确日期", "A资本证明人") == "李四，投资经理，13800000000" and cell("其他", "兴趣爱好") == "跑步、游泳"
    assert cell("其他", "学号") is None and cell("求职偏好与声明", "期望薪资") == "面议" and cell("基本信息", "证件号码") is None
    assert len(out["applied"]["items"]) == 2 and out["asked"] == 1
    sug = [s for s in apps.get(tid)["suggestions"] if s["kind"] == "底稿"]
    assert len(sug) == 1 and "学号" in sug[0]["text"] and sug[0]["payload"]["value"] == "S2026001"
    assert "旧的一段" in seen["content"] and "新写的一段" in seen["content"]
    assert any(e["kind"] == "底稿" and "A资本证明人" in e["text"] for e in apps.get(tid)["timeline"])
    assert learn.from_readback(tid) is None and seen["calls"] == 1                    # 同一份读回只学一次
    assert [p["key"] for p in learn.pending()] == ["学号"]
    assert wsprofile.changed_at("经历精确日期", "A资本证明人")                           # 记下了这一格什么时候改的


def test_backfill_only_fills_gaps(setup):
    """补学以前交过的：底稿里没有的补上；已有的不覆盖，改成问。"""
    tid, seen = setup
    seen["items"] = [item("经历精确日期", "A资本证明人", "李四，投资经理，13800000000", "本人补的", "以后都这样"),
                     item("其他", "兴趣爱好", "游泳", "本人改的", "以后都这样", old="跑步")]
    out = learn.from_readback(tid, backfill=True)
    assert cell("经历精确日期", "A资本证明人") and cell("其他", "兴趣爱好") == "跑步"
    assert out["asked"] == 1 and "兴趣爱好" in learn.pending()[0]["text"]


def test_user_edit_while_the_model_thinks_is_not_overwritten(setup):
    """模型想的十几秒里本人在页面上改了这一格：学的时候发现不一样了，不覆盖，改成问。"""
    tid, seen = setup

    def user_edits():
        profile, _ = wsprofile.load()
        profile["其他"][2][1] = "本人刚改好的"
        wsprofile.save(profile)
    seen["during"] = user_edits
    seen["items"] = [item("其他", "兴趣爱好", "游泳", "本人改的", "以后都这样", old="跑步")]
    out = learn.from_readback(tid, backfill=False)
    assert cell("其他", "兴趣爱好") == "本人刚改好的" and out["applied"] is None and out["asked"] == 1


def test_cells_changed_after_submission_are_not_relearned(setup):
    """这家交了以后本人在底稿里又改过的格子（记了修改时间的看时间）：网站上那是旧写法，不学也不问。"""
    tid, seen = setup
    wstasks.update(tid, submitted_at="2026-10-08 15:40")
    profile, _ = wsprofile.load()
    profile["其他"][2][1] = "本人后来写的"
    profile["经历精确日期"].append(["实习所在部门", "股权投资部"])
    wsprofile.save(profile)
    seen["items"] = [item("其他", "兴趣爱好", "当时交的", "本人改的", "以后都这样", old="本人后来写的"),
                     item("经历精确日期", "实习所在部门", "航空航天", "本人改的", "以后都这样", old="股权投资部"),
                     item("其他", "学号", "无", "本人补的", "以后都这样")]
    out = learn.from_readback(tid)
    assert cell("其他", "兴趣爱好") == "本人后来写的" and cell("经历精确日期", "实习所在部门") == "股权投资部"
    assert cell("其他", "学号") == "无" and out["asked"] == 0 and learn.pending() == []


def test_as_of_after_backups_rotated_is_not_trusted(setup, monkeypatch):
    """备份轮换掉以后说不准那时的底稿：已有的格子不自动覆盖（改成问），没有的照样补。"""
    tid, seen = setup
    wstasks.update(tid, submitted_at="2020-01-01 10:00")
    monkeypatch.setattr(wsprofile, "KEEP_BACKUPS", 1)
    profile, _ = wsprofile.load()
    wsprofile.save(profile)
    assert wsprofile.as_of("2020-01-01 10:00") == (False, None)
    monkeypatch.setattr(wsprofile, "changed_at", lambda *a, **k: "")              # 修改时间也没记：只能靠备份
    seen["items"] = [item("其他", "兴趣爱好", "游泳", "本人改的", "以后都这样", old="跑步"),
                     item("其他", "学号", "无", "本人补的", "以后都这样")]
    out = learn.from_readback(tid, backfill=False)
    assert cell("其他", "兴趣爱好") == "跑步" and cell("其他", "学号") == "无" and out["asked"] == 1


def test_long_text_replaced_by_much_shorter_is_asked_not_written(setup):
    """长的自我评价换成短得多的：多半是网站字数上限，不自动覆盖。"""
    tid, seen = setup
    seen["items"] = [item("其他", "自我评价", "短短一句", "本人改的", "以后都这样")]
    out = learn.from_readback(tid, backfill=False)
    assert cell("其他", "自我评价") == "旧的一段" * 30 and out["asked"] == 1


def test_value_that_matches_another_cell_is_not_listed(setup):
    tid, seen = setup
    seen["items"] = [item("其他", "自我评价", "短的一段，字数有限时用", "本人改的", "以后都这样")]    # 助手用的就是精简版那格
    out = learn.from_readback(tid)
    assert out["asked"] == 0 and out["applied"] is None


def test_likely_mistakes_are_flagged_not_learned(setup):
    tid, seen = setup
    seen["items"] = [item("基本信息", "所在城市", "某县", "像是填错了", "看不出", old="上海")]
    out = learn.from_readback(tid)
    assert out["wrong"] == 1 and cell("基本信息", "所在城市") == "上海"
    p = learn.pending()
    assert p[0]["kind"] == "核对" and "像是填错了" in p[0]["text"] and p[0]["refs"][0]["app_id"] == tid
    assert "所在城市" not in (apps.view(apps.get(tid), {"positions": []}).get("todo") or "")   # 不把这家顶进「今天」


def test_same_suggestion_from_several_applications_shows_once(setup):
    tid, seen = setup
    t2 = wstasks.add("https://jobs.example.com/learn4", "某银行 W", "")
    apps.ensure_for_task(wstasks.get(t2["id"]))
    wstasks.save_readback(t2["id"], "resume", "第二份")
    seen["items"] = [item("基本信息", "户口所在地", "某省 / 某市 / 某县", "看不出", "以后都这样")]
    learn.from_readback(tid)
    seen["items"] = [item("基本信息", "户口所在地", "某省 某市 某县", "看不出", "以后都这样")]       # 只是分隔写法不同
    learn.from_readback(t2["id"])
    p = learn.pending()
    assert len(p) == 1 and len(p[0]["refs"]) == 2 and "某银行 X、某银行 W" in p[0]["text"]
    a, b = p[0]["refs"]
    apps.accept(a["app_id"], a["sid"])                                                 # 页面上：第一家采纳、其余点掉
    apps.dismiss(b["app_id"], b["sid"])
    assert cell("基本信息", "户口所在地") and learn.pending() == []
    assert learn._state()["rejected"] == []                                            # 已经记进去的值不算「不要」


# ── 撤销、不用、失败重试 ─────────────────────────────────────

def test_undo_puts_cells_back_and_is_not_relearned(setup):
    tid, seen = setup
    seen["items"] = [item("经历精确日期", "A资本证明人", "李四", "本人补的", "以后都这样"),
                     item("其他", "兴趣爱好", "游泳", "本人改的", "以后都这样", old="跑步")]
    entry = learn.from_readback(tid, backfill=False)["applied"]
    assert learn.undo(entry["id"]) == 2
    assert cell("经历精确日期", "A资本证明人") is None and cell("其他", "兴趣爱好") == "跑步"
    reread(tid, "补读了一次，文字稍有不同")                                               # 再读回一次：撤过的不再学
    out = learn.from_readback(tid, backfill=False)
    assert out["applied"] is None and out["asked"] == 0 and cell("其他", "兴趣爱好") == "跑步"


def test_undo_order_and_cells_changed_later(setup):
    tid, seen = setup
    e1, _ = learn.apply([{"section": "其他", "key": "兴趣爱好", "new": "B"}], source="测试")
    e2, _ = learn.apply([{"section": "其他", "key": "兴趣爱好", "new": "C"}], source="测试")
    assert learn.undo(e1["id"]) == 0 and not learn.recent()[1]["undone"]               # 后来又改过：没撤，也不标已撤销
    assert learn.undo(e2["id"]) == 1 and cell("其他", "兴趣爱好") == "B"
    assert learn.undo(e1["id"]) == 1 and cell("其他", "兴趣爱好") == "跑步"
    e3, skipped = learn.apply([{"section": "其他", "key": "兴趣爱好", "new": "长版"},
                               {"section": "其他", "key": "兴趣爱好", "new": "短版"}], source="测试")
    assert cell("其他", "兴趣爱好") == "长版" and len(skipped) == 1                      # 同一批第二次写同一格：不写


def test_dismissed_suggestion_is_not_asked_again(setup):
    tid, seen = setup
    seen["items"] = [item("其他", "学号", "S1", "看不出", "以后都这样")]
    learn.from_readback(tid)
    sid = next(s["id"] for s in apps.get(tid)["suggestions"] if s["kind"] == "底稿")
    apps.dismiss(tid, sid)
    reread(tid, "再读一次")
    out = learn.from_readback(tid)
    assert out["asked"] == 0 and learn.pending() == []


def test_failure_is_retried_later(setup, monkeypatch):
    tid, seen = setup

    def boom(**kw):
        raise RuntimeError("超时了")
    monkeypatch.setattr(llm, "_call", boom)
    assert learn._safe(tid) is None and tid not in learn._state()["sigs"]               # 没学成：不记签名
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"items": [item("其他", "学号", "无", "本人补的", "以后都这样")]}, {}))
    assert learn.backfill() == 1 and cell("其他", "学号") == "无"                        # 下次启动补上
    assert learn.backfill() == 0


def test_accept_does_not_overwrite_a_cell_changed_since(setup):
    tid, seen = setup
    seen["items"] = [item("其他", "兴趣爱好", "游泳", "看不出", "以后都这样", old="跑步")]
    learn.from_readback(tid)
    profile, _ = wsprofile.load()
    profile["其他"][2][1] = "本人后来改的"
    wsprofile.save(profile)
    sid = next(s["id"] for s in apps.get(tid)["suggestions"] if s["kind"] == "底稿")
    with pytest.raises(ValueError, match="后来改过"):
        apps.accept(tid, sid)
    assert cell("其他", "兴趣爱好") == "本人后来改的"
    assert next(s for s in apps.get(tid)["suggestions"] if s["id"] == sid)["state"] == "待定"   # 建议留着


def test_spoken_accept_does_not_touch_profile_suggestions(setup, monkeypatch):
    """口述「X 那条建议采纳」只管看板上的建议：底稿、核对两类只在底稿页上点。"""
    tid, seen = setup
    seen["items"] = [item("其他", "学号", "S1", "看不出", "以后都这样")]
    learn.from_readback(tid)
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": [{"app_id": tid, "company": "某银行 X", "position": "", "event": "采纳建议",
                                                                "round": "", "happened_at": "", "due": "", "quote": "那条建议采纳",
                                                                "value": ""}]}, {}))
    progress.apply("某银行 X 那条建议采纳")
    assert cell("其他", "学号") is None and len(learn.pending()) == 1


# ── 号码 ───────────────────────────────────────────────────

@pytest.mark.parametrize("key,value", [
    ("工资卡", "6222 0212 3456 7890 123"), ("公民身份号码", "110101 19900101 1234"), ("Passport No.", "E1234 5678"),
    ("社保卡号", "1101011990****1234"), ("备用号码", "1101-0119-9001-0112-34"),
])
def test_ids_and_card_numbers_never_go_in(setup, key, value):
    _entry, msg = learn.from_chat(f"{key}：{value}")
    assert "不进底稿" in msg and value not in json.dumps(wsprofile.load()[0], ensure_ascii=False)


def test_numbers_in_why_are_dropped_and_masked_for_the_model(setup):
    tid, seen = setup
    seen["items"] = [{**item("其他", "学号", "S1", "本人补的", "以后都这样"), "why": "卡号 6222021234567890123 也交了"}]
    reread(tid, "银行卡 6222 0212 3456 7890 123；手机 138 0000 0000")
    out = learn.from_readback(tid)
    assert out["applied"] is None and "6222 0212" not in seen["content"] and "138 0000 0000" in seen["content"]


# ── 对话里的【底稿】行 ─────────────────────────────────────────

def test_chat_line_goes_into_the_profile(setup):
    entry, msg = learn.from_chat("经历精确日期 / 实习所在部门：股权投资部", app_id=setup[0], company="某银行 X")
    assert cell("经历精确日期", "实习所在部门") == "股权投资部" and "底稿记上了" in msg
    _e, msg = learn.from_chat("B资本证明人（两段同一人）：王五，董事总经理，13900000000")   # 去掉括号对上已有的：一样就不改
    assert "本来就是" in msg
    _e, msg = learn.from_chat("随便说一句")
    assert "没看懂" in msg
    assert learn.from_chat("栏目：内容") == (None, "")                                      # 解释格式时写的占位：不记


def test_chat_keys_with_slashes_update_the_existing_cell(setup):
    learn.from_chat("是否受过处分 / 被开除 / 被辞退：是")
    learn.from_chat("证券 / 基金 / 期货从业资格：证券从业资格")
    learn.from_chat("求职偏好与声明 / 期望薪资：20 万")                                      # 「段 / 栏目」
    assert cell("求职偏好与声明", "是否受过处分 / 被开除 / 被辞退") == "是" and cell("资格与考试", "证券 / 基金 / 期货从业资格") == "证券从业资格"
    assert cell("求职偏好与声明", "期望薪资") == "20 万" and cell("其他", "被开除 / 被辞退") is None


def test_chat_items_tolerate_markdown_lists_and_several_per_line():
    text = "好的。\n**【底稿】实习所在部门：股权投资部**\n【底稿】\n- 学号：无\n- 兴趣爱好：跑步\n\n【底稿】学号：无；户口类型：城镇"
    assert learn.chat_items(text) == ["实习所在部门：股权投资部", "学号：无", "兴趣爱好：跑步", "学号：无", "户口类型：城镇"]
    assert learn.chat_items("【底稿】自我评价：现金流和账期；在 A 资本做过一级项目") == ["自我评价：现金流和账期；在 A 资本做过一级项目"]


def test_find_tells_apart_similar_keys():
    """一字不差的优先；去掉括号对上的看括号里的字（「拼音」「精简版」是另一个版本）；给了段就在段里找；分不清不猜。"""
    prof = copy.deepcopy(BASE)
    prof["经历精确日期"].append(["实习所在部门（几段都是）", "股权投资部"])
    assert learn._find(prof, "其他", "自我评价")[1][1] == "旧的一段" * 30
    assert learn._find(prof, "其他", "自我评价（精简版，字数有限时用）")[1][1] == "短的一段，字数有限时用"
    assert learn._find(prof, "其他", "自我评价（精简版）")[1][1] == "短的一段，字数有限时用"
    assert learn._find(prof, "其他", "自我评价（英文）")[2] is None                       # 英文版是另一格（底稿里没有）
    assert learn._find(prof, "", "实习所在部门")[1][1] == "股权投资部"
    assert learn._find(prof, "基本信息", "姓")[2] is None                                  # 不会对上「姓（拼音）」
    assert learn._find(prof, "基本信息", "所在城市")[1][1] == "上海"                        # 给了段：不跑到「高中」去
    assert learn._find(prof, "", "所在城市")[2] == "ambiguous"
    assert learn._find(prof, "其他", "B资本证明人")[2] == "cross"


def test_example_profile_is_left_alone(setup, monkeypatch):
    monkeypatch.setattr(wsprofile, "load", lambda: ({"其他": []}, True))
    assert "还没有自己的网申底稿" in learn.from_chat("学号：无")[1]


# ── 触发、补学、接口 ─────────────────────────────────────────

def test_resume_readback_starts_learning(no_background_learning):
    t = wstasks.add("https://jobs.example.com/learn2", "某券商 Y", "")
    wstasks.save_readback(t["id"], "status", "投递记录：简历筛选中")
    assert no_background_learning == []                                              # 投递记录不学
    wstasks.save_readback(t["id"], "resume", "实际提交的简历……")
    assert no_background_learning == [t["id"]]


def test_backfill_goes_in_submission_order(setup, monkeypatch):
    tid, _seen = setup
    t2 = wstasks.add("https://jobs.example.com/learn3", "某基金 Z", "")
    wstasks.save_readback(t2["id"], "resume", "另一份")
    wstasks.update(tid, submitted_at="2026-10-09 10:00")
    wstasks.update(t2["id"], submitted_at="2026-10-08 10:00")
    order = []
    monkeypatch.setattr(learn, "_safe", lambda task_id, backfill=None, fresh=None: order.append(task_id) or {"applied": None})
    assert learn.backfill() == 2 and order == [t2["id"], tid]


def test_routes(setup):
    tid, seen = setup
    seen["items"] = [item("经历精确日期", "A资本证明人", "李四", "本人补的", "以后都这样")]
    entry = learn.from_readback(tid)["applied"]
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    d = c.get("/api/profile-learn", headers=LOCAL).get_json()
    assert d["items"][0]["id"] == entry["id"] and d["pending"] == []
    assert c.post(f"/api/profile-learn/{entry['id']}/undo", headers={**LOCAL, **XRW}).get_json() == {"ok": True, "cells": 1}
    assert c.post("/api/profile-learn/nope/undo", headers={**LOCAL, **XRW}).status_code == 404
    assert json.loads(config.DATA_DIR.joinpath("profile_learn.json").read_text(encoding="utf-8"))["entries"][0]["undone"]


def test_differently_named_key_is_matched_by_its_old_value(setup):
    """模型给几段合在一格的栏目起了新名字（「A资本所在部门」），但说了底稿里原来是什么：按原值找回那一格，
    本人交了以后改过的照样不学；对上了也只问不自动写。"""
    tid, seen = setup
    wstasks.update(tid, submitted_at="2026-10-08 15:40")
    profile, _ = wsprofile.load()
    profile["经历精确日期"].append(["实习所在部门（几段都是）", "股权投资部"])            # 交了以后才加的
    wsprofile.save(profile)
    seen["items"] = [item("经历精确日期", "A资本所在部门", "航空航天", "本人改的", "以后都这样", old="股权投资部")]
    out = learn.from_readback(tid)
    assert out["applied"] is None and out["asked"] == 0 and cell("经历精确日期", "A资本所在部门") is None


def test_pending_hides_values_already_learned_from_another_application(setup):
    tid, seen = setup
    seen["items"] = [item("其他", "学号", "S1", "看不出", "以后都这样")]
    learn.from_readback(tid)
    learn.from_chat("学号：S1")
    assert learn.pending() == []

