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
  accumulateInto, activeAt, packBars, sharedDomain, toStalls,
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

describe("accumulateInto（F17 历史累积：拖时间轴后左侧内容保留）", () => {
  const mkPoint = (t: number, minerals: number): ProjectionFrame["points"][number] => ({
    t, minerals, gas: 0, supply_used: 0, supply_cap: 0,
    mineral_workers: 0, gas_workers: 0, buildings: {}, units: {}, in_flight_count: 0,
  });

  it("后帧的点覆盖同 t 旧值（新帧实算 > 旧帧远期预测），旧 t 保留", () => {
    const hist = { points: new Map(), events: new Map() };
    const f1 = frame([started("terran/marine", 100)], { t0: 100, horizon: 120 });
    f1.points = [mkPoint(100, 10), mkPoint(101, 11), mkPoint(102, 12)];
    accumulateInto(hist, f1);
    const f2 = frame([], { t0: 102, horizon: 120 });
    f2.points = [mkPoint(102, 99), mkPoint(103, 100)];
    const merged = accumulateInto(hist, f2);
    expect(merged.points.map((p) => [p.t, p.minerals])).toEqual([
      [100, 10], [101, 11], [102, 99], [103, 100],
    ]);
  });

  it("同一事件在多帧重复出现 → 去重（started/completed 配对仍成立）", () => {
    const hist = { points: new Map(), events: new Map() };
    const ev = [started("terran/marine", 100), completed("terran/marine", 110)];
    const f1 = frame([...ev], { t0: 100, horizon: 120 });
    f1.points = [mkPoint(100, 0), mkPoint(110, 5)];
    accumulateInto(hist, f1);
    const f2 = frame([...ev], { t0: 105, horizon: 120 });
    f2.points = [mkPoint(105, 2), mkPoint(110, 3)];
    const merged = accumulateInto(hist, f2);
    // 事件各一份，卡点之外 started/completed 成对出现
    expect(merged.events.filter((e) => e.kind === "started")).toHaveLength(1);
    expect(merged.events.filter((e) => e.kind === "completed")).toHaveLength(1);
  });

  it("超出当前帧末端的旧预测被丢弃（向后拖时间轴后的残留清理）", () => {
    const hist = { points: new Map(), events: new Map() };
    const f1 = frame([started("terran/marine", 150)], { t0: 100, horizon: 120 });
    f1.points = [mkPoint(100, 0), mkPoint(150, 5)];
    accumulateInto(hist, f1);
    // 新帧回退到 t0=110/horizon=10：末端 120，旧帧里 t=150 的点与事件都超出 → 丢弃
    const f2 = frame([], { t0: 110, horizon: 10 });
    f2.points = [mkPoint(110, 1), mkPoint(120, 2)];
    const merged = accumulateInto(hist, f2);
    expect(merged.points.map((p) => p.t)).toEqual([100, 110, 120]);
    expect(merged.events).toHaveLength(0);
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
