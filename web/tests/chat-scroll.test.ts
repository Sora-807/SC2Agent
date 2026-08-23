/**
 * 聊天滚动跟随的转移表（chat-scroll.ts）—— 三条用户语义的机械锁：
 * 1. 在底部 → 内容增长顺着滚；
 * 2. 往上拨（离底超阈值）→ follow=false，内容增长**不**拽走位置；
 * 3. send / jump → 恢复跟随并滚到底。
 */
import { describe, expect, it } from "vitest";
import { NEAR_BOTTOM_PX, chatScroll, isNearBottom } from "../src/shell/chat-scroll";

describe("isNearBottom", () => {
  it("离底距离在阈值内 = 在底部", () => {
    expect(isNearBottom({ scrollTop: 968, scrollHeight: 1000, clientHeight: 40 })).toBe(true);   // 差 32-8
    expect(isNearBottom({ scrollTop: 1000, scrollHeight: 1000, clientHeight: 40 })).toBe(true); // 0
  });

  it("离底超过阈值 = 不在底部（阈值边界恰好算在）", () => {
    expect(isNearBottom({ scrollTop: 1000 - 40 - NEAR_BOTTOM_PX, scrollHeight: 1000, clientHeight: 40 })).toBe(true);
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 1000, clientHeight: 40 })).toBe(false);
  });

  it("内容不满一屏（无滚动条）恒在底部", () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 100, clientHeight: 400 })).toBe(true);
  });
});

describe("chatScroll 转移表", () => {
  it("scroll：落点决定跟随（到底=恢复，离底=钉住），且不主动滚动", () => {
    expect(chatScroll(true, { type: "scroll", nearBottom: false }))
      .toEqual({ follow: false, scrollToBottom: false });
    expect(chatScroll(false, { type: "scroll", nearBottom: true }))
      .toEqual({ follow: true, scrollToBottom: false });
  });

  it("content：本来在底才跟着滚；往上读时位置钉住", () => {
    expect(chatScroll(true, { type: "content" })).toEqual({ follow: true, scrollToBottom: true });
    expect(chatScroll(false, { type: "content" })).toEqual({ follow: false, scrollToBottom: false });
  });

  it("send / jump：恢复跟随并滚到底", () => {
    expect(chatScroll(false, { type: "send" })).toEqual({ follow: true, scrollToBottom: true });
    expect(chatScroll(false, { type: "jump" })).toEqual({ follow: true, scrollToBottom: true });
    expect(chatScroll(true, { type: "jump" })).toEqual({ follow: true, scrollToBottom: true });
  });
});
