/**
 * 模式轴（F13a / U19）—— 离线编辑 · 实时驾驶 · 复盘，一级语义的唯一权威定义。
 *
 * 模式是独立于帧源的轴：它决定**合法帧源集合**、authoring 是否启用、chrome 配色。
 * 之前三个正交轴（帧源 × 模态 × 主题页）被压成两条下拉交叉相乘，才有
 * 「分不清在线离线」的结构性问题（PLAN §1.5 根因 U/V）。
 *
 * 纯函数：store 的 setMode 与测试都吃这里，UI 不自算映射。
 */
import type { SessionFrame } from "../contract";
import type { SourceKind } from "../store/frames";

export type Mode = "offline" | "drive" | "replay";

export interface ModeMeta {
  label: string;
  /** 控件上的悬停说明（G7：模式语义一句话） */
  tip: string;
}

/** 三段控件的排布顺序（用户 2026-08-22 拍板）：游戏 | 复盘 | 规划。
 * id 不动（offline/drive/replay 全仓引用），动的只是显示名 ——
 * 「离线编辑→规划、实时驾驶→游戏」是用户对三模式的最终命名。 */
export const MODE_ORDER: Mode[] = ["drive", "replay", "offline"];

export const MODE_META: Record<Mode, ModeMeta> = {
  drive: {
    label: "游戏",
    tip: "跟随实时：连接真实 SC2 对局",
  },
  replay: {
    label: "复盘",
    tip: "只读回看：对局记录（自动录制）",
  },
  offline: {
    label: "规划",
    tip: "离线草稿，不影响任何对局",
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

/**
 * 真机首帧等待提示（I6）—— 真机从「启动真机」到首帧 + static/map 要 1-2 分钟，
 * 之前只有一句「等待 static/map…」，被实测误读为「地图黑屏/没同步」（实际帧流正常，只是慢）。
 *
 * 触发条件（纯函数，可测）：实时驾驶 + live 会话源 + static/map 还没到 +
 * 会话还在启动中/对局中（或会话帧还没来）。已结束/崩溃/未连接不提示 ——
 * 那些是真实终态，另有自己的显示。数据到达后 mapArrived 变 true，提示自动消失。
 */
export function bootHint(
  mode: Mode,
  sourceKind: SourceKind,
  sessionState: SessionFrame["state"] | null,
  mapArrived: boolean,
): string | null {
  if (mode !== "drive" || sourceKind !== "live") return null;
  if (mapArrived) return null;
  if (sessionState === "已结束" || sessionState === "崩溃" || sessionState === "未连接") {
    return null;
  }
  return "正在连接 SC2 并等待首帧（真机约需 1-2 分钟）—— 期间没有画面是正常的，数据到达后自动消失";
}

/** 每个模式的合法帧源。
 *  二十七轮用户拍板：复盘入口收敛成**只看对局记录** —— 夹具/模拟 live/API 回放三个
 *  一级选项全退役（分不清是什么）；夹具仍是规划模式的内部数据源（离线要画地图/目录），
 *  mock-live/api 两种 SourceKind 保留在 store（测试与开发通道），UI 不再露出。 */
export const MODE_SOURCES: Record<Mode, SourceKind[]> = {
  offline: ["fixture"],
  replay: ["fixture"],
  drive: ["live"],
};

/**
 * 会话装配的地图规划 id 兜底（2026-08-21 实时驾驶整改）：
 * 旧硬编码默认值与单分支预设（default-bl/tr、layout-bl/tr）已随批 2 双分支退役 ——
 * 发一个不存在的 id 会被后端 400，而那个 400 曾被 UI 吞掉 = 「点启动真机没反应」。
 * 规则：当前值仍在清单里就保持；否则落清单第一个；清单空 → null（不带参数）。
 */
export function pickMapPlan(
  plans: readonly { id: string }[],
  current: string | null,
): string | null {
  if (current && plans.some((p) => p.id === current)) return current;
  return plans[0]?.id ?? null;
}

/** 模式下可用的帧源；api 源在后端不在时剔除（置灰不如不给，G7 的理由另给在按钮上） */
export function allowedSources(mode: Mode, apiOk: boolean): SourceKind[] {
  return MODE_SOURCES[mode].filter((k) => (k === "api" || k === "live") ? apiOk : true);
}

/** 切模式时的默认落点（保持**仍然存在**的当前源优先，否则第一个可用源；live 源不需要夹具键）。
 *  复盘只落**对局记录**（rec: 前缀）；没有记录 → null（页面显示引导文案，不偷偷挂夹具）。 */
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
      const rec = currentFixtureKey?.startsWith("rec:")
        && fixtures.some((f) => f.key === currentFixtureKey)
        ? currentFixtureKey
        : fixtures.find((f) => f.key.startsWith("rec:"))?.key ?? null;
      return rec ? { kind: "fixture", fixtureKey: rec } : null;
    }
    case "drive":
      return apiOk ? { kind: "live", fixtureKey: "live" } : null;
  }
}
