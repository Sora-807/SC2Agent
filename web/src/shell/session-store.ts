/**
 * 会话面状态（2026-08-22 顶栏极简轮从 SessionBar 抽出）—— 一个模块级 store，
 * ModeBar 的指示灯/收尾按钮与主区的 StartCard 共用**同一份轮询**，不各开一套。
 *
 * 职责边界：会话描述轮询（drive 模式 2s）、地图规划清单（会话装配用）、
 * 真机两段式启动、停止（树杀兜底）。帧流状态（WS 断线）不在这 —— 那是 frames store 的地盘。
 */
import { create } from "zustand";
import { fetchSessionInfo, sessionAction, sessionSpeed, type SessionInfo } from "../api/commands";
import { listLoadouts, type LoadoutMeta } from "../api/loadouts";
import { listMapPlans } from "../api/map-plans";
import { listStrategies, type StrategyMeta } from "../api/strategies";
import { pickMapPlan } from "./mode";
import { useFrames } from "../store/frames";

/** 两段式确认的回落时间（毫秒）：点一次变「确认」，超时自动还原 */
const CONFIRM_RESET_MS = 4000;
const POLL_MS = 2000;

export interface MapPlanLite {
  id: string;
  title_zh: string;
  locked: boolean;
}

interface SessionStore {
  /** 是否在轮询（App 在 drive 模式 && 后端在线时打开） */
  watching: boolean;
  info: SessionInfo | null;
  mapPlans: MapPlanLite[] | null;
  /** null = 清单还没到；到达后经 pickMapPlan 兜底（发不存在的 id 会被后端 400） */
  mapPlanId: string | null;
  /** 策略文件清单（二十七轮「开放写策略」）：null = 还没到/后端没有；启动时选一份装配 */
  strategies: StrategyMeta[] | null;
  strategyId: string | null;
  /** 装配清单（B1）：三件套一发入魂；null = 还没到/后端没有；选中时优先于单独的规划/策略下拉 */
  loadouts: LoadoutMeta[] | null;
  loadoutId: string | null;
  /** 开启游戏的模式（2026-08-23 收敛）：normal=正常（实时可见）；fast=仿真（快进看结果） */
  gameMode: "normal" | "fast";
  /** 仿真模式起始倍数（0=不限速/最快）；运行中可经 setGameSpeed 热改 */
  gameSpeed: number;
  /** 启动/停止失败的原因（后端 detail 原文；会话活过来时自动清） */
  opErr: string | null;
  /** 真机两段式确认：第一点变「确认启动」，再点才真启动 */
  confirming: boolean;
  setWatch(on: boolean): void;
  setMapPlanId(id: string): void;
  setStrategyId(id: string): void;
  setLoadoutId(id: string): void;
  setGameMode(mode: "normal" | "fast"): void;
  setGameSpeed(speed: number): void;
  /** 运行中的仿真会话变速（即时生效，不重启；同时记住为起始倍数） */
  changeGameSpeed(speed: number): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
}

let timer: ReturnType<typeof setInterval> | null = null;
let confirmTimer: ReturnType<typeof setTimeout> | null = null;
let watchToken = 0;

async function pollOnce(): Promise<void> {
  const s = await fetchSessionInfo();
  // 停止轮询后迟到的响应直接丢弃（token 失配）
  if (!useSessionStore.getState().watching) return;
  useSessionStore.setState((st) => ({
    info: s,
    // 会话活着 = 上一次失败已经翻篇
    opErr: s?.alive ? null : st.opErr,
  }));
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  watching: false,
  info: null,
  mapPlans: null,
  mapPlanId: null,
  strategies: null,
  strategyId: null,
  loadouts: null,
  loadoutId: null,
  gameMode: "fast",
  gameSpeed: 0,
  opErr: null,
  confirming: false,

  setWatch(on) {
    if (on === get().watching) return;
    if (on) {
      const token = ++watchToken;
      set({ watching: true });
      void pollOnce();
      void listMapPlans()
        .then((rows) => {
          if (token !== watchToken) return;
          const list = rows.map((r) => ({ id: r.id, title_zh: r.title_zh, locked: r.locked }));
          set((st) => ({ mapPlans: list, mapPlanId: pickMapPlan(list, st.mapPlanId) }));
        })
        .catch(() => set({ mapPlans: null }));
      // 策略清单（失败不致命：不选 = 内置默认策略）
      void listStrategies()
        .then((rows) => {
          if (token !== watchToken) return;
          set((st) => ({
            strategies: rows,
            strategyId: st.strategyId && rows.some((r) => r.id === st.strategyId)
              ? st.strategyId
              : null,
          }));
        })
        .catch(() => set({ strategies: null }));
      // 装配清单（B1；失败不致命：不选 = 手动挑规划/策略）
      void listLoadouts()
        .then((rows) => {
          if (token !== watchToken) return;
          set((st) => ({
            loadouts: rows,
            loadoutId: st.loadoutId && rows.some((r) => r.id === st.loadoutId)
              ? st.loadoutId
              : null,
          }));
        })
        .catch(() => set({ loadouts: null }));
      timer = setInterval(() => void pollOnce(), POLL_MS);
    } else {
      ++watchToken;
      if (timer) clearInterval(timer);
      timer = null;
      set({ watching: false, info: null, confirming: false });
    }
  },

  setMapPlanId(id) {
    set({ mapPlanId: id });
  },

  setStrategyId(id) {
    set({ strategyId: id || null });
  },

  setLoadoutId(id) {
    set({ loadoutId: id || null });
  },

  setGameMode(mode) {
    set({ gameMode: mode });
  },

  setGameSpeed(speed) {
    set({ gameSpeed: speed });
  },

  async changeGameSpeed(speed) {
    set({ gameSpeed: speed, opErr: null });
    const r = await sessionSpeed(speed);
    if (!r.ok) {
      set({ opErr: r.detail });
      return;
    }
    set({ info: await fetchSessionInfo() });
  },

  async start() {
    set({ opErr: null });
    if (!get().confirming) {
      set({ confirming: true });
      if (confirmTimer) clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => set({ confirming: false }), CONFIRM_RESET_MS);
      return;
    }
    set({ confirming: false });
    if (confirmTimer) clearTimeout(confirmTimer);
    // 装配清单（B1）：选了它就只发 loadout —— 三件套由后端解析
    //（含生产序列自动入队）；单独的规划/策略选择只在没选清单时生效。
    const los = get().loadouts;
    const loadoutId = los && get().loadoutId && los.some((r) => r.id === get().loadoutId)
      ? get().loadoutId! : undefined;
    // 发送前兜底（pickMapPlan）：规划 id 必须在清单里，不在就不带（后端用出厂模板）
    const plans = get().mapPlans;
    const planId = !loadoutId && plans ? pickMapPlan(plans, get().mapPlanId) ?? undefined : undefined;
    // 策略同规则：选中的 id 必须仍在清单里，否则不带（后端用内置默认）
    const st = get().strategies;
    const strategyId = !loadoutId && st && get().strategyId && st.some((r) => r.id === get().strategyId)
      ? get().strategyId! : undefined;
    // 「开启游戏」两模式（2026-08-23 收敛）：normal=实时；fast=仿真快进（带倍数）
    const { gameMode, gameSpeed } = get();
    const r = await sessionAction("start", {
      driver: "sc2", mode: gameMode,
      speed: gameMode === "fast" ? gameSpeed : 0,
      mapPlan: planId, strategy: strategyId, loadout: loadoutId,
    });
    if (!r.ok) {
      set({ opErr: r.detail, info: await fetchSessionInfo() });
      return;
    }
    set({ info: await fetchSessionInfo() });
    // WS 在无会话时也保持连接（后端合成「未连接」帧），会话起来后同一连接自动接上
    await useFrames.getState().setMode("drive");
    if (useFrames.getState().sourceKind !== "live") {
      await useFrames.getState().attach("live", "live");
    }
  },

  async stop() {
    set({ opErr: null, confirming: false });
    const r = await sessionAction("stop");
    if (!r.ok) set({ opErr: r.detail });
    set({ info: await fetchSessionInfo() });
  },
}));
