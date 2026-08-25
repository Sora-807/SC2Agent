"""api.routes.plans_simulate：干跑端点（PLAN-V2 批 3 的 simulate v2，N3 从 plans.py 抽出）。

四段输出的服务本体（队列来源解析/起点/放置近似/静态体检或投影/采样/终值/健康检查）
是 245 行的多阶段流程——独占一个路由文件，plans.py 留 CRUD 与模块模板。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from planner.opening import opening_game_state
from planner.planner import Planner
from view.adapt import projection_frame
from view.alerts import AlertService
from view.encode import to_json
from view.projection import queue_to_ops
from view.proposals import parse_item

router = APIRouter()


@router.post("/api/plans/simulate")
def plans_simulate(body: dict, request: Request) -> dict:
    """干跑 v2（PLAN-V2 批 3）：四段输出（采样/队列状态/终值/健康检查）。

    队列来源（优先级）：`items` > `plan_id` > `queue_name`（在线队列）/`from_session`。
    起点：`initial_state`（字符串=引用 initial-states/<id>，对象=内联）/
    `from_session`（当前会话导出）/ 缺省 = 0:00 标准开局。
    `horizon=0` = 静态体检（不跑投影：前置/产出建筑/人口对账，audit 路径，D2）。
    `sample_interval`（默认 10）/`sample_start`（默认 0）控制采样段。

    返回 = frame/projection（ProjectionBoard 照旧）+ v2 附加键：
    `samples`（采样点带 workers 五分/产位明细）、`queue_status`（uid/四值状态/
    起止时刻/reason —— 事件时间线已被它取代）、`final`（终值快照+产线明细+
    已完成升级，近似处如实标注）、`alerts`（健康检查）。
    """
    from game.catalog import load_all

    catalog = load_all()
    state = request.app.state
    try:
        horizon = min(600.0, max(0.0, float(body.get("horizon") if body.get("horizon") is not None else 300.0)))
        sample_interval = min(60, max(1, int(body.get("sample_interval") or 10)))
        sample_start = max(0.0, float(body.get("sample_start") or 0.0))
        # 队列键两名都收：`queue`（文档/agent v2 词表）与 `items`（v1 兼容）
        items = [parse_item(x) for x in (body.get("queue") or body.get("items") or [])]
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # ---- 队列来源 ----
    source_note = "draft"
    if not items and body.get("plan_id"):
        pid = str(body["plan_id"])
        doc = state.plans.get(pid)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"没有规划 {pid!r}")
        items = [parse_item(x) for x in (doc.get("queue") or [])]
        source_note = pid
    if not items and (body.get("queue_name") or body.get("from_session")):
        sess = state.session
        if sess is None:
            raise HTTPException(status_code=409, detail="没有运行中的会话（queue_name/from_session 要会话）")
        from api.session_export import export_snapshot
        exported = export_snapshot(sess, catalog)
        if body.get("from_session"):
            body.setdefault("_exported_initial", exported.get("initial_state"))
            items = [parse_item(x) for x in (exported.get("queue") or [])]
            source_note = "session"
        else:
            name = str(body.get("queue_name") or "main")
            items = list(sess.queue_items(name))
            source_note = f"queue:{name}"
    if not items:
        raise HTTPException(status_code=400, detail="没有可仿真的队列（items/plan_id/queue_name/from_session 至少给一个）")

    # ---- 起点（initial_state） ----
    initial_doc = body.get("_exported_initial")
    raw_init = body.get("initial_state")
    if raw_init is not None and initial_doc is None:
        if isinstance(raw_init, str):
            try:
                initial_doc = state.initial_states.get(raw_init)
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail=f"没有 initial-state {raw_init!r}（可用：{[r['id'] for r in state.initial_states.list()]}）") from None
        elif isinstance(raw_init, dict):
            initial_doc = raw_init
        else:
            raise HTTPException(status_code=400, detail="initial_state 只能是字符串（引用）或对象（内联）")
    initial_st = None
    if initial_doc is not None:
        from planner.initial_state import state_from_doc
        try:
            initial_st = state_from_doc(initial_doc, catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"initial_state 不合法：{exc}") from None

    # ---- 放置近似（用户拍板 2026-08-24：引用可选、报错不阻断、可关）----
    # placement=true（默认）→ 槽位建模：耗尽 skip(placement_collision)、
    # exact 标记不存在摘除进「未入仿」（仿真继续）；map_plan 指定图层来源
    #（缺省 = 出厂模板；from_session 优先会话默认规划）；placement=false 关掉。
    slot_pool = None
    placement_src = None
    if body.get("placement", True):
        from tactical_map.base import load_ladder_map
        from tactical_map.merge import load_plan_templates
        from planner.slots_model import SlotPool

        mp = body.get("map_plan")
        if not mp and (body.get("from_session") or body.get("queue_name")):
            sess0 = state.session
            mp = (getattr(sess0, "map_plan_id", None)
                  or getattr(sess0, "_map_default", None)) if sess0 else None
        templates = load_plan_templates(state.map_plans.dir)
        tpl = templates.get(mp) if mp else None
        if tpl is None:
            tpl = load_ladder_map()
            placement_src = "出厂模板"
        else:
            placement_src = str(mp)
        slot_pool = SlotPool.from_template(tpl, source_id=(str(mp) if tpl else None))

    # 草稿/规划文件项没有 uid（uid 是会话账本概念）—— 干跑也要状态表可引用：
    # 给缺 uid 的项分配显示用 q01…（在线队列项带真 uid，原样保留）
    for i, it in enumerate(items, start=1):
        if getattr(it, "uid", None) is None:
            it.uid = f"q{i:02d}"
    translated = queue_to_ops(items, catalog, slot_pool)

    # ---- horizon=0：静态体检（不跑投影；D2 的 audit 合并路径） ----
    if horizon <= 0:
        from planner.initial_state import static_check
        buildings = dict(initial_st.buildings) if initial_st is not None else {}
        cap = initial_st.supply_cap if initial_st is not None else 0
        # 终态历史项（会话队列带来的 completed/skipped）不进体检——已发生的事
        # 不再报「将来会卡」；状态表仍按真值列出（与跑投影路径的回填口径一致）
        live_items = [it for it in items
                      if getattr(it, "status", "pending") not in ("completed", "skipped")]
        alerts = static_check(live_items, catalog, buildings, cap)
        if slot_pool is not None:
            from game.production import PlacementExact
            for i, it in enumerate(live_items):
                p_ = getattr(it, "placement", None)
                if not isinstance(p_, PlacementExact):
                    continue
                e_ = catalog.by_stable_id(it.type) if it.type else None
                if e_ is None or not slot_pool.handles(e_):
                    continue
                mark = p_.mark
                if isinstance(mark, str) and "/" in mark and slot_pool.source_id:
                    prefix, _, bare = mark.partition("/")
                    mark = bare if prefix == slot_pool.source_id else None
                if mark and mark not in slot_pool.marks():
                    alerts.append({
                        "severity": "error", "kind": "placement_ref",
                        "text_zh": (f"uid={getattr(it, 'uid', None) or f'#{i}'} "
                                    f"placement 标记 {mark!r} 不在图层"
                                    f"（{slot_pool.source_label}）—— 改槽位名或换图层"),
                        "uid": getattr(it, "uid", None)})
        rows = [{"uid": getattr(it, "uid", None) or f"#{i}",
                 "item": f"{it.op.value} {it.type or ''}" + (f" ×{it.count}" if it.count > 1 else ""),
                 "status": getattr(it, "status", None) or "pending",
                 "started_at": it.started_at, "completed_at": it.completed_at,
                 "reason": getattr(it, "reason", None)}
                for i, it in enumerate(items)]
        return {"static": True, "queue_source": source_note, "alerts": alerts,
                "queue_status": rows,
                "skipped": [{"op": op, "reason": reason} for op, reason in translated.skipped]}

    # ---- 跑投影 ----
    planner = Planner(catalog)
    curve = planner.project(
        opening_game_state(catalog), list(translated.ops), horizon,
        until_complete=True, tail=30.0, initial=initial_st, slot_pool=slot_pool)
    # 账本化回归修的另一半（2026-08-25）：终态历史项不进仿真（泳道幻影条根因，
    # 见 view/projection.queue_to_ops），但状态表报告仍要覆盖整条会话队列 ——
    # completed/skipped 与全发射的 in_progress 项按运行时账本真值原样回填，
    # 不靠仿真重推（时间/reason 就是 runtime 记的原值）。
    ledger_rows = [
        {"uid": getattr(it, "uid", None) or f"#{i}",
         "item": f"{it.op.value} {it.type or ''}" + (f" ×{it.count}" if it.count > 1 else ""),
         "status": it.status,
         "started_at": it.started_at, "completed_at": it.completed_at,
         "reason": getattr(it, "reason", None), "detail": None}
        for i, it in enumerate(items)
        if getattr(it, "status", "pending") in ("completed", "skipped")
        or (getattr(it, "status", "pending") == "in_progress"
            and max(0, int(it.count)) <= 0)
    ] + list(curve.queue_status)
    sim_end = curve.points[-1].t if curve.points else horizon
    frame = projection_frame(
        curve, based_on_seq=0, based_on_game_time=0.0, horizon=sim_end,
        plan_id=str(body.get("plan_id") or source_note), skipped=translated.skipped)

    # 采样段：sample_start 起、每 interval 一行（extras 与 points 等长对齐）
    samples = []
    for pt, ex in zip(curve.points, curve.extras):
        if pt.t + 1e-9 < sample_start or pt.t > horizon + 1e-9:
            continue   # 采样窗 [sample_start, horizon]；until_complete 跑过头归曲线
        if round((pt.t - sample_start) % sample_interval) != 0:
            continue
        samples.append({
            "t": round(pt.t), "minerals": round(pt.minerals, 1), "gas": round(pt.gas, 1),
            "supply_used": pt.supply_used, "supply_cap": pt.supply_cap,
            "workers": ex["workers"], "producers": ex["producers"],
        })

    # 终值快照（近似处如实标注：产线明细按类型+挂件聚合 —— 仿真无逐栋身份）
    fs = curve.final_state
    fin_extra = planner._point_extra(fs)  # noqa: SLF001 —— 同一套派生单点
    detail_rows = []
    for sid, p in sorted(fin_extra["producers"].items()):
        producing = sorted({f.type for f in fs.in_flight
                            if f.kind == "train" and f.producer == sid})
        if p["with_techlab"]:
            detail_rows.append({"building": sid, "addon": "techlab",
                                "producing": producing if producing else None,
                                "queue": p["tech_busy"]})
        if p["with_reactor"]:
            detail_rows.append({"building": sid, "addon": "reactor",
                                "producing": producing if producing else None,
                                "queue": p["normal_busy"]})
        if p["bare"]:
            detail_rows.append({"building": sid, "addon": None,
                                "producing": producing if producing else None,
                                "queue": p["normal_busy"]})
    final = {
        "t": fs.t, "minerals": round(fs.minerals, 1), "gas": round(fs.gas, 1),
        "supply_used": fs.supply_used, "supply_cap": fs.supply_cap,
        "workers": fin_extra["workers"], "buildings": dict(fs.buildings),
        "units": dict(fs.units), "upgrades": sorted(fs.research_completed),
        "production_detail": detail_rows,
        "approx_note": "产线明细按类型+挂件聚合（仿真无逐栋建筑身份）",
    }

    # 健康检查：曲线警报 + 装配对账 + 账本 skip 项（uid 建议）
    svc = AlertService(catalog)
    from api.session import DEFAULT_ASSEMBLY
    from flow.manifest import parse_assembly
    from view.schema import AlertView

    assembly = parse_assembly(DEFAULT_ASSEMBLY)
    alerts = list(svc.from_curve(curve)) + list(svc.assembly_gaps(curve, assembly))
    for row in ledger_rows:
        if row["status"] != "skipped":
            continue
        advice = ("扩图层槽位（map-plans）或减少该类建筑数量"
                  if row["reason"] == "placement_collision"
                  else f"在 {row['uid']} 前插建造项，或移除该项")
        alerts.append(AlertView(
            id=f"skipped/{row['uid']}",
            kind=("placement_collision" if row["reason"] == "placement_collision"
                  else "prereq_missing"),
            severity="error",
            at=0.0, eta=None,
            text_zh=(f"uid={row['uid']} {row['item']} skipped：{row['detail'] or row['reason']}"
                     f" —— {advice}"),
            source="projection",
            payload={"uid": row["uid"], "reason": row["reason"]}))
    # 附加键不覆盖 frame 自带键（source/skipped 已在 frame 里）；queue_source 是来源说明
    return {**to_json(frame), "alerts": to_json(alerts),
            "queue_status": ledger_rows, "samples": samples,
            "final": final, "queue_source": source_note,
            "placement_source": placement_src}
