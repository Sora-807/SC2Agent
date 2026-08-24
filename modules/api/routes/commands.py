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
    # placement 引用（裸名 / 规划id/点位名）原样直通：会话图层带全部规划的
    # 命名空间键（PLAN-V2 批 2），解析在 runtime 的 placement 层；不存在的
    # 标记执行期 dropped（作者错误，不静默）。
    items = parsed
    try:
        detail = sess.queue_op(op, body.name, items=items,
                               before_uid=body.before_uid, uid=body.uid,
                               order=body.order)
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
