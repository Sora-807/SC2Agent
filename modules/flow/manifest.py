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
from game.operation import COMPOSITE_ACTIONS, OP_CATALOG, ParamType, is_composite_action

from flow.predicates import (
    OPERATOR_ARITY,
    PREDICATE_SIGNATURES,
    SPATIAL_NAME_PARAMS,
    UNIMPLEMENTED_PREDICATE_OPS,
    UNIMPLEMENTED_SPATIAL_OPS,
)


@dataclass
class StrategyManifest:
    id: str
    version: int
    group_slots: list[str]
    params: dict  # name -> {type, default, description_zh?}（键白名单见 PARAM_KEYS）
    variables: dict
    initial_step: str
    steps: dict[str, dict]  # step_id -> {branches, display_name_zh?, description_zh?}
    edges: list[dict]
    loop_limits: dict
    definitions: dict = field(default_factory=dict)  # 别名节（T2b）：name -> 值树；when/params 用 {ref: name}
    # I2：人类可读名与意图描述 —— 策略级（"" = 没写，UI 退回 identifier）
    display_name_zh: str = ""
    description_zh: str = ""
    # I2：reason 标识符 → 中文（edges 的切换原因 / exit 的终局原因共用一张表）
    reasons: dict = field(default_factory=dict)


@dataclass
class GroupSpec:
    group_id: str
    composition: dict  # type -> {min, target, max}
    display_name_zh: str = ""  # I4：组名中文（如 G_INF → 步兵组）


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


# loop_limits 允许的键（有界环兜底；值必须正整数）
LOOP_LIMIT_KEYS = frozenset({"max_step_transitions"})

# do 操作词表（spec-003 §5 可写操作 + group_action）
# 二十六轮（T8 落地）：set_local/start_timer/stop_timer 从"未实现"转正 ——
# 读侧（{local} 节点、timer_elapsed 谓词）同批落地，不再有"写允许读拒绝"的静默无效。
DO_OPS = frozenset({
    "group_action", "exit_step", "exit_strategy", "set_variable",
    "set_local", "start_timer", "stop_timer",
})

# 词表里有、仍未实现的 do 操作 → 原因（D8：写被允许而读被拒 = 静默无效，两边一起拒）
UNIMPLEMENTED_DO_OPS: dict[str, str] = {}

# step 声明里同样"声明了但没有消费方"的键（与 UNIMPLEMENTED_DO_OPS 对称）
UNIMPLEMENTED_STEP_KEYS: dict[str, str] = {}

# params 声明允许的键与类型白名单（live_editable 等无消费方的键先不收）。
# description_zh（I4）：参数的一句话说明，UI 的 ? 帮助与 tooltip 读它。
PARAM_KEYS = frozenset({"type", "default", "description_zh"})
PARAM_TYPES = frozenset({"int", "float", "point", "bool", "str"})

# step / branch 键白名单（F3）：拼错 branches 会让这个 step 每帧什么都不做（静默死锁）；
# 拼错 when 会让条件被丢掉、分支变成无条件执行（静默灾难）。两者编译期都要拦。
# display_name_zh/description_zh（I2）：step 的人类可读名与意图描述，随 static/strategy 下发。
# locals（二十六轮 T8）：step 局部变量名声明（set_local 写 / {local} 读，换 step 清空）。
STEP_KEYS = frozenset({"step_id", "branches", "display_name_zh", "description_zh", "locals"})
# branch_id 是可选的分支稳定标识（B1 观测/读模型用；不写就按 index 定位）
BRANCH_KEYS = frozenset({"when", "do", "branch_id"})

# strategy / assembly 顶层键白名单：删掉一个字段（如 on_exit）后，旧文件继续写它必须**报错**，
# 不能静默忽略 —— 否则"删字段"就变成了"悄悄失效"（D5 的反面）。
# display_name_zh/description_zh/reasons（I2）：可读名、意图描述、reason 中文创。
STRATEGY_KEYS = frozenset({
    "id", "version", "group_slots", "params", "variables", "definitions",
    "initial_step", "steps", "edges", "loop_limits",
    "display_name_zh", "description_zh", "reasons",
})
ASSEMBLY_KEYS = frozenset({"id", "groups", "strategy_instances", "production_sequence"})

# 已删除字段 → 提示（写了就报错，并说明去哪了）
REMOVED_KEYS: dict[str, str] = {
    "on_exit": "单实例引擎下 release 与 keep_idle 无可观察差异，已删（D5）；"
               "多实例/hot-swap 轮需要时按 spec-002 恢复",
}


def _check_top_level_keys(d: dict, allowed: frozenset, what: str) -> None:
    """顶层键白名单（不静默）：未知键报错；已删字段给"去哪了"的提示。"""
    unknown = sorted(set(d) - allowed)
    if not unknown:
        return
    msgs = [
        f"{k}（{REMOVED_KEYS[k]}）" if k in REMOVED_KEYS else repr(k)
        for k in unknown
    ]
    raise AssertionError(
        f"{what} 顶层未知键: " + "、".join(msgs) + f"（允许：{sorted(allowed)}）"
    )


def parse_strategy(yaml_str: str) -> StrategyManifest:
    d = yaml.safe_load(yaml_str)
    _check_top_level_keys(d, STRATEGY_KEYS, "strategy")
    raw_steps = d["steps"]
    steps = {s["step_id"]: s for s in raw_steps}
    if len(steps) != len(raw_steps):  # dict 覆盖会静默丢 step（T2c #5）
        seen: set = set()
        dupes = sorted({s["step_id"] for s in raw_steps if s["step_id"] in seen or seen.add(s["step_id"])})
        raise AssertionError(f"strategy 编译校验失败:\n- 重复的 step_id {dupes}（后一份会静默覆盖前一份）")
    m = StrategyManifest(
        id=d["id"], version=d.get("version", 1), group_slots=d["group_slots"],
        params=d.get("params", {}), variables=d.get("variables", {}),
        initial_step=d["initial_step"], steps=steps, edges=d.get("edges", []),
        loop_limits=d.get("loop_limits", {}),
        definitions=d.get("definitions", {}) or {},
        display_name_zh=d.get("display_name_zh", "") or "",
        description_zh=d.get("description_zh", "") or "",
        reasons=d.get("reasons", {}) or {},
    )
    validate_strategy(m)
    return m


def parse_assembly(yaml_str: str) -> FlowAssembly:
    d = yaml.safe_load(yaml_str)
    _check_top_level_keys(d, ASSEMBLY_KEYS, "assembly")
    groups = [GroupSpec(g["group_id"], g["composition"], g.get("display_name_zh", "") or "")
              for g in d.get("groups", [])]
    insts = [
        StrategyInstance(si["instance_id"], si["strategy_ref"], si["bindings"], si.get("params", {}))
        for si in d.get("strategy_instances", [])
    ]
    return FlowAssembly(d["id"], groups, insts, d.get("production_sequence", []))


def _check_identifier(value, where: str, field: str, err) -> bool:
    """标识符字段必须是字符串（H3）。返回 True = 合法。

    YAML 陷阱实测：on/off/yes/no/true/false → bool；~ → None；12:30 → 750（六十进制）；
    1_000 → 1000。这些会变成"看着像名字、其实不是字符串"的值，
    最典型的是 reason: NO → False 与 edges 侧的 "NO" 不相等 → 报"无匹配 edge"，把人指向错误方向。
    """
    if isinstance(value, str):
        return True
    if isinstance(value, bool):
        err(f"{where}: {field} 的值 {value!r} 被 YAML 解析成布尔（on/off/yes/no/true/false），请加引号")
    else:
        err(f"{where}: {field} 必须是字符串，当前 {value!r}（{type(value).__name__}）")
    return False


def _validate_value_node(node, where: str, m: StrategyManifest, err, seen_refs: tuple = (),
                         locals_names: frozenset | None = None) -> None:
    """递归校验值树（when 条件 / 动作参数 / definitions / set_* 的 value 共用一套）。

    查：节点词表、谓词命名参数（缺失/多余/改用 args）、运算符 arity、param/var/ref 引用存在性、
    definitions 自引用与环。`locals_names`：当前 step 声明的局部变量名（None = definitions
    上下文，{local} 不允许 —— 别名是跨 step 替换，捕获局部变量会变成暗渠）。
    """
    if isinstance(node, (list, tuple)):
        for item in node:
            _validate_value_node(item, where, m, err, seen_refs, locals_names)
        return
    if not isinstance(node, dict):
        return  # 字面量（数字/字符串/布尔）
    if "const" in node:
        return
    if "param" in node:
        if node["param"] not in m.params:
            err(f"{where}: 引用未声明的参数 {node['param']!r}（已声明：{sorted(m.params)}）")
        return
    if "var" in node:
        if node["var"] not in m.variables:
            err(f"{where}: 引用未声明的变量 {node['var']!r}（已声明：{sorted(m.variables)}）")
        return
    if "local" in node:
        name = node["local"]
        if locals_names is None:
            err(f"{where}: {{local: {name!r}}} 不能出现在 definitions —— 局部变量是 step 作用域"
                "（别名跨 step 替换，捕获局部会变暗渠）")
        elif name not in locals_names:
            err(f"{where}: 引用未声明的局部变量 {name!r}（本 step 的 locals：{sorted(locals_names)}）")
        return
    if "ref" in node:
        name = node["ref"]
        if name not in m.definitions:
            err(f"{where}: 引用未声明的 definitions 别名 {name!r}（已声明：{sorted(m.definitions)}）")
            return
        if name in seen_refs:
            err(f"{where}: definitions 自引用/环 {list(seen_refs) + [name]}（ref 是纯替换，环会无限展开）")
            return
        _validate_value_node(m.definitions[name], f"definitions[{name!r}]", m, err,
                             seen_refs + (name,))
        return
    op = node.get("op")
    if op is None:
        err(f"{where}: 节点缺 op: {node!r}")
        return
    if not _check_identifier(op, where, "op", err):
        return
    if op in UNIMPLEMENTED_PREDICATE_OPS:
        err(f"{where}: 谓词 {op!r} 未实现（{UNIMPLEMENTED_PREDICATE_OPS[op]}）")
        return
    if op in UNIMPLEMENTED_SPATIAL_OPS:
        err(f"{where}: 空间工具 {op!r} 未实现（{UNIMPLEMENTED_SPATIAL_OPS[op]}）")
        return
    if op in OPERATOR_ARITY:
        lo, hi = OPERATOR_ARITY[op]
        args = node.get("args")
        if not isinstance(args, list):
            err(f"{where}: 运算符 {op!r} 需要 args 列表（运算符是前缀序，保留 args）")
            return
        if len(args) < lo or (hi is not None and len(args) > hi):
            want = f"{lo}" if hi == lo else (f"≥{lo}" if hi is None else f"{lo}~{hi}")
            err(f"{where}: 运算符 {op!r} 需要 {want} 个操作数，当前 {len(args)} 个")
        extra = sorted(k for k in node if k not in ("op", "args"))
        if extra:
            err(f"{where}: 运算符 {op!r} 只接受 args，多了 {extra}")
        for a in args:
            _validate_value_node(a, where, m, err, seen_refs, locals_names)
        return
    if op in PREDICATE_SIGNATURES:
        sig = PREDICATE_SIGNATURES[op]
        names = [n for n, _ in sig]
        shown = ", ".join(f"{n}{'' if req else '?'}" for n, req in sig) or "(无参数)"
        if "args" in node:
            # 只报这一条（不再叠加"缺参数/多参数"噪声）：这是迁移写法错误，不是参数错误
            err(f"{where}: {op!r} 改用命名参数（{shown}），不再接受 args"
                "（T2/D2：位置参数写错顺序编译期抓不到）")
            for a in node.get("args") or []:
                _validate_value_node(a, where, m, err, seen_refs, locals_names)
            return
        missing = [n for n, req in sig if req and n not in node]
        if missing:
            err(f"{where}: {op!r} 缺必需参数 {missing}（签名：{shown}）")
        unknown = sorted(k for k in node if k != "op" and k not in names)
        if unknown:
            err(f"{where}: {op!r} 不认识参数 {unknown}（签名：{shown}）")
        for n in names:
            if n in node:
                _validate_value_node(node[n], f"{where}/{op}.{n}", m, err, seen_refs, locals_names)
        return
    err(f"{where}: 未知谓词 {op!r}（词表：{sorted(PREDICATE_SIGNATURES)}）")


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


def _check_param_value(value, ptype, where: str, err) -> None:
    """按声明 type 校验参数值（F4 / T2c #10）。ptype 为 None（未声明类型）时跳过。"""
    if ptype is None or value is None:
        return
    if ptype == "point":
        ok = (isinstance(value, (list, tuple)) and len(value) == 2
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))
        if not ok:
            err(f"{where}: 声明 type=point，值应为 [x, y] 两个数，当前 {value!r}")
    elif ptype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            err(f"{where}: 声明 type=int，当前 {value!r}（{type(value).__name__}）")
    elif ptype == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            err(f"{where}: 声明 type=float，当前 {value!r}（{type(value).__name__}）")
    elif ptype == "bool":
        if not isinstance(value, bool):
            err(f"{where}: 声明 type=bool，当前 {value!r}（{type(value).__name__}）")
    elif ptype == "str":
        if not isinstance(value, str):
            err(f"{where}: 声明 type=str，当前 {value!r}（{type(value).__name__}）")


def iter_group_actions(m: StrategyManifest):
    """遍历所有 group_action do 项 → (where, action)。校验与读模型共用。"""
    for sid, step in m.steps.items():
        for i, b in enumerate(step.get("branches", [])):
            for a in (b.get("do") or []):
                if isinstance(a, dict) and a.get("op") == "group_action":
                    yield f"{sid}/branch[{i}]", a


def iter_value_nodes(m: StrategyManifest):
    """遍历所有值树里的 op 节点 → (where, node)：when / 动作参数 / definitions / set_* 的 value。"""
    def walk(node, where):
        if isinstance(node, (list, tuple)):
            for item in node:
                yield from walk(item, where)
            return
        if not isinstance(node, dict):
            return
        if node.get("op"):
            yield where, node
        for key, val in node.items():
            if key != "op":
                yield from walk(val, where)

    for dname, dnode in (m.definitions or {}).items():
        yield from walk(dnode, f"definitions[{dname!r}]")
    for sid, step in m.steps.items():
        for i, b in enumerate(step.get("branches", [])):
            where = f"{sid}/branch[{i}]"
            if "when" in b:
                yield from walk(b["when"], where)
            for a in (b.get("do") or []):
                if not isinstance(a, dict):
                    continue
                for pval in (a.get("params") or {}).values():
                    yield from walk(pval, where)
                if "value" in a:
                    yield from walk(a["value"], where)


def validate_map_names(m: StrategyManifest, layer) -> list[str]:
    """字面量地图名字校验（F5）：策略里写死的点位名/区域名必须在 RegionLayer 里存在。

    引擎构造期就拿到了 layer，所以这本来就该是编译期错误（R6：提交必须 validate）——
    此前拼错的名字会一路静默：resolver 原样保留 → driver 静默失败，或谓词恒 False。
    返回问题清单（空 = 通过）；layer 为 None 时跳过（离线单测/无地图场景）。
    """
    if layer is None:
        return []
    problems: list[str] = []

    def check(name, kind: str, where: str) -> None:
        if not isinstance(name, str):
            return  # 字面坐标/引用节点：不是名字
        if kind == "region":
            if name not in layer.regions and name not in layer.big_regions:
                problems.append(
                    f"{where}: 区域名 {name!r} 不在地图层的 regions/big_regions"
                    f"（点位名当区域名用会静默恒 False）"
                )
        elif layer.anchor(name) is None:
            problems.append(f"{where}: 地图名 {name!r} 在地图层里解析不到（区域锚点/大区/点位标记都没有）")

    for where, action in iter_group_actions(m):
        atom = action.get("action_atom")
        params = action.get("params") or {}
        for pname, ptype, _required in OP_CATALOG.get(atom, []):
            if ptype not in (ParamType.POINT, ParamType.POINTS) or pname not in params:
                continue
            val = params[pname]
            items = val if (ptype == ParamType.POINTS and isinstance(val, list)) else [val]
            for item in items:
                check(item, "point", f"{where}/{atom}.{pname}")

    for where, node in iter_value_nodes(m):
        for pname, kind in SPATIAL_NAME_PARAMS.get(node.get("op"), {}).items():
            if pname in node:
                check(node[pname], kind, f"{where}/{node['op']}.{pname}")
    return problems


def validate_strategy(m: StrategyManifest) -> None:
    """编译期校验（spec-003 验收点 1-5 + spec-004 子集；其余后补）。

    失败抛 AssertionError（parse_strategy 入口调用）；错误信息带 step/branch 定位。
    """
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    if m.initial_step not in m.steps:
        err(f"initial_step {m.initial_step!r} 不在 steps")

    # I2 可读性字段：必须是字符串（YAML 可能把裸词解析成别的类型；"" = 没写，合法）
    for field_name in ("display_name_zh", "description_zh"):
        val = getattr(m, field_name)
        if not isinstance(val, str):
            err(f"{field_name} 必须是字符串，当前 {val!r}（{type(val).__name__}）")
    for rname, rzh in (m.reasons or {}).items():
        if not isinstance(rname, str) or not isinstance(rzh, str):
            err(f"reasons.{rname!r}: 键与值都必须是字符串（reason 标识符 → 中文），当前 {rzh!r}")

    for sid, step in m.steps.items():
        for field_name in ("display_name_zh", "description_zh"):
            if field_name in step and not isinstance(step[field_name], str):
                err(f"step {sid}: {field_name} 必须是字符串，当前 {step[field_name]!r}")

    for sid in m.steps:
        _check_identifier(sid, "steps", "step_id", err)

    # params 声明（T2c #9）：键白名单 + type 白名单（live_editable 等无消费方的键先不收）
    for pname, spec in (m.params or {}).items():
        if not isinstance(spec, dict):
            err(f"params.{pname}: 声明必须是 mapping（如 {{type: int, default: 1}}），当前 {spec!r}")
            continue
        unknown = sorted(set(spec) - PARAM_KEYS)
        if unknown:
            err(f"params.{pname}: 未知键 {unknown}（只允许 {sorted(PARAM_KEYS)}）")
        ptype = spec.get("type")
        if ptype is not None and ptype not in PARAM_TYPES:
            err(f"params.{pname}: 未知 type {ptype!r}（白名单 {sorted(PARAM_TYPES)}）")
        elif "default" in spec:
            _check_param_value(spec["default"], ptype, f"params.{pname}.default", err)

    # variables 声明：与 params 同一套形态校验（此前只查 params，不一致）
    for vname, spec in (m.variables or {}).items():
        if not isinstance(spec, dict):
            err(f"variables.{vname}: 声明必须是 mapping（如 {{type: int, default: 0}}），当前 {spec!r}")
            continue
        unknown = sorted(set(spec) - PARAM_KEYS)
        if unknown:
            err(f"variables.{vname}: 未知键 {unknown}（只允许 {sorted(PARAM_KEYS)}）")
        vtype = spec.get("type")
        if vtype is not None and vtype not in PARAM_TYPES:
            err(f"variables.{vname}: 未知 type {vtype!r}（白名单 {sorted(PARAM_TYPES)}）")
        elif "default" in spec:
            _check_param_value(spec["default"], vtype, f"variables.{vname}.default", err)

    # definitions 别名节（T2b）：值树同 when 词表，且不得自引用/成环
    for dname, dnode in (m.definitions or {}).items():
        _check_identifier(dname, "definitions", "别名名字", err)
        _validate_value_node(dnode, f"definitions[{dname!r}]", m, err, (dname,))

    seen_edges: set[tuple] = set()
    for e in m.edges:
        for endpoint in ("from", "to"):
            if e[endpoint] not in m.steps:
                err(f"edge {e['from']}→{e['to']} 的 {endpoint} {e[endpoint]!r} 不是 step")
        for field_name in ("kind", "reason"):
            _check_identifier(e.get(field_name), f"edge {e['from']}→{e['to']}", field_name, err)
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
        # F3：step 键白名单 —— 拼错 branches（branchs）会让这个 step 每帧什么都不做，永远
        for key, reason in UNIMPLEMENTED_STEP_KEYS.items():
            if key in step:
                err(f"step {sid}: {key} 未实现（{reason}）")
        unknown_step_keys = sorted(set(step) - STEP_KEYS - set(UNIMPLEMENTED_STEP_KEYS))
        if unknown_step_keys:
            err(f"step {sid}: 未知键 {unknown_step_keys}（只允许 {sorted(STEP_KEYS)}；"
                "拼错 branches 会让这个 step 每帧什么都不做）")
        # locals 声明（二十六轮 T8）：名字必须是字符串列表 —— set_local/{local} 都对着它查
        step_locals_raw = step.get("locals")
        if step_locals_raw is not None and (
                not isinstance(step_locals_raw, list)
                or not all(isinstance(n, str) for n in step_locals_raw)):
            err(f"step {sid}: locals 必须是字符串列表（当前 {step_locals_raw!r}）")
            step_locals = frozenset()  # 形态非法：按空集继续（上面已报错，不让下游再炸）
        else:
            step_locals = frozenset(step_locals_raw or ())
        branches = step.get("branches", [])
        for i, b in enumerate(branches):
            where = f"{sid}/branch[{i}]"
            # F3：branch 键白名单 —— 拼错 when（wehn）会让条件被丢掉、分支变无条件执行
            unknown_branch_keys = sorted(set(b) - BRANCH_KEYS)
            if unknown_branch_keys:
                err(f"{where}: 未知键 {unknown_branch_keys}（只允许 {sorted(BRANCH_KEYS)}；"
                    "拼错 when 会让条件被丢掉、这条分支变成无条件执行）")
            if "branch_id" in b:
                _check_identifier(b["branch_id"], where, "branch_id", err)
            if "when" not in b and i != len(branches) - 1:
                err(f"{where}: else（无 when）分支必须且只能放在最后（spec-003 §2）")
            if "when" in b:
                _validate_value_node(b["when"], where, m, err, locals_names=step_locals)
            exited = False
            for a in b.get("do", []):
                op = a.get("op")
                if op in UNIMPLEMENTED_DO_OPS:
                    err(f"{where}: do 操作 {op!r} 未实现（{UNIMPLEMENTED_DO_OPS[op]}）")
                    continue
                if op not in DO_OPS:
                    err(f"{where}: 未知 do 操作 {op!r}（词表：{sorted(DO_OPS)}）")
                    continue
                if exited:
                    err(f"{where}: exit 之后不得再有 do 项（{op}）（spec-003 §3.1）")
                if op == "exit_step":
                    exited = True
                    k, r = a.get("kind"), a.get("reason")
                    _check_identifier(k, where, "exit_step.kind", err)
                    _check_identifier(r, where, "exit_step.reason", err)
                    if not any(
                        e["from"] == sid and e["kind"] == k and e["reason"] == r for e in m.edges
                    ):
                        err(f"{where}: exit_step {k}/{r} 无匹配 edge（spec-003 验收 #3）")
                if op == "exit_strategy":
                    exited = True
                    _check_identifier(a.get("kind"), where, "exit_strategy.kind", err)
                    _check_identifier(a.get("reason"), where, "exit_strategy.reason", err)
                if op == "group_action":
                    slot = a.get("group_slot")
                    if slot not in m.group_slots:
                        err(f"{where}: 未声明的 group_slot {slot!r}（声明：{m.group_slots}）")
                    stable_type = a.get("type")
                    if stable_type is None:
                        err(f"{where}: group_action 缺 type（stable id，如 terran/marine）")
                    elif _check_identifier(stable_type, where, "group_action.type", err):
                        if stable_type.count("/") != 1:
                            err(f"{where}: group_action.type {stable_type!r} 不是两段式 stable id"
                                "（race/name，如 terran/marine；T1 起 burnysc2 名不再接受）")
                    atom = a.get("action_atom")
                    if not is_known_action(atom):
                        err(f"{where}: 未知 action_atom {atom!r}")
                    elif is_composite_action(atom):
                        # flow 直接发它 → driver 的 translate_op 返回 [] → 静默 no-op
                        err(f"{where}: action_atom {atom!r} 是复合意图，flow 不能直接发"
                            f"（{COMPOSITE_ACTIONS[atom]}）")
                    else:
                        for pname, _ptype, required in OP_CATALOG[atom]:
                            if required and pname not in (a.get("params") or {}):
                                err(f"{where}: {atom} 缺必需参数 {pname!r}（OP_CATALOG）")
                    # 动作参数值树：此前只查 when，param/var/ref 写错在运行期才炸（T2c #3）
                    for pname, pval in (a.get("params") or {}).items():
                        _validate_value_node(pval, f"{where}/{atom}.params.{pname}", m, err,
                                             locals_names=step_locals)
                if op == "set_variable":
                    if a.get("name") not in m.variables:
                        err(f"{where}: set_variable 写未声明的变量 {a.get('name')!r}（声明：{sorted(m.variables)}）")
                    _validate_value_node(a.get("value"), f"{where}/set_variable.value", m, err,
                                         locals_names=step_locals)
                if op == "set_local":
                    # 二十六轮 T8：局部变量必须先在 step 的 locals 里声明（拼错名 = 永远读不到）
                    if a.get("name") not in step_locals:
                        err(f"{where}: set_local 写未声明的局部变量 {a.get('name')!r}"
                            f"（本 step 的 locals：{sorted(step_locals)}）")
                    _validate_value_node(a.get("value"), f"{where}/set_local.value", m, err,
                                         locals_names=step_locals)
                if op in ("start_timer", "stop_timer"):
                    # 计时器名是动态创建的（无声明表），只查字符串（YAML bool 陷阱同 H3）
                    _check_identifier(a.get("name"), where, f"{op}.name", err)


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
    if len(a.strategy_instances) > 1:
        # V1 单实例：引擎只取 instances[0]，多写的会被静默忽略（T2c #6）
        errors.append(
            f"assembly 有 {len(a.strategy_instances)} 个 strategy_instances，V1 只支持 1 个"
            "（引擎只取第一个，其余会被静默忽略）"
        )
    group_ids = {g.group_id for g in a.groups}
    for g in a.groups:
        for stable_id, spec in (g.composition or {}).items():
            if not isinstance(stable_id, str) or stable_id.count("/") != 1:
                errors.append(
                    f"group {g.group_id}: composition 键 {stable_id!r} 不是两段式 stable id（race/name）"
                )
            if not isinstance(spec, dict):
                errors.append(f"group {g.group_id}/{stable_id}: composition 值必须是 {{min,target,max}}")
                continue
            nums = {}
            for key in ("min", "target", "max"):
                v = spec.get(key)
                if v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    errors.append(f"group {g.group_id}/{stable_id}: {key} 必须是非负整数，当前 {v!r}")
                else:
                    nums[key] = v
            lo, tgt, hi = nums.get("min"), nums.get("target"), nums.get("max")
            if lo is not None and tgt is not None and lo > tgt:
                errors.append(f"group {g.group_id}/{stable_id}: 要求 min ≤ target（当前 {lo} > {tgt}）")
            if tgt is not None and hi is not None and tgt > hi:
                errors.append(f"group {g.group_id}/{stable_id}: 要求 target ≤ max（当前 {tgt} > {hi}）")
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

        # F5-1：声明的 slot 必须都有绑定 —— 否则该 slot 的所有 group_action 永久静默 no-op
        unbound = [s for s in m.group_slots if s not in si.bindings]
        if unbound:
            errors.append(
                f"instance {si.instance_id}: 声明的 group_slot {unbound} 没有绑定 group"
                "（这些 slot 的所有动作会永久静默 no-op）"
            )

        # F5-2：(slot, type) 必须在绑定组的 composition 里 —— 否则 expand 恒为空，永久 no-op
        comp_by_gid = {g.group_id: set(g.composition or {}) for g in a.groups}
        for where, action in iter_group_actions(m):
            slot = action.get("group_slot")
            stable_type = action.get("type")
            gid = si.bindings.get(slot)
            if gid is None or stable_type is None or gid not in comp_by_gid:
                continue  # 上面已分别报错
            if stable_type not in comp_by_gid[gid]:
                errors.append(
                    f"{where}: group_action({slot}, {stable_type}) —— 绑定的 group {gid} 的 composition"
                    f"里没有这个兵种（有：{sorted(comp_by_gid[gid])}），动作会永久静默 no-op"
                )

        # F4：instance params 键必须已声明（拼错会静默用 default），并按声明 type 校验值
        def _err(msg: str) -> None:
            errors.append(msg)

        for pname, pval in (si.params or {}).items():
            if pname not in m.params:
                errors.append(
                    f"instance {si.instance_id}: params 里的 {pname!r} 不是 strategy 声明的参数"
                    f"（声明：{sorted(m.params)}）—— 覆盖会被静默忽略"
                )
                continue
            _check_param_value(pval, (m.params[pname] or {}).get("type"),
                               f"instance {si.instance_id}: params.{pname}", _err)
    if errors:
        raise AssertionError("assembly 编译校验失败:\n- " + "\n- ".join(errors))
