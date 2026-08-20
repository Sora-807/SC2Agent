"""view.encode：编码只做机械转换，且不静默。"""
from dataclasses import dataclass

import pytest

from game.geometry import Grid
from view.encode import envelope, grid_to_b64, to_json
from view.schema import REV, BranchHitView, TransitionView


def test_grid_to_b64_row_major():
    """data[y][x] 行主序展平；解出来要能还原原网格。"""
    import base64

    g = Grid(width=3, height=2, data=[[1, 2, 3], [4, 5, 6]])
    b = grid_to_b64(g)
    assert (b.w, b.h, b.bpp) == (3, 2, 8)
    assert list(base64.b64decode(b.data_b64)) == [1, 2, 3, 4, 5, 6]


def test_grid_clamped_not_wrapped():
    """越界值夹到 0..255，而不是溢出成错位位图。"""
    import base64

    g = Grid(width=2, height=1, data=[[-5, 300]])
    assert list(base64.b64decode(grid_to_b64(g).data_b64)) == [0, 255]


def test_to_json_nested_dataclass_and_tuple():
    d = to_json(BranchHitView(step_id="advance", branch_id="b_arrive", index=2))
    assert d == {"step_id": "advance", "branch_id": "b_arrive", "index": 2}


def test_to_json_renames_reserved_word():
    """契约字段名是 `from`（Python 保留字）→ schema 用 from_step 承载，编码时改名。"""
    d = to_json(TransitionView(from_step="garrison", to="tank_hop", kind="done", reason="READY", at=604.0))
    assert d["from"] == "garrison"
    assert "from_step" not in d
    assert d["to"] == "tank_hop"


def test_to_json_refuses_unknown_type():
    """不静默：编不了的类型当场报错，而不是塞个 str(obj)。"""

    class Weird:
        pass

    with pytest.raises(TypeError, match="不知道怎么编码"):
        to_json(Weird())


def test_envelope_carries_rev_and_rejects_unknown_topic():
    e = envelope("frame/session", seq=7, game_time=12.3456, payload={"a": 1}, wall_ms=99)
    assert e["rev"] == REV
    assert e["seq"] == 7
    assert e["game_time"] == 12.346          # 三位小数
    assert e["payload"] == {"a": 1}
    with pytest.raises(ValueError, match="未知 topic"):
        envelope("frame/nope", seq=1, game_time=0, payload={}, wall_ms=0)


def test_dataclass_field_order_is_stable():
    """字段顺序稳定（JSON diff 才有意义；前端 zod 不依赖顺序但人 review 依赖）。"""

    @dataclass(slots=True)
    class Two:
        a: int
        b: int

    assert list(to_json(Two(a=1, b=2))) == ["a", "b"]
