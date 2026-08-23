"""步坦协同真机全链验证（T7，需 SC2）：20 步兵 + 4 坦克循环蛙跳攻到敌方主矿。

双运行时联动（同 port）：production（V3 build order + 枪兵/坦克维持）与 flow（T5 循环蛙跳 +
catalog=load_all 归一化）每帧并行消费 GameState。

生产半边：macro V3 = 农民优先(间插×11) → depot×4 → 兵营×2+反应堆×2（双倍机枪 = 4 训练槽）
→ 气矿×2(气工紧跟) → 工厂+科技实验室 → 工程站+军械库 → 机枪×20 → 坦克×4；
维持：步兵<20 + 坦克<4 补训（只在 formup 内，避免新兵拖组心；run_full_flow 真机教训）。
flow 半边：tank_marine_push.yaml 循环策略（tank_hop→siege_gate→inf_hop→threat_gate→循环）+
arrived(inf,target,8)→exit；catalog 归一让架起坦克（SIEGETANKSIEGED）仍算 SIEGETANK 组。

验收（§0）：engine_done=True、dropped=0；证据日志：组心距敌矿递减、坦克 SIEGEMODE 订单、
停等 step、SIEGETANKSIEGED 实体计数。建筑位置 [bldg_pos] 每 100 帧快照对照 base_layout。

跑：uv run python run_tank_marine_push.py
输出：docs/full_flow_tank.log + 控制台。
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from loguru import logger

from game import QueueItem, Owner, Point2
from game.catalog import load_all
from game.production import PlacementExact, PlacementInRegion
from driver.sc2_adapter import SC2GamePort
from flow.allocator import Allocator
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from production.economy import EconomyKeeper, WorkerReservations
from production.runtime import ProductionRuntime
from sc2.data import Difficulty, Race
from tactical_map import instantiate_spawn, load_ladder_map, spawn_layout_nearest
from world.adapter import adapt

LOG = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "full_flow_tank.log"
_DOC = Path(__file__).resolve().parents[2] / "docs" / "data" / "tank_marine_push.yaml"
_DATA = yaml.safe_load(_DOC.read_text(encoding="utf-8"))


def log(msg: object) -> None:
    s = str(msg)
    logger.info(s)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(s + "\n")


def _strategy_yaml() -> str:
    return yaml.safe_dump(_DATA["strategy"], sort_keys=False, allow_unicode=True)


def _assembly_yaml(target: Point2, garrison: Point2) -> str:
    d = yaml.safe_load(_DOC.read_text(encoding="utf-8"))["assembly"]  # 每次 fresh，避免残留
    d["strategy_instances"][0]["params"]["target"] = [target.x, target.y]
    d["strategy_instances"][0]["params"]["garrison_pos"] = [garrison.x, garrison.y]
    return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


class TankFullFlowSink:
    def __init__(self, port: SC2GamePort) -> None:
        self._port = port
        self._runtime: ProductionRuntime | None = None
        self._engine: FlowEngine | None = None
        self._enemy: Point2 | None = None
        self._cc: Point2 | None = None
        self._maintain_seq = 0
        self._bot = None

    def on_game_state(self, raw) -> None:
        gs = adapt(raw)
        if self._runtime is None and raw.seq == 0:
            self._setup(gs)
        if self._runtime is not None:
            self._runtime.on_game_state(gs)
            self._economy.on_game_state(gs)  # 采矿常驻收敛（ADR-0030）：只发差量
            self._engine.on_game_state(gs)
            self._maintain(raw, gs)
            self._verify(raw, gs)

    def on_session_event(self, event) -> None:
        pass

    def _setup(self, gs) -> None:
        log("=== tank marine push start（生产半边 + 循环蛙跳半边联动）===")
        cc = next(u.position for u in gs.units
                  if u.owner is Owner.SELF and u.type_name == "COMMANDCENTER")
        self._cc = cc
        template = load_ladder_map()
        layout = spawn_layout_nearest(template, cc)
        layer = instantiate_spawn(template, layout, cc)
        other = next((s for name, s in template.spawns.items() if s.origin != layout.origin), layout)
        self._enemy = other.origin  # 敌方主矿 = 另一个出生点
        orig_submit = self._port.submit_operations

        def logging_submit(ops):
            for o in ops:
                log(f"[op] {o.action} tags={o.unit_tags} params={o.params} seq={o.seq}")
            return orig_submit(ops)

        self._port.submit_operations = logging_submit  # type: ignore[method-assign]
        # ADR-0030 会话装配：一张 lease 表（Allocator=WorkerPoolPort）+ 征用登记 + 经济维持器，
        # 三方（战斗组 / 生产建造 / 采矿）共用同一份所有权，flow 与 production 都不互相 import。
        cat = load_all()
        self._reservations = WorkerReservations()
        self._alloc = Allocator(cat, reservations=self._reservations)
        self._economy = EconomyKeeper(cat, self._port, region_layer=layer,
                                     pool=self._alloc, reservations=self._reservations)
        self._runtime = ProductionRuntime(cat, self._port, region_layer=layer,
                                         reservations=self._reservations, economy=self._economy)
        # 三队列并行：SCV训练(CC槽) / 建筑(SCV建造) / 军队(兵营/工厂槽) 互不阻塞
        # 真机校准：开局 8 SCV + CC 13 supply（非标准 12/15）
        self._runtime.submit_queue("scv", [
            QueueItem(op="train", type="terran/scv", count=12),  # 8→20 总（CC 并行于 SCV 建造）
        ])
        self._runtime.submit_queue("macro", [
            # 建筑序列（SCV 建造，一次一个——生产运行时队首阻塞）
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/refinery"),
            # ADR-0030 D2：assign_workers 现在是**目标值**（维持 N 个），不是"再派 N 个" ——
            # 所以第二条是 6 而不是 3；目标持久，精炼厂还在施工也不会蒸发（原 P9）。
            QueueItem(op="assign_workers", task="gas", count=3),
            QueueItem(op="build", type="terran/refinery"),
            QueueItem(op="assign_workers", task="gas", count=6),
            QueueItem(op="build", type="terran/barracks", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/factory", placement=PlacementExact("factory1")),
            QueueItem(op="build", type="terran/reactor"),             # 兵营 #1 反应堆
            QueueItem(op="build", type="terran/reactor"),             # 兵营 #2 反应堆
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/factorytechlab"),
            QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/engineeringbay", placement=PlacementInRegion("home")),
            QueueItem(op="build", type="terran/armory", placement=PlacementInRegion("home")),
            # TODO: 二矿 CC（需扫描 5×5 放置位）+ research（生产运行时暂不支持 RESEARCH op）
        ])
        # 军队训练按**产线**分队列（T4 队首门控后必须这样写）：
        # 队首门控会冻结整条队列，兵营槽忙时若 marine/tank 同队，坦克要等 20 机枪训完才开始 ——
        # 分队列表达"不同产线并行"（与上面 scv/macro 同一原则），保持真机验过的并行产出。
        self._runtime.submit_queue("army_rax", [
            QueueItem(op="train", type="terran/marine", count=20),  # 兵营槽
        ])
        self._runtime.submit_queue("army_fac", [
            QueueItem(op="train", type="terran/siegetank", count=4),  # 工厂槽（未建好前 blocked → 自然等待）
        ])
        # 驻扎点 = CC 朝敌方方向 15 格（近似主矿入口 ramp）
        dx, dy = self._enemy.x - cc.x, self._enemy.y - cc.y
        dist = (dx * dx + dy * dy) ** 0.5
        garrison = Point2(cc.x + dx / dist * 15, cc.y + dy / dist * 15)
        self._engine = FlowEngine(
            parse_strategy(_strategy_yaml()),
            parse_assembly(_assembly_yaml(self._enemy, garrison)),
            self._port,
            region_layer=layer,
            catalog=cat,  # T3 归一化：架起坦克 SIEGETANKSIEGED 仍算 SIEGETANK 组
            allocator=self._alloc,  # 与生产/经济共用所有权表（ADR-0030 D3.5）
        )
        self._bot = self._port._bot
        self._bot._apply_failures = []
        log(f"[setup] CC={cc} 出生点={layout.origin} 敌方主矿={self._enemy}")
        log(f"[setup] 3队列并行: scv(CC槽×12) + macro(建筑depot×5 兵营×2+反应堆×2 气矿×2 工厂+科技实验室 工程站 军械库) + army(机枪×20 坦克×4)；"
            f"维持：步兵<20 + 坦克<4（formup 内）")

    def _maintain(self, raw, gs) -> None:
        """演示粘合：补给维持 + 步兵/坦克维持（formup 内补训）。

        矿/气派工已交给 EconomyKeeper（ADR-0030），这里只剩生产侧的维持。
        """
        if raw.seq - self._maintain_seq < 10:
            return
        self._maintain_seq = raw.seq
        # 矿/气派工已交给 EconomyKeeper（ADR-0030）：每帧收敛，新兵自动进矿、
        # 采空自动回收、气矿建好自动补满 —— 原来这里那段手写维持循环（steward 队列）已删。

        macro_q = self._runtime.queue("macro")
        army_qs = [self._runtime.queue(n) for n in ("army_rax", "army_fac")]
        formup = self._engine._active_step == "formup"
        # 只在 formup 补训（advance 后不补，避免新兵拖组心——run_full_flow 真机教训）
        # 多队列并行：macro(建筑) + army_rax/army_fac(两条产线) + scv(农民) 都排空后才维持
        if (macro_q is not None and not macro_q.items
                and not any(q.items for q in army_qs if q is not None)
                and not any(self._runtime._build_flights.values()) and formup):
            # 补给维持：被拆掉的补给站补建（macro 排空后）
            depots = self._count(gs, "SUPPLYDEPOT")
            supply_q = self._runtime.queue("supply")
            depot_pending = bool(supply_q and supply_q.items) or any(
                f.get("type") == "terran/supplydepot"
                for flights in self._runtime._build_flights.values() for f in flights)
            if depots < 6 and not depot_pending:
                self._runtime.submit_queue("supply", [
                    QueueItem(op="build", type="terran/supplydepot", placement=PlacementInRegion("home")),
                ])
            # 步兵维持：<20 按兵营空槽补训
            marines = self._count(gs, "MARINE")
            if marines < 20:
                open_slots = 0
                for u in gs.units:
                    if u.owner is Owner.SELF and u.type_name == "BARRACKS" and u.build_progress >= 1.0:
                        queued = sum(1 for o in u.orders if "marine" in (o.ability or "").lower())
                        open_slots += max(0, 2 - queued)
                need = min(open_slots, 20 - marines)
                if need > 0:
                    self._runtime.submit_queue("marine", [
                        QueueItem(op="train", type="terran/marine", count=need),
                    ])
            # 坦克维持：<4 按工厂空槽补训（需 techlab 就绪）
            tanks = self._count(gs, "SIEGETANK") + self._count(gs, "SIEGETANKSIEGED")
            if tanks < 4 and self._count(gs, "FACTORYTECHLAB") >= 1:
                factory_open = 0
                for u in gs.units:
                    if u.owner is Owner.SELF and u.type_name == "FACTORY" and u.build_progress >= 1.0:
                        queued = sum(1 for o in u.orders if "siege" in (o.ability or "").lower())
                        factory_open += max(0, 1 - queued)  # 工厂单训练槽（techlab）
                need_t = min(factory_open, 4 - tanks)
                if need_t > 0:
                    self._runtime.submit_queue("tank", [
                        QueueItem(op="train", type="terran/siegetank", count=need_t),
                    ])

    def _verify(self, raw, gs) -> None:
        if raw.seq % 10 == 0:
            marines = self._count(gs, "MARINE")
            tanks = self._count(gs, "SIEGETANK")
            sieged = self._count(gs, "SIEGETANKSIEGED")  # T3 归一化证据：架起实体
            factory = self._count(gs, "FACTORY")
            techlab = self._count(gs, "FACTORYTECHLAB")
            depots = self._count(gs, "SUPPLYDEPOT")
            scvs = self._count(gs, "SCV")
            step = self._engine._active_step
            fails = getattr(self._bot, "_apply_failures", []) if self._bot else []
            flight = {k: [(f["type"], f["frames"]) for f in v] for k, v in
                      self._runtime._build_flights.items()}
            dropped = [(i.type, r) for i, r in self._runtime.dropped][-3:]
            # 队首门控可观测性（H1）：卡在哪个项、卡多久、为什么（T6 真机证据点）
            blocked = {k: (v["item"].type or v["item"].op.value,
                           f"{gs.game_time - v['since']:.0f}s", v["reason"])
                       for k, v in self._runtime.blocked.items()}
            stalls = [m for _, m in self._runtime.stalls][-2:]
            eco = self._economy.snapshot(gs)
            eco_brief = {"目标": eco["targets"], "征用": len(eco["reserved"]), "本帧差量": eco["last_ops"]}
            # 坦克训练诊断：两条军队产线队列状态 + 工厂 orders
            army_items = [(n, [(i.op.value, i.type, i.count) for i in q.items])
                          for n in ("army_rax", "army_fac")
                          if (q := self._runtime.queue(n)) is not None]
            factory_orders = []
            for u in gs.units:
                if u.owner is Owner.SELF and u.type_name == "FACTORY" and u.build_progress >= 1.0:
                    factory_orders = [o.ability or "None" for o in u.orders]
            # 坦克 orders 含 SIEGEMODE（架起证据）+ 组心坐标/距敌矿
            tank_orders = sorted({o.ability or "" for u in gs.units
                                  if u.owner is Owner.SELF and u.type_name in ("SIEGETANK", "SIEGETANKSIEGED")
                                  for o in u.orders})
            # SCV order 采样（前 5 个 SELF SCV 的 order+carrying）
            scv_sample = []
            for u in gs.units:
                if u.owner is Owner.SELF and u.type_name == "SCV":
                    o0 = u.orders[0] if u.orders else None
                    carry = "M" if u.is_carrying_minerals else ("V" if u.is_carrying_vespene else "-")
                    scv_sample.append(f"{u.tag}:{o0.ability if o0 else 'IDLE'}[{carry}]")
                    if len(scv_sample) >= 5:
                        break
            refinery_count = sum(1 for u in gs.units if u.owner is Owner.SELF and u.type_name == "REFINERY")
            ms = [u for u in gs.units if u.owner is Owner.SELF and u.type_name == "MARINE"]
            # 引擎实际用的 leased inf 组心 + arrived 判定
            leased = ""
            if self._engine is not None:
                gid = self._engine._bindings.get("inf")
                if gid is not None:
                    ltags = set(self._engine._alloc.expand_all(gid))
                    lms = [u for u in gs.units if u.tag in ltags]
                    if lms and self._enemy is not None:
                        lcx = sum(u.position.x for u in lms) / len(lms)
                        lcy = sum(u.position.y for u in lms) / len(lms)
                        ld = ((lcx - self._enemy.x) ** 2 + (lcy - self._enemy.y) ** 2) ** 0.5
                        leased = f"leased_inf=({lcx:.1f},{lcy:.1f}) n={len(lms)} 距target={ld:.1f} "
            gc = ""
            if ms and self._enemy is not None:
                cx = sum(u.position.x for u in ms) / len(ms)
                cy = sum(u.position.y for u in ms) / len(ms)
                dist = ((cx - self._enemy.x) ** 2 + (cy - self._enemy.y) ** 2) ** 0.5
                gc = f"组心=({cx:.1f},{cy:.1f}) 距敌矿={dist:.1f} "
            log(f"[tick] seq={raw.seq} t={raw.game_time:.0f} 矿={gs.minerals} 气={gs.vespene} "
                f"supply={gs.supply_used}/{gs.supply_cap} depot={depots} refinery={refinery_count} factory={factory} techlab={techlab} "
                f"scv={scvs} marine={marines} tank={tanks} sieged={sieged} scv_sample={scv_sample} army_q={army_items} fac_orders={factory_orders} {leased}{gc}step={step} "
                f"done={self._engine._done} tank_orders={tank_orders} apply_failures={fails[-3:]} "
                f"flight={flight} dropped={dropped} blocked={blocked} stalls={stalls} eco={eco_brief}")
            # 建筑位置快照（每 100 帧 = 10 游戏秒）：实际落位 vs base_layout 计划位对照
            if raw.seq % 100 == 0:
                bldgs = sorted(
                    (u.type_name, round(u.position.x, 1), round(u.position.y, 1))
                    for u in gs.units
                    if u.owner is Owner.SELF and u.build_progress >= 1.0
                    and u.type_name in ("COMMANDCENTER", "SUPPLYDEPOT", "BARRACKS",
                                        "BARRACKSREACTOR", "BARRACKSTECHLAB",
                                        "FACTORY", "FACTORYTECHLAB", "REFINERY",
                                        "ENGINEERINGBAY", "ARMORY"))
                log(f"[bldg_pos] {bldgs}")

    def _count(self, gs, type_name) -> int:
        return sum(1 for u in gs.units if u.owner is Owner.SELF and u.type_name == type_name)


def main() -> None:
    LOG.unlink(missing_ok=True)
    port = SC2GamePort(
        map_name="LadderMap", race=Race.Terran, difficulty=Difficulty.VeryEasy,
        sink=None, game_time_limit=1200, realtime=False,
    )
    sink = TankFullFlowSink(port)
    port.set_sink(sink)
    port.start("tank-marine-push-1")
    log(f"=== tank marine push done === engine_done={sink._engine._done if sink._engine else False} "
        f"dropped={len(sink._runtime.dropped) if sink._runtime else 0}")


if __name__ == "__main__":
    main()
