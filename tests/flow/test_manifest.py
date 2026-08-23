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
      - when: {op: ">=", args: [{op: group_count, group: main}, {param: p1}]}
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
        "      - when: {op: \">=\", args: [{op: group_count, group: main}, {param: p1}]}\n        do: [{op: exit_step, kind: done, reason: OK}]\n      - do: []",
        "      - do: []\n      - when: {op: \">=\", args: [{op: group_count, group: main}, {param: p1}]}\n        do: [{op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="最后"):
        parse_strategy(bad)


def test_unknown_do_op_rejected():
    bad = VALID.replace("{op: exit_step, kind: done, reason: OK}", "{op: fly}")
    with pytest.raises(AssertionError, match="未知 do"):
        parse_strategy(bad)


def test_unknown_predicate_rejected():
    bad = VALID.replace("{op: group_count, group: main}", "{op: fly_pred, args: []}")
    with pytest.raises(AssertionError, match="未知谓词"):
        parse_strategy(bad)


def test_unimplemented_predicate_rejected_at_compile():
    # engaged/under_attack/timer_elapsed 已落地（二十六轮）；仍待建的是事件/基地族
    bad = VALID.replace("{op: group_count, group: main}", "{op: has_ready_base, args: [main]}")
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


def test_set_local_needs_declared_local():
    """二十六轮 T8：set_local 转正 —— 但名字必须在 step 的 locals 里声明（拼错 = 永远读不到）。"""
    bad = VALID.replace(
        "do: [{op: exit_step, kind: done, reason: OK}]",
        "do: [{op: set_local, name: nope, value: {const: 1}}, {op: exit_step, kind: done, reason: OK}]",
    )
    with pytest.raises(AssertionError, match="未声明的局部变量"):
        parse_strategy(bad)


def test_local_read_needs_declaration_too():
    """{local: x} 读侧同查：没声明的名字编译期拒绝（写读两侧一起放行的 D8 完整版）。"""
    bad = VALID.replace("{op: group_count, group: main}", "{local: nope}")
    with pytest.raises(AssertionError, match="未声明的局部变量"):
        parse_strategy(bad)


def test_local_not_allowed_in_definitions():
    bad = VALID.replace(
        "variables: {v1: {type: int, default: 0}}",
        "variables: {v1: {type: int, default: 0}}\ndefinitions: {d1: {local: v1}}",
    )
    with pytest.raises(AssertionError, match="definitions"):
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


# ---- T2：命名参数 / arity / definitions / 声明白名单（编译期）----


def test_predicate_positional_args_rejected_with_migration_hint():
    """D2：谓词改命名参数 —— 旧的位置 args 写法编译失败，且只报"改用命名参数"这一条。"""
    bad = VALID.replace("{op: group_count, group: main}", "{op: group_count, args: [main]}")
    with pytest.raises(AssertionError, match="命名参数") as e:
        parse_strategy(bad)
    assert "缺必需参数" not in str(e.value)  # 迁移错误不叠加参数噪声


def test_predicate_missing_required_named_param_rejected():
    bad = VALID.replace("{op: group_count, group: main}", "{op: group_count}")
    with pytest.raises(AssertionError, match="缺必需参数"):
        parse_strategy(bad)


def test_predicate_unknown_named_param_rejected():
    """参数名写错（arrived 的 radius 写成 dist 之类）编译失败 —— 位置参数时代抓不到这类错。"""
    bad = VALID.replace("{op: group_count, group: main}", "{op: group_count, grp: main}")
    with pytest.raises(AssertionError, match="不认识参数"):
        parse_strategy(bad)


def test_operator_arity_checked():
    """J2：运算符 arity 进签名表 —— and 单参 / not 双参此前都静默通过。"""
    single_and = VALID.replace('{op: ">=", args: [{op: group_count, group: main}, {param: p1}]}',
                               '{op: and, args: [{op: group_count, group: main}]}')
    with pytest.raises(AssertionError, match="操作数"):
        parse_strategy(single_and)
    two_not = VALID.replace('{op: ">=", args: [{op: group_count, group: main}, {param: p1}]}',
                            '{op: not, args: [{op: group_count, group: main}, {param: p1}]}')
    with pytest.raises(AssertionError, match="操作数"):
        parse_strategy(two_not)


def test_operator_rejects_extra_named_keys():
    bad = VALID.replace('{op: ">=", args: [{op: group_count, group: main}, {param: p1}]}',
                        '{op: ">=", args: [{op: group_count, group: main}, {param: p1}], group: main}')
    with pytest.raises(AssertionError, match="只接受 args"):
        parse_strategy(bad)


DEFS = """
id: d
group_slots: [inf, armor]
params: {target: {type: point, default: [10, 10]}, cover: {type: float, default: 4.0}}
variables: {}
definitions:
  front: {op: point_toward, origin: {op: group_center, group: armor}, toward: {param: target}, dist: {param: cover}}
initial_step: go
steps:
  - step_id: go
    branches:
      - when: {op: arrived, group: inf, target: {ref: front}, radius: 2.0}
        do: [{op: exit_strategy, kind: done, reason: THERE}]
      - do:
          - {op: group_action, group_slot: inf, type: terran/marine, action_atom: attack_move_to, params: {position: {ref: front}}}
edges: []
"""


def test_definitions_alias_accepted_in_when_and_params():
    """D3：definitions 别名节消灭重复表达式；when 与动作参数都能 {ref: name}。"""
    m = parse_strategy(DEFS)
    assert set(m.definitions) == {"front"}


def test_unknown_ref_rejected():
    bad = DEFS.replace("{ref: front}", "{ref: nope}")
    with pytest.raises(AssertionError, match="definitions 别名"):
        parse_strategy(bad)


def test_definitions_self_reference_rejected():
    """ref 是纯替换语义，自引用/环会无限展开 → 编译期拒绝。"""
    bad = DEFS.replace(
        "  front: {op: point_toward, origin: {op: group_center, group: armor}, toward: {param: target}, dist: {param: cover}}",
        "  front: {op: arrived, group: inf, target: {ref: front}, radius: 1.0}")
    with pytest.raises(AssertionError, match="自引用/环"):
        parse_strategy(bad)


def test_do_params_references_validated():
    """T2c #3：动作参数里的 {param}/{var}/{ref} 此前不查，写错要到运行期才发现。"""
    bad = DEFS.replace("params: {position: {ref: front}}", "params: {position: {param: nope}}")
    with pytest.raises(AssertionError, match="参数"):
        parse_strategy(bad)


def test_yaml_bool_trap_on_identifier_fields():
    """H3：YAML 把 on/off/yes/no 解析成布尔 —— 标识符字段必须是字符串。"""
    bad = VALID.replace("reason: OK", "reason: NO")
    with pytest.raises(AssertionError, match="布尔"):
        parse_strategy(bad)


def test_group_action_requires_two_segment_stable_id():
    bad = VALID.replace(
        "      - do: []",
        "      - do: [{op: group_action, group_slot: main, type: MARINE,"
        " action_atom: move_to, params: {position: [1, 1]}}]")
    with pytest.raises(AssertionError, match="两段式 stable id"):
        parse_strategy(bad)


def test_group_action_missing_type_rejected():
    bad = VALID.replace(
        "      - do: []",
        "      - do: [{op: group_action, group_slot: main,"
        " action_atom: move_to, params: {position: [1, 1]}}]")
    with pytest.raises(AssertionError, match="缺 type"):
        parse_strategy(bad)


def test_timer_do_ops_and_local_roundtrip_parse():
    """二十六轮 T8：start/stop_timer + timer_elapsed + locals/{local} 全链可用（不再对称拒绝）。"""
    parse_strategy(VALID
                   .replace("  - step_id: s1", "  - step_id: s1\n    locals: [burst]")
                   .replace("      - do: []",
                            "      - do: [{op: start_timer, name: t1},"
                            " {op: set_local, name: burst, value: {const: 1}}]"))
    # timer_elapsed 谓词读侧也要能进 when
    m2 = parse_strategy(VALID.replace(
        "{op: group_count, group: main}",
        '{op: ">=", args: [{op: timer_elapsed, name: t1}, {const: 30}]}'))
    assert m2.steps["s1"] is not None


def test_duplicate_step_id_rejected():
    """T2c #5：parse 时 dict 覆盖会静默丢掉前一份。"""
    bad = VALID.replace("edges:", "  - step_id: s1\n    branches:\n      - do: []\nedges:")
    with pytest.raises(AssertionError, match="重复的 step_id"):
        parse_strategy(bad)


def test_param_declaration_keys_and_type_whitelist():
    with pytest.raises(AssertionError, match="未知键"):
        parse_strategy(VALID.replace("{type: int, default: 1}", "{type: int, default: 1, live_editable: true}"))
    with pytest.raises(AssertionError, match="未知 type"):
        parse_strategy(VALID.replace("{type: int, default: 1}", "{type: unit, default: 1}"))


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

    docs/data/tank_marine_push.yaml 的蛙跳环没有任何出边，只靠 exit_strategy ARRIVED 出环；
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
      terran/marine: {{min: 1, target: 1, max: 1}}
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



def test_assembly_rejects_multiple_instances():
    """T2c #6：V1 单实例 —— 引擎只取 instances[0]，多写的会被静默忽略。"""
    two = _assembly().strategy_instances
    from flow.manifest import FlowAssembly, GroupSpec, StrategyInstance
    a = FlowAssembly(
        id="a",
        groups=[GroupSpec("G1", {"terran/marine": {"min": 1, "target": 1, "max": 1}})],
        strategy_instances=[two[0], StrategyInstance("s2", "v", {"main": "G1"}, {})],
    )
    with pytest.raises(AssertionError, match="只支持 1 个"):
        validate_assembly(parse_strategy(VALID), a)


def test_assembly_composition_bounds_checked():
    """T2c #7 / D6 前置：min ≤ target ≤ max，且非负整数。"""
    bad_order = _assembly_text("{min: 3, target: 1, max: 4}")
    with pytest.raises(AssertionError, match="min ≤ target"):
        validate_assembly(parse_strategy(VALID), parse_assembly(bad_order))
    bad_max = _assembly_text("{min: 1, target: 4, max: 2}")
    with pytest.raises(AssertionError, match="target ≤ max"):
        validate_assembly(parse_strategy(VALID), parse_assembly(bad_max))
    bad_neg = _assembly_text("{min: -1, target: 1, max: 2}")
    with pytest.raises(AssertionError, match="非负整数"):
        validate_assembly(parse_strategy(VALID), parse_assembly(bad_neg))


def _assembly_text(comp: str):
    return f"""
id: a
groups:
  - group_id: G1
    composition:
      terran/marine: {comp}
strategy_instances:
  - instance_id: s1
    strategy_ref: v
    bindings: {{main: G1}}
    params: {{}}
"""



def test_removed_on_exit_key_says_where_it_went():
    """T3/D5：删掉的字段必须留"墓碑" —— 旧文件继续写 on_exit 会报错并说明去哪了，
    而不是被静默忽略（否则"删字段"等于"悄悄失效"）。"""
    with pytest.raises(AssertionError, match="on_exit") as e:
        parse_strategy(VALID + "on_exit: release\n")
    assert "D5" in str(e.value) or "已删" in str(e.value)


def test_unknown_top_level_key_rejected():
    with pytest.raises(AssertionError, match="顶层未知键"):
        parse_strategy(VALID + "whatever: 1\n")


def test_assembly_unknown_top_level_key_rejected():
    with pytest.raises(AssertionError, match="顶层未知键"):
        parse_assembly(_assembly_text("{min: 1, target: 1, max: 1}") + "on_exit: release\n")



# ---- F3/F4/F5：键名打错 / 声明类型 / 绑定与兵种（编译期）----


def test_step_key_typo_rejected():
    """F3：branchs 拼错 → 旧行为是编译通过、该 step 每帧什么都不做（永远）。"""
    bad = VALID.replace("    branches:", "    branchs:")
    with pytest.raises(AssertionError, match="未知键"):
        parse_strategy(bad)


def test_branch_key_typo_rejected():
    """F3：wehn 拼错 → 旧行为是条件被丢掉、这条分支变成无条件执行。"""
    bad = VALID.replace('      - when: {op: ">=", args: [{op: strategy_elapsed}, 600]}',
                        '      - wehn: {op: ">=", args: [{op: strategy_elapsed}, 600]}')
    with pytest.raises(AssertionError, match="未知键"):
        parse_strategy(bad)


def test_locals_declaration_typechecked():
    """二十六轮 T8：locals 转正（字符串列表）；非列表/非字符串仍是编译错误。"""
    parse_strategy(VALID.replace("  - step_id: s1", "  - step_id: s1\n    locals: [x]"))
    with pytest.raises(AssertionError, match="locals 必须是字符串列表"):
        parse_strategy(VALID.replace("  - step_id: s1", "  - step_id: s1\n    locals: 42"))


def test_variables_declaration_validated_like_params():
    with pytest.raises(AssertionError, match="未知键"):
        parse_strategy(VALID.replace("{type: int, default: 0}", "{type: int, default: 0, hot: true}"))
    with pytest.raises(AssertionError, match="未知 type"):
        parse_strategy(VALID.replace("{type: int, default: 0}", "{type: unit, default: 0}"))


def test_declared_default_type_checked():
    """声明 type=int 却给了字符串 → 编译失败（F4/T2c #10）。"""
    with pytest.raises(AssertionError, match="type=int"):
        parse_strategy(VALID.replace("{type: int, default: 1}", '{type: int, default: "x"}'))


def test_point_default_must_be_xy_pair():
    with pytest.raises(AssertionError, match="type=point"):
        parse_strategy(VALID.replace("{type: int, default: 1}", "{type: point, default: [1, 2, 3]}"))


def test_assembly_instance_param_typo_rejected():
    """F4：实例 params 键拼错 → 旧行为是校验通过、静默用 default。"""
    bad = _assembly_text("{min: 1, target: 1, max: 1}").replace("params: {}", "params: {p2: 5}")
    with pytest.raises(AssertionError, match="不是 strategy 声明的参数"):
        validate_assembly(parse_strategy(VALID), parse_assembly(bad))


def test_assembly_instance_param_type_checked():
    bad = _assembly_text("{min: 1, target: 1, max: 1}").replace("params: {}", 'params: {p1: "x"}')
    with pytest.raises(AssertionError, match="type=int"):
        validate_assembly(parse_strategy(VALID), parse_assembly(bad))


TWO_SLOT = """
id: two
group_slots: [inf, armor]
params: {}
variables: {}
initial_step: go
steps:
  - step_id: go
    branches:
      - do:
          - {op: group_action, group_slot: inf, type: terran/marine, action_atom: move_to, params: {position: [1, 1]}}
          - {op: group_action, group_slot: armor, type: terran/siegetank, action_atom: move_to, params: {position: [1, 1]}}
edges: []
"""


def _two_slot_assembly(bindings: str, comp: str = "terran/marine: {min: 1, target: 1, max: 1}"):
    return f"""
id: a
groups:
  - group_id: G1
    composition:
      {comp}
strategy_instances:
  - instance_id: s1
    strategy_ref: two
    bindings: {bindings}
    params: {{}}
"""


def test_assembly_unbound_slot_rejected():
    """F5-1：声明了 armor 却不绑定 → 旧行为是 armor 的所有动作永久静默 no-op。"""
    with pytest.raises(AssertionError, match="没有绑定 group"):
        validate_assembly(parse_strategy(TWO_SLOT), parse_assembly(_two_slot_assembly("{inf: G1}")))


def test_group_action_type_must_be_in_bound_group():
    """F5-2：给 inf 下坦克命令，而 inf 绑的组里只有枪兵 → 旧行为是 expand 恒空、永久静默 no-op。"""
    with pytest.raises(AssertionError, match="composition"):
        validate_assembly(parse_strategy(TWO_SLOT),
                          parse_assembly(_two_slot_assembly("{inf: G1, armor: G1}")))



def test_composite_action_rejected_in_flow():
    """assign_workers 是复合意图（需按单位扇出），driver 的 translate_op 对它返回 [] ——

    flow 直接发它会编译通过、运行期静默 no-op。这类"能写但静默失效"的路径必须编译期拦。
    等经济维持器接上（issues §3）再放开。
    """
    bad = VALID.replace(
        "      - do: []",
        "      - do: [{op: group_action, group_slot: main, type: terran/scv,"
        " action_atom: assign_workers, params: {task: mineral, count: 8}}]")
    with pytest.raises(AssertionError, match="复合意图"):
        parse_strategy(bad)


def test_branch_id_is_allowed_and_must_be_string():
    """branch_id 是可选的分支稳定标识（观测/读模型用）—— 白名单要放行，但必须是字符串。"""
    ok = VALID.replace("      - do: []", "      - branch_id: wait\n        do: []")
    parse_strategy(ok)
    bad = VALID.replace("      - do: []", "      - branch_id: no\n        do: []")
    with pytest.raises(AssertionError, match="布尔"):
        parse_strategy(bad)


def test_display_names_and_reasons_parse():
    """I2：策略级/step 级可读名 + reasons 中文表 + 组名中文 —— 编译期接受并进 manifest。"""
    m = parse_strategy("""
id: readable
display_name_zh: 装甲蛙跳推进
description_zh: 坦克掩护步兵循环前压
group_slots: [main]
params:
  min_units: {type: int, default: 3, description_zh: 出发所需单位数}
variables: {}
reasons:
  READY: 集结就绪
  ARRIVED: 已抵达目标
initial_step: s1
steps:
  - step_id: s1
    display_name_zh: 集结
    description_zh: 等待成型
    branches:
      - when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: READY}]
      - do: []
  - step_id: s2
    branches:
      - when: {op: arrived, group: main, target: [1.0, 1.0], radius: 1.0}
        do: [{op: exit_strategy, kind: done, reason: ARRIVED}]
      - do: []
edges:
  - {from: s1, to: s2, kind: done, reason: READY}
""")
    assert m.display_name_zh == "装甲蛙跳推进"
    assert m.description_zh == "坦克掩护步兵循环前压"
    assert m.reasons == {"READY": "集结就绪", "ARRIVED": "已抵达目标"}
    assert m.steps["s1"]["display_name_zh"] == "集结"
    assert m.steps["s1"]["description_zh"] == "等待成型"
    assert m.params["min_units"]["description_zh"] == "出发所需单位数"

    a = parse_assembly("""
id: asm
groups:
  - group_id: G_INF
    display_name_zh: 步兵组
    composition:
      terran/marine: {min: 3, target: 5, max: 6}
strategy_instances:
  - instance_id: s1
    strategy_ref: readable
    bindings: {main: G_INF}
    params: {}
""")
    assert a.groups[0].display_name_zh == "步兵组"
    validate_assembly(m, a)


def test_reasons_must_be_strings():
    """reasons 的值被 YAML 解析成非字符串（如数字/列表）→ 编译失败，不静默丢中文。"""
    with pytest.raises(AssertionError, match="reasons"):
        parse_strategy("""
id: bad_reasons
group_slots: [main]
params: {}
variables: {}
reasons: {READY: 42}
initial_step: s1
steps:
  - step_id: s1
    branches:
      - when: {op: ">=", args: [{op: group_count, group: main}, 1]}
        do: [{op: exit_step, kind: done, reason: READY}]
      - do: []
edges: []
""")
