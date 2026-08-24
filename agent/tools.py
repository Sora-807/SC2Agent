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
            x1, y1, x2, y2 = (int(v) for v in args.get("bbox") or ())
        except (TypeError, ValueError):
            return "拒绝：bbox 是四个整数 [x1,y1,x2,y2]（左下 + 右上，闭区间）"
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
        # 批 4：自动 step —— 取最小 step≥1 使 列×行 ≤14×14（全量优先，超出降密度）；
        # 14×14 上限保留（用户批注）。不再收 step 参数。
        cols, rows = x2 - x1 + 1, y2 - y1 + 1
        step = 1
        while step < 64 and ((cols + step - 1) // step) * ((rows + step - 1) // step) > 14 * 14:
            step += 1
        area = MapsArea(client, Path(map_plans_dir) if map_plans_dir
                        else Path("runtime/map-plans"))
        path = f"maps/{src}/{x1}_{y1}_{x2}_{y2}" + (f"_s{step}" if step > 1 else "") + ".md"
        try:
            text = area.read(path)
        except WorkspaceError as exc:
            return f"error: {exc}"
        if step > 1:
            text += (f"\n[自动 step={step}] 框选 {cols}×{rows} 超过 14×14 网格上限，"
                     "已自动降密度；要看细节就缩小 bbox。")
        return text

    async def observe(args: dict) -> str:
        if args.get("bbox") is not None:
            return _region_grid(args)
        try:
            obs = client.observation(
                source=source,
                time=float(args["time"]) if args.get("time") is not None else None)
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
            description=("读观察包（批 4 两块：全局状态=资源/工人分任务/建筑汇总含挂件与在建/"
                         "部队汇总/生产序列；区域信息=按矿区列建筑表+部队集群（血量%，敌方：前缀=当前视野））。"
                         "先调它再做判断；它给的 seq 就是提案要回填的 based_on_seq。"
                         "带 bbox 时改读**格点网格**（布局结构：槽位/预设点/地形；建造状态仍走无参 observe）"
                         "—— bbox=[x1,y1,x2,y2]（左下+右上闭区间，全图 176×160），step 自动：≤14×14 全量、"
                         "超出自动降密度并在尾部标注实际 step。source=live（默认，当前会话图层）或地图规划 id；"
                         "time=回看该游戏秒的帧（配录像源复盘）。超范围**如实报错**（说清哪个坐标超了），别瞎试。"),
            parameters={"type": "object", "properties": {
                "bbox": {"type": "array", "items": {"type": "integer"},
                         "minItems": 4, "maxItems": 4,
                         "description": "[x1,y1,x2,y2] 左下 + 右上（闭区间）"},
                "source": {"type": "string",
                           "description": "帧源/地图源 id：live（默认）或地图规划 id"},
                "time": {"type": "number",
                         "description": "回看该游戏秒的帧（录像复盘；省略=最新帧）"},
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
                                        'payload：insert 用 {"before_uid","item"}（before_uid 省略=追加），'
                                        'modify 用 {"uid","item"}，delete 用 {"uid"}，'
                                        'reorder 用 {"order":[…]}（当前全部 uid 的排列）。'
                                        'uid 取自 observe 生产段的 q01/q02… 编号 —— 已执行项保留在队列里，'
                                        '下标会漂移，引用必须走 uid。'
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

    def _producer_zh(sid: str) -> str:
        from game.catalog import load_all
        e = load_all().by_stable_id(sid)
        return (e.display_name_zh if e is not None else sid)

    def _render_sim_v2(r: dict, title: str) -> str:
        """四段输出（templates/simulate-plan-v2-output.md 归一版）。

        事件时间线段落已删除 —— 段落 2 的 started_at/completed_at 就是它。
        """
        from constraint.semantics import SKIP_REASON_ZH, STATUS_ZH

        out = [f"# 干跑 {title}"]
        if r.get("static"):
            out.append("（horizon=0：静态体检，没跑投影）")
            for a in r.get("alerts") or []:
                out.append(f"- [{a.get('severity')}] {a.get('text_zh')}")
            if not (r.get("alerts") or []):
                out.append("通过：没发现前置缺失 / 产出建筑缺失 / 人口超支。")
            return "\n".join(out)

        # 1/4 曲线采样（行数封顶：超长规划抽行显示，防挤掉后面的段落）
        samples = r.get("samples") or []
        out.append("### 1/4 曲线采样")
        if len(samples) > 40:
            stride = (len(samples) + 39) // 40
            shown = samples[::stride]
            if samples[-1] not in shown:
                shown.append(samples[-1])
            out.append(f"（{len(samples)} 个采样点，抽行显示 {len(shown)} 行 —— "
                       "细看某段用 sample_start/sample_interval）")
            samples = shown
        if samples:
            out.append("| t（秒） | 矿 | 气 | 补给(用/上) | 工人(矿/气/建/侦/闲) | 兵营(普闲/科闲) | 工厂 | 星港 |")
            out.append("|---:|---:|---:|---|---|:---:|:---:|:---:|")

            def _idle(p: dict, key: str) -> str:
                if not p:
                    return "—"
                cap, busy = p.get(key + "_cap", 0), p.get(key + "_busy", 0)
                return f"{max(0, cap - busy)}"

            for smp in samples:
                w = smp.get("workers") or {}
                pr = smp.get("producers") or {}
                wk = "/".join(str(w.get(k, 0)) for k in ("mineral", "gas", "building", "scouting", "idle"))
                rax = pr.get("terran/barracks") or {}
                fac = pr.get("terran/factory") or {}
                sp = pr.get("terran/starport") or {}
                out.append(f"| {smp['t']:g} | {smp['minerals']:g} | {smp['gas']:g} "
                           f"| {smp['supply_used']:g}/{smp['supply_cap']:g} | {wk} "
                           f"| {_idle(rax, 'normal')}/{_idle(rax, 'tech')} "
                           f"| {_idle(fac, 'normal')}/{_idle(fac, 'tech')} "
                           f"| {_idle(sp, 'normal')}/{_idle(sp, 'tech')} |")
        else:
            out.append("（采样段为空：sample_start 之后没有采样点）")

        # 2/4 队列执行状态
        rows = r.get("queue_status") or []
        out.append("### 2/4 队列执行状态")
        out.append("| uid | 队列项 | 执行状态 | 开始 | 完成 | 跳过原因 |")
        out.append("|---|---|---|---:|---:|---|")
        for q in rows:
            why = SKIP_REASON_ZH.get(q.get("reason"), "") if q.get("reason") else ""
            out.append(f"| {q.get('uid')} | {q.get('item')} | {STATUS_ZH.get(q.get('status'), q.get('status'))} "
                       f"| {q.get('started_at') if q.get('started_at') is not None else '—'} "
                       f"| {q.get('completed_at') if q.get('completed_at') is not None else '—'} "
                       f"| {why or '—'} |")
        out.append("> 状态：等待中=等矿/气/人口/前置（等一等就满足）｜执行中｜已完成｜"
                   "已跳过=执行失败（原因见列）；没轮到=等待中")

        # 3/4 终值快照
        fin = r.get("final") or {}
        out.append(f"### 3/4 终值快照（t={fin.get('t', 0):g}s）")
        if fin:
            w = fin.get("workers") or {}
            out.append(f"矿 {fin.get('minerals', 0):g}｜气 {fin.get('gas', 0):g}｜"
                       f"人口 {fin.get('supply_used', 0):g}/{fin.get('supply_cap', 0):g}｜"
                       f"工人 矿{w.get('mineral', 0)}/气{w.get('gas', 0)}/"
                       f"建{w.get('building', 0)}/侦{w.get('scouting', 0)}/闲{w.get('idle', 0)}")
            blds = fin.get("buildings") or {}
            if blds:
                out.append("建筑：" + "，".join(f"{_producer_zh(k)}×{v}" for k, v in sorted(blds.items())))
            else:
                out.append("建筑：无")
            us = fin.get("units") or {}
            out.append("部队：" + ("，".join(f"{_producer_zh(k)}×{v}" for k, v in sorted(us.items())) if us else "无"))
            det = fin.get("production_detail") or []
            if det:
                out.append("产线明细（按类型+挂件聚合，近似）：" + "；".join(
                    f"{_producer_zh(d['building'])}({d.get('addon') or '无'})"
                    f"→{'训练 ' + _producer_zh(d['producing']) if d.get('producing') else '空闲'}"
                    for d in det))
            ups = fin.get("upgrades") or []
            out.append("已完成升级：" + ("，".join(_producer_zh(u) for u in ups) if ups else "无"))

        # 4/4 健康检查
        alerts = r.get("alerts") or []
        out.append("### 4/4 健康检查")
        by_sev: dict = {"error": [], "warn": [], "info": []}
        for a in alerts:
            by_sev.setdefault(a.get("severity"), []).append(a)
        if any(by_sev.values()):
            out.append("| 级别 | 类型 | 详情 |")
            out.append("|---|---|---|")
            for sev in ("error", "warn", "info"):
                for a in by_sev.get(sev, []):
                    mark = "🔴" if sev == "error" else ("🟡" if sev == "warn" else "⚪")
                    out.append(f"| {mark} {sev} | {a.get('kind')} | {a.get('text_zh')} |")
        for sev, zh in (("error", "🔴 error"), ("warn", "🟡 warn"), ("info", "⚪ info")):
            if not by_sev.get(sev):
                out.append(f"> {zh}：无")
        skipped = r.get("skipped") or []
        if skipped:
            out.append("被跳过的项（语法/catalog 不认，未入仿）：" + "；".join(
                f"{s.get('op')}: {s.get('reason')}" for s in skipped))
        return "\n".join(out)

    async def simulate_plan(args: dict) -> str:
        body: dict = {}
        src_note = []
        if args.get("queue"):
            body["items"] = args["queue"]
        pid = str(args.get("plan_id") or "").strip() or None
        if pid:
            body["plan_id"] = pid
            src_note.append(pid)
        qname = str(args.get("queue_name") or "").strip() or None
        if qname:
            body["queue_name"] = qname
            src_note.append(f"在线队列 {qname}")
        if args.get("from_session"):
            body["from_session"] = True
            src_note.append("当前会话")
        if not (body.get("items") or pid or qname or args.get("from_session")):
            return ("拒绝：queue / plan_id / queue_name / from_session 至少给一个"
                    "（都给则以 queue 为准）")
        try:
            raw_h = args.get("horizon")
            horizon = min(600.0, max(0.0, float(raw_h if raw_h is not None else 300.0)))
        except (TypeError, ValueError):
            return "拒绝：horizon 必须是秒数（0..600；0 = 只做静态体检不跑投影）"
        body["horizon"] = horizon
        if args.get("sample_interval"):
            body["sample_interval"] = int(args["sample_interval"])
        if args.get("sample_start"):
            body["sample_start"] = float(args["sample_start"])
        if args.get("initial_state") is not None:
            body["initial_state"] = args["initial_state"]
            note = args["initial_state"] if isinstance(args["initial_state"], str) else "内联"
            src_note.append(f"起点 {note}")
        try:
            r = client.plans_simulate(body)
        except ApiError as exc:
            return f"干跑失败：{_err(exc)}"
        title = "＋".join(src_note) or "草稿队列"
        title += f"（horizon {horizon:g}s）"
        return _clip(_render_sim_v2(r, title))

    async def export_snapshot(args: dict) -> str:
        """从活跃会话导出状态快照 + 剩余队列（I6）：可直接喂回 simulate_plan。"""
        save_as = str(args.get("id") or "").strip() or None
        try:
            out = client.session_export(save_as=save_as)
        except ApiError as exc:
            return f"导出失败：{_err(exc)}"
        doc = out.get("initial_state") or {}
        w = doc.get("workers") or {}
        lines = [f"# 会话快照（t={out.get('game_time', 0):g}s）"
                 + (f" → 已存 initial-states/{save_as}.yaml" if save_as else "")]
        lines.append(f"矿 {doc.get('minerals', 0)}｜气 {doc.get('gas', 0)}｜"
                     f"人口 {doc.get('supply_used', 0)}/{doc.get('supply_cap', 0)}｜"
                     f"工人 矿{w.get('mineral', 0)}/气{w.get('gas', 0)}/"
                     f"建{w.get('building', 0)}/侦{w.get('scouting', 0)}/闲{w.get('idle', 0)}")
        blds = doc.get("buildings") or {}
        if blds:
            lines.append("建筑：" + "，".join(f"{k}×{v}" for k, v in sorted(blds.items())))
        q = out.get("queue") or []
        lines.append(f"剩余队列（{len(q)} 项，带 uid/status）：" if q else "剩余队列：空")
        for it in q[:10]:
            n = f" ×{it['count']}" if (it.get("count") or 1) > 1 else ""
            lines.append(f"- {it.get('uid') or '?'} {it.get('op')} {it.get('type') or ''}{n}"
                         f" [{it.get('status')}]"
                         + (f"（{it.get('reason')}）" if it.get("reason") else ""))
        if len(q) > 10:
            lines.append(f"…还有 {len(q) - 10} 项")
        lines.append("用法：simulate_plan(initial_state="
                     + (f'"{save_as}"' if save_as else "导出的状态对象")
                     + ", queue=上面的队列, ...) 预演后续。")
        if out.get("note"):
            lines.append(f"[近似] {out['note']}")
        return "\n".join(lines)

    # ---- 会话（「开启游戏」两模式，2026-08-23 用户拍板收敛）----

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
             description=("干跑 v2（四段输出：曲线采样/队列执行状态/终值快照/健康检查）。"
                          "队列四选一：queue 显式草稿 / plan_id 规划文件 / queue_name 在线队列 / "
                          "from_session 当前会话。起点三选一：initial_state（字符串=引用 "
                          "initial-states/<id>，对象=内联）/ from_session / 缺省标准开局。"
                          "horizon=0 = 只做静态体检（不跑投影，吸收了原 audit_queue：前置/产出建筑/人口对账）。"
                          "**改过规划必须干跑** —— "
                          "没有试算的改动不算完成。"),
             parameters={"type": "object",
                         "properties": {"queue": {"type": "array", "items": {"type": "object"}},
                                        "plan_id": {"type": "string"},
                                        "queue_name": {"type": "string",
                                                       "description": "在线队列名（默认 main）—— 对局中预演"},
                                        "from_session": {"type": "boolean",
                                                         "description": "取当前会话的状态+剩余队列当起点"},
                                        "horizon": {"type": "number",
                                                    "description": "干跑秒数 0..600（默认 300；0=静态体检）"},
                                        "sample_interval": {"type": "integer",
                                                            "description": "采样间隔秒（默认 10）"},
                                        "sample_start": {"type": "number",
                                                         "description": "采样开始秒（默认 0，只看某段）"},
                                        "initial_state": {"description":
                                                          "起点：字符串=引用 initial-states/<id>（read initial-states/ 看有哪些），"
                                                          "对象=内联一次性 {minerals,gas,supply_used,supply_cap,workers,buildings,units,upgrades}"}},
                         "additionalProperties": False},
             function=simulate_plan),
        Tool(name="export_snapshot",
             description=("从当前会话导出状态快照 + 剩余队列（带 uid/status）：给 id 就存成 "
                          "initial-states/<id>.yaml（可复用），不给只返回。导出的状态和队列"
                          "可直接喂 simulate_plan 预演后续（from_session 是它的一次性版）。"),
             parameters={"type": "object",
                         "properties": {"id": {"type": "string",
                                               "description": "存成 initial-states/<id>.yaml（省略 = 只返回不落盘）"}},
                         "additionalProperties": False},
             function=export_snapshot),
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
