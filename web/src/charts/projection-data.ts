/**
 * ProjectionFrame → 图表数据（纯函数，可单测；组件只负责画）
 *
 * 红线 C7：这里**只做重排**，不算任何新数值。矿/气/供给/工人都直接取帧字段。
 */
import type { ProjectionFrame } from "../contract";

export interface ProjectionSeries {
  /** 游戏时间轴（秒，绝对） */
  t: number[];
  minerals: number[];
  gas: number[];
  /** 视野起止（= 现在 → 现在+horizon） */
  from: number;
  to: number;
}

export interface EventMarker {
  t: number;
  kind: ProjectionFrame["events"][number]["kind"];
  /** 悬停/图例用的中文说明（拼接自帧字段，不新增语义） */
  text: string;
}

export function toSeries(frame: ProjectionFrame): ProjectionSeries {
  const t = frame.points.map((p) => p.t);
  return {
    t,
    minerals: frame.points.map((p) => p.minerals),
    gas: frame.points.map((p) => p.gas),
    from: t.at(0) ?? frame.based_on_game_time,
    to: t.at(-1) ?? frame.based_on_game_time,
  };
}

const KIND_ZH: Record<ProjectionFrame["events"][number]["kind"], string> = {
  stalled: "卡",
  started: "开始",
  completed: "完成",
};

export function toMarkers(frame: ProjectionFrame): EventMarker[] {
  return frame.events.map((e) => ({
    t: e.t,
    kind: e.kind,
    text: [KIND_ZH[e.kind], e.stable_id ?? "", e.reason ?? ""].filter((s) => s !== "").join(" "),
  }));
}

/** mm:ss（游戏时间，不是墙钟） */
export function fmtMMSS(t: number): string {
  const s = Math.max(0, Math.round(t));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}
