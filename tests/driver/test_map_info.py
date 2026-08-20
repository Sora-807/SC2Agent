"""B4：driver 从 game_info 抽静态地形（只读 SC2，零业务规则）。

守两条：
1. `game_info` 不可用 / 三图都没有 → 返回 None（不伪造全 0 网格）；
2. 三图各自独立：缺哪张哪张是 None，不影响另外两张。
"""
from game.geometry import Grid
from driver.sc2_adapter import extract_map_info


class _Arr:
    """numpy 数组的最小 duck-type（`.tolist()` → 嵌套列表）。"""

    def __init__(self, data):
        self._data = data

    def tolist(self):
        return [list(row) for row in self._data]


class _PixelMap:
    """burnysc2 pixelmap 的最小 duck-type：`data_numpy` 是带 `.tolist()` 的数组对象。"""

    def __init__(self, data, width, height):
        self.data_numpy = _Arr(data)
        self.width = width
        self.height = height


class _GameInfo:
    def __init__(self, height=None, pathing=None, placement=None):
        self.terrain_height = height
        self.pathing_grid = pathing
        self.placement_grid = placement


class _Bot:
    def __init__(self, game_info):
        self.game_info = game_info


def test_no_game_info_returns_none():
    assert extract_map_info(_Bot(None)) is None


def test_empty_game_info_returns_none():
    assert extract_map_info(_Bot(_GameInfo())) is None


def test_all_three_grids_extracted():
    bot = _Bot(_GameInfo(
        height=_PixelMap([[1, 2], [3, 4]], 2, 2),
        pathing=_PixelMap([[1, 0], [1, 0]], 2, 2),
        placement=_PixelMap([[0, 1], [1, 1]], 2, 2),
    ))
    out = extract_map_info(bot)
    assert out is not None
    for key in ("height", "pathable", "placeable"):
        assert isinstance(out[key], Grid)
    assert out["height"].data == [[1, 2], [3, 4]]
    assert out["pathable"].data == [[1, 0], [1, 0]]
    assert out["placeable"].data == [[0, 1], [1, 1]]


def test_missing_one_grid_leaves_it_none():
    bot = _Bot(_GameInfo(height=_PixelMap([[1]], 1, 1)))
    out = extract_map_info(bot)
    assert out is not None
    assert out["height"] is not None
    assert out["pathable"] is None
    assert out["placeable"] is None
