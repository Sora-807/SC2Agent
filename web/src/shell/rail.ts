/**
 * 图标栏的纯逻辑（F13c）—— 分组结构 + 角标计数，可测（rail.test.ts）。
 *
 * 角标纪律：计数**只读帧字段**（proposals/alerts/production），不做本地推断 ——
 * 帧里没有的就不亮（红线 C7 的 UI 侧表述）。
 */
import type { AlertsFrame, ProductionFrame, ProposalsFrame } from "../contract";
import type { Mode } from "./mode";
import { PAGES, PAGE_GROUP_LABEL, type PageGroup, type PageKey } from "./route";

/** R5：live 中不创建/编辑模块与 Strategy —— 门控理由必须带编号（G7） */
export const PLAN_GATE_REASON = "R5：live 不能创建/编辑模块与 Strategy";

export interface RailGroup {
  key: PageGroup;
  label: string;
  items: (typeof PAGES)[number][];
  /** 规划组在 drive 模式门控（置灰 + 理由，不隐藏） */
  gated: boolean;
  gateReason: string | null;
}

export function railGroups(mode: Mode): RailGroup[] {
  const groups: PageGroup[] = ["drive", "plan", "diag"];
  return groups.map((g) => {
    const gated = g === "plan" && mode === "drive";
    return {
      key: g,
      label: PAGE_GROUP_LABEL[g],
      items: PAGES.filter((p) => p.group === g),
      gated,
      gateReason: gated ? PLAN_GATE_REASON : null,
    };
  });
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
