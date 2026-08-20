/**
 * WsFrameSource —— 连后端 `WS /api/frames`（B2 的回放服务，B3 之后同一条通道接 live）。
 *
 * 服务端已经实现了"每 topic 取 <= 游标的最后一帧"的 seek，语义与 `JsonlFrameSource` **逐字一致**，
 * 所以这里不需要自己维护缓冲：seek 直接转发给服务端。真 live（B3）会把 `caps.live` 变成 true，
 * 那时再在外面套 `ReviewableSource` 就能拿到"回看期间 live 继续累积"。
 *
 * 协议里的控制消息（`_hello`/`_eof`/`_error`/`_pong`）用下划线前缀与帧 topic 区分，
 * 所以**永远不会**和契约的 topic 闭集撞名。
 */
import { REV, parseEnvelope, type AnyEnvelope, type EnvelopeFor, type Topic } from "../contract";
import type { FrameSource, FrameSourceCaps, TimelineMarker, Unsubscribe } from "./types";
import { extractMarkers } from "./jsonl";

type Listener = (env: AnyEnvelope) => void;

export interface WsSourceInfo {
  id: string;
  kind: string;
  from: number;
  to: number;
}

export class WsFrameSource implements FrameSource {
  readonly caps: FrameSourceCaps;

  private ws: WebSocket | null = null;
  private readonly listeners = new Map<Topic, Set<Listener>>();
  private readonly latest = new Map<Topic, AnyEnvelope>();
  private readonly _markers: TimelineMarker[] = [];
  private readonly changeCbs = new Set<() => void>();
  private info: WsSourceInfo | null = null;
  private cursor = 0;
  private _error: string | null = null;

  constructor(
    private readonly url: string,
    opts: { live?: boolean } = {},
  ) {
    // 回放源可 seek；live 源（B3）不可 —— 那时靠外层 ReviewableSource 提供回看
    this.caps = { live: opts.live ?? false, seek: !(opts.live ?? false) };
  }

  static replay(base: string, sourceId: string, rate = 0, opts: { live?: boolean } = {}): WsFrameSource {
    const u = new URL(base);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    u.pathname = "/api/frames";
    u.searchParams.set("source", sourceId);
    u.searchParams.set("rate", String(rate));
    return new WsFrameSource(u.toString(), { live: opts.live ?? false });
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.url);
      this.ws = ws;
      let settled = false;
      ws.onmessage = (ev) => {
        const raw = JSON.parse(String(ev.data)) as { topic?: string };
        const topic = raw.topic ?? "";
        if (topic.startsWith("_")) {
          if (topic === "_hello") {
            const h = raw as unknown as { rev: number; source: string; kind: string; from: number; to: number };
            if (h.rev !== REV) {
              this._error = `契约版本不匹配：后端 rev=${h.rev}，前端 REV=${REV}`;
              if (!settled) { settled = true; reject(new Error(this._error)); }
              ws.close();
              return;
            }
            this.info = { id: h.source, kind: h.kind, from: h.from, to: h.to };
            this.cursor = h.from;
            this.notify();
            if (!settled) { settled = true; resolve(); }
          } else if (topic === "_error") {
            this._error = String((raw as unknown as { detail?: string }).detail ?? "未知错误");
            if (!settled) { settled = true; reject(new Error(this._error)); }
          } else if (topic === "_eof") {
            this.notify();
          }
          return;
        }
        // 真帧：过一遍契约校验（非法帧当场抛，不静默渲染错值）
        const env = parseEnvelope(raw);
        this.latest.set(env.topic, env);
        this.cursor = Math.max(this.cursor, env.game_time);
        for (const m of extractMarkers([env])) this._markers.push(m);
        const set = this.listeners.get(env.topic);
        if (set) for (const fn of set) fn(env);
        this.notify();
      };
      ws.onerror = () => {
        if (!settled) { settled = true; reject(new Error("WebSocket 连接失败：" + this.url)); }
      };
      ws.onclose = () => this.notify();
    });
  }

  error(): string | null {
    return this._error;
  }

  subscribe<K extends Topic>(topic: K, cb: (env: EnvelopeFor<K>) => void): Unsubscribe {
    const fn = cb as unknown as Listener;
    let set = this.listeners.get(topic);
    if (!set) {
      set = new Set();
      this.listeners.set(topic, set);
    }
    set.add(fn);
    const cur = this.latest.get(topic);
    if (cur) fn(cur);
    return () => {
      this.listeners.get(topic)?.delete(fn);
    };
  }

  seek(gameTime: number): void {
    if (!this.caps.seek) throw new Error("live 源不支持 seek（外面套 ReviewableSource）");
    this.send({ op: "seek", game_time: gameTime });
    this.cursor = gameTime;
  }

  play(rate = 4): void {
    this.send({ op: "play", rate });
  }

  pause(): void {
    this.send({ op: "pause" });
  }

  range(): { from: number; to: number } {
    return this.info ? { from: this.info.from, to: this.info.to } : { from: 0, to: 0 };
  }

  position(): number {
    return this.cursor;
  }

  markers(): TimelineMarker[] {
    return this._markers;
  }

  onChange(cb: () => void): Unsubscribe {
    this.changeCbs.add(cb);
    return () => {
      this.changeCbs.delete(cb);
    };
  }

  dispose(): void {
    this.listeners.clear();
    this.changeCbs.clear();
    this.ws?.close();
    this.ws = null;
  }

  private send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  private notify(): void {
    for (const cb of this.changeCbs) cb();
  }
}

/** 探测后端是否在跑（没跑就让 UI 显示"未连接"而不是转圈）*/
export async function probeApi(base: string): Promise<{ ok: boolean; rev?: number; sources?: string[] }> {
  try {
    const res = await fetch(new URL("/api/health", base).toString());
    if (!res.ok) return { ok: false };
    const body = (await res.json()) as { rev: number; sources: string[] };
    return { ok: true, rev: body.rev, sources: body.sources };
  } catch {
    return { ok: false };
  }
}