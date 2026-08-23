/**
 * 地图规划工作台状态（2026-08-22 左抽屉轮拆出）—— 原先全在 PlanningPage 的本地
 * state 里，导航一换页就丢；拆成模块级 store 后：
 *  - 抽屉（文件/工具/列表）与主区（画布）共享同一份真源；
 *  - 收起抽屉、切去别的入口再回来，选中/草稿都还在（用户拍板的保留语义）；
 *  - 深链（?map=<id>）由 initOnce 幂等消费。
 *
 * 几何校验（previewPlacement/placeableAt/拖动落点）留在页侧 —— 那是画布交互的
 * 一次性状态（hover 等），不属于这里；这里只管文件面与草稿 hunk 面。
 */
import { useMemo } from "react";
import { create } from "zustand";
import {
  createMapPlan, getMapPlanPayload, listMapPlans, removeMapPlan, saveMapPlan,
  type MapPlanMeta,
} from "../api/map-plans";
import { applyDraft, baseSlotsOf, mapDraftToHunks, type MapPlanHunk } from "./map-draft";
import { useFrames } from "../store/frames";
import type { MapStatic } from "../contract";

/** 地图规划 REST payload = static/map 形状 + 固定建造点预留区（矩形，后端单点算） */
export interface ReservedBox {
  tl: [number, number];
  br: [number, number];
  kind: string;
  label_zh: string;
  /** 预设名（蓝方主矿/蓝方二矿/红方主矿气井1…；矿脉无名） */
  name?: string | null;
}
export type PlanMapPayload = MapStatic & { reserved?: ReservedBox[] };

interface MapPlanStore {
  /** initOnce 跑过一次（清单+默认选中）；之后导航回来不再重拉 */
  loaded: boolean;
  mplans: MapPlanMeta[] | null;
  selMap: string;
  spawn: "bl" | "tr";
  selId: string | null;
  planPayload: PlanMapPayload | null;
  /** 草稿 = hunk 序列（真相源），画布/列表都吃 applyDraft 的投影 */
  draft: MapPlanHunk[];
  selectedName: string | null;
  placeMode: "mark" | "slot" | null;
  slotSize: 2 | 3 | 5;
  slotKind: "supply" | "production" | "addon";
  newName: string;
  planMsg: string | null;
  submitMsg: string | null;
  initOnce(apiOk: boolean, initialId: string | null): Promise<void>;
  refreshList(): Promise<MapPlanMeta[]>;
  openMapPlan(id: string): Promise<void>;
  /** 切地图/出生点 = 换一个规划空间：自动落该空间第一个规划 */
  switchSpace(m: string, sp: "bl" | "tr"): void;
  /** P2：离线保存到地图规划文件（不走提案 —— 用户拍板：离线域直改文件） */
  saveToFile(): Promise<void>;
  createMapCopy(): Promise<void>;
  deleteMapPlan(): Promise<void>;
  pushHunks(...hs: MapPlanHunk[]): void;
  patch(p: Partial<Pick<MapPlanStore,
    "selectedName" | "placeMode" | "slotSize" | "slotKind" | "newName" | "planMsg" | "submitMsg">>): void;
}

export const useMapPlanStore = create<MapPlanStore>((set, get) => ({
  loaded: false,
  mplans: null,
  selMap: "LadderMap",
  spawn: "bl",
  selId: null,
  planPayload: null,
  draft: [],
  selectedName: null,
  placeMode: null,
  slotSize: 2,
  slotKind: "supply",
  newName: "",
  planMsg: null,
  submitMsg: null,

  async initOnce(apiOk, initialId) {
    if (!apiOk) return;
    const st = get();
    if (st.loaded) {
      // 深链二次消费（chat 改动 chip 跳进来带 ?map=）：与当前选中不同才切
      if (initialId && initialId !== st.selId && st.mplans?.some((p) => p.id === initialId)) {
        void get().openMapPlan(initialId);
      }
      return;
    }
    set({ loaded: true });
    try {
      const rows = await listMapPlans();
      set({ mplans: rows });
      // 深链选中优先，否则默认 LadderMap/bl 第一个
      const deep = initialId && rows.find((p) => p.id === initialId);
      const first = deep
        ?? rows.find((p) => p.map_name === "LadderMap" && p.spawn === "bl")
        ?? rows[0];
      if (first) {
        set({ selMap: first.map_name, spawn: first.spawn as "bl" | "tr" });
        await get().openMapPlan(first.id);
      }
    } catch (err) {
      set({ planMsg: "加载地图规划失败：" + (err as Error).message, loaded: false });
    }
  },

  async refreshList() {
    const rows = await listMapPlans();
    set({ mplans: rows });
    return rows;
  },

  async openMapPlan(id) {
    const payload = await getMapPlanPayload(id);
    set({
      selId: id,
      planPayload: payload,
      draft: [],      // 换规划 = 换底图，草稿不跨规划携带
      submitMsg: null,
      planMsg: null,
    });
  },

  switchSpace(m, sp) {
    set({ selMap: m, spawn: sp });
    const first = (get().mplans ?? []).find((p) => p.map_name === m && p.spawn === sp);
    if (first) void get().openMapPlan(first.id);
    else set({ selId: null, planPayload: null, draft: [] });
  },

  async saveToFile() {
    const st = get();
    const meta = st.mplans?.find((p) => p.id === st.selId) ?? null;
    if (!st.selId || st.draft.length === 0) {
      set({ submitMsg: "草稿是空的" });
      return;
    }
    if (meta?.locked) {
      set({ submitMsg: "默认地图锁定：先「复制新建」再改" });
      return;
    }
    try {
      await saveMapPlan(st.selId, mapDraftToHunks(st.draft));
      await get().openMapPlan(st.selId);
      await get().refreshList();
      set({ submitMsg: "已保存到地图规划文件（" + st.selId + "）" });
    } catch (err) {
      set({ submitMsg: "保存被拒：" + (err as Error).message });
    }
  },

  async createMapCopy() {
    const st = get();
    const id = st.newName.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || undefined;
    try {
      const m = await createMapPlan({
        id, title_zh: st.newName.trim() || "我的地图",
        copy_from: st.selId ?? undefined,
      });
      set({ newName: "" });
      await get().refreshList();
      await get().openMapPlan(m.id);
    } catch (err) {
      set({ planMsg: "新建失败：" + (err as Error).message });
    }
  },

  async deleteMapPlan() {
    const st = get();
    const meta = st.mplans?.find((p) => p.id === st.selId);
    if (!st.selId || meta?.locked) return;
    try {
      await removeMapPlan(st.selId);
      const rows = await get().refreshList();
      const first = rows.find((r) => r.map_name === st.selMap && r.spawn === st.spawn) ?? rows[0];
      if (first) await get().openMapPlan(first.id);
    } catch (err) {
      set({ planMsg: "删除失败：" + (err as Error).message });
    }
  },

  pushHunks(...hs) {
    set((s) => ({ draft: [...s.draft, ...hs] }));
  },

  patch(p) {
    set(p);
  },
}));

/** 底图 = 所选地图规划的 payload（terrain 叠加夹具地形兜底）；无后端时回退夹具 map */
export function useBaseMap(): PlanMapPayload | null {
  const planPayload = useMapPlanStore((s) => s.planPayload);
  const fixtureMap = useFrames((s) => s.map);
  return planPayload
    ? { ...planPayload, terrain: planPayload.terrain ?? fixtureMap?.terrain ?? null }
    : fixtureMap;
}

/** 草稿投影（marks/slots 合并视图）—— 抽屉列表与画布共用 */
export function useMapProj() {
  const baseMap = useBaseMap();
  const draft = useMapPlanStore((s) => s.draft);
  return useMemo(
    () => (baseMap
      ? applyDraft(baseMap.pos_marks, baseSlotsOf(baseMap.build_slots), draft)
      : { marks: [], slots: [] }),
    [baseMap, draft],
  );
}
