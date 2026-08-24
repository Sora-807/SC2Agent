/**
 * 投影泳道数据层测试（I5）—— 纯函数，不需要 DOM。
 *
 * 关键不变式：
 * - 真泳道：每个 stable_id 一条道（不再每个事件一行），道按首次出现紧排；
 * - 配对语义沿用旧 Gantt：completed 闭掉**数值上**最早的未闭合 started；
 * - 红线 C7：from/to/done 只重排帧字段，不算新数值；
 * - sharedDomain 同时罩住曲线点范围与泳道末端（t0+horizon），曲线与泳道才能共用一条轴。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type ProjectionFrame } from "../src/contract";
import {
  LEFT_MARGIN_SECS, activeAt, nowAnchoredRange, packBars, packPairs,
  sharedDomain, toStalls,
} from "../src/charts/gantt-data";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");

type Event = ProjectionFrame["events"][number];

function frame(events: Event[], opts?: { t0?: number; horizon?: number }): ProjectionFrame {
  return {
    based_on_seq: 1,
    based_on_game_time: opts?.t0 ?? 100,
    horizon: opts?.horizon ?? 120,
    source: { kind: "live_queue", queue_name: "main" },
    points: [],
    events,
    skipped: [],
  };
}

const started = (id: string | null, t: number): Event =>
  ({ kind: "started", stable_id: id, t, reason: null });
const completed = (id: string | null, t: number): Event =>
  ({ kind: "completed", stable_id: id, t, reason: null });
const stalled = (id: string | null, t: number, reason: string): Event =>
  ({ kind: "stalled", stable_id: id, t, reason });

describe("packBars：配对（F17：全局行打包，bar 自带名字，不再分道）", () => {
  it("started→completed 闭成一条 done bar（from/to 原样来自事件）", () => {
    const { bars } = packBars(frame([started("terran/marine", 105), completed("terran/marine", 110)]));
    expect(bars).toHaveLength(1);
    expect(bars[0]!.stableId).toBe("terran/marine");
    expect(bars[0]!.from).toBe(105);
    expect(bars[0]!.to).toBe(110);
    expect(bars[0]!.done).toBe(true);
  });

  it("未闭合的 started 延到视野末端（t0+horizon）且 done=false", () => {
    const { bars } = packBars(frame([started("terran/marine", 108)], { t0: 100, horizon: 60 }));
    expect(bars[0]!.done).toBe(false);
    expect(bars[0]!.to).toBe(160);
  });

  it("completed 闭掉数值上最早的未闭合 started（不是字典序：10 不能排在 9 前）", () => {
    // started@109、started@110，completed@115 应闭 109 而不是 110
    const { bars } = packBars(frame([
      started("terran/marine", 109),
      started("terran/marine", 110),
      completed("terran/marine", 115),
    ]));
    const done = bars.filter((b) => b.done);
    expect(done).toHaveLength(1);
    expect(done[0]!.from).toBe(109);
    // 另一条未闭合 → 延到末端
    const open = bars.filter((b) => !b.done);
    expect(open[0]!.from).toBe(110);
  });

  it("没有 started 的 completed 也如实成条（零长 bar，不丢事件）", () => {
    const { bars } = packBars(frame([completed("terran/marine", 112)]));
    expect(bars).toHaveLength(1);
    expect(bars[0]!.from).toBe(112);
    expect(bars[0]!.to).toBe(112);
  });

  it("stable_id 为 null 的事件也能成 bar（stableId 保持 null，名字由页侧 zhOf 兑底）", () => {
    const { bars } = packBars(frame([started(null, 100), completed(null, 105)]));
    expect(bars).toHaveLength(1);
    expect(bars[0]!.stableId).toBeNull();
  });
});

describe("packBars：全局行打包（跨类型贪心，并行最大化）", () => {
  it("时间不重叠的 bar 同行（跨类型也一样）—— 一行塞下不同的东西", () => {
    const { bars, rows } = packBars(frame([
      started("terran/marine", 100), completed("terran/marine", 110),
      started("terran/supplydepot", 110), completed("terran/supplydepot", 131),
      started("terran/refinery", 131), completed("terran/refinery", 155),
    ]));
    expect(bars.map((b) => b.row)).toEqual([0, 0, 0]);
    expect(rows).toBe(1);
  });

  it("时间重叠的 bar 错行；行结束后可复用（贪心，行数最少）", () => {
    const { bars, rows } = packBars(frame([
      started("terran/marine", 100), completed("terran/marine", 110),    // 行 0
      started("terran/scv", 105), completed("terran/scv", 115),        // 重叠 → 行 1
      started("terran/marine", 112), completed("terran/marine", 130),  // 行 0 在 110 已空 → 回行 0
    ]));
    expect(bars.map((b) => b.row)).toEqual([0, 1, 0]);
    expect(rows).toBe(2);
  });

  it("同一时刻两个不同类型并行 → 两行", () => {
    const { rows } = packBars(frame([
      started("terran/marine", 100),
      started("terran/supplydepot", 100),
    ], { t0: 100, horizon: 60 }));
    expect(rows).toBe(2);
  });

  it("无事件时 rows=0（空态由 UI 展示）", () => {
    expect(packBars(frame([]))).toEqual({ bars: [], rows: 0 });
  });
});

describe("packPairs（复盘改版：部分条与投影条共用同一打包核心）", () => {
  it("额外塞进的部分条参与全局行打包（时间不重叠可同行）", () => {
    const projectionBar = { stableId: "terran/supplydepot", from: 105, to: 130, done: false };
    // 截断线 T=100：在建 depot 已耗 10s（from 被延到 90）、剩 20s
    const partial = { stableId: "terran/barracks", from: 90, to: 112, done: false };
    const { bars, rows } = packPairs([projectionBar, partial]);
    expect(bars).toHaveLength(2);
    expect(rows).toBe(2);          // [90,112] 与 [105,130] 重叠 → 错行
  });

  it("packPairs 不改入参（内部先拷贝排序）", () => {
    const pairs = [{ stableId: "a", from: 20, to: 30, done: true },
                   { stableId: "b", from: 5, to: 8, done: true }];
    packPairs(pairs);
    expect(pairs[0]!.from).toBe(20);
  });
});

describe("nowAnchoredRange（复盘改版：截断线钉 T、左缘 T-30、右缘不出空白）", () => {
  it("T=65s → 左缘 35s（30s 留白），右缘 = from+span（数据够长时）", () => {
    expect(nowAnchoredRange(65, 300, 600)).toEqual({ from: 35, to: 335 });
  });

  it("T 靠近开局 → 左缘钉 0（不足 30s 就只留出有的）", () => {
    expect(nowAnchoredRange(10, 300, 600)).toEqual({ from: 0, to: 300 });
  });

  it("数据末端近 → 右缘钳 dataEnd（不出空白），但至少露到 T+1", () => {
    expect(nowAnchoredRange(65, 300, 100)).toEqual({ from: 35, to: 100 });
    expect(nowAnchoredRange(65, 300, 60)).toEqual({ from: 35, to: 66 });
  });

  it("留白量是拍板的 30s", () => {
    expect(LEFT_MARGIN_SECS).toBe(30);
  });
});

describe("activeAt（F15 点击检查：t 时刻在产明细）", () => {
  it("配对区间内算在产、区间外不算；零长 bar（孤立 completed）不算", () => {
    const f = frame([
      started("terran/marine", 100), completed("terran/marine", 118),
      started("terran/supplydepot", 110), completed("terran/supplydepot", 131),
      completed("terran/refinery", 120),   // 孤立 completed → 零长 bar，不在产
    ]);
    const at105 = activeAt(f, 105);
    expect(at105).toHaveLength(1);
    expect(at105[0]!.stableId).toBe("terran/marine");
    expect(at105[0]!.from).toBe(100);
    expect(at105[0]!.to).toBe(118);
    // 两个都在产的时刻
    expect(activeAt(f, 115).map((b) => b.stableId).sort())
      .toEqual(["terran/marine", "terran/supplydepot"]);
    // 118 是 completed 时刻：闭区间左端、开区间右端 → marine 已不算
    expect(activeAt(f, 118).map((b) => b.stableId)).toEqual(["terran/supplydepot"]);
    // 零长 bar 不算在产
    expect(activeAt(f, 120).map((b) => b.stableId)).toEqual(["terran/supplydepot"]);
  });

  it("未闭合的 started 延到视野末端，末端前都算在产", () => {
    const f = frame([started("terran/marine", 108)], { t0: 100, horizon: 60 });
    expect(activeAt(f, 150)).toHaveLength(1);
    expect(activeAt(f, 159).map((b) => b.done)).toEqual([false]);
    expect(activeAt(f, 161)).toHaveLength(0);
  });
});

describe("toStalls", () => {
  it("只挑 stalled 事件，t/原因原样传递", () => {
    const s = toStalls(frame([
      started("terran/marine", 100),
      stalled("terran/marine", 103, "缺晶体矿"),
      completed("terran/marine", 110),
    ]));
    expect(s).toEqual([{ t: 103, stableId: "terran/marine", reason: "缺晶体矿" }]);
  });

  it("没有卡点时空数组", () => {
    expect(toStalls(frame([started("terran/marine", 100)]))).toEqual([]);
  });
});

describe("sharedDomain：曲线与泳道共用的定义域", () => {
  it("无点时 = [t0, t0+horizon]（泳道未闭合 bar 的末端被罩住）", () => {
    expect(sharedDomain(frame([], { t0: 100, horizon: 120 })))
      .toEqual({ from: 100, to: 220 });
  });

  it("horizon 为 0/负时至少给 1 秒（防除零）", () => {
    expect(sharedDomain(frame([], { t0: 100, horizon: 0 }))).toEqual({ from: 100, to: 101 });
    expect(sharedDomain(frame([], { t0: 100, horizon: -5 }))).toEqual({ from: 100, to: 101 });
  });

  it("曲线点超出 t0+horizon 时以点范围为准（两边都罩住）", () => {
    const f = frame([], { t0: 100, horizon: 60 });
    f.points = [
      { t: 100, minerals: 0, gas: 0, supply_used: 0, supply_cap: 0,
        mineral_workers: 0, gas_workers: 0, buildings: {}, units: {},
        in_flight_count: 0 },
      { t: 250, minerals: 0, gas: 0, supply_used: 0, supply_cap: 0,
        mineral_workers: 0, gas_workers: 0, buildings: {}, units: {},
        in_flight_count: 0 },
    ];
    expect(sharedDomain(f)).toEqual({ from: 100, to: 250 });
  });
});

describe("夹具回归（真 planner 的投影帧）", () => {
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

  it("有事件的帧都能出泳道；bar 区间自洽且未闭合 bar 延到 t0+horizon", () => {
    const frames = ["opening", "blocked", "leapfrog"]
      .flatMap((n) => projectionFrames(n + ".jsonl"))
      .filter((f) => f.events.length > 0);
    expect(frames.length).toBeGreaterThan(0);
    for (const f of frames) {
      const domain = sharedDomain(f);
      const { bars, rows } = packBars(f);
      // 只有卡点事件的帧 rows=0 合法；有 bar 的帧至少 1 行
      if (bars.length > 0) expect(rows).toBeGreaterThanOrEqual(1);
      else expect(rows).toBe(0);
      // 全局行打包不变式：同行 bar 互不重叠（闭区间）
      const byRow = new Map<number, typeof bars>();
      for (const b of bars) {
        const list = byRow.get(b.row) ?? [];
        list.push(b);
        byRow.set(b.row, list);
      }
      for (const list of byRow.values()) {
        const sorted = [...list].sort((a, b) => a.from - b.from);
        for (let i = 1; i < sorted.length; i += 1) {
          // 贴边相接（from == 前一条 to）合法：打包条件是 lastTo <= from
          expect(sorted[i]!.from).toBeGreaterThanOrEqual(sorted[i - 1]!.to);
        }
      }
      for (const b of bars) {
        expect(b.from).toBeLessThanOrEqual(b.to);
        if (!b.done) expect(b.to).toBe(f.based_on_game_time + Math.max(1, f.horizon));
        // 定义域必须罩住所有 bar（泳道百分比定位的边界条件）
        expect(b.from).toBeGreaterThanOrEqual(domain.from);
        expect(b.to).toBeLessThanOrEqual(domain.to);
      }
    }
  });

  it("stalled 事件被泳道忽略（卡点走竖线，不占 bar）", () => {
    const frames = projectionFrames("blocked.jsonl").filter((f) =>
      f.events.some((e) => e.kind === "stalled"));
    expect(frames.length).toBeGreaterThan(0);
    for (const f of frames) {
      const nonStall = f.events.filter((e) => e.kind !== "stalled").length;
      const bars = packBars(f).bars.length;
      expect(bars).toBeLessThanOrEqual(nonStall);
    }
  });
});
