/**
 * F11d：单位显示层聚类（U18）。
 *
 * 守的边界：聚类**只做显示**——任何决策路径不得读 cluster.ts 的输出。
 * 这里测的是聚合本身的正确性：同类合并、异类不合并、簇心与计数、组标签。
 */
import { describe, expect, it } from "vitest";
import { clusterUnits, type ClusterInput } from "../src/canvas/cluster";

function u(
  tag: number, owner: string, stable_id: string, x: number, y: number,
  group_id?: string | null,
): ClusterInput {
  return { tag, owner, stable_id, pos: [x, y], group_id: group_id ?? null };
}

describe("clusterUnits", () => {
  it("同类同 owner 邻近单位聚成一簇，count 与簇心正确", () => {
    const units = [
      u(1, "self", "terran/marine", 0, 0),
      u(2, "self", "terran/marine", 1, 0),
      u(3, "self", "terran/marine", 0, 1),
    ];
    const out = clusterUnits(units, 2);
    expect(out).toHaveLength(1);
    expect(out[0]!.count).toBe(3);
    expect(out[0]!.tags.sort()).toEqual([1, 2, 3]);
    expect(out[0]!.center).toEqual([1 / 3, 1 / 3]);
  });

  it("跨 owner / 跨 stable_id 不合并（即使站在同一格）", () => {
    const units = [
      u(1, "self", "terran/marine", 5, 5),
      u(2, "enemy", "terran/marine", 5, 5),
      u(3, "self", "terran/siegetank", 5, 5),
    ];
    const out = clusterUnits(units, 10);
    expect(out).toHaveLength(3);
    expect(out.every((c) => c.count === 1)).toBe(true);
  });

  it("相距超过半径的两群是两簇；链式邻近（A-B、B-C 但 A-C 远）仍连通为一簇", () => {
    const far = clusterUnits([
      u(1, "self", "terran/marine", 0, 0),
      u(2, "self", "terran/marine", 100, 100),
    ], 2);
    expect(far).toHaveLength(2);

    // 半径 2：1-2 距 2（合并）、2-3 距 2（合并）、1-3 距 4（超半径）→ 连通分量仍是 1 个
    const chain = clusterUnits([
      u(1, "self", "terran/marine", 0, 0),
      u(2, "self", "terran/marine", 2, 0),
      u(3, "self", "terran/marine", 4, 0),
    ], 2);
    expect(chain).toHaveLength(1);
    expect(chain[0]!.count).toBe(3);
  });

  it("半径 <= 0 时不聚类（LOD 关闭 = 全部还原个体的前置条件）", () => {
    const out = clusterUnits([
      u(1, "self", "terran/marine", 0, 0),
      u(2, "self", "terran/marine", 0.5, 0),
    ], 0);
    expect(out).toEqual([]);
  });

  it("全员同一 flow 分组 → 簇带组标签；混合 → null（不编造归属）", () => {
    const same = clusterUnits([
      u(1, "self", "terran/marine", 0, 0, "inf"),
      u(2, "self", "terran/marine", 1, 0, "inf"),
    ], 2);
    expect(same[0]!.group_id).toBe("inf");

    const mixed = clusterUnits([
      u(1, "self", "terran/marine", 0, 0, "inf"),
      u(2, "self", "terran/marine", 1, 0, "armor"),
    ], 2);
    expect(mixed[0]!.group_id).toBeNull();
  });

  it("未分组单位混进来 → null，且与遍历顺序无关（不拿 null 当哨兵）", () => {
    // 回归：曾用 `group === null` 判"还没设过"，而 null 也是"没被 lease"的合法值，
    // 于是 [null, "inf"] 报成 "inf"（编造归属），["inf", null] 才正确 —— 结果看顺序。
    const nullFirst = clusterUnits([
      u(1, "self", "terran/marine", 0, 0, null),
      u(2, "self", "terran/marine", 1, 0, "inf"),
    ], 2);
    expect(nullFirst).toHaveLength(1);
    expect(nullFirst[0]!.count).toBe(2);
    expect(nullFirst[0]!.group_id).toBeNull();

    const groupFirst = clusterUnits([
      u(1, "self", "terran/marine", 0, 0, "inf"),
      u(2, "self", "terran/marine", 1, 0, null),
    ], 2);
    expect(groupFirst[0]!.group_id).toBeNull();

    // 全员未分组也是 null（没有组标签可画，不是"有个叫 null 的组"）
    const noneGrouped = clusterUnits([
      u(1, "self", "terran/marine", 0, 0, null),
      u(2, "self", "terran/marine", 1, 0, null),
    ], 2);
    expect(noneGrouped[0]!.group_id).toBeNull();
  });

  it("50 枪兵 + 4 坦克场面：两类各成簇，总数不丢（显示聚合不改变计数）", () => {
    const units: ClusterInput[] = [];
    for (let i = 0; i < 50; i += 1) {
      units.push(u(100 + i, "self", "terran/marine", (i % 7) * 1.5, Math.floor(i / 7) * 1.5));
    }
    for (let i = 0; i < 4; i += 1) {
      units.push(u(200 + i, "self", "terran/siegetank", 50 + i * 1.5, 50));
    }
    const out = clusterUnits(units, 2);
    const marines = out.filter((c) => c.stable_id === "terran/marine");
    const tanks = out.filter((c) => c.stable_id === "terran/siegetank");
    expect(marines.reduce((n, c) => n + c.count, 0)).toBe(50);
    expect(tanks.reduce((n, c) => n + c.count, 0)).toBe(4);
    expect(tanks).toHaveLength(1);   // 4 坦克彼此邻近 → 一簇「坦克 4」
  });
});
