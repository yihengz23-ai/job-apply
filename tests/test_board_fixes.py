"""看板记错的几个根子（2026-10-11 本人点名：一家没投却一堆已投递、一家没交显示已投递、几家岗位数多了）：
岗位名在括号里被切碎、同一个岗位带不带地点算两个、对话对应的那家删了以后一删一建的循环、网申待办退回了看板没退；
以及本人在对话里能直接改看板（其实没投、删岗位、只投了、删申请），都能撤销。"""

import pytest

import app as panel
from jobapply import apps, llm, progress, records, wstasks

LOCAL = {"Host": "localhost:5001"}
NOW = progress._now()


def ev(app_id="", company="", event="其他", quote="", position="", value=""):
    return {"app_id": app_id, "company": company, "position": position, "event": event, "round": "",
            "happened_at": "", "due": "", "quote": quote, "value": value}


def fake_ai(monkeypatch, events):
    monkeypatch.setattr(llm, "_call", lambda **kw: ({"events": events}, {}))


def submitted_task(company="甲集团", jobs=("投资专员",), url="https://jobs.example.com/a"):
    """一家网申：点过「我已提交」，岗位记成已投递。"""
    t = wstasks.add(url, company, "")
    wstasks.apply_markers(f"【网申记录】公司：{company}｜岗位：{'、'.join(jobs) if len(jobs) > 1 else jobs[0]}｜网址：{url}｜状态：已填待提交", task_id=t["id"])
    wstasks.set_status(t["id"], "已提交")
    return wstasks.get(t["id"])


def live_positions(app_id):
    return [r for r in apps.positions(app_id) if not r.get("deleted_at")]


# ── 岗位名 ───────────────────────────────────────────────────

def test_brackets_keep_a_position_whole():
    assert wstasks.split_jobs("投资专员【2027届-理工科优先】（甲股权；乙市）") == []          # 一个岗位：照旧当岗位名
    two = wstasks.split_jobs("投资分析师（股权投资岗，北京）、管理培训生（公司金融方向）、研究员（上海、北京）")
    assert [(j["name"], j["location"]) for j in two] == [("投资分析师（股权投资岗，北京）", ""), ("管理培训生（公司金融方向）", ""),
                                                          ("研究员", "上海、北京")]         # 括号里全是城市才拆成地点
    ranked = wstasks.split_jobs("投资专员（第一志愿）、投资专员（北京市）（第二志愿）")
    assert [(j["name"], j["location"], j["rank"]) for j in ranked] == [("投资专员", "", 1), ("投资专员", "北京市", 2)]
    assert wstasks.split_jobs("（待读回）") == [] and wstasks.split_jobs("（岗位见网站）、（还没选）") == []
    got = wstasks._parse_positions("投资专员（甲股权；乙市）；行业研究员")
    assert [(p["name"], p["location"]) for p in got] == [("投资专员", "甲股权；乙市"), ("行业研究员", "")]
    assert wstasks._parse_positions("（待读回）") == []


@pytest.mark.parametrize("a,b,same", [
    # 同一个岗位的不同写法（都是看板上真出现过的）
    (("投资专员【2027届-理工科优先】（甲股权；乙市）", ""), ("投资专员【2027届-理工科优先】", "乙市"), True),
    (("投资分析师/投资经理", "北京-朝阳区（某某中心）"), ("投资分析师/投资经理", "北京-朝阳区"), True),
    (("启航计划-投资业务", "上海"), ("启航计划-投资业务（上海）", "上海-虹口区"), True),
    (("财务顾问分析师", "私募融资 & 并购"), ("财务顾问分析师（私募融资 & 并购）", ""), True),
    (("某某投资有限公司业务助理", ""), ("（2027届校招）某某投资有限公司业务助理", "北京"), True),
    (("【优先】财务顾问分析师（私募融资 & 并购）", ""), ("财务顾问分析师（私募融资 & 并购）", ""), True),
    (("管理培训生", "公司金融方向"), ("管理培训生（公司金融方向）", "总行，地点网站未显示"), True),
    (("投资业务类", "甲投资总公司"), ("投资业务类（甲投资总公司）", "北京市"), True),
    (("甲集团 产业投资岗", ""), ("产业投资岗（甲集团有限公司）", "深圳南山区"), True),
    (("FOF研究员", "深圳福田区"), ("FOF研究员（乙资产管理有限公司）", "深圳福田区乙金融大厦"), True),
    # 真是两个岗位
    (("管理培训生（公司金融方向）", ""), ("管理培训生（投行与金融市场方向）", ""), False),
    (("投资分析师", "北京"), ("投资分析师", "上海"), False),
    (("投资专员（第1志愿）", ""), ("投资专员（第2志愿）", ""), False),
    (("甲集团 产业投资岗", ""), ("乙集团 产业投资岗", ""), False),
    (("研究员", ""), ("分析师", ""), False),
])
def test_same_position(a, b, same):
    assert wstasks.same_position({"name": a[0], "location": a[1]}, {"name": b[0], "location": b[1]}) is same


def test_same_name_different_volunteer_stays_two():
    t = {"positions": [{"name": "投资专员（北京、上海）", "location": "上海", "rank": 1},
                       {"name": "投资专员（北京）", "location": "北京", "rank": 2}]}
    wstasks._merge_positions(t, [{"name": "投资专员", "location": "北京", "status": "投递简历（第2志愿）"}], src="网站")
    assert [(p["name"], p.get("rank")) for p in t["positions"]] == [("投资专员（北京、上海）", 1), ("投资专员", 2)]


def test_same_position_with_or_without_place_is_one():
    t = {"positions": [{"name": "启航计划-投资业务", "location": ""}]}
    wstasks._merge_positions(t, [{"name": "启航计划-投资业务（上海）", "location": ""}])
    wstasks._merge_positions(t, [{"name": "启航计划-投资业务", "location": "上海"}])
    assert len(t["positions"]) == 1
    wstasks._merge_positions(t, [{"name": "启航计划-投资业务", "location": "北京"}])        # 写明了不同城市：两个岗位
    assert len(t["positions"]) == 2


def test_site_name_wins_and_the_record_follows():
    t = submitted_task("丙银行", ("管理培训生（公司金融方向）", "研究交易类（权益方向）"))
    before = {r["job_title"]: r["id"] for r in live_positions(t["id"])}
    wstasks.save_readback(t["id"], "status", "我的申请：2 个", positions=[
        {"name": "管理培训生（公司金融方向）", "location": "总行"}, {"name": "（2027届）研究交易类（权益方向）", "location": "上海"}])
    after = {r["job_title"]: r["id"] for r in live_positions(t["id"])}
    assert len(after) == 2 and after["（2027届）研究交易类（权益方向）"] == before["研究交易类（权益方向）"]
    assert not [s for s in apps.get(t["id"])["suggestions"] if s["kind"] == "岗位数"]


def test_marker_and_readback_end_up_as_one_record():
    t = submitted_task("乙汽车", ("投资专员【2027届】（甲股权；乙市）",))
    wstasks.save_readback(t["id"], "status", "我的投递：已投递过的职位数：1", positions="投资专员【2027届】")
    assert len(live_positions(t["id"])) == 1


def test_site_name_is_not_renamed_again_by_a_later_readback():
    t = submitted_task("丁资本", ("产业投资岗",))
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions=[{"name": "产业投资岗（甲集团有限公司）", "location": "深圳"}])
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions=[{"name": "产业投资岗（甲集团）", "location": "深圳"}])
    assert [r["job_title"] for r in live_positions(t["id"])] == ["产业投资岗（甲集团有限公司）"]


# ── 一删一建的循环、退回不同步 ────────────────────────────────────

def test_marker_for_a_deleted_task_does_not_create_a_new_one():
    t = wstasks.add("https://jobs.example.com/b", "丙资管", "")
    wstasks.delete(t["id"])
    n = len(wstasks.list_tasks())
    done = wstasks.apply_markers("【网申记录】公司：丙资管｜岗位：（待读回）｜网址：（待你告诉我）｜状态：已提交", task_id=t["id"])
    assert done == [] and len(wstasks.list_tasks()) == n


def test_marker_without_a_real_url_does_not_create_and_new_ones_are_never_submitted():
    n = len(wstasks.list_tasks())
    assert wstasks.apply_markers("【网申记录】公司：丁银行｜网址：见之前的对话｜状态：已提交") == []
    assert len(wstasks.list_tasks()) == n
    wstasks.apply_markers("【网申记录】公司：戊证券｜岗位：分析师｜网址：https://jobs.example.com/c｜状态：已提交")
    t = next(x for x in wstasks.list_tasks() if x.get("company") == "戊证券")
    assert t["status"] == "已填待提交"                                                     # 助手一句「已提交」不算数


def test_unsubmitting_a_task_takes_the_records_back_to_draft():
    t = submitted_task("己基金")
    rid = t["record_id"]
    assert records.get(rid)["status"] == "已投递"
    wstasks.set_status(t["id"], "已填待提交")
    r = records.get(rid)
    assert r["status"] == "草稿" and not r.get("sent_at") and not wstasks.get(t["id"]).get("submitted_at")


# ── 本人在对话里改看板 ──────────────────────────────────────────

def test_not_actually_submitted_and_undo(monkeypatch):
    t = submitted_task("庚基金")
    fake_ai(monkeypatch, [ev(t["id"], "庚基金", "其实没投", "庚基金其实没交")])
    out = progress.apply("庚基金其实没交", now=NOW)
    assert wstasks.get(t["id"])["status"] == "已填待提交" and records.get(t["record_id"])["status"] == "草稿"
    progress.undo(out["id"])
    assert wstasks.get(t["id"])["status"] == "已提交" and records.get(t["record_id"])["status"] == "已投递"


def test_delete_one_position_keep_only_and_undo(monkeypatch):
    t = submitted_task("辛资本", ("投资分析师", "行业研究员", "投后管理"))
    assert len(live_positions(t["id"])) == 3
    fake_ai(monkeypatch, [ev(t["id"], "辛资本", "删岗位", "辛资本投后管理那条记错了", position="投后管理")])
    out = progress.apply("辛资本投后管理那条记错了", now=NOW)
    assert [r["job_title"] for r in live_positions(t["id"])] == ["投资分析师", "行业研究员"]
    fake_ai(monkeypatch, [ev(t["id"], "辛资本", "只投了", "辛资本我只投了投资分析师", position="投资分析师")])
    out2 = progress.apply("辛资本我只投了投资分析师", now=NOW)
    assert [r["job_title"] for r in live_positions(t["id"])] == ["投资分析师"]
    progress.undo(out2["id"])
    progress.undo(out["id"])
    assert len(live_positions(t["id"])) == 3


def test_delete_whole_application_hides_it_everywhere_and_undo(monkeypatch):
    t = submitted_task("壬资管")
    fake_ai(monkeypatch, [ev(t["id"], "壬资管", "删申请", "壬资管我根本没投，全删了")])
    out = progress.apply("壬资管我根本没投，全删了", now=NOW)
    assert apps.get(t["id"]).get("deleted_at") and live_positions(t["id"]) == []
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    assert t["id"] not in [x["id"] for x in c.get("/api/wstasks", headers=LOCAL).get_json()["tasks"]]   # 网申页上也不列
    # 那个对话里的助手再报一句、或者以后同一个网址又开一家：都不会把删掉的这家认回来
    assert wstasks.apply_markers(f"【网申记录】公司：壬资管｜网址：{t['url']}｜状态：已提交", task_id=t["id"]) == []
    again = wstasks.add(t["url"], "壬资管", "")
    assert again["id"] != t["id"]
    wstasks.delete(again["id"])
    progress.undo(out["id"])
    assert not apps.get(t["id"]).get("deleted_at") and len(live_positions(t["id"])) == 1


# ── 读回：网站上的岗位比看板少 ─────────────────────────────────────

def test_readback_with_fewer_positions_than_the_board_offers_to_drop_the_extra():
    t = submitted_task("癸证券", ("投资分析师", "北京）"))                                     # 旧版切碎留下的那种
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions="投资分析师（北京）")
    sug = [s for s in apps.get(t["id"])["suggestions"] if s["kind"] == "岗位数"]
    assert len(sug) == 1 and "网站「我的投递」上是 1 个岗位，看板记了 2 个" in sug[0]["text"] and "北京）" in sug[0]["text"]
    assert not [s for s in apps.get(t["id"])["suggestions"] if s["kind"] == "核对"]           # 「核对」是底稿页的，不混进去
    apps.accept(t["id"], sug[0]["id"], by="本人")
    assert [r["job_title"] for r in live_positions(t["id"])] == ["投资分析师"]


def test_count_check_is_not_repeated_after_a_no():
    t = submitted_task("子资本", ("投资分析师", "行业研究员"))
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions="投资分析师")
    s = next(x for x in apps.get(t["id"])["suggestions"] if x["kind"] == "岗位数")
    apps.dismiss(t["id"], s["id"], by="本人")                                                # 本人说「不用」：其实两个都投了
    wstasks.save_readback(t["id"], "status", "我的投递：1 个", positions="投资分析师")
    assert [x["state"] for x in apps.get(t["id"])["suggestions"] if x["kind"] == "岗位数"] == ["不用"]


# ── 总数：删掉的、没交的都不算投递 ────────────────────────────────────

def test_stats_list_and_duplicate_check_skip_deleted_and_drafts():
    keep = submitted_task("丑基金", url="https://jobs.example.com/d")
    gone = submitted_task("寅基金", url="https://jobs.example.com/e")
    apps.soft_delete(gone["id"], by="本人")
    draft = wstasks.add("https://jobs.example.com/f", "卯基金", "分析师")
    wstasks.set_status(draft["id"], "已填待提交")
    live = records.live(records.load())
    assert {r["company_name"] for r in live} == {"丑基金", "卯基金"}
    st = records.stats(records.load())
    assert st["total"] == 1 and st["drafts"] == 1 and st["status_dist"] == {"已投递": 1, "草稿": 1}
    assert not records.find_related("寅基金", "")                                             # 删掉的不再报「以前投过」
    panel.app.config["TESTING"] = True
    c = panel.app.test_client()
    listed = {r["company_name"] for r in c.get("/api/records?campaign=全部", headers=LOCAL).get_json()}
    assert listed == {"丑基金", "卯基金"} and keep["company"] == "丑基金"


def test_chat_drawer_closes_when_clicking_outside():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "js" / "agent.js").read_text(encoding="utf-8")
    assert "document.addEventListener('pointerdown'" in js and "closest('#agentDrawer, #agentFab, #modal" in js and "agShow(false)" in js

