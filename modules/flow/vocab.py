"""flow.vocab：机器可读词表导出（A1）—— 单一权威源 → 提示词 / 错误信息 / UI 目录。

为什么值得单独一个模块：决定"LLM 能不能可靠写出策略脚本"的不是语法选择，而是
**提示词里的词表与校验器是不是同一份**。人手抄一份词表进提示词，迟早与 PREDICATE_SIGNATURES /
OP_CATALOG 漂移，然后模型稳定地写出"看着对、编译不过"的脚本。所以这里只做一件事：
把已经存在的权威表导出成机器可读结构 + 一张紧凑提示词卡片。

三个消费方（都零维护，改词表即自动生效）：
1. LLM 提示词自动生成（render_prompt_card）
2. 编译错误信息里的"可用谓词 / 期望参数名"（validate 已直接读签名表）
3. 可视化编辑器的方块目录（dump_vocabulary）
"""
from __future__ import annotations

from game.operation import COMPOSITE_ACTIONS, OP_CATALOG

from flow.manifest import (
    DO_OPS,
    LOOP_LIMIT_KEYS,
    PARAM_KEYS,
    PARAM_TYPES,
    UNIMPLEMENTED_DO_OPS,
    UNIMPLEMENTED_STEP_KEYS,
)
from flow.predicates import (
    OPERATOR_ARITY,
    PREDICATE_SIGNATURES,
    UNIMPLEMENTED_PREDICATE_OPS,
    UNIMPLEMENTED_SPATIAL_OPS,
    VALUE_OPS,
)

#: 谓词/空间工具中文名（I1）。zh 文案单一真相源在后端（红线 C4）—— UI 的条件 chip、
#: 提示词卡片、agent 读面全部消费 dump_vocabulary 里的 name_zh，不在前端抄第二份。
#: 覆盖性由 tests/flow/test_vocab.py 锁住（新增谓词忘了配 zh 会当场红）。
PREDICATE_ZH: dict[str, str] = {
    "group_count": "组内数量",
    "unit_count": "拥有数量",
    "game_time": "游戏时间",
    "strategy_elapsed": "策略耗时",
    "step_elapsed": "本步耗时",
    "timer_elapsed": "定时器耗时",
    "arrived": "已抵达",
    "group_center": "组中心",
    "distance_between": "两组间距",
    "enemy_count_near": "近敌数量",
    "region_center": "区域中心",
    "group_center_in_region": "组在区域内",
    "enemy_visible_in": "区域见敌",
    "group_hp_ratio": "组血线",
    "has_building": "已建建筑",
    "engaged": "交火中",
    "under_attack": "受击中",
    "point_toward": "朝向取点",
}

#: 比较/逻辑运算符中文名。and/or/not 的渲染文案（且/或/非）也在这里，不再散在 UI 侧。
OPERATOR_ZH: dict[str, str] = {
    ">=": "≥", ">": ">", "<=": "≤", "<": "<", "==": "＝", "!=": "≠",
    "and": "且", "or": "或", "not": "非",
}

#: reason 标识符 → 中文的**全局默认表**（ADR-0031 同批，I2 的延伸）。
#: 覆盖模板库与内置策略共用的那批出口词；策略自己的 reasons 只写增量、同名覆盖默认
#:（`static/strategy.reasons` = 本表 ∪ 策略覆盖，view.statics 合并）。
#: 没进表的 reason 不是错误 —— 前端退回标识符显示（U6/C4：zh 单一真相源在后端）。
REASON_ZH: dict[str, str] = {
    "READY": "就绪",
    "FORMED": "成型",
    "ARRIVED": "抵达",
    "BREAKOUT": "突围",
    "IN_PLACE": "就位",
    "SIEGED": "已架起",
    "SKIP": "跳过",
    "STRAGGLED": "脱队",
    "RESUME": "恢复推进",
    "CLEARED": "威胁清除",
    "DONE": "完成",
    "FAIL": "失败",
    "TIMEOUT": "超时",
    "LOOP_LIMIT": "转移上限",
}

#: 动作原子中文名（I4 的延伸：分支详情里的动作行同样读得出意图）。
ACTION_ZH: dict[str, str] = {
    "move_to": "移动",
    "attack_move_to": "攻击移动",
    "hold_position": "原地驻守",
    "stop": "停止",
    "follow": "跟随",
    "patrol": "巡逻",
    "focus_fire": "集火",
    "siege": "架起",
    "unsiege": "收起",
    "build": "建造",
    "build_gas": "建气矿",
    "train": "训练",
    "research": "研究",
    "assign_workers": "分配工人",
    "gather": "采集",
    "load": "装载",
    "unload": "卸载",
    "use_ability": "用技能",
    "cancel": "取消",
    "morph": "变形",
    # do 操作的中文名（二十六轮 T8 转正的三个；渲染在分支详情里）
    "set_local": "设局部变量",
    "start_timer": "起表",
    "stop_timer": "停表",
}


def dump_vocabulary() -> dict:
    """完整词表（JSON 可序列化）：谓词签名 / 运算符 arity / 动作目录 / do 操作 / 未实现清单 / 声明白名单。

    predicates/operators/actions 每项带 `name_zh`（I1）—— 值内加键，字段结构没变，
    但按 rev 5 的先例走 REV+1（前端 zod 是逐字段校验的，静默 strip 会让别名永远到不了 UI）。
    """
    return {
        "predicates": {
            name: {
                "name_zh": PREDICATE_ZH.get(name, name),
                "params": [{"name": p, "required": req} for p, req in sig],
                "kind": "value" if name in VALUE_OPS else "bool",
            }
            for name, sig in sorted(PREDICATE_SIGNATURES.items())
        },
        "operators": {
            name: {"name_zh": OPERATOR_ZH.get(name, name), "min_args": lo, "max_args": hi}
            for name, (lo, hi) in sorted(OPERATOR_ARITY.items())
        },
        "actions": {
            name: {"name_zh": ACTION_ZH.get(name, name),
                   "params": [{"name": p, "type": t.value, "required": req}
                              for p, t, req in params]}
            for name, params in sorted(OP_CATALOG.items())
            if name not in COMPOSITE_ACTIONS  # 复合意图不能作为 group_action 直接发
        },
        "do_ops": sorted(DO_OPS),
        "forbidden": {
            "predicates": dict(sorted(UNIMPLEMENTED_PREDICATE_OPS.items())),
            "spatial_tools": dict(sorted(UNIMPLEMENTED_SPATIAL_OPS.items())),
            "do_ops": dict(sorted(UNIMPLEMENTED_DO_OPS.items())),
            "composite_actions": dict(sorted(COMPOSITE_ACTIONS.items())),
            "step_keys": dict(sorted(UNIMPLEMENTED_STEP_KEYS.items())),
        },
        "declarations": {
            "param_keys": sorted(PARAM_KEYS),
            "param_types": sorted(PARAM_TYPES),
            "loop_limit_keys": sorted(LOOP_LIMIT_KEYS),
        },
        "node_forms": {
            "literal": "3.5 / \"terran/marine\" / [x, y]",
            "param": "{param: name}（params 中声明过）",
            "variable": "{var: name}（variables 中声明过）",
            "ref": "{ref: name}（definitions 中声明过；纯替换，不得自引用）",
            "predicate": "{op: arrived, group: inf, target: {param: t}, radius: 8.0}（命名参数）",
            "operator": "{op: \">=\", args: [a, b]} / {op: and, args: [...]}（运算符保留 args）",
            "group_action": "{op: group_action, group_slot: inf, type: terran/marine,"
                            " action_atom: move_to, params: {position: [x, y]}}",
            "exit": "{op: exit_step, kind: done, reason: NAME}（必须有匹配 edge）/"
                    " {op: exit_strategy, kind: done, reason: NAME}",
        },
        "rules": [
            "谓词/空间工具用命名参数；比较/逻辑用 args",
            "分支自上而下取第一个命中；无 when 的 else 分支只能放最后",
            "每个 exit_step 的 (kind, reason) 必须有对应 edge；反之没有 exit_step 的 edge 是死边（编译失败）",
            "step 必须从 initial_step 可达；每个环必须有出口（出环 edge 或环内 exit_strategy）",
            "单位类型一律两段式 stable id（terran/marine），不接受 burnysc2 名（MARINE）",
            "标识符字段必须是字符串：YAML 会把 on/off/yes/no 解析成布尔（reason: NO → False）",
        ],
    }


def render_prompt_card() -> str:
    """紧凑提示词卡片（给 LLM 的词表片段；与校验器同源，不会漂移）。"""
    v = dump_vocabulary()
    lines: list[str] = ["## 可用谓词（命名参数）"]
    for name, spec in v["predicates"].items():
        params = ", ".join(p["name"] + ("" if p["required"] else "?") for p in spec["params"])
        zh = spec.get("name_zh", name)
        lines.append(f"- {name}({params or ''}) ｜ {zh}"
                     + ("  # 返回值（可用在参数位）" if spec["kind"] == "value" else ""))
    lines.append("")
    lines.append("## 运算符（用 args）")
    for name, spec in v["operators"].items():
        hi = spec["max_args"]
        lines.append(f"- {name} ｜ {spec.get('name_zh', name)}: {spec['min_args']}"
                     + ("+" if hi is None else (f"~{hi}" if hi != spec["min_args"] else "")))
    lines.append("")
    lines.append("## 动作（group_action.action_atom）")
    for name, spec in v["actions"].items():
        params = ", ".join(p["name"] + ("" if p["required"] else "?") for p in spec["params"])
        lines.append(f"- {name}({params}) ｜ {spec.get('name_zh', name)}")
    lines.append("")
    lines.append("## 禁止使用（词表里有但未实现，编译期会拒）")
    for group, items in v["forbidden"].items():
        for name, reason in items.items():
            lines.append(f"- {name}（{group}）：{reason}")
    lines.append("")
    lines.append("## 节点形态")
    for kind, form in v["node_forms"].items():
        lines.append(f"- {kind}: {form}")
    lines.append("")
    lines.append("## 规则")
    lines.extend(f"- {r}" for r in v["rules"])
    return "\n".join(lines)
