/**
 * F13a：模式轴 —— 模式 → 合法帧源映射、切模式落点、store 的真 live attach。
 *
 * 关键不变式（锁死 PLAN §1.5 根因 U 不复发）：选「实时驾驶」时 store 必须走到
 * `attach("live")` 且 `caps.live === true` —— 之前 attach("live") 的代码存在但
 * 永远不会被 UI 触发，live 会话上反而显示「播放 ×4」。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { REV } from "../src/contract";
import { bootHint, writeGate } from "../src/shell/mode";

// mock 掉 ws 源与 API 探测（node 环境不打网络）；fixtures 加载也 mock（离线切换要读夹具文本）
// wsMod.listeners/emit：让测试能**主动推帧**（store 的静态面合并顺序测试需要）
const wsMod = vi.hoisted(() => {
  const listeners = new Map<string, Set<(e: unknown) => void>>();
  return {
    created: 0,
    reset() {
      listeners.clear();
    },
    emit(topic: string, env: unknown) {
      for (const cb of listeners.get(topic) ?? []) cb(env);
    },
    register(topic: string, cb: (e: unknown) => void) {
      let set = listeners.get(topic);
      if (!set) {
        set = new Set();
        listeners.set(topic, set);
      }
      set.add(cb);
      return () => set.delete(cb);
    },
  };
});
vi.mock("../src/source/ws", () => ({
  WsFrameSource: {
    replay: vi.fn(() => {
      wsMod.created += 1;
      return {
        connect: async () => {},
        subscribe: (topic: string, cb: (e: unknown) => void) => wsMod.register(topic, cb),
        range: () => ({ from: 0, to: 0 }),
        position: () => 0,
        markers: () => [],
        caps: { live: false, seek: false },
        dispose: () => wsMod.reset(),
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

import { MODE_META, MODE_ORDER, MODE_SOURCES, allowedSources, defaultSource, pickMapPlan, type Mode } from "../src/shell/mode";
import { useFrames } from "../src/store/frames";

const FIXTURES = [{ key: "opening" }, { key: "leapfrog" }, { key: "blocked" }];

describe("模式命名与顺序（2026-08-22 十四轮：用户最终命名）", () => {
  it("三段控件顺序 = 游戏 | 复盘 | 规划", () => {
    expect(MODE_ORDER).toEqual(["drive", "replay", "offline"]);
  });

  it("显示名收短（id 不动）：drive=游戏、replay=复盘、offline=规划", () => {
    expect(MODE_META.drive.label).toBe("游戏");
    expect(MODE_META.replay.label).toBe("复盘");
    expect(MODE_META.offline.label).toBe("规划");
  });
});

describe("模式 → 合法帧源", () => {
  it("三个模式各自的合法帧源（二十七轮：复盘收敛成只看对局记录，mock-live/api 退役不露出）", () => {
    expect(MODE_SOURCES.offline).toEqual(["fixture"]);
    expect(MODE_SOURCES.replay).toEqual(["fixture"]);
    expect(MODE_SOURCES.drive).toEqual(["live"]);
  });

  it("后端不在时剔除 api / live（不给点不动的选项）", () => {
    expect(allowedSources("replay", false)).toEqual(["fixture"]);
    expect(allowedSources("drive", false)).toEqual([]);
  });

  it("切模式落点：offline 保持当前夹具；replay 只落对局记录（没有记录 = null 引导）", () => {
    expect(defaultSource("offline", FIXTURES, false, "leapfrog"))
      .toEqual({ kind: "fixture", fixtureKey: "leapfrog" });
    expect(defaultSource("offline", FIXTURES, false, null))
      .toEqual({ kind: "fixture", fixtureKey: "opening" });
    // 只有夹具（无 rec: 录像）时进复盘 = null（页面给「开一局自动录制」的引导）
    expect(defaultSource("replay", FIXTURES, true, null)).toBeNull();
    const withRec = [...FIXTURES, { key: "rec:rec-1-sc2" }];
    expect(defaultSource("replay", withRec, true, null))
      .toEqual({ kind: "fixture", fixtureKey: "rec:rec-1-sc2" });
    // 已在看某份录像 → 切走再切回仍保持它
    expect(defaultSource("replay", withRec, true, "rec:rec-1-sc2"))
      .toEqual({ kind: "fixture", fixtureKey: "rec:rec-1-sc2" });
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

  it("所有模式值都能落到合法帧源（穷举；replay 没录像时允许 null = 引导态）", () => {
    for (const m of ["offline", "drive"] as Mode[]) {
      const target = defaultSource(m, FIXTURES, true, null);
      expect(target, m).not.toBeNull();
      expect(MODE_SOURCES[m]).toContain(target!.kind);
    }
    expect(defaultSource("replay", FIXTURES, true, null)).toBeNull();
    expect(defaultSource("replay", [{ key: "rec:x" }], true, null)).not.toBeNull();
  });
});

describe("真机首帧等待提示 bootHint（I6）", () => {
  it("drive + live 会话 + 地图未到 → 提示（含 1-2 分钟预期，治「误读为没同步」）", () => {
    expect(bootHint("drive", "live", "启动中", false)).toContain("1-2 分钟");
    expect(bootHint("drive", "live", "对局中", false)).toContain("正在连接");
  });

  it("会话帧还没来（null）也提示 —— attach(live) 之后第一帧本来就要等", () => {
    expect(bootHint("drive", "live", null, false)).not.toBeNull();
  });

  it("数据到了自动消失", () => {
    expect(bootHint("drive", "live", "启动中", true)).toBeNull();
    expect(bootHint("drive", "live", null, true)).toBeNull();
  });

  it("终态不提示：已结束/崩溃/未连接的真实状态另有显示", () => {
    for (const s of ["已结束", "崩溃", "未连接"] as const) {
      expect(bootHint("drive", "live", s, false), s).toBeNull();
    }
  });

  it("非 drive 模式 / 非 live 源不提示（离线与回放没有等待真机这回事）", () => {
    expect(bootHint("offline", "fixture", "启动中", false)).toBeNull();
    expect(bootHint("replay", "api", null, false)).toBeNull();
    expect(bootHint("replay", "mock-live", "启动中", false)).toBeNull();
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

describe("会话装配的地图规划 id 兜底 pickMapPlan（2026-08-21 实时驾驶整改）", () => {
  it("当前值仍在清单里 → 保持不变", () => {
    const plans = [{ id: "default-bl" }, { id: "layout-bl" }, { id: "my-plan" }];
    expect(pickMapPlan(plans, "my-plan")).toBe("my-plan");
  });

  it("旧默认值 default 已随预设改名退役 → 落清单第一个，而不是原样发出去吃 400", () => {
    const plans = [{ id: "default-bl" }, { id: "layout-bl" }];
    expect(pickMapPlan(plans, "default")).toBe("default-bl");
  });

  it("清单空 / 还没到 → null（不带参数，后端用出厂模板）", () => {
    expect(pickMapPlan([], "default")).toBeNull();
    expect(pickMapPlan([], null)).toBeNull();
  });
});

describe("store：静态面到达顺序无关（真机 terrain 先于 map 曾被整局吞掉）", () => {
  beforeEach(() => {
    wsMod.reset();
    wsMod.created = 0;
    useFrames.setState({
      fixtures: [], fixtureKey: null, sourceKind: "fixture",
      api: { ok: true }, mode: "offline", error: null,
    });
  });

  it("terrain 先到、map 后到 → 挂起再合并（pendingTerrain，A5 帧序不可假设）", async () => {
    await useFrames.getState().attach("live", "live");
    wsMod.emit("static/terrain", {
      topic: "static/terrain", rev: REV, seq: 2, game_time: 0, wall_ms: 0,
      payload: { marker: "T" },
    });
    // map 未到：terrain 不能丢（旧实现这里静默吞掉 = 真机「地形不可用」）
    expect(useFrames.getState().map).toBeNull();
    wsMod.emit("static/map", {
      topic: "static/map", rev: REV, seq: 1, game_time: 0, wall_ms: 0,
      payload: { map_name: "M", terrain: null },
    });
    const s = useFrames.getState();
    expect(s.map?.map_name).toBe("M");
    expect(s.map?.terrain).toEqual({ marker: "T" });
  });

  it("map 先到、terrain 后到（正常序）→ 同样合并，行为不变", async () => {
    await useFrames.getState().attach("live", "live");
    wsMod.emit("static/map", {
      topic: "static/map", rev: REV, seq: 1, game_time: 0, wall_ms: 0,
      payload: { map_name: "M", terrain: null },
    });
    wsMod.emit("static/terrain", {
      topic: "static/terrain", rev: REV, seq: 2, game_time: 0, wall_ms: 0,
      payload: { marker: "T" },
    });
    const s = useFrames.getState();
    expect(s.map?.map_name).toBe("M");
    expect(s.map?.terrain).toEqual({ marker: "T" });
  });
});
