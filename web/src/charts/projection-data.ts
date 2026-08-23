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

export type EventKind = ProjectionFrame["events"][number]["kind"] | "assign";

export interface EventMarker {
  t: number;
  kind: EventKind;
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

/** 瞬时目标值动作（维持采气/采矿/空闲）—— planner 用 assign_<task> 当标签，
 *  不是目录项：不进泳道（没有工期），在曲线上画成黄色虚线（用户拍板 2026-08-22）。 */
export function isAssignId(stableId: string | null): boolean {
  return stableId != null && stableId.startsWith("assign_");
}

const ASSIGN_ZH: Record<string, string> = {
  assign_gas: "维持采气", assign_mineral: "维持采矿", assign_idle: "维持空闲",
};

export function toMarkers(frame: ProjectionFrame): EventMarker[] {
  return frame.events.map((e) => {
    if (isAssignId(e.stable_id)) {
      return {
        t: e.t, kind: "assign" as const,
        text: ASSIGN_ZH[e.stable_id ?? ""] ?? e.stable_id ?? "assign",
      };
    }
    return {
      t: e.t,
      kind: e.kind,
      text: [KIND_ZH[e.kind], e.stable_id ?? "", e.reason ?? ""].filter((s) => s !== "").join(" "),
    };
  });
}

/** mm:ss（游戏时间，不是墙钟） */
export function fmtMMSS(t: number): string {
  const s = Math.max(0, Math.round(t));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}
