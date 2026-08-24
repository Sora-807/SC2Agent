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

describe("MapCanvas 槽位拖动 ghost（2026-08-25 修）", () => {
  it("拖动路径 bump 重绘 + 吸附 + validateSlotDrop 着色（源码级锁）", () => {
    const src = readFileSync("src/canvas/MapCanvas.tsx", "utf-8");
    // 跟随：slot 分支必须 bump dirty（ref 直改否则冻在起点 —— 用户两报同根因）
    const slotBranch = src.slice(src.indexOf('if (d.mode === "slot")'), src.indexOf("槽位拖动不吃 pan"));
    expect(slotBranch).toContain("dirty.current += 1");
    expect(slotBranch).toMatch(/Math\.round\(wx - gs \/ 2\)/);
    // 效果对齐放置预览：绿/红着色走 validateSlotDrop（判定留页侧单点）
    expect(src).toContain("validateSlotDrop?:");
    expect(src).toMatch(/props\.validateSlotDrop\s*\? props\.validateSlotDrop\(ghost\.pos/);
    expect(src).toContain('"#34d399" : "#f87171"');
    // 原位置虚线空心
    expect(src).toContain("ghostSlotName === s.name");
  });

  it("PlanningPage 传校验回调且排除被拖槽自身", () => {
    const src = readFileSync("src/pages/PlanningPage.tsx", "utf-8");
    expect(src).toContain("validateSlotDrop={");
    expect(src).toContain('x.name !== name');
  });
});
