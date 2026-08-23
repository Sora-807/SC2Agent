/**
 * 帧 store —— 组件唯一的读帧入口（红线：组件不得 import FrameSource 实现）
 *
 * 构造实现的职责在这里，页面只调 `attachFixture` / `attachMockLive` / `seek` / `returnToLive`。
 * 这样"换帧源"就是一次 attach，页面代码零改动 —— 复盘、夹具、live 共用同一批组件（决策 U1/U2）。
 */
import { create } from "zustand";
import type {
  AlertsFrame, CatalogStatic, EconomyFrame, FlowFrame, MapStatic, OpsFrame, ProductionFrame,
  ProjectionFrame, ProposalsFrame, SchemaStatic, SessionFrame, StrategyStatic, WorldFrame,
} from "../contract";
import { listFixtures, listRecordings, loadFixture, type FixtureMeta } from "../fixtures";
import { JsonlFrameSource } from "../source/jsonl";
import { MockLiveFrameSource } from "../source/mock-live";
import { ReviewableSource } from "../source/reviewable";
import { WsFrameSource, probeApi } from "../source/ws";
import type { FrameSource, TimelineMarker, Unsubscribe } from "../source/types";
import { defaultSource, type Mode } from "../shell/mode";

/**
 * 帧源种类：
 * - `fixture`   本地 JSONL（离线，可任意 seek）
 * - `mock-live` 用 JSONL 冒充 live（验"只读回看 + 回到实时"的语义）
 * - `api`       连后端 WS 回放服务（任意 JSONL 帧源，可 seek）
 * - `live`      连后端 **live 会话**（B3 的子进程驱动；`ReviewableSource` 提供环形缓冲回看）
 */
export type SourceKind = "fixture" | "mock-live" | "api" | "live";

/** 后端 API 基址（开发期固定；`tools/serve_api.py` 的默认端口） */
export const API_BASE = "http://127.0.0.1:8770";

interface Frames {
  session: SessionFrame | null;
  world: WorldFrame | null;
  flow: FlowFrame | null;
  production: ProductionFrame | null;
  economy: EconomyFrame | null;
  ops: OpsFrame | null;
  projection: ProjectionFrame | null;
  alerts: AlertsFrame | null;
  proposals: ProposalsFrame | null;
  map: MapStatic | null;
  catalog: CatalogStatic | null;
  schema: SchemaStatic | null;
  /** 策略图结构（static/strategy）：F4 的图与 F9 的编辑器都靠它 */
  strategy: StrategyStatic | null;
  /** terrain 先于 map 到达时的挂起载荷（真机 on_step 曾先发地形后发静态面；
   *  帧序不可假设 —— A5 快照语义，合并必须两个方向都成立） */
  pendingTerrain: MapStatic["terrain"];
}

const EMPTY_FRAMES: Frames = {
  session: null, world: null, flow: null, production: null, economy: null, ops: null, projection: null,
  alerts: null, proposals: null, map: null, catalog: null, schema: null, strategy: null,
  pendingTerrain: null,
};

interface FramesStore extends Frames {
  /** 可选夹具清单 */
  fixtures: FixtureMeta[];
  fixtureKey: string | null;
  sourceKind: SourceKind;
  /** 后端在不在（不在就把 api 选项置灰，而不是让 UI 转圈） */
  api: { ok: boolean; rev?: number; sources?: string[] };
  caps: { live: boolean; seek: boolean };
  /** 回看子状态（跟随/回看，只对可回看源有意义）——与模式轴 `mode` 是两个东西 */
  timeline: "live" | "review";
  /** 模式轴（F13/U19）：离线编辑 / 实时驾驶 / 复盘 */
  mode: Mode;
  range: { from: number; to: number };
  position: number;
  /** 当前帧的 seq —— 命令的 `based_on_seq` 取它（R8） */
  seq: number;
  markers: TimelineMarker[];
  error: string | null;
  loading: boolean;
  /** WS 帧流中途断开（握手成功之后）。之前零处理 = 驾驶舱静默冻结在最后一帧 */
  disconnected: boolean;

  init(): Promise<void>;
  attach(kind: SourceKind, fixtureKey: string): Promise<void>;
  /** 断线后用当前 (kind, fixtureKey) 重新 attach */
  reconnect(): Promise<void>;
  setMode(m: Mode): Promise<void>;
  /** 重新探测后端（用户后启动 serve_api 时不用刷新页面） */
  probe(): void;
  seek(t: number): void;
  returnToLive(): void;
  /** 夹具/复盘的自动前进（live 源不需要） */
  play(rate?: number): void;
  pause(): void;
  detach(): void;
}

let source: FrameSource | null = null;
let unsubs: Unsubscribe[] = [];
let rafPending = false;

/** 动态帧合并（二十三轮：live 地图卡顿的主因之一）—— SC2 每 game step 一帧
 *  （~16/s），逐帧 set() 把 React 渲染打满。按 topic 只留最新 payload，首个到达
 *  后 150ms 冲刷一次：≤7 次 setState/秒，画面延迟 ≤150ms（live 视觉无感）。
 *  静态面（static/*）不走这里 —— 一局只来几次，且测试同步断言依赖立即生效。 */
const FRAME_FLUSH_MS = 150;
const frameBuf = new Map<string, () => void>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;
const offer = (key: string, apply: () => void): void => {
  frameBuf.set(key, apply);
  if (flushTimer === null) {
    flushTimer = setTimeout(() => {
      flushTimer = null;
      const fns = [...frameBuf.values()];
      frameBuf.clear();
      for (const fn of fns) fn();
    }, FRAME_FLUSH_MS);
  }
};

export const useFrames = create<FramesStore>((set, get) => {
  /** 把源的游标/范围/模式同步进 store（合帧到一次 rAF，避免每帧 setState 抖动） */
  const syncMeta = (): void => {
    if (rafPending) return;
    rafPending = true;
    const flush = (): void => {
      rafPending = false;
      const s = source;
      if (!s) return;
      set({
        range: s.range(),
        position: s.position(),
        markers: s.markers(),
        timeline: isReviewable(s) ? s.mode() : "live",
      });
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
    else setTimeout(flush, 16);
  };

  return {
    ...EMPTY_FRAMES,
    fixtures: [], fixtureKey: null, sourceKind: "fixture",
    api: { ok: false },
    caps: { live: false, seek: true }, timeline: "live", mode: "offline",
    range: { from: 0, to: 0 }, position: 0, seq: 0, markers: [],
    error: null, loading: false, disconnected: false,

    async init() {
      // 探测后端与读本地夹具是独立的两件事：任一可用就能工作
      void probeApi(API_BASE).then((api) => set({ api }));
      try {
        // 二十六轮：复盘下拉 = 夹具（手搓场景）+ 对局录像（后端落盘的真局）。
        // 录像取不到不影响夹具（listRecordings 内部吞错返回空表）。
        const fixtures = await listFixtures();
        const recordings = await listRecordings(API_BASE);
        const list = [...fixtures, ...recordings];
        set({ fixtures: list, error: null });
        const first = list.at(0);
        if (first) await get().attach("fixture", first.key);
      } catch (err) {
        set({ error: (err as Error).message });
      }
    },

    async attach(kind, fixtureKey) {
      get().detach();
      set({ loading: true, error: null, ...EMPTY_FRAMES });
      // api/live 源的帧由后端推，不需要本地夹具索引里有它
      const meta = get().fixtures.find((f) => f.key === fixtureKey);
      if (kind !== "api" && kind !== "live" && !meta) {
        set({ loading: false, error: `没有夹具 ${fixtureKey}` });
        return;
      }
      let text = "";
      if (kind !== "api" && kind !== "live") {
        // 注：live 也不读本地夹具（它的帧全部来自后端推送；F13 修：之前 live 会误走
        // loadFixture(undefined) —— 但旧 SessionBar 从不产生 "live"，所以一直没炸）
        try {
          text = await loadFixture(meta!);
        } catch (err) {
          set({ loading: false, error: (err as Error).message });
          return;
        }
      }

      let src: FrameSource;
      try {
        if (kind === "live") {
          // live 会话：WS 是**跟随**的（caps.seek=false），套 ReviewableSource
          // 拿到"拖回最近历史不碰后端 + 回看期间 live 继续累积"（F1 已验的语义）。
          const ws = WsFrameSource.replay(API_BASE, "live", 0, { live: true });
          const rev = new ReviewableSource(ws);
          rev.onChange(syncMeta);
          ws.onDisconnect(() => set({ disconnected: true }));
          await ws.connect();      // 握手里会校验 rev，不匹配直接抛（红线 C8）
          src = rev;
        } else if (kind === "api") {
          const ws = WsFrameSource.replay(API_BASE, fixtureKey, 0);
          ws.onChange(syncMeta);
          ws.onDisconnect(() => set({ disconnected: true }));
          await ws.connect();      // 握手里会校验 rev，不匹配直接抛（红线 C8）
          src = ws;
        } else if (kind === "mock-live") {
          const live = MockLiveFrameSource.fromJsonl(text, 4, 200);
          const rev = new ReviewableSource(live);
          rev.onChange(syncMeta);
          live.start();
          src = rev;
        } else {
          src = JsonlFrameSource.fromJsonl(text, meta!.snapshots);
        }
      } catch (err) {
        set({ loading: false, error: (err as Error).message });
        return;
      }

      source = src;
      unsubs = [
        // 动态帧经 offer() 合并（见 FRAME_FLUSH_MS 注释）；static/* 直通
        src.subscribe("frame/session", (e) => offer("session", () => set({ session: e.payload }))),
        // world 帧的 seq 就是"这一刻的世界版本"，命令拿它当 based_on_seq
        src.subscribe("frame/world", (e) => offer("world", () => set({ world: e.payload, seq: e.seq }))),
        src.subscribe("frame/flow", (e) => offer("flow", () => set({ flow: e.payload }))),
        src.subscribe("frame/production", (e) => offer("production", () => set({ production: e.payload }))),
        src.subscribe("frame/economy", (e) => offer("economy", () => set({ economy: e.payload }))),
        src.subscribe("frame/ops", (e) => offer("ops", () => set({ ops: e.payload }))),
        src.subscribe("frame/projection", (e) => offer("projection", () => set({ projection: e.payload }))),
        src.subscribe("frame/alerts", (e) => offer("alerts", () => set({ alerts: e.payload }))),
        src.subscribe("proposals", (e) => offer("proposals", () => set({ proposals: e.payload }))),
        src.subscribe("static/map", (e) => set((s2) => ({
          map: { ...e.payload, terrain: e.payload.terrain ?? s2.pendingTerrain ?? null },
          pendingTerrain: null,
        }))),
        // B4：地形晚到（真机上 game_info 在 bot 第一个 on_step 才可用）。
        // 合并进 map.terrain —— 不是重发一张完整 static/map。
        // 顺序不可假设（A5）：terrain 先到就先挂 pendingTerrain，map 到了再并 ——
        // 真机 driver 曾先发地形后发静态面，这里丢过一整局的地形（"地形不可用"）。
        src.subscribe("static/terrain", (e) => set((s2) => ({
          map: s2.map ? { ...s2.map, terrain: e.payload } : null,
          pendingTerrain: s2.map ? null : e.payload,
        }))),
        src.subscribe("static/catalog", (e) => set({ catalog: e.payload })),
        src.subscribe("static/schema", (e) => set({ schema: e.payload })),
        src.subscribe("static/strategy", (e) => set({ strategy: e.payload })),
      ];
      set({
        sourceKind: kind, fixtureKey, loading: false, caps: src.caps, disconnected: false,
        range: src.range(), position: src.position(), markers: src.markers(),
        timeline: isReviewable(src) ? src.mode() : "live",
      });
      syncMeta();
    },

    probe() {
      void probeApi(API_BASE).then((api) => set({ api }));
    },

    async setMode(m) {
      // 切模式 = 换到该模式的默认帧源（模式→合法帧源的映射在 shell/mode.ts，UI 不自算）
      const { mode: current, fixtureKey, fixtures, api } = get();
      if (m === current) return;
      const target = defaultSource(m, fixtures, api.ok, fixtureKey);
      if (target === null) {
        set({
          mode: m,
          error: m === "drive"
            ? "实时驾驶需要后端 API：先启动 python tools/serve_api.py，再回到这里切模式"
            : m === "replay"
              ? "还没有对局记录 —— 切到「游戏」开一局（真机或沙盒），结束后自动出现在复盘列表里"
              : "该模式没有可用帧源（没有夹具？先在 web/ 下跑 pnpm gen:fixtures）",
        });
        return;
      }
      set({ mode: m });
      await get().attach(target.kind, target.fixtureKey);
    },

    async reconnect() {
      const { sourceKind, fixtureKey } = get();
      if (!fixtureKey) return;      // 理论上不该发生（任何已 attach 的源都有键）
      await get().attach(sourceKind, fixtureKey);
    },

    seek(t) {
      if (!source?.caps.seek) return;
      source.seek(t);
      syncMeta();
    },

    returnToLive() {
      if (source && isReviewable(source)) {
        source.returnToLive();
        syncMeta();
      }
    },

    play(rate = 4) {
      if (source instanceof WsFrameSource) {
        source.play(rate);
        return;
      }
      if (source instanceof JsonlFrameSource) {
        source.play(rate);
        const tick = setInterval(() => {
          if (!(source instanceof JsonlFrameSource)) {
            clearInterval(tick);
            return;
          }
          syncMeta();
          if (source.position() >= source.range().to) clearInterval(tick);
        }, 120);
      }
    },

    pause() {
      if (source instanceof WsFrameSource) source.pause();
      if (source instanceof JsonlFrameSource) source.pause();
      syncMeta();
    },

    detach() {
      for (const u of unsubs) u();
      unsubs = [];
      source?.dispose();
      source = null;
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      frameBuf.clear();
    },
  };
});

function isReviewable(s: FrameSource): s is FrameSource & {
  mode(): "live" | "review";
  returnToLive(): void;
  onChange(cb: () => void): Unsubscribe;
} {
  return s instanceof ReviewableSource;
}