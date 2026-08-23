"""view.recap：对局录像 → 人/Agent 可读的衍生摘要（I20 文件契约闭环）。

为什么要有这一层：录像 jsonl 是几 MB 的原始帧流，人翻不动、agent read 一次就爆
token —— **存了等于没存**。落盘原则补一条：原始数据 + 给人/agent 读的衍生视图
一起保存（`rec-<id>.md` 与 jsonl 同目录）。渲染只读已有帧（信封 dict），不猜、
不补 —— 帧里没有的东西摘要里也没有。

分类不依赖 catalog：`footprint` 在 = 建筑（UnitView 已按 catalog 标注），工人按
stable_id 后缀识别 —— recap 是读模型上的**汇总**，不该再拖一份目录依赖。
"""
from __future__ import annotations

from collections.abc import Iterable

#: 工人后缀（不算部队）：三族 + 人族的空投采矿单位
_WORKER_IDS = ("scv", "mule", "probe", "drone")

#: 时间线里同 kind+文本 的连续重复警报只留第一条（压制刷屏）
_ALERT_DEDUPE_WINDOW = 30.0


def render_recording_summary(frames: Iterable[dict]) -> str:
    """渲染一份录像的 markdown 摘要（`recordings/<rid>.md` 的内容）。"""
    rows = list(frames)
    sessions = [f for f in rows if f.get("topic") == "frame/session"]
    worlds = [f for f in rows if f.get("topic") == "frame/world"]
    flows = [f for f in rows if f.get("topic") == "frame/flow"]
    alerts = [f for f in rows if f.get("topic") == "frame/alerts"]

    out: list[str] = []
    head = _head_line(rows, sessions, worlds)
    out.append(f"# 对局摘要\n")
    out.append(f"- {head}")
    exit_line = _exit_line(flows, sessions)
    if exit_line:
        out.append(f"- {exit_line}")

    timeline = _timeline(worlds, flows, alerts)
    if timeline:
        out.append("\n## 时间线\n")
        out += [f"- {line}" for line in timeline]

    out.append("\n## 终局盘点\n")
    out += _final_state(worlds)

    gone = _lost_buildings(worlds)
    if gone:
        out.append("\n## 消失的建筑（被摧毁/变形/飞走）\n")
        out += [f"- {line}" for line in gone]
    return "\n".join(out).strip() + "\n"


def render_recordings_index(metas: list[dict]) -> str:
    """渲染 `recordings/index.md`（清单页）：meta 列表 → 一行一局。

    metas 来自各 `rec-*.meta.json`（或 `/api/recordings` 的清单端点，同形）。
    """
    out = ["# 对局记录索引", "",
           "每局一份 `rec-<id>.md` 摘要（时间线 + 终局盘点）；原始帧流 .jsonl 刻意不挂载。",
           ""]
    if not metas:
        out.append("（还没有录像 —— 开一局 live 会话自动录制。）")
        return "\n".join(out) + "\n"
    out.append("| 记录 | 对局 | 时长 | 帧数 | 状态 |")
    out.append("|---|---|---|---|---|")
    for m in metas:
        rid = m.get("id") or "rec-?"
        races = f"{m.get('my_race_zh') or '?'} vs {m.get('enemy_race_zh') or '?'}"
        dur = m.get("to") or m.get("to_time") or 0.0
        out.append(
            f"| [{rid}]({rid}.md) | {races} · {m.get('map') or m.get('label') or ''} "
            f"| {float(dur):.0f}s | {m.get('envelopes') or '?'} | {m.get('state') or '?'} |")
    return "\n".join(out) + "\n"


# ---- 段落 ----

def _head_line(rows: list[dict], sessions: list[dict], worlds: list[dict]) -> str:
    sess = sessions[-1]["payload"] if sessions else {}
    if not worlds:
        return "（无 world 帧 —— 会话没有产生画面数据）"
    first, last = worlds[0], worlds[-1]
    eco0 = first["payload"].get("economy") or {}
    workers = sum(1 for u in first["payload"].get("units") or []
                  if any(u.get("stable_id", "").endswith(w) for w in _WORKER_IDS))
    races = f"{sess.get('my_race_zh') or sess.get('my_race') or '?'} vs " \
            f"{sess.get('enemy_race_zh') or sess.get('enemy_race') or '?'}"
    return (f"对局：{races} · {sess.get('map_name') or '?'} · 时长 "
            f"{float(last['game_time']):.0f} 秒 · {len(rows)} 帧 · "
            f"开局 {workers} 工人 · 人口 {eco0.get('supply_used')}/{eco0.get('supply_cap')}")


def _exit_line(flows: list[dict], sessions: list[dict]) -> str:
    if flows:
        s = (flows[-1]["payload"].get("strategies") or [{}])[0]
        rec = s.get("exit_record")
        if rec:
            return f"终局：{rec.get('kind')}/{rec.get('reason')}（策略 {s.get('strategy_ref')}）"
        if s.get("done"):
            return f"终局：策略 {s.get('strategy_ref')} 已结束"
    if sessions:
        return f"会话终态：{sessions[-1]['payload'].get('state')}"
    return ""


def _timeline(worlds: list[dict], flows: list[dict], alerts: list[dict]) -> list[str]:
    events: list[tuple[float, str]] = []

    # 建筑：开建 / 落成（tag 维度跟踪，同型建筑各自一条）
    seen_ready: set[int] = set()
    for f in worlds:
        t = float(f["game_time"])
        for u in f["payload"].get("units") or []:
            if u.get("owner") != "self" or u.get("footprint") is None:
                continue
            tag = u.get("tag")
            prog = float(u.get("build_progress") or 0.0)
            if tag in seen_ready:
                continue
            if prog >= 1.0:
                seen_ready.add(tag)
                events.append((t, f"落成 {u.get('stable_id')}"))

    # 供给上限变化（落成事件的人口读数，二矿/补给节奏一眼可见）
    cap = None
    for f in worlds:
        eco = (f["payload"].get("economy") or {})
        cur = eco.get("supply_cap")
        if cur is not None and cap is not None and cur > cap:
            events.append((float(f["game_time"]), f"人口上限 {cap}→{cur}"))
        if cur is not None:
            cap = cur

    # 警报（连续同文本压成一条）
    last_alert = ("", -1e9)
    for f in alerts:
        t = float(f["game_time"])
        for a in f["payload"].get("alerts") or []:
            key = f"{a.get('kind')}:{a.get('text_zh')}"
            if key == last_alert[0] and t - last_alert[1] < _ALERT_DEDUPE_WINDOW:
                continue
            last_alert = (key, t)
            events.append((t, f"[{a.get('severity')}] {a.get('text_zh')}"))

    # 策略转移（最后一份 flow 帧里带完整历史）
    if flows:
        s = (flows[-1]["payload"].get("strategies") or [{}])[0]
        for tr in s.get("transitions") or []:
            events.append((float(tr.get("at") or 0.0),
                           f"策略 {tr.get('from_step')}→{tr.get('to')}"
                           f"（{tr.get('kind')}/{tr.get('reason')}）"))

    events.sort(key=lambda e: e[0])
    return [f"t={t:.0f}s {text}" for t, text in events]


def _final_state(worlds: list[dict]) -> list[str]:
    if not worlds:
        return ["（无数据）"]
    last = worlds[-1]["payload"]
    eco = last.get("economy") or {}
    buildings: dict[str, int] = {}
    army: dict[str, int] = {}
    enemy: dict[str, int] = {}
    for u in last.get("units") or []:
        sid = u.get("stable_id") or "?"
        if u.get("owner") == "self":
            if u.get("footprint") is not None:
                buildings[sid] = buildings.get(sid, 0) + 1
            elif not any(sid.endswith(w) for w in _WORKER_IDS):
                army[sid] = army.get(sid, 0) + 1
        elif u.get("owner") == "enemy" and not sid.startswith(("neutral/", "unknown/")):
            enemy[sid] = enemy.get(sid, 0) + 1
    out = [f"- 资源：矿 {eco.get('minerals')} · 气 {eco.get('vespene')} · "
           f"人口 {eco.get('supply_used')}/{eco.get('supply_cap')}"]
    out.append("- 建筑现存：" + ("、".join(f"{k} ×{v}" for k, v in sorted(buildings.items())) or "无"))
    out.append("- 我方部队：" + ("、".join(f"{k} ×{v}" for k, v in sorted(army.items())) or "无"))
    out.append("- 敌方（当前所见）：" + ("、".join(f"{k} ×{v}" for k, v in sorted(enemy.items())) or "无"))
    return out


def _lost_buildings(worlds: list[dict]) -> list[str]:
    """曾落成、终局不在场的建筑（tag 维度）：被摧毁/变形/飞走的痕迹。"""
    last_seen: dict[int, tuple[float, str]] = {}
    ready_seen: set[int] = set()
    for f in worlds:
        t = float(f["game_time"])
        for u in f["payload"].get("units") or []:
            if u.get("owner") != "self" or u.get("footprint") is None:
                continue
            tag = u.get("tag")
            last_seen[tag] = (t, u.get("stable_id") or "?")
            if float(u.get("build_progress") or 0.0) >= 1.0:
                ready_seen.add(tag)
    if not worlds:
        return []
    end_t = float(worlds[-1]["game_time"])
    out = []
    for tag, (t, sid) in sorted(last_seen.items(), key=lambda kv: kv[1][0]):
        if tag in ready_seen and end_t - t > 5.0:
            out.append(f"{sid} 在 t={t:.0f}s 后未再出现")
    return out
