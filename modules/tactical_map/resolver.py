"""tactical_map.resolver：目标解析（ADR-0029 D1：名字 → 坐标的单一权威实现）。

三层职责：
- game 管词汇（OP_CATALOG 参数类型）
- 这里管语义：静态名（区域锚点/点位名）与字面量 → Point2；纯函数，离线在线同结果
- engine 管时机：emit Operation 前调用 resolve_action_params
动态目标（group_center/nearest_enemy）由 engine 先求值成 Point2/tag，不经过这里。
"""
from __future__ import annotations

from game import Point2
from game.operation import OP_CATALOG

from tactical_map.region import RegionLayer


def resolve_target(val, layer: RegionLayer | None) -> Point2 | None:
    """静态目标 → Point2 | None。

    接受：Point2 / (x, y) 序列 / 名字（leaf 区域锚点、大区锚点或点位标记）。
    未知名或 layer 为 None 时返回 None（调用方决定保留原值还是报错）。
    """
    if val is None:
        return None
    if isinstance(val, Point2):
        return val
    if isinstance(val, (tuple, list)) and len(val) >= 2:
        return Point2(float(val[0]), float(val[1]))
    if isinstance(val, str) and layer is not None:
        return layer.anchor(val)
    return None


def _to_pair(p: Point2) -> list[float]:
    return [p.x, p.y]


def resolve_action_params(action: str, params: dict, layer: RegionLayer | None) -> dict:
    """按 OP_CATALOG 的参数类型解析 action 的 params（ADR-0029 D1）。

    point/points 型参数（position/positions）解析为 [x, y]；其余参数（type/target_unit/…）原样。
    解析失败的未知名原样保留 → driver 应用时静默失败（D6/V1 降级路径；编译期校验后补）。
    """
    spec = OP_CATALOG.get(action, [])
    out = dict(params)
    for name, ptype, _required in spec:
        if ptype == "point" and name in out:
            p = resolve_target(out[name], layer)
            out[name] = _to_pair(p) if p is not None else out[name]
        elif ptype == "points" and name in out:
            pts: list = []
            for item in (out[name] or []):
                p = resolve_target(item, layer)
                pts.append(_to_pair(p) if p is not None else item)
            out[name] = pts
    return out
