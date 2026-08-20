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
import { listFixtures, loadFixture, type FixtureMeta } from "../fixtures";
import { JsonlFrameSource } from "../source/jsonl";
import { MockLiveFrameSource } from "../source/mock-live";
import { ReviewableSource } from "../source/reviewable";
import { WsFrameSource, probeApi } from "../source/ws";
import type { FrameSource, TimelineMarker, Unsubscribe } from "../source/types";

/**
 * 帧源种类：
 * - `fixture`   本地 JSONL（离线，可任意 seek）
 * - `mock-live` 用 JSONL 冒充 live（验"只读回看 + 回到实时"的语义）
 * - `api`       连后端 WS（B2 的回放服务；B3 之后同一条通道就是真 live）
 */
export type SourceKind = "fixture" | "mock-live" | "api";

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
}

const EMPTY_FRAMES: Frames = {
  session: null, world: null, flow: null, production: null, economy: null, ops: null, projection: null,
  alerts: null, proposals: null, map: null, catalog: null, schema: null, strategy: null,
};

interface FramesStore extends Frames {
  /** 可选夹具清单 */
  fixtures: FixtureMeta[];
  fixtureKey: string | null;
  sourceKind: SourceKind;
  /** 后端在不在（不在就把 api 选项置灰，而不是让 UI 转圈） */
  api: { ok: boolean; rev?: number; sources?: string[] };
  caps: { live: boolean; seek: boolean };
  mode: "live" | "review";
  range: { from: number; to: number };
  position: number;
  markers: TimelineMarker[];
  error: string | null;
  loading: boolean;

  init(): Promise<void>;
  attach(kind: SourceKind, fixtureKey: string): Promise<void>;
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
        mode: isReviewable(s) ? s.mode() : "live",
      });
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
    else setTimeout(flush, 16);
  };

  return {
    ...EMPTY_FRAMES,
    fixtures: [], fixtureKey: null, sourceKind: "fixture",
    api: { ok: false },
    caps: { live: false, seek: true }, mode: "live",
    range: { from: 0, to: 0 }, position: 0, markers: [],
    error: null, loading: false,

    async init() {
      // 探测后端与读本地夹具是独立的两件事：任一可用就能工作
      void probeApi(API_BASE).then((api) => set({ api }));
      try {
        const list = await listFixtures();
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
      const meta = get().fixtures.find((f) => f.key === fixtureKey);
      if (!meta) {
        set({ loading: false, error: `没有夹具 ${fixtureKey}` });
        return;
      }
      let text = "";
      if (kind !== "api") {          // api 源由后端推帧，不需要本地文件
        try {
          text = await loadFixture(meta);
        } catch (err) {
          set({ loading: false, error: (err as Error).message });
          return;
        }
      }

      let src: FrameSource;
      try {
        if (kind === "api") {
          const ws = WsFrameSource.replay(API_BASE, fixtureKey, 0);
          ws.onChange(syncMeta);
          await ws.connect();      // 握手里会校验 rev，不匹配直接抛（红线 C8）
          src = ws;
        } else if (kind === "mock-live") {
          const live = MockLiveFrameSource.fromJsonl(text, 4, 200);
          const rev = new ReviewableSource(live);
          rev.onChange(syncMeta);
          live.start();
          src = rev;
        } else {
          src = JsonlFrameSource.fromJsonl(text, meta.snapshots);
        }
      } catch (err) {
        set({ loading: false, error: (err as Error).message });
        return;
      }

      source = src;
      unsubs = [
        src.subscribe("frame/session", (e) => set({ session: e.payload })),
        src.subscribe("frame/world", (e) => set({ world: e.payload })),
        src.subscribe("frame/flow", (e) => set({ flow: e.payload })),
        src.subscribe("frame/production", (e) => set({ production: e.payload })),
        src.subscribe("frame/economy", (e) => set({ economy: e.payload })),
        src.subscribe("frame/ops", (e) => set({ ops: e.payload })),
        src.subscribe("frame/projection", (e) => set({ projection: e.payload })),
        src.subscribe("frame/alerts", (e) => set({ alerts: e.payload })),
        src.subscribe("proposals", (e) => set({ proposals: e.payload })),
        src.subscribe("static/map", (e) => set({ map: e.payload })),
        src.subscribe("static/catalog", (e) => set({ catalog: e.payload })),
        src.subscribe("static/schema", (e) => set({ schema: e.payload })),
        src.subscribe("static/strategy", (e) => set({ strategy: e.payload })),
      ];
      set({
        sourceKind: kind, fixtureKey, loading: false, caps: src.caps,
        range: src.range(), position: src.position(), markers: src.markers(),
        mode: isReviewable(src) ? src.mode() : "live",
      });
      syncMeta();
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