"""agent.tools：语义工具面（文件工作区改造后，2026-08-22；2026-08-23 工具审视 19→17）。

**给什么工具就等于给什么权限**（§6 P1）。规划文件的读写不再走 CRUD 包装 ——
文件契约（ls/read/grep/edit/insert/…）由引擎按 agent.workspace（ApiWorkspace：
plans/ + map-plans/ + strategies/ 虚拟目录 + scratch）装配，见 agent/workspace.py。这里只剩：

- `observe`      读当前观察包（ADR-0009 的"当前事实"；规则是替换而非追加）
- `propose`      对局域唯一改动通道（校验通过即自动应用；记录 ChangeRecord）
- `simulate_plan`/`start_session` 规划域的**动作**（干跑/起会话，文件表达不了）
- `list_modules`/`read_module` 战术素材（只读的参考生产模块库）

写面清单（能做什么/为什么不能做）**不是工具**：挂成只读文件 `system/surface.md`
（读=文件，SurfaceArea 渲染 /api/agent/tools）。`read_current_strategy` 同批退役
—— 它 dump 的是写死常量而非当前会话实装的策略（对 live 有误导）；替代：
`read strategies/<id>.yaml` + observe 的策略段（带 strategy_ref）。

**不给** `queue_op` / `set_worker_quota`：那是直接改对局状态。agent 想改就提提案。
"""
from __future__ import annotations

import json

from agentic.types import Tool

from agent.client import ApiClient, ApiError
from agent.workspace import ChangeLog, ChangeRecord

#: 观察包里给 LLM 的文本上限：再长就是噪声，而且挤掉后续轮的空间
OBSERVATION_CHARS = 6000


def make_tools(client: ApiClient, *, source: str = "live",
               changes: ChangeLog | None = None) -> list[Tool]:
    async def observe(_args: dict) -> str:
        try:
            obs = client.observation(source=source)
        except ApiError as exc:
            return f"取观察失败：{exc}"
        text = obs.get("text") or ""
        facts = json.dumps(obs.get("facts", {}), ensure_ascii=False)
        return (text[:OBSERVATION_CHARS]
                + f"\n\n[机器可读] {facts}\n"
                + f"[提醒] 提案/命令里的 based_on_seq 用 {obs.get('seq')}")

    async def propose(args: dict) -> str:
        body = {
            "kind": args.get("kind") or "production_queue",
            "author": "agent",
            "title_zh": args.get("title_zh") or "",
            "rationale_zh": args.get("rationale_zh") or "",
            "target": args.get("target") or {"queue": "main"},
            "hunks": args.get("hunks") or [],
        }
        if not body["rationale_zh"].strip():
            return "拒绝：rationale_zh 必填 —— 没有理由的提案无法追溯判断依据，后端也会拒（§6 P3）"
        if not body["hunks"]:
            return "拒绝：hunks 不能为空 —— 提案必须给出可应用的改动，不能只描述想法"
        try:
            p = client.propose(body)
        except ApiError as exc:
            return f"提案被拒：{exc.detail}"
        v = p.get("validation") or {}
        if v.get("ok"):
            if p.get("status") == "已接受":
                if changes is not None:
                    changes.add(ChangeRecord(
                        area="live", action="edit",
                        ref=str(p.get("title_zh") or p["id"]),
                        label=f"对局队列：{p.get('title_zh') or p['id']}"))
                return (f"提案 {p['id']} 已提交并**自动应用**（校验通过即生效，无审批环节）。"
                        f"预览={(p.get('preview') or {}).get('kind', '无')}")
            return (f"提案 {p['id']} 校验通过但**没有应用成**（大概率是没有运行中的会话）—— "
                    "它保留在提案历史里，会话起来后基于当前状态重提一条。")
        errs = "；".join(e.get("text_zh", "") for e in v.get("errors", []))
        return (f"提案 {p['id']} 已提交但**校验未通过**：{errs}。"
                "它对历史可见但不会被应用 —— 修掉这些问题再提一条。")

    return [
        Tool(
            name="observe",
            description=("读当前观察包（经济/部队/生产/策略/风险/投影）。"
                         "先调它再做判断；它给的 seq 就是提案要回填的 based_on_seq。"),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            function=observe,
        ),
        Tool(
            name="propose",
            description=("提交队列改动：校验通过即**自动应用**（审批已停用），不通过会带原因返回。"
                         "对局内你**只能**这样改变局面 —— 没有直接下命令的工具。"
                         "hunks 必须是可应用的操作。"),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["production_queue"],
                             "description": "V1 只有 production_queue 能被应用"},
                    "title_zh": {"type": "string", "description": "一句话说清改什么"},
                    "rationale_zh": {"type": "string",
                                     "description": "**必填**：为什么这么改（用户靠它判断）"},
                    "target": {"type": "object", "description": '如 {"queue": "main"}'},
                    "hunks": {
                        "type": "array",
                        "description": ("可应用的改动。每条："
                                        '{"id","kind":"insert|delete|modify|reorder","text_zh","payload"}；'
                                        'payload：insert/modify 用 {"index","item"}，'
                                        'delete 用 {"index"}，reorder 用 {"order":[…]}（0..n-1 的排列）。'
                                        'item 形如 {"op":"build|train|assign_workers",'
                                        '"type":"terran/xxx","count":1,'
                                        '"placement":{"kind":"in_region","region":"home"}}'),
                        "items": {"type": "object"},
                    },
                },
                "required": ["title_zh", "rationale_zh", "hunks"],
                "additionalProperties": False,
            },
            function=propose,
        ),
    ]


#: 规划工具输出上限：read_map_plan 的槽位清单等可能很长，超了截断保后续轮空间
PLANNING_CHARS = 9000


def _clip(text: str, limit: int = PLANNING_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…（过长截断）"


def _err(exc: ApiError) -> str:
    """把 ApiError 变成 agent 可读的一句：结构化 detail（hunk 校验错误）展开成行。"""
    d = exc.detail
    if isinstance(d, dict) and isinstance(d.get("errors"), list):
        lines = "；".join(str(e.get("text_zh") or e) for e in d["errors"])
        return f"HTTP {exc.status}：{lines}"
    return f"HTTP {exc.status}：{d}"


def _item_line(i: int, it: dict) -> str:
    parts = [f"{i}. {it.get('op')}", str(it.get("type") or it.get("task") or "?")]
    if it.get("count") and it["count"] != 1:
        parts.append(f"×{it['count']}")
    if it.get("placement"):
        parts.append(f"placement={json.dumps(it['placement'], ensure_ascii=False)}")
    return " ".join(parts)


def make_planning_tools(client: ApiClient) -> list[Tool]:
    """规划域的**语义工具**（2026-08-22 文件工作区改造后的存活面）。

    规划文件的读写走文件契约（ls/read/grep/edit/insert/…，存储后端是
    agent.workspace.ApiWorkspace：plans//map-plans/ 虚拟目录 + scratch 自留地），
    CRUD 包装层不再需要。这里只剩文件表达不了的**动作**：
    干跑试算、起会话、看参考战术库/当前策略。
    """

    async def simulate_plan(args: dict) -> str:
        items = args.get("queue")
        pid = str(args.get("plan_id") or "").strip() or None
        if not items and pid:
            try:
                items = client.plan_get(pid)["queue"]
            except ApiError as exc:
                return f"取规划失败：{_err(exc)}"
        if not isinstance(items, list) or not items:
            return "拒绝：queue 与 plan_id 至少给一个（都给则以 queue 为准）"
        try:
            horizon = min(600.0, max(1.0, float(args.get("horizon") or 300.0)))
        except (TypeError, ValueError):
            return "拒绝：horizon 必须是秒数（1..600）"
        try:
            r = client.plans_simulate({"items": items, "horizon": horizon,
                                       "plan_id": pid or "draft"})
        except ApiError as exc:
            return f"干跑失败：{_err(exc)}"
        pts = r.get("points") or []
        out = [f"干跑 {pid or '草稿队列'}（horizon {horizon:g}s）："]
        if pts:
            last = pts[-1]
            army = sum(v for k, v in (last.get("units") or {}).items())
            bld = sum(v for v in (last.get("buildings") or {}).values())
            out.append(f"曲线末点 t={last['t']:g}s 矿 {last['minerals']:g} 气 {last['gas']:g} "
                       f"人口 {last['supply_used']:g}/{last['supply_cap']:g}"
                       f"（采气工 {last.get('gas_workers', 0)}，建筑 {bld}，单位 {army}）")
        evs = r.get("events") or []
        if evs:
            out.append("事件：")
            out += [f"- t={e['t']:g}s {e['kind']} {e.get('stable_id') or ''}"
                    + (f"：{e['reason']}" if e.get("reason") else "") for e in evs]
        alerts = r.get("alerts") or []
        out.append("前瞻警报：" if alerts else "前瞻警报：（无）")
        out += [f"- [{a.get('severity')}] {a.get('text_zh')}" for a in alerts]
        skipped = r.get("skipped") or []
        if skipped:
            out.append("被跳过的项（语法/catalog 不认）：")
            out += [f"- {s.get('op')}: {s.get('reason')}" for s in skipped]
        return _clip("\n".join(out))

    # ---- 会话 ----

    async def start_session(args: dict) -> str:
        driver = str(args.get("driver") or "sim")
        if driver not in ("offline", "sim", "sc2"):
            return "拒绝：driver 只能是 offline（进程内假世界）/ sim（沙盒子进程）/ sc2（真机）"
        strategy = str(args.get("strategy") or "").strip() or None
        loadout = str(args.get("loadout") or "").strip() or None
        try:
            d = client.session_start(
                driver=driver, map_plan=args.get("map_plan"), strategy=strategy,
                loadout=loadout, autotick=bool(args.get("autotick", True)))
        except ApiError as exc:
            return f"启动会话失败：{_err(exc)}"
        keep = {k: v for k, v in d.items() if k in ("state", "driver", "map_plan_path",
                                                    "frame_source", "error")}
        mp = str(args.get("map_plan") or (f"loadout·{loadout}" if loadout else "出厂模板"))
        st = strategy or (f"loadout 内含" if loadout else "内置默认")
        return (f"会话已启动（driver={driver}，map_plan={mp}，strategy={st}）："
                f"{json.dumps(keep, ensure_ascii=False)}")

    # ---- 战术素材（参考模块库；策略文件可写 —— 二十七轮放开，免审。
    # read_current_strategy 已退役（2026-08-23）：dump 写死常量而非当前会话实装；
    # 替代 = read strategies/<id>.yaml（含 _lib.yaml 模板库）+ observe 策略段） ----

    async def list_modules(_args: dict) -> str:
        from planner.build_order import MODULE_REGISTRY
        from view.plans import ops_to_items as export

        lines = []
        for ref, fn in sorted(MODULE_REGISTRY.items()):
            doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
            n = len(export(fn({})))
            lines.append(f"- {ref}（{n} 项）：{doc}")
        return "内置生产模块（参考战术库，read_module 看完整导出）：\n" + "\n".join(lines)

    async def read_module(args: dict) -> str:
        from planner.build_order import MODULE_REGISTRY
        from view.plans import ops_to_items as export

        ref = str(args.get("ref") or "").strip()
        if not ref:
            return "拒绝：要给模块 ref（先 list_modules）"
        fn = MODULE_REGISTRY.get(ref)
        if fn is None:
            return f"没有模块 {ref!r}（先 list_modules 看有哪些）"
        doc = (fn.__doc__ or "").strip()
        from view.proposals import item_to_json

        items = [item_to_json(x) for x in export(fn({}))]
        out = [f"{ref}：{doc or '（无说明）'}", f"默认参数导出（{len(items)} 项）："]
        out += [_item_line(i, it) for i, it in enumerate(items)]
        return _clip("\n".join(out))

    return [
        Tool(name="simulate_plan",
             description=("离线干跑：真 planner 投影 + 前瞻警报（不需要会话）。给 queue 直接试，"
                          "或给 plan_id 用该规划的现行队列。输出曲线末点/事件/警报/被跳过项。"
                          "**改过规划文件必须干跑** —— 没有试算的改动不算完成。"),
             parameters={"type": "object",
                         "properties": {"queue": {"type": "array", "items": {"type": "object"}},
                                        "plan_id": {"type": "string"},
                                        "horizon": {"type": "number",
                                                    "description": "干跑秒数，1..600，默认 300"}},
                         "additionalProperties": False},
             function=simulate_plan),
        Tool(name="start_session",
             description=("启动会话（可选带一份地图规划进游戏）。driver：sim = 沙盒子进程"
                          "（默认，验证装配）；sc2 = 真机，**会打开一个 SC2 游戏进程**，"
                          "确认用户要真机才用；offline = 进程内假世界（最轻）。"
                          "loadout：装配清单 id（GET /api/loadouts 看有哪些）—— 一发入魂："
                          "地图规划 + 策略 + 生产序列自动入队，显式 map_plan/strategy 覆盖它。"),
             parameters={"type": "object",
                         "properties": {"driver": {"type": "string",
                                                   "enum": ["offline", "sim", "sc2"]},
                                        "map_plan": {"type": "string",
                                                     "description": "地图规划 id，缺省出厂模板"},
                                        "strategy": {"type": "string",
                                                     "description": ("策略文件 id（strategies/<id>.yaml），"
                                                                     "缺省内置默认 —— 验证你写的策略就传它")},
                                        "loadout": {"type": "string",
                                                    "description": ("装配清单 id（三件套引用：map_plan/"
                                                                    "strategy/plan），生产序列自动入队")},
                                        "autotick": {"type": "boolean",
                                                     "description": "默认 true；仅测试/单步调试传 false"}},
                         "additionalProperties": False},
             function=start_session),
        Tool(name="list_modules",
             description="列内置生产模块（参考战术库：步坦协同开局等）—— 商量战术时的对比基准。",
             parameters={"type": "object", "properties": {}, "additionalProperties": False},
             function=list_modules),
        Tool(name="read_module",
             description="读一个参考模块的说明与默认参数导出（build/train 项序列）。",
             parameters={"type": "object",
                         "properties": {"ref": {"type": "string"}},
                         "required": ["ref"], "additionalProperties": False},
             function=read_module),
    ]
