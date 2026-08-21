/**
 * 地图规划草稿模型（F14 切片 1）—— applyDraft 是纯函数投影，行为必须钉死。
 *
 * 语义约定（与 map-draft.ts 头注释一致）：
 * - add 同名 = 覆盖位置；rename 撞名**抛错**（UI 先查重）；move/del 指向不存在 = 幂等忽略；
 * - 吸附 = 格心（世界坐标左下原点，(cx,cy) 格中心 = (cx+0.5, cy+0.5)）。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

// node 环境没有 localStorage：stub 一个内存实现（行为与浏览器一致：同键覆盖、坏值原样存）
const memStore = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => memStore.get(k) ?? null,
  setItem: (k: string, v: string) => { memStore.set(k, v); },
  removeItem: (k: string) => { memStore.delete(k); },
});
afterEach(() => memStore.clear());
import {
  applyDraft, draftStorageKey, loadDraft, nextMarkName, saveDraft, snapToCellCenter,
  type MapPlanHunk, type MarkView,
} from "../src/planning/map-draft";

const base: MarkView[] = [
  { name: "rally", pos: [48.5, 30.5], description_zh: "集合点" },
  { name: "choke", pos: [60.5, 44.5] },
];

describe("snapToCellCenter", () => {
  it("吸附到所在格的格心（左下原点）", () => {
    expect(snapToCellCenter([48.6, 30.2])).toEqual([48.5, 30.5]);
    expect(snapToCellCenter([48.5, 30.5])).toEqual([48.5, 30.5]);   // 已在格心 = 不动
    expect(snapToCellCenter([-0.1, 0.9])).toEqual([-0.5, 0.5]);
  });
});

describe("applyDraft", () => {
  it("空草稿 = 原样投影（含 description）", () => {
    const out = applyDraft(base, []);
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual(base[0]);
  });

  it("add → move → rename → del 按序应用", () => {
    const d: MapPlanHunk[] = [
      { kind: "add_mark", name: "new1", pos: [10.5, 20.5] },
      { kind: "move_mark", name: "new1", pos: [11.5, 21.5] },
      { kind: "rename_mark", from: "new1", to: "final" },
      { kind: "del_mark", name: "choke" },
    ];
    const out = applyDraft(base, d);
    const byName = new Map(out.map((m) => [m.name, m]));
    expect(byName.get("final")!.pos).toEqual([11.5, 21.5]);
    expect(byName.has("new1")).toBe(false);
    expect(byName.has("choke")).toBe(false);
    expect(byName.has("rally")).toBe(true);
  });

  it("add 同名 = 覆盖位置（重复放置的意图就是'放这里'）", () => {
    const out = applyDraft(base, [{ kind: "add_mark", name: "rally", pos: [1.5, 2.5] }]);
    expect(out.find((m) => m.name === "rally")!.pos).toEqual([1.5, 2.5]);
    expect(out).toHaveLength(2);
  });

  it("rename 撞名抛错（UI 必须先查重，模型不静默合并）", () => {
    expect(() => applyDraft(base, [{ kind: "rename_mark", from: "rally", to: "choke" }]))
      .toThrow(/已存在/);
  });

  it("move/del 指向不存在的名字 = 幂等忽略", () => {
    const out = applyDraft(base, [
      { kind: "move_mark", name: "ghost", pos: [0.5, 0.5] },
      { kind: "del_mark", name: "ghost" },
    ]);
    expect(out).toHaveLength(2);
  });

  it("不修改 base（纯函数）", () => {
    const snapshot = JSON.stringify(base);
    applyDraft(base, [{ kind: "move_mark", name: "rally", pos: [9.5, 9.5] }]);
    expect(JSON.stringify(base)).toBe(snapshot);
  });
});

describe("nextMarkName", () => {
  it("跳过已占用的名字", () => {
    expect(nextMarkName(["mark_1", "mark_3", "rally"])).toBe("mark_2");
    expect(nextMarkName([])).toBe("mark_1");
  });
});

describe("草稿持久化", () => {
  it("键带地图名（换图 = 换草稿）", () => {
    expect(draftStorageKey("LadderMap")).toBe("map-plan-draft:LadderMap");
  });

  it("save → load 往返；坏数据回空草稿（不猜半份）", () => {
    const map = { map_name: "T" } as never;
    const d: MapPlanHunk[] = [{ kind: "add_mark", name: "a", pos: [1.5, 2.5] }];
    saveDraft(map, d);
    expect(loadDraft(map)).toEqual(d);

    memStore.set(draftStorageKey("T"), "{bad json");
    expect(loadDraft(map)).toEqual([]);

    memStore.set(draftStorageKey("T"), JSON.stringify([{ kind: "not_a_hunk" }]));
    expect(loadDraft(map)).toEqual([]);
  });
});