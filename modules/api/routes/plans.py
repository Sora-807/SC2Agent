"""规划（P0）：离线生产规划的文件存储 + 无会话干跑 + 模块模板（I12-B3）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

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
