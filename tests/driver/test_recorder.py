"""StateRecorder：每整秒存 JSONL snapshot；字段齐全；grid 只存 dims。"""
import json

from driver.recorder import StateRecorder
from game import Grid, Point2, RawGameState, RawOrder, RawUnit


def _raw(seq, game_time, tag=1, type_name="SCV", build_progress=1.0, orders=None):
    u = RawUnit(
        tag=tag, type_name=type_name, position=Point2(1.0, 2.0), alliance=1,
        health=45.0, health_max=45.0, shield=0.0, energy=0.0, build_progress=build_progress,
        orders=orders or [],
    )
    g = Grid(2, 2, [[0, 0], [0, 0]])
    return RawGameState(
        seq=seq, game_time=game_time, minerals=50, vespene=0, supply_used=8, supply_cap=13,
        units=[u], map_size=(2, 2), creep=g, visibility=g,
    )


def test_recorder_saves_each_second(tmp_path):
    trace = tmp_path / "t.jsonl"
    rec = StateRecorder(trace, interval_sec=1.0)
    for i, t in enumerate([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]):
        rec.on_game_state(_raw(i, t))
    lines = trace.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4  # t=0,1,2,3
    times = [json.loads(l)["game_time"] for l in lines]
    assert times == [0.0, 1.0, 2.0, 3.0]
    assert rec.count == 4


def test_recorder_unit_fields(tmp_path):
    trace = tmp_path / "t.jsonl"
    rec = StateRecorder(trace, 1.0)
    rec.on_game_state(_raw(0, 0.0, tag=7, type_name="SUPPLYDEPOT", build_progress=0.5,
                          orders=[RawOrder(ability_name="Build", target_tag=99,
                                           target_pos=Point2(3.0, 4.0))]))
    snap = json.loads(trace.read_text(encoding="utf-8").strip())
    u = snap["units"][0]
    assert u["tag"] == 7
    assert u["type_name"] == "SUPPLYDEPOT"
    assert u["build_progress"] == 0.5
    assert u["position"] == [1.0, 2.0]
    assert u["orders"][0]["ability"] == "Build"
    assert u["orders"][0]["target_tag"] == 99
    assert u["orders"][0]["target_pos"] == [3.0, 4.0]
    assert snap["creep_dims"] == [2, 2]  # grid 只存 dims
    assert snap["map_size"] == [2, 2]
