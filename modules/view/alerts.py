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

#: skipped 家族警报（D1）：一次性（同一 uid 只报一次，状态不会自己变回去）
_SKIP_ALERT_KINDS = frozenset({"prereq_missing", "placement_collision", "queue_skipped"})

#: 敌方踪迹有效窗（游戏秒，D 批 2026-08-24 用户设计）：observe 只看得到**当前帧**，
#: 离开视野就蒸发 —— 所以在警报层留滚动记忆：窗内统计「见过多少个不同敌兵 +
#: 峰值同屏多少 + 最后出现在哪」
CONTACT_WINDOW = 10.0
#: 窗内不同敌兵到这个数（或峰值同屏到 5）→ warn（够格叫醒 sleep）
CONTACT_WARN_DISTINCT = 3
CONTACT_WARN_PEAK = 5
#: 「活跃警报」窗口（游戏秒）：最近这么久内报过的 warn+ 仍算活跃 —— sleep 轮询用
ALERT_ACTIVE_SECS = 15.0


@dataclass
class AlertService:
    """有状态：冷却账本 + 敌方踪迹滚动记忆（D 批）+ 最近报过的警报（活跃面）。"""

    catalog: Catalog
    _last: dict[str, float] = field(default_factory=dict)
    #: tag → (最后看到的游戏时刻, type_name, 位置)。observe 是当前帧的 —— 敌方踪迹
    #: 必须在显示/警报层留一段记忆，否则 agent sleep 一轮就全忘了
    _contact: dict[int, tuple[float, str, tuple[float, float]]] = field(default_factory=dict)
    #: 每帧敌方同屏数 [(t, count)] —— 滚动窗内取峰值
    _contact_frames: list[tuple[float, int]] = field(default_factory=list)
    #: id → 最近一次**报出**的警报（活跃面用：sleep 轮询问「现在有没有正在响的」）
    _fired: dict[str, AlertView] = field(default_factory=dict)
    #: 已报过一次的 skipped 家族警报 id（D1：一次性，防刷屏）
    _skip_fired: set[str] = field(default_factory=set)

    def evaluate(
        self,
        gs: GameState,
        *,
        production: dict | None = None,
        curve=None,
    ) -> list[AlertView]:
        """产出本帧应当报的警报（已过冷却过滤）。

        skipped 家族（prereq_missing/placement_collision，D1）是**一次性**的：
        项的状态不会自己变回去，重复报只会刷屏 —— 同一 uid 只报一次；
        修复后重提的新项是新 uid，自然会有新警报。
        """
        out: list[AlertView] = []
        out.extend(self._queue_alerts(gs, production))
        out.extend(self._supply_alerts(gs, production))
        out.extend(self._projection_alerts(gs, curve))
        out.extend(self._economy_alerts(gs))
        out.extend(self._contact_alerts(gs))
        out = [a for a in out
               if a.kind not in _SKIP_ALERT_KINDS or a.id not in self._skip_fired]
        passed = [a for a in out if self._pass_cooldown(a, gs.game_time)]
        for a in passed:
            self._fired[a.id] = a
            if a.kind in _SKIP_ALERT_KINDS:
                self._skip_fired.add(a.id)
        return passed

    def active_alerts(self, now: float, *, min_severity: str = "info",
                      within: float = ALERT_ACTIVE_SECS) -> list[dict]:
        """最近 `within` 游戏秒内报过、且仍「在响」的警报（按严重度过滤）。
        sleep 的唤醒链用（D 批）：纯拉模式的补丁 —— agent 在 sleep 也能被 warn+ 叫醒。"""
        rank = {"info": 0, "warn": 1, "error": 2}
        floor = rank.get(min_severity, 0)
        return [
            {"id": a.id, "kind": a.kind, "severity": a.severity, "text_zh": a.text_zh,
             "at": round(a.at, 1)}
            for a in self._fired.values()
            if now - a.at <= within and rank.get(a.severity, 0) >= floor
        ]

    # ---- 敌方踪迹（D 批：10s 滚动窗） ----

    def _contact_alerts(self, gs: GameState) -> list[AlertView]:
        now = gs.game_time
        seen_now = 0
        for u in gs.units:
            if u.owner is not Owner.ENEMY:
                continue
            seen_now += 1
            self._contact[u.tag] = (now, u.type_name, (float(u.position.x), float(u.position.y)))
        # 窗外记忆淘汰（滚动窗）
        self._contact = {tag: v for tag, v in self._contact.items()
                         if now - v[0] <= CONTACT_WINDOW}
        self._contact_frames.append((now, seen_now))
        self._contact_frames = [(t, c) for t, c in self._contact_frames
                                if now - t <= CONTACT_WINDOW]
        distinct = len(self._contact)
        if distinct == 0:
            return []
        peak = max((c for _, c in self._contact_frames), default=0)
        last_tag = max(self._contact, key=lambda k: self._contact[k][0])
        _, tname, pos = self._contact[last_tag]
        zh = _zh(self.catalog, self._enemy_stable_id(tname)).strip() or tname
        severity = ("warn" if (distinct >= CONTACT_WARN_DISTINCT or peak >= CONTACT_WARN_PEAK)
                    else "info")
        return [AlertView(
            id="enemy_contact",
            kind="enemy_contact",
            severity=severity,
            at=now, eta=None,
            text_zh=(f"敌方踪迹（{CONTACT_WINDOW:g}s 窗）：见过 {distinct} 个不同敌兵，"
                     f"峰值同屏 {peak}；最后出现 {zh} @ ({pos[0]:.0f},{pos[1]:.0f})"),
            source="session",
            payload={"distinct": distinct, "peak": peak,
                     "last_pos": list(pos), "last_type": tname},
        )]

    def _enemy_stable_id(self, type_name: str) -> str | None:
        entry = self.catalog.by_burnysc2_name(
            self.catalog.normalize_burnysc2_name(str(type_name).upper()))
        return entry.stable_id if entry else None

    # ---- 队列 ----

    def _queue_alerts(self, gs: GameState, production: dict | None) -> list[AlertView]:
        if not production:
            return []
        from production.semantics import CAPACITY_WARN_SECS, is_capacity_wait

        out: list[AlertView] = []
        for q in production["queues"]:
            # D1②③ live 面：skipped 项进警报（一次性），带 uid 与整改建议
            out.extend(self._skipped_alerts(gs, q))
            blocked = q.get("blocked")
            if not blocked:
                continue
            waited = round(gs.game_time - blocked["since"], 1)
            blocked_items = [it for it in q.get("items", [])
                             if it.get("uid") == blocked.get("uid")] or q.get("items", [])
            head = blocked_items[0] if blocked_items else None
            what = _zh(self.catalog, head.get("stable_id") if head else None)
            reason = blocked["reason"] or ""
            # 顺序执行的资源等待（攒矿/攒气）是队列的常态，不是事件（用户拍板
            # 2026-08-22：不标红）。训练槽满（production_capacity）也是瞬态等待，
            # 但超 60s 该有人知道（PLAN-V2 §7 拍板）。结构性卡死才按 warned 阈值升 error。
            if is_capacity_wait(reason):
                severity = "warn" if waited >= CAPACITY_WARN_SECS else "info"
            elif _is_resource_wait(reason):
                severity = "info"
            else:
                # warned = 后端判定已超 STALL_WARN_SECS → 升级为 error（阈值判断在后端，不在前端）
                severity = "error" if blocked["warned"] else "warn"
            # E 批（2026-08-24，只告警不动作）：区分「产出建筑被摧毁」与「还没建」——
            # 曾建成过才消失 = 大概率被毁（重排/重建）；从没建过 = 建造被卡/掉单
            ever = blocked.get("producer_ever_ready")
            hint = ""
            if ever is True:
                hint = "；产出建筑曾建成、现在不在 —— 大概率被摧毁：重排队首或重建"
            elif ever is False:
                hint = "；产出建筑从没建成过 —— 检查建造项是否被卡/掉单"
            who = f"uid={blocked.get('uid')} " if blocked.get("uid") else ""
            out.append(AlertView(
                id=f"queue_blocked/{q['name']}",
                kind="queue_blocked",
                severity=severity,
                at=gs.game_time,
                eta=None,
                text_zh=(f"生产队列「{q['name']}」{who}阻塞 {waited:g}s："
                         f"{what}{blocked['reason']}{hint}"),
                source="production",
                payload={"queue": q["name"], "waited": waited, "reason": blocked["reason"],
                         "producer_ever_ready": ever, "uid": blocked.get("uid")},
            ))
        return out

    def _skipped_alerts(self, gs: GameState, q: dict) -> list[AlertView]:
        """skipped 项（ADR-0032 账本）：prereq_missing 家族（D1②③）+ placement_collision。

        `producer_ever_ready`（runtime `_ever_ready` 判「曾有现无」）：True = 相关建筑
        被摧毁（error，要重建）；False/None = 从没建/前置没排（warn，给 before_uid 建议）。
        """
        out: list[AlertView] = []
        for it in q.get("items", []):
            if it.get("status") != "skipped":
                continue
            uid = it.get("uid") or f"#{it.get('index')}"
            what = _zh(self.catalog, it.get("stable_id")).strip() or str(it.get("op"))
            reason = it.get("reason")
            if reason == "prereq_missing":
                ever = it.get("producer_ever_ready")
                if ever is True:
                    kind, severity = "prereq_missing", "error"
                    hint = "相关建筑曾建成、现在不在 —— 大概率被摧毁：重建或换路径"
                else:
                    kind, severity = "prereq_missing", "warn"
                    hint = f"前置不在场也不在队列 —— 在 {uid} 前插建造项，或 remove uid={uid}"
            elif reason == "placement_collision":
                kind, severity = "placement_collision", "warn"
                hint = "放置无可用位 —— 扩图层槽位/换 placement 后重提"
            else:
                kind, severity = "queue_skipped", "warn"
                hint = reason or "执行失败"
            out.append(AlertView(
                id=f"{kind}/{q['name']}/{uid}",
                kind=kind,
                severity=severity,
                at=gs.game_time,
                eta=None,
                text_zh=f"队列「{q['name']}」uid={uid} {what} 已跳过：{hint}",
                source="production",
                payload={"queue": q["name"], "uid": it.get("uid"),
                         "stable_id": it.get("stable_id"), "reason": reason,
                         "producer_ever_ready": it.get("producer_ever_ready")},
            ))
        return out

    def _supply_alerts(self, gs: GameState, production: dict | None) -> list[AlertView]:
        """supply_capped（D1 live 面，替代已删除的 supply_block 前瞻）：
        已卡人口 **且** 队列/在途没有任何供给建筑 —— 建议插 depot，带 before_uid。
        队列里已排就闭嘴（等它建成就好）。"""
        from planner.economy import DEFAULT_ECON
        from production.semantics import STATUS_IN_PROGRESS, STATUS_PENDING

        if gs.supply_used < gs.supply_cap or gs.supply_cap >= 200:
            return []
        supply_types = {t for t, n in DEFAULT_ECON.supply_provided.items() if n > 0}
        head_uid: str | None = None
        if production:
            for q in production.get("queues", []):
                for it in q.get("items", []):
                    if it.get("status") not in (STATUS_PENDING, STATUS_IN_PROGRESS):
                        continue
                    if it.get("op") == "build" and it.get("stable_id") in supply_types:
                        return []  # 已排供给：等它
                    if head_uid is None and it.get("status") == STATUS_PENDING:
                        head_uid = it.get("uid")
            for f in production.get("in_flight", []):
                if f.get("stable_id") in supply_types:
                    return []
        before = f"（before_uid={head_uid}）" if head_uid else ""
        return [AlertView(
            id="supply_capped",
            kind="supply_capped",
            severity="warn",
            at=gs.game_time, eta=None,
            text_zh=(f"已卡人口（{gs.supply_used:g}/{gs.supply_cap:g}）且队列/在途"
                     f"没有供给建筑 —— 建议插 build 补给站{before}"),
            source="production",
            payload={"supply_used": gs.supply_used, "supply_cap": gs.supply_cap,
                     "before_uid": head_uid},
        )]

    # ---- 投影 ----

    def _projection_alerts(self, gs: GameState, curve) -> list[AlertView]:
        """live 窗口投影的前瞻警报。

        卡人口前瞻（旧 supply_block）已删（PLAN-V2 D1/D7）：投影替人补供给的
        「前瞻」与手动哲学冲突 —— live 面由 `_supply_alerts`（真实队列有没有排
        供给建筑）接管，规划面由 `from_curve` 的 supply_capped 给建议。
        """
        if curve is None:
            return []
        out: list[AlertView] = []
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
                    id="supply_capped", kind="supply_capped",
                    severity="warn" if eta > 5 else "error",
                    at=now, eta=eta,
                    text_zh=(f"约 {eta:g}s 后卡人口（{p.supply_used}/{p.supply_cap}）—— "
                             "队列里没有供给建筑，插 build 补给站"
                             if eta > 0 else f"已经卡人口（{p.supply_used}/{p.supply_cap}）—— "
                             "队列里没有供给建筑，插 build 补给站"),
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
