"""view.alerts（B8）：警报是唯一来源，判定与文案都在后端。

ADR-0007/0022 的反例明确禁止"前端自己根据资源数字写一套卡人口告警"，
所以这里必须测到：文案来自后端、阈值判定在后端、同一警报有冷却。
"""
from game.catalog import load_terran
from game.geometry import Grid, Point2
from game.state import GameState, Order, Owner, Unit
from planner.curve import ProjectionCurve, ProjectionEvent, ProjectionPoint

from view.alerts import COOLDOWN_SECS, MINERAL_FLOAT, AlertService

CAT = load_terran()


def _gs(t: float = 100.0, units=(), **kw) -> GameState:
    g = Grid(1, 1, [[0]])
    base = dict(minerals=100, vespene=0, supply_used=10, supply_cap=15)
    base.update(kw)
    return GameState(seq=int(t), game_time=t, units=list(units), map_size=(176, 160),
                     creep=g, visibility=g, **base)


def _unit(tag, name, ready=True, orders=()) -> Unit:
    return Unit(tag=tag, type_name=name, position=Point2(1.0, 1.0), owner=Owner.SELF,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0,
                build_progress=1.0 if ready else 0.4, orders=list(orders))


def _curve(points=(), events=()) -> ProjectionCurve:
    return ProjectionCurve(points=list(points), events=list(events))


def _point(t, used, cap) -> ProjectionPoint:
    return ProjectionPoint(t=t, minerals=0.0, gas=0.0, supply_used=used, supply_cap=cap,
                           mineral_workers=0, gas_workers=0, buildings={}, units={},
                           in_flight_count=0)


def test_queue_blocked_text_comes_from_backend_reason():
    """文案由后端拼，含 catalog 中文名 + 门控给的原因（前端零文案，红线 C4）。"""
    svc = AlertService(catalog=CAT)
    production = {
        "queues": [{
            "name": "main", "head_status": "阻塞",
            "blocked": {"reason": "高能瓦斯不足（本帧余 0 < 100）", "since": 70.0,
                        "frames": 30, "warned": False},
            "items": [{"index": 0, "stable_id": "terran/factory"}],
        }],
        "in_flight": [], "dropped": [],
    }
    alerts = svc.evaluate(_gs(100.0), production=production)
    blocked = [a for a in alerts if a.kind == "queue_blocked"]
    assert len(blocked) == 1
    assert "工厂" in blocked[0].text_zh          # catalog 中文名
    assert "高能瓦斯不足" in blocked[0].text_zh   # 后端门控原因
    assert "30" in blocked[0].text_zh            # 已阻塞时长
    assert blocked[0].severity == "warn"


def test_severity_escalates_only_when_backend_says_warned():
    """升级到 error 的判定在后端（STALL_WARN_SECS），前端/警报层不自己算阈值（红线 C3）。"""
    svc = AlertService(catalog=CAT)
    production = {
        "queues": [{
            "name": "main", "head_status": "阻塞",
            "blocked": {"reason": "缺矿", "since": 0.0, "frames": 999, "warned": True},
            "items": [{"index": 0, "stable_id": "terran/barracks"}],
        }],
        "in_flight": [], "dropped": [],
    }
    a = svc.evaluate(_gs(100.0), production=production)[0]
    assert a.severity == "error"


def test_supply_block_predicted_from_projection_with_eta():
    svc = AlertService(catalog=CAT)
    curve = _curve(points=[_point(100.0, 10, 15), _point(112.0, 15, 15)])
    alerts = svc.evaluate(_gs(100.0), curve=curve)
    supply = [a for a in alerts if a.kind == "supply_block"]
    assert len(supply) == 1
    assert supply[0].eta == 12.0
    assert "12" in supply[0].text_zh


def test_supply_block_ignores_maxed_cap():
    """cap 已满 200 时 used>=cap 不是"卡人口"（不误报）。"""
    svc = AlertService(catalog=CAT)
    curve = _curve(points=[_point(101.0, 200, 200)])
    assert not [a for a in svc.evaluate(_gs(100.0), curve=curve) if a.kind == "supply_block"]


def test_prereq_missing_reason_passed_through_not_invented():
    svc = AlertService(catalog=CAT)
    curve = _curve(events=[ProjectionEvent(kind="stalled", type="terran/siegetank",
                                           t=110.0, reason="前置没有：terran/techlab")])
    a = [x for x in svc.evaluate(_gs(100.0), curve=curve) if x.kind == "prereq_missing"]
    assert len(a) == 1
    assert "前置没有：terran/techlab" in a[0].text_zh
    assert "攻城坦克" in a[0].text_zh


def test_mineral_float_threshold():
    svc = AlertService(catalog=CAT)
    assert not [a for a in svc.evaluate(_gs(minerals=MINERAL_FLOAT - 1)) if a.kind == "mineral_float"]
    svc2 = AlertService(catalog=CAT)
    got = [a for a in svc2.evaluate(_gs(minerals=MINERAL_FLOAT + 50)) if a.kind == "mineral_float"]
    assert len(got) == 1 and str(MINERAL_FLOAT + 50) in got[0].text_zh


def test_line_idle_detects_ready_producers_without_orders():
    svc = AlertService(catalog=CAT)
    units = [
        _unit(1, "BARRACKS"),                                  # 就绪且空闲 → 报
        _unit(2, "BARRACKS", orders=[Order(ability="MARINE")]),  # 在生产 → 不报
        _unit(3, "BARRACKS", ready=False),                      # 在建 → 不报
        _unit(4, "MARINE"),                                     # 不是产出建筑 → 不报
    ]
    got = [a for a in svc.evaluate(_gs(units=units)) if a.kind == "line_idle"]
    assert len(got) == 1
    assert got[0].payload["tags"] == [1]
    assert "兵营" in got[0].text_zh
    assert "兵营 " not in got[0].text_zh, "join 时不该留下悬空空格"


def test_cooldown_suppresses_repeats_but_lets_it_through_later():
    """1Hz 求值下不冷却会把时间线刷满。"""
    svc = AlertService(catalog=CAT)
    first = svc.evaluate(_gs(100.0, minerals=MINERAL_FLOAT + 1))
    assert [a for a in first if a.kind == "mineral_float"]
    again = svc.evaluate(_gs(100.0 + COOLDOWN_SECS - 1, minerals=MINERAL_FLOAT + 1))
    assert not [a for a in again if a.kind == "mineral_float"]
    later = svc.evaluate(_gs(100.0 + COOLDOWN_SECS + 1, minerals=MINERAL_FLOAT + 1))
    assert [a for a in later if a.kind == "mineral_float"]


def test_no_alerts_when_everything_fine():
    svc = AlertService(catalog=CAT)
    assert svc.evaluate(_gs(minerals=100, vespene=0)) == []
