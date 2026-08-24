/**
 * 生产规划工作台状态（2026-08-22 左抽屉轮拆出）—— 与 map-plan-store 同理：
 * 规划文件/草稿/试算放模块级 store，抽屉（文件与试算控制）与主区（队列表格/投影板）
 * 共享，导航与收起不丢状态。深链（?plan=<id>）由 initOnce 幂等消费。
 */
import { create } from "zustand";
import {
  createPlan, createPlanFromModule, getPlan, listPlans, removePlan, savePlan, simulatePlan,
  type Plan, type PlanMeta, type PlanSimResult,
} from "../api/plans";
import { listMapPlans, getMapPlanPayload, type MapPlanMeta } from "../api/map-plans";
import { draftFromJson, itemToJson, type DraftItem } from "./queue-draft";
import type { PlacementMap } from "./queue-draft";

interface QueueStore {
  loaded: boolean;
  plans: PlanMeta[] | null;
  plan: Plan | null;
  title: string;
  items: DraftItem[];
  dirty: boolean;
  msg: string | null;
  busy: boolean;
  newName: string;
  horizon: number;
  /** 试算（真 planner 干跑，不需要会话）—— 草稿板 + 已保存对照板，同一组件 */
  sim: PlanSimResult | null;
  baseSim: PlanSimResult | null;
  /** 对照板默认收起（用户拍板：平时只看草稿，不占横屏） */
  showBase: boolean;
  /** I8：放置引用的地图规划 —— 选项来自它的 payload；null = 回退夹具地图（无限定） */
  refPlans: MapPlanMeta[] | null;
  refId: string | null;
  refPayload: PlacementMap | null;
  initOnce(apiOk: boolean, initialPlanId: string | null): Promise<void>;
  refresh(): Promise<PlanMeta[]>;
  open(id: string): Promise<void>;
  save(): Promise<void>;
  create(copyFrom: string | null): Promise<void>;
  /** I12-B3：从参考模块（内置战术库）落地成新规划 —— 模板是唯一真相源 */
  createFromModule(moduleId: string): Promise<void>;
  del(): Promise<void>;
  /** 试算 = 后端真 planner 从标准开局干跑（A3/C7：前端不本地算；不需要会话） */
  simulate(): Promise<void>;
  update(id: string, patch: Partial<DraftItem>): void;
  setItems(fn: (l: DraftItem[]) => DraftItem[]): void;
  patch(p: Partial<Pick<QueueStore,
    "title" | "newName" | "horizon" | "msg" | "showBase" | "refId">>): void;
  /** 放置引用默认落当前规划空间的出厂校准布局（内部用，open/init 后调） */
  syncRefId(): void;
  syncRefPayload(): Promise<void>;
}

export const useQueueStore = create<QueueStore>((set, get) => ({
  loaded: false,
  plans: null,
  plan: null,
  title: "",
  items: [],
  dirty: false,
  msg: null,
  busy: false,
  newName: "",
  horizon: 300,
  sim: null,
  baseSim: null,
  showBase: false,
  refPlans: null,
  refId: null,
  refPayload: null,

  async initOnce(apiOk, initialPlanId) {
    if (!apiOk) return;
    const st = get();
    if (st.loaded) {
      // 深链二次消费（chat 改动 chip 跳进来带 ?plan=）
      if (initialPlanId && initialPlanId !== st.plan?.id
          && st.plans?.some((p) => p.id === initialPlanId)) {
        void get().open(initialPlanId);
      }
      return;
    }
    set({ loaded: true });
    try {
      const rows = await listPlans();
      set({ plans: rows });
      const target = (initialPlanId && rows.find((p) => p.id === initialPlanId)) ?? rows[0];
      if (target) await get().open(target.id);
    } catch (err) {
      set({ msg: "加载规划失败：" + (err as Error).message, loaded: false });
      return;
    }
    // 放置引用清单（失败不致命：回退夹具地图）
    try {
      set({ refPlans: await listMapPlans() });
    } catch {
      set({ refPlans: null });
    }
    get().syncRefId();
  },

  async refresh() {
    const rows = await listPlans();
    // 地图规划引用清单一起刷（2026-08-24 用户报「不刷新看不到最新规划」：
    // agent 走 REST 写的规划，前端清单此前只在 initOnce 拉一次）
    try {
      set({ plans: rows, refPlans: await listMapPlans() });
    } catch {
      set({ plans: rows });   // 引用清单失败不致命，保规划清单更新
    }
    return rows;
  },

  async open(id) {
    const p = await getPlan(id);
    set({
      plan: p,
      title: p.title_zh,
      items: draftFromJson(p.queue),
      dirty: false,
      msg: null,
      sim: null,
      baseSim: p.queue.length > 0
        ? await simulatePlan(p.queue, { horizon: get().horizon, planId: p.id }).catch(() => null)
        : null,
    });
    get().syncRefId();
  },

  async save() {
    const st = get();
    if (!st.plan || st.plan.locked) return;
    try {
      const p = await savePlan(st.plan.id, {
        title_zh: st.title || st.plan.title_zh, map: st.plan.map, spawn: st.plan.spawn,
        queue: st.items.map(itemToJson),
      });
      set({
        plan: p,
        title: p.title_zh,
        dirty: false,
        msg: "已保存 " + p.id,
        baseSim: p.queue.length > 0
          ? await simulatePlan(p.queue, { horizon: st.horizon, planId: p.id }).catch(() => null)
          : null,
      });
      await get().refresh();
    } catch (err) {
      set({ msg: "保存失败：" + (err as Error).message });
    }
  },

  async create(copyFrom) {
    const st = get();
    const id = st.newName.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || undefined;
    try {
      const p = await createPlan({
        id, title_zh: st.newName.trim() || "新规划",
        copy_from: copyFrom ?? undefined,
      });
      set({ newName: "" });
      await get().refresh();
      await get().open(p.id);
    } catch (err) {
      set({ msg: "新建失败：" + (err as Error).message });
    }
  },

  async createFromModule(moduleId) {
    const st = get();
    if (!moduleId) return;
    try {
      const p = await createPlanFromModule({
        module: moduleId,
        title_zh: st.newName.trim() || undefined,
      });
      set({ newName: "" });
      await get().refresh();
      await get().open(p.id);
      set({ msg: "已从模板落地 " + p.id + "（" + p.queue.length + " 项）" });
    } catch (err) {
      set({ msg: "模板落地失败：" + (err as Error).message });
    }
  },

  async del() {
    const st = get();
    if (!st.plan || st.plan.locked) return;
    try {
      await removePlan(st.plan.id);
      const rows = await get().refresh();
      if (rows[0]) await get().open(rows[0].id);
    } catch (err) {
      set({ msg: "删除失败：" + (err as Error).message });
    }
  },

  async simulate() {
    const st = get();
    if (st.items.length === 0) {
      set({ sim: null, msg: "草稿是空的" });
      return;
    }
    set({ busy: true });
    try {
      set({
        sim: await simulatePlan(st.items.map(itemToJson),
                                { horizon: st.horizon, planId: st.plan?.id }),
        msg: null,
      });
    } catch (err) {
      set({ sim: null, msg: "试算失败：" + (err as Error).message });
    } finally {
      set({ busy: false });
    }
  },

  update(id, patch) {
    set((s) => ({ items: s.items.map((it) => (it.id === id ? { ...it, ...patch } : it)), dirty: true }));
  },

  setItems(fn) {
    set((s) => ({ items: fn(s.items), dirty: true }));
  },

  patch(p) {
    set(p);
    // refId 变化时同步拉 payload（放置引用选项的数据源）
    if ("refId" in p) void get().syncRefPayload();
  },

  /** 放置引用默认落该空间的出厂校准布局（槽位最全，选项最丰富），没有就第一个 */
  syncRefId() {
    const st = get();
    const space = (st.refPlans ?? []).filter(
      (p) => p.map_name === (st.plan?.map ?? "LadderMap") && p.spawn === (st.plan?.spawn ?? "bl"));
    if (space.length === 0) {
      set({ refId: null });
      void get().syncRefPayload();
      return;
    }
    const preferred = space.find((p) => p.id === "layout-" + (st.plan?.spawn ?? "bl")) ?? space[0];
    const next = st.refId && space.some((p) => p.id === st.refId) ? st.refId : preferred!.id;
    if (next !== st.refId) {
      set({ refId: next });
      void get().syncRefPayload();
    }
  },

  async syncRefPayload() {
    const st = get();
    if (!st.refId) {
      set({ refPayload: null });
      return;
    }
    try {
      set({ refPayload: await getMapPlanPayload(st.refId) });
    } catch {
      set({ refPayload: null });
    }
  },
}));
