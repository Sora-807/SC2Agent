"""编译期模板展开（ADR-0031）：_lib 解析、imports 展开、接线契约与运行时同构。

执行计划的三条验收（ADR-0031 + WORKLOG §0.41 批 A）在这里锁：
1. 含 imports 的策略编译通过且图正确（展开产物与手写同构，引擎直接跑）；
2. edges 接错 exits / 绑定拼错 / 模板体引用未声明参数 —— 全部编译红（中文定位）；
3. 种子模板（gather/push/hold_ramp/garrison/armor_hop/inf_hop）逐个过
   「编译 + 引擎跑到对应转移」（合成 GameState，与 test_tank_marine_push 同款手法）。
"""
from pathlib import Path

import pytest
import yaml

from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from flow.templates import expand_strategy, parse_lib
from game import GameState, Grid, Owner, Point2, Unit
from game.catalog import load_all
from tests.factories import make_gs, make_unit
from flow.templates import SEED_LIB_PATH

CAT = load_all()
# 真相源 = 随库种子（modules/flow/data/_lib.yaml）；runtime/strategies/_lib.yaml
# 是 StrategyStore 播种出来的工作副本（runtime/ 整目录 gitignore）
_LIB = SEED_LIB_PATH
TEMPLATES = parse_lib(_LIB.read_text(encoding="utf-8"))


def _unit(tag, type_name, pos, owner=Owner.SELF):
    return make_unit(tag, type_name, owner, pos.x, pos.y, hp=45.0, hp_max=45.0)


def _gs(seq, units, t=None):
    return make_gs(units, seq=seq, game_time=float(seq) if t is None else t,
                   minerals=0, vespene=0,
                   supply_used=len(units), supply_cap=100)


def _marine_gs(seq, n, pos):
    return _gs(seq, [_unit(100 + i, "MARINE", pos) for i in range(n)])


#: 终局 step：给模板转移一个出环出口（环校验要求环有出口；自环套自环过不了编译）
_FINISH = {"step_id": "finish",
           "branches": [{"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]}]}


def _strategy_doc(imports: dict, steps: list | None = None, edges: list | None = None,
                  group_slots=("main",), params: dict | None = None) -> dict:
    return {
        "id": "t_import", "version": 1, "group_slots": list(group_slots),
        "params": params or {}, "variables": {},
        "initial_step": next(iter(imports)),
        "imports": imports,
        "steps": (steps if steps is not None else []) + [_FINISH],
        "edges": edges if edges is not None else [],
    }


def _assembly_doc(slots=("main",)):
    """min=target：Allocator 的滞回语义是"跌破 min 才补到 target"，min<target 会留缺口
    （组不满也算满足）—— 测试要的是"场上几个就租几个"，把 min 顶到 target。
    inf/armor 两槽必须绑**不同的组**：绑同一个组会让两组心恒等、arrived 恒真。"""
    if tuple(slots) == ("inf", "armor"):
        groups = [
            {"group_id": "G_INF", "display_name_zh": "步兵组",
             "composition": {"terran/marine": {"min": 20, "target": 20, "max": 20}}},
            {"group_id": "G_TANK", "display_name_zh": "装甲组",
             "composition": {"terran/siegetank": {"min": 4, "target": 4, "max": 4}}},
        ]
        bindings = {"inf": "G_INF", "armor": "G_TANK"}
    else:
        groups = [{"group_id": "G_MAIN", "display_name_zh": "主组",
                   "composition": {"terran/marine": {"min": 20, "target": 20, "max": 20},
                                   "terran/siegetank": {"min": 4, "target": 4, "max": 4},
                                   "terran/scv": {"min": 12, "target": 12, "max": 12}}}]
        bindings = {s: "G_MAIN" for s in slots}
    return {
        "id": "t_asm", "groups": groups,
        "strategy_instances": [
            {"instance_id": "s1", "strategy_ref": "t_import",
             "bindings": bindings, "params": {}},
        ],
    }


def _compile(doc: dict):
    return parse_strategy(yaml.safe_dump(doc, allow_unicode=True), templates=TEMPLATES)


def _engine(doc: dict, port=None):
    m = _compile(doc)
    a = parse_assembly(yaml.safe_dump(_assembly_doc(tuple(doc["group_slots"])),
                                      allow_unicode=True))
    return FlowEngine(m, a, port or FakeGamePort(script=[]), catalog=CAT)


# ---------------- lib 本身 ----------------

def test_shipped_lib_parses_and_has_seed_templates():
    names = set(TEMPLATES)
    assert {"gather", "push", "hold_ramp", "garrison", "armor_hop", "inf_hop"} <= names


def test_lib_rejects_missing_exit_declaration():
    lib = """
step_templates:
  gather:
    params: {min_units: {type: int, default: 1}}
    exits: [{kind: done, reason: FORMED}]
    branches:
      - when: {op: ">=", args: [{op: game_time}, 999]}
        do: [{op: exit_step, kind: done, reason: OTHER}]
      - do: []
"""
    with pytest.raises(AssertionError, match="exits"):
        parse_lib(lib)


def test_lib_rejects_exit_strategy_in_template():
    lib = """
step_templates:
  finisher:
    params: {}
    exits: [{kind: done, reason: DONE}]
    branches:
      - do: [{op: exit_strategy, kind: done, reason: DONE}]
"""
    with pytest.raises(AssertionError, match="exit_strategy"):
        parse_lib(lib)


def test_lib_rejects_nested_import_shape():
    lib = """
step_templates:
  weird:
    imports: {x: {from: _lib, template: gather}}
    params: {}
    exits: [{kind: done, reason: FORMED}]
    branches:
      - do: [{op: exit_step, kind: done, reason: FORMED}]
"""
    with pytest.raises(AssertionError, match="未知键.*imports"):
        parse_lib(lib)


# ---------------- 展开：编译期校验 ----------------

def test_imports_need_templates_loaded():
    doc = _strategy_doc({"gather": {"from": "_lib", "template": "gather"}})
    with pytest.raises(AssertionError, match="_lib"):
        parse_strategy(yaml.safe_dump(doc), templates=None)


def test_unknown_template_name_is_compile_error():
    doc = _strategy_doc({"gather": {"from": "_lib", "template": "nope"}})
    with pytest.raises(AssertionError, match="nope.*不在 _lib"):
        _compile(doc)


def test_unknown_binding_key_is_compile_error():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather",
                    "params": {"min_unitss": 1}}})
    with pytest.raises(AssertionError, match="未声明的参数.*min_unitss"):
        _compile(doc)


def test_unbound_required_template_param_is_compile_error():
    # push 的 target 无默认值 —— 不绑定就是接口没接全
    doc = _strategy_doc(
        {"push": {"from": "_lib", "template": "push"}},
        edges=[{"from": "push", "to": "finish", "kind": "done", "reason": "ARRIVED"}])
    with pytest.raises(AssertionError, match="target.*未绑定且无默认值"):
        _compile(doc)


def test_binding_type_mismatch_is_compile_error():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather",
                    "params": {"min_units": "八个"}}})
    with pytest.raises(AssertionError, match="type=int"):
        _compile(doc)


def test_edge_reason_outside_template_exits_is_compile_error():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather", "params": {"min_units": 1}}},
        edges=[{"from": "gather", "to": "gather", "kind": "done", "reason": "NOPE"}])
    with pytest.raises(AssertionError, match="不在模板.*gather.*exits"):
        _compile(doc)


def test_import_id_colliding_with_handwritten_step_is_error():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather", "params": {"min_units": 1}}},
        steps=[{"step_id": "gather", "branches": [{"do": []}]}],
        edges=[{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}])
    with pytest.raises(AssertionError, match="重复"):
        _compile(doc)


def test_template_body_cannot_capture_strategy_params():
    # 自造库：模板体引用了未声明的名字 → 编译红（两层作用域，暗渠比错误贵）
    lib = parse_lib("""
step_templates:
  sneak:
    params: {}
    exits: [{kind: done, reason: GO}]
    branches:
      - when: {op: ">=", args: [{op: game_time}, {param: strategy_level_name}]}
        do: [{op: exit_step, kind: done, reason: GO}]
      - do: []
""")
    doc = _strategy_doc({"sneak": {"from": "_lib", "template": "sneak"}},
                        edges=[{"from": "sneak", "to": "finish", "kind": "done", "reason": "GO"}])
    with pytest.raises(AssertionError, match="未声明的模板参数.*strategy_level_name"):
        parse_strategy(yaml.safe_dump(doc, allow_unicode=True), templates=lib)


def test_strategy_param_bridge_survives_expansion():
    # 绑定值引用策略级参数（{param: target}）：展开后原样保留、由策略 params 声明兜住
    doc = _strategy_doc(
        {"push": {"from": "_lib", "template": "push",
                  "params": {"target": {"param": "aim"}}}},
        params={"aim": {"type": "point", "default": [10.0, 10.0]}},
        edges=[{"from": "push", "to": "finish", "kind": "done", "reason": "ARRIVED"}])
    m = _compile(doc)
    assert m.imported == ["push"]
    when = m.steps["push"]["branches"][0]["when"]
    assert when["target"] == {"param": "aim"}     # 桥接节点原样保留


def test_expand_produces_same_shape_as_handwritten():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather", "params": {"min_units": 1}}},
        edges=[{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}])
    d, imported = expand_strategy(yaml.safe_load(yaml.safe_dump(doc)), TEMPLATES)
    assert imported == ["gather"]
    step = d["steps"][0]
    assert step["step_id"] == "gather"
    # {param: min_units} 已替换为绑定值 1；{param: group_slot} 走默认 "main"
    when = step["branches"][0]["when"]
    assert when["args"][1] == 1
    assert when["args"][0]["group"] == "main"
    assert "imports" not in d


# ---------------- 展开：引擎跑到对应转移（种子模板逐个） ----------------

def test_gather_template_transitions_on_formed():
    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather", "params": {"min_units": 3}}},
        edges=[{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}])
    eng = _engine(doc)
    eng.on_game_state(_marine_gs(0, 2, Point2(0, 0)))     # 不够 → 等待
    assert eng.snapshot()["active_step"] == "gather"
    eng.on_game_state(_marine_gs(1, 3, Point2(0, 0)))     # 够 3 → FORMED
    snap = eng.snapshot()
    assert snap["transitions"][-1]["reason"] == "FORMED"
    assert snap["branch_hit"]["branch_id"] == "b_ready"


def test_push_template_transitions_on_arrived():
    doc = _strategy_doc(
        {"push": {"from": "_lib", "template": "push",
                  "params": {"target": [50.0, 50.0], "radius": 4.0}}},
        edges=[{"from": "push", "to": "finish", "kind": "done", "reason": "ARRIVED"}])
    port = FakeGamePort(script=[])
    eng = _engine(doc, port)
    eng.on_game_state(_marine_gs(0, 4, Point2(0, 0)))     # 远 → 每帧续压 attack_move
    assert any(o.action == "attack_move_to" for o in port.submitted)
    eng.on_game_state(_marine_gs(1, 4, Point2(50, 50)))   # 到 → ARRIVED
    assert eng.snapshot()["transitions"][-1]["reason"] == "ARRIVED"


def test_hold_ramp_breakout_and_hold():
    doc = _strategy_doc(
        {"hold": {"from": "_lib", "template": "hold_ramp",
                  "params": {"hold_point": [10.0, 10.0], "min_units": 3}}},
        edges=[{"from": "hold", "to": "finish", "kind": "done", "reason": "BREAKOUT"}])
    port = FakeGamePort(script=[])
    eng = _engine(doc, port)
    # 1 个兵且不在口子 → move_to 口子
    eng.on_game_state(_marine_gs(0, 1, Point2(0, 0)))
    assert any(o.action == "move_to" for o in port.submitted)
    # 到位但人不够 → hold_position 驻守
    eng.on_game_state(_marine_gs(1, 1, Point2(10, 10)))
    assert any(o.action == "hold_position" for o in port.submitted)
    assert eng.snapshot()["active_step"] == "hold"
    # 凑够 3 → BREAKOUT
    eng.on_game_state(_marine_gs(2, 3, Point2(10, 10)))
    assert eng.snapshot()["transitions"][-1]["reason"] == "BREAKOUT"


def test_garrison_transitions_on_ready():
    doc = _strategy_doc(
        {"gar": {"from": "_lib", "template": "garrison",
                 "params": {"garrison_pos": [20.0, 20.0], "min_units": 8}}},
        edges=[{"from": "gar", "to": "finish", "kind": "done", "reason": "READY"}])
    port = FakeGamePort(script=[])
    eng = _engine(doc, port)
    eng.on_game_state(_marine_gs(0, 4, Point2(0, 0)))     # 不够 → 攻击移动去驻扎点
    assert any(o.action == "attack_move_to" for o in port.submitted)
    eng.on_game_state(_marine_gs(1, 8, Point2(0, 0)))
    assert eng.snapshot()["transitions"][-1]["reason"] == "READY"


def _tank_gs(seq, inf_pos, tank_pos, inf_n=6, tank_n=2):
    units = [_unit(100 + i, "MARINE", inf_pos) for i in range(inf_n)]
    units += [_unit(200 + i, "SIEGETANK", tank_pos) for i in range(tank_n)]
    return _gs(seq, units)


def _two_slot_doc(imports, edges, params=None):
    doc = _strategy_doc(imports, edges=edges, group_slots=("inf", "armor"),
                        params=params or {})
    return doc


def test_armor_hop_transitions():
    doc = _two_slot_doc(
        {"hop": {"from": "_lib", "template": "armor_hop",
                 "params": {"target": [60.0, 0.0], "armor_slot": "armor",
                            "inf_slot": "inf"}}},
        edges=[{"from": "hop", "to": "finish", "kind": "done", "reason": "IN_PLACE"},
               {"from": "hop", "to": "finish", "kind": "done", "reason": "ARRIVED"}])
    eng = _engine(doc)
    eng.on_game_state(_tank_gs(0, Point2(0, 0), Point2(30, 0)))   # 坦克远 → 前压
    assert eng.snapshot()["active_step"] == "hop"
    eng.on_game_state(_tank_gs(1, Point2(0, 0), Point2(1, 0)))    # 坦克到组心 3 内 → IN_PLACE
    assert eng.snapshot()["transitions"][-1]["reason"] == "IN_PLACE"


def test_inf_hop_transitions():
    doc = _two_slot_doc(
        {"hop": {"from": "_lib", "template": "inf_hop",
                 "params": {"target": [60.0, 0.0], "inf_slot": "inf",
                            "armor_slot": "armor"}}},
        edges=[{"from": "hop", "to": "finish", "kind": "done", "reason": "ARRIVED"},
               {"from": "hop", "to": "finish", "kind": "interrupted", "reason": "STRAGGLED"},
               {"from": "hop", "to": "finish", "kind": "done", "reason": "IN_PLACE"}])
    eng = _engine(doc)
    eng.on_game_state(_tank_gs(0, Point2(0, 0), Point2(0, 0)))    # 同位：未到目标、未脱队、未就位 → 前压
    assert eng.snapshot()["active_step"] == "hop"
    eng.on_game_state(_tank_gs(1, Point2(59, 0), Point2(0, 0)))   # 步兵近目标 → ARRIVED
    assert eng.snapshot()["transitions"][-1]["reason"] == "ARRIVED"


def test_strategy_static_carries_imported_marker():
    from view.statics import strategy_static

    doc = _strategy_doc(
        {"gather": {"from": "_lib", "template": "gather", "params": {"min_units": 1}}},
        edges=[{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}])
    m = _compile(doc)
    a = parse_assembly(yaml.safe_dump(_assembly_doc(), allow_unicode=True))
    st = strategy_static(m, a)
    assert st.imported == ["gather"]
    # reason 默认表 ∪ 策略覆盖（rev 15）：默认词不写也有中文
    assert st.reasons["FORMED"] == "成型"
    assert st.reasons["ARRIVED"] == "抵达"


def test_offline_session_assembles_imported_strategy(tmp_path):
    """会话装配闭环：load_strategy_file 装同目录 _lib → OfflineSession 真跑出转移。"""
    import shutil

    shutil.copy(_LIB, tmp_path / "_lib.yaml")
    doc = {
        "strategy": _strategy_doc(
            {"gather": {"from": "_lib", "template": "gather",
                        "params": {"min_units": 1, "unit_type": "terran/scv"}}},
            edges=[{"from": "gather", "to": "finish",
                    "kind": "done", "reason": "FORMED"}]),
        "assembly": {
            "id": "t_asm",
            "groups": [{"group_id": "G_MAIN", "composition":
                        {"terran/scv": {"min": 12, "target": 12, "max": 12}}}],
            "strategy_instances": [{"instance_id": "s1", "strategy_ref": "t_import",
                                    "bindings": {"main": "G_MAIN"}, "params": {}}],
        },
    }
    path = tmp_path / "scv_walk.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    from api.session import OfflineSession
    from view.strategies import load_strategy_file

    m, _a = load_strategy_file(path)
    assert m.imported == ["gather"]
    sess = OfflineSession(CAT, strategy_path=str(path))
    for _ in range(5):
        sess.tick()
    snap = sess.engine.snapshot()
    assert any(t["reason"] == "FORMED" for t in snap["transitions"])
