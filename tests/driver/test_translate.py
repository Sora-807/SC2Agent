"""driver 翻译层单测：OP_CATALOG 每个 action → burnysc2 命令（FakeUnit 记录调用）。

覆盖两层：
- 已实现 action（TRANSLATORS）：逐个断言调用的 burnysc2 方法 + 参数正确。
- 未实现 action（UNIMPLEMENTED_ACTIONS）：断言返回 []（V1 no-op），且两份清单与
  OP_CATALOG 完全对齐——catalog 加新 action 要么实现、要么进未实现清单，不静默漏掉。
"""
import pytest

from driver.sc2_adapter import TRANSLATORS, UNIMPLEMENTED_ACTIONS, resolve_point, translate_op
from game import OP_CATALOG, Operation, Point2
from sc2.position import Point2 as SC2Point2


class FakeUnit:
    """记录调用的假单位（方法签名与 sc2.unit.Unit 的命令方法对齐，见 test_sc2_api_contract）。"""

    def __init__(self, tag):
        self.tag = tag

    def move(self, p, queue=False): return ("move", self.tag, p)
    def attack(self, t, queue=False): return ("attack", self.tag, t)
    def hold_position(self, queue=False): return ("hold", self.tag)
    def stop(self, queue=False): return ("stop", self.tag)
    def patrol(self, p, queue=False): return ("patrol", self.tag, p)
    def smart(self, t, queue=False): return ("smart", self.tag, t)
    def build(self, t, p=None, queue=False): return ("build", self.tag, t, p)
    def train(self, t, queue=False): return ("train", self.tag, t)
    def research(self, t, queue=False): return ("research", self.tag, t)


def _find(units):
    return lambda tag: next((u for u in units if u.tag == tag), None)


def _op(action, unit_tags=(1,), **params):
    return Operation(op_id=1, unit_tags=list(unit_tags), action=action, params=params, seq=0)


# ---- move_to ----


def test_move_to_with_list_point():
    cmds = translate_op(_op("move_to", position=[10, 20]), _find([FakeUnit(1)]))
    assert len(cmds) == 1
    assert cmds[0][0] == "move" and cmds[0][1] == 1
    assert cmds[0][2].x == 10 and cmds[0][2].y == 20


def test_move_to_with_game_point2():
    cmds = translate_op(_op("move_to", position=Point2(3.5, 4.5)), _find([FakeUnit(1)]))
    assert cmds[0][2] == SC2Point2((3.5, 4.5))


def test_move_to_with_sc2_point2_passthrough():
    p = SC2Point2((7.0, 8.0))
    cmds = translate_op(_op("move_to", position=p), _find([FakeUnit(1)]))
    assert cmds[0][2] is p  # burnysc2 Point2 原样透传，不重复包装


# ---- attack_move_to / hold / stop ----


def test_attack_move_to():
    cmds = translate_op(_op("attack_move_to", position=[5, 6]), _find([FakeUnit(1)]))
    assert cmds[0][0] == "attack" and cmds[0][2].x == 5


def test_hold_position_and_stop():
    assert translate_op(_op("hold_position"), _find([FakeUnit(1)])) == [("hold", 1)]
    assert translate_op(_op("stop"), _find([FakeUnit(1)])) == [("stop", 1)]


# ---- follow / focus_fire / load（目标单位型）----


def test_follow_and_focus_fire():
    units = [FakeUnit(1), FakeUnit(2)]
    fol = translate_op(_op("follow", unit_tags=[1], target_unit=2), _find(units))
    assert fol == [("move", 1, units[1])]  # V1 近似：follow = move-to-target
    ff = translate_op(_op("focus_fire", unit_tags=[1], target_unit=2), _find(units))
    assert ff == [("attack", 1, units[1])]


def test_load_uses_smart():
    units = [FakeUnit(1), FakeUnit(2)]
    assert translate_op(_op("load", unit_tags=[1], target_unit=2), _find(units)) == [("smart", 1, units[1])]


def test_target_ops_noop_when_target_missing():
    # 目标单位不存在 → 不发命令（V1 no-op），而不是 crash
    assert translate_op(_op("follow", target_unit=99), _find([FakeUnit(1)])) == []
    assert translate_op(_op("focus_fire", target_unit=99), _find([FakeUnit(1)])) == []
    assert translate_op(_op("load", target_unit=99), _find([FakeUnit(1)])) == []


# ---- patrol ----


def test_patrol_uses_first_point():
    cmds = translate_op(_op("patrol", positions=[[1, 2], [3, 4]]), _find([FakeUnit(1)]))
    assert cmds[0][0] == "patrol" and cmds[0][2].x == 1  # V1：只取第一个点（burnysc2 patrol 单点）


def test_patrol_noop_when_empty_positions():
    assert translate_op(_op("patrol", positions=[]), _find([FakeUnit(1)])) == []


# ---- build / train / research ----


def test_build_train_research():
    fu = _find([FakeUnit(1)])
    b = translate_op(_op("build", type="SUPPLYDEPOT", position=[9, 9]), fu)
    assert b[0][0] == "build" and b[0][2] == "SUPPLYDEPOT" and b[0][3].x == 9
    assert translate_op(_op("train", type="SCV"), fu) == [("train", 1, "SCV")]
    assert translate_op(_op("research", type="STIMPACK"), fu) == [("research", 1, "STIMPACK")]


def test_build_train_research_noop_when_no_units():
    # V1：build/train/research 只用 units[0]；无单位 → no-op
    assert translate_op(_op("build", type="SUPPLYDEPOT", position=[9, 9]), _find([])) == []
    assert translate_op(_op("train", type="SCV"), _find([])) == []
    assert translate_op(_op("research", type="STIMPACK"), _find([])) == []


# ---- 多单位 / 空 / 未知 ----


def test_multi_unit_move():
    cmds = translate_op(_op("move_to", unit_tags=[1, 2], position=[1, 1]), _find([FakeUnit(1), FakeUnit(2)]))
    assert len(cmds) == 2 and {c[1] for c in cmds} == {1, 2}


def test_missing_unit_skipped():
    # unit_tags 里有不存在的 tag → 只对存在的单位发命令
    cmds = translate_op(_op("move_to", unit_tags=[1, 99], position=[1, 1]), _find([FakeUnit(1)]))
    assert len(cmds) == 1 and cmds[0][1] == 1


def test_unknown_action_returns_empty():
    assert translate_op(_op("fly_to", position=[1, 1]), _find([FakeUnit(1)])) == []


# ---- resolve_point 边界 ----


def test_resolve_point_variants():
    assert resolve_point(Point2(1.5, 2.5)) == SC2Point2((1.5, 2.5))
    assert resolve_point([1, 2]) == SC2Point2((1, 2))
    assert resolve_point((3.0, 4.0)) == SC2Point2((3.0, 4.0))
    sc2p = SC2Point2((5.0, 6.0))
    assert resolve_point(sc2p) is sc2p


def test_resolve_point_duck_typed():
    from types import SimpleNamespace
    assert resolve_point(SimpleNamespace(x=7.0, y=8.0)) == SC2Point2((7.0, 8.0))


def test_resolve_point_rejects_garbage():
    with pytest.raises(ValueError, match="cannot resolve point"):
        resolve_point("main_base")  # map 名不应到达 driver（engine 已解析，ADR-0029）
    with pytest.raises(ValueError, match="cannot resolve point"):
        resolve_point(None)


# ---- 操作集 ↔ 翻译清单 全量对齐 ----


def test_translators_and_unimplemented_partition_catalog():
    """OP_CATALOG 是权威源：每个 action 要么有翻译函数、要么在未实现清单里（带原因）。"""
    implemented = set(TRANSLATORS)
    unimplemented = set(UNIMPLEMENTED_ACTIONS)
    assert implemented & unimplemented == set(), "同一 action 不能既实现又未实现"
    assert implemented | unimplemented == set(OP_CATALOG), \
        "OP_CATALOG 与 driver 清单不一致：" \
        f"缺 {set(OP_CATALOG) - implemented - unimplemented}，" \
        f"多 {implemented | unimplemented - set(OP_CATALOG)}"


def test_translators_subset_of_catalog():
    assert set(TRANSLATORS) <= set(OP_CATALOG), "driver 有 catalog 外的 action"


@pytest.mark.parametrize("action", sorted(UNIMPLEMENTED_ACTIONS))
def test_unimplemented_actions_noop(action):
    """未实现清单里的每个 action：V1 明确 no-op（返回 []），且每一条都有原因说明。"""
    assert UNIMPLEMENTED_ACTIONS[action], f"{action} 缺未实现原因说明"
    assert translate_op(_op(action), _find([FakeUnit(1)])) == []  # 未实现 → no-op

