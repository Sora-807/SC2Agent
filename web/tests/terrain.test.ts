/**
 * F11b：地形量化纯函数 —— 台地 level / 悬崖边缘 / 斜坡掩码（U15：显示层，不上移后端）。
 *
 * 量化与边缘检测只在显示层成立；一旦斜坡要被命名/被谓词引用就必须上移后端
 * （PLAN §12 结论 3），届时不只改这里的测试，还要动契约。
 */
import { describe, expect, it } from "vitest";
import { cliffMask, quantizeLevels, rampMask, type DecodedGrid } from "../src/canvas/grid";

/** 直接构造 DecodedGrid（行主序 data[y][x]，与解码产物同形） */
function grid(w: number, h: number, value: (x: number, y: number) => number): DecodedGrid {
  const data = new Uint8Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) data[y * w + x] = value(x, y);
  }
  return {
    w, h, data,
    at(x, y) {
      if (x < 0 || y < 0 || x >= w || y >= h) return 0;
      return data[y * w + x] ?? 0;
    },
  };
}

describe("quantizeLevels", () => {
  it("3 级台地恰好量化成 3 个 level，且有序（低高度 → 低 level）", () => {
    // 左 1/3 高度 8，中 1/3 高度 16，右 1/3 高度 24
    const g = grid(30, 10, (x) => (x < 10 ? 8 : x < 20 ? 16 : 24));
    const { levels, count } = quantizeLevels(g);
    expect(count).toBe(3);
    expect(levels[0]).toBe(0);        // 高度 8 → level 0（最低台地最暗）
    expect(levels[15]).toBe(1);       // 高度 16 → level 1
    expect(levels[25]).toBe(2);       // 高度 24 → level 2
  });

  it("扫描毛刺（低频值）并入最近的幸存 level，不单独成级", () => {
    // 网格要够大：noiseFloor = max(1, ⌊total×0.001⌋)，3000 格 → 3，单次毛刺活不下来
    const g = grid(60, 50, (x) => (x < 30 ? 8 : 16));
    // 埋一个只出现一次的毛刺值 17（并入 16 那一级）
    g.data[5 * 60 + 35] = 17;
    const { levels, count } = quantizeLevels(g);
    expect(count).toBe(2);
    expect(levels[5 * 60 + 35]).toBe(1);  // 毛刺并入 level 1（16 的级），不是新 level
  });

  it("平坦地图只有 1 个 level（不崩、不分级）", () => {
    const g = grid(10, 10, () => 12);
    const { levels, count } = quantizeLevels(g);
    expect(count).toBe(1);
    expect(levels.every((v) => v === 0)).toBe(true);
  });
});

describe("cliffMask", () => {
  it("人工阶梯：边缘格为真、台地内部为假", () => {
    // 左半 level 0，右半 level 1 → 悬崖在中缝（x=9 与 x=10 两列）
    const g = grid(20, 6, (x) => (x < 10 ? 0 : 8));   // 真实台地落差 ≥4（SC2 常见 8）
    const { levels } = quantizeLevels(g);
    const mask = cliffMask(levels, 20, 6);
    expect(mask[3 * 20 + 9]).toBe(1);    // 中缝左格
    expect(mask[3 * 20 + 10]).toBe(1);   // 中缝右格
    expect(mask[3 * 20 + 2]).toBe(0);    // 左台地内部
    expect(mask[3 * 20 + 17]).toBe(0);   // 右台地内部
  });

  it("缓变噪声（1-2 单位高度差）并成同一层 —— 不再满图假斜坡/假悬崖", () => {
    // 真机 (62,80) 附近实测形态：189/190/191 交替的近平地。旧量化把每个值当一层，
    // 满图都是"跨层"→ 斜坡暖色抖动刷屏（用户看到的黄色格点）。
    const g = grid(20, 6, (x) => 189 + (x % 3));
    const { levels, count } = quantizeLevels(g);
    expect(count).toBe(1);
    expect(levels.every((v) => v === 0)).toBe(true);
  });

  it("上下邻居跨 level 也算边缘（悬崖不只在左右方向）", () => {
    const g = grid(6, 6, (_x, y) => (y < 3 ? 0 : 8));
    const { levels } = quantizeLevels(g);
    const mask = cliffMask(levels, 6, 6);
    expect(mask[2 * 6 + 3]).toBe(1);     // y=2（level 0 一侧的最底行）
    expect(mask[3 * 6 + 3]).toBe(1);     // y=3
    expect(mask[0 * 6 + 3]).toBe(0);     // 远离边缘
  });
});

describe("rampMask", () => {
  it("斜坡掩码只在「跨 level 的可走格」为真；不可走的边缘格不算斜坡", () => {
    const g = grid(20, 6, (x) => (x < 10 ? 0 : 8));   // 真实台地落差 ≥4（SC2 常见 8）
    const { levels } = quantizeLevels(g);
    // 可走掩码：只有中缝右格（x=10, y=3）可走，其余全部不可走
    const pathable = grid(20, 6, () => 0);
    pathable.data[3 * 20 + 10] = 1;
    const ramps = rampMask(levels, pathable, 20, 6);
    expect(ramps[3 * 20 + 10]).toBe(1);  // 跨 level 且可走 → 斜坡
    expect(ramps[3 * 20 + 9]).toBe(0);   // 跨 level 但不可走 → 不是斜坡
    expect(ramps[3 * 20 + 17]).toBe(0);  // 可走不判定（不跨 level）→ 不是斜坡
  });

  it("pathable=null（帧里缺可走图）时斜坡掩码为空 —— 不伪造", () => {
    const g = grid(20, 6, (x) => (x < 10 ? 0 : 8));   // 真实台地落差 ≥4（SC2 常见 8）
    const { levels } = quantizeLevels(g);
    const ramps = rampMask(levels, null, 20, 6);
    expect(ramps.every((v) => v === 0)).toBe(true);
  });
});
