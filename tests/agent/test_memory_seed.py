"""记忆工作区种子（agent/memory_seed.py）：丢失自动初始化、绝不覆盖用户修改。

2026-08-25（I31/I33）：提示词模板（system/prompt.md + templates/*）从 runtime 迁入
seeds —— 提示词面进版本库（D16：改模板=改提示词），eval 的全新工作区与真机同面。
"""
from pathlib import Path

from agent.memory_seed import SEED_ROOT, seed_memory_workspace

SEED_FILES = [
    "improvement-notes.md",
    "memory/replays/README.md",
    "memory/strategy-notes.md",
    "memory/system-capabilities.md",
    "memory/user-preferences.md",
    "session/current.md",
    "system/prompt.md",
    "templates/catalog-structure.md",
    "templates/observe-output.md",
    "templates/simulate-plan-v2-output.md",
    "workspace.md",
]


def test_seeds_are_archived_in_repo():
    """随库存档齐全（runtime/ 整目录 gitignore —— 种子不在版本库里就等于丢）。"""
    assert sorted(p.relative_to(SEED_ROOT).as_posix() for p in SEED_ROOT.rglob("*")
                  if p.is_file()) == SEED_FILES


def test_empty_workspace_gets_seeded(tmp_path: Path):
    created = seed_memory_workspace(tmp_path)
    assert sorted(created) == SEED_FILES
    # 内容 = 模板本体（头部注释即格式约定，单一出处）
    notes = (tmp_path / "memory" / "strategy-notes.md").read_text(encoding="utf-8")
    assert "格式" in notes and "[E1]" in notes
    caps = (tmp_path / "memory" / "system-capabilities.md").read_text(encoding="utf-8")
    assert "system/surface.md" in caps      # 种子里不残留已退役的 write_surface 字样


def test_existing_files_are_never_overwritten(tmp_path: Path):
    mine = tmp_path / "memory" / "user-preferences.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("# 我自己改过的版本\n", encoding="utf-8")
    created = seed_memory_workspace(tmp_path)
    assert "memory/user-preferences.md" not in created
    assert mine.read_text(encoding="utf-8") == "# 我自己改过的版本\n"
    # 其余缺失的照补
    assert "memory/strategy-notes.md" in created


def test_seeding_is_idempotent(tmp_path: Path):
    assert seed_memory_workspace(tmp_path)
    assert seed_memory_workspace(tmp_path) == []
