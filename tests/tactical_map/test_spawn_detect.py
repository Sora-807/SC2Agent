"""真机随机出生点检测（2026-08-24 事故修）。

事故：真机出生 tr，但会话构造期锚点写死 bl（cc=30.5,30.5）→ 经济维持器把工人
派去左下采矿（横穿全图，零收入）、槽位/地图层全错。修法：sc2 首帧实测我方 CC →
`pick_spawn_layout` 就近选 bl/tr 分支 → 重建 layer 并同步给引擎/经济/生产/产帧四方。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "modules", ROOT / "tools", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_all
from tactical_map.base import instantiate_spawn, load_ladder_map, pick_spawn_layout

CAT = load_all()
TPL = load_ladder_map()


def test_pick_spawn_layout_nearest_branch():
    cc_bl = Point2(48.5, 28.5)     # 模板 bl origin
    cc_tr = Point2(127.5, 119.5)   # 模板 tr origin
    key_bl, _ = pick_spawn_layout(TPL, cc_bl)
    key_tr, _ = pick_spawn_layout(TPL, cc_tr)
    assert key_bl == "bl" and key_tr == "tr"
    # 偏一点也归就近分支
    key, _ = pick_spawn_layout(TPL, Point2(120.0, 115.0))
    assert key == "tr"


def test_session_detect_spawn_rebuilds_layer_for_tr():
    """sc2 首帧发现 CC 在右上 → layer 重建到实测位置，四方持有者全部换层。"""
    from run_session import Session

    sess = Session(driver="sc2", reader=_SilentReader(), cc=Point2(30.5, 30.5))
    assert sess._spawn_detected is False, "sc2 会话构造期不该标记已检测"

    gs = _gs_with_cc_at(Point2(127.5, 119.5))
    key = sess._detect_spawn(gs)
    assert key == "tr"
    # layer 锚点跟着实测 CC 走（不再是写死的左下）
    big = sess.layer.big_regions.get(sess.layer.big_index.get(
        sess.layer.big_grid.data[0][0]))
    assert abs(big.anchor.x - 127.5) < 1.0 and abs(big.anchor.y - 119.5) < 1.0
    # 四方持有者同一份新层（经济锚点/引擎解析/摆放/静态面）
    assert sess.keeper._region_layer is sess.layer
    assert sess.engine._region_layer is sess.layer
    assert sess.runtime._region_layer is sess.layer
    assert sess.producer.region_layer is sess.layer
    assert sess.producer.spawn == "tr"


def test_session_detect_spawn_keeps_layer_when_cc_missing():
    from run_session import Session

    sess = Session(driver="sc2", reader=_SilentReader(), cc=Point2(30.5, 30.5))
    before = sess.layer
    assert sess._detect_spawn(_gs_with_cc_at(None)) is None
    assert sess.layer is before, "找不到 CC = 保持临时假定层（如实，不伪造）"


def test_instantiate_spawn_translates_to_actual_cc():
    """模板平移语义（既有行为锁）：bl 分支平移到 tr 的 CC，槽位跟着走。"""
    _, layout = pick_spawn_layout(TPL, Point2(48.5, 28.5))
    base_slot = layout.build_slots[0]
    layer = instantiate_spawn(TPL, layout, Point2(127.5, 119.5))
    slot = layer.build_slots[base_slot.name]
    assert abs(slot.tl.x - (127.5 - 48.5 + base_slot.tl.x)) < 1e-6


class _SilentReader:
    def drain(self):
        return []


def _gs_with_cc_at(pos):
    units = []
    if pos is not None:
        units.append(Unit(tag=1, type_name="COMMANDCENTER", position=pos, owner=Owner.SELF,
                          hp=1500.0, hp_max=1500.0, shield=0.0, energy=0.0, build_progress=1.0))
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=50, vespene=0,
                     supply_used=12, supply_cap=15, units=units,
                     map_size=(176, 160), creep=g, visibility=g)
