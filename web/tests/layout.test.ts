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

  it("常驻页脚与 rev 徽章都已移除（2026-08-22 用户拍板：rev 提示没用）", () => {
    expect(app).not.toContain("<footer");
    expect(app).not.toContain("StatusChip");
  });

  it("全仓库没有任何地方再引入 min-h-screen", () => {
    const bad = allSources()
      .filter((p) => stripComments(readFileSync(p, "utf8")).includes("min-h-screen"))
      .map(rel);
    expect(bad).toEqual([]);
  });
});

describe("页面自己接管滚动（G1）", () => {
  // 2026-08-22 起生产页升级为「三区固定 + 区内滚」（用户拍板），从 PAGE_SCROLL 名单移出
  const docPages = [
    "pages/Overview.tsx", "pages/FlowPage.tsx",
    "pages/DebugPage.tsx", "panels/ProposalHost.tsx",
  ];

  it("PAGE_SCROLL 常量存在且自带高度与滚动", () => {
    const m = code("shell/ui.tsx").match(/export const PAGE_SCROLL = "([^"]+)"/);
    expect(m).not.toBeNull();
    const cls = m![1]!;
    expect(cls).toContain("h-full");
    expect(cls).toContain("min-h-0");
    expect(cls).toContain("overflow-y-auto");
  });

  it("生产页：三区固定占据视口，页面根不滚、各区内部滚（2026-08-22 用户拍板）", () => {
    const src = code("pages/ProductionPage.tsx");
    expect(src).toMatch(/flex h-full flex-col gap-2 overflow-hidden/);
    expect(src).not.toContain("PAGE_SCROLL");
    expect(src).toContain("overflow-y-auto");          // 队列/经济/投影区内滚
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

  it("规划页（F16/P1/左抽屉轮）：根是分栏页不滚；生产/Flow tab 内部自己接管滚动", () => {
    const src = code("pages/PlanningPage.tsx");
    expect(src).toMatch(/return \(\s*\n\s*<div className="flex h-full min-h-0 flex-col gap-2">/);
    expect(src).toContain("PAGE_SCROLL");          // 生产/Flow tab 的文档页包裹
    expect(src).not.toContain("min-h-[420px]");   // 旧固定高画布已删（跟着容器高度走）
  });

  it("规划工作台状态在模块级 store（抽屉与主区共享；导航/收起不丢）", () => {
    const store = code("planning/map-plan-store.ts");
    expect(store).toContain("applyDraft");
    expect(store).toContain("baseSlotsOf");
    const sidebar = code("panels/MapPlanSidebar.tsx");
    expect(sidebar).toContain("点位与槽位");
    expect(sidebar).toContain("保存到地图规划文件");
    expect(sidebar).toContain("data-name=");
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

describe("生产编辑器复用（2026-08-22 十八轮：规划是完整版，复盘是缩小版）", () => {
  it("队列编辑表是共享组件：规划页与复盘生产页吃同一个 QueueTable", () => {
    const table = code("planning/QueueTable.tsx");
    expect(table).toContain("＋ 在此插入");
    expect(table).toContain('"＋ 加一项"按钮'.replace('"＋ 加一项"按钮', "加一项"));
    const plan = code("pages/PlanningPage.tsx");
    expect(plan).toContain('<QueueTable items={st.items}');
    const prod = code("pages/ProductionPage.tsx");
    expect(prod).toContain("<QueueTable items={draft}");
  });

  it("复盘生产页：转为规划草稿 → 另存为规划文件（命令面仍走 writeGate，不越界）", () => {
    const prod = code("pages/ProductionPage.tsx");
    expect(prod).toContain("转为规划草稿");
    expect(prod).toContain("draftFromSessionQueues");
    expect(prod).toContain("另存为规划");
    expect(prod).toContain("createPlan");
    // 只读域的编辑不碰命令面（writeGate 不动；cmd.run 仍只在可写时渲染）
    expect(prod).toContain("writeGate");
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

describe("F14 切片 1：地图规划画布（2026-08-21）", () => {
  it("规划页的地图 tab 是画布不是列表（草稿投影 2026-08-22 起在 map-plan-store）", () => {
    const page = code("pages/PlanningPage.tsx");
    expect(page).toContain("<MapCanvas");
    expect(page).toContain("marksOverride");
    expect(page).toContain("onBlankClick");
    expect(code("planning/map-plan-store.ts")).toContain("applyDraft");
    // 回归：曾是三张只读列表、没有 canvas（用户最初问题 3 的"规划与地图错位"）
    expect(page).not.toContain('Card title="放置语法速查"');
  });

  it("画布不感知草稿 —— marksOverride 非 null 才替代 map.pos_marks", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).toContain("props.marksOverride ?? map.pos_marks");
    expect(canvas).toContain("props.onBlankClick?.(");
  });

  it("草稿模型是 hunk 序列，画布只吃投影（applyDraft 纯函数）", () => {
    const md = code("planning/map-draft.ts");
    expect(md).toContain("export function applyDraft");
    expect(md).toContain("localStorage");
    expect(md).toContain("map-plan-draft:");
  });

});

describe("F14 切片 2：槽位工具与提案通道（2026-08-21）", () => {
  it("画布接收 slotsOverride，槽位层画草稿投影", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).toContain("props.slotsOverride ?? map.build_slots");
  });

  it("规划工作台有槽位放置工具与重叠即时校验（2026-08-22 起工具在抽屉、校验在主区）", () => {
    const page = code("pages/PlanningPage.tsx");
    // F16：吸附/重叠/placeable 校验收进 previewPlacement 纯函数（ghost 与落笔同一结果）
    expect(page).toContain("previewPlacement");
    expect(page).toContain("placeableAt");
    const sidebar = code("panels/MapPlanSidebar.tsx");
    expect(sidebar).toContain('"＋ 槽位"');
  });

  it("离线保存按钮真实可用（P2：不走提案，直接写地图规划文件）", () => {
    const sidebar = code("panels/MapPlanSidebar.tsx");
    const store = code("planning/map-plan-store.ts");
    expect(sidebar).toContain("保存到地图规划文件");
    expect(store).toContain("mapDraftToHunks");
    expect(store).toContain("saveMapPlan");
    // 回归：提案通道的按钮已从规划工作台退役（离线域直改文件，用户拍板）
    expect(sidebar).not.toContain("提为提案（map_plan）");
  });

  it("审批面板的 map_overlay 分支是叠加画布而非「不能应用」", () => {
    const review = code("panels/ProposalReview.tsx");
    expect(review).toContain("MapOverlayPreview");
    expect(review).toContain("hunksToDraft");
    expect(review).toContain(">当前<");
    expect(review).toContain(">提案后<");
    expect(review).not.toContain("后端目前也不能应用这类提案");
  });

  it("槽位拖动落点是 del+add 两条 hunk（不给 move_slot 动契约）", () => {
    const page = code("pages/PlanningPage.tsx");
    expect(page).toContain("dropSlot");
    expect(page).toContain('{ kind: "del_slot", name }');
    expect(page).toContain('{ kind: "add_slot", name, pos: pv.pos');
  });

  it("MapCanvas 的槽位拖动走独立手势分支，不污染 pan/选单位", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).toContain('mode: "pan" | "slot"');
    expect(canvas).toContain("draggableSlots");
    expect(canvas).toContain("motion.slotGhost");
  });
});

describe("F16：规划编辑体验（2026-08-21 用户反馈）", () => {
  it("画布有放置预览 ghost、悬停上报与双向选中", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).toContain("props.ghost");
    expect(canvas).toContain("props.onHover");
    expect(canvas).toContain("props.selectedName");
    expect(canvas).toContain("props.onMarkClick");
    expect(canvas).toContain("props.onSlotClick");
  });

  it("规划模式（slotsOverride 非 null）槽位画实线 + 名字；可建区层存在", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).toContain("const planning = props.slotsOverride != null");
    expect(canvas).toContain("baked.placeableImage");
    const layers = code("canvas/layers.ts");
    expect(layers).toContain('"placeable"');
  });

  it("预览与落笔同一结果：吸附/重叠/预留/placeable 都在 previewPlacement 纯函数里", () => {
    const md = code("planning/map-draft.ts");
    expect(md).toContain("export function previewPlacement");
    expect(md).toContain('reason: "overlap" | "reserved" | "unplaceable" | null');
  });
});
describe("外壳重构（2026-08-22 十四/十五轮：顶栏极简 + 时间轴下沉 + 右侧固定工作面板）", () => {
  const app = code("App.tsx");

  it("顶栏只剩【指示灯】+ 三框：ModeBar 无标题无下拉，SessionBar/IconRail 退役", () => {
    const bar = code("shell/ModeBar.tsx");
    expect(bar).not.toContain("驾驶舱");
    expect(bar).not.toContain("<select");
    expect(bar).toContain("MODE_ORDER");
    expect(app).not.toContain("SessionBar");
    expect(app).not.toContain("IconRail");
  });

  it("模式命名与顺序：游戏 | 复盘 | 规划（id 不动，动显示名）", () => {
    const mode = code("shell/mode.ts");
    expect(mode).toContain('MODE_ORDER: Mode[] = ["drive", "replay", "offline"]');
    expect(mode).toContain('label: "游戏"');
    expect(mode).toContain('label: "复盘"');
    expect(mode).toContain('label: "规划"');
  });

  it("时间轴下沉主列（I11）：App 不再直挂 Timeline，规划模式整条不渲染", () => {
    const strip = code("shell/TimeStrip.tsx");
    expect(strip).toContain("<Timeline />");
    expect(app).toContain('mode !== "offline"');
    expect(app).not.toMatch(/<Timeline\s*\/>/);
    expect(app).toContain("<TimeStrip />");
  });

  it("游戏模式无活会话 → 主区是 StartCard（会话启动不再住在顶栏）", () => {
    expect(app).toContain("driveIdle ? (");
    expect(app).toContain("<StartCard />");
    const card = code("shell/StartCard.tsx");
    expect(card).toContain("启动真机（SC2）");
  });

  it("无边缘缝外壳（十五轮）：顶栏全宽贴顶，侧栏接在其下，列间浅边分割（无 gap/无 padding 缝）", () => {
    expect(app).toMatch(/<div className="flex h-\[100dvh\] flex-col overflow-hidden text-body">/);
    expect(app).not.toMatch(/className="flex h-\[100dvh\][^"]*p-\d/);   // 视口边不留缝
    const bar = code("shell/ModeBar.tsx");
    expect(bar).toMatch(/border-b border-chrome bg-panel/);             // 白顶栏↔下排的浅边（十六轮反转）
    const rail = code("shell/SideRail.tsx");
    expect(rail).toContain("w-[4.5rem]");
    expect(rail).toMatch(/border-r border-chrome bg-panel/);            // 白侧栏贴左边、接顶栏
  });

  it("生产规划用复盘-生产的三区固定布局（十九轮用户拍板）", () => {
    const plan = code("pages/PlanningPage.tsx");
    expect(plan).toMatch(/flex h-full flex-col gap-2 overflow-hidden/);       // 页根不滚
    expect(plan).toMatch(/xl:grid-cols-3 xl:h-\[38%\]/);                     // 上排固定高度
    expect(plan).toContain("文件与试算");
    expect(plan).toContain("试算投影");                                        // 下方投影占满
  });

  it("工作台融合成一整块白卡（十七轮用户拍板）：地图与工具栏同卡、内部 border-l 分割", () => {
    const page = code("pages/PlanningPage.tsx");
    expect(page).toMatch(/flex h-full overflow-hidden rounded-lg border border-l1 bg-panel shadow-sm/);
    expect(page).toMatch(/w-72 shrink-0 flex-col border-l border-l1/);
    expect(page).toContain("<MapPlanSidebar />");
    expect(page).toContain("<QueueSidebar />");
    expect(app).not.toContain("WorkPanel");       // 独立面板列退役
  });

  it("对话区（十七轮终形）：右/上/下贴边不留缝，只左缘留 8px 缝（main 的 p-2）", () => {
    expect(app).toMatch(/<div className="flex shrink-0 flex-col">/);
    expect(app).not.toMatch(/border-l border-chrome py-2/);
    expect(app).toMatch(/overflow-hidden p-2"/);   // 工作卡四周的缝（也承担卡↔对话的左缝）
    const dock = code("shell/ChatDock.tsx");
    expect(dock).toMatch(/overflow-hidden bg-panel/);   // 贴边矩形（不再圆角浮卡）
  });

  it("选中态反转（十六轮）：外围白，选中的模式段/侧栏按钮变蓝（bg-select）", () => {
    const bar = code("shell/ModeBar.tsx");
    expect(bar).toMatch(/bg-select font-semibold text-strong/);
    const rail = code("shell/SideRail.tsx");
    expect(rail).toMatch(/bg-select font-semibold text-strong/);
  });

  it("复盘只读横幅退役（十六轮用户拍板）：写入控件仍走 writeGate 门控", () => {
    const page = code("pages/ProductionPage.tsx");
    expect(page).toContain("writeGate");
    expect(page).not.toContain("只读：当前是离线帧源");
    expect(page).not.toMatch(/!props\.writable && mode !== "offline"/);
  });

  it("会话轮询只有一份：session-store，ModeBar 与 StartCard 都吃它", () => {
    const store = code("shell/session-store.ts");
    expect(store).toContain("setWatch");
    expect(store).toContain("pickMapPlan");
    expect(app).toContain("useSessionStore.getState().setWatch");
  });
});

describe("动态帧合并（2026-08-22 二十三轮：live 卡顿治理）", () => {
  it("store 侧按 topic 合并动态帧（≤7 次 setState/秒），静态面直通", () => {
    const src = code("store/frames.ts");
    expect(src).toContain("FRAME_FLUSH_MS = 150");
    expect(src).toContain('offer("world"');
    expect(src).toMatch(/subscribe\("static\/map", \(e\) => set/);   // 静态面不走 offer
  });

  it("队列编辑表表头吸顶（滚动只有列表内容动）", () => {
    expect(code("planning/QueueTable.tsx")).toContain('sticky top-0 z-10 bg-panel');
  });
});

describe("回放的生产队列 = 整局操作序列（2026-08-22 二十四轮用户定义）", () => {
  it("回放模式渲染 WholeOpsList：累积 hook + 已执行/待执行两段，拖时间轴不重排", () => {
    const page = code("pages/ProductionPage.tsx");
    expect(page).toContain("WholeOpsList");
    expect(page).toContain("useAccumulatedProjection");
    expect(page).toContain("待执行");
    expect(page).toMatch(/mode === "replay" && projection/);
  });

  it("累积逻辑是共享 hook：投影板与队列卡同源", () => {
    const board = code("charts/ProjectionBoard.tsx");
    expect(board).toContain("useAccumulatedProjection(frame)");
  });
});
