"""架构依赖方向检查 + 环境冒烟。

红线（docs/contract/需求文档-v0.1.md §0；ADR-0001）：
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
- view：只读视图层，在 flow/production/planner/tactical_map **之上**；可依赖它们，
  但零 driver / 零 sc2（view 不认识 SC2，也不拥有游戏循环）；**没有任何下层模块 import view**

"ports" 出现在每个模块的禁止列表里 = 回归守卫：顶层 ports 模块已删除，
任何模块重新 import ports 即测试失败。

顶层包（modules/ 之外，2026-08-25 补守卫）：
- agent：引擎之外的消费者。可读 view/tactical_map/planner/constraint/game 的读模型与
  vendor agentic；禁止碰 api（传输）/driver+sc2（驱动与游戏循环）/flow/production/
  world/mechanics（引擎内部）——命令走 HTTP 客户端，观测走读模型，不内嵌引擎
- eval：评测消费者，地位同 tests——可以 import 任何项目代码；反向禁止：
  modules/ 与 agent/ 不认识 eval（评测框架渗进被测对象即失效）
- voice：语音栈是可附加 sidecar（本地 WIP，未入库）。引擎与 agent 永不 import 它；
  若日后回库，此规则防止它长进引擎依赖
- api → agent 只许 lazy：装配根（create_app 内）可构造 agent 运行时，但模块级
  import 即传输层反向硬依赖运行时，测试失败
"""
import re
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"
AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"

# module -> 禁止 import 的顶层名（其余模块 + 第三方 sc2，driver 除外）
PROHIBITED = {
    "game":         {"ports", "tactical_map", "mechanics", "constraint", "planner", "world", "flow", "production", "view", "driver", "sc2", "api"},
    "tactical_map": {"ports", "mechanics", "constraint", "planner", "world", "flow", "production", "view", "driver", "sc2", "api"},
    "mechanics":    {"ports", "tactical_map", "constraint", "planner", "world", "flow", "production", "view", "driver", "sc2", "api"},
    "constraint":   {"ports", "planner", "world", "flow", "production", "view", "driver", "sc2", "api"},
    "planner":      {"ports", "tactical_map", "world", "flow", "production", "view", "driver", "sc2", "api"},
    "world":        {"ports", "tactical_map", "mechanics", "constraint", "planner", "flow", "production", "view", "driver", "sc2", "api"},
    "flow":         {"ports", "mechanics", "constraint", "planner", "world", "production", "view", "driver", "sc2", "api"},
    "production":   {"ports", "mechanics", "planner", "world", "flow", "view", "driver", "sc2", "api"},
    "driver":       {"ports", "tactical_map", "mechanics", "constraint", "planner", "world", "flow", "production", "view", "api"},
    # view 是最上层的只读视图：可依赖所有引擎模块，但不认识 SC2，也不拥有游戏循环
    "view":         {"ports", "driver", "sc2", "api"},
    # api 是最外层传输：可依赖 view 与引擎,但同样不认识 SC2、不拥有游戏循环
    "api":          {"ports", "driver", "sc2"},
}

_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)")
_MODULE_LEVEL_IMPORT_RE = re.compile(r"^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)")

# agent 包禁止 import 的顶层名（见模块 docstring：消费者边界）
AGENT_PROHIBITED = {"ports", "api", "driver", "sc2", "flow", "production", "world", "mechanics", "eval", "voice"}


def _module_files(module: str) -> list[Path]:
    d = MODULES_DIR / module
    return list(d.rglob("*.py")) if d.is_dir() else []


def _pkg_files(root: Path) -> list[Path]:
    return list(root.rglob("*.py")) if root.is_dir() else []


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
    """顶层 ports 已并入 game.ports（已并入 game.ports）；残留即回归。"""
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


def test_view_schema_depends_only_on_game():
    """view.schema 是契约的数据模型，必须能独立于任何引擎重构演进。

    "从引擎取数"隔离在 view.statics / view.adapt；schema 只碰 game 的类型。
    这条守住了，flow/planner 的重构就永远不会波及契约定义本身。
    """
    f = MODULES_DIR / "view" / "schema.py"
    assert f.is_file(), "missing modules/view/schema.py"
    engine = {"flow", "production", "planner", "constraint", "tactical_map", "mechanics", "world", "driver"}
    bad = _imports_in(f) & engine
    assert not bad, f"view/schema.py 只应依赖 game，却 import 了 {sorted(bad)}"


def test_nobody_imports_view_or_api():
    """没有任何下层模块 import view / api（否则视图与传输层会反向污染引擎）。

    `api` 可以 import `view`，但 `view` 不能 import `api` —— 传输方式（WS/REST/文件）
    不该渗进视图定义里，否则换传输就要动帧。
    """
    violations = []
    for module in PROHIBITED:
        for f in _module_files(module):
            imports = _imports_in(f)
            for upper in ("view", "api"):
                if upper == module or (module == "api" and upper == "view"):
                    continue
                if upper in imports:
                    violations.append(f"{f.relative_to(MODULES_DIR)}: imports {upper}")
    assert not violations, "上层模块被下层 import（禁止）:\n" + "\n".join(violations)


def test_sc2_importable():
    """环境冒烟：driver 的 SC2 库可 import。"""
    import sc2  # noqa: F401


def test_agent_dependency_direction():
    """agent/ 是引擎之外的消费者：只读读模型 + vendor，不内嵌引擎/传输/驱动。

    命令必须走 HTTP 客户端（agent.client），观测必须走 view 读模型——agent 一旦
    import api/driver/引擎内部，"外接 agent"就变成了进程内耦合，评测与替换都失效。
    """
    violations = []
    for f in _pkg_files(AGENT_DIR):
        bad = _imports_in(f) & AGENT_PROHIBITED
        if bad:
            violations.append(f"{f.relative_to(AGENT_DIR.parent)}: imports {sorted(bad)} (prohibited)")
    assert not violations, "agent 包依赖方向违规:\n" + "\n".join(violations)


def test_nobody_imports_eval_or_voice():
    """eval 是评测消费者（同 tests 地位），voice 是可附加 sidecar——被测对象不认识它们。

    modules/ 与 agent/ 里出现 `import eval` / `import voice` 即方向反了：
    评测框架或语音栈渗进引擎，被测系统就依赖了自己的裁判/外设。
    """
    violations = []
    for root in (MODULES_DIR, AGENT_DIR):
        for f in _pkg_files(root):
            bad = _imports_in(f) & {"eval", "voice"}
            if bad:
                violations.append(f"{f.relative_to(root.parent)}: imports {sorted(bad)}")
    assert not violations, "引擎/agent 反向 import 了 eval 或 voice:\n" + "\n".join(violations)


def test_api_imports_agent_lazy_only():
    """api 装配根可在函数体内 lazy 构造 agent 运行时；模块级 import 即硬违规。

    现状：app.py create_app 内 `from agent... import`（缩进 = lazy）。这条测试把
    lazy-only 固化——模块级 import 会让传输层在 import 期就拖起 agent/LLM 栈。
    """
    violations = []
    for f in _module_files("api"):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = _MODULE_LEVEL_IMPORT_RE.match(line)
            if m and m.group(1) == "agent":
                violations.append(f"{f.relative_to(MODULES_DIR)}: `{line.strip()}`（模块级）")
    assert not violations, "modules/api 对 agent 只许函数内 lazy import:\n" + "\n".join(violations)
