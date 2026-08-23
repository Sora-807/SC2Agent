"""装配清单（loadout，B1）：`runtime/loadouts/<id>.yaml` 的只读清单。

loadout = 三件套引用（map_plan / strategy / plan[+spawn]），`POST /api/session/start?loadout=`
解析它并自动把生产序列 submit 入队。没有写面 —— 文件由人管（与锁定预设同一姿态）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/loadouts")
def loadouts_list(request: Request) -> list[dict]:
    """清单（StartCard 的下拉与 `start_session(loadout=)` 都吃它）。"""
    return request.app.state.loadouts.list()
