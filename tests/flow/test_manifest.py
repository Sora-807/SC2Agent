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

