"""view.observe：ObservationPacket —— 给 agent 的帧投影（B10）。

**红线**：严禁为 agent 另建一条"从 GameState 直接摘要"的路径。那就是 ADR-0007 禁止的
"第二份派生"的 agent 版 —— 后果是 agent 看到的世界和 UI 看到的对不上，
而两边都说自己是对的。所以这里的输入就是**已有的 ViewFrame**（`latest_at()` 的输出）。

形态对齐 ADR-0009 §2：`seq` / `game_time` / `supersedes` / `sections`。
规则是"**替换**"而不是"追加"：新 packet 生成后旧的立即 superseded，
prompt 里只注入当前这一份（否则上下文无限膨胀，且 agent 会拿旧观察当依据 —— R8）。

`facts.based_on_seq` 是给命令用的：agent 下命令时必须回填它，
后端会拒掉过期的（`api.session.MAX_STALE_SEQ`）。这就是 R8 在 agent 侧的闭环。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from game.catalog import Catalog

from view.proposals import STATUS_PENDING, STATUS_REJECTED, STATUS_STALE

#: 投影摘要只看未来这么久（ADR-0009 §1 的"30 秒投影"量级）。
#: 更远的以后还会重算，写进 prompt 只是噪声。
PROJECTION_LOOKAHEAD = 30.0
#: 最近转移只给这么多条（完整历史进 trace，不进 prompt）
RECENT_TRANSITIONS = 5
#: 提案历史只给最近这么多条。给它是为了**闭上 §6 P3 的环**：
#: 用户拒绝时写的理由必须回流，否则 agent 会一遍遍推同一个被拒的提案。
RECENT_PROPOSALS = 6


@dataclass(slots=True)
class ObservationPacket:
    """一份"当前事实"。旧 packet 由 `supersedes` 指向，只保留在 trace 里。"""

    seq: int
    game_time: float
    supersedes: int | None
    #: 名称 → 人类/LLM 可读文本。**中文**：catalog 里就是中文名，翻回英文是白丢信息
    sections: dict[str, str]
    #: 机器可读的关键数字（agent 下命令要用；不用从文本里再解析一遍）
    facts: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """拼成一段可直接进 prompt 的文本。顶部声明新鲜度（ADR-0009 §5）。"""
        head = (f"# 当前观察（seq={self.seq}，游戏时间 {_mmss(self.game_time)}）\n"
                f"只以本 packet 为行动依据；下命令时 based_on_seq={self.seq}。\n")
        body = "\n".join(f"\n## {name}\n{text}" for name, text in self.sections.items() if text)
        return head + body


def observation_packet(
    frames: dict[str, dict],
    *,
    catalog: Catalog,
    supersedes: int | None = None,
) -> ObservationPacket:
    """`{topic: 信封}` → ObservationPacket。

    `frames` 直接用 `session.latest_at(now)` 或 `JsonlSource.latest_at()` 的输出转成的字典 ——
    也就是**UI 看到的同一批帧**。
    """
    world = _payload(frames, "frame/world")
    flow = _payload(frames, "frame/flow")
    prod = _payload(frames, "frame/production")
    econ = _payload(frames, "frame/economy")
    proj = _payload(frames, "frame/projection")
    alerts = _payload(frames, "frame/alerts")
    session = _payload(frames, "frame/session")
    strategy = _payload(frames, "static/strategy")
    proposals = _payload(frames, "proposals")
    ops = _payload(frames, "frame/ops")

    seq = int(_env(frames, "frame/world").get("seq", 0)) if "frame/world" in frames else 0
    game_time = float(_env(frames, "frame/world").get("game_time", 0.0)) if "frame/world" in frames else 0.0
    zh = _zh_of(catalog)

    sections = {
        "会话": _session_text(session),
        # 批 4（模板 observe-output.md）：无 bbox 输出两块 —— 全局状态 + 区域信息
        "全局状态": _global_text(world, econ, prod, catalog, zh),
        "区域信息": _areas_text(world, catalog, zh),
        "编组": _groups_text(flow, zh),
        "op 流水": _ops_text(ops, zh),
        "策略": _strategy_text(flow, strategy),
        "风险": _alerts_text(alerts),
        "投影": _projection_text(proj, game_time, zh),
        "生产队列": _production_text(prod, zh),
        "提案历史": _proposals_text(proposals),
    }
    facts = {
        "based_on_seq": seq,
        "game_time": round(game_time, 3),
        "minerals": (world or {}).get("economy", {}).get("minerals"),
        "vespene": (world or {}).get("economy", {}).get("vespene"),
        "supply": [(world or {}).get("economy", {}).get("supply_used"),
                   (world or {}).get("economy", {}).get("supply_cap")],
        "queues": [q["name"] for q in (prod or {}).get("queues", [])],
        "blocked_queues": [q["name"] for q in (prod or {}).get("queues", [])
                           if q.get("blocked")],
        "active_step": ((flow or {}).get("strategies") or [{}])[0].get("active_step"),
        "alert_kinds": sorted({a["kind"] for a in (alerts or {}).get("alerts", [])}),
        "pending_proposals": [p["id"] for p in (proposals or {}).get("proposals", [])
                              if p["status"] == STATUS_PENDING],
        "rejected_titles": [p["title_zh"] for p in (proposals or {}).get("proposals", [])
                            if p["status"] == STATUS_REJECTED],
        # §0.52 E 批：机器可读副本（agent 下命令/做算术不用从中文文本里解析）
        "buildings": _count_buildings(world, catalog),
        "army": _count_army(world, catalog),
    }
    return ObservationPacket(seq=seq, game_time=round(game_time, 3), supersedes=supersedes,
                             sections={k: v for k, v in sections.items() if v}, facts=facts)


# ---------------- 各段 ----------------

def _worker_split(world: dict | None, econ: dict | None, prod: dict | None) -> dict:
    """工人五分：mineral/gas/idle 取经济帧 actual；building=在途建造数派生；
    scouting 恒 0（D3：侦查是编组派生）。总数对不上时如实标注 other。"""
    tasks = {t.get("task"): int(t.get("actual") or 0) for t in (econ or {}).get("tasks") or []}
    building = len((prod or {}).get("in_flight") or [])
    scvs = [u for u in (world or {}).get("units") or []
            if u.get("owner") == "self" and (u.get("stable_id") or "").endswith("/scv")]
    total = len(scvs)
    # D3：侦查 = 编入 flow 组的 SCV（group_id 非空）—— 仿真侧恒 0（无编组）
    scouting = sum(1 for u in scvs if u.get("group_id"))
    mineral = tasks.get("mineral", 0)
    gas = tasks.get("gas", 0)
    idle = tasks.get("idle", 0)
    other = max(0, total - mineral - gas - idle - building - scouting)
    return {"mineral": mineral, "gas": gas, "building": building,
            "scouting": scouting, "idle": idle, "other": other, "total": total}


_ADDON_ZH = {"techlab": "科技实验室", "reactor": "反应堆"}


def _global_text(world, econ, prod, catalog: Catalog, zh) -> str:
    """全局状态：资源 / 工人分布 / 建筑汇总（挂件分布+在建）/ 部队汇总 / 生产序列。"""
    if not world:
        return ""
    eco = world.get("economy") or {}
    out = [f"资源：矿 {eco.get('minerals', 0)}｜气 {eco.get('vespene', 0)}｜"
           f"人口 {eco.get('supply_used', 0)}/{eco.get('supply_cap', 0)}"]
    w = _worker_split(world, econ, prod)
    extra = f"（另有 {w['other']} 未分类）" if w["other"] else ""
    out.append(f"工人：采矿 {w['mineral']}｜采气 {w['gas']}｜建造 {w['building']}｜"
               f"侦查 {w['scouting']}｜空闲 {w['idle']}{extra}")
    # 建筑汇总：类型 → 总数 + 挂件分布 + 在建
    rows: dict[str, dict] = {}
    for u in world.get("units") or []:
        if u.get("owner") != "self":
            continue
        sid = u.get("stable_id") or ""
        e = catalog.by_stable_id(sid)
        if e is None or (e.role and e.role.value != "building"):
            continue
        rec = rows.setdefault(sid, {"total": 0, "techlab": 0, "reactor": 0, "wip": 0})
        rec["total"] += 1
        if u.get("addon") in rec:
            rec[u["addon"]] += 1
        if (u.get("build_progress") or 1) < 1:
            rec["wip"] += 1
    if rows:
        out.append("建筑汇总：")
        for sid, r in sorted(rows.items()):
            add = ""
            if r["techlab"] or r["reactor"]:
                add = (f"（科技 {r['techlab']} / 反应堆 {r['reactor']} / 无 "
                       f"{r['total'] - r['techlab'] - r['reactor']}）")
            wip = f"，在建 {r['wip']}" if r["wip"] else ""
            out.append(f"- {zh(sid)}：{r['total']}{add}{wip}")
    else:
        out.append("建筑汇总：无")
    army = _count_army(world, catalog)
    out.append("部队汇总：" + ("，".join(f"{zh(k)}×{v}" for k, v in sorted(army.items()))
                             if army else "无"))
    # 生产序列：正在训练（账本 + 建筑 producing 双来源）× 待训练（队列 train 计数，
    # 旧录像的 status 值（未处理/队首阻塞）也算未执行 —— 回放优先）
    training: dict[str, int] = {}
    for t in (prod or {}).get("training") or []:
        training[t["stable_id"]] = training.get(t["stable_id"], 0) + 1
    for u in world.get("units") or []:
        for pr in u.get("producing") or []:
            training[pr["stable_id"]] = training.get(pr["stable_id"], 0) + 1
    queued: dict[str, int] = {}
    for q in (prod or {}).get("queues") or []:
        for it in q.get("items") or []:
            if it.get("op") == "train" and it.get("status") not in ("completed", "skipped"):
                queued[it["stable_id"]] = queued.get(it["stable_id"], 0) + max(1, it.get("count") or 1)
    if training or queued:
        out.append("生产序列：" + "；".join(
            f"{zh(k)} 训练 {v}/排队 {queued.get(k, 0)}" for k, v in sorted(training.items()))
            + ("｜排队（未开训）：" + "，".join(f"{zh(k)}×{v}" for k, v in sorted(queued.items())
                                              if k not in training) if queued else ""))
    return "\n".join(out)


def _areas_text(world, catalog: Catalog, zh) -> str:
    """区域信息：按基础数据矿区分区（批 2 落的 mine_areas，坐标待校准已标注）。

    每矿区：建筑表（坐标/挂件/正在做什么）+ 部队表（兵种/集群/数量/坐标/血量%/绝对
    血量；`敌方：` 前缀 = 当前帧视野内；历史踪迹在 enemy_contact 警报不混入）。
    """
    from tactical_map.mine_areas import load_mine_areas

    if not world:
        return ""
    areas = load_mine_areas()
    if not areas:
        return ""
    units = world.get("units") or []

    def _in(u, a):
        x, y = u.get("pos") or (0, 0)
        return a.contains(x, y)

    out = []
    leftovers = list(units)
    for a in areas:
        inside = [u for u in units if _in(u, a)]
        if not inside:
            out.append(f"### {a.name}（{a.side}）\n无建筑，无部队")
            continue
        for u in inside:
            leftovers.remove(u)
        bl = [u for u in inside if u.get("owner") == "self"
              and (e := catalog.by_stable_id(u.get("stable_id") or "")) is not None
              and e.role and e.role.value == "building"]
        troops = [u for u in inside
                  if (u.get("owner") in ("self", "enemy"))
                  and u.get("stable_id") and not (u.get("stable_id") or "").endswith("/scv")]
        head = [f"### {a.name}（{a.side}）"]
        if bl:
            head.append("建筑：")
            for u in bl:
                x, y = u.get("pos") or (0, 0)
                add = _ADDON_ZH.get(u.get("addon")) or ("无" if catalog.by_stable_id(
                    u["stable_id"]).capabilities and "train" in
                    catalog.by_stable_id(u["stable_id"]).capabilities else "—")
                doing = "空闲"
                prod_of = u.get("producing") or []
                if (u.get("build_progress") or 1) < 1:
                    doing = f"建造中（{int((u.get('build_progress') or 0) * 100)}%）"
                elif prod_of:
                    doing = "训练 " + zh(prod_of[0]["stable_id"])
                head.append(f"- {zh(u['stable_id'])} ({x:.0f},{y:.0f})｜挂件 {add}｜{doing}")
        else:
            head.append("建筑：无")
        rows = []
        for owner, prefix in (("self", ""), ("enemy", "敌方：")):
            pool = [u for u in troops if u.get("owner") == owner]
            if not pool:
                continue
            items = [{"x": (u.get("pos") or (0, 0))[0], "y": (u.get("pos") or (0, 0))[1],
                      "stable_id": u.get("stable_id"), "hp": u.get("hp", 0),
                      "hp_max": u.get("hp_max", 0)} for u in pool]
            for c in _cluster_payload(items):
                kinds = "，".join(f"{zh(k)}×{v}" for k, v in sorted(c["by_stable_id"].items()))
                hp_pct = f"{c['hp_pct']:.0f}%" if c.get("hp_pct") is not None else "?"
                rows.append(f"- {prefix}{kinds}｜集群 {c['count']} 单位 @({c['center'][0]:.0f},"
                            f"{c['center'][1]:.0f})｜血量 {hp_pct}（{c['hp_total']:.0f}）")
        head.append("部队：" + ("\n".join(rows) if rows else "无"))
        out.append("\n".join(head))
    n_out = sum(1 for u in leftovers
                if u.get("owner") in ("self", "enemy") and not (u.get("stable_id") or "").endswith("/scv"))
    n_bl = sum(1 for u in leftovers if u.get("owner") == "self")
    if n_out or n_bl:
        out.append(f"### 矿区外（未划入任何矿区）\n"
                   f"约 {n_bl} 我方单位/建筑、{n_out} 部队 —— "
                   "矿区坐标待校准（mine_areas.yaml），部分单位暂落此栏")
    return "\n\n".join(out)


def _cluster_payload(items: list[dict]) -> list[dict]:
    from view.clusters import cluster_units

    return cluster_units(items)


def _session_text(session: dict | None) -> str:
    if not session:
        return ""
    parts = [f"状态 {session.get('state')}", f"帧源 {session.get('frame_source')}"]
    if session.get("map_name"):
        parts.append(f"地图 {session['map_name']}")
    if session.get("my_race"):
        parts.append(f"{session['my_race']} vs {session.get('enemy_race') or '?'}")
    if session.get("error"):
        parts.append(f"错误 {session['error']}")
    return " · ".join(parts)


def _economy_text(world: dict | None, econ: dict | None, zh) -> str:
    if not world:
        return ""
    e = world["economy"]
    lines = [f"矿 {e['minerals']} / 气 {e['vespene']} / 供给 {e['supply_used']}·{e['supply_cap']}"]
    if e["supply_used"] >= e["supply_cap"] and e["supply_cap"] < 200:
        lines.append("**已卡人口**")
    if econ:
        for t in econ["tasks"]:
            name = {"mineral": "采矿", "gas": "采气", "idle": "备用"}.get(t["task"], t["task"])
            quota = "按比例" if t["quota"] is None else f"维持{t['quota']}"
            lines.append(f"{name}：实际 {t['actual']} / 可达 {t['target']}（{quota}）")
        if econ["reserved"]:
            lines.append(f"建造征用 {len(econ['reserved'])} 人（维持器不会改派它们）")
    own = sum(1 for u in world["units"] if u["owner"] == "self")
    enemy = sum(1 for u in world["units"] if u["owner"] == "enemy")
    lines.append(f"可见单位：我方 {own}，敌方 {enemy}")
    return "\n".join(lines)


def _groups_text(flow: dict | None, zh) -> str:
    if not flow or not flow.get("groups"):
        return ""
    out = []
    for g in flow["groups"]:
        comp = "，".join(f"{zh(k)} {v['current']}/{v['target']}"
                         for k, v in g["composition"].items())
        center = (f"组心 {g['center'][0]:.0f},{g['center'][1]:.0f}" if g.get("center") else "组心 —")
        out.append(f"{g['group_id']}：{comp}（{g['refill_state']}）· {center}")
    return "\n".join(out)


def _own_units(world: dict | None) -> list:
    return [u for u in (world or {}).get("units", []) if u.get("owner") == "self"]


def _role_of(catalog: Catalog, stable_id: str) -> str | None:
    entry = catalog.by_stable_id(stable_id)
    return entry.role.value if entry is not None else None


def _count_buildings(world: dict | None, catalog: Catalog) -> dict[str, int]:
    """facts 副本：建成建筑计数（挂件算在其宿主上，键带 :reactor/:techlab 后缀）。"""
    out: dict[str, int] = {}
    for u in _own_units(world):
        if _role_of(catalog, u["stable_id"]) != "building":
            continue
        if u.get("build_progress", 1.0) < 1.0:
            continue
        key = u["stable_id"] + (f":{u['addon']}" if u.get("addon") else "")
        out[key] = out.get(key, 0) + 1
    return out


def _count_army(world: dict | None, catalog: Catalog) -> dict[str, int]:
    out: dict[str, int] = {}
    for u in _own_units(world):
        if _role_of(catalog, u["stable_id"]) == "combat":
            out[u["stable_id"]] = out.get(u["stable_id"], 0) + 1
    return out


def _army_text(world: dict | None, prod: dict | None, catalog: Catalog, zh) -> str:
    """已有全部部队（§0.52 E 批，用户结构化读法）：不只 flow 编成的组 ——
    未编组/散兵也在；在训（world producing）与待训（队列 train 项）各一行。"""
    own = _own_units(world)
    army = [u for u in own if _role_of(catalog, u["stable_id"]) == "combat"]
    if not army:
        return ""
    by_id: dict[str, int] = {}
    for u in army:
        label = zh(u["stable_id"]) + (f"·{u['form']}" if u.get("form") else "")
        by_id[label] = by_id.get(label, 0) + 1
    out = ["已有：" + "，".join(f"{k}×{n}" for k, n in sorted(by_id.items()))]
    # 在训：建筑 producing 里的单位（SC2 不给进度，只给名单）
    training: dict[str, int] = {}
    for u in own:
        for p in u.get("producing") or ():
            sid = p.get("stable_id")
            if sid:
                training[sid] = training.get(sid, 0) + 1
    if training:
        out.append("在训：" + "，".join(f"{zh(k)}×{v}" for k, v in sorted(training.items())))
    # 待训：还在队列里的 train 项
    queued: dict[str, int] = {}
    for q in (prod or {}).get("queues", []):
        for it in q.get("items", []):
            if it.get("op") == "train" and it.get("stable_id"):
                queued[it["stable_id"]] = queued.get(it["stable_id"], 0) + max(1, it.get("count", 1))
    if queued:
        out.append("待训（排队）：" + "，".join(f"{zh(k)}×{v}" for k, v in sorted(queued.items())))
    return "\n".join(out)


def _buildings_text(world: dict | None, catalog: Catalog, zh) -> str:
    """关键建筑数量（含挂件，§0.52 E 批）：建成按类计数、挂件点名、在建单列带进度。"""
    own = _own_units(world)
    buildings = [u for u in own if _role_of(catalog, u["stable_id"]) == "building"]
    if not buildings:
        return ""
    done: dict[str, int] = {}
    addons: dict[str, int] = {}
    building_up: list[str] = []
    for u in buildings:
        if u.get("build_progress", 1.0) < 1.0:
            building_up.append(f"{zh(u['stable_id'])} {u['build_progress'] * 100:.0f}%")
            continue
        sid = u["stable_id"]
        done[sid] = done.get(sid, 0) + 1
        if u.get("addon"):
            addons[u["addon"]] = addons.get(u["addon"], 0) + 1
    parts = ["，".join(f"{zh(k)}×{v}" for k, v in sorted(done.items()))]
    if addons:
        parts.append("挂件：" + "，".join(
            ({"reactor": "反应堆", "techlab": "科技实验室"}.get(k, k)) + f"×{v}"
            for k, v in sorted(addons.items())))
    if building_up:
        parts.append("在建：" + "，".join(building_up))
    return "\n".join(parts)


def _regions_text(world: dict | None, econ: dict | None, catalog: Catalog, zh) -> str:
    """区域信息（§0.52 E 批，用户结构化读法）：按经济基地分桶 —— 桶内建筑逐个
    （坐标+血量%），部队按类计数（首个坐标）；远离所有基地的归「机动」。
    只读文字，不做地形/网格（那是 I8 inspect_region 的地盘）。"""
    own = _own_units(world)
    if not own:
        return ""
    by_tag = {u["tag"]: u for u in own if u.get("tag") is not None}
    # 基地 = 经济节点的 base_tag（正在运营的主基地/分矿），去重取位置
    centers: list[tuple[str, tuple[float, float]]] = []
    seen_tags: set[int] = set()
    for n in (econ or {}).get("nodes", []):
        bt = n.get("base_tag")
        base = by_tag.get(bt)
        if base is None or bt in seen_tags or not base.get("pos"):
            continue
        seen_tags.add(bt)
        centers.append((zh(base["stable_id"]), (float(base["pos"][0]), float(base["pos"][1]))))
    if not centers:
        return ""
    FAR = 30.0   # 距所有基地都超过这个距离 = 机动部队，不硬塞给最近的基地

    def _bucket(pos) -> int | None:
        best_i, best_d = None, 1e18
        for i, (_name, c) in enumerate(centers):
            d = abs(pos[0] - c[0]) + abs(pos[1] - c[1])   # 曼哈顿够用（文本归桶）
            if d < best_d:
                best_i, best_d = i, d
        return best_i if best_d <= FAR else None

    lines: list[str] = []
    mobile: dict[str, int] = {}
    for i, (name, c) in enumerate(centers):
        bl: list[str] = []
        army: dict[str, tuple[int, tuple[float, float]]] = {}
        for u in own:
            pos = u.get("pos") or [0.0, 0.0]
            if _bucket(pos) != i:
                continue
            role = _role_of(catalog, u["stable_id"])
            hp_pct = f"{(u.get('hp', 0) / u.get('hp_max', 1)) * 100:.0f}%" if u.get("hp_max") else "?"
            if role == "building":
                prog = "" if u.get("build_progress", 1.0) >= 1.0 else \
                    f"（建 {u['build_progress'] * 100:.0f}%）"
                bl.append(f"{zh(u['stable_id'])}@{pos[0]:.0f},{pos[1]:.0f} {hp_pct}{prog}")
            elif role == "combat":
                key = zh(u["stable_id"])
                n, first = army.get(key, (0, (pos[0], pos[1])))
                army[key] = (n + 1, first)
        seg = [f"{name}基地 @{c[0]:.0f},{c[1]:.0f}："]
        seg.extend(bl[:6])
        seg.extend(f"{k}×{n}@{p[0]:.0f},{p[1]:.0f}" for k, (n, p) in
                   sorted(army.items(), key=lambda kv: -kv[1][0]))
        lines.append(" ".join(seg))
    for u in own:
        pos = u.get("pos") or [0.0, 0.0]
        if (_role_of(catalog, u["stable_id"]) == "combat"
                and _bucket(pos) is None):
            key = zh(u["stable_id"])
            mobile[key] = mobile.get(key, 0) + 1
    if mobile:
        lines.append("机动（远离基地）：" +
                     "，".join(f"{k}×{v}" for k, v in sorted(mobile.items())))
    return "\n".join(lines)


def _production_text(prod: dict | None, zh) -> str:
    """ADR-0032 账本：uid + 四态状态（等待中/执行中/已完成/已跳过）+ skip 原因。

    agent 的 propose hunk 用 before_uid 引用 —— uid 必须在这里可见；
    已完成/已跳过的历史项折叠成一行计数，执行细节留给前 N 项未完成项。
    """
    if not prod:
        return ""
    from constraint.semantics import SKIP_REASON_ZH, STATUS_ZH

    out = []
    for q in prod.get("queues", []):
        items = q.get("items", [])
        todo = [it for it in items if it.get("status") not in ("completed", "skipped")]
        done = sum(1 for it in items if it.get("status") == "completed")
        skip = sum(1 for it in items if it.get("status") == "skipped")
        head = (f"队列 {q['name']}（待执行 {len(todo)} 项"
                + (f"，已完成 {done}" if done else "")
                + (f"，已跳过 {skip}" if skip else "") + "）")
        if q.get("blocked"):
            b = q["blocked"]
            who = f"uid={b['uid']} " if b.get("uid") else ""
            head += f" —— {who}已阻塞 {b['waited']:.0f}s：{b['reason']}"
        out.append(head)
        for it in todo[:8]:
            what = (f"维持 {it['task']}" if it["op"] == "assign_workers" else zh(it["stable_id"]))
            n = f" ×{it['count']}" if it["count"] > 1 else ""
            uid = it.get("uid") or it["index"]
            st = STATUS_ZH.get(it["status"], it["status"])
            why = (f"（{SKIP_REASON_ZH.get(it['reason'], it['reason'])}）"
                   if it.get("reason") else "")
            wait = f" —— {it['block_reason']}" if it.get("block_reason") else ""
            out.append(f"  {uid}. {it['op']} {what}{n} [{st}]{why}{wait}")
        if len(todo) > 8:
            out.append(f"  …还有 {len(todo) - 8} 项待执行")
        for it in items:
            if it.get("status") == "skipped":
                what = zh(it.get("stable_id"))
                out.append(f"  {it.get('uid') or it['index']}. {what}"
                           f" [已跳过]（{SKIP_REASON_ZH.get(it['reason'], it['reason'])}）")
    if prod.get("in_flight"):
        parts = []
        for f in prod.get("in_flight", []):
            origin = f"来源 {f['uid']}，" if f.get("uid") else ""
            seg = f"{zh(f['stable_id'])}（{origin}等待 {f['frames_waited']} 帧"
            if f["retries"]:
                seg += f"，重试 {f['retries']}"
            parts.append(seg + "）")
        out.append("在途：" + "，".join(parts))
    if prod.get("dropped"):
        out.append("被丢弃（作者错误）：" + "；".join(
            f"{d['op']} {zh(d['stable_id'])} —— {d['reason']}" for d in prod["dropped"][-3:]))
    return "\n".join(out)


def _ops_text(ops: dict | None, zh) -> str:
    """op 流水（frame/ops 的环形缓冲取最近几条）：命令真的发出去没有，看这里。"""
    rows = (ops or {}).get("ops") or []
    if not rows:
        return ""
    out = []
    for o in rows[-5:]:
        what = zh(o["params"].get("type")) if o["action"] in ("build", "train", "research") \
            else o["action"]
        apply_state = {True: "✓", False: "✗"}.get(o.get("apply", {}).get("ok") if o.get("apply") else None, "…")
        out.append(f"[{o['at']:>6.0f}s] {o['origin']}/{o['action']} {what} {apply_state}")
    return "\n".join(out)


def _strategy_text(flow: dict | None, strategy: dict | None) -> str:
    if not flow or not flow.get("strategies"):
        return ""
    s = flow["strategies"][0]
    out = [f"{s['strategy_ref']} v{s['version']} · 当前 step {s['active_step']}"
           f"（第 {s['step_entry_count']} 次进入，驻留 {s['step_elapsed']:.0f}s）"]
    if s.get("branch_hit"):
        out.append(f"本帧命中分支 {s['branch_hit']['branch_id'] or '#' + str(s['branch_hit']['index'])}")
    else:
        out.append("本帧没有命中任何分支（等待型 step）")
    for t in (s.get("transitions") or [])[-RECENT_TRANSITIONS:]:
        out.append(f"转移 {t['from']} → {t['to']}（{t['reason']}）@{_mmss(t['at'])}")
    out.append(f"转移计数 {s['transition_count']}/{s['transition_limit']}")
    if s.get("exit_record"):
        out.append(f"**策略已结束**：{s['exit_record']['kind']}/{s['exit_record']['reason']}")
    if s.get("eval_diagnostics"):
        out.append("求值诊断：" + "；".join(
            f"{d['step_id']} {d['kind']} {d['detail']}×{d['count']}"
            for d in s["eval_diagnostics"][:3]))
    if strategy:
        out.append("可走的边：" + "，".join(
            f"{e['from']}→{e['to']}({e['reason']})" for e in strategy.get("edges", [])))
    return "\n".join(out)


def _alerts_text(alerts: dict | None) -> str:
    if not alerts or not alerts.get("alerts"):
        return ""
    return "\n".join(f"[{a['severity']}] {a['text_zh']}" for a in alerts["alerts"])


def _proposals_text(proposals: dict | None) -> str:
    """我之前提过什么、结果如何、**为什么被拒**。

    这是 §6 P3 的另一半：用户拒绝时写的理由必须回到 agent 面前，
    否则它会一遍遍推同一个被拒的提案（而且每次都觉得自己是第一次想到）。
    """
    rows = (proposals or {}).get("proposals") or []
    if not rows:
        return ""
    # **被拒的和待审批的永远带上**，不受"最近 N 条"窗口限制：
    # 它们是最需要影响下一步决策的两类，被窗口截掉就等于回流失效（实测踩过）。
    must = [p for p in rows if p["status"] in (STATUS_REJECTED, STATUS_PENDING)]
    recent = [p for p in rows[-RECENT_PROPOSALS:] if p not in must]
    shown = must[-RECENT_PROPOSALS:] + recent
    out: list[str] = []
    for p in shown:
        line = f"[{p['status']}] {p['title_zh']}"
        decision = p.get("decision") or {}
        if decision.get("comment_zh"):
            line += f" —— 用户说：{decision['comment_zh']}"
        if p["status"] == STATUS_STALE:
            line += "（基于的世界已经过去了，要重提就基于当前状态）"
        if (p.get("validation") or {}).get("ok") is False:
            errs = "；".join(e.get("text_zh", "") for e in p["validation"].get("errors", []))
            line += f"（校验未通过：{errs}）"
        out.append(line)
    pending = [p for p in rows if p["status"] == STATUS_PENDING]
    if pending:
        out.append(f"**还有 {len(pending)} 条在等审批** —— 别重复提同一件事，"
                   "也别提与它冲突的改动。")
    rejected = [p for p in rows if p["status"] == STATUS_REJECTED]
    if rejected:
        out.append("被拒过的方向不要原样再提；要提就针对用户给的理由做调整。")
    return "\n".join(out)


def _projection_text(proj: dict | None, now: float, zh) -> str:
    if not proj:
        return ""
    src = proj["source"]
    where = (f"当前队列 {src['queue_name']}" if src["kind"] == "live_queue"
             else f"参考计划 {src['plan_id']}")
    out = [f"基于 {where}（seq {proj['based_on_seq']}）"]
    horizon = now + PROJECTION_LOOKAHEAD
    near = [e for e in proj["events"] if e["t"] <= horizon]
    stalls = [e for e in near if e["kind"] == "stalled"]
    done = [e for e in near if e["kind"] == "completed"]
    if stalls:
        out.append("（30s 预估已移除：看曲线用 simulate_plan）未来 30s 内会卡：" + "；".join(
            f"{_mmss(e['t'])} {zh(e['stable_id'])} {e['reason'] or ''}" for e in stalls))
    if done:
        out.append("未来 30s 内完成：" + "，".join(
            f"{zh(e['stable_id'])}@{_mmss(e['t'])}" for e in done))
    pt = next((p for p in proj["points"] if p["t"] >= horizon), None)
    if pt:
        out.append(f"30s 后预计：矿 {pt['minerals']:.0f} / 气 {pt['gas']:.0f}"
                   f" / 供给 {pt['supply_used']}·{pt['supply_cap']}")
    if proj.get("skipped"):
        out.append("注意：有 " + str(len(proj["skipped"]))
                   + " 项没进投影（" + "；".join(
                       f"{s['op']}:{s['reason']}" for s in proj["skipped"]) + "）"
                   + " —— 曲线比真实队列少算了这部分")
    return "\n".join(out)


# ---------------- 工具 ----------------

def _env(frames: dict[str, dict], topic: str) -> dict:
    return frames.get(topic) or {}


def _payload(frames: dict[str, dict], topic: str) -> dict | None:
    env = frames.get(topic)
    return env.get("payload") if env else None


def _zh_of(catalog: Catalog):
    def zh(stable_id: str | None) -> str:
        if not stable_id:
            return "—"
        e = catalog.by_stable_id(stable_id)
        return e.display_name_zh if e else stable_id
    return zh


def _mmss(t: float) -> str:
    s = max(0, int(round(t)))
    return f"{s // 60:02d}:{s % 60:02d}"


def frames_by_topic(envelopes: list[dict]) -> dict[str, dict]:
    """`latest_at()` 返回的信封列表 → `{topic: 信封}`（后到的覆盖先到的）。"""
    return {e["topic"]: e for e in envelopes}