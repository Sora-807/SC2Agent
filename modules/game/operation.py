"""game.operation：driver 边界动作（Operation + 操作目录 OP_CATALOG）。

"操作在哪定义"的单一权威源（docs/P0-影响边界.md D2 的代码版）。
- engine/生产运行时 产 Operation → driver 翻译成 burnysc2 命令（下一 step 生效）。
- flow 用它校验 action_atom（编译期拒未知 action / 缺参数）。
- 加新 action = 在 OP_CATALOG 加一条 + driver 加对应翻译函数（TRANSLATORS）。
"""
from __future__ import annotations

from dataclasses import dataclass

# action -> [(param_name, type, required), ...]
# type 含义：
#   point      坐标 [x, y]（如 move_to 的 position）
#   tag        单位 tag 整数（如 focus_fire 的 target_unit）
#   int        整数（如 count）
#   str        字符串（如 cancel 的 order 引用）
#   points     坐标列表 [[x,y],...]（如 patrol 的 positions）
#   stable_id  稳定类型 ID 字符串（如 build 的 type "terran/barracks"）
#   task       任务名（assign_workers 的 task：mineral|gas|idle）
OP_CATALOG: dict[str, list[tuple[str, str, bool]]] = {
    "move_to":        [("position", "point", True)],
    "attack_move_to": [("position", "point", True)],
    "hold_position":  [],
    "stop":           [],
    "follow":         [("target_unit", "tag", True)],
    "patrol":         [("positions", "points", True)],
    "focus_fire":     [("target_unit", "tag", True)],
    "build":          [("type", "stable_id", True), ("position", "point", True)],
    "train":          [("type", "stable_id", True)],
    "research":       [("type", "stable_id", True)],
    "load":           [("target_unit", "tag", True)],
    "unload":         [("position", "point", True)],
    "use_ability":    [("ability", "stable_id", True)],
    "cancel":         [("order", "str", True)],
    "morph":          [("type", "stable_id", True)],
}


@dataclass(slots=True)
class Operation:
    """driver 边界动作（unit 级）。engine 产 → driver 翻译成 burnysc2 命令，下一 step 生效。"""

    op_id: int  # 单调递增 ID（追踪/去重）
    unit_tags: list[int]  # 目标单位 tag 列表（engine 从 group lease 展开）
    action: str  # 稳定 action 名（OP_CATALOG 中，如 "move_to"/"build"/"train"）
    params: dict  # 参数（如 {position:[x,y]} / {type:"terran/barracks"} / {target_unit:tag}）
    seq: int  # 提交时 GameState seq（关联到哪一帧）


def is_known_action(action: str) -> bool:
    return action in OP_CATALOG


def validate_op(op: Operation) -> list[str]:
    """返回错误清单（空=合法）。V1 只查 action 已知 + required 参数齐；类型检查后补。"""
    params = OP_CATALOG.get(op.action)
    if params is None:
        return [f"unknown action {op.action!r}"]
    errors: list[str] = []
    p = op.params or {}
    for name, _typ, required in params:
        if required and name not in p:
            errors.append(f"{op.action}: missing required param {name!r}")
    return errors
