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
import { isAssignId } from "./projection-data";

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

/** 帧事件 → 配对 bar（started→completed；未闭合的延到视野末端）。
 *  导出供 ProjectionBoard 做「在建部分条」合并（复盘改版，同一套配对语义）。 */
export function pairEvents(frame: ProjectionFrame): PairedBar[] {  const t0 = frame.based_on_game_time;
  const end = t0 + Math.max(1, frame.horizon);
  const open = new Map<string, number>();
  const pairs: PairedBar[] = [];
  for (const e of frame.events) {
    if (e.kind === "stalled") continue;
    // assign 是瞬时目标值动作（无工期）：不占泳道 —— 曲线上画黄虚线（projection-data）
    if (isAssignId(e.stable_id)) continue;
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
export function packPairs(pairs: PairedBar[]): PackedRows {
  const sorted = pairs.slice().sort((a, b) => a.from - b.from);
  const lastTo: number[] = [];
  const bars: PackedBar[] = sorted.map((p, seq) => {
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

export function packBars(frame: ProjectionFrame): PackedRows {
  return packPairs(pairEvents(frame));
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

/** 视窗宽度上下限（秒）：再窄读不出一个建造周期，再宽全是空。
 *  二十七轮用户拍板：默认窗口 5 分钟（10 分钟拖得还是快 + 事件挤）。 */
export const ZOOM_SPAN_MIN = 20;
export const ZOOM_SPAN_MAX = 300;

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

/* ---------------- 截断锚定（复盘改版，2026-08-24 拍板：F17 历史累积退役） ---------------- */

/** 截断线左侧留白（秒）：只够跨线的在产/在建部分条把名字显出来（用户拍板 30s） */
export const LEFT_MARGIN_SECS = 30;

/**
 * 截断锚定视窗：红截断线钉 T（当前帧 based_on 游戏时间），左侧只留
 * LEFT_MARGIN_SECS、不许再往左拖；右侧钳数据末端（不出空白）。
 * span 由缩放决定（ZOOM_SPAN_MIN..MAX）。
 */
export function nowAnchoredRange(t: number, span: number, dataEnd: number): TimeDomain {
  const from = Math.max(0, t - LEFT_MARGIN_SECS);
  const to = Math.max(t + 1, Math.min(from + Math.max(1, span), Math.max(1, dataEnd)));
  return { from, to: Math.max(from + 1, to) };
}
