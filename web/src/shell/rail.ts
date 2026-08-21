/**
 * 图标栏的纯逻辑（F13c）—— 分组结构 + 角标计数，可测（rail.test.ts）。
 *
 * 角标纪律：计数**只读帧字段**（proposals/alerts/production），不做本地推断 ——
 * 帧里没有的就不亮（红线 C7 的 UI 侧表述）。
 */
import type { AlertsFrame, ProductionFrame, ProposalsFrame } from "../contract";
import type { Mode } from "./mode";
import { PAGES, PAGE_GROUP_LABEL, type PageGroup, type PageKey } from "./route";

/** R5：live 中不创建/编辑模块与 Strategy —— 门控理由必须带编号（G7）。
 * P1 后规划组不再出现在驾驶模式导航里（见 railGroups），这条理由由
 * PlanningPage 的守卫兜底（hash 直达规划页时仍显示，不静默）。 */
export const PLAN_GATE_REASON = "R5：live 不能创建/编辑模块与 Strategy";

export interface RailGroup {
  key: PageGroup;
  label: string;
  items: (typeof PAGES)[number][];
  /** P1 后导航按模式重组，组级门控不再出现（保留字段兼容角标逻辑） */
  gated: boolean;
  gateReason: string | null;
}

/**
 * 模式 → 导航组的映射（P1，用户四轮反馈拍板：「离线编辑不应有驾驶栏、
 * 复盘不应有规划栏」——导航结构就是协作流程的形状）：
 * - 离线规划：只有规划工作台（驾驶页看"正在发生的世界"，离线没有；静态面仍从夹具来）；
 * - 实时/复盘：只有驾驶四页（authoring 入口不属于对局现场，R5）；
 * - 诊断常驻。
 * 可发现性由顶栏一级模式保证（G7 的精神），不是静默藏功能。
 */
export function railGroups(mode: Mode): RailGroup[] {
  const keys: PageGroup[] = mode === "offline" ? ["plan", "diag"] : ["drive", "diag"];
  return keys.map((g) => ({
    key: g,
    label: PAGE_GROUP_LABEL[g],
    items: PAGES.filter((p) => p.group === g),
    gated: false,
    gateReason: null,
  }));
}

/** 模式的默认落点页（模式切换后当前页不在导航里时跳到这里） */
export function homePageOf(mode: Mode): PageKey {
  return mode === "offline" ? "plan-production" : "overview";
}

export interface Badges {
  /** 待审批提案数（挂在规划组/生产入口上） */
  pendingProposals: number;
  /** 非提示级警报数（挂在驾驶组整体） */
  alerts: number;
  /** 是否有队首阻塞（挂在生产入口上） */
  headBlocked: boolean;
}

export function badgeCounters(
  proposals: ProposalsFrame | null,
  alerts: AlertsFrame | null,
  production: ProductionFrame | null,
): Badges {
  return {
    pendingProposals:
      proposals?.proposals.filter((p) => p.status === "待审批").length ?? 0,
    alerts: alerts?.alerts.filter((a) => a.severity !== "info").length ?? 0,
    headBlocked:
      production?.queues.some((q) => q.head_status === "阻塞") ?? false,
  };
}

/** 某个入口该亮哪个角标（数量 0/不适用 → null） */
export function badgeFor(
  key: PageKey,
  b: Badges,
): { text: string; tone: "warn" | "error" } | null {
  switch (key) {
    case "plan-production":
    case "plan-map":
    case "plan-flow":
      return b.pendingProposals > 0
        ? { text: String(b.pendingProposals), tone: "warn" }
        : null;
    case "production":
      return b.headBlocked
        ? { text: "阻塞", tone: "error" }
        : null;
    case "overview":
      return b.alerts > 0
        ? { text: String(b.alerts), tone: "error" }
        : null;
    default:
      return null;
  }
}
