/**
 * 聊天滚动跟随（2026-08-23 用户三轮要求）：
 * 1. 在最底部时，内容增长顺着一起滚（follow=true → content 事件滚到底）；
 * 2. 往上拨动（离底超过 NEAR_BOTTOM_PX）→ follow=false，**不再强制滚动**，阅读位置钉住；
 * 3. follow=false 时显示「回到底部」浮钮，点击 → 平滑滚回并恢复跟随。
 *
 * 纯函数：DOM 事件归一成 ScrollEvt 后走同一张转移表 —— 组件只做搬运，表本身用测试锁。
 * 内容增长不触发 scroll 事件（scrollTop 不变、scrollHeight 变），所以 follow 不会被
 * 流式增量误伤；只有真实的滚动落点能改变它。
 */
export const NEAR_BOTTOM_PX = 32;

export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** 离底是否在阈值内（=「在最底部」）。clientHeight≥scrollHeight（内容不满）恒 true。 */
export function isNearBottom(el: ScrollBox): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
}

export type ScrollEvt =
  /** 滚动事件落点（用户拨动或程序性滚动的终点） */
  | { type: "scroll"; nearBottom: boolean }
  /** 内容更新（新消息 / 流式增量）：只有本来就在底才跟着滚 */
  | { type: "content" }
  /** 用户发出新消息：期待看到回应，恢复跟随 */
  | { type: "send" }
  /** 点击「回到底部」浮钮 */
  | { type: "jump" };

export interface ScrollState {
  follow: boolean;
  scrollToBottom: boolean;
}

export function chatScroll(follow: boolean, ev: ScrollEvt): ScrollState {
  switch (ev.type) {
    case "scroll":
      return { follow: ev.nearBottom, scrollToBottom: false };
    case "content":
      // instant 滚（组件侧）：流式高频增量下平滑动画会与用户拨动打架
      return { follow, scrollToBottom: follow };
    case "send":
    case "jump":
      return { follow: true, scrollToBottom: true };
  }
}
