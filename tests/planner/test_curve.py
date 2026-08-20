"""planner.curve：ProjectionCurve 查询（time_to/peak_minerals/stalls）。"""
from planner.curve import ProjectionCurve, ProjectionEvent, ProjectionPoint


def _pt(t, minerals=0.0, units=None):
    return ProjectionPoint(t=t, minerals=minerals, gas=0.0, supply_used=0, supply_cap=0,
                           mineral_workers=0, gas_workers=0, buildings={}, units=units or {},
                           in_flight_count=0)


def test_time_to_first_match():
    c = ProjectionCurve(points=[_pt(0), _pt(10, units={"MARINE": 5}), _pt(20, units={"MARINE": 20})])
    assert c.time_to(lambda p: p.units.get("MARINE", 0) >= 20) == 20
    assert c.time_to(lambda p: p.units.get("MARINE", 0) >= 100) is None
    assert c.time_to(lambda p: p.units.get("MARINE", 0) >= 5) == 10  # 首个满足


def test_peak_minerals():
    c = ProjectionCurve(points=[_pt(0, minerals=50), _pt(10, minerals=300), _pt(20, minerals=100)])
    assert c.peak_minerals() == 300
    assert ProjectionCurve().peak_minerals() == 0.0  # 空


def test_stalls():
    ev = [ProjectionEvent("completed", "FACTORY", 43),
          ProjectionEvent("stalled", "train", 50, "缺气"),
          ProjectionEvent("stalled", "build", 60, "缺矿"),
          ProjectionEvent("started", "train", 70, None)]
    c = ProjectionCurve(points=[_pt(0)], events=ev)
    s = c.stalls()
    assert len(s) == 2
    assert all(e.kind == "stalled" for e in s)
    assert {(e.type, e.reason) for e in s} == {("train", "缺气"), ("build", "缺矿")}
