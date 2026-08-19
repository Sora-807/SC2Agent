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
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2 as SC2Point2

from game import (
    ApplyResult,
    GameEvent,
    GamePort,
    Grid,
    Operation,
    Point2,
    RawGameState,
    RawOrder,
    RawUnit,
    RuntimeSink,
    SessionEvent,
)
from game.catalog import load_terran

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
    p = resolve_point(op.params["position"])
    # 注：burnysc2 build 要求 UnitTypeId 枚举（字符串会静默失败）；气矿建筑
    # （REFINERY 等）还需要传气井 Unit 而非坐标——待 ability 目录后处理。
    return [units[0].build(_resolve_type_id(op.params["type"], catalog), p)]


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
    "build": _t_build,
    "train": _t_train,
    "research": _t_research,
    "load": _t_load,
    "gather": _t_gather,
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

    async def on_step(self, iteration: int) -> None:
        raw = extract_raw_state(self, iteration)
        self._last_raw = raw
        if self._sink is not None:
            self._sink.on_game_state(raw)
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
            for cmd in translate_op(op, self._find_unit, self._catalog):
                self.do(cmd)
        except Exception:
            pass  # D6：经 ApplyResult/events 回流；V1 静默


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
    ) -> None:
        self._map_name = map_name
        self._race = race
        self._difficulty = difficulty
        self._sink = sink
        self._game_time_limit = game_time_limit
        self._realtime = realtime
        self._catalog = catalog if catalog is not None else load_terran()
        self._op_queue: list[Operation] = []
        self._bot: SC2DriverBot | None = None
        self._events: list[GameEvent] = []

    def set_sink(self, sink: RuntimeSink) -> None:
        self._sink = sink

    def start(self, request_id: str) -> None:
        bot = SC2DriverBot()
        bot._sink = self._sink
        bot._op_queue = self._op_queue
        bot._catalog = self._catalog
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
        self._op_queue.extend(ops)
        return ApplyResult(ok=True)

    def events(self, cursor: int) -> list[GameEvent]:
        return list(self._events)

    def read(self) -> RawGameState | None:
        """最近一帧 RawGameState（仅离线/单测用；未 start 时为 None）。"""
        return self._bot._last_raw if self._bot is not None else None
