"""命令写入面（B6）：UI 与 agent 共用同一入口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.commands import CommandResult, QueueCommand, WorkerCommand
from api.state import guard_seq, live_session

router = APIRouter()


@router.post("/api/commands/queue/{op}", response_model=CommandResult)
def queue_command(op: str, body: QueueCommand, request: Request) -> CommandResult:
    state = request.app.state
    sess = live_session(state)
    guard_seq(sess, body.based_on_seq)
    try:
        parsed = body.to_items()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    from view.plans import resolve_placement_refs

    items, ref_err = resolve_placement_refs(
        parsed, getattr(sess, "map_plan_id", None))
    if ref_err is not None:
        # I8 限定引用（「规划名/点位名」）解析失败 = 请求写错了，400 带结构化理由
        raise HTTPException(status_code=400, detail=ref_err)
    try:
        detail = sess.queue_op(op, body.name, items=items,
                               index=body.index, order=body.order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return CommandResult(ok=True, detail=detail, accepted_seq=sess.seq)


@router.post("/api/commands/workers", response_model=CommandResult)
def worker_command(body: WorkerCommand, request: Request) -> CommandResult:
    sess = live_session(request.app.state)
    guard_seq(sess, body.based_on_seq)
    try:
        detail = sess.set_worker_target(body.task, body.count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return CommandResult(ok=True, detail=detail, accepted_seq=sess.seq)
