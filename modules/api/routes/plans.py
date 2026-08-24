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
def plans_simulate(body: dict) -> dict:
    """离线干跑（P0）：标准开局种子 + 真 planner 投影 —— **不需要会话**。

    与提案预览（`/api/proposals/{id}/preview`，要会话当起点）互补：规划是
    authoring 数据，仿真只需要开局种子。前端与 agent 都走这里（A3：不本地算）。
    请求体 `{items: [{op,type,count,placement?,task?}], horizon?, plan_id?}`；
    返回 frame/projection 的 payload —— ProjectionBoard 直接渲染，
    stalled 事件即「前瞻警报」（缺矿/缺气/前置没……）。
    """
    try:
        horizon = min(600.0, max(1.0, float(body.get("horizon") or 300.0)))
        items = [parse_item(x) for x in (body.get("items") or [])]
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    from game.catalog import load_all

    catalog = load_all()
    translated = queue_to_ops(items, catalog)
    # 二十三轮用户拍板：跑到队列完成 —— 曲线不再在生产中途截断（死局有封顶）；
    # 二十七轮：尾部再多留 30 秒（最后事件完成后看得到经济余势，右缘钳制有自然末端）
    # H 批（2026-08-24）：auto_supply 默认关 —— 投影不替人补供给，卡人口真实浮出
    curve = Planner(catalog).project(
        opening_game_state(catalog), list(translated.ops), horizon,
        until_complete=True, tail=30.0,
        auto_supply=bool(body.get("auto_supply", False)))
    sim_end = curve.points[-1].t if curve.points else horizon
    frame = projection_frame(
        curve, based_on_seq=0, based_on_game_time=0.0, horizon=sim_end,
        plan_id=str(body.get("plan_id") or "draft"), skipped=translated.skipped)
    # 前瞻警报与实时警报同一数据模型（AlertView）、同一渲染组件 —— 干跑无冷却
    svc = AlertService(catalog)
    # I12-B2：装配 target ↔ 规划总产出对账 —— 会话装配是规划的验收方
    # （V1 恒为 DEFAULT_ASSEMBLY；装配可配置后跟着走）。缺口显形，不再靠肉眼对照。
    from api.session import DEFAULT_ASSEMBLY
    from flow.manifest import parse_assembly

    assembly = parse_assembly(DEFAULT_ASSEMBLY)
    alerts = svc.from_curve(curve) + svc.assembly_gaps(curve, assembly)
    return {**to_json(frame), "alerts": to_json(alerts)}


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
