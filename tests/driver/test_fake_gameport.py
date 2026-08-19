"""FakeGamePort：脚本驱动 + 捕获 submitted ops + stop 中断。"""
from types import SimpleNamespace

from driver.fake import FakeGamePort
from game import Grid, Operation, Point2, RawGameState, RawUnit


def _raw(seq: int, tag: int) -> RawGameState:
    u = RawUnit(
        tag=tag, type_name="SCV", position=Point2(0.0, 0.0), alliance=1,
        health=45.0, health_max=45.0, shield=0.0, energy=0.0, build_progress=1.0,
    )
    g = Grid(1, 1, [[0]])
    return RawGameState(
        seq=seq, game_time=0.0, minerals=50, vespene=0, supply_used=8, supply_cap=13,
        units=[u], map_size=(176, 160), creep=g, visibility=g,
    )


class _RecordingSink:
    def __init__(self, port: FakeGamePort) -> None:
        self._port = port
        self.received: list[RawGameState] = []

    def on_game_state(self, raw: RawGameState) -> None:
        self.received.append(raw)
        own = next((u for u in raw.units if u.alliance == 1), None)
        if own is not None:
            self._port.submit_operations([Operation(
                op_id=len(self.received), unit_tags=[own.tag], action="move_to",
                params={"position": Point2(1.0, 2.0)}, seq=raw.seq,
            )])

    def on_session_event(self, event) -> None:
        pass


def test_fake_gameport_drives_script_and_captures_ops():
    port = FakeGamePort(script=[_raw(0, 1), _raw(1, 1), _raw(2, 1)])
    sink = _RecordingSink(port)
    port.set_sink(sink)
    port.start("req-1")
    assert len(sink.received) == 3
    assert len(port.submitted) == 3
    assert port.submitted[0].action == "move_to"
    assert port.submitted[0].unit_tags == [1]
    assert port.read().seq == 2


def test_fake_gameport_stop_breaks_loop():
    port = FakeGamePort(script=[_raw(0, 1), _raw(1, 1), _raw(2, 1)])

    class _StopSink:
        def __init__(self, p):
            self._p = p
            self.n = 0

        def on_game_state(self, raw):
            self.n += 1
            self._p.stop()

        def on_session_event(self, e):
            pass

    sink = _StopSink(port)
    port.set_sink(sink)
    port.start("req")
    assert sink.n == 1
