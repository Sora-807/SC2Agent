"""static/terrain（rev 9）：地形帧的编码与 null 语义。"""
import base64
import json

from game.geometry import Grid
from view.encode import to_json
from view.statics import terrain_static


def test_none_in_none_out():
    """缺哪张图发 null，不伪造全 0 网格。"""
    t = terrain_static({})
    assert t.height is None and t.pathable is None and t.placeable is None
    d = to_json(t)
    assert d == {"height": None, "pathable": None, "placeable": None}


def test_grids_encoded_as_row_major_base64():
    g = Grid(2, 2, [[0, 1], [2, 3]])
    d = to_json(terrain_static({"height": g, "pathable": None, "placeable": None}))
    assert d["pathable"] is None
    assert list(base64.b64decode(d["height"]["data_b64"])) == [0, 1, 2, 3]
    assert (d["height"]["w"], d["height"]["h"], d["height"]["bpp"]) == (2, 2, 8)


def test_json_serializable():
    g = Grid(2, 1, [[0, 1]])
    d = to_json(terrain_static({"height": g, "pathable": g, "placeable": g}))
    json.dumps(d, ensure_ascii=False)   # 不抛 = 可进信封
