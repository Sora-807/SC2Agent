"""规划（P0）：离线生产规划的文件存储 + 无会话干跑 + 模块模板（I12-B3）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from planner.opening import opening_game_state
from planner.planner import Planner
from view.adapt import projection_frame
from view.alerts import AlertService
from view.encode import to_json
from view.projection import queue_to_ops
from view.proposals import parse_item

router = APIRouter()


@router.get("/api/plans")
def plans_list(request: Request) -> list[dict]:
    return request.app.state.plans.list()


@router.get("/api/plans/{pid}")
def plans_get(pid: str, request: Request) -> dict:
    p = request.app.state.plans.get(pid)
    if p is None:
        raise HTTPException(status_code=404, detail=f"没有规划 {pid!r}")
    return p


@router.post("/api/plans")
def plans_create(body: dict, request: Request) -> dict:
    try:
        return request.app.state.plans.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.put("/api/plans/{pid}")
def plans_save(pid: str, body: dict, request: Request) -> dict:
    try:
        return request.app.state.plans.save(pid, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("/api/plans/{pid}")
def plans_delete(pid: str, request: Request) -> dict:
    try:
        request.app.state.plans.remove(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有规划 {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


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

    # 草稿/规划文件项没有 uid（uid 是会话账本概念）—— 干跑也要状态表可引用：
    # 给缺 uid 的项分配显示用 q01…（在线队列项带真 uid，原样保留）
    for i, it in enumerate(items, start=1):
        if getattr(it, "uid", None) is None:
            it.uid = f"q{i:02d}"
    translated = queue_to_ops(items, catalog)

    # ---- horizon=0：静态体检（不跑投影；D2 的 audit 合并路径） ----
    if horizon <= 0:
        from planner.initial_state import static_check
        buildings = dict(initial_st.buildings) if initial_st is not None else {}
        cap = initial_st.supply_cap if initial_st is not None else 0
        alerts = static_check(items, catalog, buildings, cap)
        rows = [{"uid": getattr(it, "uid", None) or f"#{i}",
                 "item": f"{it.op.value} {it.type or ''}" + (f" ×{it.count}" if it.count > 1 else ""),
                 "status": "pending", "started_at": None, "completed_at": None,
                 "reason": None}
                for i, it in enumerate(items)]
        return {"static": True, "queue_source": source_note, "alerts": alerts,
                "queue_status": rows,
                "skipped": [{"op": op, "reason": reason} for op, reason in translated.skipped]}

    # ---- 跑投影 ----
    planner = Planner(catalog)
    curve = planner.project(
        opening_game_state(catalog), list(translated.ops), horizon,
        until_complete=True, tail=30.0, initial=initial_st)
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
    for row in curve.queue_status:
        if row["status"] != "skipped":
            continue
        alerts.append(AlertView(
            id=f"skipped/{row['uid']}", kind="prereq_missing", severity="error",
            at=0.0, eta=None,
            text_zh=(f"uid={row['uid']} {row['item']} skipped：{row['detail'] or row['reason']}"
                     f" —— 在 {row['uid']} 前插建造项，或移除该项"),
            source="projection",
            payload={"uid": row["uid"], "reason": row["reason"]}))
    # 附加键不覆盖 frame 自带键（source/skipped 已在 frame 里）；queue_source 是来源说明
    return {**to_json(frame), "alerts": to_json(alerts),
            "queue_status": curve.queue_status, "samples": samples,
            "final": final, "queue_source": source_note}


# ---- 模块模板（I12-B3 最小版）：参考模块一键落地成规划文件 ----

@router.get("/api/modules")
def modules_list() -> list[dict]:
    """内置生产模块（参考战术库）。B3 的模板源：MODULE_REGISTRY 此前只有 agent 只读，
    现在能落地成 plans/<id>.yaml —— 消除「模块与规划两份独立副本各自漂移」。"""
    import planner  # noqa: F401  触发内置模块注册
    from planner.build_order import MODULE_REGISTRY
    from view.plans import ops_to_items

    out = []
    for ref, fn in sorted(MODULE_REGISTRY.items()):
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        out.append({"id": ref, "title_zh": doc, "items": len(ops_to_items(fn({})))})
    return out


@router.post("/api/plans/from-module")
def plans_from_module(body: dict, request: Request) -> dict:
    """从参考模块新建规划：`{module, params?, id?, title_zh?}`。

    默认规划就是这么来的（bio_tank_opening 导出）；现在 UI/agent 都能走同一条路
    —— 模块改了重新落地一份，比手抄队列靠谱（模板是唯一真相源）。
    """
    import planner  # noqa: F401  触发内置模块注册
    from planner.build_order import MODULE_REGISTRY
    from view.plans import ops_to_items
    from view.proposals import item_to_json

    ref = str(body.get("module") or "")
    fn = MODULE_REGISTRY.get(ref)
    if fn is None:
        raise HTTPException(status_code=400,
                            detail=f"没有模块 {ref!r}（GET /api/modules 看有哪些）")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params 必须是对象")
    try:
        items = ops_to_items(fn(params))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"模块参数不合法：{exc}") from None
    try:
        return request.app.state.plans.create({
            "id": body.get("id") or f"{ref}-{uuid.uuid4().hex[:4]}",
            "title_zh": str(body.get("title_zh") or f"{ref}（模板落地）"),
            "queue": [item_to_json(i) for i in items],
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
