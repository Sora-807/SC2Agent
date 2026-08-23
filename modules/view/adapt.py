"""view.adapt：动态面 —— GameState / ProjectionCurve → 帧。

本模块承担契约的"派生量后端算"那一半（红线 C2/C3/C5）：
stable_id 翻译与形态归一、footprint 矩形、在训单位、挂件判定、资源饱和度、订单原子名映射。
前端因此只做"帧 → 像素"，不复算任何规则。

world / projection / session 是纯函数（B0）；flow / production / ops 读引擎的 `snapshot()`（B1）——
一律经显式读模型，**绝不伸手读 `_` 私有字段**（否则引擎任何重构都会打断 UI）。
"""
from __future__ import annotations

from game.catalog import Catalog
from game.operation import OP_CATALOG
from game.state import GameState, Owner, Unit

from tactical_map.placement import BuildSlot
from tactical_map.spatial import center_of_units

from view.encode import grid_to_b64
from view.schema import (
    ApplyView,
    BlockedView,
    BranchHitView,
    CompositionView,
    DroppedView,
    EconomyFrame,
    EconomyNodeView,
    EconomyReservationView,
    EconomyTaskView,
    EconomyView,
    EvalDiagnosticView,
    FlowFrame,
    GroupView,
    InFlightView,
    LandingView,
    OpsFrame,
    OpView,
    ProductionFrame,
    QueueItemView,
    QueueView,
    StrategyView,
    TransitionView,
    FootprintView,
    GridsView,
    OrderView,
    ProducingView,
    ProjectionEventView,
    ProjectionFrame,
    ProjectionPointView,
    ResourceStateView,
    SessionFrame,
    UnitView,
    WorldFrame,
)

#: SC2 原生能力名 → 我们的动作原子名。只收**能确定**的映射；
#: 映射不出就发 None + 原始名（不静默猜，红线"不静默"）。
_ABILITY_TO_ATOM: dict[str, str] = {
    "MOVE": "move_to", "MOVE_MOVE": "move_to", "MOVE_MOVEPATROL": "patrol",
    "ATTACK": "attack_move_to", "ATTACK_ATTACK": "attack_move_to",
    "HOLDPOSITION": "hold_position", "HOLDPOSITION_HOLD": "hold_position",
    "STOP": "stop", "STOP_STOP": "stop",
    "HARVEST_GATHER": "gather", "HARVEST_RETURN": "gather",
    "SIEGEMODE_SIEGEMODE": "siege", "UNSIEGE_UNSIEGE": "unsiege",
    "SMART": "move_to",
}

#: 挂件实体名片段 → 契约里的 addon 取值
_ADDON_KINDS = (("REACTOR", "reactor"), ("TECHLAB", "techlab"))

#: 采矿/采气的判定半径（与 production.worker.NODE_RADIUS 同源语义：谁在这个矿区里干活）
_WORKER_NEAR = 8.0


def session_frame(
    state: str,
    *,
    frame_source: str,
    game_time: float,
    map_name: str | None = None,
    my_race: str | None = None,
    enemy_race: str | None = None,
    error: str | None = None,
) -> SessionFrame:
    return SessionFrame(
        state=state, frame_source=frame_source, map_name=map_name,
        my_race=my_race, enemy_race=enemy_race, game_time=round(float(game_time), 3), error=error,
    )


def grids_of(gs: GameState) -> GridsView:
    """GameState 的菌毯/可见性栅格 → GridsView。

    契约里 `grids` 是「仅变化时下发」——**是否**随本帧下发由 caller（FrameProducer）
    按内容指纹决定，这里只负责编码。
    """
    return GridsView(creep=grid_to_b64(gs.creep), visibility=grid_to_b64(gs.visibility))


def world_frame(
    gs: GameState,
    catalog: Catalog,
    *,
    group_of: dict[int, str] | None = None,
    grids: GridsView | None = None,
) -> WorldFrame:
    """GameState → frame/world。

    `group_of`：unit_tag → group_id（由 B1 从 Allocator 读模型 join 进来）。
    B0 传 None，前端的分组图层就为空 —— 不猜，不用"离得近就算一组"糊过去。
    `grids`：调用方决定要不要带栅格（`grids_of(gs)`；None = 本帧不带，前端保留上一份）。
    """
    groups = group_of or {}
    units = [_unit_view(u, catalog, groups.get(u.tag)) for u in gs.units]
    return WorldFrame(
        economy=EconomyView(
            minerals=gs.minerals, vespene=gs.vespene,
            supply_used=gs.supply_used, supply_cap=gs.supply_cap,
        ),
        units=units,
        # 聚类算法未实现（flow.vocab 的 forbidden.spatial_tools 里有登记）→ None，前端降级
        enemy_clusters=None,
        resource_state=_resource_state(gs),
        grids=grids,
    )


def projection_frame(
    curve,
    *,
    based_on_seq: int,
    based_on_game_time: float,
    horizon: float,
    queue_name: str | None = None,
    plan_id: str | None = None,
    skipped: list[tuple[str, str]] | None = None,
) -> ProjectionFrame:
    """planner.ProjectionCurve → frame/projection（纯重排，不算新数值）。"""
    if (queue_name is None) == (plan_id is None):
        raise ValueError("projection_frame 需要且只需要 queue_name 或 plan_id 之一")
    source = (
        {"kind": "live_queue", "queue_name": queue_name}
        if queue_name is not None
        else {"kind": "draft", "plan_id": plan_id}
    )
    return ProjectionFrame(
        based_on_seq=int(based_on_seq),
        based_on_game_time=round(float(based_on_game_time), 3),
        horizon=float(horizon),
        source=source,
        points=[
            ProjectionPointView(
                t=p.t, minerals=p.minerals, gas=p.gas,
                supply_used=p.supply_used, supply_cap=p.supply_cap,
                mineral_workers=p.mineral_workers, gas_workers=p.gas_workers,
                buildings=dict(p.buildings), units=dict(p.units),
                in_flight_count=p.in_flight_count,
            )
            for p in curve.points
        ],
        events=[
            ProjectionEventView(kind=e.kind, stable_id=e.type, t=e.t, reason=e.reason)
            for e in curve.events
        ],
        skipped=[{"op": op, "reason": reason} for op, reason in (skipped or [])],
    )


# ---------------- 内部 ----------------

def _unit_view(u: Unit, catalog: Catalog, group_id: str | None) -> UnitView:
    raw = u.type_name.upper()
    main = catalog.normalize_burnysc2_name(raw)          # 形态变体归一到主名（SIEGETANKSIEGED→SIEGETANK）
    entry = catalog.by_burnysc2_name(main)
    stable_id = entry.stable_id if entry else _unknown_stable_id(raw)
    form = _form_of(raw, main)
    return UnitView(
        tag=u.tag,
        stable_id=stable_id,
        form=form,
        owner=u.owner.value if isinstance(u.owner, Owner) else str(u.owner),
        pos=(u.position.x, u.position.y),
        facing=u.facing,
        hp=u.hp, hp_max=u.hp_max, shield=u.shield, energy=u.energy,
        build_progress=u.build_progress,
        group_id=group_id,
        order=_order_view(u),
        footprint=_footprint(u, entry),
        producing=_producing(u, catalog),
        addon=_addon(raw),
        carrying="minerals" if u.is_carrying_minerals else ("vespene" if u.is_carrying_vespene else None),
        buffs=list(u.buffs),
    )


def _unknown_stable_id(raw: str) -> str:
    """catalog 里没登记的类型。

    可识别的中立类型（矿脉/气井/瞭望塔/可破坏障碍物）归一到已知 stable_id，
    前端从 catalog.entries 查到中文名而非显示"未知"。
    真正无法识别的类型才打 `unknown/` 前缀（红线 C1：动态帧不出现 burnysc2 名）。
    """
    name = raw.lower()
    # 中立资源/建筑（按名称模式归一，覆盖 catalog 变体表没穷举的子类型）
    if "mineral" in name:
        return "neutral/mineralfield"
    if "geyser" in name:
        return "neutral/vespenegeyser"
    if "xelnagatower" in name:
        return "neutral/xelnagatower"
    # 可破坏障碍物（岩石/残骸/斜坡/墙/门/冰/可崩塌塔子类型 → 通用"障碍物"）
    if any(kw in name for kw in (
        "destructible", "debris", "ramp", "collapsible",
        "blocker", "barrier", "unbuildable", "sandbag", "rockcover",
    )):
        return "neutral/destructible"
    return "unknown/" + name


def _form_of(raw: str, main: str) -> str | None:
    if raw == main:
        return None
    # SIEGETANKSIEGED → "sieged"：变体名去掉主名前缀，剩下的就是形态
    return raw[len(main):].lower() or "variant"


def _order_view(u: Unit) -> OrderView | None:
    if not u.orders:
        return None
    o = u.orders[0]
    ability = (o.ability or "").upper()
    atom = _ABILITY_TO_ATOM.get(ability)
    if atom is None and ability in OP_CATALOG:
        atom = ability.lower()
    return OrderView(
        atom=atom,
        ability_raw=o.ability,
        target_tag=o.target_tag,
        target_pos=(o.target_pos.x, o.target_pos.y) if o.target_pos else None,
        is_auto=o.is_auto,
    )


def _footprint(u: Unit, entry) -> FootprintView | None:
    """从 SC2 **报告位置**反解 footprint 矩形。

    一定要走 `BuildSlot.tl_from_reported`（floor(R - size/2)），**不能**自己写公式：
    ADR-0027 里有两个不同的反解 —— 命令点用 `ceil`（tl_from_pos）、报告位用 `floor`
    （tl_from_reported）。`GameState.position` 是报告位，抄错那个在负边角会偏一格。
    红线 C2 说的"不许有第二份换算"，对后端自己也成立。
    """
    if entry is None or entry.size is None:
        return None
    size = entry.size
    tl = BuildSlot.tl_from_reported(u.position, size)
    return FootprintView(tl=(tl.x, tl.y), br=(tl.x + size - 1, tl.y + size - 1))


def _producing(u: Unit, catalog: Catalog) -> list[ProducingView] | None:
    """生产建筑正在训练什么（订单能力名 = 单位自带类型名，如 "Marine"）。

    只对**已建成的建筑**给列表（在建建筑的订单是它自己的建造过程）。
    认不出的订单不猜：跳过，宁可少显示。
    `progress` 一律 None：SC2 订单不带进度（协议就没这个字段，真机拿不到）；
    sim 侧被训单位自身的 `build_progress` 已承载真值。发假 0.0 会让 UI/agent
    把"未知"读成"刚开始"。
    """
    if u.build_progress < 1.0:
        return None
    entry = catalog.by_burnysc2_name(catalog.normalize_burnysc2_name(u.type_name.upper()))
    if entry is None or entry.size is None:
        return None
    out: list[ProducingView] = []
    for o in u.orders:
        target = catalog.by_burnysc2_name((o.ability or "").upper())
        if target is None:
            continue
        out.append(ProducingView(stable_id=target.stable_id, progress=None))
    return out


def _addon(raw: str) -> str | None:
    for frag, kind in _ADDON_KINDS:
        if frag in raw:
            return kind
    return None


def _resource_state(gs: GameState) -> list[ResourceStateView]:
    """资源点的动态量：剩余（SC2 未给则 None）+ 附近在采集的工兵数。

    人数由 orders 的 target_tag 判定（谁在采这个点），target_tag 缺失时退化为半径判定。
    """
    harvesting: dict[int, int] = {}
    near: list[Unit] = []
    for u in gs.units:
        if u.owner is not Owner.SELF or not u.orders:
            continue
        o = u.orders[0]
        atom = _ABILITY_TO_ATOM.get((o.ability or "").upper())
        if atom != "gather":
            continue
        if o.target_tag is not None:
            harvesting[o.target_tag] = harvesting.get(o.target_tag, 0) + 1
        else:
            near.append(u)

    out: list[ResourceStateView] = []
    for node in gs.resources:
        n = harvesting.get(node.tag, 0)
        if n == 0 and near:
            n = sum(
                1 for u in near
                if (u.position.x - node.position.x) ** 2 + (u.position.y - node.position.y) ** 2
                <= _WORKER_NEAR ** 2
            )
        out.append(ResourceStateView(tag=node.tag, remaining=None, workers=n))
    return out

# ---------------- flow / production / ops（B1）----------------

def flow_frame(
    snap: dict,
    gs: GameState | None = None,
    *,
    catalog: Catalog | None = None,
) -> FlowFrame:
    """`FlowEngine.snapshot()` → frame/flow。

    `gs` 给了才能算组心与 hp 比（要真实单位位置）；不给就是 None ——
    **不猜**（红线：帧里没有的东西前端也画不出来，所以后端宁可给 None）。
    组心用 `tactical_map.spatial.center_of_units`，不在这里重写几何。
    """
    by_tag = {u.tag: u for u in gs.units} if gs is not None else {}
    groups: list[GroupView] = []
    for g in snap["groups"]:
        tags = list(g["leased_tags"])
        units = [by_tag[t] for t in tags if t in by_tag]
        center = center_of_units(units) if units else None
        hp = _hp_ratio(units)
        groups.append(
            GroupView(
                group_id=g["group_id"],
                composition={
                    k: CompositionView(min=c["min"], target=c["target"],
                                       max=c["max"], current=c["current"])
                    for k, c in g["composition"].items()
                },
                refill_state=g["refill_state"],
                leased_tags=tags,
                center=None if center is None else (center.x, center.y),
                hp_ratio=hp,
            )
        )

    strategy = StrategyView(
        instance_id=snap["instance_id"],
        strategy_ref=snap["strategy_ref"],
        version=snap["version"],
        params=_jsonable(snap["params"]),
        variables=_jsonable(snap["variables"]),
        locals=_jsonable(snap["locals"]),
        definitions=snap["definitions"],
        active_step=snap["active_step"],
        step_entered_at=snap["step_entered_at"] or 0.0,
        step_elapsed=snap["step_elapsed"] or 0.0,
        step_entry_count=snap["step_entry_count"],
        branch_hit=None if snap["branch_hit"] is None else BranchHitView(
            step_id=snap["branch_hit"]["step_id"],
            branch_id=snap["branch_hit"]["branch_id"],
            index=snap["branch_hit"]["index"],
        ),
        transitions=[
            TransitionView(from_step=t["from_step"], to=t["to"], kind=t["kind"],
                           reason=t["reason"], at=t["at"])
            for t in snap["transitions"]
        ],
        transition_count=snap["transition_count"],
        transition_limit=snap["transition_limit"],
        done=snap["done"],
        exit_record=snap["exit_record"],
        bindings=snap["bindings"],
        eval_diagnostics=[
            EvalDiagnosticView(step_id=d["step_id"], kind=d["kind"],
                               detail=d["detail"], count=d["count"])
            for d in snap["eval_diagnostics"]
        ],
    )
    # 列表形状：V1 编译期只允许一个实例，但协议保持列表，多实例长出来时不改契约（红线 C6）
    return FlowFrame(strategies=[strategy], groups=groups)


def group_of_from_flow(snap: dict) -> dict[int, str]:
    """`FlowEngine.snapshot()` → `{unit_tag: group_id}`，喂给 `world_frame(group_of=...)`。

    分组归属只能这样 join —— world_frame 自己绝不"离得近就算一组"。
    """
    out: dict[int, str] = {}
    for g in snap["groups"]:
        for tag in g["leased_tags"]:
            out[tag] = g["group_id"]
    return out


def production_frame(snap: dict, catalog: Catalog) -> ProductionFrame:
    """`ProductionRuntime.snapshot()` → frame/production。

    `kind` 按 catalog 的 capabilities 判定（addon/gas/build），不看名字猜。
    """
    queues = [
        QueueView(
            name=q["name"],
            head_status=q["head_status"],
            blocked=None if q["blocked"] is None else BlockedView(
                reason=q["blocked"]["reason"],
                since=q["blocked"]["since"],
                waited=0.0,  # 由 caller 用当前 game_time 覆写，见 with_waited()
                warned=q["blocked"]["warned"],
            ),
            items=[
                QueueItemView(
                    index=it["index"], op=it["op"], stable_id=it["stable_id"],
                    count=it["count"], placement=it["placement"], task=it["task"],
                    status=it["status"], block_reason=it["block_reason"],
                )
                for it in q["items"]
            ],
        )
        for q in snap["queues"]
    ]
    in_flight = [
        InFlightView(
            queue=f["queue"],
            stable_id=f["stable_id"],
            kind=_flight_kind(f["stable_id"], catalog),
            builder_tag=f["builder_tag"],
            expect_pos=None if f["expect_pos"] is None
            else (f["expect_pos"].x, f["expect_pos"].y),
            radius=float(f["radius"]),
            frames_waited=int(f["frames_waited"]),
            retries=int(f["retries"]),
            attempted_slots=list(f["attempted_slots"]),
        )
        for f in snap["in_flight"]
    ]
    dropped = [
        DroppedView(at=d["at"], op=d["op"], stable_id=d["stable_id"], reason=d["reason"])
        for d in snap["dropped"]
    ]
    return ProductionFrame(queues=queues, in_flight=in_flight, dropped=dropped)


def with_waited(frame: ProductionFrame, game_time: float) -> ProductionFrame:
    """把队首阻塞的 `waited` 按当前游戏时间算出来（`since` 在快照里，现在时刻只有 caller 知道）。"""
    for q in frame.queues:
        if q.blocked is not None:
            q.blocked.waited = round(game_time - q.blocked.since, 3)
    return frame


def ops_frame(ring) -> OpsFrame:
    """`view.port.OpRing` → frame/ops。apply/landing 在 B9（D6/D7）之前多为 None。"""
    return OpsFrame(
        ops=[
            OpView(
                op_id=r["op_id"], seq=r["seq"], at=r["at"] or 0.0,
                action=r["action"], unit_tags=list(r["unit_tags"]),
                params=_jsonable(r["params"]), origin=r["origin"],
                apply=None if r["apply"] is None else ApplyView(**r["apply"]),
                landing=None if r["landing"] is None else LandingView(**r["landing"]),
            )
            for r in ring.all()
        ]
    )


def _flight_kind(stable_id: str, catalog: Catalog) -> str:
    entry = catalog.by_stable_id(stable_id)
    caps = set(entry.capabilities) if entry else set()
    if "addon" in caps:
        return "addon"
    if "gas" in caps:
        return "gas"
    return "build"


def _hp_ratio(units: list[Unit]) -> float | None:
    total_max = sum(u.hp_max for u in units)
    if total_max <= 0:
        return None
    return round(sum(u.hp for u in units) / total_max, 4)


def _jsonable(d: dict) -> dict:
    """params/variables 里可能有 Point2 之类 —— 转成 JSON 可编码形态（不静默丢字段）。"""
    out: dict = {}
    for k, v in d.items():
        if hasattr(v, "x") and hasattr(v, "y"):
            out[k] = [v.x, v.y]
        elif isinstance(v, (list, tuple)):
            out[k] = [[i.x, i.y] if hasattr(i, "x") and hasattr(i, "y") else i for i in v]
        else:
            out[k] = v
    return out

def economy_frame(snap: dict) -> EconomyFrame:
    """`EconomyKeeper.snapshot()` → frame/economy（ADR-0030 D4.6）。

    这里只做**join**，不复算维持器的任何规则：
    - 每个节点的 `workers` = `assigned`（tag → 节点 tag）里指向它的人数；
    - 每个 task 的 `actual` = 按节点的 gas 标志把 `assigned` 分类计数；
    - `target` 与 `capacity` 一律取维持器给的值（矿 2 / 气 3 是它的真机常数，前端不硬编码）。

    `source` 的判定：policy 里给了绝对配额（`mineral_workers`/`gas_workers` 非 None）就是
    explicit，否则是按 per-node 比例推出来的 default —— 这个区别对用户很重要，
    因为 explicit 会**压过**比例配额。
    """
    policy = snap["policy"]
    nodes_raw = snap["nodes"]
    assigned: dict[int, int | None] = {int(k): v for k, v in snap["assigned"].items()}

    gas_tags = {n["tag"] for n in nodes_raw if n["gas"]}
    mineral_tags = {n["tag"] for n in nodes_raw if not n["gas"]}
    per_node: dict[int, int] = {}
    for node_tag in assigned.values():
        if node_tag is None:
            continue
        per_node[node_tag] = per_node.get(node_tag, 0) + 1

    actual_gas = sum(1 for v in assigned.values() if v in gas_tags)
    actual_mineral = sum(1 for v in assigned.values() if v in mineral_tags)
    actual_idle = sum(1 for v in assigned.values() if v is None)

    # quota = 持久配额（None = 按比例）；target = 本帧可达（被容量/人数夹紧）。
    # 两个都要给：没精炼厂时 gas 的 quota=6 而 target=0，只给后者会让人以为意图蒸发了（P9）。
    tasks = [
        EconomyTaskView(
            task="mineral", quota=policy.get("mineral_workers"),
            target=int(snap["targets"]["mineral"]), actual=actual_mineral,
        ),
        EconomyTaskView(
            task="gas", quota=policy.get("gas_workers"),
            target=int(snap["targets"]["gas"]), actual=actual_gas,
        ),
        EconomyTaskView(
            task="idle", quota=policy.get("reserve_idle"),
            target=int(policy.get("reserve_idle") or 0), actual=actual_idle,
        ),
    ]
    nodes = [
        EconomyNodeView(
            tag=n["tag"],
            kind="gas" if n["gas"] else "mineral",
            workers=per_node.get(n["tag"], 0),
            capacity=int(n["cap"]),
            saturated=per_node.get(n["tag"], 0) >= int(n["cap"]),
            base_tag=n.get("base"),
        )
        for n in nodes_raw
    ]
    # 注意方向：`WorkerReservations.snapshot()` 是 `owner -> tag`（一个 owner 一个工兵），
    # 不是 `tag -> owner`。帧里按 tag 排序输出，UI 才能和单位列表对齐。
    reserved = sorted(
        (EconomyReservationView(tag=int(tag), owner=str(owner))
         for owner, tag in snap["reserved"].items()),
        key=lambda r: r.tag,
    )
    return EconomyFrame(
        tasks=tasks,
        nodes=nodes,
        reserved=reserved,
        emitted_count=int(snap["last_ops"]),
        domain_workers=len(assigned),
        quotas={
            "mineral_per_patch": int(policy["mineral_per_patch"]),
            "gas_per_refinery": int(policy["gas_per_refinery"]),
            "reserve_idle": int(policy.get("reserve_idle") or 0),
        },
    )
