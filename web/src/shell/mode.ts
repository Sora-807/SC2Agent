/**
 * 模式轴（F13a / U19）—— 离线编辑 · 实时驾驶 · 复盘，一级语义的唯一权威定义。
 *
 * 模式是独立于帧源的轴：它决定**合法帧源集合**、authoring 是否启用、chrome 配色。
 * 之前三个正交轴（帧源 × 模态 × 主题页）被压成两条下拉交叉相乘，才有
 * 「分不清在线离线」的结构性问题（PLAN §1.5 根因 U/V）。
 *
 * 纯函数：store 的 setMode 与测试都吃这里，UI 不自算映射。
 */
import type { SourceKind } from "../store/frames";

export type Mode = "offline" | "drive" | "replay";

export interface ModeMeta {
  label: string;
  /** chrome 上的状态文字（离线/驾驶/复盘各自不可忽略的那句话） */
  tip: string;
  /** 顶部色带与状态点的颜色族（tailwind 名，SessionBar 引用） */
  band: string;
  dot: string;
  text: string;
}

export const MODE_META: Record<Mode, ModeMeta> = {
  offline: {
    label: "离线编辑",
    tip: "离线草稿，不影响任何对局",
    band: "bg-sky-700",
    dot: "bg-sky-400",
    text: "text-sky-300",
  },
  drive: {
    label: "实时驾驶",
    tip: "跟随实时",
    band: "bg-emerald-600",
    dot: "bg-emerald-400 animate-pulse",
    text: "text-emerald-300",
  },
  replay: {
    label: "复盘",
    tip: "只读回看",
    band: "bg-amber-600",
    dot: "bg-amber-400",
    text: "text-amber-300",
  },
};

/** 每个模式的合法帧源（R5：live 中不创建/编辑模块与 Strategy → 驾驶态只有 live 源） */
export const MODE_SOURCES: Record<Mode, SourceKind[]> = {
  offline: ["fixture"],
  replay: ["fixture", "mock-live", "api"],
  drive: ["live"],
};

/** 模式下可用的帧源；api 源在后端不在时剔除（置灰不如不给，G7 的理由另给在按钮上） */
export function allowedSources(mode: Mode, apiOk: boolean): SourceKind[] {
  return MODE_SOURCES[mode].filter((k) => (k === "api" || k === "live") ? apiOk : true);
}

/** 切模式时的默认落点（保持**仍然存在**的当前夹具优先，否则第一个夹具；live 源不需要夹具键）。
 * 「仍然存在」：drive 会话会把 fixtureKey 写成哨兵 "live" —— 切回离线时不能拿它当夹具名。 */
export function defaultSource(
  mode: Mode,
  fixtures: readonly { key: string }[],
  apiOk: boolean,
  currentFixtureKey: string | null,
): { kind: SourceKind; fixtureKey: string } | null {
  const first = fixtures[0]?.key ?? null;
  const fx = currentFixtureKey && fixtures.some((f) => f.key === currentFixtureKey)
    ? currentFixtureKey
    : first;
  switch (mode) {
    case "offline":
      return fx ? { kind: "fixture", fixtureKey: fx } : null;
    case "replay": {
      const allowed = allowedSources(mode, apiOk);
      // 有夹具优先夹具（最常用）；没有夹具再退 api 回放；都没有 → null
      if (fx) return { kind: "fixture", fixtureKey: fx };
      if (allowed.includes("api")) return { kind: "api", fixtureKey: "" };
      return null;
    }
    case "drive":
      return apiOk ? { kind: "live", fixtureKey: "live" } : null;
  }
}
