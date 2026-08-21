/**
 * canvas.cluster：单位显示层聚类（F11d）。
 *
 * ⚠️ U18 边界声明：本文件**仅做显示层 LOD 聚合**，任何决策路径（谓词、命令、提案、
 * 投影）不得读取它的输出。后端的 enemy_clusters 才是语义字段（目前恒 null、词表登记
 * forbidden）；显示聚类与它没有任何数据通路 —— 把边界写进代码，防止有人拿显示聚类喂谓词。
 *
 * 纯函数：同类（owner, stable_id 相同）且空间邻近（格哈希 + 连通合并）的单位聚成一簇，
 * 供地图在低缩放下画「枪兵 24」这种 chip。放大超过 LOD 阈值就不再调用它（还原个体）。
 */

export interface ClusterInput {
  tag: number;
  owner: string;
  stable_id: string;
  pos: [number, number];
  group_id?: string | null;
}

export interface UnitCluster {
  owner: string;
  stable_id: string;
  count: number;
  /** 簇心（成员坐标的算术平均；只做显示，不进任何语义） */
  center: [number, number];
  /** 成员 tag（选中/hit-testing 仍按个体；这里只为调试与测试） */
  tags: number[];
  /** 全员同一个 flow 分组时给组标签（与 Flow 页同词），否则 null */
  group_id: string | null;
}

/**
 * 按 (owner, stable_id) 分键，键内做空间连通聚类：
 * 半径 radiusCells 内的单位互相合并（格哈希桶 + BFS 连通分量，确定性：按 tag 排序扫描）。
 */
export function clusterUnits(units: ClusterInput[], radiusCells: number): UnitCluster[] {
  if (radiusCells <= 0) return [];
  const byKey = new Map<string, ClusterInput[]>();
  for (const u of units) {
    const key = `${u.owner}|${u.stable_id}`;
    const arr = byKey.get(key);
    if (arr) arr.push(u);
    else byKey.set(key, [u]);
  }
  const out: UnitCluster[] = [];
  for (const members of byKey.values()) {
    members.sort((a, b) => a.tag - b.tag); // 确定性
    const n = members.length;
    // 格哈希：桶边 = radiusCells，相邻单位至多落在 3x3 邻桶内
    const cell = Math.max(0.0001, radiusCells);
    const buckets = new Map<string, number[]>();
    const bucketOf = (u: ClusterInput): string =>
      `${Math.floor(u.pos[0] / cell)},${Math.floor(u.pos[1] / cell)}`;
    members.forEach((u, i) => {
      const k = bucketOf(u);
      const arr = buckets.get(k);
      if (arr) arr.push(i);
      else buckets.set(k, [i]);
    });
    // 邻近关系（对称）→ 连通分量
    const near: number[][] = Array.from({ length: n }, () => []);
    const r2 = radiusCells * radiusCells;
    for (let i = 0; i < n; i += 1) {
      const a = members[i]!;
      const parts = bucketOf(a).split(",").map(Number);
      const bx = parts[0] ?? 0;
      const by = parts[1] ?? 0;
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          for (const j of buckets.get(`${bx + dx},${by + dy}`) ?? []) {
            if (j <= i) continue; // 只记 i<j，避免重复
            const b = members[j]!;
            const ddx = a.pos[0] - b.pos[0];
            const ddy = a.pos[1] - b.pos[1];
            if (ddx * ddx + ddy * ddy <= r2) {
              near[i]!.push(j);
              near[j]!.push(i);
            }
          }
        }
      }
    }
    const seen = new Uint8Array(n);
    for (let i = 0; i < n; i += 1) {
      if (seen[i]) continue;
      // BFS 收集连通分量
      const comp: number[] = [i];
      seen[i] = 1;
      for (let head = 0; head < comp.length; head += 1) {
        for (const j of near[comp[head]!]!) {
          if (!seen[j]) {
            seen[j] = 1;
            comp.push(j);
          }
        }
      }
      let sx = 0;
      let sy = 0;
      let group: string | null = null;
      let groupSame = true;
      for (const j of comp) {
        const u = members[j]!;
        sx += u.pos[0];
        sy += u.pos[1];
        const g = u.group_id ?? null;
        if (group === null) group = g;
        else if (group !== g) groupSame = false;
      }
      out.push({
        owner: members[i]!.owner,
        stable_id: members[i]!.stable_id,
        count: comp.length,
        center: [sx / comp.length, sy / comp.length],
        tags: comp.map((j) => members[j]!.tag),
        group_id: groupSame ? group : null,
      });
    }
  }
  return out;
}
