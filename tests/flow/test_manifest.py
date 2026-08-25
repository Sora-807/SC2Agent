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


def test_predicate_positional_args_rejected_with_migration_hint():
    """D2：谓词改命名参数 —— 旧的位置 args 写法编译失败，且只报"改用命名参数"这一条。"""
    bad = VALID.replace("{op: group_count, group: main}", "{op: group_count, args: [main]}")
    with pytest.raises(AssertionError, match="命名参数") as e:
        parse_strategy(bad)
    assert "缺必需参数" not in str(e.value)  # 迁移错误不叠加参数噪声


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


# ---- 拒绝族单表（2026-08-25 审计批3a 收敛：~40 个同构 test_*_rejected → 一张表）----
# 每条 = (基模板, old, new, match)：一次变异 + 一条锁定文案。错误文案是契约
# （REFACTOR §2 G3：有测试锁），逐字锁定有意。行尾注释保留原独立测试的"为什么"。
# 有额外断言/正向配对的用例不入表，仍独立成函数（见下）。

_EDGE = "  - {from: s1, to: s1, kind: done, reason: OK}\n"
_EXIT_DO = "do: [{op: exit_step, kind: done, reason: OK}]"
_CMP = '{op: ">=", args: [{op: group_count, group: main}, {param: p1}]}'
_ELSE_BLOCK = (
    '      - when: {op: ">=", args: [{op: group_count, group: main}, {param: p1}]}\n'
    f"        {_EXIT_DO}\n"
    "      - do: []"
)
_ELSE_SWAPPED = (
    "      - do: []\n"
    '      - when: {op: ">=", args: [{op: group_count, group: main}, {param: p1}]}\n'
    f"        {_EXIT_DO}"
)
_TIMEOUT_BRANCH = (
    '      - when: {op: ">=", args: [{op: strategy_elapsed}, 600]}\n'
    "        do: [{op: exit_strategy, kind: failed, reason: TIMEOUT}]\n"
)

REJECTED = [
    pytest.param(VALID, _ELSE_BLOCK, _ELSE_SWAPPED, "最后", id="else-must-be-last"),
    pytest.param(VALID, _EXIT_DO, "do: [{op: fly}]", "未知 do", id="unknown-do-op"),
    pytest.param(VALID, "{op: group_count, group: main}", "{op: fly_pred, args: []}", "未知谓词", id="unknown-predicate"),
    # engaged/under_attack/timer_elapsed 已落地（二十六轮）；仍待建的是事件/基地族
    pytest.param(VALID, "{op: group_count, group: main}", "{op: has_ready_base, args: [main]}", "未实现", id="unimplemented-predicate"),
    pytest.param(VALID, _EXIT_DO, "do: [{op: group_action, group_slot: other, type: MARINE, action_atom: move_to, params: {position: [1, 1]}}, {op: exit_step, kind: done, reason: OK}]", "group_slot", id="undeclared-slot"),
    pytest.param(VALID, _EXIT_DO, "do: [{op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {}}, {op: exit_step, kind: done, reason: OK}]", "position", id="action-missing-required-param"),
    pytest.param(VALID, _EXIT_DO, "do: [{op: exit_strategy, kind: done, reason: SAFE}, {op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {position: [1, 1]}}]", "exit 之后", id="action-after-exit"),
    pytest.param(VALID, _EDGE, _EDGE + _EDGE, "重复 edge", id="duplicate-edge"),
    pytest.param(VALID, _EXIT_DO, "do: [{op: set_variable, name: nope, value: {const: 1}}, {op: exit_step, kind: done, reason: OK}]", "变量", id="undeclared-variable-write"),
    # 二十六轮 T8：set_local 转正，但名字必须在 step 的 locals 里声明（拼错 = 永远读不到）
    pytest.param(VALID, _EXIT_DO, "do: [{op: set_local, name: nope, value: {const: 1}}, {op: exit_step, kind: done, reason: OK}]", "未声明的局部变量", id="set-local-undeclared"),
    # {local: x} 读侧同查（D8 完整版：写读两侧一起放行）
    pytest.param(VALID, "{op: group_count, group: main}", "{local: nope}", "未声明的局部变量", id="local-read-undeclared"),
    pytest.param(VALID, "variables: {v1: {type: int, default: 0}}", "variables: {v1: {type: int, default: 0}}\ndefinitions: {d1: {local: v1}}", "definitions", id="local-not-in-definitions"),
    pytest.param(VALID, "{param: p1}", "{param: nope}", "参数", id="undeclared-param-ref"),
    pytest.param(VALID, "{param: p1}", "{var: nope}", "变量", id="undeclared-var-ref"),
    pytest.param(VALID, "{from: s1, to: s1, kind: done, reason: OK}", "{from: s1, to: nope, kind: done, reason: OK}", "不是 step", id="edge-endpoint"),
    pytest.param(VALID, "{op: group_count, group: main}", "{op: group_count}", "缺必需参数", id="predicate-missing-named-param"),
    # 参数名写错（arrived 的 radius 写成 dist 之类）——位置参数时代抓不到这类错
    pytest.param(VALID, "{op: group_count, group: main}", "{op: group_count, grp: main}", "不认识参数", id="predicate-unknown-named-param"),
    # J2：运算符 arity 进签名表 —— and 单参 / not 双参此前都静默通过
    pytest.param(VALID, _CMP, '{op: and, args: [{op: group_count, group: main}]}', "操作数", id="arity-and-single"),
    pytest.param(VALID, _CMP, '{op: not, args: [{op: group_count, group: main}, {param: p1}]}', "操作数", id="arity-not-two"),
    pytest.param(VALID, _CMP, _CMP[:-1] + ", group: main}", "只接受 args", id="operator-extra-named-keys"),
    # H3：YAML 把 on/off/yes/no 解析成布尔 —— 标识符字段必须是字符串
    pytest.param(VALID, "reason: OK", "reason: NO", "布尔", id="yaml-bool-trap"),
    pytest.param(VALID, "      - do: []", "      - do: [{op: group_action, group_slot: main, type: MARINE, action_atom: move_to, params: {position: [1, 1]}}]", "两段式 stable id", id="action-type-single-segment"),
    pytest.param(VALID, "      - do: []", "      - do: [{op: group_action, group_slot: main, action_atom: move_to, params: {position: [1, 1]}}]", "缺 type", id="action-missing-type"),
    # T2c #5：parse 时 dict 覆盖会静默丢掉前一份
    pytest.param(VALID, "edges:", "  - step_id: s1\n    branches:\n      - do: []\nedges:", "重复的 step_id", id="duplicate-step-id"),
    pytest.param(VALID, "{type: int, default: 1}", "{type: int, default: 1, live_editable: true}", "未知键", id="param-unknown-key"),
    pytest.param(VALID, "{type: int, default: 1}", "{type: unit, default: 1}", "未知 type", id="param-unknown-type"),
    # 声明了却从 initial_step 无边路径可达 = 死配置（红线"不静默"）
    pytest.param(VALID, "edges:", "  - step_id: island\n    branches:\n      - do: []\nedges:", "不可达", id="unreachable-step"),
    # ADR-0021 反例：无出口的环（去掉了 TIMEOUT 分支后 s1 自环无出口）
    pytest.param(VALID, _TIMEOUT_BRANCH, "", "无出口", id="cycle-without-exit"),
    pytest.param(VALID, _EDGE, _EDGE + "loop_limits: {max_iterations: 5}\n", "loop_limits", id="loop-limits-unknown-key"),
    # YAML 把 on/yes 解析成 bool（bool 是 int 子类，会绕过 isinstance(int) 检查）
    pytest.param(VALID, _EDGE, _EDGE + "loop_limits: {max_step_transitions: on}\n", "正整数", id="loop-limits-bool"),
    pytest.param(VALID, _EDGE, _EDGE + "loop_limits: {max_step_transitions: 0}\n", "正整数", id="loop-limits-non-positive"),
    # 没有任何 exit_step 会走的 edge = 死配置
    pytest.param(VALID, _EDGE, _EDGE + "  - {from: s1, to: s1, kind: failed, reason: NEVER}\n", "死 edge", id="dead-edge"),
    pytest.param(VALID, _EDGE, _EDGE + "whatever: 1\n", "顶层未知键", id="top-level-unknown-key"),
    # F3：branchs 拼错 → 旧行为是编译通过、该 step 每帧什么都不做（永远）
    pytest.param(VALID, "    branches:", "    branchs:", "未知键", id="step-key-typo"),
    # F3：wehn 拼错 → 旧行为是条件被丢掉、这条分支变成无条件执行
    pytest.param(VALID, '      - when: {op: ">=", args: [{op: strategy_elapsed}, 600]}', '      - wehn: {op: ">=", args: [{op: strategy_elapsed}, 600]}', "未知键", id="branch-key-typo"),
    pytest.param(VALID, "{type: int, default: 0}", "{type: int, default: 0, hot: true}", "未知键", id="variables-unknown-key"),
    pytest.param(VALID, "{type: int, default: 0}", "{type: unit, default: 0}", "未知 type", id="variables-unknown-type"),
    # F4/T2c #10：声明 type=int 却给了字符串
    pytest.param(VALID, "{type: int, default: 1}", '{type: int, default: "x"}', "type=int", id="declared-default-type"),
    pytest.param(VALID, "{type: int, default: 1}", "{type: point, default: [1, 2, 3]}", "type=point", id="point-default-3tuple"),
    # assign_workers 是复合意图（driver translate_op 返回 []，flow 发它=运行期静默 no-op），编译期拦
    pytest.param(VALID, "      - do: []", "      - do: [{op: group_action, group_slot: main, type: terran/scv, action_atom: assign_workers, params: {task: mineral, count: 8}}]", "复合意图", id="composite-action"),
    # ---- DEFS 基模板 ----
    pytest.param(DEFS, "{ref: front}", "{ref: nope}", "definitions 别名", id="unknown-ref"),
    # ref 是纯替换语义，自引用/环会无限展开
    pytest.param(DEFS, "  front: {op: point_toward, origin: {op: group_center, group: armor}, toward: {param: target}, dist: {param: cover}}", "  front: {op: arrived, group: inf, target: {ref: front}, radius: 1.0}", "自引用/环", id="definitions-self-reference"),
    # T2c #3：动作参数里的 {param}/{var}/{ref} 此前不查，写错要到运行期才发现
    pytest.param(DEFS, "params: {position: {ref: front}}", "params: {position: {param: nope}}", "参数", id="do-params-bad-ref"),
]


@pytest.mark.parametrize("base,old,new,match", REJECTED)
def test_strategy_rejected(base, old, new, match):
    with pytest.raises(AssertionError, match=match):
        parse_strategy(base.replace(old, new))


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


def test_assembly_unknown_top_level_key_rejected():
    with pytest.raises(AssertionError, match="顶层未知键"):
        parse_assembly(_assembly_text("{min: 1, target: 1, max: 1}") + "on_exit: release\n")


# ---- F3/F4/F5：键名打错 / 声明类型 / 绑定与兵种（编译期）----


def test_locals_declaration_typechecked():
    """二十六轮 T8：locals 转正（字符串列表）；非列表/非字符串仍是编译错误。"""
    parse_strategy(VALID.replace("  - step_id: s1", "  - step_id: s1\n    locals: [x]"))
    with pytest.raises(AssertionError, match="locals 必须是字符串列表"):
        parse_strategy(VALID.replace("  - step_id: s1", "  - step_id: s1\n    locals: 42"))


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
