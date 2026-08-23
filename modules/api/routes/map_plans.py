"""地图规划文件（P2 切片 1）：默认地图锁定 + 复制新建 + 出生点读取。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/api/map-plans")
def map_plans_list(request: Request) -> list[dict]:
    return request.app.state.map_plans.list()


@router.get("/api/map-plans/{pid}")
def map_plans_payload(pid: str, request: Request) -> dict:
    """该规划的 static/map 形状 payload（规划自带地图+出生点，画布直接渲染）。"""
    try:
        return request.app.state.map_plans.payload(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/api/map-plans/{pid}/doc")
def map_plans_doc(pid: str, request: Request) -> dict:
    """文档形状（agent 文件工作区把地图规划当 YAML 文件读写的载体）。"""
    try:
        return request.app.state.map_plans.doc(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None


@router.put("/api/map-plans/{pid}/doc")
def map_plans_save_doc(pid: str, body: dict, request: Request) -> dict:
    """全量保存文档（agent 文件工作区的写路径；校验口径与 hunks 保存一致）。"""
    try:
        out = request.app.state.map_plans.save_payload(pid, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)   # 结构化 errors 给 agent
    return out


@router.post("/api/map-plans")
def map_plans_create(body: dict, request: Request) -> dict:
    try:
        return request.app.state.map_plans.create(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.put("/api/map-plans/{pid}")
def map_plans_save(pid: str, body: dict, request: Request) -> dict:
    """离线保存：hunks 应用到该规划（与 map_plan 提案同一套校验，不走审批）。"""
    try:
        out = request.app.state.map_plans.save(pid, list(body.get("hunks") or []))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)   # 结构化 errors 给前端/agent
    return out


@router.delete("/api/map-plans/{pid}")
def map_plans_delete(pid: str, request: Request) -> dict:
    try:
        request.app.state.map_plans.remove(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有地图规划 {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}
