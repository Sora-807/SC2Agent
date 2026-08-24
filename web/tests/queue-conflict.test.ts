/**
 * 保存冲突二选一（2026-08-25）：保存 = 全量替换 —— agent 并发改写文件后
 * 用户再保存会静默抹掉对方。锁三件事：open 记指纹 / save 对账挂起 /
 * resolveConflict 两个方向各自的语义。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const getPlan = vi.fn();
const savePlan = vi.fn();
const simulatePlan = vi.fn();
const listPlans = vi.fn();
const listMapPlans = vi.fn();

vi.mock("../src/api/plans", () => ({
  getPlan: (...a: unknown[]) => getPlan(...a),
  savePlan: (...a: unknown[]) => savePlan(...a),
  simulatePlan: (...a: unknown[]) => simulatePlan(...a),
  listPlans: (...a: unknown[]) => listPlans(...a),
  createPlan: vi.fn(), createPlanFromModule: vi.fn(), removePlan: vi.fn(),
}));
vi.mock("../src/api/map-plans", () => ({
  listMapPlans: (...a: unknown[]) => listMapPlans(...a),
}));

const plan = (updated_at: number, queue: unknown[] = []) => ({
  id: "p1", title_zh: "t", map: "LadderMap", spawn: "bl", locked: false,
  updated_at, queue,
});

describe("queue-store 保存冲突", () => {
  beforeEach(() => {
    vi.resetModules();
    getPlan.mockReset(); savePlan.mockReset(); simulatePlan.mockReset();
    listPlans.mockReset().mockResolvedValue([]); listMapPlans.mockReset().mockResolvedValue([]);
    simulatePlan.mockResolvedValue({ points: [], events: [], alerts: [], skipped: [] });
  });

  it("open 记指纹；文件未变 → 直接保存不挂冲突", async () => {
    getPlan.mockResolvedValue(plan(100));
    const { useQueueStore } = await import("../src/planning/queue-store");
    await useQueueStore.getState().open("p1");
    expect(useQueueStore.getState().baseUpdatedAt).toBe(100);
    getPlan.mockResolvedValue(plan(100));            // 对账：没变
    savePlan.mockResolvedValue(plan(200));
    await useQueueStore.getState().save();
    expect(savePlan).toHaveBeenCalled();
    expect(useQueueStore.getState().conflict).toBeNull();
  });

  it("编辑期间文件被改（updated_at 变）→ 保存挂起冲突、不写文件", async () => {
    getPlan.mockResolvedValue(plan(100));
    const { useQueueStore } = await import("../src/planning/queue-store");
    await useQueueStore.getState().open("p1");
    getPlan.mockResolvedValue(plan(555));            // agent 改过（时间戳甚至更旧也行：变=变）
    await useQueueStore.getState().save();
    expect(savePlan).not.toHaveBeenCalled();
    expect(useQueueStore.getState().conflict).toEqual({ theirUpdatedAt: 555 });
  });

  it("二选一：用我的覆盖 → 强存；采用对方的 → 重开丢草稿", async () => {
    getPlan.mockResolvedValue(plan(100));
    const { useQueueStore } = await import("../src/planning/queue-store");
    await useQueueStore.getState().open("p1");
    getPlan.mockResolvedValue(plan(555));
    await useQueueStore.getState().save();
    // 用我的覆盖：指纹对齐对方 → 对账通过 → 真存
    getPlan.mockResolvedValue(plan(555));
    savePlan.mockResolvedValue(plan(600));
    await useQueueStore.getState().resolveConflict(true);
    expect(savePlan).toHaveBeenCalled();
    expect(useQueueStore.getState().conflict).toBeNull();
    // 重来一局：采用对方的 → open 重开（草稿丢弃）
    getPlan.mockResolvedValue(plan(100, [{ op: "train", type: "terran/scv", count: 1 }]));
    await useQueueStore.getState().open("p1");
    getPlan.mockResolvedValue(plan(555));
    await useQueueStore.getState().save();
    const theirs = plan(777, [{ op: "train", type: "terran/marine", count: 2 }]);
    getPlan.mockResolvedValue(theirs);
    await useQueueStore.getState().resolveConflict(false);
    expect(savePlan).toHaveBeenCalledTimes(1);       // 只有覆盖那次真存过
    expect(useQueueStore.getState().items.length).toBe(1);
    expect(useQueueStore.getState().items[0]?.type).toBe("terran/marine");
  });
});
