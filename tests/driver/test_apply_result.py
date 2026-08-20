"""B9 之 D6/D7：ApplyResult 逐 op 裁决 + GameEvent 目录。

守两条：
1. **三态语义**：`ok=None` = 已受理待裁决（"Operation 下一 step 生效"的异步语义），
   不是失败也不是成功 —— 把待裁决显示成"失败"或"已接受"都是骗人；
2. 失败裁决要**带着原因**，并落在 `GAME_EVENTS` 登记的目录里。
"""
import pytest

from game import ApplyResult, GAME_EVENTS, GameEvent, OpApply, Operation
from view.port import OpRing, RecordingPort


class _Inner:
    """按裁决脚本回 ApplyResult 的假 port。"""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.submitted: list[list[Operation]] = []

    def submit_operations(self, ops):
        self.submitted.append(list(ops))
        return self.verdicts.pop(0) if self.verdicts else None


def _ops():
    return [Operation(op_id=1, unit_tags=[10], action="move_to", params={}, seq=0),
            Operation(op_id=2, unit_tags=[20], action="build", params={}, seq=0)]


def test_apply_result_none_means_pending_not_failure():
    """什么都没回 = 待裁决：ok=None。显示成"失败"或"已接受"都是骗人。"""
    ring = OpRing()
    port = RecordingPort(_Inner([]), "flow", ring)
    port.submit_operations(_ops())
    op = ring.all()[0]
    assert op["apply"] == {"ok": None, "failed": False, "detail": None}


def test_per_op_results_win_over_legacy_fields():
    """`results`（D6 正式通道）优先于 `failed_op_ids`（旧字段）。"""
    ring = OpRing()
    result = ApplyResult(
        ok=False,
        failed_op_ids=[1],
        results=[OpApply(1, True, None), OpApply(2, False, "位置非法")])
    port = RecordingPort(_Inner([result]), "production", ring)
    port.submit_operations(_ops())
    ops = {o["op_id"]: o for o in ring.all()}
    assert ops[1]["apply"] == {"ok": True, "failed": False, "detail": None}
    assert ops[2]["apply"] == {"ok": False, "failed": True, "detail": "位置非法"}


def test_legacy_failed_op_ids_still_work():
    ring = OpRing()
    result = ApplyResult(ok=False, failed_op_ids=[2])
    port = RecordingPort(_Inner([result]), "flow", ring)
    port.submit_operations(_ops())
    ops = {o["op_id"]: o for o in ring.all()}
    assert ops[2]["apply"]["failed"] is True


def test_game_event_catalog_is_registered_not_scattered():
    """事件目录只增不改，集中登记 —— 散写字符串会让目录漂移。"""
    assert GAME_EVENTS["op_apply_failed"]
    assert GAME_EVENTS["build_confirmed"]
    assert GAME_EVENTS["user_takeover"]
    # 事件带 game_time（复盘时间线对齐）
    ev = GameEvent(kind="op_apply_failed", payload={}, game_time=12.5)
    assert ev.game_time == 12.5


def test_unknown_event_kind_is_visible_as_an_error():
    """目录外的 kind 直接报错，而不是静默混进事件流。"""
    with pytest.raises(KeyError):
        _ = GAME_EVENTS["op_something_else"]  # 目录里没有
