"""agent.bootstrap：发布态默认工作区（PLAN-V2 批 5「初始化模块」，新2）。

泛化 memory_seed 的「只补缺失」：首运/启动确保默认库存存在 ——
系统提示词快照（system/prompt.md，来自 spec.SYSTEM_PROMPT —— 用户改 spec 后
快照跟着补）+ 工作区设计文档（workspace.md 指路各虚拟区）。用户的修改永远优先。
"""
from __future__ import annotations

from pathlib import Path

_DESIGN_DOC = """# 工作区地图

- `production-plans/<id>.yaml`  生产规划（队列）—— 读写（旧名 plans/ 仍可读）
- `map-plans/<id>.yaml`         地图规划（双分支槽位）—— 读写
- `initial-states/<id>.yaml`    状态快照（simulate 起点 / 会话导出）—— 读写
- `strategies/<id>.yaml`        策略 —— 读写
- `catalog/`                    三族数据手册 —— 只读（从活 catalog 渲染，零漂移）
- `maps/<源>/<bbox>.md`         格点网格 —— 只读
- `memory/`、`scratch/`         记忆与自留地 —— 读写
- `recordings/`、`system/`      对局录像 / 系统说明 —— 只读
"""


def ensure_defaults(workspace_root: Path) -> list[str]:
    """只补缺失（返回本次创建的相对路径清单；幂等）。"""
    created: list[str] = []
    ws = Path(workspace_root)
    design = ws / "workspace.md"
    if not design.is_file():
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text(_DESIGN_DOC, encoding="utf-8")
        created.append("workspace.md")
    from agent.spec import SYSTEM_PROMPT

    prompt = ws / "system" / "prompt.md"
    if not prompt.is_file():
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(SYSTEM_PROMPT, encoding="utf-8")
        created.append("system/prompt.md")
    return created
