/**
 * 复盘数据源（二十六轮）：对局录像进下拉 —— listRecordings 的形状与容错。
 * 夹具是手搓场景，录像是真开过的一局；同格式（JSONL 帧流），只是 URL 走后端 API。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { listRecordings } from "../src/fixtures";

afterEach(() => vi.unstubAllGlobals());

describe("listRecordings（对局录像清单）", () => {
  it("后端行 → 复盘源：对局记录 + 时间 + 族 vs 族 + 地图 + 时长（二十七轮拍板形态）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => [{
        id: "rec-20260823-143000-sc2", driver: "sc2", to: 754, map: "LadderMap",
        state: "已结束", started_at: "2026-08-23T14:30:00", envelopes: 9000,
        my_race_zh: "人族", enemy_race_zh: "神族",
      }, {
        id: "rec-20260823-150000-sim", driver: "sim", to: 61,
        state: "recording", started_at: "2026-08-23T15:00:00", envelopes: 100,
      }],
    })));
    const rows = await listRecordings("http://x");
    expect(rows).toHaveLength(2);
    expect(rows[0]!.key).toBe("rec:rec-20260823-143000-sc2");
    expect(rows[0]!.url).toBe("http://x/api/recordings/rec-20260823-143000-sc2/jsonl");
    expect(rows[0]!.label).toBe("对局记录 08-23 14:30 · 人族 vs 神族 · LadderMap（12:34）");
    // 没见过敌人的局：族段省略，不显示「— vs —」
    expect(rows[1]!.label).toBe("对局记录 08-23 15:00（1:01 · 录制中）");
  });

  it("后端没起 / 没配目录 = 空表，不算错误（夹具照常用）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false })));
    expect(await listRecordings("http://x")).toEqual([]);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("网络"); }));
    expect(await listRecordings("http://x")).toEqual([]);
  });
});
