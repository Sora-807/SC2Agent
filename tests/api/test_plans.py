"""规划存储 + 无会话干跑（P0）。

守三件事：
1. 默认规划存在、锁定、排第一 —— 复制是唯一改动路径（用户拍板）；
2. 校验不通过 400 + 中文原因（agent 拿着原因就能改）；
3. simulate **不需要会话**：标准开局种子 → 真 planner 曲线 + stalled 前瞻警报。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from game.catalog import load_all
from planner.build_order import Build, Train
from view.plans import PlanStore, ops_to_items


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path, plans_dir=tmp_path / "plans"))


# ---------------- 存储 ----------------

def test_default_plan_exists_locked_and_first(client: TestClient):
    rows = client.get("/api/plans").json()
    assert rows[0]["id"] == "default"
    assert rows[0]["locked"] is True
    assert rows[0]["items"] > 0, "默认规划从 bio_tank_opening 导出，不该是空的"


def test_default_plan_is_immutable(client: TestClient):
    assert client.put("/api/plans/default", json={"queue": []}).status_code == 400
    assert "锁定" in client.put("/api/plans/default", json={"queue": []}).json()["detail"]
    assert client.delete("/api/plans/default").status_code == 400


def test_copy_save_get_delete_roundtrip(client: TestClient):
    r = client.post("/api/plans", json={"id": "myplan", "title_zh": "我的开局",
                                        "copy_from": "default"})
    assert r.status_code == 200
    assert len(r.json()["queue"]) > 0, "复制默认规划要带上队列"

    saved = client.put("/api/plans/myplan", json={
        "title_zh": "我的开局 v2", "map": "LadderMap", "spawn": "tr",
        "queue": [{"op": "train", "type": "terran/marine", "count": 2}],
    })
    assert saved.status_code == 200

    got = client.get("/api/plans/myplan").json()
    assert got["title_zh"] == "我的开局 v2" and got["spawn"] == "tr"
    assert len(got["queue"]) == 1 and got["queue"][0]["count"] == 2
    assert got["locked"] is False

    assert client.delete("/api/plans/myplan").status_code == 200
    assert client.get("/api/plans/myplan").status_code == 404


def test_save_validates_items_with_reasons(client: TestClient):
    client.post("/api/plans", json={"id": "p1"})
    bad = [
        ({"queue": [{"op": "train", "type": "terran/nope"}]}, "不在 catalog"),
        ({"queue": [{"op": "train"}]}, "缺 type"),
        ({"queue": [{"op": "assign_workers", "count": 3}]}, "缺 task"),
        ({"queue": [{"op": "explode", "type": "terran/scv"}]}, "未知队列 op"),
        ({"spawn": "middle"}, "spawn"),
    ]
    for body, needle in bad:
        r = client.put("/api/plans/p1", json=body)
        assert r.status_code == 400, body
        assert needle in r.json()["detail"], (body, r.json()["detail"])


def test_plan_files_survive_restart(tmp_path: Path):
    cat = load_all()
    dir = tmp_path / "plans"
    PlanStore(cat, dir).create({"id": "keep", "title_zh": "落盘",
                                "queue": [{"op": "train", "type": "terran/marine",
                                           "count": 3}]})
    reloaded = PlanStore(cat, dir)
    p = reloaded.get("keep")
    assert p is not None and p["queue"][0]["count"] == 3
    assert reloaded.get("default") is not None, "重开也要有默认规划兜底"


# ---------------- ops → 队列项（默认规划的导出器） ----------------

def test_ops_to_items_merges_consecutive_same_type():
    items = ops_to_items([
        Train("terran/scv"), Train("terran/scv"), Train("terran/scv"),
        Build("terran/supplydepot"),
        Train("terran/marine"),
    ])
    assert [(i.op.value, i.type, i.count) for i in items] == [
        ("train", "terran/scv", 3),
        ("build", "terran/supplydepot", 1),
        ("train", "terran/marine", 1),
    ]


# ---------------- 无会话干跑 ----------------

def test_simulate_needs_no_session(client: TestClient):
    """规划是 authoring 数据：干跑不建会话、不碰会话。"""
    assert client.get("/api/session").json()["state"] == "未连接"
    r = client.post("/api/plans/simulate", json={
        "items": [{"op": "train", "type": "terran/marine", "count": 1}],
        "horizon": 60,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == {"kind": "draft", "plan_id": "draft"}
    assert len(body["points"]) >= 59
    assert body["points"][0]["minerals"] >= 50, "标准开局 50 矿起步"
    assert body["points"][0]["supply_used"] >= 12, "12 SCV 开局"
    # 枪兵需要兵营 —— 没造兵营就是前瞻警报（stalled：前置没）
    stalls = [e for e in body["events"] if e["kind"] == "stalled"]
    assert stalls and stalls[0]["reason"] == "前置没"
    # 前瞻警报与实时警报同一模型（AlertView：severity/text_zh），前端同一组件渲染
    assert any(a["kind"] == "plan_stalled" and a["severity"] == "error"
               and "前置" in a["text_zh"] for a in body["alerts"])
    assert client.get("/api/session").json()["state"] == "未连接", "干跑后也不该起会话"


def test_simulate_reports_skipped_items(client: TestClient):
    """simulate 不拒未知项 —— 进 skipped 带原因返回（不静默；save 才 400）。"""
    r = client.post("/api/plans/simulate", json={
        "items": [{"op": "cancel"}, {"op": "train", "type": "terran/zzz_fake"}],
    })
    assert r.status_code == 200
    reasons = "；".join(s["reason"] for s in r.json()["skipped"])
    assert "取消不进投影" in reasons and "terran/zzz_fake" in reasons


def test_simulate_bad_shape_is_400(client: TestClient):
    """形状错（未知 op / 非法 placement）才 400 —— 那是请求写错了，不是数据问题。"""
    r = client.post("/api/plans/simulate", json={
        "items": [{"op": "explode", "type": "terran/scv"}],
    })
    assert r.status_code == 400 and "未知队列 op" in r.json()["detail"]


def test_simulate_reports_assembly_gap(client: TestClient):
    """I12-B2：规划终局凑不齐装配 target → assembly_gap 前瞻警报（缺口显形）。"""
    r = client.post("/api/plans/simulate", json={
        "items": [
            {"op": "build", "type": "terran/barracks", "count": 1,
             "placement": {"kind": "in_region", "region": "home"}},
            {"op": "train", "type": "terran/marine", "count": 4},
        ],
    })
    assert r.status_code == 200
    gaps = [a for a in r.json()["alerts"] if a["kind"] == "assembly_gap"]
    # DEFAULT_ASSEMBLY 步兵组 target=10；本规划只出 4 个机枪兵
    assert any("步兵组" in a["text_zh"] and "机枪兵" in a["text_zh"]
               for a in gaps)
    assert all(a["severity"] == "warn" for a in gaps)


def test_modules_list_and_from_module_roundtrip(client: TestClient):
    """I12-B3：参考模块一键落地成规划 —— 模板是唯一真相源，不再手抄队列。"""
    rows = client.get("/api/modules").json()
    ids = {m["id"] for m in rows}
    assert {"basic_opening", "bio_tank_opening"} <= ids
    assert all(m["items"] > 0 for m in rows)

    r = client.post("/api/plans/from-module", json={"module": "basic_opening"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["queue"], "模板落地不该是空队列"
    assert plan["title_zh"].startswith("basic_opening")
    # 落地的是真规划文件：读回来一致、可删
    got = client.get(f"/api/plans/{plan['id']}").json()
    assert len(got["queue"]) == len(plan["queue"])
    assert client.delete(f"/api/plans/{plan['id']}").status_code == 200

    # 模块不存在 / params 形态错 → 400 带原因
    assert client.post("/api/plans/from-module",
                       json={"module": "nope"}).status_code == 400
    assert client.post("/api/plans/from-module",
                       json={"module": "basic_opening", "params": 42}).status_code == 400
