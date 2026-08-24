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

import asyncio
import json
import threading
import time
from pathlib import Path

from agentic.types import Tool

from agent.client import ApiClient, ApiError
from agent.workspace import ChangeLog, ChangeRecord

#: 观察包里给 LLM 的文本上限：再长就是噪声，而且挤掉后续轮的空间
OBSERVATION_CHARS = 6000


def make_tools(client: ApiClient, *, source: str = "live",
               changes: ChangeLog | None = None,
               map_plans_dir: Path | None = None) -> list[Tool]:
    def _region_grid(args: dict) -> str:
        """B 批（2026-08-24 用户拍板）：observe 直接框选格点网格 —— 不用再拼
        `maps/<源>/<bbox>.md` 路径（read 的 contains 预检会把渲染错误吞成假
        not-found，模型只能瞎猜坐标）。**错误如实返回**：超范围报地图尺寸与
        具体超的坐标；网格超上限报上限与建议 step（render_region 本来的好文案
        直达模型，不再被 exists 吞掉）。"""
        from agent.readonly import MapsArea
        from agentic.workspace.workspace import WorkspaceError
        from tactical_map.region_view import load_placeable

        src = str(args.get("source") or "live")
        try:
            step = int(args.get("step") or 2)
            x1, y1, x2, y2 = (int(v) for v in args.get("bbox") or ())
        except (TypeError, ValueError):
            return "拒绝：bbox 是四个整数 [x1,y1,x2,y2]（左下 + 右上，闭区间）"
        if step < 1:
            return f"拒绝：step 必须 ≥1（收到 {step}）"
        (w, h), _ = load_placeable()
        bad = []
        for name, v, hi in (("x1", x1, w - 1), ("x2", x2, w - 1),
                            ("y1", y1, h - 1), ("y2", y2, h - 1)):
            if v < 0 or v > hi:
                bad.append(f"{name}={v} 不在 [0,{hi}]")
        if not bad and x2 < x1:
            bad.append(f"x2={x2} < x1={x1}（右上不能在左下左边）")
        if not bad and y2 < y1:
            bad.append(f"y2={y2} < y1={y1}（右上不能在左下下边）")
        if bad:
            return (f"error: bbox 超出可索引范围 —— 地图 {w}×{h}"
                    f"（x∈[0,{w - 1}]，y∈[0,{h - 1}]；bbox=左下+右上闭区间）："
                    + "；".join(bad))
        area = MapsArea(client, Path(map_plans_dir) if map_plans_dir
                        else Path("runtime/map-plans"))
        path = f"maps/{src}/{x1}_{y1}_{x2}_{y2}" + (f"_s{step}" if step > 1 else "") + ".md"
        try:
            return area.read(path)
        except WorkspaceError as exc:
            return f"error: {exc}"

    async def observe(args: dict) -> str:
        if args.get("bbox") is not None:
            return _region_grid(args)
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
            description=("读当前观察包（经济/部队/部队清单/关键建筑/生产/策略/风险/投影/区域）。"
                         "先调它再做判断；它给的 seq 就是提案要回填的 based_on_seq。"
                         "带 bbox 时改读**格点网格**（布局结构：槽位/预设点/地形；建造状态"
                         "仍走无参 observe）—— bbox=[x1,y1,x2,y2]（左下+右上闭区间，全图 176×160），"
                         "step 降密度（默认 2，网格上限 14×14 列×行），source 默认 live"
                         "（当前会话装配的地图规划，无会话=出厂 bl 布局；有哪些源 read maps/index.md）。"
                         "超范围/网格超上限**如实报错**（说清哪个坐标超了/建议 step），别瞎试。"),
            parameters={"type": "object", "properties": {
                "bbox": {"type": "array", "items": {"type": "integer"},
                         "minItems": 4, "maxItems": 4,
                         "description": "[x1,y1,x2,y2] 左下 + 右上（闭区间）"},
                "step": {"type": "integer", "minimum": 1,
                         "description": "格点步长，默认 2（每 2 格取 1 格降密度）"},
                "source": {"type": "string",
                           "description": "地图源 id：live（默认）或地图规划 id"},
            }, "additionalProperties": False},
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


class InterjectionQueue:
    """用户插话队列（2026-08-24：对局跟随期间 agent 一轮跑很久，用户要能插话）。

    BaseAgent 引擎没有轮内消息通道（state.inbox 只在轮首排水）—— 我们在**自己的
    工具层**做检查点：sleep 轮询时立刻醒、任意工具结果尾部捎带。LLM 思考中无法
    硬打断（vendor 不改），思考通常几十秒，等下一个工具检查点。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[str] = []
        # 排空账本 [(text, time.time())]：A 批（2026-08-24）—— 落史 segments 用它把
        # 用户插话按真实时序插进轮内时间线（对齐 trace 事件的 ts）
        self._drained: list[tuple[str, float]] = []

    def add(self, text: str) -> None:
        with self._lock:
            self._items.append(text)

    def drain(self) -> list[str]:
        with self._lock:
            out, self._items = self._items, []
            self._drained.extend((t, time.time()) for t in out)
            return out

    def take_drained(self) -> list[tuple[str, float]]:
        """取走排空账本（轮末消费）：[(插话文本, 排空墙钟 epoch)]，取后清空。"""
        with self._lock:
            out, self._drained = self._drained, []
            return out

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._items)

#: sleep 工具的节拍与墙钟上限（2026-08-24 用户要求）：按**游戏时间**等（快进模式下
#: 游戏钟跑得快，同样的游戏秒等得更省）；墙钟上限要留足对话轮看门狗（600s）的余量。
#: 模块级常量（闭包运行期查表）—— 测试可以 monkeypatch 小值快速走上限路径。
SLEEP_POLL_SECS = 0.5
SLEEP_WALL_CAP = 300.0
#: 游戏时钟冻结的叫醒阈值（墙秒）：状态仍是「对局中」但 game_time 不再前进 ——
#: 对局打完停在结算画面而子进程没退出的窗口（正常结束会翻「已结束」，C 批已认）
SLEEP_FREEZE_WALL_SECS = 60.0


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


#: production 参数认的键（全称 → economy task；简写同名直通）
_PRODUCTION_KEYS = ("mineral_workers", "gas_workers", "reserve_idle",
                    "mineral", "gas", "idle")


def _validated_production(raw) -> dict | str:
    """start_session 的 production 参数校验（§0.52 C 批）。返回规范 dict 或拒绝文案。"""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        return ("拒绝：production 是对象，如 {\"mineral_workers\": 8, \"gas_workers\": 3}"
                f"（认的键：{'/'.join(_PRODUCTION_KEYS)}）")
    for key, value in raw.items():
        if str(key) not in _PRODUCTION_KEYS:
            return (f"拒绝：production 不认识键 {key!r}"
                    f"（认的键：{'/'.join(_PRODUCTION_KEYS)}）")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"拒绝：production.{key} 必须是 ≥0 的整数（目标值语义，不是增量）"
    return {str(k): int(v) for k, v in raw.items()}


def make_planning_tools(client: ApiClient,
                        pending: InterjectionQueue | None = None) -> list[Tool]:
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
                                       "plan_id": pid or "draft",
                                       "auto_supply": bool(args.get("auto_supply", False))})
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

    # ---- 会话（「开启游戏」两模式，2026-08-23 用户拍板收敛）----

    async def audit_queue(args: dict) -> str:
        """F 批（2026-08-24 用户拍板：**只诊断 + 给建议**，agent 按建议手动插入）。
        在线队列（不给 queue/plan_id = 当前会话的队列，用 name=队列名选）或离线队列
        （plan_id 规划文件 / 显式 queue）跑体检：卡补给 / 前置不在场也不在队列 /
        产出建筑缺失。每条建议给「插什么、插在剩余队列哪个下标之前」。"""
        from game.catalog import load_all
        from planner.economy import DEFAULT_ECON

        items = args.get("queue")
        pid = str(args.get("plan_id") or "").strip() or None
        online = not items and not pid
        if not items and pid:
            try:
                items = client.plan_get(pid)["queue"]
            except ApiError as exc:
                return f"取规划失败：{_err(exc)}"
        if online:
            try:
                payload = client.latest_frame("frame/production") or {}
            except ApiError as exc:
                return f"取在线队列失败：{_err(exc)}"
            qs = payload.get("queues") or []
            name = str(args.get("name") or "main")
            q = next((x for x in qs if x.get("name") == name), None)
            if q is None:
                have = [x.get("name") for x in qs]
                return f"拒绝：在线队列 {name!r} 不存在（现有：{have or '无'}）"
            items = [{"op": it.get("op"), "type": it.get("stable_id"),
                      "count": it.get("count") or 1}
                     for it in (q.get("items") or [])]
        if not isinstance(items, list) or not items:
            return "拒绝：没有可体检的队列（queue/plan_id 至少给一个；在线模式先入队）"

        # 世界态（在线才有；离线按空世界 —— 只查顺序/结构问题）
        ready: dict[str, int] = {}
        used = cap = 0.0
        if online:
            try:
                facts = client.observation(source="live", text=False).get("facts") or {}
                ready = {str(k).split(":")[0]: int(v)
                         for k, v in (facts.get("buildings") or {}).items()}
                supply = facts.get("supply") or [0, 0]
                used, cap = float(supply[0] or 0), float(supply[1] or 0)
            except ApiError:
                pass
        catalog = load_all()
        if not online:
            # 离线空世界基线：种族主基地默认在场（开局必然有）—— 离线体检查的是
            # **顺序/结构**问题，不是「有没有基地」。注意 produced_by=None 是所有
            # 建筑的属性（工兵建造），不能拿它认「起始建筑」。
            mains = {"terran": "terran/commandcenter", "protoss": "protoss/nexus",
                     "zerg": "zerg/hatchery"}
            race = next((str(it.get("type") or "").split("/")[0]
                         for it in items if "/" in str(it.get("type") or "")), "terran")
            main_id = mains.get(race)
            if main_id and catalog.by_stable_id(main_id) is not None:
                ready[main_id] = ready.get(main_id, 0) + 1
                cap += DEFAULT_ECON.supply_provided.get(main_id, 0)
        issues: list[str] = []
        queued_builds: dict[str, int] = {}
        planned_cap = 0.0
        for i, it in enumerate(items):
            op = str(it.get("op") or "")
            sid = str(it.get("type") or "")
            try:
                count = max(1, int(it.get("count") or 1))
            except (TypeError, ValueError):
                count = 1
            entry = catalog.by_stable_id(sid)
            if entry is None:
                issues.append(f"[error] #{i} {op} {sid}：catalog 不认 —— 先修类型名")
                continue
            zh = entry.display_name_zh
            for req in entry.prerequisites:
                req_e = catalog.by_stable_id(req)
                if req != sid and ready.get(req, 0) + queued_builds.get(req, 0) < 1:
                    issues.append(
                        f"[warn] #{i} {zh}：前置 {req_e.display_name_zh if req_e else req}"
                        f" 既不在场、队列更早处也没有 —— 建议在 #{i} 前插入其建造项")
            if op == "build":
                queued_builds[sid] = queued_builds.get(sid, 0) + count
                planned_cap += DEFAULT_ECON.supply_provided.get(sid, 0) * count
            elif op == "train":
                pb = entry.produced_by
                if pb and ready.get(pb, 0) + queued_builds.get(pb, 0) < 1:
                    pb_e = catalog.by_stable_id(pb)
                    issues.append(
                        f"[error] #{i} {zh}：产出建筑"
                        f"{pb_e.display_name_zh if pb_e else pb}不在场、队列里也没排"
                        f" —— 先建它，否则整队会冻结在这")
                used += entry.cost.supply * count
                if used > cap + planned_cap and cap + planned_cap < 200:
                    deficit = used - cap - planned_cap
                    issues.append(
                        f"[error] #{i} {zh}×{count}：累计要人口 {used:.0f} >"
                        f" 可用 {cap + planned_cap:.0f} —— 建议在 #{i} 前插补给站"
                        f"（还差 {deficit:.0f} 人口，一座 +8）")
                    planned_cap += DEFAULT_ECON.supply_provided["terran/supplydepot"]
        target = (f"在线队列 {args.get('name') or 'main'}" if online
                  else f"规划 {pid}" if pid else "草稿队列")
        out = [f"队列体检（{target}，{len(items)} 项）—— 只诊断不动队列："]
        if issues:
            out.append(f"发现 {len(issues)} 处：")
            out += [f"- {s}" for s in issues]
            out.append("按建议手动插（在线走 propose hunk insert，index = 剩余队列下标；"
                       "离线直接 edit 规划文件），插完 audit_queue 复查。")
        else:
            out.append("通过：没发现卡补给 / 前置缺失 / 产出建筑缺失。")
        return "\n".join(out)

    async def start_session(args: dict) -> str:
        mode = str(args.get("mode") or "fast")
        if mode not in ("normal", "fast"):
            return ("拒绝：mode 只能是 normal（正常模式：玩家可见、实时流速）"
                    "或 fast（仿真模式：快进跑完看实际游戏结果）")
        try:
            speed = float(args.get("speed") or 0)
        except (TypeError, ValueError):
            return "拒绝：speed 是数字（0=不限速/最快，或 1..64 的倍数）"
        if mode == "normal" and speed:
            return "拒绝：正常模式按实时流速跑；倍数（speed）属于仿真模式（mode=fast）"
        strategy = str(args.get("strategy") or "").strip() or None
        loadout = str(args.get("loadout") or "").strip() or None
        production = _validated_production(args.get("production"))
        if isinstance(production, str):        # 校验拒绝（文案即原因）
            return production
        try:
            d = client.session_start(
                driver="sc2", map_plan=args.get("map_plan"), strategy=strategy,
                loadout=loadout, mode=mode, speed=speed,
                autotick=bool(args.get("autotick", True)),
                production=production or None)
        except ApiError as exc:
            return f"开启游戏失败：{_err(exc)}"
        keep = {k: v for k, v in d.items() if k in ("state", "driver", "mode", "speed",
                                                    "map_plan_path", "error")}
        mp = str(args.get("map_plan") or (f"loadout·{loadout}" if loadout else "出厂模板"))
        st = strategy or ("loadout 内含" if loadout else "内置默认")
        sp = "最快" if speed == 0 else f"{speed:g}×"
        tag = "正常模式（实时）" if mode == "normal" else f"仿真模式（快进 {sp}）"
        pd = (f"，采集配额 {json.dumps(production, ensure_ascii=False)}"
              if production else "")
        return (f"游戏已开启（{tag}，map_plan={mp}，strategy={st}{pd}）："
                f"{json.dumps(keep, ensure_ascii=False)}")

    async def sleep_tool(args: dict) -> str:
        """等游戏时间：相对（game_seconds）或绝对（until_game_time）。
        无会话/会话结束（含对局打完的「已结束」）/时钟冻结/墙钟上限都提前返回。"""
        until = args.get("until_game_time")
        if until is not None:
            try:
                target = float(until)
            except (TypeError, ValueError):
                return "拒绝：until_game_time 要是数字（绝对游戏秒，如 180 = 等到 03:00）"
            game_seconds = None
        else:
            try:
                game_seconds = float(args.get("game_seconds") or 0)
            except (TypeError, ValueError):
                return "拒绝：game_seconds 要是数字（游戏秒）"
            if game_seconds <= 0:
                return "拒绝：game_seconds 要 > 0"
            if game_seconds > 600:
                return "拒绝：一次最多等 600 游戏秒；更久分几次等（或用 until_game_time 指定绝对时刻）"
            target = None

        def _gt(info: dict):
            # C 批（2026-08-24）：「已结束/崩溃」也是结束 —— 对局打完（run_game 返回）
            # 后 game_time 冻结，旧判定只认「未连接」→ sleep 空转到墙钟上限
            if not isinstance(info, dict) or info.get("state") in ("未连接", None):
                return None
            if info.get("state") in ("已结束", "崩溃"):
                return ("_ended", info.get("state"), info.get("game_time"))
            return info.get("game_time")

        try:
            start = _gt(client.session())
        except ApiError as exc:
            return f"取会话失败：{_err(exc)}"
        if start is None:
            return "拒绝：没有运行中的会话 —— 游戏时间不会前进。先 start_session 开一局（或等用户开）"
        if isinstance(start, tuple):
            return (f"对局已是「{start[1]}」（开局前就结束了）—— observe 看终局，别等")
        if target is None:
            target = float(start) + game_seconds
        if float(start) >= target:
            return (f"已经到了：当前游戏时间 {float(start):.0f}s ≥ 目标 {target:.0f}s —— 不用等")
        deadline = time.monotonic() + SLEEP_WALL_CAP
        frozen_since: float | None = None   # 时钟冻结检测（SC2 挂死不退出的兜底）
        frozen_wall = time.monotonic()
        woken: set[str] = set()             # 本轮 sleep 已叫醒过的警报 id（同 id 只叫一次）
        while True:
            await asyncio.sleep(SLEEP_POLL_SECS)
            # 用户插话 → 立刻结束等待（2026-08-24：sleep 正是插话的主要窗口）
            if pending is not None:
                msgs = pending.drain()
                if msgs:
                    return ("（用户插话：" + "／".join(msgs) + "）—— sleep 提前结束。"
                            "优先回应用户的插话，处理完再按需继续等待。")
            try:
                info = client.session()
            except ApiError:
                continue    # 后端瞬时取不到：再等一轮（上限兜底）
            gt = _gt(info)
            now = time.monotonic()
            if gt is None:
                return f"会话结束在等待途中（等了不到 {(target - float(start)):.0f} 游戏秒）—— observe 看终局"
            if isinstance(gt, tuple):
                return (f"对局已结束（{gt[1]}，游戏时间停在 {float(gt[2] or 0):.0f}s）—— "
                        "sleep 提前结束。observe 看终局、复盘，别再等")
            # 时钟冻结兜底：状态还是「对局中」但游戏时间不走了（SC2 挂死/停在结算画面
            # 而子进程没退出的窗口）—— 冻满 60 墙秒就叫醒，别空转到 300s 上限
            if frozen_since is None or float(gt) > frozen_since:
                frozen_since = float(gt)
                frozen_wall = now
            elif now - frozen_wall >= SLEEP_FREEZE_WALL_SECS:
                return (f"游戏时钟停在 {float(gt):.0f}s 已 {now - frozen_wall:.0f} 墙秒"
                        "（状态仍是对局中）—— 对局大概率已结束或挂死。"
                        "observe 确认，别干等")
            # 警报唤醒（D 批 2026-08-24 用户设计）：sleep 不再对 warn+ 警报失聪 ——
            # 敌方踪迹/队列卡死这类事件发生时就该醒，不该等 observe 才看见。
            # 同一 id 只叫一次：agent 决定继续睡就让它睡（升级成新 id 的会再叫）。
            hot = [a for a in (info.get("alerts") or [])
                   if a.get("severity") in ("warn", "error")
                   and str(a.get("id")) not in woken]
            if hot:
                for a in hot:
                    woken.add(str(a.get("id")))
                return ("警报叫醒：" + "；".join(str(a.get("text_zh")) for a in hot)
                        + " —— 优先处理（observe 看细节），处理完再按需继续等待")
            if float(gt) >= target:
                return (f"等到游戏时间 {float(gt):.0f}s（+{float(gt) - float(start):.0f} 游戏秒，"
                        f"耗时 {now - (deadline - SLEEP_WALL_CAP):.0f} 墙秒）——可以 observe 看变化了")
            if now >= deadline:
                return (f"墙钟上限 {SLEEP_WALL_CAP:.0f}s 到了：游戏时间才走到 {float(gt):.0f}s"
                        f"（目标 {target:.0f}s）—— 倍速低或等太长；"
                        "缩小 game_seconds、调快仿真倍数，或分几次等")

    async def stop_session(_args: dict) -> str:
        try:
            client.session_stop()
        except ApiError as exc:
            return f"结束会话失败：{_err(exc)}"
        return "会话已结束（子进程树收尾，SC2 一并退出）。"

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
                                                    "description": "干跑秒数，1..600，默认 300"},
                                        "auto_supply": {"type": "boolean",
                                                        "description": ("默认 false：投影不替你补"
                                                                        "供给，卡人口真实浮出（配 audit_queue"
                                                                        " 体检后手动插）")}},
                         "additionalProperties": False},
             function=simulate_plan),
        Tool(name="audit_queue",
             description=("队列体检（只诊断+给建议，不自动改）：检测卡补给/卡科技（前置不在场"
                          "也不在队列）/产出建筑缺失，每条建议给「插什么、插在剩余队列哪个"
                          "下标前」。对象三选一：不给参数=当前会话在线队列（name 选队列名）、"
                          "plan_id=离线规划文件、queue=显式草稿。改法自己动手"
                          "（在线 propose hunk insert / 离线 edit 规划文件），插完复查。"),
             parameters={"type": "object",
                         "properties": {"queue": {"type": "array", "items": {"type": "object"},
                                                  "description": "显式草稿队列（op/type/count 项）"},
                                        "plan_id": {"type": "string", "description": "离线规划 id"},
                                        "name": {"type": "string",
                                                 "description": "在线队列名（默认 main）"}},
                         "additionalProperties": False},
             function=audit_queue),
        Tool(name="start_session",
             description=("开启游戏（真 SC2 对局，两种模式）。mode=fast 仿真模式（默认）："
                          "快进跑完看**实际游戏结果** —— 验证策略/装配/规划就用它，不用问用户；"
                          "mode=normal 正常模式：玩家可见、实时流速，用户在场要看才用。"
                          "loadout：装配清单 id —— 一发入魂（地图规划+策略+生产序列自动入队），"
                          "显式 map_plan/strategy 覆盖它。production：开局采集配额"
                          "（如 {\"mineral_workers\": 8, \"gas_workers\": 3}，目标值语义）。"),
             parameters={"type": "object",
                         "properties": {"mode": {"type": "string", "enum": ["normal", "fast"],
                                                 "description": "normal=正常（实时）；fast=仿真（快进，默认）"},
                                        "speed": {"type": "number",
                                                  "description": "仿真模式倍数：2/4/8…，0=不限速（最快，默认）"},
                                        "map_plan": {"type": "string",
                                                     "description": "地图规划 id，缺省出厂模板"},
                                        "strategy": {"type": "string",
                                                     "description": ("策略文件 id（strategies/<id>.yaml），"
                                                                     "缺省内置默认 —— 验证你写的策略就传它")},
                                        "loadout": {"type": "string",
                                                    "description": ("装配清单 id（三件套引用：map_plan/"
                                                                    "strategy/plan），生产序列自动入队")},
                                        "production": {"type": "object",
                                                       "description": ("开局生产力默认值（采集配额目标值）："
                                                                       "mineral_workers/gas_workers/"
                                                                       "reserve_idle（简写 mineral/gas/idle）"
                                                                       "→ ≥0 整数")},
                                        "autotick": {"type": "boolean",
                                                     "description": "默认 true；仅测试/单步调试传 false"}},
                         "additionalProperties": False},
             function=start_session),
        Tool(name="sleep",
             description=("按**游戏时间**等待（等建造完成/矿攒够/局面推进后再观察）。两种给法："
                          "until_game_time=绝对时刻（如 180 = 等到 03:00）或 game_seconds=相对秒数 —— "
                          "**定时节点用 until，别用相对秒硬凑**。快进（仿真）模式下游戏钟跑得快，"
                          "同样的游戏秒等得更省。一轮里可以 observe → sleep → observe 连着做，"
                          "把等待留给自己、别推给用户。对局结束（已结束/崩溃）、游戏时钟冻结 60 墙秒、"
                          "墙钟 300s 上限都会**提前叫醒**并说明 —— 对局失败不会干等。"),
             parameters={"type": "object",
                         "properties": {
                             "until_game_time": {"type": "number",
                                                 "description": "等到的绝对游戏秒（如 180 = 03:00）"},
                             "game_seconds": {"type": "number",
                                              "description": "要等的游戏秒数（>0，≤600）；与 until 二选一"}},
                         "additionalProperties": False},
             function=sleep_tool),
        Tool(name="stop_session",
             description=("结束当前游戏会话（树杀子进程，SC2 一并退出）。你开的仿真局跑完/卡住就"
                          "用它收尾，别留孤儿游戏进程；**用户在场的正常模式局先问用户再关**。"),
             parameters={"type": "object", "properties": {}, "additionalProperties": False},
             function=stop_session),
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
