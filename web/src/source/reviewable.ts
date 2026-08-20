/**
 * ReviewableFrameSource —— 环形缓冲 + 只读回看（ADR-0023 §3 的实现）
 *
 * 包一个 live 源，得到三件事：
 *  1. **拖回最近历史不碰后端**：最近 N 帧留在客户端环形缓冲里；
 *  2. **回看期间 live 继续在后台累积**（ADR-0023 反例明确禁止"拖回历史时停止采集"）：
 *     新帧照进缓冲，但**不推给订阅者**，所以画面停在历史那一刻；
 *  3. `returnToLive()` 一键回到最新帧。
 *
 * 拖到最右端等价于回到实时（顺手的交互，不需要再点按钮）。
 */
import { isStaticTopic, zTopic, type AnyEnvelope, type EnvelopeFor, type Topic } from "../contract";
import type {
  FrameSource,
  FrameSourceCaps,
  ReviewableFrameSource,
  TimelineMarker,
  Unsubscribe,
} from "./types";

type Listener = (env: AnyEnvelope) => void;

const DEFAULT_RETAIN = 900; // 每 topic 保留帧数；1Hz 下约 15 分钟

export class ReviewableSource implements ReviewableFrameSource {
  readonly caps: FrameSourceCaps = { live: true, seek: true };

  private readonly buffers = new Map<Topic, AnyEnvelope[]>();
  /** 哪些 topic 真的丢过帧（只有丢过帧的 topic 才约束可 seek 窗口，见 range() 注释） */
  private readonly evicted = new Set<Topic>();
  private readonly listeners = new Map<Topic, Set<Listener>>();
  private readonly innerUnsubs: Unsubscribe[] = [];
  private readonly changeCbs = new Set<() => void>();
  private _mode: "live" | "review" = "live";
  private cursor = 0;

  constructor(
    private readonly inner: FrameSource,
    private readonly retain = DEFAULT_RETAIN,
  ) {
    // topic 是闭集（契约 §2.1），所以可以全量订阅内层源
    for (const topic of zTopic.options) {
      this.innerUnsubs.push(
        this.inner.subscribe(topic, (env) => this.ingest(env as AnyEnvelope)),
      );
    }
  }

  private ingest(env: AnyEnvelope): void {
    let buf = this.buffers.get(env.topic);
    if (!buf) {
      buf = [];
      this.buffers.set(env.topic, buf);
    }
    buf.push(env);
    if (buf.length > this.retain) {
      buf.splice(0, buf.length - this.retain);
      this.evicted.add(env.topic);
    }

    if (this._mode === "live") {
      this.cursor = env.game_time;
      const set = this.listeners.get(env.topic);
      if (set) for (const fn of set) fn(env);
    } else {
      // 回看模式：只进缓冲，不推给订阅者 —— live 在后台继续跑
      const { from } = this.range();
      if (this.cursor < from) this.cursor = from; // 缓冲淘汰到游标之前 → 夹回窗口内
    }
    this.notify();
  }

  mode(): "live" | "review" {
    return this._mode;
  }

  subscribe<K extends Topic>(topic: K, cb: (env: EnvelopeFor<K>) => void): Unsubscribe {
    const fn = cb as unknown as Listener;
    let set = this.listeners.get(topic);
    if (!set) {
      set = new Set();
      this.listeners.set(topic, set);
    }
    set.add(fn);
    const cur = this.latestAt(topic, this.cursor);
    if (cur) fn(cur);
    return () => {
      this.listeners.get(topic)?.delete(fn);
    };
  }

  seek(gameTime: number): void {
    const { from, to } = this.range();
    // 拖到最右端 = 回到实时
    if (gameTime >= to - 1e-9) {
      this.returnToLive();
      return;
    }
    this._mode = "review";
    this.cursor = Math.min(to, Math.max(from, gameTime));
    this.emitAt(this.cursor);
    this.notify();
  }

  returnToLive(): void {
    this._mode = "live";
    this.cursor = this.range().to;
    this.emitAt(this.cursor);
    this.notify();
  }

  /**
   * 可 seek 窗口。`to` = 已观测到的最新帧（回看期间仍在增长 —— 这就是"live 没停"的可见证据）。
   *
   * `from` 的规则值得写清楚，因为直觉版本是错的：
   * - 直觉版"所有 topic 里最老保留帧的最小值"→ 被**永不淘汰的低频 topic**（静态面、只在 t0 发一次的
   *   `frame/session`）永久钉在 t=0：时间线会假装能拖回开局，实际拿到的是窗口内最老的动态帧。
   * - 另一种直觉"最老保留帧的最大值"→ 被**起始较晚的 topic**（如首个 `proposals` 出现在 t=45）
   *   过度收紧：明明能回看 36~45s，窗口却从 45 开始。
   *
   * 正确规则：**只有真的丢过帧的 topic 才约束窗口** ——
   * `from = max(丢过帧的 topic 的最老保留时间)`；没有任何 topic 丢过帧时 = 流的起点。
   * 语义上就是"能忠实重建全部面板的最早时刻"（丢过帧的 topic 在更早处没有覆盖）。
   */
  range(): { from: number; to: number } {
    let to = Number.NEGATIVE_INFINITY;
    let streamStart = Number.POSITIVE_INFINITY;
    let evictedFloor = Number.NEGATIVE_INFINITY;

    for (const [topic, buf] of this.buffers) {
      const first = buf.at(0);
      const last = buf.at(-1);
      if (last) to = Math.max(to, last.game_time);
      if (!first || isStaticTopic(topic)) continue;
      streamStart = Math.min(streamStart, first.game_time);
      if (this.evicted.has(topic)) {
        evictedFloor = Math.max(evictedFloor, first.game_time);
      }
    }

    if (!Number.isFinite(to)) return this.inner.range();
    const from = Number.isFinite(evictedFloor)
      ? evictedFloor
      : Number.isFinite(streamStart)
        ? streamStart
        : to;
    return { from, to: Math.max(from, to) };
  }

  position(): number {
    return this.cursor;
  }

  markers(): TimelineMarker[] {
    return this.inner.markers();
  }

  onChange(cb: () => void): Unsubscribe {
    this.changeCbs.add(cb);
    return () => {
      this.changeCbs.delete(cb);
    };
  }

  dispose(): void {
    for (const u of this.innerUnsubs) u();
    this.innerUnsubs.length = 0;
    this.listeners.clear();
    this.changeCbs.clear();
    this.inner.dispose();
  }

  private emitAt(t: number): void {
    for (const [topic, set] of this.listeners) {
      if (set.size === 0) continue;
      const env = this.latestAt(topic, t);
      if (!env) continue;
      for (const fn of set) fn(env);
    }
  }

  private latestAt(topic: Topic, t: number): AnyEnvelope | null {
    const buf = this.buffers.get(topic);
    if (!buf || buf.length === 0) return null;
    let found: AnyEnvelope | null = null;
    for (const e of buf) {
      if (e.game_time <= t + 1e-9) found = e;
      else break;
    }
    // 游标早于该 topic 的第一帧（例如静态面在 t0 之后才到）→ 给最早的一帧
    return found ?? buf.at(0) ?? null;
  }

  private notify(): void {
    for (const cb of this.changeCbs) cb();
  }
}
