"""flow.templates：策略模板库（strategies/_lib.yaml）的解析与编译期展开（ADR-0031）。

模板 = **裸 step**（branches/locals/可读名）+ params 声明 + exits 接口契约：
- 展开发生在 parse 阶段（AST 级文本替换），展开产物与手写策略**同构** ——
  运行时模型零变化，全套既有校验（图可达/死边/谓词签名/环出口）照跑；
- 这与 ADR-0028 废掉的"运行时可组合模块"（实例状态/热切/多实例复杂度）不是
  一回事：模板没有运行时形态，编译完就没了。

两层词法作用域：**模板体只看得见自己的 params**；绑定值可以引用策略级
`{param: Y}`（留给运行时求值），这是模板与策略之间唯一的桥。反过来，
模板体里写策略级参数名 = 编译错误（不静默捕获，暗渠比错误更贵）。

lib 文件约束：只含裸 step（`step_templates:` 一层），模板不能套模板、
不能 exit_strategy（终局是策略作者的权，不是模板的）。
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

#: 模板声明的键白名单（与 manifest.STEP_KEYS 同源减 step_id，加接口契约两键）
TEMPLATE_KEYS = frozenset({
    "display_name_zh", "description_zh", "params", "exits", "branches", "locals",
})
#: 模板 params 声明允许的键（与 manifest.PARAM_KEYS 同一份形态）
TEMPLATE_PARAM_KEYS = frozenset({"type", "default", "description_zh"})
TEMPLATE_PARAM_TYPES = frozenset({"int", "float", "point", "bool", "str"})
#: import 引用声明的键白名单（from / template / params）
IMPORT_KEYS = frozenset({"from", "template", "params"})

#: V1 唯一的库名。`from` 字段写别的 = 编译错误（不静默假装支持多库）
LIB_NAME = "_lib"

#: 出厂模板库（随版本库走：`modules/flow/data/_lib.yaml`）。`runtime/` 整目录 gitignore，
#: 所以 runtime/strategies/_lib.yaml 是**播种出来的工作副本**（人改它；StrategyStore
#: 启动时发现缺失就从这里拷）—— 与 default 策略「内置常量播种」同一模式。
SEED_LIB_PATH = Path(__file__).resolve().parent / "data" / "_lib.yaml"


def seed_lib_text() -> str:
    """出厂模板库原文（StrategyStore 播种 / 无同目录 _lib 时的兜底）。"""
    return SEED_LIB_PATH.read_text(encoding="utf-8")


def parse_lib(yaml_str: str) -> dict[str, dict]:
    """`_lib.yaml` → {模板名: 模板声明}。坏库当场红（AssertionError，中文原因）。

    独立于任何策略校验 lib 本身：params 声明形态、exits 与 branches 的
    exit_step 逐对一致（声明是接口契约，谎报的契约比没有契约更贵）。
    """
    d = yaml.safe_load(yaml_str) or {}
    if not isinstance(d, dict) or "step_templates" not in d:
        raise AssertionError("模板库必须有 step_templates: 一层（模板名 → 裸 step 声明）")
    unknown = sorted(set(d) - {"step_templates"})
    if unknown:
        raise AssertionError(f"模板库顶层未知键 {unknown}（只允许 step_templates）")
    raw = d["step_templates"]
    if not isinstance(raw, dict) or not raw:
        raise AssertionError("step_templates 必须是非空 mapping（模板名 → 声明）")
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    templates: dict[str, dict] = {}
    for name, tpl in raw.items():
        where = f"模板 {name!r}"
        if not isinstance(tpl, dict):
            err(f"{where}: 声明必须是 mapping，当前 {tpl!r}")
            continue
        unknown_keys = sorted(set(tpl) - TEMPLATE_KEYS)
        if unknown_keys:
            err(f"{where}: 未知键 {unknown_keys}（只允许 {sorted(TEMPLATE_KEYS)}；"
                "模板不能套模板、不能引用 imports）")
        if not isinstance(tpl.get("branches"), list) or not tpl["branches"]:
            err(f"{where}: branches 必须是非空列表（空分支的 step 是静默死锁）")
        templates[name] = copy.deepcopy(tpl)
        _check_template_params(name, tpl, err)
        _check_template_exits(name, tpl, err)
    if errors:
        raise AssertionError("模板库 _lib 编译校验失败:\n- " + "\n- ".join(errors))
    return templates


def _check_template_params(name: str, tpl: dict, err) -> None:
    from flow.manifest import _check_param_value  # 局部导入避免环（manifest 顶层 import 本模块）

    for pname, spec in (tpl.get("params") or {}).items():
        where = f"模板 {name!r}.params.{pname}"
        if not isinstance(spec, dict):
            err(f"{where}: 声明必须是 mapping（如 {{type: int, default: 1}}），当前 {spec!r}")
            continue
        unknown = sorted(set(spec) - TEMPLATE_PARAM_KEYS)
        if unknown:
            err(f"{where}: 未知键 {unknown}（只允许 {sorted(TEMPLATE_PARAM_KEYS)}）")
        ptype = spec.get("type")
        if ptype is not None and ptype not in TEMPLATE_PARAM_TYPES:
            err(f"{where}: 未知 type {ptype!r}（白名单 {sorted(TEMPLATE_PARAM_TYPES)}）")
        elif "default" in spec:
            _check_param_value(spec["default"], ptype, f"{where}.default", err)


def _check_template_exits(name: str, tpl: dict, err) -> None:
    """exits 接口契约：与 branches 里 exit_step 的 (kind, reason) 集合逐对一致。"""
    where = f"模板 {name!r}"
    declared = tpl.get("exits")
    if not isinstance(declared, list) or not declared:
        err(f"{where}: exits 必须是非空列表 —— 它是模板的函数签名，接线的策略靠它编译")
        declared = []
    actual: set[tuple[str, str]] = set()
    for b in tpl.get("branches") or []:
        if not isinstance(b, dict):
            continue
        for a in b.get("do") or []:
            if not isinstance(a, dict):
                continue
            if a.get("op") == "exit_strategy":
                err(f"{where}: 模板内不允许 exit_strategy（终局是策略作者的权，不是模板的）")
            elif a.get("op") == "exit_step":
                actual.add((a.get("kind"), a.get("reason")))
    decl_set = {(e.get("kind"), e.get("reason")) for e in declared if isinstance(e, dict)}
    for pair in sorted(decl_set - actual, key=str):
        err(f"{where}: exits 声明了 {pair} 但 branches 里没有对应的 exit_step（谎报的接口契约）")
    for pair in sorted(actual - decl_set, key=str):
        err(f"{where}: branches 有 exit_step {pair} 但 exits 没声明（接口契约不完整）")


def expand_strategy(d: dict, templates: dict) -> tuple[dict, list[str]]:
    """策略 dict（含 imports）→ (展开后的 dict, 导入的 step_id 列表)。

    - 每个 import 以**键名**为 step_id 实例化模板（`{param: X}` → 绑定值）；
    - 绑定值可引用策略级 `{param: Y}` / `{var: Y}` / `{ref: Y}`（原样保留，运行时求值）；
    - edges 引用导入 step 的 (kind, reason) 必须落在模板 exits 里 —— 接错线当场红；
    - 展开后的 dict 交给 parse_strategy 的全部既有校验（图可达/死边/谓词照跑）。
    """
    imports = d.pop("imports")
    if not isinstance(imports, dict) or not imports:
        raise AssertionError("strategy 编译校验失败:\n- imports 必须是非空 mapping"
                             "（step_id → {from: _lib, template: 名字, params: {...}}）")
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    # 收集全部错误再抛（一次看全，别挤牙膏）
    imported_steps: list[dict] = []
    imported_ids: list[str] = {}
    for sid, spec in imports.items():
        if not isinstance(spec, dict):
            err(f"imports.{sid}: 引用必须是 mapping（from/template/params），当前 {spec!r}")
            continue
        unknown = sorted(set(spec) - IMPORT_KEYS)
        if unknown:
            err(f"imports.{sid}: 未知键 {unknown}（只允许 {sorted(IMPORT_KEYS)}）")
        if spec.get("from") != LIB_NAME:
            err(f"imports.{sid}: from 必须是 {LIB_NAME!r}（V1 唯一模板库），当前 {spec.get('from')!r}")
        tpl_name = spec.get("template")
        tpl = templates.get(tpl_name) if isinstance(tpl_name, str) else None
        if tpl is None:
            known = sorted(templates)
            err(f"imports.{sid}: 模板 {tpl_name!r} 不在 _lib（可用：{known}）")
            continue
        imported_ids[sid] = str(tpl_name)
        imported_steps.append(_instantiate(sid, spec, str(tpl_name), tpl, err))

    # edges 接线检查（对模板的接口契约，不在合并后的死边检查里重复报）
    exits_of = {sid: {(e.get("kind"), e.get("reason"))
                      for e in templates[tpl_name].get("exits", []) if isinstance(e, dict)}
                for sid, tpl_name in imported_ids.items()}
    for e in d.get("edges") or []:
        sid = e.get("from")
        if sid in exits_of:
            pair = (e.get("kind"), e.get("reason"))
            if pair not in exits_of[sid]:
                avail = "、".join(f"{k}/{r}" for k, r in sorted(exits_of[sid], key=str))
                err(f"edge {e.get('from')}→{e.get('to')} 的 {e.get('kind')}/{e.get('reason')}"
                    f" 不在模板 {imported_ids[sid]!r} 的 exits（可用：{avail}）")

    # step_id 冲突：imports 与手写 steps 撞名 = 后者静默覆盖前者（manifest 对纯手写
    # 重复已有同样的检查，这里把导入侧也算进去）
    for sid in imported_ids:
        if any(s.get("step_id") == sid for s in d.get("steps") or []):
            err(f"step_id {sid!r} 重复：imports 导入了它，steps 又手写了一份（后一份会静默覆盖）")

    if errors:
        raise AssertionError("strategy 编译校验失败:\n- " + "\n- ".join(errors))

    d = dict(d)
    d["steps"] = imported_steps + list(d.get("steps") or [])
    return d, list(imported_ids)


def _instantiate(sid: str, spec: dict, tpl_name: str, tpl: dict, err) -> dict:
    """一个 import → 展开的 step dict（模板参数替换完成、step_id 换成导入键名）。"""
    from flow.manifest import _check_param_value  # 局部导入避免环

    declared = tpl.get("params") or {}
    binding = spec.get("params") or {}
    if not isinstance(binding, dict):
        err(f"imports.{sid}: params 必须是 mapping，当前 {binding!r}")
        binding = {}
    # 绑定键必须已声明；值按声明 type 校验字面量（{param:Y} 桥接节点放行，运行时才定型）
    for pname, val in binding.items():
        if pname not in declared:
            err(f"imports.{sid}: 绑定了模板 {tpl_name!r} 未声明的参数 {pname!r}"
                f"（声明：{sorted(declared)}）—— 拼错会被静默忽略，这里直接拒")
        if isinstance(val, dict) and (set(val) & {"param", "var", "ref", "local", "const"}):
            continue  # 策略级引用节点（两层作用域的桥）
        ptype = (declared.get(pname) or {}).get("type")
        _check_param_value(val, ptype, f"imports.{sid}.params.{pname}", err)
    # 值表 = 绑定 ∪ 默认；无默认又未绑定 = 接口没接全
    values: dict = {}
    for pname, pspec in declared.items():
        if pname in binding:
            values[pname] = copy.deepcopy(binding[pname])
        elif "default" in pspec:
            values[pname] = copy.deepcopy(pspec["default"])
        else:
            err(f"imports.{sid}: 模板参数 {pname!r} 未绑定且无默认值（type="
                f"{(pspec or {}).get('type')!r}）—— 模板的必需参数必须显式接")
    step = {k: copy.deepcopy(v) for k, v in tpl.items()
            if k not in ("params", "exits")}
    step["step_id"] = sid
    step["branches"] = [_substitute(b, values, sid, tpl_name, err)
                        for b in step.get("branches") or []]
    if "locals" in step:
        step["locals"] = list(step["locals"])
    return step


def _substitute(node, values: dict, sid: str, tpl_name: str, err):
    """深度替换 branches 里的 `{param: X}`（X ∈ 模板参数）为绑定/默认值。

    X 不在模板参数里 = 模板体试图引用策略级名字 —— 拒（两层作用域，暗渠比错误贵）。
    绑定值本身是策略级引用节点（{param: Y} 等）时原样保留，运行时求值。
    """
    if isinstance(node, dict):
        if set(node) == {"param"} and isinstance(node["param"], str):
            name = node["param"]
            if name in values:
                return copy.deepcopy(values[name])
            err(f"imports.{sid}: 模板 {tpl_name!r} 的 branches 引用了未声明的模板参数"
                f" {name!r}（模板体只看得见自己的 params；要接策略级参数，在 imports 的"
                f" params 里绑定 {{param: ...}}）")
            return node
        return {k: _substitute(v, values, sid, tpl_name, err) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(i, values, sid, tpl_name, err) for i in node]
    return node
