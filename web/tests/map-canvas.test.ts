/**
 * MapCanvas 空白点击守卫（2026-08-25 修）：规划页 world=null 时
 * onBlankClick（放置槽位/点位、取消选中）必须仍能触发 —— 旧守卫
 * `!props.world → return` 把无 world 帧的点击全吞了（悬浮预览绿、点不动、拖拽正常）。
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("MapCanvas pointer-up 守卫（源码级锁）", () => {
  it("守卫只看视口，不再要求 world 帧；nearestUnit 仅在有 world 时调用", () => {
    const src = readFileSync("src/canvas/MapCanvas.tsx", "utf-8");
    expect(src).not.toContain("!vp || !props.world");
    expect(src).toContain("props.world");
    expect(src).toMatch(/props\.world\s*\?\s*nearestUnit/);
  });
});
