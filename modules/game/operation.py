"""game.operation：driver 边界动作（Operation + 操作目录 OP_CATALOG）。

"操作在哪定义"的单一权威源（docs/P0-影响边界.md D2 的代码版）。
- engine/生产运行时 产 Operation → driver 翻译成 burnysc2 命令（下一 step 生效）。
- flow 用它校验 action_atom（编译期拒未知 action / 缺参数）。
- 加新 action = 在 OP_CATALOG 加一条 + driver 加对应翻译函数（TRANSLATORS）。
- 本目录只定义词汇；map 名 → 坐标的解析归 tactical_map.resolver（ADR-0029 D1）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ParamType(str, Enum):
    """OP_CATALOG 参数类型（闭集控制词：validate_op/resolver 按它分支）。"""

    POINT = "point"        # 坐标 [x,y] 或 map 名（如 move_to 的 position；engine 经 resolver 解析成数值）
    TAG = "tag"            # 单位 tag 整数（如 focus_fire 的 target_unit）
    INT = "int"            # 整数（如 count）
    STR = "str"            # 字符串（如 cancel 的 order 引用）
    POINTS = "points"      # 坐标列表 [[x,y],...]（如 patrol 的 positions）
    STABLE_ID = "stable_id"  # 稳定类型 ID 字符串（如 build 的 type "terran/barracks"）
    TASK = "task"          # 任务名（assign_workers 的 task：mineral|gas|idle）


# action -> [(param_name, ParamType, required), ...]
OP_CATALOG: dict[str, list[tuple[str, ParamType, bool]]] = {
    "move_to":        [("position", ParamType.POINT, True)],
    "attack_move_to": [("position", ParamType.POINT, True)],
    "hold_position":  [],
    "stop":           [],
    "follow":         [("target_unit", ParamType.TAG, True)],
    "patrol":         [("positions", ParamType.POINTS, True)],
    "focus_fire":     [("target_unit", ParamType.TAG, True)],
    "build":          [("type", ParamType.STABLE_ID, True), ("position", ParamType.POINT, True)],
    "train":          [("type", ParamType.STABLE_ID, True)],
    "research":       [("type", ParamType.STABLE_ID, True)],
    "assign_workers": [("task", ParamType.TASK, True), ("count", ParamType.INT, True)],
    "load":           [("target_unit", ParamType.TAG, True)],
    "unload":         [("position", ParamType.POINT, True)],
    "use_ability":    [("ability", ParamType.STABLE_ID, True)],
    "cancel":         [("order", ParamType.STR, True)],
    "morph":          [("type", ParamType.STABLE_ID, True)],
}


@dataclass(slots=True)
class Operation:
    """driver 边界动作（unit 级）。engine 产 → driver 翻译成 burnysc2 命令，下一 step 生效。"""

    op_id: int  # 单调递增 ID（追踪/去重）
    unit_tags: list[int]  # 目标单位 tag 列表（engine 从 group lease 展开）
    action: str  # 稳定 action 名（OP_CATALOG 的 key，如 "move_to"/"build"/"train"）
    params: dict  # 参数（schema 随 action 变化，见 OP_CATALOG；如 {position:[x,y]}/{target_unit:tag}）
    seq: int  # 提交时 GameState seq（关联到哪一帧）


def is_known_action(action: str) -> bool:
    """action 是否在 OP_CATALOG 中（flow 编译期校验用）。"""
    return action in OP_CATALOG


def validate_op(op: Operation) -> list[str]:
    """返回错误清单（空=合法）。V1 查 action 已知 + required 参数齐；参数值类型检查后补。"""
    params = OP_CATALOG.get(op.action)
    if params is None:
        return [f"unknown action {op.action!r}"]
    errors: list[str] = []
    p = op.params or {}
    for name, _typ, required in params:
        if required and name not in p:
            errors.append(f"{op.action}: missing required param {name!r}")
    return errors
