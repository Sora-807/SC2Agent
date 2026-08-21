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

/**
 * 写入面门禁（U7 / R8）—— 命令必须打到**有会话的当前世界**上。
 *
 * 曾经这条判断写在 ProductionPage 里且是 `sourceKind === "api"`：
 * 而 `api` 是**回放源**（历史 JSONL，背后没有会话），真正的会话源是 `live`。
 * 后果是「启动沙盒 → 下命令」这条主链路在 UI 上整条走不通（写入控件全部消失），
 * 横幅还写着"当前帧源是本地夹具"——明明是 live 会话。反过来在回放源上却放开了写，
 * 命令会打到一个你并没有在看的世界上。两个方向都错，所以门禁必须收在这里、并可测。
 */
export interface WriteGate {
  writable: boolean;
  /** 不可写时必须给出**真话**（红线 G7：禁用带理由） */
  reason: string | null;
}

export function writeGate(sourceKind: SourceKind, timeline: "live" | "review"): WriteGate {
  if (sourceKind !== "live") {
    return {
      writable: false,
      reason:
        sourceKind === "api"
          ? "只读：当前是后端回放源（历史 JSONL），它背后没有会话 —— 在这里下命令会打到一个你没在看的世界上。切到「实时驾驶」并启动会话。"
          : "只读：当前是离线帧源，没有会话可以接受命令。切到「实时驾驶」并启动会话。",
    };
  }
  if (timeline === "review") {
    return {
      writable: false,
      reason: "只读回看中：拖回历史时不能下命令 —— 那一刻你看的不是现在的世界（R8）。点「回到实时」恢复。",
    };
  }
  return { writable: true, reason: null };
}

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
