/**
 * JsonlFrameSource —— 从 JSONL 文本回放帧（夹具 / 复盘共用同一个实现）。
 *
 * 语义：游标 = 游戏时间。每个 topic 只回放"<= 游标的最后一帧"，
 * 因为所有 topic 的 payload 都是**该时刻的完整快照** —— 这正是 seek 能免费工作的原因，
 * 也是"复盘照抄 live"能反过来变成"live 照抄复盘"（决策 U2）的技术前提。
 */
import {
  isStaticTopic,
  parseEnvelopeLine,
  type AnyEnvelope,
  type EnvelopeFor,
  type Topic,
} from "../contract";
import type {
  FrameSource,
  FrameSourceCaps,
  TimelineMarker,
  Unsubscribe,
} from "./types";

type Listener = (env: AnyEnvelope) => void;

export class JsonlFrameSource implements FrameSource {
  readonly caps: FrameSourceCaps = { live: false, seek: true };

  private readonly byTopic = new Map<Topic, AnyEnvelope[]>();
  private readonly listeners = new Map<Topic, Set<Listener>>();
  private readonly _markers: TimelineMarker[];
  private cursor = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(envelopes: AnyEnvelope[]) {
    for (const e of envelopes) {
      const list = this.byTopic.get(e.topic);
      if (list) list.push(e);
      else this.byTopic.set(e.topic, [e]);
    }
    for (const list of this.byTopic.values()) {
      list.sort((a, b) => a.game_time - b.game_time || a.seq - b.seq);
    }
    this._markers = extractMarkers(envelopes);
    this.cursor = this.range().from;
  }

  /** 从 JSONL 文本构造；每行都过契约校验（非法行直接抛，不静默跳过） */
  static fromJsonl(text: string): JsonlFrameSource {
    return new JsonlFrameSource(parseJsonl(text));
  }

  topics(): Topic[] {
    return [...this.byTopic.keys()];
  }

  /** 可 seek 窗口：只由**动态**帧决定（静态面任何游标下都有效，见契约 STATIC_TOPICS） */
  range(): { from: number; to: number } {
    let from = Number.POSITIVE_INFINITY;
    let to = Number.NEGATIVE_INFINITY;
    for (const [topic, list] of this.byTopic) {
      if (isStaticTopic(topic)) continue;
      const first = list.at(0);
      const last = list.at(-1);
      if (first) from = Math.min(from, first.game_time);
      if (last) to = Math.max(to, last.game_time);
    }
    if (!Number.isFinite(from)) return { from: 0, to: 0 };
    return { from, to };
  }

  position(): number {
    return this.cursor;
  }

  markers(): TimelineMarker[] {
    return this._markers;
  }

  subscribe<K extends Topic>(
    topic: K,
    cb: (env: EnvelopeFor<K>) => void,
  ): Unsubscribe {
    const fn = cb as unknown as Listener;
    let set = this.listeners.get(topic);
    if (!set) {
      set = new Set();
      this.listeners.set(topic, set);
    }
    set.add(fn);
    const latest = this.frameAt(topic, this.cursor);
    if (latest) fn(latest);
    return () => {
      this.listeners.get(topic)?.delete(fn);
    };
  }

  seek(gameTime: number): void {
    const { from, to } = this.range();
    this.cursor = Math.min(to, Math.max(from, gameTime));
    for (const [topic, set] of this.listeners) {
      if (set.size === 0) continue;
      const latest = this.frameAt(topic, this.cursor);
      if (!latest) continue;
      for (const fn of set) fn(latest);
    }
  }

  /** 静态面即使晚于游标也要给（每局只发一次，任何游标下都有效） */
  private frameAt(topic: Topic, t: number): AnyEnvelope | null {
    const list = this.byTopic.get(topic);
    const found = latestAt(list, t);
    if (found) return found;
    return isStaticTopic(topic) ? (list?.at(0) ?? null) : null;
  }

  /** 夹具/复盘专属：按倍速自动前进（不进 FrameSource 接口，组件不依赖它） */
  play(rate = 1, tickMs = 250): void {
    this.pause();
    this.timer = setInterval(() => {
      const { to } = this.range();
      if (this.cursor >= to) {
        this.pause();
        return;
      }
      this.seek(this.cursor + (rate * tickMs) / 1000);
    }, tickMs);
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

/* ---------------- 共用工具 ---------------- */

export function parseJsonl(text: string): AnyEnvelope[] {
  const envs: AnyEnvelope[] = [];
  let lineNo = 0;
  for (const raw of text.split("\n")) {
    lineNo += 1;
    const line = raw.trim();
    if (line === "") continue;
    try {
      envs.push(parseEnvelopeLine(line));
    } catch (err) {
      throw new Error(`第 ${lineNo} 行不合契约：${(err as Error).message}`);
    }
  }
  return envs;
}

export function latestAt(
  list: AnyEnvelope[] | undefined,
  t: number,
): AnyEnvelope | null {
  if (!list || list.length === 0) return null;
  let found: AnyEnvelope | null = null;
  for (const e of list) {
    if (e.game_time <= t + 1e-9) found = e;
    else break;
  }
  return found;
}

/**
 * 从帧流里抽时间线标记。
 * **只读帧字段**：警报文案取 `text_zh`、转移原因取 `reason` —— 前端不编文案（红线 C3/C4）。
 */
export function extractMarkers(envelopes: AnyEnvelope[]): TimelineMarker[] {
  const out: TimelineMarker[] = [];
  const seenAlert = new Set<string>();
  const seenTrans = new Set<string>();

  for (const env of envelopes) {
    if (env.topic === "frame/alerts") {
      for (const a of env.payload.alerts) {
        // 按 id + severity 去重：同一警报从 warn 升级到 error 是**新事件**（后端超过
        // STALL_WARN_SECS 才升级），只按 id 去重会把升级这件事吞掉。
        const key = a.id + "|" + a.severity;
        if (seenAlert.has(key)) continue;
        seenAlert.add(key);
        out.push({ t: a.at, kind: "alert", text: a.text_zh, severity: a.severity });
      }
    } else if (env.topic === "frame/flow") {
      for (const s of env.payload.strategies) {
        for (const tr of s.transitions) {
          const key = `${s.instance_id}|${tr.from}->${tr.to}|${tr.at}`;
          if (seenTrans.has(key)) continue;
          seenTrans.add(key);
          out.push({
            t: tr.at,
            kind: "transition",
            text: `${tr.from} → ${tr.to}${tr.reason ? "（" + tr.reason + "）" : ""}`,
          });
        }
      }
    } else if (env.topic === "proposals") {
      for (const p of env.payload.proposals) {
        out.push({ t: p.created_at, kind: "proposal", text: `提案：${p.title_zh}` });
      }
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}
