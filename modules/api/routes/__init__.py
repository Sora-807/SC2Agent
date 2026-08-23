"""api.routes：按资源分组的路由（REFACTOR G1 拆分产物）。

每个文件一个 `APIRouter` + 同形的一组端点；`api/app.py` 的 `create_app` 统一挂载。
辅助（帧源解析 / 命令门 / 沙盒泵）在 `api/state.py`，状态本体在 `app.state` 扁平键。
"""
from api.routes import (agent, commands, frames_ws, loadouts, map_plans, meta, plans,
                        proposals, recordings, session, sources, strategies)

#: 挂载顺序 = 声明顺序。路径无方法级冲突（`/api/plans/simulate` 是 POST，
#: 不会被 GET `/api/plans/{pid}` 抢走），新增路由组时 append 即可。
ROUTERS = (
    meta.router,
    sources.router,
    session.router,
    commands.router,
    agent.router,
    proposals.router,
    plans.router,
    recordings.router,
    strategies.router,
    map_plans.router,
    loadouts.router,
    frames_ws.router,
)
