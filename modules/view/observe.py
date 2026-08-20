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

#: 投影摘要只看未来这么久（ADR-0009 §1 的"30 秒投影"量级）。
#: 更远的以后还会重算，写进 prompt 只是噪声。
PROJECTION_LOOKAHEAD = 30.0
#: 最近转移只给这么多条（完整历史进 trace，不进 prompt）
RECENT_TRANSITIONS = 5


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

    seq = int(_env(frames, "frame/world").get("seq", 0)) if "frame/world" in frames else 0
    game_time = float(_env(frames, "frame/world").get("game_time", 0.0)) if "frame/world" in frames else 0.0
    zh = _zh_of(catalog)

    sections = {
        "会话": _session_text(session),
        "经济": _economy_text(world, econ, zh),
        "部队": _groups_text(flow, zh),
        "生产": _production_text(prod, zh),
        "策略": _strategy_text(flow, strategy),
        "风险": _alerts_text(alerts),
        "投影": _projection_text(proj, game_time, zh),
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
    }
    return ObservationPacket(seq=seq, game_time=round(game_time, 3), supersedes=supersedes,
                             sections={k: v for k, v in sections.items() if v}, facts=facts)


# ---------------- 各段 ----------------

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


def _production_text(prod: dict | None, zh) -> str:
    if not prod:
        return ""
    out = []
    for q in prod.get("queues", []):
        head = f"队列 {q['name']}（队首 {q['head_status']}）"
        if q.get("blocked"):
            b = q["blocked"]
            head += f" —— 已阻塞 {b['waited']:.0f}s：{b['reason']}"
        out.append(head)
        for it in q["items"][:8]:
            what = (f"维持 {it['task']}" if it["op"] == "assign_workers" else zh(it["stable_id"]))
            n = f" ×{it['count']}" if it["count"] > 1 else ""
            out.append(f"  {it['index']}. {it['op']} {what}{n} [{it['status']}]")
        if len(q["items"]) > 8:
            out.append(f"  …还有 {len(q['items']) - 8} 项")
    if prod.get("in_flight"):
        out.append("在途：" + "，".join(
            f"{zh(f['stable_id'])}（等待 {f['frames_waited']} 帧"
            + (f"，重试 {f['retries']}" if f["retries"] else "") + "）"
            for f in prod["in_flight"]))
    if prod.get("dropped"):
        out.append("被丢弃：" + "；".join(
            f"{d['op']} {zh(d['stable_id'])} —— {d['reason']}" for d in prod["dropped"][-3:]))
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
        out.append("未来 30s 内会卡：" + "；".join(
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
