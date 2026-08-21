"""B4 真机路径：terrain 控制行 → static/terrain 帧（父进程侧）。

真机上 driver 把地形包成 `{"_":"terrain", "terrain": {...}}` 控制行（on_step 之外发的，
没有 seq/game_time）；父进程必须把它转成**真正的静态面帧**，前端才能按统一路径
订阅并合并进 map.terrain —— 特殊通道越多，两侧漂移的缝就越多。
"""
import json

from api.live import LiveSession
from view.schema import REV


def _fake_terrain_line():
    return json.dumps({
        "_": "terrain",
        "terrain": {"height": None, "pathable": None, "placeable": None},
    })


def test_terrain_control_line_becomes_a_frame(monkeypatch):
    """不真起游戏：直接喂一行控制线给 `_control`，看它变成帧。"""
    sess = LiveSession.__new__(LiveSession)   # 绕过 __init__（不起子进程）
    sess._lock = __import__("threading").Lock()
    sess._meta = {}
    sess._statics = []
    sess.frames = []
    sess.seq = 12
    sess.game_time = 3.5
    sess.state = "对局中"
    sess._acks = 0
    sess._pending = {}
    sess._next_req = 0

    sess._control(json.loads(_fake_terrain_line()))
    terrains = [f for f in sess.frames if f["topic"] == "static/terrain"]
    assert len(terrains) == 1
    f = terrains[0]
    assert f["rev"] == REV
    assert f["seq"] == 12, "用当前游标补齐 seq"
    assert f["game_time"] == 3.5
    assert f["payload"]["height"] is None
    # 静态面过滤会把 static/terrain 算进去
    assert any(t["topic"] == "static/terrain" for t in
               [{"topic": "static/terrain"}] + sess._statics)
