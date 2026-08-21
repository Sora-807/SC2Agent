/**
 * 投影泳道数据（I5/F15/F17）—— ProjectionFrame → bar 打包 / 卡点 / 共享定义域（纯函数，可单测）。
 *
 * F17 泳道模型（用户三轮反馈）：不再「每个 stable_id 一条道」—— 名字放进 bar 里
 * （圆角矩形自带 zh 名），行变成**全局打包行**（跨类型贪心装行，尽可能并行不重叠），
 * 固定可见行数、超出滚动。
 *
 * 红线 C7：这里**只做重排**，不算任何新数值 —— from/to/done 全部来自帧字段；
 * started→completed 的配对沿用「同类型最早未闭合 started」语义（数值排序，不是字典序）。
 */
import type { ProjectionFrame } from "../contract";

export interface StallMark {
  t: number;
  stableId: string | null;
  reason: string | null;
}

/** 共享时间轴定义域（I5）：曲线与泳道必须吃同一个值，像素才能对齐 */
export interface TimeDomain {
  from: number;
  to: number;
}

/** 一条配对好的 bar（started→completed；未闭合的延到视野末端） */
export interface PairedBar {
  stableId: string | null;
  from: number;
  to: number;
  done: boolean;
}

export interface PackedBar {
  id: string;
  stableId: string | null;
  from: number;
  to: number;
  done: boolean;
  /** 全局行号（跨类型打包；0 起） */
  row: number;
}

export interface PackedRows {
  bars: PackedBar[];
  /** 总行数（= max(row)+1；无 bar 时 0） */
  rows: number;
}

function pairEvents(frame: ProjectionFrame): PairedBar[] {
  const t0 = frame.based_on_game_time;
  const end = t0 + Math.max(1, frame.horizon);
  const open = new Map<string, number>();
  const pairs: PairedBar[] = [];
  for (const e of frame.events) {
    if (e.kind === "stalled") continue;
    const key = e.stable_id ?? "?";
    if (e.kind === "started") {
      open.set(key + ":" + e.t, e.t);
      continue;
    }
    // completed：闭掉**最早的**未闭合 started（数值比较，不是字符串——"id:10" < "id:9" 是字典序坑）；
    // 没有可闭的就画零长 bar（如实显示，不丢事件）
    const entry = [...open.entries()]
      .filter(([k]) => k.startsWith(key + ":"))
      .sort((a, b) => a[1] - b[1])[0];
    const from = entry ? entry[1] : e.t;
    if (entry) open.delete(entry[0]);
    pairs.push({ stableId: e.stable_id, from, to: e.t, done: true });
  }
  for (const [k, from] of open) {
    const key = k.split(":")[0] ?? "?";
    pairs.push({ stableId: key === "?" ? null : key, from, to: end, done: false });
  }
  return pairs;
}

/**
 * t 时刻在产明细（F15 点击检查）：显示层从 events 配对出来（与 packBars 同层、同一套配对），
 * 不是帧里算好的字段 —— 只给检查面板用，不入任何决策路径（G5）。
 * 零长 bar（无 started 的孤立 completed）是瞬时事件，不算在产。
 */
export function activeAt(frame: ProjectionFrame, t: number): PairedBar[] {
  return pairEvents(frame).filter((b) => b.from <= t && t < b.to);
}

/**
 * F17 全局行打包：所有 bar（不分类型）按 from 排序后贪心装行 ——
 * 与某行行尾不重叠就放该行，都重叠就开新行。行数最少、并行最大化。
 */
export function packBars(frame: ProjectionFrame): PackedRows {
  const pairs = pairEvents(frame).sort((a, b) => a.from - b.from);
  const lastTo: number[] = [];
  const bars: PackedBar[] = pairs.map((p, seq) => {
    let row = lastTo.findIndex((t) => t <= p.from);
    if (row < 0) {
      row = lastTo.length;
      lastTo.push(p.to);
    } else {
      lastTo[row] = p.to;
    }
    return {
      // id 带配对序号兜底：同一 (类型，区间) 理论上不重复，重复也不许塌 key
      id: `${p.stableId ?? "?"}:${p.from}-${p.to}-${p.done ? "d" : "o"}#${seq}`,
      stableId: p.stableId, from: p.from, to: p.to, done: p.done, row,
    };
  });
  return { bars, rows: lastTo.length };
}

export function toStalls(frame: ProjectionFrame): StallMark[] {
  return frame.events
    .filter((e) => e.kind === "stalled")
    .map((e) => ({ t: e.t, stableId: e.stable_id, reason: e.reason }));
}

/**
 * 共享定义域：曲线的点范围与「现在 → 现在+horizon」（泳道未闭合 bar 的末端）都罩住。
 * horizon 为 0/负时至少给 1 秒，避免除零。
 */
export function sharedDomain(frame: ProjectionFrame): TimeDomain {
  const t0 = frame.based_on_game_time;
  const end = t0 + Math.max(1, frame.horizon);
  const first = frame.points[0]?.t;
  const last = frame.points.at(-1)?.t;
  return {
    from: Math.min(t0, first ?? t0),
    to: Math.max(end, last ?? end),
  };
}

/* ---------------- 时间轴缩放（F15：滚轮调宽度、中心跟顶层时间轴） ---------------- */

/** 视窗宽度上下限（秒）：再窄读不出一个建造周期，再宽全是空 */
export const ZOOM_SPAN_MIN = 20;
export const ZOOM_SPAN_MAX = 600;

export function zoomSpan(span: number, factor: number): number {
  const next = span * factor;
  return Math.min(ZOOM_SPAN_MAX, Math.max(ZOOM_SPAN_MIN, next));
}

/** center 居中、宽 span 的视窗 */
export function centerRange(center: number, span: number): TimeDomain {
  return { from: center - span / 2, to: center + span / 2 };
}

/** 滚轮缩放：保持 anchorT 在屏幕上的 frac 位置不变（围绕光标缩放） */
export function anchorRange(anchorT: number, frac: number, span: number): TimeDomain {
  const from = anchorT - frac * span;
  return { from, to: from + span };
}

/* ---------------- 历史累积（F17：拖时间轴时左侧内容保留） ---------------- */

/**
 * 把一帧投影并进历史（F17）：points 按 t 去重（后到覆盖 —— 后一帧在 t 处的值比旧帧的
 * 远期预测更可信），events 按 (kind,t,stable_id,reason) 去重（同一事件在多个帧里重复出现）。
 *
 * 背景：投影帧只含 [based_on, +horizon]，中心跟随时轴后左半视窗没有数据 ——
 * 操作习惯是「数据向左流走、历史保留」，所以显示层把走过的每秒累积下来
 * （与 ReviewableSource 的环形缓冲同类：显示层累积，不入决策路径）。
 *
 * 返回合并后的帧（points/events 已按上述规则去重、按 t 排序）。
 * `reset` 由调用方在帧源回退（based_on 大幅倒退）时先清空再喂。
 */
export function accumulateInto(
  hist: { points: Map<number, ProjectionFrame["points"][number]>; events: Map<string, ProjectionFrame["events"][number]> },
  frame: ProjectionFrame,
): ProjectionFrame {
  for (const p of frame.points) hist.points.set(Math.round(p.t), p);
  for (const e of frame.events) {
    hist.events.set(`${e.kind}|${e.t}|${e.stable_id}|${e.reason}`, e);
  }
  const lastT = frame.points.at(-1)?.t ?? frame.based_on_game_time + frame.horizon;
  // 只保留当前帧覆盖范围内的事件/点：旧帧的远期预测若超出当前帧末端（向后拖时间轴）则丢弃
  const points = [...hist.points.values()]
    .filter((p) => p.t <= lastT)
    .sort((a, b) => a.t - b.t);
  const events = [...hist.events.values()]
    .filter((e) => e.t <= lastT)
    .sort((a, b) => a.t - b.t);
  return { ...frame, points, events };
}
