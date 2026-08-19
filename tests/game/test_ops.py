"""game.operation 操作目录：known actions + validate_op。"""
from game import OP_CATALOG, Operation, is_known_action, validate_op


def test_known_actions():
    for a in [
        "move_to", "attack_move_to", "hold_position", "stop", "follow", "patrol",
        "focus_fire", "build", "train", "research", "load", "unload", "use_ability",
        "cancel", "morph",
    ]:
        assert is_known_action(a), a


def test_unknown_action():
    assert not is_known_action("fly_to")


def test_validate_op_ok():
    op = Operation(op_id=1, unit_tags=[1], action="move_to", params={"position": [1, 2]}, seq=0)
    assert validate_op(op) == []


def test_validate_op_missing_param():
    op = Operation(op_id=1, unit_tags=[1], action="move_to", params={}, seq=0)
    errs = validate_op(op)
    assert len(errs) == 1 and "position" in errs[0]


def test_validate_op_unknown():
    op = Operation(op_id=1, unit_tags=[1], action="fly_to", params={}, seq=0)
    errs = validate_op(op)
    assert len(errs) == 1 and "unknown" in errs[0]


def test_build_requires_type_and_position():
    op = Operation(op_id=1, unit_tags=[1], action="build", params={"type": "SUPPLYDEPOT"}, seq=0)
    errs = validate_op(op)
    assert any("position" in e for e in errs)
