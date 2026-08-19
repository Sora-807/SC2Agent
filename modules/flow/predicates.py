"""flow 谓词：结构化 AST 求值 + V1 谓词目录。

V1 谓词：group_count / game_time / strategy_elapsed / step_elapsed。
AST node：
- {"const": v} / {"param": name} → 字面 / 参数
- {"op": "group_count", "args": [slot, type?]} → int
- {"op": "game_time"} / {"op": "strategy_elapsed"} / {"op": "step_elapsed"} → float
- {"op": ">="|">"|"<="|"<"|"=="|"!="|"and"|"or"|"not", "args": [...]} → bool
"""
from __future__ import annotations

from dataclasses import dataclass

from game import GameState, Owner, Point2

from tactical_map.spatial import center_of_units, distance, units_within


@dataclass
class EvalCtx:
    gs: GameState
    allocator: object  # duck-typed：count(group_id, type=None), expand(group_id, type)
    bindings: dict  # slot -> group_id
    params: dict
    variables: dict
    strategy_start: float
    step_entered: float
    registry: object = None  # tactical_map.PointRegistry


def eval_when(node, ctx: EvalCtx):
    if isinstance(node, dict):
        if "const" in node:
            return node["const"]
        if "param" in node:
            return ctx.params.get(node["param"])
        op = node.get("op")
        args = [eval_when(a, ctx) for a in node.get("args", [])]
        if op == "group_count":
            slot = args[0]
            type_name = args[1] if len(args) > 1 else None
            return _group_count(ctx, slot, type_name)
        if op == "game_time":
            return ctx.gs.game_time
        if op == "strategy_elapsed":
            return ctx.gs.game_time - ctx.strategy_start
        if op == "step_elapsed":
            return ctx.gs.game_time - ctx.step_entered
        if op == ">=":
            return args[0] >= args[1]
        if op == ">":
            return args[0] > args[1]
        if op == "<=":
            return args[0] <= args[1]
        if op == "<":
            return args[0] < args[1]
        if op == "==":
            return args[0] == args[1]
        if op == "!=":
            return args[0] != args[1]
        if op == "and":
            return all(args)
        if op == "or":
            return any(args)
        if op == "not":
            return not args[0]
        if op == "arrived":
            return _p_arrived(ctx, *args)
        if op == "group_center":
            return _p_group_center(ctx, args[0])
        if op == "distance_between":
            return _p_distance_between(ctx, args[0], args[1])
        if op == "enemy_count_near":
            return _p_enemy_count_near(ctx, args[0], args[1])
        if op == "group_hp_ratio":
            return _p_group_hp_ratio(ctx, args[0])
        if op == "region_center":
            return _p_region_center(ctx, args[0])
        if op == "unit_count":
            return _p_unit_count(ctx, args[0])
        raise ValueError(f"unknown op {op!r}")
    return node  # literal


def _group_count(ctx: EvalCtx, slot, type_name=None) -> int:
    gid = ctx.bindings.get(slot)
    if gid is None:
        return 0
    return ctx.allocator.count(gid, type_name)


def _group_units(ctx: EvalCtx, slot) -> list:
    """slot -> group 的 leased units（从 gs 按 expand_all 的 tag 取）。"""
    gid = ctx.bindings.get(slot)
    if gid is None:
        return []
    tags = set(ctx.allocator.expand_all(gid))
    return [u for u in ctx.gs.units if u.tag in tags]


def _resolve_target(val, ctx: EvalCtx) -> Point2 | None:
    """Point2 / (x,y) / 区域名(走 registry) -> Point2 | None。"""
    if val is None:
        return None
    if isinstance(val, Point2):
        return val
    if isinstance(val, (tuple, list)) and len(val) >= 2:
        return Point2(float(val[0]), float(val[1]))
    if isinstance(val, str) and ctx.registry is not None:
        return ctx.registry.region_center(val)
    return None


def _p_arrived(ctx: EvalCtx, slot, target, radius) -> bool:
    c = center_of_units(_group_units(ctx, slot))
    if c is None:
        return False
    t = _resolve_target(target, ctx)
    if t is None:
        return False
    return distance(c, t) <= float(radius)


def _p_group_center(ctx: EvalCtx, slot) -> Point2 | None:
    return center_of_units(_group_units(ctx, slot))


def _p_distance_between(ctx: EvalCtx, slot_a, slot_b) -> float:
    a = center_of_units(_group_units(ctx, slot_a))
    b = center_of_units(_group_units(ctx, slot_b))
    if a is None or b is None:
        return float("inf")
    return distance(a, b)


def _p_enemy_count_near(ctx: EvalCtx, anchor, radius) -> int:
    t = _resolve_target(anchor, ctx)
    if t is None:
        return 0
    enemies = [u for u in ctx.gs.units if u.owner == Owner.ENEMY]
    return len(units_within(t, enemies, float(radius)))


def _p_group_hp_ratio(ctx: EvalCtx, slot) -> float:
    units = _group_units(ctx, slot)
    if not units:
        return 0.0
    ratios = [u.hp / u.hp_max for u in units if u.hp_max > 0]
    return sum(ratios) / len(ratios) if ratios else 0.0


def _p_region_center(ctx: EvalCtx, name) -> Point2 | None:
    if ctx.registry is None:
        return None
    return ctx.registry.region_center(name)


def _p_unit_count(ctx: EvalCtx, type_name) -> int:
    return sum(1 for u in ctx.gs.units if u.type_name == type_name and u.owner == Owner.SELF)
