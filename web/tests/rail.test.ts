/**
 * F13c：图标栏纯逻辑 —— 分组结构、模式门控、角标计数。
 *
 * 角标纪律：计数只读帧字段（proposals/alerts/production），不做本地推断 ——
 * 帧里没有的就不亮（红线 C7 的 UI 侧表述）。
 */
import { describe, expect, it } from "vitest";
import {
  PLAN_GATE_REASON, badgeCounters, badgeFor, homePageOf, railGroups,
} from "../src/shell/rail";

describe("railGroups 分组结构（P1：导航是模式的函数）", () => {
  it("离线规划模式：只有规划工作台 + 诊断（驾驶页不出现）", () => {
    const groups = railGroups("offline");
    expect(groups.map((g) => g.label)).toEqual(["规划", "诊断"]);
    expect(groups[0]!.items.map((p) => p.key))
      .toEqual(["plan-map", "plan-production", "plan-flow"]);
    // 2026-08-25：诊断组加「评测」（eval 管线前端面）
    expect(groups[1]!.items.map((p) => p.key)).toEqual(["eval", "debug"]);
  });

  it("实时/复盘：只有驾驶四页 + 诊断（规划入口不出现，R5）", () => {
    for (const m of ["drive", "replay"] as const) {
      const groups = railGroups(m);
      expect(groups.map((g) => g.label), m).toEqual(["驾驶", "诊断"]);
      expect(groups[0]!.items.map((p) => p.key))
        .toEqual(["overview", "map", "production", "flow"]);
      expect(groups.some((g) => g.key === "plan"), m).toBe(false);
    }
  });

  it("组不再门控：模式一级入口（顶栏三段）保证可发现性，G7 的理由由 PlanningPage 守卫兜底", () => {
    for (const m of ["offline", "drive", "replay"] as const) {
      for (const g of railGroups(m)) {
        expect(g.gated, m).toBe(false);
        expect(g.gateReason).toBeNull();
      }
    }
    expect(PLAN_GATE_REASON).toContain("R5");
  });

  it("homePageOf：离线 → 生产规划（主场景）；实时/复盘 → 概览", () => {
    expect(homePageOf("offline")).toBe("plan-production");
    expect(homePageOf("drive")).toBe("overview");
    expect(homePageOf("replay")).toBe("overview");
  });
});

describe("badgeCounters / badgeFor（角标只读帧字段）", () => {
  const proposals = {
    proposals: [
      { id: "1", status: "待审批" },
      { id: "2", status: "已接受" },
      { id: "3", status: "待审批" },
    ],
  } as never;
  const alerts = {
    alerts: [
      { id: "a", severity: "info" },
      { id: "b", severity: "warn" },
      { id: "c", severity: "error" },
    ],
  } as never;
  const production = {
    queues: [
      { name: "main", head_status: "可执行" },
      { name: "gas", head_status: "阻塞" },
    ],
  } as never;

  it("三个计数都来自帧字段", () => {
    expect(badgeCounters(proposals, alerts, production)).toEqual({
      pendingProposals: 2,
      alerts: 2,
      headBlocked: true,
    });
  });

  it("帧为 null 时不亮角标（帧里没有 = 画不出来，C7）", () => {
    expect(badgeCounters(null, null, null)).toEqual({
      pendingProposals: 0,
      alerts: 0,
      headBlocked: false,
    });
  });

  it("角标落到对的入口：待审批→规划入口、阻塞→生产、警报→概览", () => {
    const b = badgeCounters(proposals, alerts, production);
    expect(badgeFor("plan-production", b)).toEqual({ text: "2", tone: "warn" });
    expect(badgeFor("plan-map", b)).toEqual({ text: "2", tone: "warn" });
    expect(badgeFor("production", b)).toEqual({ text: "阻塞", tone: "error" });
    expect(badgeFor("overview", b)).toEqual({ text: "2", tone: "error" });
    expect(badgeFor("map", b)).toBeNull();
    expect(badgeFor("debug", b)).toBeNull();
  });

  it("计数归零时不亮（不出无意义的 0）", () => {
    const b = badgeCounters(null, null, null);
    expect(badgeFor("plan-production", b)).toBeNull();
    expect(badgeFor("production", b)).toBeNull();
    expect(badgeFor("overview", b)).toBeNull();
  });
});
