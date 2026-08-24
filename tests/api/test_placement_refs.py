"""placement 点名引用（I8 → PLAN-V2 批 2 直通语义）。

两层各守一条：
1. **预设固定建造名可解析**：会话装配把 36 个命名预留区挂进 layer.pos_marks ——
   `exact.mark = "蓝方主矿气井1"` 在 runtime 的 placement 解析里就是合法目标；
2. **`规划id/点位名` 引用直通**（批 2：会话图层 = 全部规划命名空间键合并）——
   REST 层不再剥前缀/拒绝跨规划；解析在 runtime 的 placement 层，不存在的
   标记执行期 dropped（作者错误，不静默）。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from game.catalog import load_all
from view.proposals import parse_item


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    c = TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                              plans_dir=tmp_path / "plans",
                              map_plans_dir=tmp_path / "map-plans"))
    c.post("/api/map-plans", json={"id": "agent-r1", "copy_from": "layout"})
    return c


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


# ---------------- 命令面：引用直通（批 2 语义） ----------------

def _submit(api: TestClient, mark: str):
    sess = api.app.state.session
    return api.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main", "items": [
            {"op": "build", "type": "terran/refinery", "count": 1,
             "placement": {"kind": "exact", "mark": mark}}]})


def test_qualified_ref_passes_through_and_resolves_in_merged_layer(api: TestClient):
    """`规划id/槽位名` 原样进队（REST 不剥前缀）—— 解析靠合并图层的命名空间键。"""
    api.post("/api/session/start", params={"autotick": "false",
                                           "map_plan": "default"})
    r = _submit(api, "agent-r1/D1")
    assert r.status_code == 200, r.text
    q = api.app.state.session.runtime.queue("main")
    assert q.items[0].placement.mark == "agent-r1/D1", "引用原样保留（钉在规划上）"
    # 合并图层里真的有这个键（默认是 default 也不影响 —— 全部规划都可寻址）
    assert "agent-r1/D1" in api.app.state.session.layer.build_slots


def test_cross_plan_ref_no_longer_rejected_at_rest(api: TestClient):
    """跨规划引用不再 400（旧的「会话装的不是这份规划就拒」逻辑已删）——
    不存在的标记在执行期 dropped（作者错误），请求本身是合法的。"""
    api.post("/api/session/start", params={"autotick": "false",
                                           "map_plan": "agent-r1"})
    r = _submit(api, "ghost-plan/R9")
    assert r.status_code == 200, r.text
    q = api.app.state.session.runtime.queue("main")
    assert q.items[0].placement.mark == "ghost-plan/R9"


def test_queue_command_bare_preset_name_needs_no_plan(api: TestClient):
    """预设名是全局命名空间：出厂模板会话上裸用也应该能进队（解析在 runtime）。"""
    api.post("/api/session/start", params={"autotick": "false"})
    r = _submit(api, "蓝方主矿气井1")
    assert r.status_code == 200, r.text
    q = api.app.state.session.runtime.queue("main")
    assert q.items[0].placement.mark == "蓝方主矿气井1"


def test_parse_item_accepts_slash_marks_without_mutation():
    """纯函数面：带斜杠的 mark 往返不变（直通语义的最小锁）。"""
    it = parse_item({"op": "build", "type": "terran/supplydepot",
                     "placement": {"kind": "exact", "mark": "p1/D2"}})
    assert it.placement.mark == "p1/D2"
