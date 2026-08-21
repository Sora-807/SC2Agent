/**
 * 布局与输入不变量（F10 验收）—— 源码扫描式回归锁
 *
 * 为什么扫描源码而不是渲染组件：本轮要锁的三件事（外壳固定一屏、页面自己接管滚动、
 * 滚轮走 native 非 passive 监听）都是**结构约定**，不是渲染输出。渲染 App 需要引入
 * jsdom + testing-library 并 mock 掉帧源与 fetch，为几个 class 名付这套代价不值；
 * 而扫描能覆盖**全部页面**（渲染只能覆盖被挂载的那一个）。
 * 仓库已有同类先例（contract.test.ts 扫夹具做红线体检）；F13 的 rail.test.tsx 需要真渲染时再引 DOM 栈。
 *
 * 断言一律走 code()（剥掉注释）—— 注释里正好会解释"为什么不用 min-h-screen"。
 *
 * 锁死：红线 G1（外壳固定一屏）、G2（视口不因 resize 重置）、G3（native wheel）。
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { allSources, code, rel, stripComments } from "./source-scan";

describe("扫描工具自身", () => {
  it("剥注释但不动字符串", () => {
    const src = [
      'const a = "http://x//y";',
      "// 这里提到 min-h-screen",
      "/* 块注释里也提到 onWheel */",
      "const b = " + "`" + "模板 // 不是注释" + "`" + ";",
      "const c = 1; // 尾注释",
    ].join("\n");
    const out = stripComments(src);
    expect(out).toContain('"http://x//y"');
    expect(out).toContain("模板 // 不是注释");
    expect(out).not.toContain("min-h-screen");
    expect(out).not.toContain("onWheel");
    expect(out).toContain("const c = 1;");
  });
});

describe("外壳固定一屏（G1 / 根因 B）", () => {
  const app = code("App.tsx");

  it("根节点用 h-[100dvh] 而不是 min-h-screen", () => {
    expect(app).toContain("h-[100dvh]");
    expect(app).not.toContain("min-h-screen");
  });

  it("根节点 overflow-hidden", () => {
    const root = app.match(/<div className="flex h-\[100dvh\][^"]*"/)?.[0] ?? "";
    expect(root).toContain("overflow-hidden");
  });

  it("main 不再是 overflow-auto（滚动权下放给页面）", () => {
    const main = app.match(/<main className="[^"]*"/)?.[0] ?? "";
    expect(main).not.toBe("");
    expect(main).not.toContain("overflow-auto");
    expect(main).toContain("overflow-hidden");
    expect(main).toContain("min-h-0");
  });

  it("常驻页脚已移除，改为状态浮层", () => {
    expect(app).not.toContain("<footer");
    expect(app).toContain("StatusChip");
  });

  it("状态浮层读契约真值，不硬编码 rev", () => {
    const chip = code("shell/StatusChip.tsx");
    expect(chip).toContain("REV");
    expect(chip).not.toMatch(/rev\s*=\s*1\b/);
  });

  it("全仓库没有任何地方再引入 min-h-screen", () => {
    const bad = allSources()
      .filter((p) => stripComments(readFileSync(p, "utf8")).includes("min-h-screen"))
      .map(rel);
    expect(bad).toEqual([]);
  });
});

describe("页面自己接管滚动（G1）", () => {
  const docPages = [
    "pages/Overview.tsx", "pages/ProductionPage.tsx", "pages/FlowPage.tsx",
    "pages/PlanningPage.tsx", "pages/DebugPage.tsx", "panels/ProposalHost.tsx",
  ];

  it("PAGE_SCROLL 常量存在且自带高度与滚动", () => {
    const m = code("shell/ui.tsx").match(/export const PAGE_SCROLL = "([^"]+)"/);
    expect(m).not.toBeNull();
    const cls = m![1]!;
    expect(cls).toContain("h-full");
    expect(cls).toContain("min-h-0");
    expect(cls).toContain("overflow-y-auto");
  });

  for (const path of docPages) {
    it(path + " 根节点套了 PAGE_SCROLL", () => {
      const src = code(path);
      expect(src).toMatch(/return \(\s*\n\s*<div className=\{PAGE_SCROLL/);
    });
  }

  it("地图页是分栏页：画布 pane 不滚，侧栏自己滚", () => {
    const src = code("pages/MapPage.tsx");
    expect(src).toContain("flex h-full min-h-0");
    expect(src).not.toContain("min-h-[420px]");
    expect(src).toContain("overflow-y-auto");
  });
});

describe("滚轮走 native 非 passive 监听（G3 / 根因 A）", () => {
  const canvas = code("canvas/MapCanvas.tsx");

  it("全仓库不出现 React 的 onWheel / onTouchStart / onTouchMove 属性", () => {
    // React 18 把这三个事件挂在 root container 上且**强制 passive**，
    // 写在它们里面的 preventDefault 是空操作（facebook/react#22794）。
    // 禁的是 JSX 属性，不是标识符 —— 局部 handler 叫 handleWheel 是正常的。
    const offenders = allSources()
      .filter((p) => /\bon(Wheel|TouchStart|TouchMove)\s*=/.test(stripComments(readFileSync(p, "utf8"))))
      .map(rel);
    expect(offenders).toEqual([]);
    expect(canvas).not.toMatch(/onWheel\s*=/);
  });

  it("用 addEventListener 且显式 passive:false，并成对摘除", () => {
    expect(canvas).toContain('addEventListener("wheel"');
    expect(canvas).toContain("passive: false");
    expect(canvas).toContain('removeEventListener("wheel"');
  });

  it("preventDefault 仍然在（只是搬进了 native 监听）", () => {
    expect(canvas).toContain("e.preventDefault()");
  });
});

describe("绘制循环不随 props 重建（根因 C）", () => {
  const canvas = code("canvas/MapCanvas.tsx");

  it("rAF effect 的依赖不含 props", () => {
    expect(canvas).not.toMatch(/\}, \[vp, props/);
    expect(canvas).toMatch(/\}, \[vp\]\);/);
  });

  it("最新 props 与烤好的位图经 ref 读取", () => {
    expect(canvas).toContain("live.current");
  });
});

describe("视口不因 resize 重置（G2 / 根因 D）", () => {
  const canvas = code("canvas/MapCanvas.tsx");

  it("resize 走 resizeViewport，fit 必须有条件", () => {
    expect(canvas).toContain("resizeViewport");
    expect(canvas).toContain("needFit");
  });

  it("换图判定带地图身份（换图才 fit）", () => {
    expect(canvas).toContain("mapKey");
  });
});
