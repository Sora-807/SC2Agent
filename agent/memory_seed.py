"""agent.memory_seed：记忆工作区的随库种子（2026-08-23 用户拍板「目录丢了要能自动初始化」）。

种子 = `agent/seeds/` 下的工作区模板（runtime/ 整目录 gitignore，所以档案随版本库走，
与 `_lib.yaml` 出厂种子同一模式）。**只补缺失、绝不覆盖** —— 用户的修改永远优先；
补齐发生在 AgentTalk / 单回合 runner 装配工作区时，测试注入的 tmp 工作区不受影响。

模板本身是「格式的单一出处」：每个文件头部的注释就是它的格式约定（提示词只说
"格式见文件头部"），改格式改一处即可。
"""
from __future__ import annotations

from pathlib import Path

SEED_ROOT = Path(__file__).resolve().parent / "seeds"


def seed_memory_workspace(root: Path) -> list[str]:
    """把随库种子补进工作区（相对路径已存在的跳过）。返回补齐的相对路径列表。"""
    root = Path(root)
    created: list[str] = []
    if not SEED_ROOT.is_dir():
        return created    # 种子目录缺失（打包裁剪等）：如实什么都不做，不静默造空文件
    for src in sorted(SEED_ROOT.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(SEED_ROOT)
        dst = root / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(rel.as_posix())
    return created
