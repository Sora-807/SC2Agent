/**
 * 投影板时间轴缩放（F15）—— 纯函数，不需要 DOM。
 *
 * 锁死的交互语义（用户拍板）：
 * - 宽度钳制（二十七轮起上限 300s = 默认窗口 5 分钟），拖顶层时间轴/新帧不许改变宽度；
 * - 滚轮围绕光标缩放（anchorT 停在屏幕上的 frac 位置不动）；
 * - 中心跟随 = centerRange(center, span)，center 变化只移中心。
 */
import { describe, expect, it } from "vitest";
import {
  ZOOM_SPAN_MAX, ZOOM_SPAN_MIN, anchorRange, centerRange, zoomSpan,
} from "../src/charts/gantt-data";

describe("zoomSpan", () => {
  it("正常放大/缩小", () => {
    expect(zoomSpan(120, 1.25)).toBe(150);
    expect(zoomSpan(120, 0.8)).toBe(96);
  });

  it("夹在 [20, 300]（二十七轮拍板：默认窗口 5 分钟，10 分钟拖得还是快）", () => {
    expect(zoomSpan(25, 0.1)).toBe(ZOOM_SPAN_MIN);
    expect(zoomSpan(500, 10)).toBe(ZOOM_SPAN_MAX);
    expect(ZOOM_SPAN_MIN).toBe(20);
    expect(ZOOM_SPAN_MAX).toBe(300);
  });
});

describe("centerRange", () => {
  it("center 居中、宽 span", () => {
    expect(centerRange(100, 120)).toEqual({ from: 40, to: 160 });
  });

  it("center 跨 0 也如实（游戏时间轴可以是 0 附近）", () => {
    expect(centerRange(10, 60)).toEqual({ from: -20, to: 40 });
  });
});

describe("anchorRange", () => {
  it("围绕光标缩放：anchorT 停在屏幕上的 frac 位置不动", () => {
    // 光标在正中（frac=0.5）：锚点应保持居中
    const r = anchorRange(100, 0.5, 200);
    expect(r).toEqual({ from: 0, to: 200 });
    // 光标在左缘（frac=0）：锚点贴左缘
    expect(anchorRange(100, 0, 200)).toEqual({ from: 100, to: 300 });
    // 光标在 1/4 处：锚点左侧占视窗 1/4
    expect(anchorRange(100, 0.25, 200)).toEqual({ from: 50, to: 250 });
  });
});
