"""initial-state 快照文件（PLAN-V2 批 3，I2）：清单/读/全量写/删。

与 plans/map-plans 同一套 CRUD 风格；校验在 store 侧（catalog/工人分项/supply_cap
对账），400 带结构化理由。example-* 前缀锁定（出厂示例，复制再改）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _store(request: Request):
    st = getattr(request.app.state, "initial_states", None)
    if st is None:
        raise HTTPException(status_code=503, detail="initial-states 未启用（检查 --initial-states 目录）")
    return st


@router.get("/api/initial-states")
def initial_states_list(request: Request) -> list[dict]:
    return _store(request).list()


@router.get("/api/initial-states/{pid}")
def initial_states_get(pid: str, request: Request) -> dict:
    try:
        return _store(request).get(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有 initial-state {pid!r}") from None


@router.put("/api/initial-states/{pid}")
def initial_states_save(pid: str, body: dict, request: Request) -> dict:
    from game.catalog import load_all

    try:
        out = _store(request).save(pid, body, load_all())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@router.delete("/api/initial-states/{pid}")
def initial_states_delete(pid: str, request: Request) -> dict:
    try:
        _store(request).remove(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"没有 initial-state {pid!r}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}
