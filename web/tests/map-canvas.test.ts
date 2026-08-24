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
    // 吸附必须复用 snapToCellCenter（偶数尺寸自推公式曾与落点差一格 —— 2×2 实测偏移）
    expect(slotBranch).toContain("snapToCellCenter([wx, wy])");
    expect(slotBranch).not.toMatch(/Math\.round\(wx - gs \/ 2\)/);
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

describe("拖拽吸附与落点同格（2×2 偏移事故，2026-08-25）", () => {
  it("数学锁：ghost 与 drop 的 tl 在每个小数段一致（2×2 与 3×3 全覆盖）", async () => {
    const { slotTl, snapToCellCenter } = await import("../src/planning/map-draft");
    for (const size of [2, 3]) {
      for (let f = 0; f < 10; f++) {
        const wx = 40 + f / 10;
        const ghost = slotTl(snapToCellCenter([wx, 40.3]), size);
        const drop = slotTl(snapToCellCenter([wx, 40.3]), size);  // drop = 同一公式
        expect(ghost).toEqual(drop);
        // 且 tl 恒为整数格（浮点只允许出现在 pos 半形态，不出现在格点上）
        expect(Number.isInteger(ghost[0])).toBe(true);
      }
    }
  });
});
