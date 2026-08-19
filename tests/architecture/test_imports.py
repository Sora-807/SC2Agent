"""架构依赖方向检查 + 环境冒烟。

红线（docs/需求文档-v0.1.md §0；ADR-0001）：
- game：唯一地基——几何原语 + Raw/GameState 两面 + Operation/OP_CATALOG + 生产队列
  schema + 边界契约（原顶层 ports 已并入 game.ports）+ catalog；零 sc2
- driver：只依赖 game（+ sc2，唯一 SC2 适配器）
- world：game（mechanics/tactical_map 待 D11/P4 启用）
- flow：game/tactical_map（constraint 后补）
- constraint：game/mechanics/tactical_map
- production：game/constraint/tactical_map（生产运行时；port duck-typing）
- planner：constraint/mechanics/game
- tactical_map：game
- mechanics：game

"ports" 出现在每个模块的禁止列表里 = 回归守卫：顶层 ports 模块已删除，
任何模块重新 import ports 即测试失败。
"""
import re
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"

# module -> 禁止 import 的顶层名（其余模块 + 第三方 sc2，driver 除外）
PROHIBITED = {
    "game":         {"ports", "tactical_map", "mechanics", "constraint", "planner", "world", "flow", "driver", "sc2"},
    "tactical_map": {"ports", "mechanics", "constraint", "planner", "world", "flow", "driver", "sc2"},
    "mechanics":    {"ports", "tactical_map", "constraint", "planner", "world", "flow", "driver", "sc2"},
    "constraint":   {"ports", "planner", "world", "flow", "driver", "sc2"},
    "planner":      {"ports", "tactical_map", "world", "flow", "driver", "sc2"},
    "world":        {"ports", "tactical_map", "mechanics", "constraint", "planner", "flow", "driver", "sc2"},
    "flow":         {"ports", "mechanics", "constraint", "planner", "world", "driver", "sc2"},
    "production":   {"ports", "mechanics", "planner", "world", "flow", "driver", "sc2"},
    "driver":       {"ports", "tactical_map", "mechanics", "constraint", "planner", "world", "flow", "production"},
}

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)")


def _module_files(module: str) -> list[Path]:
    d = MODULES_DIR / module
    return list(d.rglob("*.py")) if d.is_dir() else []


def _imports_in(path: Path) -> set[str]:
    found: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _IMPORT_RE.match(line)
        if m:
            found.add(m.group(1))
    return found


def test_modules_exist():
    for module in PROHIBITED:
        assert (MODULES_DIR / module).is_dir(), f"missing module dir: {module}"


def test_ports_module_removed():
    """顶层 ports 已并入 game.ports（docs/模块审查.md 建议）；残留即回归。"""
    assert not (MODULES_DIR / "ports").exists(), "modules/ports 应已并入 game.ports"


def test_no_prohibited_imports():
    violations = []
    for module, prohibited in PROHIBITED.items():
        for f in _module_files(module):
            bad = _imports_in(f) & prohibited
            if bad:
                rel = f.relative_to(MODULES_DIR)
                violations.append(f"{rel}: imports {sorted(bad)} (prohibited)")
    assert not violations, "dependency red-line violations:\n" + "\n".join(violations)


def test_sc2_importable():
    """环境冒烟：driver 的 SC2 库可 import。"""
    import sc2  # noqa: F401
