"""view.encode：dataclass → JSON 可序列化 dict + 栅格编码。

只做机械转换，不做任何业务判断。两条规则值得留意：
1. `Pt`/`Cell` 在 schema 里已经是 tuple，这里只把 tuple 转成 list（JSON 无 tuple）；
2. 契约里有个字段名是 `from`（Python 保留字），schema 用 `from_step` 承载，
   由各 dataclass 的 `RENAME` 类属性声明改名 —— 改名声明与字段定义放在一起，不易失配。
"""
from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from game.geometry import Grid

from view.schema import REV, TOPICS, Envelope, GridB64


def grid_to_b64(grid: Grid | None) -> GridB64 | None:
    """None 进 None 出 —— 缺哪张图发 null，别伪造全 0 网格。"""
    """Grid（data[y][x] 的 int）→ 行主序 uint8 + base64。

    值域超 uint8 会被截断，所以这里显式夹到 0..255：creep/visibility/区域标签都远小于 255，
    真超了说明上游语义变了，宁可看到夹紧后的异常也不要静默产生错位的位图。
    """
    if grid is None:
        return None
    buf = bytearray(grid.width * grid.height)
    i = 0
    for row in grid.data:
        for v in row:
            buf[i] = 0 if v < 0 else (255 if v > 255 else int(v))
            i += 1
    return GridB64(w=grid.width, h=grid.height, data_b64=base64.b64encode(bytes(buf)).decode("ascii"))


def to_json(obj: Any) -> Any:
    """递归转成 JSON 可序列化结构（dataclass/dict/list/tuple/Enum/primitive）。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        rename: dict[str, str] = getattr(type(obj), "RENAME", {})
        out: dict[str, Any] = {}
        for f in fields(obj):
            out[rename.get(f.name, f.name)] = to_json(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_json(v) for v in obj]
    raise TypeError(f"view.encode 不知道怎么编码 {type(obj).__name__}（不静默：请显式转换）")


def envelope(topic: str, seq: int, game_time: float, payload: Any, wall_ms: int) -> dict:
    """打一条信封并直接转成 dict（写盘/发 WS 用）。"""
    if topic not in TOPICS:
        raise ValueError(f"未知 topic {topic!r}（契约 §2.1 的闭集）")
    return to_json(
        Envelope(topic=topic, seq=seq, game_time=round(float(game_time), 3),
                 wall_ms=int(wall_ms), payload=payload, rev=REV)
    )