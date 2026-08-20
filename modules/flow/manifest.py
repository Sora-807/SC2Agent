"""flow.manifest：Strategy/Flow.md 解析 + 编译期校验（spec-003 验收点 1-5 子集）。

V1 用结构化 AST（op/args dict）作 when/do，直接 YAML 解析（无表达式解析器），
对应 spec-004 的 FlowIR JSON AST。step/branch/edge/do 保留为 dict（runtime 直读）。

编译期校验（validate_strategy / validate_assembly）：
- else（无 when）分支必须且只能放在最后
- do 操作词表：未知 op / 未实现谓词 / 未知谓词 / 未声明 slot / 缺必需参数 / 未声明变量写入
  / exit 之后再有 do 项 —— 全部编译失败（R6；spec-003 验收 #5）
- edge：端点存在、不重复、exit_step 有匹配边
- 图级（ADR-0021 §4 + 验收 #3）：不可达 step 拒绝；环必须有出口（出环 edge 或 exit_strategy）
- loop_limits：键白名单 + 正整数（缺省时引擎用全局默认上限兜底，见 engine.DEFAULT_MAX_STEP_TRANSITIONS）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from game import is_known_action
from game.operation import OP_CATALOG

from flow.predicates import (
    COMPARISON_OPS,
    KNOWN_PREDICATE_OPS,
    KNOWN_SPATIAL_OPS,
    LOGIC_OPS,
    UNIMPLEMENTED_PREDICATE_OPS,
    UNIMPLEMENTED_SPATIAL_OPS,
)


@dataclass
class StrategyManifest:
    id: str
    version: int
    group_slots: list[str]
    params: dict  # name -> {type, default, live_editable}
    variables: dict
    initial_step: str
    steps: dict[str, dict]  # step_id -> {branches: [...], locals?: [...]}
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


# loop_limits 允许的键（有界环兜底；值必须正整数）
LOOP_LIMIT_KEYS = frozenset({"max_step_transitions"})

# do 操作词表（spec-003 §5 可写操作 + group_action）
DO_OPS = frozenset({
    "group_action", "exit_step", "exit_strategy",
    "set_variable", "set_local", "start_timer", "stop_timer",
})


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


def _validate_when_node(node, where: str, m: StrategyManifest, err) -> None:
    """递归校验 when AST：节点词表 + param/var 引用存在性。"""
    if not isinstance(node, dict):
        return  # 字面量（数字/坐标列表/字符串）
    if "const" in node:
        return
    if "param" in node:
        if node["param"] not in m.params:
            err(f"{where}: 引用未声明的参数 {node['param']!r}")
        return
    if "var" in node:
        if node["var"] not in m.variables:
            err(f"{where}: 引用未声明的变量 {node['var']!r}")
        return
    op = node.get("op")
    if op is None:
        err(f"{where}: 条件节点缺 op: {node!r}")
        return
    if op in UNIMPLEMENTED_PREDICATE_OPS:
        err(f"{where}: 谓词 {op!r} 未实现（{UNIMPLEMENTED_PREDICATE_OPS[op]}）")
    elif op in UNIMPLEMENTED_SPATIAL_OPS:
        err(f"{where}: 空间工具 {op!r} 未实现（{UNIMPLEMENTED_SPATIAL_OPS[op]}）")
    elif op in KNOWN_SPATIAL_OPS:
        pass  # 已实现空间值工具（point_toward）：args 递归校验（下方 for 循环）
    elif op not in KNOWN_PREDICATE_OPS | COMPARISON_OPS | LOGIC_OPS:
        err(f"{where}: 未知谓词 {op!r}")
    for a in node.get("args", []):
        _validate_when_node(a, where, m, err)


def _step_successors(m: StrategyManifest) -> dict[str, set[str]]:
    """step 邻接表（只收两端都是合法 step 的 edge；端点错误由 edge 校验单独报）。"""
    adj: dict[str, set[str]] = {sid: set() for sid in m.steps}
    for e in m.edges:
        f, t = e.get("from"), e.get("to")
        if f in adj and t in adj:
            adj[f].add(t)
    return adj


def _reach(adj: dict[str, set[str]], start: str) -> set[str]:
    """从 start 沿**至少一条边**可达的 step 集合（start 只在有回路时才在结果内）。"""
    seen: set[str] = set()
    stack = list(adj.get(start, ()))
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, ()))
    return seen


def _has_exit_strategy(step: dict) -> bool:
    """step 的任一分支是否含 exit_strategy（= 该 step 能直接终止 strategy）。"""
    return any(
        (a or {}).get("op") == "exit_strategy"
        for b in step.get("branches", [])
        for a in (b.get("do") or [])
    )


def _validate_graph(m: StrategyManifest, err) -> None:
    """图级校验（ADR-0021 §4 + 验收 #3）。

    1. **不可达 step**：非 initial_step 且从 initial_step 无边路径 → 编译失败。
       声明了却永不执行的 step 是死配置（需求文档红线"不静默"），LLM 生成时高频产孤岛。
    2. **环必须有出口**：每个环（SCC，含自环）至少要有一个出口 ——
       指向环外的 edge，**或**环内任一 step 的 exit_strategy。
       注意 docs/tank_marine_push.yaml 的蛙跳环没有任何出边，靠 tank_hop/inf_hop 的
       exit_strategy ARRIVED 出环；只查出边会误杀真机验过的样例。
       loop_limits.max_step_transitions 是**意外死循环的兜底，不算合法出口**（ADR-0021 反例）。
    """
    if m.initial_step not in m.steps:
        return  # initial_step 非法（上游已报错），可达性无从谈起
    adj = _step_successors(m)
    reachable = {m.initial_step} | _reach(adj, m.initial_step)
    for sid in m.steps:
        if sid not in reachable:
            err(f"step {sid!r} 不可达：从 initial_step {m.initial_step!r} 无边路径（死配置）")

    reach_of = {sid: _reach(adj, sid) for sid in m.steps}
    cyclic = {sid for sid in m.steps if sid in reach_of[sid]}  # 能回到自身 = 在环上
    grouped: set[str] = set()
    for sid in sorted(cyclic):
        if sid in grouped:
            continue
        scc = {o for o in cyclic if o in reach_of[sid] and sid in reach_of[o]} | {sid}
        grouped |= scc
        has_edge_out = any(t not in scc for u in scc for t in adj[u])
        has_exit_strategy = any(_has_exit_strategy(m.steps[u]) for u in scc)
        if not has_edge_out and not has_exit_strategy:
            err(
                f"环 {sorted(scc)} 无出口：既无指向环外的 edge，也无 exit_strategy"
                "（ADR-0021 §4/验收 #3；loop_limits 是兜底不是出口）"
            )


def validate_strategy(m: StrategyManifest) -> None:
    """编译期校验（spec-003 验收点 1-5 + spec-004 子集；其余后补）。

    失败抛 AssertionError（parse_strategy 入口调用）；错误信息带 step/branch 定位。
    """
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    if m.initial_step not in m.steps:
        err(f"initial_step {m.initial_step!r} 不在 steps")

    seen_edges: set[tuple] = set()
    for e in m.edges:
        for endpoint in ("from", "to"):
            if e[endpoint] not in m.steps:
                err(f"edge {e['from']}→{e['to']} 的 {endpoint} {e[endpoint]!r} 不是 step")
        key = (e["from"], e.get("kind"), e.get("reason"))
        if key in seen_edges:
            err(f"重复 edge {key}（同 (from,kind,reason) 路由歧义）")
        seen_edges.add(key)
        # 死边：没有任何 exit_step 会走这条边（引擎只从 exit_step 路由）。
        # 除了"死配置不静默"，_validate_graph 的环出口判定也依赖它 ——
        # 否则一条死边就能假冒"出环 edge"，让无出口的环通过校验。
        src = m.steps.get(e["from"])
        if src is not None and not any(
            (a or {}).get("op") == "exit_step"
            and a.get("kind") == e.get("kind")
            and a.get("reason") == e.get("reason")
            for b in src.get("branches", [])
            for a in (b.get("do") or [])
        ):
            err(
                f"死 edge {e['from']}→{e['to']} {e.get('kind')}/{e.get('reason')}："
                f"{e['from']} 没有任何 exit_step 产生这个 kind/reason"
            )

    for sid, step in m.steps.items():
        branches = step.get("branches", [])
        locals_declared = set(step.get("locals") or [])
        for i, b in enumerate(branches):
            where = f"{sid}/branch[{i}]"
            if "when" not in b and i != len(branches) - 1:
                err(f"{where}: else（无 when）分支必须且只能放在最后（spec-003 §2）")
            if "when" in b:
                _validate_when_node(b["when"], where, m, err)
            exited = False
            for a in b.get("do", []):
                op = a.get("op")
                if op not in DO_OPS:
                    err(f"{where}: 未知 do 操作 {op!r}（词表：{sorted(DO_OPS)}）")
                    continue
                if exited:
                    err(f"{where}: exit 之后不得再有 do 项（{op}）（spec-003 §3.1）")
                if op == "exit_step":
                    exited = True
                    k, r = a.get("kind"), a.get("reason")
                    if not any(
                        e["from"] == sid and e["kind"] == k and e["reason"] == r for e in m.edges
                    ):
                        err(f"{where}: exit_step {k}/{r} 无匹配 edge（spec-003 验收 #3）")
                if op == "exit_strategy":
                    exited = True
                if op == "group_action":
                    slot = a.get("group_slot")
                    if slot not in m.group_slots:
                        err(f"{where}: 未声明的 group_slot {slot!r}（声明：{m.group_slots}）")
                    atom = a.get("action_atom")
                    if not is_known_action(atom):
                        err(f"{where}: 未知 action_atom {atom!r}")
                    else:
                        for pname, _ptype, required in OP_CATALOG[atom]:
                            if required and pname not in (a.get("params") or {}):
                                err(f"{where}: {atom} 缺必需参数 {pname!r}（OP_CATALOG）")
                if op == "set_variable" and a.get("name") not in m.variables:
                    err(f"{where}: set_variable 写未声明的变量 {a.get('name')!r}（声明：{sorted(m.variables)}）")
                if op == "set_local" and a.get("name") not in locals_declared:
                    err(f"{where}: set_local 写未声明的 local {a.get('name')!r}（本 step 声明：{sorted(locals_declared)}）")

    for lk, lv in (m.loop_limits or {}).items():
        if lk not in LOOP_LIMIT_KEYS:
            err(f"loop_limits 未知键 {lk!r}（只允许 {sorted(LOOP_LIMIT_KEYS)}）")
        elif isinstance(lv, bool) or not isinstance(lv, int) or lv <= 0:
            err(f"loop_limits.{lk} 必须是正整数（当前 {lv!r}；YAML 的 on/off/yes/no 会解析成 bool）")

    _validate_graph(m, err)

    if errors:
        raise AssertionError("strategy 编译校验失败:\n- " + "\n- ".join(errors))


def validate_assembly(m: StrategyManifest, a: FlowAssembly) -> None:
    """assembly ↔ strategy 绑定校验（engine 构造时调用，R6）。"""
    errors: list[str] = []
    if not a.strategy_instances:
        errors.append("assembly 缺 strategy_instances")
    group_ids = {g.group_id for g in a.groups}
    for si in a.strategy_instances:
        if si.strategy_ref != m.id:
            errors.append(
                f"instance {si.instance_id} 的 strategy_ref {si.strategy_ref!r} != 加载的 strategy {m.id!r}"
            )
        for slot, gid in si.bindings.items():
            if slot not in m.group_slots:
                errors.append(f"instance {si.instance_id}: 绑定未声明的 slot {slot!r}")
            if gid not in group_ids:
                errors.append(f"instance {si.instance_id}: 绑定不存在的 group {gid!r}")
    if errors:
        raise AssertionError("assembly 编译校验失败:\n- " + "\n- ".join(errors))
