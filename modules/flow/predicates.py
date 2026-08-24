"""flow.predicates：结构化 AST 条件求值 + V1 谓词目录（spec-003 §4.1/§4.4 子集）。

AST node 形态（T2 起谓词用**命名参数**，运算符保留 args）：
- {"const": v} / {"param": name} / {"var": name} → 字面 / 参数 / 持久变量
- {"op": "arrived", "group": "inf", "target": {...}, "radius": 8.0} → 谓词/空间工具（命名参数）
- {"op": ">=", "args": [a, b]} / {"op": "and", "args": [...]} → 比较/逻辑（运算符是前缀序，保留 args）
命名参数的理由（D2）：位置参数不可自解释，且"顺序写错"编译期抓不到 —— 那是 LLM 高频错误。

词表（编译期校验与提示词共用的单一权威源；导出见 flow.vocab）：
- PREDICATE_SIGNATURES：已实现谓词/空间工具 → ((参数名, 是否必需), ...)
- OPERATOR_ARITY：比较/逻辑运算符 → (最少, 最多|None)
- UNIMPLEMENTED_PREDICATE_OPS / UNIMPLEMENTED_SPATIAL_OPS：spec 里有、V1 未实现（带原因，编译期+求值期都拒）
spec-003 验收 #5：原子目录外的新条件在编译期被拒绝。

求值语义：
- and/or **短路**（右支可以依赖左支已成立的前提，且省求值）
- 比较遇 None 操作数 → False（空组 group_center=None 不崩，R7）；==/!= 走正常 None 语义
  （None == None 为 True）。每次 None 比较记一条 EvalCtx.diagnostics（不静默，见"红线：不静默"）
"""
from __future__ import annotations

from dataclasses import dataclass

from game import GameState, Owner, Point2

from tactical_map.region import RegionLayer
from tactical_map.resolver import resolve_target
from tactical_map.spatial import center_of_units, distance, units_within

COMPARISON_OPS = frozenset({">=", ">", "<=", "<", "==", "!="})

# 谓词/空间工具签名表（单一权威源：validate_strategy 校验、eval_when 取参、flow.vocab 导出提示词）
PREDICATE_SIGNATURES: dict[str, tuple[tuple[str, bool], ...]] = {
    # 计数 / 时间
    "group_count": (("group", True), ("type", False)),
    "unit_count": (("type", True),),
    "game_time": (),
    "strategy_elapsed": (),
    "step_elapsed": (),
    "timer_elapsed": (("name", True),),   # T8/二十六轮落地：start_timer 起算的秒数（未跑 → None）
    # 空间 / 位置
    "arrived": (("group", True), ("target", True), ("radius", True)),
    "group_center": (("group", True),),
    "distance_between": (("a", True), ("b", True)),
    "enemy_count_near": (("anchor", True), ("radius", True)),
    "region_center": (("name", True),),
    "group_center_in_region": (("group", True), ("region", True)),
    "enemy_visible_in": (("region", True),),
    # 状态
    "group_hp_ratio": (("group", True),),
    "has_building": (("type", True), ("region", False), ("ready", False)),
    "engaged": (("group", True),),        # 二十六轮落地：组在交火（攻击命令或近敌入射程）
    "under_attack": (("group", True),),   # 二十六轮落地：组近期掉血（引擎 hp 历史推导）
    # 值工具（消费方：when 的参数位 / 动作参数位）
    "point_toward": (("origin", True), ("toward", True), ("dist", True)),
}

# 运算符 arity（最少, 最多|None=不限）：J2 —— 此前 {op: and, args: [x]} 能过、
# {op: not, args: [a, b]} 静默忽略第二个
OPERATOR_ARITY: dict[str, tuple[int, int | None]] = {
    ">=": (2, 2), ">": (2, 2), "<=": (2, 2), "<": (2, 2), "==": (2, 2), "!=": (2, 2),
    "and": (2, None), "or": (2, None), "not": (1, 1),
}

# 词表 = 签名表的键（不再单列 KNOWN_PREDICATE_OPS/LOGIC_OPS：一份表，无同步负担）

# spec-003 §4.1 中 V1 未实现的谓词 → 原因（编译期/求值期都明确拒绝，不静默）
UNIMPLEMENTED_PREDICATE_OPS: dict[str, str] = {
    "has_ready_base": "基地类型目录（三族 town hall）落 catalog 后实现",
    "event_occurred": "引擎事件流（GameEvent 目录 D7）待建",
    "user_cancel": "用户接管/取消通道待建",
}

# spec-003 §4.4 空间工具中 V1 未实现的（只读工具；消费方是 when AST）
UNIMPLEMENTED_SPATIAL_OPS: dict[str, str] = {
    "nearest_units": "需要 selector/筛选词表（spec-003 §4.3），待建",
    "cluster_centers": "需要聚类算法，待建",
}

# 值工具（返回 Point2/数值而非布尔）：动作参数位直接用，when 里作为参数嵌套
VALUE_OPS = frozenset({"group_center", "region_center", "point_toward"})

# 会把字符串当"地图名字"解析的参数位 → 期望的名字种类（编译期校验字面量名字，F5）。
# "point" 走 RegionLayer.anchor（区域锚点/大区锚点/点位标记都算）；
# "region" 必须是区域名（regions/big_regions）—— 点位名当区域名用会静默恒 False。
SPATIAL_NAME_PARAMS: dict[str, dict[str, str]] = {
    "arrived": {"target": "point"},
    "enemy_count_near": {"anchor": "point"},
    "point_toward": {"origin": "point", "toward": "point"},
    "region_center": {"name": "region"},
    "group_center_in_region": {"region": "region"},
    "enemy_visible_in": {"region": "region"},
}


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
    catalog: object = None  # game.Catalog（stable id ↔ 实体名翻译；T1 起由 FlowEngine 必传）
    definitions: dict | None = None  # {ref: name} 别名节（T2b；纯替换语义，无变量捕获）
    step_id: str = ""  # 当前 step（只用于诊断定位）
    diagnostics: dict | None = None  # (step, kind, detail) -> 次数；None = 不记录（单测直调）
    # ---- 二十六轮（T8 落地）：locals / timers / 交火态由 FlowEngine 注入 ----
    #: step 局部变量（set_local 写、{local: name} 读、换 step 清空）。None = 引擎外直调。
    locals: dict | None = None
    #: 定时器读接口：name -> 已耗时秒数 | None（未 start / 已 stop）。callable，由引擎闭包提供。
    timers: object = None
    #: 交火态读接口：engaged(slot) / under_attack(slot) -> bool（引擎从 orders/射程/hp 历史推导）。
    combat: object = None


def note_diagnostic(ctx: EvalCtx, kind: str, detail: str) -> None:
    """记一条求值期诊断（H6：降级为 False 的路径不能静默）。同 key 只累计次数，不刷屏。"""
    if ctx.diagnostics is None:
        return
    key = (ctx.step_id, kind, detail)
    ctx.diagnostics[key] = ctx.diagnostics.get(key, 0) + 1


def unit_is_type(catalog, unit_type_name: str, stable_id: str) -> bool:
    """gs 单位（burnysc2 实体名，可能是形态变体）是否属于 authoring 的 stable id 类型（T1/D1）。

    authoring 侧只有 stable id（terran/siegetank）；gs 侧是 burnysc2 名且可能是变体
    （SIEGETANKSIEGED）。方向固定为"stable id → 主名，单位名归一到主名后比较"，
    单侧归一，不再有"把 stable id 当 burnysc2 名再归一"的兼容路径。
    未知 stable id → False（构造期 create_group/validate_assembly 已拒，此为兜底）。
    """
    want = catalog.burnysc2_name_for(stable_id)
    if want is None:
        return False
    return catalog.normalize_burnysc2_name(unit_type_name) == want


def eval_when(node, ctx: EvalCtx):
    """条件 / 值 AST 求值（只读、确定性；绑定当前 GameState seq，spec-003 §4.1）。

    when 与动作参数走**同一个**求值器：同一份词表、同一份 None 语义、同一套诊断。
    谓词/空间工具取命名参数；比较/逻辑取 args；and/or 短路；{ref} 纯替换展开。
    """
    if not isinstance(node, dict):
        return node  # 字面量（数字/字符串/坐标列表）
    if "const" in node:
        return node["const"]
    if "param" in node:
        return ctx.params.get(node["param"])
    if "var" in node:
        return ctx.variables.get(node["var"])
    if "local" in node:
        # step 局部变量（二十六轮 T8 落地）：set_local 写、换 step 清空。
        # 未声明/未写 → None（比较路径有 None 诊断兜底，不静默也不崩）。
        name = node["local"]
        if ctx.locals is None:
            note_diagnostic(ctx, "no_locals_ctx", f"{{local: {name!r}}}：求值上下文没有 locals（引擎外直调？）")
            return None
        if name not in ctx.locals:
            note_diagnostic(ctx, "unset_local", f"{name!r} 未写入（进入本 step 后还没有 set_local）")
        return ctx.locals.get(name)
    if "ref" in node:
        name = node["ref"]
        defs = ctx.definitions or {}
        if name not in defs:
            note_diagnostic(ctx, "unknown_ref", f"{name!r} 不在 definitions（编译期应已拦）")
            return None
        return eval_when(defs[name], ctx)  # 纯替换语义；自引用/环由编译期拒绝
    op = node.get("op")
    if op in UNIMPLEMENTED_PREDICATE_OPS:
        raise ValueError(f"predicate {op!r} 未实现：{UNIMPLEMENTED_PREDICATE_OPS[op]}")
    if op in UNIMPLEMENTED_SPATIAL_OPS:
        raise ValueError(f"spatial tool {op!r} 未实现：{UNIMPLEMENTED_SPATIAL_OPS[op]}")
    # 逻辑：短路（右支可依赖左支前提，且省求值）
    if op == "and":
        for a in node.get("args", []):
            if not eval_when(a, ctx):
                return False
        return True
    if op == "or":
        for a in node.get("args", []):
            if eval_when(a, ctx):
                return True
        return False
    if op == "not":
        args = node.get("args") or [None]
        return not eval_when(args[0], ctx)
    if op in COMPARISON_OPS:
        args = node.get("args") or []
        a = eval_when(args[0], ctx) if len(args) > 0 else None
        b = eval_when(args[1], ctx) if len(args) > 1 else None
        return _compare(op, a, b, ctx)
    if op in PREDICATE_SIGNATURES:
        kw = {name: eval_when(node[name], ctx)
              for name, _required in PREDICATE_SIGNATURES[op] if name in node}
        return _call_predicate(op, ctx, kw)
    raise ValueError(f"unknown op {op!r}")


def _compare(op: str, a, b, ctx: EvalCtx):
    """比较求值（D9/H6）：==/!= 走正常 None 语义（None == None 为 True）；
    其余运算符遇 None 操作数 → False + 记诊断（空组 group_center=None 不崩，也不静默）。

    注意：这使 `x >= y` 与 `x < y` 可以同时为 False（失败安全），且 `not (a > b)` 在 None 时为 True ——
    所以取 False 的每一次都留痕（EvalCtx.diagnostics），便于 trace/UI 发现"条件其实没求出来"。
    """
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if a is None or b is None:
        note_diagnostic(ctx, "compare_none", f"{op} 操作数为 None（{a!r} {op} {b!r}）→ False")
        return False
    if op == ">=":
        return a >= b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    return a < b  # "<"


def _call_predicate(op: str, ctx: EvalCtx, kw: dict):
    """按签名表取到的命名参数分派到实现（必需参数编译期已校验，此处直取）。"""
    if op == "group_count":
        return _group_count(ctx, kw["group"], kw.get("type"))
    if op == "unit_count":
        return _p_unit_count(ctx, kw["type"])
    if op == "game_time":
        return ctx.gs.game_time
    if op == "strategy_elapsed":
        return ctx.gs.game_time - ctx.strategy_start
    if op == "step_elapsed":
        return ctx.gs.game_time - ctx.step_entered
    if op == "timer_elapsed":
        return _p_timer_elapsed(ctx, kw["name"])
    if op == "arrived":
        return _p_arrived(ctx, kw["group"], kw["target"], kw["radius"])
    if op == "group_center":
        return group_center(ctx, kw["group"])
    if op == "distance_between":
        return _p_distance_between(ctx, kw["a"], kw["b"])
    if op == "enemy_count_near":
        return _p_enemy_count_near(ctx, kw["anchor"], kw["radius"])
    if op == "group_hp_ratio":
        return _p_group_hp_ratio(ctx, kw["group"])
    if op == "engaged":
        return _p_combat(ctx, "engaged", kw["group"])
    if op == "under_attack":
        return _p_combat(ctx, "under_attack", kw["group"])
    if op == "region_center":
        return _p_region_center(ctx, kw["name"])
    if op == "group_center_in_region":
        return _p_group_center_in_region(ctx, kw["group"], kw["region"])
    if op == "enemy_visible_in":
        return _p_enemy_visible_in(ctx, kw["region"])
    if op == "has_building":
        return _p_has_building(ctx, kw["type"], kw.get("region"), kw.get("ready", False))
    if op == "point_toward":
        return point_toward(kw["origin"], kw["toward"], kw["dist"], ctx.region_layer)
    raise ValueError(f"unknown op {op!r}")


def _p_timer_elapsed(ctx: EvalCtx, name):
    """定时器已耗时（二十六轮 T8 落地）：start_timer 起算、stop_timer 冻结。

    引擎外直调（timers 未注入）或未 start → None：比较路径走 None 诊断兜底
    （`timer_elapsed(t) >= 30` 在没起表时是 False 且留痕，不静默也不崩）。
    """
    if ctx.timers is None:
        note_diagnostic(ctx, "no_timer_ctx", f"timer_elapsed({name!r})：求值上下文没有计时器（引擎外直调？）")
        return None
    return ctx.timers(name)


def _p_combat(ctx: EvalCtx, which: str, slot):
    """engaged / under_attack（二十六轮落地）：交火态由引擎从 orders/射程/hp 历史推导。

    引擎外直调（combat 未注入）→ False + 诊断（与 None 比较同款的不静默降级）。
    """
    if ctx.combat is None:
        note_diagnostic(ctx, "no_combat_ctx", f"{which}({slot!r})：求值上下文没有交火态（引擎外直调？）")
        return False
    return bool(getattr(ctx.combat, which)(slot))


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


def point_toward(origin_val, toward_val, dist, layer=None) -> Point2 | None:
    """从 origin 朝 toward 方向延伸 dist 距离的点（T4）。

    参数名用 origin 而非 from（J3：from 是 Python 关键字，会绊到 TypedDict 生成/表达式语法/提示词示例）。
    origin/toward 接受 Point2/(x,y)序列/点位名（经 resolve_target 解析）；dist 为数值。
    origin==toward 或 dist<=0 → 返回 origin；任一端为 None（含未知名）→ None（arrived 判 false，不崩）。
    layer=None 时点位名解析返回 None；eval_when 从 ctx.region_layer 注入。
    """
    f = resolve_target(origin_val, layer)
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
    return sum(1 for u in ctx.gs.units
               if u.owner is Owner.SELF and unit_is_type(ctx.catalog, u.type_name, type_name))


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
    for u in ctx.gs.units:
        if u.owner is not Owner.SELF or not unit_is_type(ctx.catalog, u.type_name, type_name):
            continue
        if ready and u.build_progress < 1.0:
            continue
        if region is not None and (layer is None or not layer.contains(region, u.position)):
            continue
        return True
    return False
