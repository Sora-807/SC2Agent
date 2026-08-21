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

describe("地图渲染面的回归锁（2026-08-21 审查发现）", () => {
  const canvas = code("canvas/MapCanvas.tsx");

  it("pos_marks 真的被画了（帧里有、画布不画 = 看不见标记）", () => {
    expect(canvas).toContain("map.pos_marks");
    // 图层清单里要有开关，否则关不掉也说不出为什么不可用
    const layers = code("canvas/layers.ts");
    expect(layers).toContain('"marks"');
    expect(layers).toContain("pos_marks");
  });

  it("标记用菱形（U16：不与建筑矩形 / 单位 chip 撞形）", () => {
    expect(canvas).toContain("SHAPE.mark.diamondHalf");
  });

  it("标记色是专门登记的第三类语义，不偷用状态色（G4）", () => {
    const theme = code("canvas/theme.ts");
    expect(theme).toMatch(/mark:\s*"#/);
  });

  it("pan 手势有位移容差，拖图松手不会误触选中", () => {
    expect(canvas).toContain("CLICK_SLOP");
    expect(canvas).toContain("travel");
  });

  it("pointermove 用函数式 setVp（批处理下读旧闭包会丢增量）", () => {
    expect(canvas).not.toMatch(/setVp\(\{ \.\.\.vp,/);
    expect(canvas).toMatch(/setVp\(\(old\) =>/);
  });

  it("命令连线画在 chip/个体两个分支之外（低缩放档也要生效）", () => {
    // orders 块必须在 layersOn(props,"units") 的 if 之外
    const unitsIdx = canvas.indexOf('layersOn(props, "units")');
    const ordersIdx = canvas.indexOf('layersOn(props, "orders")');
    expect(unitsIdx).toBeGreaterThan(-1);
    expect(ordersIdx).toBeGreaterThan(unitsIdx);
    // chip 分支内不得再出现 orders（否则又只在个体档生效）
    const chipBlock = canvas.slice(unitsIdx, ordersIdx);
    expect(chipBlock).not.toContain('layersOn(props, "orders")');
  });

  it("chip 的组标签受「flow 分组」开关门控", () => {
    expect(canvas).toContain("showGroup");
    expect(canvas).toMatch(/showGroup && c\.group_id/);
  });
});

describe("写入面门禁不再绑在回放源（2026-08-21 审查发现）", () => {
  it("ProductionPage 不自算门禁，走 shell/mode 的 writeGate", () => {
    const page = code("pages/ProductionPage.tsx");
    expect(page).toContain("writeGate");
    // 回归：曾是 sourceKind === "api"（回放源）→ live 下写入控件全消失
    expect(page).not.toMatch(/sourceKind === "api"/);
  });

  it("只读横幅说真话（不再写死「本地夹具」）", () => {
    const page = code("pages/ProductionPage.tsx");
    expect(page).toContain("gateReason");
    expect(page).not.toContain("当前帧源是本地夹具");
  });
});

describe("P2 接受按钮禁用但可见（2026-08-21 审查发现）", () => {
  const review = code("panels/ProposalReview.tsx");

  it("接受按钮始终渲染，靠 disabled 关掉而不是整块隐藏", () => {
    expect(review).not.toMatch(/\{canAct && \(/);
    expect(review).toMatch(/disabled=\{busy \|\| picked\.size === 0 \|\| !canAct\}/);
  });

  it("校验未过/已失效时拒绝仍可用（P3 的理由回流通道不能一起藏）", () => {
    expect(review).toContain("settled");
    expect(review).toMatch(/!canReject\(reason\) \|\| settled/);
  });
});

describe("Flow 图的两处交互修复（2026-08-21 审查发现）", () => {
  it("PanZoom 的 fit 触发键是**图身份**，不是内容包围盒", () => {
    const pz = code("graph/PanZoom.tsx");
    expect(pz).toContain("fitKey");
    // fit effect 的依赖必须**恰好**是 [fitKey, size.w, size.h]：contentW/contentH 会被节点
    // 拖动改，一旦进依赖就是"每拖一下 fit 一次"，拖拽与视口打架。
    // （不能写成"全文不得出现这些依赖"——「适应窗口」按钮的 doFit 本该用当前内容尺寸，
    //   那条负向断言会误伤合法代码，第一版就踩了。）
    expect(pz).toMatch(/\}, \[fitKey, size\.w, size\.h\]\);/);
    const page = code("pages/FlowPage.tsx");
    expect(page).toMatch(/fitKey=\{graph\.id/);
  });

  it("节点位置落盘吃传入的 map，不读渲染闭包（否则丢最后一段位移）", () => {
    const page = code("pages/FlowPage.tsx");
    expect(page).toMatch(/const persist = \(next: Map/);
    expect(page).toContain("dr.last");
    expect(page).not.toMatch(/else persist\(\);/);
  });

  it("终局分支有独立视觉，不与转场出口圆点撞形", () => {
    const page = code("pages/FlowPage.tsx");
    expect(page).toContain("outcomeOf");
    expect(page).toMatch(/oc\.tone === "end"/);
    expect(page).not.toMatch(/const hasEdge = target !== "留在本步"/);
  });
});
