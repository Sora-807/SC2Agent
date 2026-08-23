"""I8 收口（P3 切片 3）：placement 点名引用。

两层各守一条：
1. **预设固定建造名可解析**：会话装配把 36 个命名预留区挂进 layer.pos_marks ——
   `exact.mark = "蓝方主矿气井1"` 在 runtime 的 placement 解析里就是合法目标；
2. **「规划名/点位名」限定引用在 REST 层映射**：命令面收 "P/name"，会话装的是 P
   就剥前缀；装的不是 P（或没装规划）→ 400 带结构化理由。ViewFrame 零改动。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from game.catalog import load_all
from view.plans import resolve_placement_refs
from view.proposals import parse_item


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                              plans_dir=tmp_path / "plans",
                              map_plans_dir=tmp_path / "map-plans"))
    c.post("/api/map-plans", json={"id": "agent-r1", "copy_from": "layout-bl"})
    return c


# ---------------- 纯函数：限定引用映射 ----------------

def _item(mark: str):
    return parse_item({"op": "build", "type": "terran/refinery", "count": 1,
                       "placement": {"kind": "exact", "mark": mark}})


def test_matching_plan_prefix_is_stripped():
    out, err = resolve_placement_refs([_item("agent-r1/rax9")], "agent-r1")
    assert err is None
    assert out[0].placement.mark == "rax9"


def test_bare_mark_and_region_pass_through():
    bare = _item("蓝方主矿气井1")
    region = parse_item({"op": "build", "type": "terran/supplydepot",
                         "placement": {"kind": "in_region", "region": "home"}})
    out, err = resolve_placement_refs([bare, region], "agent-r1")
    assert err is None
    assert out[0].placement.mark == "蓝方主矿气井1"
    assert out[1].placement.region == "home"


def test_foreign_plan_ref_is_rejected_with_reason():
    _, err = resolve_placement_refs([_item("other-plan/rax9")], "agent-r1")
    assert err is not None and "other-plan" in err and "agent-r1" in err


def test_ref_without_session_plan_is_rejected():
    _, err = resolve_placement_refs([_item("agent-r1/rax9")], None)
    assert err is not None and "出厂模板" in err


# ---------------- 会话装配：预设名进 layer ----------------

def test_offline_session_layer_carries_reserved_names(api: TestClient):
    api.post("/api/session/start", params={"autotick": "false",
                                           "map_plan": "agent-r1"})
    layer = api.app.state.session.layer
    marks = layer.pos_marks
    assert "蓝方主矿" in marks and "蓝方二矿" in marks and "红方主矿气井1" in marks
    named = [n for n in marks if n not in ("rally",)]
    assert len([n for n in named if "矿" in n or "气井" in n]) >= 36


def test_reserved_mark_pos_is_geyser_center_not_box_corner(api: TestClient):
    """蓝方主矿气井1 的落点 = 气井中心（= 采集数据里的气井坐标，奇数 footprint）。"""
    from tactical_map.reserved import reserved_boxes

    api.post("/api/session/start", params={"autotick": "false"})
    layer = api.app.state.session.layer
    geyser = next(b for b in reserved_boxes(load_all())
                  if b.get("name") == "蓝方主矿气井1")
    cx = (geyser["tl"][0] + geyser["br"][0]) / 2
    cy = (geyser["tl"][1] + geyser["br"][1]) / 2
    m = layer.pos_marks["蓝方主矿气井1"]
    assert (m.pos.x, m.pos.y) == (cx, cy)


# ---------------- 命令面：REST 层映射 ----------------

def _submit(api: TestClient, mark: str):
    sess = api.app.state.session
    return api.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main", "items": [
            {"op": "build", "type": "terran/refinery", "count": 1,
             "placement": {"kind": "exact", "mark": mark}}]})


def test_queue_command_acceptes_qualified_ref_for_loaded_plan(api: TestClient):
    api.post("/api/session/start", params={"autotick": "false",
                                           "map_plan": "agent-r1"})
    r = _submit(api, "agent-r1/蓝方主矿气井1")
    assert r.status_code == 200, r.text
    q = api.app.state.session.runtime.queue("main")
    assert q.items[0].placement.mark == "蓝方主矿气井1", "前缀应已在 REST 层剥掉"


def test_queue_command_rejectes_cross_plan_ref(api: TestClient):
    api.post("/api/session/start", params={"autotick": "false",
                                           "map_plan": "agent-r1"})
    r = _submit(api, "other-plan/rax9")
    assert r.status_code == 400
    assert "other-plan" in r.json()["detail"] and "agent-r1" in r.json()["detail"]


def test_queue_command_rejectes_qualified_ref_on_default_template(api: TestClient):
    api.post("/api/session/start", params={"autotick": "false"})
    r = _submit(api, "agent-r1/rax9")
    assert r.status_code == 400
    assert "出厂模板" in r.json()["detail"]


def test_queue_command_bare_preset_name_needs_no_plan(api: TestClient):
    """预设名是全局命名空间：出厂模板会话上裸用也应该能进队（解析在 runtime）。"""
    api.post("/api/session/start", params={"autotick": "false"})
    r = _submit(api, "蓝方主矿气井1")
    assert r.status_code == 200, r.text
    q = api.app.state.session.runtime.queue("main")
    assert q.items[0].placement.mark == "蓝方主矿气井1"
