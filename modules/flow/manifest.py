"""flow manifest：Strategy/Flow.md 解析 + 最小校验。

V1 用结构化 AST（op/args dict）作 `when`/`do`，直接 YAML 解析（无表达式解析器），
对应 spec-004 的 FlowIR JSON AST。step/branch/edge/do 保留为 dict（runtime 直读）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from game import is_known_action


@dataclass
class StrategyManifest:
    id: str
    version: int
    group_slots: list[str]
    params: dict  # name -> {type, default, live_editable}
    variables: dict
    initial_step: str
    steps: dict[str, dict]  # step_id -> {branches: [...]}
    edges: list[dict]
    on_exit: str
    loop_limits: dict


@dataclass
class GroupSpec:
    group_id: str
    composition: dict  # type -> {min, target, max}


@dataclass
class StrategyInstance:
    instance_id: str
    strategy_ref: str
    bindings: dict  # slot -> group_id
    params: dict


@dataclass
class FlowAssembly:
    id: str
    groups: list[GroupSpec]
    strategy_instances: list[StrategyInstance]
    production_sequence: list = field(default_factory=list)  # V1：flow 不管，queue 另跑


@dataclass(slots=True)
class ActionRequest:
    """flow→engine 的 group 级动作（engine 展开 (group_slot,stable_type)→unit_tags 成 Operation）。"""

    group_slot: str  # 槽位名（strategy group_slots 中声明的，如 "main"；bindings[slot]→group_id）
    stable_type: str  # 兵种类型（如 "terran/marine"；Allocator.expand(group_id, type) 取 unit_tags）
    action_atom: str  # 操作原子名（OP_CATALOG 中，如 "move_to"/"focus_fire"；编译期校验）
    params: dict  # 参数（透传到 Operation.params）


def parse_strategy(yaml_str: str) -> StrategyManifest:
    d = yaml.safe_load(yaml_str)
    steps = {s["step_id"]: s for s in d["steps"]}
    m = StrategyManifest(
        id=d["id"], version=d.get("version", 1), group_slots=d["group_slots"],
        params=d.get("params", {}), variables=d.get("variables", {}),
        initial_step=d["initial_step"], steps=steps, edges=d.get("edges", []),
        on_exit=d.get("on_exit", "keep_idle"), loop_limits=d.get("loop_limits", {}),
    )
    validate_strategy(m)
    return m


def parse_assembly(yaml_str: str) -> FlowAssembly:
    d = yaml.safe_load(yaml_str)
    groups = [GroupSpec(g["group_id"], g["composition"]) for g in d.get("groups", [])]
    insts = [
        StrategyInstance(si["instance_id"], si["strategy_ref"], si["bindings"], si.get("params", {}))
        for si in d.get("strategy_instances", [])
    ]
    return FlowAssembly(d["id"], groups, insts, d.get("production_sequence", []))


def validate_strategy(m: StrategyManifest) -> None:
    """最小编译期校验（spec-003/004 的子集；其余后补）。"""
    assert m.initial_step in m.steps, f"initial_step {m.initial_step!r} 不在 steps"
    for e in m.edges:
        assert e["from"] in m.steps, f"edge from {e['from']!r} 不是 step"
        assert e["to"] in m.steps, f"edge to {e['to']!r} 不是 step"
    for sid, step in m.steps.items():
        for b in step.get("branches", []):
            for a in b.get("do", []):
                if a.get("op") == "exit_step":
                    k, r = a.get("kind"), a.get("reason")
                    assert any(
                        e["from"] == sid and e["kind"] == k and e["reason"] == r for e in m.edges
                    ), f"exit_step {k}/{r} in {sid} 无匹配 edge"
                if a.get("op") == "group_action":
                    atom = a.get("action_atom")
                    assert is_known_action(atom), f"unknown action_atom {atom!r} in {sid}"
