/**
 * F13c：图标栏纯逻辑 —— 分组结构、模式门控、角标计数。
 *
 * 角标纪律：计数只读帧字段（proposals/alerts/production），不做本地推断 ——
 * 帧里没有的就不亮（红线 C7 的 UI 侧表述）。
 */
import { describe, expect, it } from "vitest";
import {
  PLAN_GATE_REASON, badgeCounters, badgeFor, railGroups,
} from "../src/shell/rail";

describe("railGroups 分组结构", () => {
  it("驾驶 4 / 规划 3 / 诊断 1，带分组标题", () => {
    const groups = railGroups("offline");
    expect(groups.map((g) => g.label)).toEqual(["驾驶", "规划", "诊断"]);
    expect(groups[0]!.items.map((p) => p.key))
      .toEqual(["overview", "map", "production", "flow"]);
    expect(groups[1]!.items.map((p) => p.key))
      .toEqual(["plan-map", "plan-production", "plan-flow"]);
    expect(groups[2]!.items.map((p) => p.key)).toEqual(["debug"]);
  });

  it("离线/复盘下规划组可用；实时驾驶（live）下门控 + 理由引 R5（G7）", () => {
    for (const m of ["offline", "replay"] as const) {
      const plan = railGroups(m).find((g) => g.key === "plan")!;
      expect(plan.gated, m).toBe(false);
      expect(plan.gateReason).toBeNull();
    }
    const plan = railGroups("drive").find((g) => g.key === "plan")!;
    expect(plan.gated).toBe(true);
    expect(plan.gateReason).toContain("R5");
    expect(PLAN_GATE_REASON).toContain("R5");
    // 驾驶/诊断组永不被 authoring 门控
    expect(railGroups("drive").find((g) => g.key === "drive")!.gated).toBe(false);
    expect(railGroups("drive").find((g) => g.key === "diag")!.gated).toBe(false);
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
