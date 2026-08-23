"""SC2 driver：burnysc2 → RawGameState 抽取 + Operation 应用（GamePort 真实现）。

start() = run_game 阻塞；每 step 抽取 RawGameState 回调 sink.on_game_state，
然后 drain op_queue 把 Operation 翻译成 burnysc2 命令 self.do（下一 step 生效，spike 已验）。

本模块 import sc2（driver 是唯一允许的 SC2 适配器，R2）；game 零 sc2。
extraction 是纯函数（duck-typed burnysc2 对象），可单测（见 tests/driver/test_extraction.py）。
"""
from __future__ import annotations

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2 as SC2Point2

from game import (
    GAME_EVENTS,
    ApplyResult,
    GameEvent,
    GamePort,
    Grid,
    OpApply,
    Operation,
    Point2,
    RawGameState,
    RawOrder,
    RawUnit,
    RuntimeSink,
    SessionEvent,
)
from game.catalog import load_all

# ---------- extraction（纯函数，duck-typed burnysc2 对象）----------


def extract_raw_order(o) -> RawOrder:
    ability = getattr(o, "ability", None)
    # burnysc2 AbilityData 的干净名是 .button_name（如 "Move"/"SupplyDepot"），不是 .name
    name = getattr(ability, "button_name", None) or getattr(ability, "name", None) or str(ability)
    tpos = getattr(o, "target_world_space_pos", None)
    ttag = getattr(o, "target_unit_tag", None) or None
    otag = getattr(o, "other_unit", None) or getattr(o, "other_unit_tag", None) or None
    return RawOrder(
        ability_name=name,
        target_tag=int(ttag) if ttag else None,
        target_pos=Point2(float(tpos.x), float(tpos.y)) if tpos is not None and hasattr(tpos, "x") else None,
    )


def _unit_type_name(u) -> str:
    """burnysc2 Unit 类型名：优先 type_id 枚举名；中性单位（矿脉/装饰物）type_id 会抛
    ValueError，回退到 _proto.unit_type 原始 int + game_data 名。"""
    try:
        return u.type_id.name
    except Exception:
        pass
    raw = getattr(getattr(u, "_proto", None), "unit_type", None)
    try:
        return UnitTypeId(raw).name
    except Exception:
        pass
    try:
        return u._type_data.name
    except Exception:
        return str(raw) if raw is not None else "UNKNOWN"


def extract_raw_unit(u) -> RawUnit:
    type_name = _unit_type_name(u)
    pos = u.position
    buffs = getattr(u, "buffs", ()) or ()
    return RawUnit(
        tag=int(u.tag),
        type_name=type_name,
        position=Point2(float(pos.x), float(pos.y)),
        alliance=int(u.alliance),
        health=float(u.health),
        health_max=float(getattr(u, "health_max", u.health)),
        shield=float(u.shield),
        energy=float(getattr(u, "energy", 0.0)),
        build_progress=float(u.build_progress),
        orders=[extract_raw_order(o) for o in (getattr(u, "orders", ()) or ())],
        facing=float(getattr(u, "facing", 0.0)),
        buffs=tuple(b.name if hasattr(b, "name") else str(b) for b in buffs),
        is_carrying_minerals=bool(getattr(u, "is_carrying_minerals", False)),
        is_carrying_vespene=bool(getattr(u, "is_carrying_vespene", False)),
    )


def _grid_from_pixelmap(pm) -> Grid:
    if pm is None:
        return Grid(0, 0, [])
    w = int(getattr(pm, "width", 0))
    h = int(getattr(pm, "height", 0))
    arr = getattr(pm, "data_numpy", None)
    if arr is not None:
        try:
            data = arr.tolist()  # numpy (h,w) → list[list[int]]
        except Exception:
            data = [[0] * w for _ in range(h)]
    else:
        raw = getattr(pm, "data", None)  # 测试/duck-typed：bytes 或 list
        if raw is not None:
            try:
                data = [[int(raw[y * w + x]) for x in range(w)] for y in range(h)]
            except Exception:
                data = [[0] * w for _ in range(h)]
        else:
            data = [[0] * w for _ in range(h)]
    return Grid(w, h, data)


def extract_map_info(bot) -> Grid | None:
    """从 `game_info` 抽**静态地形**（B4：地图页从纯色底升级成真地图）。

    只做"读 SC2"——高度/可走/可建三张网格的原始形态透传，由 view 侧决定怎么画
    （driver 零业务规则，R2）。`game_info` 是 burnysc2 的 `GameInfo`，在游戏
    启动后可用；不可用时返回 None（不伪造一张全 0 网格）。

    返回 `Grid | None` 而不是三张：三张都来自同一个 game_info，拆三个返回值只会
    让调用方多做三次 None 判断。数据形状：
    `{"height": Grid|None, "pathable": Grid|None, "placeable": Grid|None}`
    """
    gi = getattr(bot, "game_info", None)
    if gi is None:
        return None
    terrain_height = getattr(gi, "terrain_height", None)
    pathing_grid = getattr(gi, "pathing_grid", None)
    placement_grid = getattr(gi, "placement_grid", None)
    if terrain_height is None and pathing_grid is None and placement_grid is None:
        return None
    # 基地（扩张）位置：burnysc2 从资源簇算好的权威位置。注意 game_info 的
    # placeable/pathable **不含资源占用**（矿脉/气井位置两格全是 1，实测），
    # "基地/气矿要预留"必须靠这份显式数据。
    expansions: list[tuple[float, float]] = []
    try:
        for p in getattr(bot, "expansion_locations_list", None) or ():
            expansions.append((float(p.x), float(p.y)))
    except Exception:              # noqa: BLE001 —— 不同版本形态不稳，缺了就空（不伪造）
        expansions = []
    return {
        "height": _grid_from_pixelmap(terrain_height) if terrain_height is not None else None,
        "pathable": _grid_from_pixelmap(pathing_grid) if pathing_grid is not None else None,
        "placeable": _grid_from_pixelmap(placement_grid) if placement_grid is not None else None,
        "expansions": expansions,
    }


def extract_raw_state(bot, seq: int) -> RawGameState:
    units = [extract_raw_unit(u) for u in getattr(bot, "all_units", ())]
    state = bot.state
    creep = _grid_from_pixelmap(getattr(state, "creep", None))
    visibility = _grid_from_pixelmap(getattr(state, "visibility", None))
    map_size = getattr(getattr(bot, "game_info", None), "map_size", (0, 0))
    return RawGameState(
        seq=seq,
        game_time=float(getattr(bot, "time", 0.0)),
        minerals=int(getattr(bot, "minerals", 0)),
        vespene=int(getattr(bot, "vespene", 0)),
        supply_used=int(getattr(bot, "supply_used", 0)),
        supply_cap=int(getattr(bot, "supply_cap", 0)),
        units=units,
        map_size=tuple(map_size),
        creep=creep,
        visibility=visibility,
    )


# ---------- Operation → burnysc2 命令 ----------


def resolve_point(val) -> SC2Point2:
    """把位置规格（our Point2 / tuple / burnysc2 Point2 / duck-typed）转成 burnysc2 Point2，供 burnysc2 命令用。"""
    if isinstance(val, SC2Point2):
        return val
    if isinstance(val, Point2):
        return SC2Point2((val.x, val.y))
    if isinstance(val, (tuple, list)) and len(val) >= 2:
        return SC2Point2((float(val[0]), float(val[1])))
    if hasattr(val, "x") and hasattr(val, "y"):
        return SC2Point2((float(val.x), float(val.y)))
    raise ValueError(f"cannot resolve point: {val!r}")


def _units(op, find_unit):
    return [u for u in (find_unit(t) for t in op.unit_tags) if u is not None]


def _t_move_to(op, find_unit, catalog=None):
    p = resolve_point(op.params["position"])
    return [u.move(p) for u in _units(op, find_unit)]


def _t_attack_move_to(op, find_unit, catalog=None):
    p = resolve_point(op.params["position"])
    return [u.attack(p) for u in _units(op, find_unit)]


def _t_hold_position(op, find_unit, catalog=None):
    return [u.hold_position() for u in _units(op, find_unit)]


def _t_stop(op, find_unit, catalog=None):
    return [u.stop() for u in _units(op, find_unit)]


def _t_follow(op, find_unit, catalog=None):
    tgt = find_unit(op.params["target_unit"]) if op.params.get("target_unit") else None
    units = _units(op, find_unit)
    return [u.move(tgt) for u in units] if tgt is not None else []


def _t_patrol(op, find_unit, catalog=None):
    pts = op.params.get("positions", [])
    if not pts:
        return []
    p = resolve_point(pts[0])
    return [u.patrol(p) for u in _units(op, find_unit)]


def _t_focus_fire(op, find_unit, catalog=None):
    tgt = find_unit(op.params["target_unit"]) if op.params.get("target_unit") else None
    units = _units(op, find_unit)
    return [u.attack(tgt) for u in units] if tgt is not None else []


def _t_siege(op, find_unit, catalog=None):
    # 架起后实体 type_id 由 SIEGETANK 变 SIEGETANKSIEGED（真机观测，见 docs/siege_probe.log）：
    # flow 计数需经 T3 形态变体归一化（catalog variants 反查）才能仍算作 SIEGETANK 组。
    # 无具名 Unit 方法 → 走 __call__(AbilityId)，与挂件 BUILD_REACTOR 同路径。
    return [u(AbilityId.SIEGEMODE_SIEGEMODE) for u in _units(op, find_unit)]


def _t_unsiege(op, find_unit, catalog=None):
    return [u(AbilityId.UNSIEGE_UNSIEGE) for u in _units(op, find_unit)]


def _t_gather(op, find_unit, catalog=None):
    """采集：assign_workers 经生产运行时 WorkerAllocator 展开后的落地原子（P0）。"""
    tgt = find_unit(op.params["target_unit"]) if op.params.get("target_unit") else None
    units = _units(op, find_unit)
    return [u.gather(tgt) for u in units] if tgt is not None else []


def _t_load(op, find_unit, catalog=None):
    """V1 近似：smart(target_unit)。

    smart() 是 burnysc2 的多态命令（运输机对被载单位执行装载），避免 driver 按单位类型
    硬编码 LOAD_MEDIVAC/LOAD_BUNKER 等能力（R2：driver 零业务规则）；精确 per-type 能力
    走 ability 稳定 ID 目录后补（见 UNIMPLEMENTED_ACTIONS 的同类条目）。
    """
    tgt = find_unit(op.params["target_unit"]) if op.params.get("target_unit") else None
    units = _units(op, find_unit)
    return [u.smart(tgt) for u in units] if tgt is not None else []


def _t_build(op, find_unit, catalog=None):
    units = _units(op, find_unit)
    if not units:
        return []
    type_id = _resolve_type_id(op.params["type"], catalog)
    # 挂件（REACTOR/TECHLAB）：game_data 里 creation_ability 为 None，build() 静默返回 False；
    # 需直接发通用 BUILD_REACTOR/BUILD_TECHLAB 能力（catalog build_ability），由母建筑自建、
    # SC2 吸附到右下 2×2。真机踩坑（trace 见 docs/full_flow.log）：per-parent 拼名
    # BUILD_REACTOR_BARRACKS 被接受、扣钱、订单常驻却永不产实体——通用能力才有实体产出。
    entry = catalog.by_burnysc2_name(type_id.name) if catalog is not None else None
    if entry is not None and "addon" in entry.capabilities:
        ability_name = entry.build_ability
        if not ability_name:
            return []  # catalog 校验已挡（addon 必须填 build_ability）；双保险 no-op
        try:
            ability = AbilityId[ability_name]
        except KeyError:
            raise ValueError(f"unknown addon build ability {ability_name}")
        return [units[0](ability)]
    pos_raw = op.params.get("position")
    p = resolve_point(pos_raw) if pos_raw is not None else None
    return [units[0].build(type_id, p)]


def _t_build_gas(op, find_unit, catalog=None):
    """气矿建筑（REFINERY 等）：burnysc2 要求 target 是气井 Unit 而非坐标。"""
    tgt = find_unit(op.params["target_unit"]) if op.params.get("target_unit") else None
    units = _units(op, find_unit)
    if not units or tgt is None:
        return []
    return [units[0].build(_resolve_type_id(op.params["type"], catalog), tgt)]


def _t_train(op, find_unit, catalog=None):
    units = _units(op, find_unit)
    return [units[0].train(_resolve_type_id(op.params["type"], catalog))] if units else []


def _t_research(op, find_unit, catalog=None):
    units = _units(op, find_unit)
    return [units[0].research(_resolve_type_id(op.params["type"], catalog))] if units else []


def _resolve_type_id(val, catalog=None):
    """build/train/research 的 type 参数 → burnysc2 枚举（UnitTypeId/UpgradeId）。

    接受三种形态：
    - 枚举实例（UnitTypeId/UpgradeId）：透传；
    - burnysc2 枚举名（如 "SCV"/"SUPPLYDEPOT"/"STIMPACK"）：直接查名（无 catalog 场景）；
    - catalog 稳定 ID（如 "terran/barracks"）：先经 catalog 映射到枚举名再查。
    解析失败抛 ValueError → 上层 _apply_op 静默跳过（D6/V1 降级路径）。
    """
    if isinstance(val, (UnitTypeId, UpgradeId)):
        return val
    if not isinstance(val, str):
        raise ValueError(f"cannot resolve type id: {val!r}")
    name = catalog.burnysc2_name_for(val) if catalog is not None else None
    name = name or val  # 无 catalog 或非稳定 ID：按 burnysc2 枚举名直接查
    for cls in (UnitTypeId, UpgradeId):
        try:
            return cls[name]
        except KeyError:
            continue
    raise ValueError(f"cannot resolve type id: {val!r}")


# action -> translator（查表；key 与 game.operation.OP_CATALOG 一致）
TRANSLATORS: dict[str, object] = {
    "move_to": _t_move_to,
    "attack_move_to": _t_attack_move_to,
    "hold_position": _t_hold_position,
    "stop": _t_stop,
    "follow": _t_follow,
    "patrol": _t_patrol,
    "focus_fire": _t_focus_fire,
    "siege": _t_siege,
    "unsiege": _t_unsiege,
    "build": _t_build,
    "train": _t_train,
    "research": _t_research,
    "load": _t_load,
    "gather": _t_gather,
    "build_gas": _t_build_gas,
}

# OP_CATALOG 中尚未翻译到 burnysc2 命令的 action → 原因。
# 测试锁死这份清单（TRANSLATORS ∪ UNIMPLEMENTED_ACTIONS == OP_CATALOG）：
# catalog 加新 action 要么进 TRANSLATORS、要么进这里，不会静默漏掉。
# V1 行为：translate_op 对它们返回 []（no-op；D6/V1 降级路径，不崩游戏）。
UNIMPLEMENTED_ACTIONS: dict[str, str] = {
    "unload": "卸载需按运输单位类型选 UNLOADALLAT_MEDIVAC/UNLOADALL_BUNKER 等能力；待 ability 稳定 ID 目录（catalog data）落地后经目录名→AbilityId 翻译",
    "use_ability": "params.ability 是稳定 ID；待 ability 目录（stable_id↔AbilityId 映射）落地",
    "cancel": "取消建造/训练需按上下文选 CANCEL_BUILDINPROGRESS/CANCEL_QUEUE*；待 ability 目录",
    "morph": "Zerg 变形（MORPH_*）；V1 Terran 场景暂不需要，待三族目录",
    "assign_workers": "复合意图：由生产运行时 production.worker.WorkerAllocator 展开成 gather/stop 级操作（矿2气3饱和）；driver 不直接执行",
}


def translate_op(op, find_unit, catalog=None) -> list:
    """查表翻译 Operation → burnysc2 UnitCommand 列表（纯函数，可单测）。

    catalog 用于 stable ID → burnysc2 枚举（build/train/research 的 type）；
    未注册 action（含 UNIMPLEMENTED_ACTIONS 的 V1 缺口）返回 []（no-op，不崩游戏）。
    """
    fn = TRANSLATORS.get(op.action)
    return fn(op, find_unit, catalog) if fn is not None else []


class SC2DriverBot(BotAI):
    """burnysc2 bot：每 step 抽取 RawGameState 推 sink；drain op_queue 翻译成命令。

    属性由 SC2GamePort.start 在构造后注入（不 override __init__，避免与 BotAI 初始化冲突）。
    """

    _sink: RuntimeSink | None = None
    _op_queue: list[Operation] | None = None
    _last_raw: RawGameState | None = None
    _catalog = None  # game.Catalog：stable ID → burnysc2 名（build/train/research 的 type 解析）
    _apply_failures: list | None = None  # V1 审计：翻译/下发失败记录（D6 正式 ApplyResult 通道前的降级）
    _apply_trace: list | None = None  # V1 调试：翻译结果追踪（runner 注入后记录每条 op 的翻译输出）
    _pending_results: dict | None = None  # D6：op_id -> OpApply，driver 逐 op 裁决后回填
    _events: list | None = None           # D7：事件收集器（SC2GamePort 注入）

    _map_info_sent = False
    _map_info_cb = None   # SC2GamePort 注入的静态地形回调（B4）

    async def on_step(self, iteration: int) -> None:
        raw = extract_raw_state(self, iteration)
        self._last_raw = raw
        if self._sink is not None:
            # 首帧先走 sink（run_session 由此发出 static/map 等静态面）——地形回调必须
            # 排在它**后面**：前端的 terrain→map 合并在 map 未到时静默丢弃（B16 教训，
            # sim 侧当年就是这个顺序坑；真机曾因此"地形不可用"，地图页一片纯色底）。
            self._sink.on_game_state(raw)
        if not self._map_info_sent and self._map_info_cb is not None:
            info = extract_map_info(self)
            if info is not None:
                self._map_info_sent = True
                self._map_info_cb(info)
        self._drain_ops()

    # ---- op 应用 ----
    def _drain_ops(self) -> None:
        if not self._op_queue:
            return
        while self._op_queue:
            self._apply_op(self._op_queue.pop(0))

    def _find_unit(self, tag: int):
        return next((u for u in self.all_units if u.tag == tag), None)

    def _apply_op(self, op: Operation) -> None:
        try:
            cmds = translate_op(op, self._find_unit, self._catalog)
            if self._apply_trace is not None:
                self._apply_trace.append((op.action, len(cmds), repr(cmds[0]) if cmds else ""))
            for cmd in cmds:
                self.do(cmd)
            self._settle(op, True, None)
        except Exception as e:  # D6：经 ApplyResult/events 回流；V1 记入审计清单，不崩游戏
            if self._apply_failures is not None:
                self._apply_failures.append((op.action, repr(e)))
            self._settle(op, False, f"{type(e).__name__}: {e}")

    def _settle(self, op: Operation, ok: bool, reason: str | None) -> None:
        """逐 op 裁决（D6）：回填 ApplyResult.results；失败发 GameEvent（D7 目录登记）。"""
        if self._pending_results is not None:
            self._pending_results[op.op_id] = OpApply(op_id=op.op_id, ok=ok, reason=reason)
        if not ok:
            self._emit_event(GameEvent(
                kind="op_apply_failed",
                payload={"op_id": op.op_id, "action": op.action, "reason": reason or ""}))

    def _emit_event(self, event: GameEvent) -> None:
        if self._events is not None:
            self._events.append(event)


class SC2GamePort:
    """GamePort 真实现：run_game 阻塞驱动 SC2。"""

    def __init__(
        self,
        map_name: str,
        race: Race,
        difficulty: Difficulty,
        sink: RuntimeSink | None = None,
        game_time_limit: int = 120,
        realtime: bool = False,
        catalog=None,  # game.Catalog：stable ID → burnysc2 名；None 时默认加载 terran catalog
        bot_cls: type = SC2DriverBot,  # 注入 bot 类（扫描/校准类 runner 用；默认 SC2DriverBot）
    ) -> None:
        self._map_name = map_name
        self._race = race
        self._difficulty = difficulty
        self._sink = sink
        self._game_time_limit = game_time_limit
        self._realtime = realtime
        self._catalog = catalog if catalog is not None else load_all()
        self._bot_cls = bot_cls
        self._op_queue: list[Operation] = []
        self._pending_results: dict[int, OpApply] = {}
        self._bot: SC2DriverBot | None = None
        self._events: list[GameEvent] = []
        #: 地形抽取回调（B4）：game_info 就绪后一次性推送。给会话子进程用 ——
        #: 静态面不该进每帧 RawGameState（地形不变，塞进逐帧是纯浪费）。
        self._map_info_sink = None

    def set_sink(self, sink: RuntimeSink) -> None:
        self._sink = sink

    def on_map_info(self, callback) -> None:
        """注册地形回调：bot 第一个 on_step 里 game_info 可用后调一次。"""
        self._map_info_sink = callback

    def start(self, request_id: str) -> None:
        bot = self._bot_cls()
        # burnysc2 现代模式：unit 方法返回 UnitCommand，由我们的 do() 统一下发。
        # 默认旧式直发路径返回 bool、do(True) 被静默忽略——挂件建造真机踩坑（trace 见 docs/full_flow.log）。
        bot.unit_command_uses_self_do = True
        bot._sink = self._sink
        bot._op_queue = self._op_queue
        bot._catalog = self._catalog
        bot._map_info_cb = self._map_info_sink
        bot._pending_results = self._pending_results
        bot._events = self._events
        self._bot = bot
        run_game(
            maps.get(self._map_name),
            [Bot(self._race, bot), Computer(Race.Random, self._difficulty)],
            realtime=self._realtime,
            game_time_limit=self._game_time_limit,
        )

    def stop(self) -> None:
        # V1：run_game 到 game_time_limit 自止；显式 kill SC2 待补
        pass

    def submit_operations(self, ops: list[Operation]) -> ApplyResult:
        """受理后**异步**应用（下一 step 生效），所以同步返回时全是 `ok=None`（待裁决）。

        裁决在 bot 的 `_apply_op` 里回填（`_pending_results`）；下一次读到
        `ApplyResult.results` / `events()` 时拿到真结果。
        """
        for op in ops:
            self._pending_results[op.op_id] = OpApply(op_id=op.op_id, ok=None)
        self._op_queue.extend(ops)
        return ApplyResult(ok=True, results=[self._pending_results[op.op_id] for op in ops])

    def events(self, cursor: int) -> list[GameEvent]:
        return list(self._events[cursor:])

    def read(self) -> RawGameState | None:
        """最近一帧 RawGameState（仅离线/单测用；未 start 时为 None）。"""
        return self._bot._last_raw if self._bot is not None else None