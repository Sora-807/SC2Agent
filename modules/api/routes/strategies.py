"""策略文件（二十七轮「开放写策略，免审」）：strategy + assembly 两段 YAML。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/api/strategies")
def strategies_list(request: Request) -> list[dict]:
    return request.app.state.strategies.list()


@router.get("/api/strategies/{sid}/doc")
def strategies_doc(sid: str, request: Request) -> dict:
    """文档形状（strategy + assembly 两段；agent 文件工作区读写的载体）。"""
    try:
        return request.app.state.strategies.doc(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有策略 {sid!r}") from None


@router.post("/api/strategies")
def strategies_create(body: dict, request: Request) -> dict:
    try:
        return request.app.state.strategies.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.put("/api/strategies/{sid}/doc")
def strategies_save_doc(sid: str, body: dict, request: Request) -> dict:
    """全量保存 + 编译期校验（parse/validate 全套，错误带 step 定位返回）。"""
    try:
        return request.app.state.strategies.save_doc(sid, body or {})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有策略 {sid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("/api/strategies/{sid}")
def strategies_delete(sid: str, request: Request) -> dict:
    try:
        request.app.state.strategies.remove(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有策略 {sid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}
