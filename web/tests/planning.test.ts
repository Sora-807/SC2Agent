/**
 * 生产规划草稿（F9）纯函数。
 *
 * 守的纪律：草稿全程本地，成本**只来自 catalog**（缺目录项标出来，不假装免费）；
 * "提为提案"生成的是可应用的 hunk 序列，与后端 `apply_hunks` 的 item 形状一致。
 */
import { describe, expect, it } from "vitest";
import {
  describeItem, draftCost, draftFromJson, draftFromSessionQueues, draftToHunks, emptyItem, itemToJson,
  placementOptions,
  type DraftItem,
} from "../src/planning/queue-draft";
import type { CatalogStatic, MapStatic } from "../src/contract";

const CATALOG: CatalogStatic = {
  entries: [
    {
      stable_id: "terran/supplydepot", display_name_zh: "补给站", short_name_zh: "补给", role: "building",
      capabilities: ["supply"], cost: { minerals: 100, vespene: 0, supply: 0 },
      build_time: 21, produced_by: null, prerequisites: [], size: 2,
      attack_range: null, siege_range: null, burnysc2_name: "SUPPLYDEPOT",
    },
    {
      stable_id: "terran/marine", display_name_zh: "机枪兵", short_name_zh: "枪兵", role: "combat",
      capabilities: ["attack"], cost: { minerals: 50, vespene: 0, supply: 1 },
      build_time: 18, produced_by: "terran/barracks", prerequisites: [], size: null,
      attack_range: 5, siege_range: null, burnysc2_name: "MARINE",
    },
  ],
};

const MAP: MapStatic = {
  map_name: "M", size: [176, 160], spawn: "bl", terrain: null,
  regions: {
    big: [], leaf: [
      { stable_id: "main_build", parent: "main", anchor: [0, 0], display_name_zh: "主建区",
        aliases_zh: [], build_slots: ["rax_1"] },
    ],
    big_grid: null, leaf_grid: null, big_index: {}, leaf_index: {},
  },
  build_slots: [{ name: "rax_1", tl: [0, 0], br: [2, 2], size: 3, kind: "production",
    build_point: [1.5, 1.5], reported_position: [1.5, 1.5] }],
  pos_marks: [{ name: "ramp", pos: [10, 10], description_zh: "楼梯" }],
  resource_nodes: [],
};

function item(patch: Partial<DraftItem>): DraftItem {
  return { ...emptyItem(), op: "build", type: "terran/supplydepot", count: 1,
    placement: { kind: "exact", mark: "rax_1" }, task: null, ...patch };
}

describe("draftCost", () => {
  it("成本只来自 catalog", () => {
    const cost = draftCost([item({}), item({ type: "terran/marine", count: 3 })], CATALOG);
    expect(cost).toEqual({ minerals: 250, vespene: 0, supply: 3, missing: [] });
  });

  it("catalog 没有的项标出来，不假装免费", () => {
    const cost = draftCost([item({ type: "terran/nonexistent" })], CATALOG);
    expect(cost.missing).toEqual(["terran/nonexistent"]);
    expect(cost.minerals).toBe(0);
  });

  it("缺类型也标出来", () => {
    expect(draftCost([item({ type: null })], CATALOG).missing).toEqual(["（缺类型）"]);
  });

  it("维持配额不花资源", () => {
    const cost = draftCost([item({ op: "assign_workers", type: null, task: "gas", count: 3 })], CATALOG);
    expect(cost).toEqual({ minerals: 0, vespene: 0, supply: 0, missing: [] });
  });
});

describe("draftToHunks", () => {
  it("生成的 hunk 与后端 apply_hunks 的 item 形状一致", () => {
    const hunks = draftToHunks([item({ type: "terran/marine", count: 2 })]);
    expect(hunks).toHaveLength(1);
    const h = hunks[0]!;
    expect(h.kind).toBe("insert");
    expect(h.payload.index).toBe(0);
    expect(h.payload.item).toEqual(itemToJson(item({ type: "terran/marine", count: 2 })));
    expect(itemToJson(item({ type: "terran/marine", count: 2 }))).toEqual({
      op: "build", type: "terran/marine", count: 2,
      placement: { kind: "exact", mark: "rax_1" }, task: null,
    });
  });
});

describe("draftFromJson", () => {
  it("规划文件队列项 ↔ 草稿项往返：op/type/count/placement/task 全保真", () => {
    const draft = [
      item({ op: "build", type: "terran/supplydepot", count: 2,
             placement: { kind: "in_region", region: "home", index: null } }),
      item({ op: "train", type: "terran/marine", count: 6, placement: null }),
      item({ op: "research", type: "terran/infantryweapons1", count: 1, placement: null }),
      item({ op: "assign_workers", type: null, count: 3, placement: null, task: "gas" }),
    ];
    const back = draftFromJson(draft.map(itemToJson));
    expect(back.map(({ id, ...rest }) => rest)).toEqual(draft.map(({ id, ...rest }) => rest));
  });

  it("research 项不丢（默认规划里有 Research 导出项，编辑器必须能装下）", () => {
    const [first] = draftFromJson([
      { op: "research", type: "terran/infantryweapons1", count: 1,
        placement: null, task: null },
    ]);
    expect(first?.op).toBe("research");
    expect(first?.type).toBe("terran/infantryweapons1");
  });

  it("count 缺省回退 1、未知 op 回退 build（文件里来的数据不假设形状完好）", () => {
    const [a, b] = draftFromJson([
      { op: "train", type: "terran/marine" },
      { op: " morph ", type: "x", count: 2 },
    ]);
    expect(a?.count).toBe(1);
    expect(b?.op).toBe("build");
  });

  it("null 给空草稿", () => {
    expect(draftFromJson(null)).toEqual([]);
  });
});

describe("placementOptions", () => {
  it("槽位 / 点位 / 区域三类都列出来", () => {
    const opts = placementOptions(MAP);
    expect(opts.map((o) => o.value)).toEqual([
      "slot:rax_1", "mark:ramp", "region:main_build",
    ]);
  });

  it("没地图时给空列表", () => {
    expect(placementOptions(null)).toEqual([]);
  });

  it("I8：给了 qualifyId 就把槽位/点位限定成「规划id/名」，预设名不带前缀", () => {
    const withReserved = { ...MAP, reserved: [
      { name: "蓝方主矿气井1", label_zh: "气井" },
      { name: null, label_zh: "矿脉" },          // 矿脉不命名 → 不进选项
    ] };
    const opts = placementOptions(withReserved, "agent-m1");
    expect(opts.map((o) => o.value)).toEqual([
      "slot:agent-m1/rax_1", "mark:agent-m1/ramp",
      "mark:蓝方主矿气井1",                      // 预设固定建造点 = 全局命名空间
      "region:main_build",
    ]);
  });
});

describe("describeItem", () => {
  it("把草稿项说成人话（用于 hunk 的 text_zh）", () => {
    expect(describeItem(item({}))).toBe("build terran/supplydepot ×1 @槽位 rax_1");
    expect(describeItem(item({ op: "assign_workers", task: "gas", type: null, count: 3 })))
      .toBe("维持 gas 3 人");
    expect(describeItem(item({ type: null }))).toContain("（选类型）");
  });
});

// ---------------- 复盘队列 → 规划草稿（十八轮：复盘生产页复用规划编辑器） ----------------

describe("draftFromSessionQueues", () => {
  const item = (index: number, op: string, type: string | null, extra: Record<string, unknown> = {}) => ({
    index, op, stable_id: type, count: 1, status: "未处理", ...extra,
  });

  it("多队列按全局 index 摊平成单序列（还原规划顺序）", () => {
    const draft = draftFromSessionQueues([
      { items: [item(1, "train", "scv"), item(3, "build", "barracks")] },
      { items: [item(0, "build", "depot"), item(2, "train", "scv")] },
    ]);
    expect(draft.map((d) => d.type)).toEqual(["depot", "scv", "scv", "barracks"]);
  });

  it("cancel 是会话运行期产物，不进规划草稿", () => {
    const draft = draftFromSessionQueues([
      { items: [item(0, "build", "depot"), item(1, "cancel", null)] },
    ]);
    expect(draft.map((d) => d.type)).toEqual(["depot"]);
  });

  it("placement/task 原样带回（会话里已解析成具体槽位/区域）", () => {
    const draft = draftFromSessionQueues([{
      items: [
        item(0, "build", "refinery", {
          placement: { kind: "exact", mark: "蓝方主矿气井1" },
        }),
        item(1, "assign_workers", null, { task: "gas", count: 3 }),
      ],
    }]);
    expect(draft[0]!.placement).toEqual({ kind: "exact", mark: "蓝方主矿气井1" });
    expect(draft[1]!.op).toBe("assign_workers");
    expect(draft[1]!.task).toBe("gas");
    expect(draft[1]!.count).toBe(3);
  });

  it("空队列 → 空草稿", () => {
    expect(draftFromSessionQueues([{ items: [] }, { items: [] }])).toEqual([]);
    expect(draftFromSessionQueues([])).toEqual([]);
  });
});
