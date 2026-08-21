/**
 * 视口纯函数（F10 验收）
 *
 * 锁死红线 G2：**视口状态只在换图时重置；resize 必须保住用户视角。**
 * 之前 MapCanvas 的 resize 直接 `fitViewport` 重置 —— 改窗口大小、收起对话栏，
 * 甚至侧栏多一行字都会把缩放平移丢掉（根因 D）。这里把"保住"这件事钉成纯函数断言。
 */
import { describe, expect, it } from "vitest";
import {
  fitViewport, resizeViewport, screenToWorld, viewportCenter, worldToScreen, zoomAt,
} from "../src/canvas/view";

/** LadderMap 尺寸 + 一块典型画布 */
const MW = 176;
const MH = 160;
const fit = () => fitViewport(900, 600, MW, MH);

describe("fitViewport", () => {
  it("整张地图装得进画布，且居中", () => {
    const v = fit();
    expect(v.scale).toBeCloseTo(Math.min(900 / MW, 600 / MH), 10);
    // 地图四角都落在画布内
    const [x0, y0] = worldToScreen(v, 0, MH);
    const [x1, y1] = worldToScreen(v, MW, 0);
    expect(x0).toBeGreaterThanOrEqual(-1e-9);
    expect(y0).toBeGreaterThanOrEqual(-1e-9);
    expect(x1).toBeLessThanOrEqual(900 + 1e-9);
    expect(y1).toBeLessThanOrEqual(600 + 1e-9);
    // 地图中心 = 画布中心
    const [cx, cy] = worldToScreen(v, MW / 2, MH / 2);
    expect(cx).toBeCloseTo(450, 10);
    expect(cy).toBeCloseTo(300, 10);
  });

  it("世界坐标是左下原点：y 越大屏幕越靠上", () => {
    const v = fit();
    const [, yLow] = worldToScreen(v, 10, 10);
    const [, yHigh] = worldToScreen(v, 10, 100);
    expect(yHigh).toBeLessThan(yLow);
  });
});

describe("screenToWorld / worldToScreen 互逆", () => {
  it("往返回到原点", () => {
    const v = zoomAt(fit(), 300, 200, 3.1);
    for (const [x, y] of [[0, 0], [88, 80], [175.5, 159.5]] as const) {
      const [sx, sy] = worldToScreen(v, x, y);
      const [bx, by] = screenToWorld(v, sx, sy);
      expect(bx).toBeCloseTo(x, 9);
      expect(by).toBeCloseTo(y, 9);
    }
  });
});

describe("zoomAt", () => {
  it("锚定在光标下的世界点（缩放不跑焦）", () => {
    const v = fit();
    const [wx, wy] = screenToWorld(v, 712, 133);
    const z = zoomAt(v, 712, 133, 1.15);
    const [sx, sy] = worldToScreen(z, wx, wy);
    expect(sx).toBeCloseTo(712, 8);
    expect(sy).toBeCloseTo(133, 8);
  });

  it("scale 被夹在 [0.4, 24]", () => {
    let v = fit();
    for (let i = 0; i < 80; i += 1) v = zoomAt(v, 450, 300, 1.15);
    expect(v.scale).toBeCloseTo(24, 10);
    for (let i = 0; i < 200; i += 1) v = zoomAt(v, 450, 300, 1 / 1.15);
    expect(v.scale).toBeCloseTo(0.4, 10);
  });
});

describe("resizeViewport（红线 G2）", () => {
  it("scale 不变", () => {
    const v = zoomAt(fit(), 400, 300, 1.15 ** 6);
    const r = resizeViewport(v, 640, 900);
    expect(r.scale).toBe(v.scale);
    expect(r.cw).toBe(640);
    expect(r.ch).toBe(900);
  });

  it("屏幕中心对应的世界点不变", () => {
    const v = zoomAt(fit(), 700, 120, 1.15 ** 9);
    const before = viewportCenter(v);
    const after = viewportCenter(resizeViewport(v, 480, 1000));
    expect(after[0]).toBeCloseTo(before[0], 9);
    expect(after[1]).toBeCloseTo(before[1], 9);
  });

  it("尺寸没变就是恒等（除对象身份）", () => {
    const v = zoomAt(fit(), 100, 100, 2);
    const r = resizeViewport(v, v.cw, v.ch);
    expect(r).toEqual(v);
  });

  it("连续 resize 不漂移（收侧栏再放回来，视角回到原处）", () => {
    const v = zoomAt(fit(), 300, 400, 1.15 ** 5);
    const shrunk = resizeViewport(v, 620, 600);   // 展开对话栏
    const back = resizeViewport(shrunk, 900, 600); // 收起对话栏
    expect(back.scale).toBe(v.scale);
    expect(back.panX).toBeCloseTo(v.panX, 8);
    expect(back.panY).toBeCloseTo(v.panY, 8);
  });

  it("fit → zoom → resize 全链：世界中心与 scale 双保", () => {
    const a = fit();
    const b = zoomAt(a, 810, 90, 1.15 ** 12);
    const centerBefore = viewportCenter(b);
    const c = resizeViewport(b, 1200, 400);
    expect(c.scale).toBe(b.scale);
    const centerAfter = viewportCenter(c);
    expect(centerAfter[0]).toBeCloseTo(centerBefore[0], 9);
    expect(centerAfter[1]).toBeCloseTo(centerBefore[1], 9);
  });

  it("resize 不是 fit —— 放大后 resize 绝不回到 fit 的 scale", () => {
    const zoomed = zoomAt(fit(), 450, 300, 1.15 ** 10);
    const resized = resizeViewport(zoomed, 900, 600);
    const refit = fitViewport(900, 600, MW, MH);
    expect(resized.scale).not.toBeCloseTo(refit.scale, 3);
  });
});
