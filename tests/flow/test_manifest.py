"""manifest 编译期校验：词表/结构/引用（spec-003 验收点 1-5 的代码化）。"""
import pytest

from flow.manifest import parse_assembly, parse_strategy, validate_assembly

VALID = """
id: v
group_slots: [main]
params: {p1: {type: int, default: 1}}
variables: {v1: {type: int, default: 0}}
initial_step: s1
steps:
  - step_id: s1
    branches:
      # 环出口（ADR-0021 §4）：s1→s1 是自环，必须有 exit_strategy 或出环 edge 才能编译通过
      - when: {op: ">=", args: [{op: strategy_elapsed}, 600]}
        do: [{op: exit_strategy, kind: failed, reason: TIMEOUT}]
      - when: {op: ">=", args: [{op: group_count, args: [main]}, {param: p1}]}
        do: [{op: exit_step, kind: done, reason: OK}]
      - do: []
edges:
  - {from: s1, to: s1, kind: done, reason: OK}
"""


def test_valid_strategy_parses():
    m = parse_strategy(VALID)
    assert m.id == "v" and m.initial_step == "s1"


def test_else_must_be_last():
    bad = VALID.replace(
        "      - when: {op: \">=\", args: [{op: group_count, args: [main]}, {param: p1}]}\n        do: [{op: exit_step, kind: done, reason: OK}]\n      - do: []",
        "      - do: []\n      - when: {op: \">=\", args: [{op: group_count, args: [main]}, {param: p1}]}\n        do: [{op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="最后"):
        parse_strategy(bad)


def test_unknown_do_op_rejected():
    bad = VALID.replace("{op: exit_step, kind: done, reason: OK}", "{op: fly}")
    with pytest.raises(AssertionError, match="未知 do"):
        parse_strategy(bad)


def test_unknown_predicate_rejected():
    bad = VALID.replace("{op: group_count, args: [main]}", "{op: fly_pred, args: []}")
    with pytest.raises(AssertionError, match="未知谓词"):
        parse_strategy(bad)


def test_unimplemented_predicate_rejected_at_compile():
    bad = VALID.replace("{op: group_count, args: [main]}", "{op: engaged, args: [main]}")
    with pytest.raises(AssertionError, match="未实现"):
        parse_strategy(bad)


def test_undeclared_slot_rejected():
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: group_action, group_slot: other, type: MARINE, action_atom: move_to, params: {position: [1, 1]}}, {op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="group_slot"):
        parse_strategy(bad)


def test_group_action_missing_required_param_rejected():
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {}}, {op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="position"):
        parse_strategy(bad)


def test_action_after_exit_rejected():
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: exit_strategy, kind: done, reason: SAFE}, {op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {position: [1, 1]}}]",
    )
    with pytest.raises(AssertionError, match="exit 之后"):
        parse_strategy(bad)


def test_duplicate_edge_rejected():
    bad = VALID + "  - {from: s1, to: s1, kind: done, reason: OK}\n"
    with pytest.raises(AssertionError, match="重复 edge"):
        parse_strategy(bad)


def test_undeclared_variable_write_rejected():
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: set_variable, name: nope, value: {const: 1}}, {op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="变量"):
        parse_strategy(bad)


def test_undeclared_local_write_rejected():
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: set_local, name: nope, value: {const: 1}}, {op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="local"):
        parse_strategy(bad)


def test_undeclared_param_ref_rejected():
    bad = VALID.replace("{param: p1}", "{param: nope}")
    with pytest.raises(AssertionError, match="参数"):
        parse_strategy(bad)


def test_undeclared_var_ref_rejected():
    bad = VALID.replace("{param: p1}", "{var: nope}")
    with pytest.raises(AssertionError, match="变量"):
        parse_strategy(bad)


def test_edge_endpoints_checked():
    bad = VALID.replace("edges:", "edges:").replace("{from: s1, to: s1, kind: done, reason: OK}", "{from: s1, to: nope, kind: done, reason: OK}")
    with pytest.raises(AssertionError, match="不是 step"):
        parse_strategy(bad)


# ---- 图级校验（ADR-0021 §4 + 验收 #3）----

CYCLE_WITH_EDGE_OUT = """
id: cyc
group_slots: [main]
params: {}
variables: {}
initial_step: s1
steps:
  - step_id: s1
    branches:
      - when: {op: ">=", args: [{op: game_time}, 10]}
        do: [{op: exit_step, kind: done, reason: NEXT}]
      - when: {op: ">=", args: [{op: game_time}, 5]}
        do: [{op: exit_step, kind: failed, reason: BAIL}]
      - do: []
  - step_id: s2
    branches:
      - do: [{op: exit_step, kind: done, reason: BACK}]
  - step_id: hold
    branches:
      - do: []
edges:
  - {from: s1, to: s2, kind: done, reason: NEXT}
  - {from: s2, to: s1, kind: done, reason: BACK}
  - {from: s1, to: hold, kind: failed, reason: BAIL}
loop_limits: {max_step_transitions: 10}
"""


def test_unreachable_step_rejected():
    """声明了却从 initial_step 无边路径可达的 step = 死配置（红线"不静默"）→ 编译失败。"""
    bad = VALID.replace("edges:", "  - step_id: island\n    branches:\n      - do: []\nedges:")
    with pytest.raises(AssertionError, match="不可达"):
        parse_strategy(bad)


def test_cycle_without_exit_rejected():
    """环内既无出环 edge 也无 exit_strategy → 拒绝（ADR-0021 反例：无出口的环）。"""
    bad = VALID.replace(
        '      - when: {op: ">=", args: [{op: strategy_elapsed}, 600]}\n'
        "        do: [{op: exit_strategy, kind: failed, reason: TIMEOUT}]\n",
        "",
    )
    with pytest.raises(AssertionError, match="无出口"):
        parse_strategy(bad)


def test_cycle_with_exit_strategy_accepted():
    """环的出口可以是 exit_strategy，不必是出环 edge。

    docs/tank_marine_push.yaml 的蛙跳环没有任何出边，只靠 exit_strategy ARRIVED 出环；
    若把"必须有出环 edge"当规则，会误杀真机验过的样例（见 test_tank_marine_push）。
    """
    m = parse_strategy(VALID)  # s1→s1 自环 + exit_strategy TIMEOUT
    assert m.initial_step == "s1"


def test_cycle_with_outgoing_edge_accepted():
    """环的出口也可以是指向环外的 edge（环内无 exit_strategy 也合法）。"""
    m = parse_strategy(CYCLE_WITH_EDGE_OUT)
    assert set(m.steps) == {"s1", "s2", "hold"}


def test_loop_limits_unknown_key_rejected():
    with pytest.raises(AssertionError, match="loop_limits"):
        parse_strategy(VALID + "loop_limits: {max_iterations: 5}\n")


def test_loop_limits_bool_value_rejected():
    """YAML 把 on/yes 解析成 bool（bool 是 int 子类，会绕过 isinstance(int) 检查）→ 显式拒绝。"""
    with pytest.raises(AssertionError, match="正整数"):
        parse_strategy(VALID + "loop_limits: {max_step_transitions: on}\n")


def test_loop_limits_non_positive_rejected():
    with pytest.raises(AssertionError, match="正整数"):
        parse_strategy(VALID + "loop_limits: {max_step_transitions: 0}\n")


DEAD_EDGE_LAUNDERING = """
id: launder
group_slots: [main]
params: {}
variables: {}
initial_step: s1
steps:
  - step_id: s1
    branches:
      - do: [{op: exit_step, kind: done, reason: OK}]
  - step_id: hold
    branches:
      - do: []
edges:
  - {from: s1, to: s1, kind: done, reason: OK}
  - {from: s1, to: hold, kind: failed, reason: NEVER}
loop_limits: {max_step_transitions: 5}
"""


def test_dead_edge_rejected():
    """没有任何 exit_step 会走的 edge = 死配置 → 编译失败。"""
    with pytest.raises(AssertionError, match="死 edge"):
        parse_strategy(VALID + "  - {from: s1, to: s1, kind: failed, reason: NEVER}\n")


def test_dead_edge_cannot_launder_exitless_cycle():
    """死边不能给无出口的环洗白：s1 自环无 exit_strategy，出环边是死边 → 整份仍被拒。

    环出口判定只看"有出环 edge"时，一条死边就能假冒出口；死边校验与环校验在同一遍里，
    任一报错即编译失败，所以这条洗白路径不成立。
    """
    with pytest.raises(AssertionError, match="死 edge"):
        parse_strategy(DEAD_EDGE_LAUNDERING)


# ---- assembly 绑定校验 ----


def _assembly(strategy_ref="v", bindings="{main: G1}"):
    return parse_assembly(f"""
id: a
groups:
  - group_id: G1
    composition:
      MARINE: {{min: 1, target: 1, max: 1}}
strategy_instances:
  - instance_id: s1
    strategy_ref: {strategy_ref}
    bindings: {bindings}
    params: {{}}
""")


def test_assembly_valid():
    validate_assembly(parse_strategy(VALID), _assembly())  # 不抛 = 通过


def test_assembly_wrong_strategy_ref():
    with pytest.raises(AssertionError, match="strategy_ref"):
        validate_assembly(parse_strategy(VALID), _assembly(strategy_ref="other"))


def test_assembly_undeclared_slot_binding():
    with pytest.raises(AssertionError, match="slot"):
        validate_assembly(parse_strategy(VALID), _assembly(bindings="{nope: G1}"))


def test_assembly_missing_group():
    with pytest.raises(AssertionError, match="group"):
        validate_assembly(parse_strategy(VALID), _assembly(bindings="{main: G99}"))

