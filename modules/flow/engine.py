"""flow engine：RuntimeSink；per-frame eval Step 分支 → group_action → 去重 → 展开 → Operation。

driver→world→flow：engine.on_game_state(GameState) → eval active step → 命中 do →
group_action 经去重 + Allocator.expand 成 Operation → port.submit_operations。
exit_step 按边路由（本帧结束，下帧求值新 step）；exit_strategy 结束并记 exit_record。
有界环兜底：step 转移数超 loop_limits.max_step_transitions（未声明则 DEFAULT_MAX_STEP_TRANSITIONS）
→ 结束并记 exit_record = failed/LOOP_LIMIT（ADR-0021 §4/验收 #4）。
动作去重（spec-003 §2.1）：相同 (slot,type,action_atom,params) 不重发。
port 用 duck-typing（任何有 submit_operations 的对象）；flow 不 import driver。
"""
from __future__ import annotations

import json
from collections import deque

from game import GameState, Operation
from game.operation import OP_CATALOG, ParamType

from flow.allocator import Allocator
from flow.manifest import FlowAssembly, StrategyManifest, validate_assembly, validate_map_names
from flow.predicates import EvalCtx, eval_when
from tactical_map.resolver import resolve_action_params

# 全局转移上限（ADR-0021 §4 第 3 条）：strategy 未声明 loop_limits.max_step_transitions 时兜底，
# 保证"没有任何配置能让引擎无限转移 step"。声明值优先（编译期已校验为正整数）。
DEFAULT_MAX_STEP_TRANSITIONS = 200

#: 转移历史保留条数（观测用）。只保留最近 K 条：完整历史属于事件日志（ADR-0024），
#: 引擎内存里留一小段就够 UI 显示"最近怎么转过来的"。
TRANSITION_HISTORY = 20

#: under_attack 的受击回看窗口（游戏秒）：掉血后这么久内算"正在被打"。
#: hp 是逐帧快照，没有伤害事件 —— 窗口太短会闪断（1Hz 求值漏拍），太长会把旧伤算成现伤。
HIT_MEMORY_SECS = 5.0

#: engaged 的近敌判定缓冲（格）：敌人进入 (单位射程 + 该值) 视为已接敌。
#: 攻击移动中的单位射程外一点也在"交火"语义里（弹道/走位中）。
ENGAGE_BUFFER = 2.0


class FlowEngine:
    def __init__(self, manifest: StrategyManifest, assembly: FlowAssembly, port,
                 region_layer=None, catalog=None, allocator=None) -> None:
        if catalog is None:
            raise ValueError(
                "FlowEngine 需要 catalog（game.catalog.load_all()）：flow authoring 只用 stable id"
                "（如 terran/marine），引擎靠 catalog 把 gs 的 burnysc2 实体名（含 SIEGETANKSIEGED 这类"
                "形态变体）翻译回 stable id 才能匹配（T1 词汇统一，D1）"
            )
        validate_assembly(manifest, assembly)  # R6：绑定/引用错误在构造期拒绝
        # R6/F5-3：策略里写死的点位名/区域名在这里才有 layer 可查 —— 构造期拒绝，不留到运行期静默失败
        name_problems = validate_map_names(manifest, region_layer)
        if name_problems:
            raise AssertionError("地图名字校验失败:\n- " + "\n- ".join(name_problems))
        self._m = manifest
        self._port = port
        # 热切 V1（批 C）要留着：swap 用改了 strategy_ref 的 assembly shim 重跑全套
        # validate_assembly（同装配约束），并按实例参数重建 params。
        self._assembly = assembly
        self._instance_params = dict(assembly.strategy_instances[0].params)
        # Allocator 可由会话装配注入（ADR-0030 D3.5）：它同时是生产/经济的工兵所有权表（WorkerPoolPort），
        # 三方必须共用同一个实例；不注入就自建，保持既有用法与测试不变。
        self._alloc = allocator if allocator is not None else Allocator(catalog=catalog)
        self._region_layer = region_layer  # 区域模型（map 名→坐标，ADR-0029）
        self._catalog = catalog  # 透传给 EvalCtx（谓词层归一化，T3）
        for g in assembly.groups:
            self._alloc.create_group(g.group_id, g.composition)
        si = assembly.strategy_instances[0]
        self._instance_id = si.instance_id
        self._strategy_ref = si.strategy_ref
        self._bindings = si.bindings  # slot -> group_id
        self._params = {p: spec.get("default") for p, spec in manifest.params.items()}
        self._params.update(si.params)
        self._active_step = manifest.initial_step
        # 每个 step 各自的进入次数（环上策略才有意义："ADVANCE 第 3 次"）。
        # 累计转移数另有 _step_transition_count，二者不是一回事。
        self._step_entries: dict[str, int] = {manifest.initial_step: 1}
        self._step_transition_count = 0
        self._variables = {v: spec.get("default") for v, spec in manifest.variables.items()}
        self._locals: dict = {}  # 进入 step 时重置（spec-003 §3.2）
        # ---- 二十六轮（T8 落地）：计时器与交火态 ----
        # 计时器：name -> {"start": t, "end": t|None}。start_timer 起算、stop_timer 冻结
        # （end 后 elapsed 不再增长）；未 start 的名字 elapsed = None（比较降级 False + 诊断）。
        self._timers: dict[str, dict] = {}
        # 交火态：上一帧的 hp 快照 + 最近掉血时刻。under_attack = 窗口内掉过血（HIT_MEMORY_SECS），
        # engaged = 攻击命令或近敌入射程 —— 都从每帧 gs 推导，不发新命令、不加新帧。
        self._prev_hp: dict[int, float] = {}
        self._last_hit: dict[int, float] = {}
        self._strategy_start: float | None = None
        self._step_entered: float | None = None
        # 去重状态：(slot, type, atom) -> (unit_tags, params_key)。
        # 单位集合必须进键：组补兵/伤亡后成员变了，同一条命令要重新下发，否则新兵永远待命（F1）。
        self._last_emitted: dict[tuple, tuple] = {}
        self._done = False
        self.exit_record: dict | None = None  # 结束原因（exit_strategy 的 kind/reason，或 LOOP_LIMIT）
        # 求值期诊断（H6）：(step, kind, detail) -> 次数。None 比较等"降级为 False"的路径留痕，
        # 不静默；UI/agent/真机日志读它就知道"条件其实没求出来"。
        self.eval_diagnostics: dict[tuple, int] = {}
        self._op_seq = 0
        # ---- 观测（B1，供 view.adapt 读；引擎不认识 view）----
        # 这两样必须在求值/转移**当场**记：分支命中不落到任何持久状态里，
        # 转移原因在转移完成后也无从反推。事后从外部推断不出来，所以写入点就在这里。
        self._last_game_time: float | None = None
        self._branch_hit: dict | None = None
        self._transitions: deque = deque(maxlen=TRANSITION_HISTORY)

    # ---- RuntimeSink ----
    def on_game_state(self, gs: GameState) -> None:
        if self._done:
            return
        if self._strategy_start is None:
            self._strategy_start = gs.game_time
        if self._step_entered is None:
            self._step_entered = gs.game_time
        self._last_game_time = gs.game_time
        self._branch_hit = None  # 本帧未命中就是 None（等待型 step 的真实状态）
        self._alloc.refresh(gs)
        ctx = EvalCtx(gs, self._alloc, self._bindings, self._params, self._variables,
                      self._strategy_start, self._step_entered, self._region_layer,
                      catalog=self._catalog, definitions=self._m.definitions,
                      step_id=self._active_step, diagnostics=self.eval_diagnostics,
                      locals=self._locals,
                      timers=lambda name: self._timer_elapsed(name, gs.game_time),
                      combat=self._combat_view(gs))
        step = self._m.steps[self._active_step]
        for index, b in enumerate(step.get("branches", [])):
            when = b.get("when")
            if when is None or eval_when(when, ctx):  # else（无 when）或 true
                self._branch_hit = {
                    "step_id": self._active_step,
                    "branch_id": b.get("branch_id"),
                    "index": index,
                }
                self._exec_do(b.get("do", []), ctx, gs)
                self._track_hp(gs)
                return  # 首条命中，本帧结束
        self._track_hp(gs)

    # ---- 热切 V1（批 C）----

    def swap_strategy(self, manifest: StrategyManifest) -> None:
        """整份策略切换（对运行中的引擎；帧边界由会话层保证调用时机）。

        同装配约束：当前 assembly 不换（组结构/绑定装配期固定），新策略必须吃得上它 ——
        用改了 `strategy_ref` 的 assembly shim 跑全套 `validate_assembly`
        （slots 绑定/产能覆盖/实例参数类型全查），不满足抛 AssertionError →
        调用方转 409，**引擎状态不受影响**（先校验后变更，本方法没有中间态）。

        续位规则：新策略含同名 `active_step` → 停留该 step（locals/timers 保留）；
        不含 → 从 `initial_step` 起、locals/timers 清零。variables 是策略级：
        同名保留现值、新名取默认、消失的丢弃。对已结束的策略 swap = 复活（done 清零）。
        """
        from dataclasses import replace as _replace

        shim = _replace(
            self._assembly,
            strategy_instances=[
                _replace(self._assembly.strategy_instances[0], strategy_ref=manifest.id)
            ],
        )
        validate_assembly(manifest, shim)
        name_problems = validate_map_names(manifest, self._region_layer)
        if name_problems:
            raise AssertionError("地图名字校验失败:\n- " + "\n- ".join(name_problems))

        old = self._m
        old_step = self._active_step
        keep = old_step in manifest.steps
        new_step = old_step if keep else manifest.initial_step
        variables = {v: spec.get("default") for v, spec in manifest.variables.items()}
        variables.update({k: val for k, val in self._variables.items()
                          if k in manifest.variables})
        self._m = manifest
        self._strategy_ref = manifest.id
        self._params = {p: spec.get("default") for p, spec in manifest.params.items()}
        self._params.update(self._instance_params)
        self._variables = variables
        self._locals = dict(self._locals) if keep else {}
        self._timers = dict(self._timers) if keep else {}
        self._active_step = new_step
        entries = {k: v for k, v in self._step_entries.items() if k in manifest.steps} if keep else {}
        entries[new_step] = entries.get(new_step, 0) + 1
        self._step_entries = entries
        self._step_transition_count = 0
        # 新策略的第一帧命令不该被旧去重键吞掉（旧签名只对"完全相同的重复命令"去重，
        # 热切后第一条恰恰可能是想重发的同款命令）
        self._last_emitted = {}
        self._branch_hit = None
        self._done = False
        self.exit_record = None
        self._transitions.append({
            "from_step": old_step, "to": new_step, "kind": "swap",
            "reason": (f"{old.id}@{old.version}→{manifest.id}@{manifest.version}"
                       + ("（续位）" if keep else "（重起）")),
            "at": self._last_game_time if self._last_game_time is not None else 0.0,
        })

    # ---- 计时器 / 交火态（二十六轮 T8；供 EvalCtx 的 timers/combat 注入）----

    def _timer_elapsed(self, name: str, now: float):
        """timer_elapsed 的读侧：未 start / 引擎没跑过 → None（比较降级 False + 诊断）。"""
        t = self._timers.get(name)
        if t is None:
            return None
        end = t["end"] if t["end"] is not None else now
        return max(0.0, end - t["start"])

    def _combat_view(self, gs: GameState):
        """engaged / under_attack 的读侧闭包（装进 EvalCtx.combat）。

        组集合在构造 ctx 前算好（一帧一份，两个谓词共享）；
        slot 级查询就是集合成员判断。
        """
        eng = self._engaged_groups(gs)
        hit = self._under_attack_groups(gs)

        class View:
            def engaged(_self, slot):
                return slot in eng

            def under_attack(_self, slot):
                return slot in hit

        return View()

    def _engaged_groups(self, gs) -> frozenset[str]:
        """交火中的 slot 集合：组内任一单位带攻击命令，或任一敌人进入其射程 + 缓冲。"""
        from game import Owner
        from tactical_map.spatial import distance

        enemies = [u for u in gs.units if u.owner is Owner.ENEMY]
        out: set[str] = set()
        for slot, gid in self._bindings.items():
            tags = set(self._alloc.expand_all(gid)) if gid else set()
            units = [u for u in gs.units if u.tag in tags]
            if not units:
                continue
            # 攻击命令不需要敌人可见（迷雾下 order 也存在）；近敌判定没有敌人自然为 False
            engaged = False
            for u in units:
                if any("attack" in (o.ability or "").lower() for o in u.orders):
                    engaged = True
                    break
                entry = self._catalog.by_burnysc2_name(
                    self._catalog.normalize_burnysc2_name(u.type_name.upper()))
                rng = float(entry.attack_range or 4.0) + ENGAGE_BUFFER if entry else 6.0
                if any(distance(u.position, e.position) <= rng for e in enemies):
                    engaged = True
                    break
            if engaged:
                out.add(slot)
        return frozenset(out)

    def _under_attack_groups(self, gs) -> frozenset[str]:
        """近期掉血的 slot 集合：窗口内 hp 下降过的组内单位（hp 历史推导，无伤害事件）。"""
        out: set[str] = set()
        for slot, gid in self._bindings.items():
            tags = set(self._alloc.expand_all(gid)) if gid else set()
            if any(t in self._last_hit and gs.game_time - self._last_hit[t] <= HIT_MEMORY_SECS
                   for t in tags):
                out.add(slot)
        return frozenset(out)

    def _track_hp(self, gs: GameState) -> None:
        """帧尾更新 hp 快照 + 掉血时刻（under_attack 的记忆来源）。

        必须在**求值之后**调：本帧的 under_attack 反映"上一帧到这一帧"的变化，
        同帧写同帧读会把一次掉血当两次。阵亡单位（快照里有、场上没了）也记一击。
        """
        current = {u.tag: u.hp for u in gs.units if u.owner is not None and u.hp_max > 0}
        for tag, prev in self._prev_hp.items():
            now_hp = current.get(tag)
            if now_hp is None or now_hp < prev - 1e-6:
                self._last_hit[tag] = gs.game_time
        self._prev_hp = current

    def on_session_event(self, event) -> None:
        pass

    # ---- 读模型（B1）----
    def snapshot(self) -> dict:
        """当前运行时状态的显式只读快照（供 view / agent / 复盘录制）。

        为什么必须有这个方法而不是让外部读 `_` 字段：引擎离三族全量还有很长的路，
        如果 UI 直接依赖私有字段，之后任何重构都会打断 UI。这里是唯一的观测出口。

        返回**普通 dict**（不是 view 的 dataclass）：flow 不认识 view，架构测试锁死这个方向。
        键名与 `docs/contract/plan-frontend.md` §2 的 StrategyView 对齐，由 view.adapt 显式映射。
        """
        now = self._last_game_time
        entered = self._step_entered
        return {
            "instance_id": self._instance_id,
            "strategy_ref": self._strategy_ref,
            "version": int(self._m.version),
            "params": dict(self._params),
            "variables": dict(self._variables),
            "locals": dict(self._locals),
            # 计时器读数（二十六轮 T8）：观测用，契约 StrategyView 未收（内部态）；
            # 调试页要看时可从这里取。未启动的表不在 dict 里（elapsed=None 的语义）。
            "timers": {name: self._timer_elapsed(name, now) if now is not None else None
                       for name in self._timers},
            "definitions": dict(self._m.definitions),
            "active_step": self._active_step,
            "step_entered_at": entered,
            "step_elapsed": None if (now is None or entered is None) else (now - entered),
            "step_entry_count": self._step_entries.get(self._active_step, 1),
            "branch_hit": None if self._branch_hit is None else dict(self._branch_hit),
            "transitions": [dict(t) for t in self._transitions],
            "transition_count": self._step_transition_count,
            "transition_limit": int(
                self._m.loop_limits.get("max_step_transitions", DEFAULT_MAX_STEP_TRANSITIONS)
            ),
            "done": self._done,
            "exit_record": None if self.exit_record is None else dict(self.exit_record),
            "bindings": dict(self._bindings),
            # 求值诊断（H6）：("step", kind, detail) -> 次数。摊平成列表给 UI，
            # 让"条件其实没求出来"这件事在调试页可见（不静默）。
            "eval_diagnostics": [
                {"step_id": k[0], "kind": k[1], "detail": k[2], "count": v}
                for k, v in sorted(self.eval_diagnostics.items())
            ],
            "groups": _allocator_snapshot(self._alloc),
        }

    # ---- do 执行 ----
    def _exec_do(self, actions: list, ctx: EvalCtx, gs: GameState) -> None:
        for a in actions:
            op = a.get("op")
            if op == "group_action":
                self._emit_group_action(a, ctx, gs)
            elif op == "exit_step":
                self._do_exit_step(a, gs)
                return  # 本帧结束
            elif op == "exit_strategy":
                self._done = True
                self.exit_record = {"kind": a.get("kind"), "reason": a.get("reason")}
                return
            elif op == "set_variable":
                self._variables[a["name"]] = eval_when(a.get("value"), ctx)
            elif op == "set_local":
                self._locals[a["name"]] = eval_when(a.get("value"), ctx)
            elif op == "start_timer":
                # 幂等：已在走的不归零（do 每帧重执行，非幂等会让表永远到不了阈值）。
                # 要重新起算：先 stop_timer 再 start_timer（显式两步，不藏在重复执行里）。
                t = self._timers.get(a["name"])
                if t is None or t["end"] is not None:
                    self._timers[a["name"]] = {"start": gs.game_time, "end": None}
            elif op == "stop_timer":
                t = self._timers.get(a["name"])
                if t is not None and t["end"] is None:
                    t["end"] = gs.game_time  # 冻结读数；stop 未启动的表是 no-op（不静默谎报 0）
            else:
                raise ValueError(f"unknown do op {op!r}")  # 编译期已拦；此为兜底

    def _emit_group_action(self, a: dict, ctx: EvalCtx, gs: GameState) -> None:
        slot = a["group_slot"]
        type_name = a["type"]
        atom = a["action_atom"]
        # 先展开单位：空组直接 no-op 且**不写去重键** ——
        # 旧实现先写键再判空，导致"首次求值时组还空着"的 step 之后永远不再下发（F1 的变体）。
        gid = self._bindings.get(slot)
        tags = self._alloc.expand(gid, type_name) if gid else []
        if not tags:
            return  # 空 group no-op（下一帧有人了再发）
        params = _resolve_params(a.get("params", {}), ctx)
        # ADR-0029 D1：emit 前把 map 名解析成数值（去重也在解析后，spec-003 §2.1）
        params = resolve_action_params(atom, params, self._region_layer)
        # T4 去重量化：POINT/POINTS 参数 round 到整格进键（动态点微移 <0.5 不重发、≥1 才重发）；
        # 实际下发的 params 保留精确值（仅 pkey 量化）。
        pkey = json.dumps(_quantize_for_dedup(atom, params), sort_keys=True, default=str)
        signature = (tuple(tags), pkey)  # 单位集合 + 参数：任一变化都要重发（F1）
        if self._last_emitted.get((slot, type_name, atom)) == signature:
            return  # 去重：同 (slot,type,action,单位集合,params) 不重发
        self._last_emitted[(slot, type_name, atom)] = signature
        self._op_seq += 1
        self._port.submit_operations([Operation(
            op_id=self._op_seq, unit_tags=tags, action=atom, params=params, seq=gs.seq,
        )])

    def _do_exit_step(self, a: dict, gs: GameState) -> None:
        k, r = a.get("kind"), a.get("reason")
        edge = next((e for e in self._m.edges
                     if e["from"] == self._active_step and e["kind"] == k and e["reason"] == r), None)
        if edge is None:
            # 编译期已拦（死边/无匹配边都是编译错误）；热改或手构造 manifest 时留痕，不静默卡住
            note_diagnostic_key = (self._active_step, "exit_step_no_edge", f"{k}/{r}")
            self.eval_diagnostics[note_diagnostic_key] = (
                self.eval_diagnostics.get(note_diagnostic_key, 0) + 1
            )
            return
        self._transitions.append({
            "from_step": self._active_step, "to": edge["to"],
            "kind": k, "reason": r, "at": gs.game_time,
        })
        self._active_step = edge["to"]
        self._step_entries[edge["to"]] = self._step_entries.get(edge["to"], 0) + 1
        self._step_transition_count += 1
        self._step_entered = gs.game_time
        self._locals = {}  # 进入新 step：locals 重置（spec-003 §3.2）
        limit = self._m.loop_limits.get("max_step_transitions", DEFAULT_MAX_STEP_TRANSITIONS)
        if self._step_transition_count > limit:
            # 有界环兜底：转移上限（ADR-0021 验收 #4 —— 以 LOOP_LIMIT 失败，不静默停）
            self._done = True
            self.exit_record = {"kind": "failed", "reason": "LOOP_LIMIT", "limit": limit}
        # 本帧结束（不本帧求值新 step；下帧求值）


def _allocator_snapshot(alloc) -> list[dict]:
    """取 allocator 的读模型。

    ADR-0030 D3.5 会把 Allocator 改成**会话装配注入**（`FlowEngine(..., allocator=...)`）。
    届时注入进来的实现必须同样提供 `snapshot()` —— 缺了就在这里显式报错，
    而不是抛一个看不出所以然的 AttributeError（不静默）。包装类请把 `snapshot()` 透传下去。
    """
    snap = getattr(alloc, "snapshot", None)
    if snap is None:
        raise TypeError(
            f"注入的 allocator（{type(alloc).__name__}）缺 snapshot()："
            "flow 的读模型要靠它给出 composition/current/refill_state/leased_tags"
            "（契约 frame/flow.groups；见 docs/contract/plan-backend-view.md B1）"
        )
    return snap()


def _resolve_params(params: dict, ctx: EvalCtx) -> dict:
    """动作参数求值：与 when 共用 eval_when（T2 起只有一个求值器 —— 同词表、同 None 语义、同诊断）。"""
    return {k: eval_when(v, ctx) for k, v in params.items()}


def _quantize_for_dedup(action: str, params: dict) -> dict:
    """去重键用的参数量化（T4）：POINT/POINTS 坐标 round 到整格，使动态点（如组心）微移
    <0.5 格不触发重发、跨整格才重发。只服务于去重键；下发的 Operation.params 保留精确值。"""
    point_params = {n for n, t, _ in OP_CATALOG.get(action, [])
                    if t in (ParamType.POINT, ParamType.POINTS)}
    if not point_params:
        return params
    out = dict(params)
    for name in point_params:
        if name in out:
            out[name] = _round_coords(out[name])
    return out


def _round_coords(v):
    """POINT/POINTS 值的坐标量化：[x,y]→[round,round]；[[x,y],...]→逐点；Point2→[round,round]；
    非点值（字符串/None/解析失败残留）原样返回（json default=str 兜底）。"""
    if isinstance(v, (list, tuple)):
        if len(v) >= 2 and isinstance(v[0], (int, float)) and isinstance(v[1], (int, float)):
            return [round(float(v[0])), round(float(v[1]))]  # POINT [x, y]
        return [_round_coords(p) for p in v]  # POINTS [[x,y], ...]（或混合）
    if hasattr(v, "x") and hasattr(v, "y"):
        return [round(float(v.x)), round(float(v.y))]  # Point2 兜底
    return v
