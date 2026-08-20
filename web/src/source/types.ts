/**
 * 帧源抽象（plan-frontend.md §4）
 *
 * 组件**只允许**经 store 读帧，不得 import 任何实现 —— 这是"组件是 ViewFrame→像素 的纯函数"
 * （决策 U1）可执行化的地方，也是复盘/live 同一套组件的前提。
 * 构造实现的职责在 `store/frames.ts`，不在页面里。
 */
import type { EnvelopeFor, Topic } from "../contract";

export interface FrameSourceCaps {
  /** 是否是活的对局（false = 复盘/夹具） */
  live: boolean;
  /** 是否支持按游戏时间跳转 */
  seek: boolean;
}

export type Unsubscribe = () => void;

/** 时间线刻度标记（ADR-0023 §3：快照点、警报、策略转移可见） */
export interface TimelineMarker {
  t: number;
  kind: "alert" | "transition" | "proposal" | "snapshot";
  text: string;
  severity?: "info" | "warn" | "error";
}

export interface FrameSource {
  readonly caps: FrameSourceCaps;
  /** 订阅一个 topic；订阅时立即回放"当前游标下的最新一帧"（若有） */
  subscribe<K extends Topic>(
    topic: K,
    cb: (env: EnvelopeFor<K>) => void,
  ): Unsubscribe;
  /** 跳到某游戏时间；caps.seek=false 时抛错 */
  seek(gameTime: number): void;
  /** 可跳转范围（游戏秒） */
  range(): { from: number; to: number };
  /** 当前游标 */
  position(): number;
  /**
   * 时间线标记。可 seek 的源（复盘/夹具）能一次性给全量；
   * live 源只能给"已观测到的"，随帧增量长出来。
   */
  markers(): TimelineMarker[];
  /** 释放资源（定时器、连接） */
  dispose(): void;
}

/** live 源被拖回历史后进入只读回看；ADR-0023 §3 要求 live 继续在后台采集 */
export interface ReviewableFrameSource extends FrameSource {
  /** "live" = 跟随最新帧；"review" = 停在历史某点，live 仍在后台累积 */
  mode(): "live" | "review";
  /** 回到实时 */
  returnToLive(): void;
  /** 订阅 mode/range 变化（时间线要重画） */
  onChange(cb: () => void): Unsubscribe;
}
