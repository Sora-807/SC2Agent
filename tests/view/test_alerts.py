"""view.alerts（B8）：警报是唯一来源，判定与文案都在后端。

ADR-0007/0022 的反例明确禁止"前端自己根据资源数字写一套卡人口告警"，
所以这里必须测到：文案来自后端、阈值判定在后端、同一警报有冷却。
"""
from game.catalog import load_all
from game.geometry import Grid, Point2
from game.state import GameState, Order, Owner, Unit
from planner.curve import ProjectionCurve, ProjectionEvent, ProjectionPoint

from view.alerts import COOLDOWN_SECS, MINERAL_FLOAT, AlertService

CAT = load_all()


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
    assert blocked[0].severity == "info"        # 资源等待不标红（2026-08-22 拍板）


def test_severity_escalates_only_when_backend_says_warned():
    """升级到 error 的判定在后端（STALL_WARN_SECS），前端/警报层不自己算阈值（红线 C3）。"""
    svc = AlertService(catalog=CAT)
    production = {
        "queues": [{
            "name": "main", "head_status": "阻塞",
            "blocked": {"reason": "前置未满足 terran/barracks", "since": 0.0,
                        "frames": 999, "warned": True},
            "items": [{"index": 0, "stable_id": "terran/factory"}],
        }],
        "in_flight": [], "dropped": [],
    }
    a = svc.evaluate(_gs(100.0), production=production)[0]
    assert a.severity == "error"


def test_resource_wait_never_escalates_to_error():
    """顺序执行的资源等待（攒矿/攒气）是队列常态，不标红（用户拍板 2026-08-22）：
    即使超了 STALL_WARN 阈值也只留在 info —— 红色留给结构性卡死。"""
    for reason in ("晶体矿不足 50<150", "高能瓦斯不足（本帧余 0 < 100）", "缺矿", "缺气"):
        svc = AlertService(catalog=CAT)   # 每轮新实例：同 id 警报有冷却，不能跨轮复用
        production = {
            "queues": [{
                "name": "main", "head_status": "阻塞",
                "blocked": {"reason": reason, "since": 0.0, "frames": 999, "warned": True},
                "items": [{"index": 0, "stable_id": "terran/barracks"}],
            }],
            "in_flight": [], "dropped": [],
        }
        a = svc.evaluate(_gs(100.0), production=production)[0]
        assert a.severity == "info", reason


def test_supply_capped_from_dry_run_curve_with_eta():
    """干跑前瞻（D1/D7：supply_block → supply_capped）：规划里没排供给建筑 →
    卡人口点带 eta + 手动插 depot 建议。"""
    svc = AlertService(catalog=CAT)
    curve = _curve(points=[_point(100.0, 10, 15), _point(112.0, 15, 15)])
    supply = [a for a in svc.from_curve(curve, now=100.0) if a.kind == "supply_capped"]
    assert len(supply) == 1
    assert supply[0].eta == 12.0
    assert "12" in supply[0].text_zh and "补给站" in supply[0].text_zh


def test_supply_capped_ignores_maxed_cap():
    """cap 已满 200 时 used>=cap 不是"卡人口"（不误报）。"""
    svc = AlertService(catalog=CAT)
    curve = _curve(points=[_point(101.0, 200, 200)])
    assert not [a for a in svc.from_curve(curve, now=100.0) if a.kind == "supply_capped"]


def test_supply_capped_live_only_when_no_supply_queued():
    """live 面（D1）：已卡人口且队列/在途没排供给建筑才报；排了就闭嘴等它。"""
    svc = AlertService(catalog=CAT)
    gs = _gs(100.0, supply_used=15, supply_cap=15)  # 已卡人口
    prod = {"queues": [{"name": "main", "items": [
        {"uid": "q01", "op": "train", "stable_id": "terran/marine", "count": 2,
         "status": "pending"}]}], "in_flight": []}
    fired = [a for a in svc.evaluate(gs, production=prod) if a.kind == "supply_capped"]
    assert len(fired) == 1 and fired[0].payload["before_uid"] == "q01"
    # 队列里排了 depot → 不报（等它建好）
    prod2 = {"queues": [{"name": "main", "items": [
        {"uid": "q01", "op": "build", "stable_id": "terran/supplydepot", "count": 1,
         "status": "pending"},
        {"uid": "q02", "op": "train", "stable_id": "terran/marine", "count": 2,
         "status": "pending"}]}], "in_flight": []}
    assert not [a for a in svc.evaluate(_gs(101.0, supply_used=15, supply_cap=15),
                                        production=prod2)
                if a.kind == "supply_capped"]


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


def test_assembly_gap_reports_shortfall_against_target():
    """I12-B2：规划终局凑不齐装配 target → 前瞻警报（warn，带缺口数字）。"""
    from flow.manifest import parse_assembly

    asm = parse_assembly("""
id: a
groups:
  - group_id: G_INF
    display_name_zh: 步兵组
    composition:
      terran/marine: {min: 4, target: 10, max: 12}
""")
    pt = ProjectionPoint(t=300, minerals=0.0, gas=0.0, supply_used=10, supply_cap=50,
                         mineral_workers=0, gas_workers=0, buildings={},
                         units={"terran/marine": 4}, in_flight_count=0)
    alerts = AlertService(CAT).assembly_gaps(_curve([pt]), asm)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind == "assembly_gap" and a.severity == "warn"
    assert a.id == "assembly_gap/G_INF/terran/marine"
    assert "步兵组" in a.text_zh and "4" in a.text_zh and "10" in a.text_zh


def test_assembly_gap_silent_when_target_met_or_no_target():
    """达标不出声；没写 target 的组合项不参与对账（min/max 不是验收线）。"""
    from flow.manifest import parse_assembly

    asm = parse_assembly("""
id: a
groups:
  - group_id: G_INF
    composition:
      terran/marine: {min: 4, target: 10, max: 12}
      terran/siegetank: {min: 0, max: 4}
""")
    pt = ProjectionPoint(t=300, minerals=0.0, gas=0.0, supply_used=10, supply_cap=50,
                         mineral_workers=0, gas_workers=0, buildings={},
                         units={"terran/marine": 12, "terran/siegetank": 1},
                         in_flight_count=0)
    assert AlertService(CAT).assembly_gaps(_curve([pt]), asm) == []
    # 无曲线 / 无装配 → 空（live 窗口投影不该走这条对账）
    assert AlertService(CAT).assembly_gaps(None, asm) == []
    assert AlertService(CAT).assembly_gaps(_curve([pt]), None) == []


# ---------------- D 批（2026-08-24）：敌方踪迹滚动窗 + 活跃警报面 ----------------

def _enemy(tag, name="Marine", x=50.0, y=50.0) -> Unit:
    return Unit(tag=tag, type_name=name, position=Point2(x, y), owner=Owner.ENEMY,
                hp=100.0, hp_max=100.0, shield=0.0, energy=0.0,
                build_progress=1.0, orders=[])


def test_enemy_contact_rolls_over_10s_window():
    """敌方踪迹在窗内保留：离开视野后 10s 内仍可见统计（observe 只看当前帧）。"""
    svc = AlertService(catalog=CAT)
    fired = svc.evaluate(_gs(t=100.0, units=[_enemy(1), _enemy(2)]))
    contact = [a for a in fired if a.kind == "enemy_contact"]
    assert len(contact) == 1
    assert "见过 2 个不同敌兵" in contact[0].text_zh
    assert contact[0].severity == "info"          # 2 个 = info（没到 3）

    # 敌人离开视野：t=105 记忆仍在（冷却压住了重报 —— 直接看滚动表）
    svc.evaluate(_gs(t=105.0, units=[]))
    assert len(svc._contact) == 2
    # t=150：窗早已过，踪迹蒸发（此时冷却也过了 —— 有记忆才会报，没有才是对）
    fired = svc.evaluate(_gs(t=150.0, units=[]))
    assert not [a for a in fired if a.kind == "enemy_contact"]
    assert len(svc._contact) == 0


def test_enemy_contact_escalates_by_window_stats():
    """窗内 ≥3 个不同敌兵（或峰值同屏 ≥5）→ warn：够格叫醒 sleep。"""
    svc = AlertService(catalog=CAT)
    fired = svc.evaluate(_gs(t=100.0, units=[_enemy(1), _enemy(2), _enemy(3)]))
    contact = [a for a in fired if a.kind == "enemy_contact"]
    assert contact[0].severity == "warn"
    assert "峰值同屏 3" in contact[0].text_zh
    assert "@ (50,50)" in contact[0].text_zh        # 最后出现位置如实带上


def test_enemy_contact_ignores_neutral_units():
    """I25 兜底：owner=ENEMY 的岩石/矿脉（world 层漏网时）不算敌方踪迹。

    事故：白名单外的岩石 alliance=3 → Owner.ENEMY，每 10s 触发一次 warn 警报，
    把 sleep 叫醒又 observe 不到真威胁，困 Agent 空转整局。"""
    svc = AlertService(catalog=CAT)
    rocks = [_enemy(10 + i, name="DESTRUCTIBLEROCKTALL4X4", x=57.0, y=62.0) for i in range(5)]
    fired = svc.evaluate(_gs(t=100.0, units=rocks))
    assert not [a for a in fired if a.kind == "enemy_contact"]
    assert svc._contact == {}
    # 真敌兵照常报；与岩石混编时只数真敌兵
    fired = svc.evaluate(_gs(t=131.0, units=[_enemy(1), _enemy(2), _enemy(3)] + rocks))
    contact = [a for a in fired if a.kind == "enemy_contact"]
    assert len(contact) == 1
    assert "见过 3 个不同敌兵" in contact[0].text_zh


def test_active_alerts_exposes_recent_warn_plus():
    """活跃面：最近 15 游戏秒内报过的 warn+ 可查 —— sleep 轮询的唤醒数据源。"""
    svc = AlertService(catalog=CAT)
    svc.evaluate(_gs(t=100.0, units=[_enemy(1), _enemy(2), _enemy(3)]))
    hot = svc.active_alerts(105.0, min_severity="warn")
    assert [a["kind"] for a in hot] == ["enemy_contact"]
    assert hot[0]["severity"] == "warn"
    # 20s 冷却内不重报，但活跃面仍能查到（报过 = 在响）
    assert not svc.active_alerts(130.0, min_severity="warn")


# ---------------- E 批（2026-08-24）：TRAIN 卡死区分「被摧毁/还没建」（只告警） ----------------

def test_queue_blocked_distinguishes_destroyed_from_unbuilt():
    """producer_ever_ready=True（曾建成、被摧毁）→ 文案指向重排/重建；
    False（从没建过）→ 指向建造被卡。只给文案，不动队列（用户拍板）。"""
    base = {"name": "main", "head_status": "阻塞",
            "items": [{"stable_id": "terran/marine", "op": "train"}],
            "blocked": {"reason": "缺就绪产出建筑 terran/barracks",
                        "since": 90.0, "frames": 10, "warned": True}}
    for ever, mark in ((True, "被摧毁"), (False, "从没建成过")):
        svc = AlertService(catalog=CAT)   # 同 id 有冷却，两轮各用新实例
        production = {"queues": [{**base,
                                  "blocked": {**base["blocked"], "producer_ever_ready": ever}}]}
        fired = svc.evaluate(_gs(t=100.0), production=production)
        hit = [a for a in fired if a.kind == "queue_blocked"][0]
        assert mark in hit.text_zh
        assert hit.payload["producer_ever_ready"] is ever


def test_queue_blocked_destroyed_hint_checks_current_presence():
    """I27：ever=True 只表「曾建成」；建筑**当前在场**时绝不挂「大概率被摧毁」。

    事故（rec-20260825-012256）：矿不够/训练槽满的阻塞 + 兵营活着，整局报
    「产出建筑曾建成、现在不在——大概率被摧毁」，甚至同屏自相矛盾
    （「兵营就绪但订单已满」+「大概率被摧毁」），误导重建。"""
    base = {"name": "main", "head_status": "阻塞",
            "items": [{"stable_id": "terran/marine", "op": "train"}],
            "blocked": {"reason": "production_capacity：兵营 就绪但订单已满",
                        "since": 90.0, "frames": 10, "warned": True,
                        "producer_ever_ready": True}}
    svc = AlertService(catalog=CAT)
    # 兵营在场（已建成）→ 非被摧毁
    gs = _gs(t=100.0, units=[_unit(1, "BARRACKS")])
    hit = [a for a in svc.evaluate(gs, production={"queues": [base]})
           if a.kind == "queue_blocked"][0]
    assert "非被摧毁" in hit.text_zh
    assert "大概率被摧毁" not in hit.text_zh
    # 兵营真没了（曾建成、现在不在）→ 被摧毁提示照常给（这条 hint 的本职）
    svc2 = AlertService(catalog=CAT)
    hit2 = [a for a in svc2.evaluate(_gs(t=100.0), production={"queues": [base]})
            if a.kind == "queue_blocked"][0]
    assert "大概率被摧毁" in hit2.text_zh
