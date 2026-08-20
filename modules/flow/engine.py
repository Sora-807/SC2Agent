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


class FlowEngine:
    def __init__(self, manifest: StrategyManifest, assembly: FlowAssembly, port,
                 region_layer=None, catalog=None) -> None:
        if catalog is None:
            raise ValueError(
                "FlowEngine 需要 catalog（game.catalog.load_terran()）：flow authoring 只用 stable id"
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
        self._alloc = Allocator(catalog=catalog)  # catalog 透传给 Allocator（形态变体归一化，T3）
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
                      step_id=self._active_step, diagnostics=self.eval_diagnostics)
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
                return  # 首条命中，本帧结束

    def on_session_event(self, event) -> None:
        pass

    # ---- 读模型（B1）----
    def snapshot(self) -> dict:
        """当前运行时状态的显式只读快照（供 view / agent / 复盘录制）。

        为什么必须有这个方法而不是让外部读 `_` 字段：引擎离三族全量还有很长的路，
        如果 UI 直接依赖私有字段，之后任何重构都会打断 UI。这里是唯一的观测出口。

        返回**普通 dict**（不是 view 的 dataclass）：flow 不认识 view，架构测试锁死这个方向。
        键名与 `docs/plan-frontend.md` §2 的 StrategyView 对齐，由 view.adapt 显式映射。
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
            "（契约 frame/flow.groups；见 docs/plan-backend-view.md B1）"
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
