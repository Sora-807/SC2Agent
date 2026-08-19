"""translate_op 逐操作分派：op → burnysc2 命令（假 unit 验命令对）。"""
from driver.sc2_adapter import translate_op
from game import Operation


class FakeUnit:
    def __init__(self, tag):
        self.tag = tag

    def move(self, p): return ("move", self.tag, p)
    def attack(self, t): return ("attack", self.tag, t)
    def hold_position(self): return ("hold", self.tag)
    def stop(self): return ("stop", self.tag)
    def patrol(self, p): return ("patrol", self.tag, p)
    def build(self, t, p): return ("build", self.tag, t, p)
    def train(self, t): return ("train", self.tag, t)
    def research(self, t): return ("research", self.tag, t)


def _find(units):
    return lambda tag: next((u for u in units if u.tag == tag), None)


def _op(action, unit_tags=(1,), **params):
    return Operation(op_id=1, unit_tags=list(unit_tags), action=action, params=params, seq=0)


def test_move_to():
    cmds = translate_op(_op("move_to", position=[10, 20]), _find([FakeUnit(1)]))
    assert len(cmds) == 1
    assert cmds[0][0] == "move" and cmds[0][1] == 1
    assert cmds[0][2].x == 10 and cmds[0][2].y == 20


def test_attack_move_to():
    cmds = translate_op(_op("attack_move_to", position=[5, 6]), _find([FakeUnit(1)]))
    assert cmds[0][0] == "attack" and cmds[0][2].x == 5


def test_hold_position_and_stop():
    assert translate_op(_op("hold_position"), _find([FakeUnit(1)])) == [("hold", 1)]
    assert translate_op(_op("stop"), _find([FakeUnit(1)])) == [("stop", 1)]


def test_follow_and_focus_fire():
    units = [FakeUnit(1), FakeUnit(2)]
    fol = translate_op(_op("follow", unit_tags=[1], target_unit=2), _find(units))
    assert fol == [("move", 1, units[1])]  # V1: follow 近似 move-to-target
    ff = translate_op(_op("focus_fire", unit_tags=[1], target_unit=2), _find(units))
    assert ff == [("attack", 1, units[1])]


def test_patrol():
    cmds = translate_op(_op("patrol", positions=[[1, 2], [3, 4]]), _find([FakeUnit(1)]))
    assert cmds[0][0] == "patrol" and cmds[0][2].x == 1


def test_build_train_research():
    fu = _find([FakeUnit(1)])
    b = translate_op(_op("build", type="SUPPLYDEPOT", position=[9, 9]), fu)
    assert b[0][0] == "build" and b[0][2] == "SUPPLYDEPOT" and b[0][3].x == 9
    assert translate_op(_op("train", type="SCV"), fu) == [("train", 1, "SCV")]
    assert translate_op(_op("research", type="STIMPACK"), fu) == [("research", 1, "STIMPACK")]


def test_multi_unit_move():
    cmds = translate_op(_op("move_to", unit_tags=[1, 2], position=[1, 1]), _find([FakeUnit(1), FakeUnit(2)]))
    assert len(cmds) == 2 and {c[1] for c in cmds} == {1, 2}


def test_empty_units_and_unknown_action():
    assert translate_op(_op("move_to", unit_tags=[99], position=[1, 1]), _find([])) == []
    assert translate_op(_op("load", target_unit=2), _find([FakeUnit(1)])) == []  # load 未实现


def test_translators_subset_of_catalog():
    """driver 的 TRANSLATORS key 必须 ⊆ game.operation.OP_CATALOG（catalog 是权威源）。"""
    from driver.sc2_adapter import TRANSLATORS
    from game.operation import OP_CATALOG
    assert set(TRANSLATORS) <= set(OP_CATALOG), "driver 有 catalog 外的 action"
