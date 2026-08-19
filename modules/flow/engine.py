"""flow engine：RuntimeSink；per-frame eval Step 分支 → ActionRequest → 去重 → 展开 → Operation。

driver→world→flow：engine.on_game_state(GameState) → eval active step → 命中 do →
group_action 经去重 + Allocator.expand 成 Operation → port.submit_operations。
exit_step 按边路由（本帧结束，下帧求值新 step）；exit_strategy 结束。
动作去重（spec-003 §2.1）：相同 (slot,type,action_atom,params) 不重发。
port 用 duck-typing（任何有 submit_operations 的对象）；flow 不 import driver。
"""
from __future__ import annotations

import json

from game import GameState, Operation

from flow.allocator import Allocator
from flow.manifest import FlowAssembly, StrategyManifest
from flow.predicates import EvalCtx, eval_when


class FlowEngine:
    def __init__(self, manifest: StrategyManifest, assembly: FlowAssembly, port, registry=None) -> None:
        self._m = manifest
        self._port = port
        self._alloc = Allocator()
        self._registry = registry
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
        self._strategy_start: float | None = None
        self._step_entered: float | None = None
        self._last_emitted: dict[tuple, str] = {}  # (slot, type, atom) -> params_key
        self._done = False
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
                      self._strategy_start, self._step_entered, self._registry)
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
                return
            elif op == "set_variable":
                self._variables[a["name"]] = _eval_value(a.get("value"), ctx)
            # set_local / start_timer / stop_timer：V1 stub

    def _emit_group_action(self, a: dict, ctx: EvalCtx, gs: GameState) -> None:
        slot = a["group_slot"]
        type_name = a["type"]
        atom = a["action_atom"]
        params = _resolve_params(a.get("params", {}), ctx)
        pkey = json.dumps(params, sort_keys=True, default=str)
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
        # 本帧结束（不本帧求值新 step；下帧求值）


def _eval_value(v, ctx: EvalCtx):
    if isinstance(v, dict):
        if "const" in v:
            return v["const"]
        if "param" in v:
            return ctx.params.get(v["param"])
    return v


def _resolve_params(params: dict, ctx: EvalCtx) -> dict:
    return {k: _eval_value(v, ctx) for k, v in params.items()}
