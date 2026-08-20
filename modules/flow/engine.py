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

from game import GameState, Operation
from game.operation import OP_CATALOG, ParamType

from flow.allocator import Allocator
from flow.manifest import FlowAssembly, StrategyManifest, validate_assembly
from flow.predicates import EvalCtx, eval_when
from tactical_map.resolver import resolve_action_params

# 全局转移上限（ADR-0021 §4 第 3 条）：strategy 未声明 loop_limits.max_step_transitions 时兜底，
# 保证"没有任何配置能让引擎无限转移 step"。声明值优先（编译期已校验为正整数）。
DEFAULT_MAX_STEP_TRANSITIONS = 200


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
        self._m = manifest
        self._port = port
        self._alloc = Allocator(catalog=catalog)  # catalog 透传给 Allocator（形态变体归一化，T3）
        self._region_layer = region_layer  # 区域模型（map 名→坐标，ADR-0029）
        self._catalog = catalog  # 透传给 EvalCtx（谓词层归一化，T3）
        for g in assembly.groups:
            self._alloc.create_group(g.group_id, g.composition)
        si = assembly.strategy_instances[0]
        self._bindings = si.bindings  # slot -> group_id
        self._params = {p: spec.get("default") for p, spec in manifest.params.items()}
        self._params.update(si.params)
        self._active_step = manifest.initial_step
        self._step_entry_count = 1
        self._step_transition_count = 0
        self._variables = {v: spec.get("default") for v, spec in manifest.variables.items()}
        self._locals: dict = {}  # 进入 step 时重置（spec-003 §3.2）
        self._strategy_start: float | None = None
        self._step_entered: float | None = None
        self._last_emitted: dict[tuple, str] = {}  # (slot, type, atom) -> params_key
        self._done = False
        self.exit_record: dict | None = None  # 结束原因（exit_strategy 的 kind/reason，或 LOOP_LIMIT）
        # 求值期诊断（H6）：(step, kind, detail) -> 次数。None 比较等"降级为 False"的路径留痕，
        # 不静默；UI/agent/真机日志读它就知道"条件其实没求出来"。
        self.eval_diagnostics: dict[tuple, int] = {}
        self._op_seq = 0

    # ---- RuntimeSink ----
    def on_game_state(self, gs: GameState) -> None:
        if self._done:
            return
        if self._strategy_start is None:
            self._strategy_start = gs.game_time
        if self._step_entered is None:
            self._step_entered = gs.game_time
        self._alloc.refresh(gs)
        ctx = EvalCtx(gs, self._alloc, self._bindings, self._params, self._variables,
                      self._strategy_start, self._step_entered, self._region_layer,
                      catalog=self._catalog, definitions=self._m.definitions,
                      step_id=self._active_step, diagnostics=self.eval_diagnostics)
        step = self._m.steps[self._active_step]
        for b in step.get("branches", []):
            when = b.get("when")
            if when is None or eval_when(when, ctx):  # else（无 when）或 true
                self._exec_do(b.get("do", []), ctx, gs)
                return  # 首条命中，本帧结束

    def on_session_event(self, event) -> None:
        pass

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
            elif op in ("start_timer", "stop_timer"):
                pass  # V1 stub：timer_elapsed 谓词未实现前，计时器写操作无害空转
            else:
                raise ValueError(f"unknown do op {op!r}")  # 编译期已拦；此为兜底

    def _emit_group_action(self, a: dict, ctx: EvalCtx, gs: GameState) -> None:
        slot = a["group_slot"]
        type_name = a["type"]
        atom = a["action_atom"]
        params = _resolve_params(a.get("params", {}), ctx)
        # ADR-0029 D1：emit 前把 map 名解析成数值（去重也在解析后，spec-003 §2.1）
        params = resolve_action_params(atom, params, self._region_layer)
        # T4 去重量化：POINT/POINTS 参数 round 到整格进键（动态点微移 <0.5 不重发、≥1 才重发）；
        # 实际下发的 params 保留精确值（仅 pkey 量化）。
        pkey = json.dumps(_quantize_for_dedup(atom, params), sort_keys=True, default=str)
        if self._last_emitted.get((slot, type_name, atom)) == pkey:
            return  # 去重：同 (slot,type,action,params) 不重发
        self._last_emitted[(slot, type_name, atom)] = pkey
        gid = self._bindings.get(slot)
        tags = self._alloc.expand(gid, type_name) if gid else []
        if not tags:
            return  # 空 group no-op
        self._op_seq += 1
        self._port.submit_operations([Operation(
            op_id=self._op_seq, unit_tags=tags, action=atom, params=params, seq=gs.seq,
        )])

    def _do_exit_step(self, a: dict, gs: GameState) -> None:
        k, r = a.get("kind"), a.get("reason")
        edge = next((e for e in self._m.edges
                     if e["from"] == self._active_step and e["kind"] == k and e["reason"] == r), None)
        if edge is None:
            return  # validate 应拦
        self._active_step = edge["to"]
        self._step_entry_count += 1
        self._step_transition_count += 1
        self._step_entered = gs.game_time
        self._locals = {}  # 进入新 step：locals 重置（spec-003 §3.2）
        limit = self._m.loop_limits.get("max_step_transitions", DEFAULT_MAX_STEP_TRANSITIONS)
        if self._step_transition_count > limit:
            # 有界环兜底：转移上限（ADR-0021 验收 #4 —— 以 LOOP_LIMIT 失败，不静默停）
            self._done = True
            self.exit_record = {"kind": "failed", "reason": "LOOP_LIMIT", "limit": limit}
        # 本帧结束（不本帧求值新 step；下帧求值）


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
