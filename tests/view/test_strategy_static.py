"""static/strategy（rev 7）：策略图结构。

守两件事：
1. **图与状态分开**：这里是不变的结构（steps/edges），`frame/flow` 是每帧的位置。
   合起来才画得出"当前在哪、从哪条边来、还有哪些没走过"。
2. `branches` 的值树**原样带出** —— F9 的 AST 编辑器需要完整结构，摊平一次就得再补通道。
"""
from flow.manifest import parse_assembly, parse_strategy

from view.encode import to_json
from view.statics import strategy_static

STRATEGY = """
id: graph_probe
version: 2
group_slots: [main]
params:
  min_units: {type: int, default: 3}
variables:
  checkpoint: {type: point, default: null}
definitions:
  ahead: {op: point_toward, origin: {op: group_center, group: main}, toward: [9.0, 9.0], dist: 3.0}
initial_step: a
steps:
  - step_id: a
    branches:
      - branch_id: go
        when: {op: ">=", args: [{op: group_count, group: main}, {param: min_units}]}
        do: [{op: exit_step, kind: done, reason: READY}]
      - branch_id: wait
        do: []
  - step_id: b
    branches:
      - branch_id: back
        when: {op: arrived, group: main, target: {ref: ahead}, radius: 2.0}
        do: [{op: exit_step, kind: done, reason: LOOP}]
      - branch_id: push
        do:
          - {op: group_action, group_slot: main, type: terran/marine,
             action_atom: move_to, params: {position: {ref: ahead}}}
  - step_id: c
    branches:
      - branch_id: fin
        do: [{op: exit_strategy, kind: done, reason: DONE}]
edges:
  - {from: a, to: b, kind: done, reason: READY}
  - {from: b, to: c, kind: done, reason: LOOP}
loop_limits: {max_step_transitions: 30}
"""

ASSEMBLY = """
id: graph_assembly
groups:
  - group_id: G1
    composition:
      terran/marine: {min: 3, target: 5, max: 6}
strategy_instances:
  - instance_id: s1
    strategy_ref: graph_probe
    bindings: {main: G1}
    params: {min_units: 4}
"""


def _static():
    return strategy_static(parse_strategy(STRATEGY), parse_assembly(ASSEMBLY))


def test_graph_structure_is_complete():
    s = _static()
    assert s.id == "graph_probe" and s.version == 2
    assert s.initial_step == "a"
    assert [x.step_id for x in s.steps] == ["a", "b", "c"]
    assert [(e.from_step, e.to, e.kind, e.reason) for e in s.edges] == [
        ("a", "b", "done", "READY"),
        ("b", "c", "done", "LOOP"),
    ]
    assert s.loop_limits == {"max_step_transitions": 30}
    assert s.bindings == {"main": "G1"}


def test_edge_from_is_renamed_for_json():
    """契约字段名是 `from`（Python 保留字）→ schema 用 from_step 承载，编码时改名。"""
    d = to_json(_static())
    assert d["edges"][0]["from"] == "a"
    assert "from_step" not in d["edges"][0]


def test_branch_value_trees_are_passed_through_verbatim():
    """F9 的 AST 编辑器要完整结构：when 的值树、do 的动作、ref 引用都不能被摊平。"""
    s = _static()
    step_b = next(x for x in s.steps if x.step_id == "b")
    back = next(b for b in step_b.branches if b["branch_id"] == "back")
    assert back["when"]["op"] == "arrived"
    assert back["when"]["target"] == {"ref": "ahead"}     # ref 原样保留
    push = next(b for b in step_b.branches if b["branch_id"] == "push")
    assert push["do"][0]["action_atom"] == "move_to"


def test_declarations_are_declarations_not_values():
    """params/variables 是**声明**（type+default）；生效值在 frame/flow（实例覆盖过的）。"""
    s = _static()
    assert s.params["min_units"] == {"type": "int", "default": 3}   # 声明里是 3
    assert s.variables["checkpoint"]["type"] == "point"
    assert s.definitions["ahead"]["op"] == "point_toward"


def test_unvisited_steps_are_visible():
    """图里有 c，即使一次都没走到过 —— 只靠转移历史推图会看不见没走过的节点。"""
    s = _static()
    assert "c" in [x.step_id for x in s.steps]


def test_json_serializable():
    d = to_json(_static())
    assert set(d) >= {"id", "version", "steps", "edges", "initial_step", "bindings"}


def test_readability_fields_forwarded():
    """rev 12（I2/I4）：display_name_zh/description_zh/reasons/group_names 原样转发，
    没写的地方是空串/空表 —— 前端 default("") 容错，退回 identifier 不炸。"""
    zh_strategy = STRATEGY.replace(
        "id: graph_probe",
        "id: graph_probe\ndisplay_name_zh: 探针策略\ndescription_zh: 验证转发", 1,
    ).replace(
        "  - step_id: a\n", "  - step_id: a\n    display_name_zh: 甲\n", 1,
    ).replace(
        "loop_limits: {max_step_transitions: 30}",
        "loop_limits: {max_step_transitions: 30}\nreasons: {READY: 就绪, LOOP: 回环, DONE: 完成}", 1,
    )
    zh_assembly = ASSEMBLY.replace(
        "  - group_id: G1\n", "  - group_id: G1\n    display_name_zh: 步兵组\n", 1)
    s = strategy_static(parse_strategy(zh_strategy), parse_assembly(zh_assembly))
    assert s.display_name_zh == "探针策略"
    assert s.description_zh == "验证转发"
    assert s.reasons == {"READY": "就绪", "LOOP": "回环", "DONE": "完成"}
    assert s.group_names == {"G1": "步兵组"}
    assert s.steps[0].display_name_zh == "甲"
    assert s.steps[1].display_name_zh == ""  # 没写 = 空串，不是 KeyError
    d = to_json(s)
    assert d["display_name_zh"] == "探针策略" and d["reasons"]["READY"] == "就绪"
    assert d["group_names"] == {"G1": "步兵组"}
    assert d["steps"][0]["display_name_zh"] == "甲"
