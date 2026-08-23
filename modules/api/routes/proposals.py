"""提案（B7）：agent 的唯一产出面。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/api/proposals")
def proposals_list(request: Request) -> list[dict]:
    return request.app.state.proposals.list()


@router.post("/api/proposals")
def proposals_create(body: dict, request: Request) -> dict:
    """新建提案。**校验不通过也存**（§6 P2：不可接受，但必须可见 —— agent 要学、用户要诊断）。

    审批已停用（2026-08-22 用户拍板）：校验通过的提案**创建后立即自动应用**，
    decision.auto=True 留审计。accept/reject 端点与 ProposalStore 的审批能力
    原样保留（休眠代码）—— 想恢复人工审批，删掉下面这段自动应用即可。
    """
    store = request.app.state.proposals
    try:
        p = store.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if (p.validation or {}).get("ok"):
        try:
            p = store.accept(p.id, auto=True)
        except ValueError:
            pass  # 应用失败（如 offline 无会话）：留在待审批，提案本身已落盘可见
    return p.to_json()


@router.get("/api/proposals/{pid}/preview")
def proposals_preview(pid: str, request: Request,
                      horizon: float = Query(120.0, gt=0, le=600)) -> dict:
    """双投影：当前队列 vs 提案后的队列 —— 接受前先看未来。"""
    try:
        return request.app.state.proposals.preview_pair(pid, horizon=horizon)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/api/proposals/{pid}/accept")
def proposals_accept(pid: str, request: Request, body: dict | None = None) -> dict:
    payload = body or {}
    try:
        p = request.app.state.proposals.accept(
            pid, hunk_ids=payload.get("hunk_ids"), comment=payload.get("comment_zh"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        # 409：不是请求写错了，而是提案的状态/校验不允许接受
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return p.to_json()


@router.post("/api/proposals/{pid}/reject")
def proposals_reject(pid: str, request: Request, body: dict) -> dict:
    try:
        p = request.app.state.proposals.reject(pid, str(body.get("comment_zh") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return p.to_json()
