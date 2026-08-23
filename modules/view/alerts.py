"""view.alerts：警报最小版（B8）—— 唯一的警报来源。

红线（ADR-0007/0022 反例）：**前端不许自己根据资源数字写一套"卡人口告警"**。
所以判定与文案都在这里，帧里只有结论 + 后端给的 `text_zh`（契约 C3/C4）。

来源分三类，都是**已有数据**，本模块不新增预测：
1. `ProductionRuntime.blocked` / `.stalls` → 队列阻塞（后端已有原因与失速文案）；
2. `planner.ProjectionCurve` → 卡人口 / 缺前置（投影里的 stalled 事件与供给曲线）；
3. 当前 `GameState` → 浮矿 / 浮气 / 产线空闲。

去重与冷却：同一 `id` 在 `COOLDOWN_SECS` 内只出一次（否则每帧一条会把时间线刷满）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game.catalog import Catalog
from game.state import GameState, Owner

from view.schema import AlertView

#: 同一警报的冷却（游戏秒）。1Hz 求值下不冷却会每帧一条。
COOLDOWN_SECS = 20.0

#: 浮矿/浮气阈值：超过这么多还没花出去就值得提醒（真机手感值，可调）
MINERAL_FLOAT = 600
GAS_FLOAT = 400

#: 投影里多久内会卡人口才值得报（更远的以后还会重算）
SUPPLY_LOOKAHEAD = 45.0


@dataclass
class AlertService:
    """有状态：只保存"每个 id 上次报的时间"用于冷却。"""

    catalog: Catalog
    _last: dict[str, float] = field(default_factory=dict)

    def evaluate(
        self,
        gs: GameState,
        *,
        production: dict | None = None,
        curve=None,
    ) -> list[AlertView]:
        """产出本帧应当报的警报（已过冷却过滤）。"""
        out: list[AlertView] = []
        out.extend(self._queue_alerts(gs, production))
        out.extend(self._projection_alerts(gs, curve))
        out.extend(self._economy_alerts(gs))
        return [a for a in out if self._pass_cooldown(a, gs.game_time)]

    # ---- 队列 ----

    def _queue_alerts(self, gs: GameState, production: dict | None) -> list[AlertView]:
        if not production:
            return []
        out: list[AlertView] = []
        for q in production["queues"]:
            blocked = q.get("blocked")
            if not blocked:
                continue
            waited = round(gs.game_time - blocked["since"], 1)
            head = q["items"][0] if q["items"] else None
            what = _zh(self.catalog, head.get("stable_id") if head else None)
            reason = blocked["reason"] or ""
            # 顺序执行的资源等待（攒矿/攒气）是队列的常态，不是事件（用户拍板
            # 2026-08-22：不标红）。只有结构性卡死（前置永不来/供给/放置）才按
            # warned 阈值升级 error —— 那才是真警报。
            if _is_resource_wait(reason):
                severity = "info"
            else:
                # warned = 后端判定已超 STALL_WARN_SECS → 升级为 error（阈值判断在后端，不在前端）
                severity = "error" if blocked["warned"] else "warn"
            out.append(AlertView(
                id=f"queue_blocked/{q['name']}",
                kind="queue_blocked",
                severity=severity,
                at=gs.game_time,
                eta=None,
                text_zh=f"生产队列「{q['name']}」队首阻塞 {waited:g}s：{what}{blocked['reason']}",
                source="production",
                payload={"queue": q["name"], "waited": waited, "reason": blocked["reason"]},
            ))
        return out

    # ---- 投影 ----

    def _projection_alerts(self, gs: GameState, curve) -> list[AlertView]:
        if curve is None:
            return []
        out: list[AlertView] = []
        # 卡人口：投影里第一个 supply_used >= supply_cap 的点（cap 已满 200 不算）
        for p in curve.points:
            if p.t - gs.game_time > SUPPLY_LOOKAHEAD:
                break
            if p.supply_cap < 200 and p.supply_used >= p.supply_cap:
                eta = round(p.t - gs.game_time, 1)
                out.append(AlertView(
                    id="supply_block",
                    kind="supply_block",
                    severity="warn" if eta > 5 else "error",
                    at=gs.game_time, eta=eta,
                    text_zh=(f"约 {eta:g}s 后卡人口（{p.supply_used}/{p.supply_cap}）"
                             if eta > 0 else f"已经卡人口（{p.supply_used}/{p.supply_cap}）"),
                    source="projection",
                    payload={"supply_used": p.supply_used, "supply_cap": p.supply_cap},
                ))
                break
        # 缺前置：投影里 stalled 事件的原因由 planner 给（不在这里编）
        for e in curve.events:
            if e.kind != "stalled" or e.reason is None:
                continue
            if "前置" not in e.reason:
                continue
            out.append(AlertView(
                id=f"prereq_missing/{e.type}",
                kind="prereq_missing",
                severity="warn",
                at=gs.game_time, eta=round(e.t - gs.game_time, 1),
                text_zh=f"{_zh(self.catalog, e.type)}前置不满足：{e.reason}",
                source="projection",
                payload={"stable_id": e.type, "reason": e.reason},
            ))
            break
        return out

    def from_curve(self, curve, *, now: float = 0.0) -> list[AlertView]:
        """规划干跑的**无状态**前瞻警报：同一条投影曲线 → 同一形状的 AlertView。

        与实时警报（`evaluate`，带冷却、混运行时来源）共用 AlertView 数据模型，
        前端因此能用同一个组件渲染两处。这里 stalled 只剩死局（等待不记事件，
        见 planner 的 wait 语义），全部按 error 报；不做冷却 —— 干跑是一次性的。
        """
        out: list[AlertView] = []
        for p in curve.points:
            if p.t - now > SUPPLY_LOOKAHEAD:
                break
            if p.supply_cap < 200 and p.supply_used >= p.supply_cap:
                eta = round(p.t - now, 1)
                out.append(AlertView(
                    id="supply_block", kind="supply_block",
                    severity="warn" if eta > 5 else "error",
                    at=now, eta=eta,
                    text_zh=(f"约 {eta:g}s 后卡人口（{p.supply_used}/{p.supply_cap}）"
                             if eta > 0 else f"已经卡人口（{p.supply_used}/{p.supply_cap}）"),
                    source="projection",
                    payload={"supply_used": p.supply_used, "supply_cap": p.supply_cap},
                ))
                break
        for e in curve.events:
            if e.kind != "stalled":
                continue
            out.append(AlertView(
                id=f"plan_stalled/{e.type}/{e.t:g}",
                kind="plan_stalled",
                severity="error",
                at=now, eta=round(e.t - now, 1),
                text_zh=f"{_mmss(e.t)} {_zh(self.catalog, e.type).strip()}走不下去：{e.reason}",
                source="projection",
                payload={"stable_id": e.type, "reason": e.reason, "t": e.t},
            ))
        return out

    def assembly_gaps(self, curve, assembly, *, now: float = 0.0) -> list[AlertView]:
        """I12-B2：装配 target ↔ 规划总产出交叉校验（干跑终态对账）。

        装配说「步兵组要 10 个机枪兵」，规划却只造 4 个就转去造坦克 —— 两者
        之间此前没有任何约束，缺口只能靠肉眼两边对照。这里在**跑完的**规划曲线
        （until_complete，队列与在途都清空）终态上对账：终局数量 < target
        就发前瞻警报。只对完整曲线有意义 —— live 的窗口投影会在队列中途截断，
        终态数字不代表规划总产出，不参与此对账。
        """
        if curve is None or assembly is None or not curve.points:
            return []
        final = curve.points[-1]
        out: list[AlertView] = []
        for g in assembly.groups:
            for sid, spec in (g.composition or {}).items():
                target = (spec or {}).get("target") if isinstance(spec, dict) else None
                if not target:
                    continue
                have = final.units.get(sid, 0) + final.buildings.get(sid, 0)
                if have >= target:
                    continue
                gid_zh = (f"{g.display_name_zh}（{g.group_id}）"
                          if getattr(g, "display_name_zh", "") else g.group_id)
                out.append(AlertView(
                    id=f"assembly_gap/{g.group_id}/{sid}",
                    kind="assembly_gap",
                    severity="warn",
                    at=now, eta=None,
                    text_zh=(f"装配缺口：{gid_zh}要 "
                             f"{_zh(self.catalog, sid).strip()} ×{target}，"
                             f"规划终局只有 {have} —— 队列跑完也凑不齐"),
                    source="projection",
                    payload={"group": g.group_id, "stable_id": sid,
                             "target": target, "final": have},
                ))
        return out

    # ---- 经济 / 产线 ----

    def _economy_alerts(self, gs: GameState) -> list[AlertView]:
        out: list[AlertView] = []
        if gs.minerals >= MINERAL_FLOAT:
            out.append(AlertView(
                id="mineral_float", kind="mineral_float", severity="warn",
                at=gs.game_time, eta=None,
                text_zh=f"浮矿 {gs.minerals}：矿花不出去（看队首是否阻塞、产线是否够）",
                source="projection", payload={"minerals": gs.minerals},
            ))
        if gs.vespene >= GAS_FLOAT:
            out.append(AlertView(
                id="gas_float", kind="gas_float", severity="info",
                at=gs.game_time, eta=None,
                text_zh=f"浮气 {gs.vespene}：气用不掉",
                source="projection", payload={"vespene": gs.vespene},
            ))
        idle = self._idle_producers(gs)
        if idle:
            # `_zh` 末尾带空格（拼在原因前更自然），join 的时候要去掉
            names = "、".join(sorted({_zh(self.catalog, s).strip() for _, s in idle}))
            out.append(AlertView(
                id="line_idle", kind="line_idle", severity="info",
                at=gs.game_time, eta=None,
                text_zh=f"产线空闲：{names}（共 {len(idle)} 座没在生产）",
                source="production", payload={"tags": [t for t, _ in idle]},
            ))
        return out

    def _idle_producers(self, gs: GameState) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for u in gs.units:
            if u.owner is not Owner.SELF or u.build_progress < 1.0 or u.orders:
                continue
            entry = self.catalog.by_burnysc2_name(
                self.catalog.normalize_burnysc2_name(u.type_name.upper())
            )
            # 能力名取自 catalog 的 KNOWN_CAPABILITIES：产出建筑是 "train"（研究是 "research"）。
            # 别凭直觉写 "produce" —— 那个词表里没有，条件会永远为假、警报静默失效。
            if entry is None or not ({"train", "research"} & set(entry.capabilities)):
                continue
            out.append((u.tag, entry.stable_id))
        return out

    # ---- 冷却 ----

    def _pass_cooldown(self, a: AlertView, now: float) -> bool:
        last = self._last.get(a.id)
        if last is not None and now - last < COOLDOWN_SECS:
            return False
        self._last[a.id] = now
        return True


#: 资源等待类阻塞原因（constraint/checks.py 的文案前缀）：攒矿/攒气是顺序执行的
#: 常态，不是警报 —— 不升级 error（用户拍板 2026-08-22「缺矿不应标红」）。
RESOURCE_WAIT_MARKS = ("晶体矿不足", "高能瓦斯不足", "缺矿", "缺气")


def _is_resource_wait(reason: str) -> bool:
    return any(mark in reason for mark in RESOURCE_WAIT_MARKS)


def _zh(catalog: Catalog, stable_id: str | None) -> str:
    """中文名一律取自 catalog（前端无 i18n 字典，红线 C4）。未登记就留空，不编名字。"""
    if not stable_id:
        return ""
    entry = catalog.by_stable_id(stable_id)
    return f"{entry.display_name_zh} " if entry else f"{stable_id} "


def _mmss(t: float) -> str:
    s = max(0, int(round(t)))
    return f"{s // 60:02d}:{s % 60:02d}"
