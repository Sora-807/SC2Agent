"""元信息：健康检查 + flow 词表（不依赖任何会话，编辑器可以先加载）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from view.encode import to_json
from view.schema import REV, TOPICS
from view.statics import schema_static

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict:
    state = request.app.state
    return {"ok": True, "rev": REV, "topics": list(TOPICS),
            "frame_dir": str(state.registry.root), "sources": state.registry.ids()}


@router.get("/api/schema")
def schema() -> dict:
    """flow 词表：不依赖任何会话，编辑器可以先加载（形状同 `static/schema` 的 payload）。"""
    return to_json(schema_static())
