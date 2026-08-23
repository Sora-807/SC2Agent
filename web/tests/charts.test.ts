/**
 * 图表数据层测试 —— 纯函数，不需要 DOM。
 * 关键断言：图表**只重排帧字段，不新增数值**（红线 C7）。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type ProjectionFrame } from "../src/contract";
import { fmtMMSS, isAssignId, toMarkers, toSeries } from "../src/charts/projection-data";
import { packBars } from "../src/charts/gantt-data";

function code(rel: string): string {
  const src = readFileSync(new URL("../src/" + rel, import.meta.url), "utf-8");
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");

function projectionFrames(file: string): ProjectionFrame[] {
  const text = readFileSync(resolve(FIX_DIR, file), "utf8");
  const out: ProjectionFrame[] = [];
  for (const line of text.split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic === "frame/projection") out.push(env.payload);
  }
  return out;
}

describe("fmtMMSS", () => {
  it("按游戏时间格式化", () => {
    expect(fmtMMSS(0)).toBe("00:00");
    expect(fmtMMSS(59.4)).toBe("00:59");
    expect(fmtMMSS(600)).toBe("10:00");
    expect(fmtMMSS(645)).toBe("10:45");
    expect(fmtMMSS(-3)).toBe("00:00");
  });
});

describe("toSeries", () => {
  const frames = projectionFrames("blocked.jsonl");

  it("夹具里有投影帧", () => {
    expect(frames.length).toBeGreaterThan(0);
  });

  it("逐秒采样、间隔恒为 1（点数由真 planner 决定，不写死）", () => {
    for (const f of frames) {
      expect(f.horizon).toBe(120);
      const s = toSeries(f);
      expect(s.t.length).toBe(f.points.length);
      expect(s.t.length).toBeGreaterThan(60);
      for (let i = 1; i < s.t.length; i += 1) {
        expect(s.t[i]! - s.t[i - 1]!).toBeCloseTo(1, 6);
      }
      // 视野长度 = 点数 - 1（planner 从 based_on_game_time 起逐秒推进到 horizon）
      expect(Math.round(s.to - s.from)).toBe(s.t.length - 1);
    }
  });

  it("x 轴是绝对游戏时间且单调递增（左边缘=现在）", () => {
    const s = toSeries(frames[0]!);
    expect(s.from).toBeCloseTo(frames[0]!.based_on_game_time, 5);
    for (let i = 1; i < s.t.length; i += 1) {
      expect(s.t[i]!).toBeGreaterThan(s.t[i - 1]!);
    }
  });

  it("只重排、不新增数值（逐点与帧字段全等）", () => {
    for (const f of frames) {
      const s = toSeries(f);
      f.points.forEach((p, i) => {
        expect(s.t[i]).toBe(p.t);
        expect(s.minerals[i]).toBe(p.minerals);
        expect(s.gas[i]).toBe(p.gas);
      });
    }
  });
});

describe("toMarkers", () => {
  it("每个 event 都变成一个标记，t 与 kind 原样传递", () => {
    const f = projectionFrames("blocked.jsonl").find((x) => x.events.length > 0);
    expect(f).toBeDefined();
    const m = toMarkers(f!);
    expect(m.length).toBe(f!.events.length);
    expect(m.map((x) => x.t)).toEqual(f!.events.map((e) => e.t));
    expect(m.map((x) => x.kind)).toEqual(f!.events.map((e) => e.kind));
  });

  it("卡点的文案带后端给的原因（前端不编文案）", () => {
    const stalls = projectionFrames("blocked.jsonl")
      .flatMap((f) => toMarkers(f))
      .filter((m) => m.kind === "stalled");
    expect(stalls.length, "阻塞场景的投影里应有卡点").toBeGreaterThan(0);
    for (const s of stalls) {
      expect(s.text.startsWith("卡")).toBe(true);
      expect(s.text.length).toBeGreaterThan(2); // 原因非空
    }
  });

  it("三种事件都可能出现，且每种都能变成标记", () => {
    const kinds = new Set(
      ["opening", "blocked", "leapfrog"]
        .flatMap((n) => projectionFrames(n + ".jsonl"))
        .flatMap((f) => toMarkers(f))
        .map((m) => m.kind),
    );
    expect(kinds.has("started")).toBe(true);
    expect(kinds.has("completed")).toBe(true);
    expect(kinds.has("stalled")).toBe(true); // 真 planner 会报供给/资源卡点
  });

  it("无事件时无竖线", () => {
    const empty: ProjectionFrame = {
      based_on_seq: 1, based_on_game_time: 0, horizon: 0,
      source: { kind: "live_queue", queue_name: "main" }, points: [], events: [], skipped: [],
    };
    expect(toMarkers(empty)).toEqual([]);
  });
});

function baseFrame(): ProjectionFrame {
  return {
    based_on_seq: 1, based_on_game_time: 0, horizon: 300,
    source: { kind: "live_queue", queue_name: "main" },
    points: [], events: [], skipped: [],
  };
}

// ---------------- 二十轮：拖动=平移、零点钉 0、框选下线、折叠退役 ----------------

describe("投影交互重定义（2026-08-22 二十轮）", () => {
  it("uPlot 框选缩放全时禁用（蓝色选框下线）", () => {
    const chart = code("charts/ProjectionChart.tsx");
    expect(chart).toContain("drag: { x: false, y: false }");
    const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf-8");
    expect(css).not.toContain("u-select");
  });

  it("板内拖动 = 平移视野，不再 seek（拖时间轴会回退时间→历史清空→泳道突现突失）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    expect(board).toContain("平移视野");
    expect(board).not.toContain("st.seek");
    expect(board).not.toContain("startPos");
  });

  it("零点钉最左：from 恒 >= 0（初始化与平移双侧 clamp）；初始窗口钳 ZOOM_SPAN_MAX（二十六轮）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    // 双侧钳制（二十七轮）：左钉 0，右钳数据末端；手势死于元素外松手要能收尾
    expect(board).toMatch(/const from = clamp\(r\.from \+ dt, 0, Math\.max\(0, end - span\)\)/);
    expect(board).toContain("e.buttons & 1");
    expect(board).toContain("setPointerCapture");
    // 初始窗口经 zoomSpan 钳制：until_complete 的整局 horizon 不再全塞一屏
    //（「拖一下十几分钟 + 事件密到不可读」的根因就是全量 initialDomain）
    expect(board).toContain("zoomSpan(Math.max(1, frame.horizon), 1)");
    // 右端钳数据末端（不出空白）
    expect(board).toContain("Math.min(zoomSpan(Math.max(1, frame.horizon), 1), Math.max(1, dataEnd))");
    expect(board).not.toContain("initialDomain");
    const chart = code("charts/ProjectionChart.tsx");
    expect(chart).toMatch(/Math\.max\(0, d\.from\)/);
  });

  it("独立卡（概览）拖图 = 平移：内部持有定域，拖过之后数据增长只延伸右端", () => {
    const chart = code("charts/ProjectionChart.tsx");
    expect(chart).toContain("ownRange");
    expect(chart).toMatch(/series\.to <= ownRange\.to/);
  });

  it("跟随是边缘触发滑窗，不是居中重定（整轴跳的另一半根因）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    expect(board).not.toContain("centerRange");
    expect(board).toMatch(/position <= r\.from \+ margin/);
  });
});

describe("折叠退役（2026-08-22 二十轮：假折叠不省空间）", () => {
  it("概览面板常开：useCollapsed/STORE_KEY/折叠钮都删了", () => {
    const src = code("pages/Overview.tsx");
    expect(src).not.toContain("useCollapsed");
    expect(src).not.toContain("collapsed");
    expect(src).not.toContain("▸");
  });

  it("对话栏常开：收起按钮与收起态删了", () => {
    const src = code("shell/ChatDock.tsx");
    expect(src).not.toContain("展开对话栏");
    expect(src).not.toContain('title="收起"');
    expect(src).not.toContain("setOpen");
  });
});

// ---------------- 二十一轮：assign 不进泳道 / 黄虚线标记 / 毛刺刻度退役 ----------------

describe("assign 瞬时动作的展示（2026-08-22 二十一轮）", () => {
  it("isAssignId 识别 planner 的 assign_<task> 标签", () => {
    expect(isAssignId("assign_gas")).toBe(true);
    expect(isAssignId("assign_mineral")).toBe(true);
    expect(isAssignId("terran/scv")).toBe(false);
    expect(isAssignId(null)).toBe(false);
  });

  it("toMarkers 把 assign 事件映射成 kind=assign（带中文说明）", () => {
    const frame = baseFrame();
    frame.events = [
      { kind: "started", stable_id: "assign_gas", t: 10, reason: null },
      { kind: "started", stable_id: "terran/barracks", t: 12, reason: null },
    ];
    const ms = toMarkers(frame);
    expect(ms[0]).toMatchObject({ kind: "assign", text: "维持采气" });
    expect(ms[1]!.kind).toBe("started");
  });

  it("packBars 不给 assign 事件造 bar（无工期的动作不占泳道）", () => {
    const frame = baseFrame();
    frame.events = [
      { kind: "started", stable_id: "assign_gas", t: 10, reason: null },
      { kind: "started", stable_id: "terran/barracks", t: 12, reason: null },
      { kind: "completed", stable_id: "terran/barracks", t: 40, reason: null },
    ];
    const packed = packBars(frame);
    expect(packed.bars.map((b) => b.stableId)).toEqual(["terran/barracks"]);
  });

  it("底部开工/完成刻度退役（用户看不懂的绿毛刺）；assign 走黄虚线分支", () => {
    const chart = code("charts/ProjectionChart.tsx");
    expect(chart).not.toContain("bottom - (m.kind ===");
    expect(chart).toContain('m.kind === "assign"');
    expect(chart).toContain("C.assign");
  });

  it("平移 1:1 跟手（二十四轮拍板）+ 封顶 960s（半幅拖 ≈8 分钟上限不变）", () => {
    expect(code("charts/ProjectionChart.tsx")).toContain("PAN_GAIN = 1.0");
    expect(code("charts/ProjectionBoard.tsx")).toContain("PAN_GAIN = 1.0");
    expect(code("charts/ProjectionChart.tsx")).toContain("PAN_SPAN_CAP = 960");
    expect(code("charts/ProjectionBoard.tsx")).toContain("PAN_SPAN_CAP = 960");
  });

  it("单位 chip 白底黑字（暗色硬编码 rgba(13,17,23) 退役）；rAF 有脏标记不再 60fps 空转", () => {
    const canvas = code("canvas/MapCanvas.tsx");
    expect(canvas).not.toContain("rgba(13,17,23");
    expect(canvas).toContain("canvasBase().void");
    expect(canvas).toContain("dirty.current");
    expect(canvas).toContain("const animating = p.smooth && alpha < 1");
  });

  it("思考图标旋转中心居中（svg 是 13px，8px 原点偏心会晃）", () => {
    const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf-8");
    expect(css).toContain(".think-running svg { animation: think-orbit 2.4s linear infinite; transform-origin: center; }");
  });
});

// ---------------- 二十三轮：泳道轴对齐 + 行间虚线 ----------------

describe("泳道与曲线共享时间轴（像素对齐）", () => {
  it("bar 在 gutter 内层容器定位（旧版整箱百分比 → 泳道 0 点比曲线靠左 46px）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    const gutter = board.indexOf('left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right');
    const bars = board.indexOf(".filter((b) => b.to >= range.from && b.from <= range.to)");
    expect(gutter).toBeGreaterThan(-1);
    expect(bars).toBeGreaterThan(gutter);   // bars 容器带 gutter，且在分隔线容器之后
    expect(board.indexOf("left: l0 + \"%\"")).toBeGreaterThan(bars);
  });

  it("二十六轮：bar 只渲染视窗内 + 窄条隐藏文字（密事件可读性）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    expect(board).toContain(".filter((b) => b.to >= range.from && b.from <= range.to)");
    expect(board).toContain("BAR_LABEL_MIN_PX = 30");
    expect(board).toContain("wPx >= BAR_LABEL_MIN_PX");
    expect(board).toContain("ResizeObserver");
    // 试算投影不再把整局 horizon 塞初始窗口（「拖一下十几分钟」的根因）
    expect(code("pages/PlanningPage.tsx")).not.toContain("initialDomain={{");
  });

  it("泳道行间有灰色细虚线分隔（纯白太素，用户拍板）", () => {
    const board = code("charts/ProjectionBoard.tsx");
    expect(board).toContain("border-t border-dashed border-l1");
  });
});
