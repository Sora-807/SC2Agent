/**
 * MockLiveFrameSource —— 用 JSONL 冒充一条 live 流。
 *
 * 为什么需要它：F8 才会有真 WebSocket（依赖后端 B2/B3），但"只读回看 + 回到实时"的语义
 * （ADR-0023 §3）现在就得钉死，否则等 live 接上再发现语义不对就是重做。
 * 它的行为刻意受限：`caps.seek=false`、只能往前推、订阅只给最新帧 —— 和真 live 一样。
 */
import type { AnyEnvelope, EnvelopeFor, Topic } from "../contract";
import { extractMarkers, parseJsonl } from "./jsonl";
import type {
  FrameSource,
  FrameSourceCaps,
  TimelineMarker,
  Unsubscribe,
} from "./types";

type Listener = (env: AnyEnvelope) => void;

export class MockLiveFrameSource implements FrameSource {
  readonly caps: FrameSourceCaps = { live: true, seek: false };

  private readonly script: AnyEnvelope[];
  private readonly listeners = new Map<Topic, Set<Listener>>();
  private readonly latest = new Map<Topic, AnyEnvelope>();
  private readonly _markers: TimelineMarker[] = [];
  private idx = 0;
  private clock: number;
  private timer: ReturnType<typeof setInterval> | null = null;

  /** rate = 游戏秒/真实秒；tickMs = 推进步长 */
  constructor(envelopes: AnyEnvelope[], private readonly rate = 4, private readonly tickMs = 200) {
    this.script = [...envelopes].sort((a, b) => a.game_time - b.game_time || a.seq - b.seq);
    this.clock = this.script.at(0)?.game_time ?? 0;
  }

  static fromJsonl(text: string, rate = 4, tickMs = 200): MockLiveFrameSource {
    return new MockLiveFrameSource(parseJsonl(text), rate, tickMs);
  }

  start(): void {
    if (this.timer !== null) return;
    this.pump(); // 立即吐出 t0 的静态面，UI 不用等一个 tick
    this.timer = setInterval(() => {
      this.advance((this.rate * this.tickMs) / 1000);
    }, this.tickMs);
  }

  /** 推进游戏时间并吐出跨过的帧。测试用它替代定时器，行为完全一致（确定性）。 */
  advance(dtGameSeconds: number): void {
    this.clock += dtGameSeconds;
    this.pump();
  }

  private pump(): void {
    while (this.idx < this.script.length) {
      const env = this.script[this.idx];
      if (!env || env.game_time > this.clock + 1e-9) break;
      this.idx += 1;
      this.latest.set(env.topic, env);
      for (const m of extractMarkers([env])) this._markers.push(m);
      const set = this.listeners.get(env.topic);
      if (set) for (const fn of set) fn(env);
    }
    if (this.idx >= this.script.length) this.pause();
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

  seek(): void {
    throw new Error("live 源不支持 seek（需套一层 ReviewableSource 提供环形缓冲回看）");
  }

  range(): { from: number; to: number } {
    return { from: this.script.at(0)?.game_time ?? 0, to: this.clock };
  }

  position(): number {
    return this.clock;
  }

  markers(): TimelineMarker[] {
    return this._markers;
  }

  pause(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  dispose(): void {
    this.pause();
    this.listeners.clear();
  }
}
