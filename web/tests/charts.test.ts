/**
 * 图表数据层测试 —— 纯函数，不需要 DOM。
 * 关键断言：图表**只重排帧字段，不新增数值**（红线 C7）。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type ProjectionFrame } from "../src/contract";
import { fmtMMSS, toMarkers, toSeries } from "../src/charts/projection-data";

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
