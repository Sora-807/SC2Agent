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
  applyDraft, draftStorageKey, loadDraft, mapDraftToHunks, nextMarkName, previewPlacement,
  saveDraft, slotOverlaps, slotTl, snapToCellCenter,
  type MapPlanHunk, type MarkView, type SlotView,
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
    const out = applyDraft(base, [], []);
    expect(out.marks).toHaveLength(2);
    expect(out.marks[0]).toEqual(base[0]);
  });

  it("add → move → rename → del 按序应用", () => {
    const d: MapPlanHunk[] = [
      { kind: "add_mark", name: "new1", pos: [10.5, 20.5] },
      { kind: "move_mark", name: "new1", pos: [11.5, 21.5] },
      { kind: "rename_mark", from: "new1", to: "final" },
      { kind: "del_mark", name: "choke" },
    ];
    const out = applyDraft(base, [], d);
    const byName = new Map(out.marks.map((m) => [m.name, m]));
    expect(byName.get("final")!.pos).toEqual([11.5, 21.5]);
    expect(byName.has("new1")).toBe(false);
    expect(byName.has("choke")).toBe(false);
    expect(byName.has("rally")).toBe(true);
  });

  it("add 同名 = 覆盖位置（重复放置的意图就是'放这里'）", () => {
    const out = applyDraft(base, [], [{ kind: "add_mark", name: "rally", pos: [1.5, 2.5] }]);
    expect(out.marks.find((m) => m.name === "rally")!.pos).toEqual([1.5, 2.5]);
    expect(out.marks).toHaveLength(2);
  });

  it("rename 撞名抛错（UI 必须先查重，模型不静默合并）", () => {
    expect(() => applyDraft(base, [], [{ kind: "rename_mark", from: "rally", to: "choke" }]))
      .toThrow(/已存在/);
  });

  it("move/del 指向不存在的名字 = 幂等忽略", () => {
    const out = applyDraft(base, [], [
      { kind: "move_mark", name: "ghost", pos: [0.5, 0.5] },
      { kind: "del_mark", name: "ghost" },
    ]);
    expect(out.marks).toHaveLength(2);
  });

  it("不修改 base（纯函数）", () => {
    const snapshot = JSON.stringify(base);
    applyDraft(base, [], [{ kind: "move_mark", name: "rally", pos: [9.5, 9.5] }]);
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

describe("槽位草稿（F14 切片 2）", () => {
  const baseSlots: SlotView[] = [
    { name: "depot1", pos: [40.5, 32.5], size: 2, kind: "supply", tl: [40, 32], br: [41, 33] },
    { name: "rax1", pos: [55.5, 33.5], size: 3, kind: "production", tl: [54, 32], br: [56, 34] },
  ];

  it("slotTl 与后端 placement.tl_from_pos 同公式（黄金用例）", () => {
    // 偶数尺寸（2×2）：锚点落格角 → TL = ceil(P - 1)
    expect(slotTl([40.5, 32.5], 2)).toEqual([40, 32]);
    // 奇数尺寸（3×3）：锚点落格心 → TL = ceil(P - 1.5)
    expect(slotTl([55.5, 33.5], 3)).toEqual([54, 32]);
  });

  it("slotOverlaps 含边界（后端 _overlaps 同语义）", () => {
    const a = { tl: [40, 32] as [number, number], br: [41, 33] as [number, number] };
    expect(slotOverlaps(a, a)).toBe(true);
    // 紧邻（不相交）
    expect(slotOverlaps(a, { tl: [42, 32] as [number, number], br: [43, 33] as [number, number] })).toBe(false);
    // 相交
    expect(slotOverlaps(a, { tl: [41, 33] as [number, number], br: [42, 34] as [number, number] })).toBe(true);
  });

  it("add_slot → 投影进 slots；del_slot 删除", () => {
    const d: MapPlanHunk[] = [
      { kind: "add_slot", name: "new1", pos: [20.5, 20.5], size: 2, slotKind: "supply" },
      { kind: "del_slot", name: "depot1" },
    ];
    const out = applyDraft([], baseSlots, d);
    const names = out.slots.map((s) => s.name);
    expect(names).toEqual(["rax1", "new1"]);
    const n = out.slots.find((s) => s.name === "new1")!;
    expect(n.tl).toEqual([20, 20]);
    expect(n.br).toEqual([21, 21]);
  });

  it("add_slot 同名 = 覆盖位置（与 add_mark 同语义）", () => {
    const out = applyDraft([], baseSlots, [
      { kind: "add_slot", name: "depot1", pos: [10.5, 10.5], size: 2, slotKind: "supply" },
    ]);
    expect(out.slots.find((s) => s.name === "depot1")!.pos).toEqual([10.5, 10.5]);
    expect(out.slots).toHaveLength(2);
  });

  it("草稿槽位的 footprint 由 slotTl 现算，不进 hunk（渲染不重算后端已给值）", () => {
    // add_slot 的 hunk 只带锚点 pos + size；tl/br 是投影时算出来的
    const d: MapPlanHunk = { kind: "add_slot", name: "x", pos: [9.5, 9.5], size: 3, slotKind: "production" };
    expect(Object.keys(d)).not.toContain("tl");
  });
});
describe("mapDraftToHunks：草稿 → 提案 hunk（与后端 map_plan 枚举对齐）", () => {
  it("六种 hunk 的 kind 与 payload 字段名对齐后端校验", () => {
    const draft: MapPlanHunk[] = [
      { kind: "add_mark", name: "m1", pos: [1.5, 2.5] },
      { kind: "move_mark", name: "m1", pos: [3.5, 4.5] },
      { kind: "rename_mark", from: "m1", to: "m2" },
      { kind: "del_mark", name: "m2" },
      { kind: "add_slot", name: "s1", pos: [5.5, 6.5], size: 2, slotKind: "supply" },
      { kind: "del_slot", name: "s1" },
    ];
    const hs = mapDraftToHunks(draft);
    expect(hs.map((h) => h.kind)).toEqual([
      "add_mark", "move_mark", "rename_mark", "del_mark", "add_slot", "del_slot",
    ]);
    // add_slot 的 payload 里类别字段叫 kind（后端校验读 p.get("kind")），不是 slotKind
    const slot = hs[4]!;
    expect(slot.payload["kind"]).toBe("supply");
    expect(slot.payload["size"]).toBe(2);
    // 每条都带人类可读的 text_zh（审批时逐条可见）
    expect(hs.every((h) => h.text_zh.length > 0)).toBe(true);
    expect(hs.every((h) => h.id)).toBe(true);
  });
});

describe("previewPlacement（F16：吸附 + 重叠 + placeable，预览与落笔同一结果）", () => {
  const slots = [{
    name: "rax1",
    tl: [10, 10] as [number, number],
    br: [12, 12] as [number, number],
  }];

  it("mark：吸附格心，恒 ok（点位不受地形/重叠约束）", () => {
    const pv = previewPlacement({ mode: "mark", pos: [48.3, 30.9], slots: [] });
    expect(pv.pos).toEqual([48.5, 30.5]);
    expect(pv.ok).toBe(true);
    expect(pv.reason).toBeNull();
  });

  it("slot：吸附格心 + footprint 由 slotTl 公式给出（奇偶尺寸都与后端一致）", () => {
    const odd = previewPlacement({ mode: "slot", pos: [30.2, 30.8], size: 3, slots: [] });
    expect(odd.pos).toEqual([30.5, 30.5]);
    expect(odd.tl).toEqual([29, 29]);       // ceil(30.5 - 1.5) = 29
    expect(odd.br).toEqual([31, 31]);
    const even = previewPlacement({ mode: "slot", pos: [30.2, 30.8], size: 2, slots: [] });
    expect(even.tl).toEqual([30, 30]);      // ceil(30.5 - 1) = 30（偶数尺寸落格角）
    expect(even.br).toEqual([31, 31]);
  });

  it("slot：与既有槽位重叠 → ok=false 且指出撞了谁", () => {
    const pv = previewPlacement({ mode: "slot", pos: [11.4, 11.4], size: 2, slots });
    expect(pv.ok).toBe(false);
    expect(pv.reason).toBe("overlap");
    expect(pv.overlaps).toBe("rax1");
  });

  it("slot：footprint 任一格 placeable=0 → 拒（unplaceable）", () => {
    // 可建区只在 x∈[10,20]；贴右界放 3×3，footprint 右半出界
    const placeableAt = (x: number, y: number): boolean => x >= 10 && x <= 20 && y >= 0;
    const inside = previewPlacement({
      mode: "slot", pos: [12.5, 5.5], size: 2, slots: [], placeableAt,
    });
    expect(inside.ok).toBe(true);
    const edge = previewPlacement({
      mode: "slot", pos: [20.5, 5.5], size: 3, slots: [], placeableAt,
    });
    // 吸附 20.5 → tl=19、br=21：x=21 出界 → 拒
    expect(edge.tl).toEqual([19, 4]);
    expect(edge.ok).toBe(false);
    expect(edge.reason).toBe("unplaceable");
  });

  it("地形未下发（placeableAt=null）不算非法 —— 未知如实降级", () => {
    const pv = previewPlacement({ mode: "slot", pos: [11.4, 11.4], size: 2, slots: [] });
    // 不重叠且地形未知 → ok；placeable 字段如实为 true（语义：未判定，不是判定可建）
    expect(pv.ok).toBe(true);
    expect(pv.reason).toBeNull();
  });

  it("固定建造点预留区不可占用（基地/气井/矿脉）", () => {
    // 背景：SC2 placeable 栅格不含资源占用 —— 预留只能靠矩形数据显式挡
    const reserved: { tl: [number, number]; br: [number, number]; kind: string }[] = [
      { tl: [126, 118], br: [130, 122], kind: "base" },     // 5×5 基地（tr 主基）
      { tl: [10, 10], br: [12, 12], kind: "geyser" },       // 3×3 气井
    ];
    const onBase = previewPlacement({
      mode: "slot", pos: [128.5, 120.5], size: 3, slots: [], reserved,
    });
    expect(onBase.ok).toBe(false);
    expect(onBase.reason).toBe("reserved");
    expect(onBase.reservedKind).toBe("base");
    const onGeyser = previewPlacement({
      mode: "slot", pos: [11.5, 11.5], size: 2, slots: [], reserved,
    });
    expect(onGeyser.reason).toBe("reserved");
    expect(onGeyser.reservedKind).toBe("geyser");
    const beside = previewPlacement({
      mode: "slot", pos: [15.5, 15.5], size: 2, slots: [], reserved,
    });
    expect(beside.ok).toBe(true);
  });
});