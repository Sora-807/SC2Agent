"""帧源：清单 + 三个静态面 + 服务端 seek + 整份 JSONL（B2）。

设计对着前端的 `FrameSource`（`plan-frontend.md` §4）：每局一次的东西走 REST，
不挤 WS。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from api.state import require_source
from view.encode import to_json

router = APIRouter()


@router.get("/api/sources")
def sources(request: Request) -> list[dict]:
    state = request.app.state
    rows = [to_json(info) for info in state.registry.list()]
    sess = state.session
    if sess is not None:
        rows.insert(0, to_json(sess.info()))
    return rows


@router.get("/api/sources/{source_id}/statics")
def statics(source_id: str, request: Request) -> list[dict]:
    return require_source(request.app.state, source_id).statics()


@router.get("/api/sources/{source_id}/frames")
def frames_at(source_id: str, request: Request, game_time: float = Query(...)) -> list[dict]:
    """服务端 seek：每个 topic 给 `<= game_time` 的最后一帧（语义与前端 seek 逐字一致）。"""
    return require_source(request.app.state, source_id).latest_at(game_time)


@router.get("/api/sources/{source_id}/jsonl", response_class=PlainTextResponse)
def jsonl(source_id: str, request: Request) -> str:
    return require_source(request.app.state, source_id).path.read_text(encoding="utf-8")
