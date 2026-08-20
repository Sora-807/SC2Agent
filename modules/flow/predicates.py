"""flow.predicates：结构化 AST 条件求值 + V1 谓词目录（spec-003 §4.1/§4.4 子集）。

AST node 形态：
- {"const": v} / {"param": name} / {"var": name} → 字面 / 参数 / 持久变量
- {"op": "...", "args": [...]} → 谓词/比较/逻辑
比较：>= > <= < == !=；逻辑：and or not。

词表三份（编译期校验用，见 flow.manifest.validate_strategy）：
- KNOWN_PREDICATE_OPS：已实现
- UNIMPLEMENTED_PREDICATE_OPS：spec 里有、V1 未实现（带原因；编译期/求值期都明确拒绝）
- COMPARISON_OPS / LOGIC_OPS：运算符
spec-003 验收 #5：原子目录外的新条件在编译期被拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass

from game import GameState, Owner, Point2

from tactical_map.region import RegionLayer
from tactical_map.resolver import resolve_target
from tactical_map.spatial import center_of_units, distance, units_within

COMPARISON_OPS = frozenset({">=", ">", "<=", "<", "==", "!="})
LOGIC_OPS = frozenset({"and", "or", "not"})

KNOWN_PREDICATE_OPS = frozenset({
    "group_count", "game_time", "strategy_elapsed", "step_elapsed",
    "arrived", "group_center", "distance_between", "enemy_count_near",
    "group_hp_ratio", "region_center", "unit_count",
    "group_center_in_region", "enemy_visible_in", "has_building",
})

# spec-003 §4.1 中 V1 未实现的谓词 → 原因（编译期/求值期都明确拒绝，不静默）
UNIMPLEMENTED_PREDICATE_OPS: dict[str, str] = {
    "engaged": "需引擎从 orders/攻击射程/近期事件推导交火状态",
    "under_attack": "需引擎从 hp 历史推导受击状态",
    "has_ready_base": "基地类型目录（三族 town hall）落 catalog 后实现",
    "timer_elapsed": "计时器运行时（start_timer/stop_timer 的 deadline 存储）待建",
    "event_occurred": "引擎事件流（GameEvent 目录 D7）待建",
    "user_cancel": "用户接管/取消通道待建",
}

# spec-003 §4.4 空间工具中 V1 未实现的（只读工具；消费方是 when AST）
UNIMPLEMENTED_SPATIAL_OPS: dict[str, str] = {
    "nearest_units": "需要 selector/筛选词表（spec-003 §4.3），待建",
    "cluster_centers": "需要聚类算法，待建",
}

# 已实现的空间值工具（消费方是 when AST 的 args / 动作参数；T4 起逐个加）
KNOWN_SPATIAL_OPS = frozenset({"point_toward"})


@dataclass
class EvalCtx:
    gs: GameState
    allocator: object  # duck-typed：count(group_id, type=None), expand_all(group_id)
    bindings: dict  # slot -> group_id
    params: dict
    variables: dict
    strategy_start: float
    step_entered: float
    region_layer: RegionLayer | None = None  # 区域模型（名字→锚点/归属，ADR-0029）
    catalog: object = None  # game.Catalog（形态变体归一化；None 透传，T3）


def _normalize_type(catalog, type_name: str) -> str:
    """形态变体归一化（T3）：catalog 为 None 时原样返回（离线/单测兼容）；
    有 catalog 时把变体名（如 SIEGETANKSIEGED）归一到主名（SIEGETANK），
    使架起后实体仍算作原组成员。主名/未知名原样返回（宽容，不报错）。"""
    if catalog is None:
        return type_name
    return catalog.normalize_burnysc2_name(type_name)


def eval_when(node, ctx: EvalCtx):
    """条件 AST 求值（只读、确定性；绑定当前 GameState seq，spec-003 §4.1）。"""
    if isinstance(node, dict):
        if "const" in node:
            return node["const"]
        if "param" in node:
            return ctx.params.get(node["param"])
        if "var" in node:
            return ctx.variables.get(node["var"])
        op = node.get("op")
        if op in UNIMPLEMENTED_PREDICATE_OPS:
            raise ValueError(f"predicate {op!r} 未实现：{UNIMPLEMENTED_PREDICATE_OPS[op]}")
        if op in UNIMPLEMENTED_SPATIAL_OPS:
            raise ValueError(f"spatial tool {op!r} 未实现：{UNIMPLEMENTED_SPATIAL_OPS[op]}")
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
        if op == "point_toward":
            return point_toward(args[0], args[1], args[2], ctx.region_layer)
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
        if op == "group_center_in_region":
            return _p_group_center_in_region(ctx, *args)
        if op == "enemy_visible_in":
            return _p_enemy_visible_in(ctx, args[0])
        if op == "has_building":
            return _p_has_building(ctx, *args)
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


def group_center(ctx: EvalCtx, slot) -> Point2 | None:
    """slot group 的单位质心（公开版：谓词与 engine 的动作参数求值共用）。"""
    return center_of_units(_group_units(ctx, slot))


def point_toward(from_val, toward_val, dist, layer=None) -> Point2 | None:
    """从 from 朝 toward 方向延伸 dist 距离的点（T4）。

    from/toward 接受 Point2/(x,y)序列/点位名（经 resolve_target 解析）；dist 为数值。
    from==toward 或 dist<=0 → 返回 from；任一端为 None（含未知名）→ None（arrived 判 false，不崩）。
    layer=None 时点位名解析返回 None；eval_when/_eval_value 从 ctx.region_layer 注入。
    """
    f = resolve_target(from_val, layer)
    t = resolve_target(toward_val, layer)
    if f is None or t is None:
        return None
    try:
        d = float(dist)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return f
    dx, dy = t.x - f.x, t.y - f.y
    mag = (dx * dx + dy * dy) ** 0.5
    if mag == 0:
        return f  # from==toward
    return Point2(f.x + dx / mag * d, f.y + dy / mag * d)


def _resolve_target(val, ctx: EvalCtx) -> Point2 | None:
    """静态目标解析（统一走 tactical_map.resolver，ADR-0029 D1）。"""
    return resolve_target(val, ctx.region_layer)


def _p_arrived(ctx: EvalCtx, slot, target, radius) -> bool:
    c = group_center(ctx, slot)
    if c is None:
        return False
    t = _resolve_target(target, ctx)
    if t is None:
        return False
    return distance(c, t) <= float(radius)


def _p_group_center(ctx: EvalCtx, slot) -> Point2 | None:
    return group_center(ctx, slot)


def _p_distance_between(ctx: EvalCtx, slot_a, slot_b) -> float:
    a = group_center(ctx, slot_a)
    b = group_center(ctx, slot_b)
    if a is None or b is None:
        return float("inf")
    return distance(a, b)


def _p_enemy_count_near(ctx: EvalCtx, anchor, radius) -> int:
    t = _resolve_target(anchor, ctx)
    if t is None:
        return 0
    enemies = [u for u in ctx.gs.units if u.owner is Owner.ENEMY]
    return len(units_within(t, enemies, float(radius)))


def _p_group_hp_ratio(ctx: EvalCtx, slot) -> float:
    units = _group_units(ctx, slot)
    if not units:
        return 0.0
    ratios = [u.hp / u.hp_max for u in units if u.hp_max > 0]
    return sum(ratios) / len(ratios) if ratios else 0.0


def _p_region_center(ctx: EvalCtx, name) -> Point2 | None:
    if ctx.region_layer is None:
        return None
    return ctx.region_layer.anchor(name)


def _p_unit_count(ctx: EvalCtx, type_name) -> int:
    want = _normalize_type(ctx.catalog, type_name)  # 双侧归一：架起态仍计为主名（T3）
    return sum(1 for u in ctx.gs.units
               if _normalize_type(ctx.catalog, u.type_name) == want and u.owner is Owner.SELF)


def _p_group_center_in_region(ctx: EvalCtx, slot, region) -> bool:
    layer = ctx.region_layer
    if layer is None:
        return False
    c = group_center(ctx, slot)
    return c is not None and layer.contains(region, c)


def _p_enemy_visible_in(ctx: EvalCtx, region) -> bool:
    layer = ctx.region_layer
    if layer is None:
        return False
    return any(u.owner is Owner.ENEMY and layer.contains(region, u.position) for u in ctx.gs.units)


def _p_has_building(ctx: EvalCtx, type_name, region=None, ready=False) -> bool:
    layer = ctx.region_layer
    want = _normalize_type(ctx.catalog, type_name)  # 双侧归一（T3）
    for u in ctx.gs.units:
        if u.owner is not Owner.SELF or _normalize_type(ctx.catalog, u.type_name) != want:
            continue
        if ready and u.build_progress < 1.0:
            continue
        if region is not None and (layer is None or not layer.contains(region, u.position)):
            continue
        return True
    return False
