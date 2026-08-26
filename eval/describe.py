"""eval.describe：Project → 可序列化描述（PLAN-EVAL-FRONTEND 批 A，只读叠加）。

类数据 introspection：Project/Task 是 dataclass（fields 直读）；Fixture/Runner/
Grader 是 Protocol，但全仓实现都是普通类、构造参数全存同名 self 字段（PLAN §0
逐个核实）——**vars(instance) 单源即全部构造参数**，不需要 signature。

值渲染只保证一件事：describe_project 的输出一定能 json.dumps（不变式有测试，
前端面靠它——新场景/新 grader 的构造参数里塞了不可渲染的对象会在测试红）。
不可 JSON 化的值展成标注：函数 → "<名> @ 相对路径:行"（够定位、不假装看得见
闭包内部，EF2）；未知对象 → 截断 repr。

可选 describe() override：组件实现了 `describe() -> dict` 就 merge 进 params——
期望藏在 grade() 逻辑里的主动 grader 用（EF1/EF3：introspect 只能看到类名，
如 PlansGrader）。
"""
from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

from eval.contracts import Project

_REPO = Path(__file__).resolve().parent.parent


def describe_project(p: Project) -> dict:
    """一个评测项目的完整构成（前端「项目详情」的全部数据源）。"""
    return {
        "id": p.id,
        "tags": list(p.tags),
        "runs": p.runs,
        "judge_model": p.judge_model,
        "task": {f.name: _render(getattr(p.task, f.name)) for f in fields(p.task)},
        "fixture": _component(p.fixture),
        "runner": _component(p.runner),
        "graders": [_component(g) for g in p.graders],
    }


def _component(obj) -> dict:
    """契约组件 → {class, module, name?, axis?, params}。

    params 主源 vars()（构造参数同名存 self 的全仓约定）；组件自带 describe()
    时 merge 其上（主动 grader 显式补期望）。name/axis 是类属性（EitherGrader
    的 name 是 property），单独浮到顶层供前端直接列。
    """
    out: dict = {
        "class": type(obj).__name__,
        "module": _rel(inspect.getsourcefile(type(obj))),
        "params": {k: _render(v) for k, v in sorted(vars(obj).items())},
    }
    for key in ("name", "axis"):
        v = getattr(obj, key, None)
        if v is not None:
            out[key] = _render(v)
    describe = getattr(obj, "describe", None)
    if callable(describe):
        out["params"] = {**out["params"], **{k: _render(v) for k, v in describe().items()}}
    return out


def _render(v):
    """任意构造参数值 → JSON 安全形态（describe 的唯一硬保证）。"""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_render(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _render(x) for k, x in v.items()}
    if isinstance(v, Path):
        return v.as_posix()
    # 嵌套契约组件（EitherGrader.graders 等）：按 Protocol 鸭子判（grade/run/setup），
    # 递归成组件描述而不是 repr 字符串——json.dumps 对字符串照样过，不变式测不到这层
    if (callable(getattr(v, "grade", None)) or callable(getattr(v, "run", None))
            or callable(getattr(v, "setup", None))):
        return _component(v)
    if callable(v):
        return f"<{getattr(v, '__name__', '?')}> @ {_where(v)}"
    return repr(v)[:120]


def _where(fn) -> str:
    try:
        return f"{_rel(inspect.getsourcefile(fn))}:{inspect.getsourcelines(fn)[1]}"
    except (OSError, TypeError):
        return getattr(fn, "__module__", "?") or "?"


def _rel(path: str | None) -> str:
    """绝对路径 → 仓内相对路径（跨机器可读；仓外文件原样）。"""
    if not path:
        return "?"
    p = Path(path)
    try:
        return p.resolve().relative_to(_REPO).as_posix()
    except ValueError:
        return p.as_posix()
