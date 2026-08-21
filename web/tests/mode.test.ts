/**
 * F13a：模式轴 —— 模式 → 合法帧源映射、切模式落点、store 的真 live attach。
 *
 * 关键不变式（锁死 PLAN §1.5 根因 U 不复发）：选「实时驾驶」时 store 必须走到
 * `attach("live")` 且 `caps.live === true` —— 之前 attach("live") 的代码存在但
 * 永远不会被 UI 触发，live 会话上反而显示「播放 ×4」。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { REV } from "../src/contract";
import { writeGate } from "../src/shell/mode";

// mock 掉 ws 源与 API 探测（node 环境不打网络）；fixtures 加载也 mock（离线切换要读夹具文本）
const wsMod = vi.hoisted(() => ({ created: 0 }));
vi.mock("../src/source/ws", () => ({
  WsFrameSource: {
    replay: vi.fn(() => {
      wsMod.created += 1;
      return {
        connect: async () => {},
        subscribe: () => () => {},
        range: () => ({ from: 0, to: 0 }),
        position: () => 0,
        markers: () => [],
        caps: { live: false, seek: false },
        dispose: () => {},
        onChange: () => () => {},
        onDisconnect: () => () => {},   // 2026-08-21 起 frames.attach 会注册断线回调
        disconnected: () => false,
      };
    }),
  },
  probeApi: vi.fn(async () => ({ ok: false })),
}));
vi.mock("../src/fixtures", () => ({
  listFixtures: vi.fn(async () => []),
  loadFixture: vi.fn(async () => JSON.stringify({
    topic: "frame/session", rev: REV, seq: 1, game_time: 0, wall_ms: 0,
    payload: {
      state: "未连接", frame_source: "fixture", map_name: null,
      my_race: null, enemy_race: null, game_time: 0, error: null,
    },
  })),
}));

import { MODE_SOURCES, allowedSources, defaultSource, type Mode } from "../src/shell/mode";
import { useFrames } from "../src/store/frames";

const FIXTURES = [{ key: "opening" }, { key: "leapfrog" }, { key: "blocked" }];

describe("模式 → 合法帧源", () => {
  it("三个模式各自的合法帧源（U19：drive 只有 live；offline 只有夹具）", () => {
    expect(MODE_SOURCES.offline).toEqual(["fixture"]);
    expect(MODE_SOURCES.replay).toEqual(["fixture", "mock-live", "api"]);
    expect(MODE_SOURCES.drive).toEqual(["live"]);
  });

  it("后端不在时剔除 api / live（不给点不动的选项）", () => {
    expect(allowedSources("replay", false)).toEqual(["fixture", "mock-live"]);
    expect(allowedSources("replay", true)).toEqual(["fixture", "mock-live", "api"]);
    expect(allowedSources("drive", false)).toEqual([]);
  });

  it("切模式落点：保持当前夹具优先；drive 需要后端", () => {
    expect(defaultSource("offline", FIXTURES, false, "leapfrog"))
      .toEqual({ kind: "fixture", fixtureKey: "leapfrog" });
    expect(defaultSource("offline", FIXTURES, false, null))
      .toEqual({ kind: "fixture", fixtureKey: "opening" });
    expect(defaultSource("replay", FIXTURES, true, null))
      .toEqual({ kind: "fixture", fixtureKey: "opening" });
    expect(defaultSource("drive", FIXTURES, false, null)).toBeNull();
    expect(defaultSource("drive", FIXTURES, true, null))
      .toEqual({ kind: "live", fixtureKey: "live" });
  });
});

describe("store：切模式真正连 live（根因 U 不复发）", () => {
  beforeEach(() => {
    wsMod.created = 0;
    useFrames.setState({
      fixtures: [
        { key: "opening", label: "①", file: "opening.jsonl", snapshots: [] },
        { key: "leapfrog", label: "③", file: "leapfrog.jsonl", snapshots: [] },
      ] as never,
      fixtureKey: null,
      sourceKind: "fixture",
      api: { ok: true },
      mode: "offline",
      error: null,
    });
  });

  it("选「实时驾驶」→ 走 attach(live) 且 caps.live===true（外部套 ReviewableSource）", async () => {
    await useFrames.getState().setMode("drive");
    const s = useFrames.getState();
    expect(s.mode).toBe("drive");
    expect(s.sourceKind).toBe("live");
    expect(s.caps.live).toBe(true); // live 源套 ReviewableSource 后 caps.live 必须为 true
    expect(s.error).toBeNull();
  });

  it("后端不在时选「实时驾驶」→ 不落 attach，error 给出启动后端的方法", async () => {
    useFrames.setState({ api: { ok: false } });
    await useFrames.getState().setMode("drive");
    const s = useFrames.getState();
    expect(s.sourceKind).not.toBe("live");
    expect(s.error ?? "").toContain("serve_api");
  });

  it("切回离线 → 回到夹具源，authoring 可用（offline 是唯一编辑态）", async () => {
    await useFrames.getState().setMode("drive");
    await useFrames.getState().setMode("offline");
    const s = useFrames.getState();
    expect(s.mode).toBe("offline");
    expect(s.sourceKind).toBe("fixture");
    expect(s.fixtureKey).toBe("opening");
  });

  it("所有模式值都能落到合法帧源（穷举，防 Mode 扩了忘改 defaultSource）", () => {
    for (const m of ["offline", "replay", "drive"] as Mode[]) {
      const target = defaultSource(m, FIXTURES, true, null);
      expect(target, m).not.toBeNull();
      expect(MODE_SOURCES[m]).toContain(target!.kind);
    }
  });
});

describe("写入面门禁 writeGate（U7 / R8）", () => {
  it("live 会话源 + 跟随实时 = 可写", () => {
    expect(writeGate("live", "live")).toEqual({ writable: true, reason: null });
  });

  it("live 但在回看 → 不可写，理由点明 R8", () => {
    const g = writeGate("live", "review");
    expect(g.writable).toBe(false);
    expect(g.reason).toContain("R8");
    expect(g.reason).toContain("回到实时");
  });

  it("回放源（api）不可写 —— 它背后没有会话，写了会打到没在看的世界上", () => {
    const g = writeGate("api", "live");
    expect(g.writable).toBe(false);
    expect(g.reason).toContain("回放");
  });

  it("离线夹具 / 模拟 live 都不可写", () => {
    for (const k of ["fixture", "mock-live"] as const) {
      const g = writeGate(k, "live");
      expect(g.writable, k).toBe(false);
      expect(g.reason, k).toContain("离线");
    }
  });

  it("回归：门禁绝不能绑在 api 源上（曾经如此，导致 live 下写入控件全消失）", () => {
    // 这条是核心不变式：可写的**唯一**源是 live
    const kinds = ["fixture", "mock-live", "api", "live"] as const;
    const writable = kinds.filter((k) => writeGate(k, "live").writable);
    expect(writable).toEqual(["live"]);
  });

  it("不可写时一定给出理由（G7：禁用必须带理由）", () => {
    for (const k of ["fixture", "mock-live", "api"] as const) {
      expect(writeGate(k, "live").reason, k).toBeTruthy();
    }
    expect(writeGate("live", "review").reason).toBeTruthy();
  });
});
