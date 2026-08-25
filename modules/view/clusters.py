"""view.clusters：单位就近聚类（PLAN-V2 批 4 —— EnemyClusterView 的算法单点）。

网格桶聚类（桶边长 = CLUSTER_RADIUS）：同桶/邻桶归一簇（并查集），
簇心 = 成员均值，血量% = 均值、绝对血量 = 总和。own+enemy 通用 ——
模板里「集群1/集群2」与 `敌方：` 前缀行都吃这里的输出。
"""
from __future__ import annotations

CLUSTER_RADIUS = 5.0   # 格；≈ 一个部队编队的散布半径


def cluster_units(items: list[dict]) -> list[dict]:
    """[(x, y, stable_id, hp, hp_max), ...]（dict 键同名）→ 簇列表（按簇心排序）。

    返回 {center: (x, y), count, by_stable_id: {sid: n}, hp_pct: 均值, hp_total: 总和}。
    """
    if not items:
        return []
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets: dict[tuple[int, int], list[int]] = {}
    for i, it in enumerate(items):
        key = (int(it["x"] // CLUSTER_RADIUS), int(it["y"] // CLUSTER_RADIUS))
        buckets.setdefault(key, []).append(i)
    for (bx, by), idxs in buckets.items():
        for dx in (0, 1):
            for dy in (0, 1):
                for i in idxs:
                    for j in buckets.get((bx + dx, by + dy), []):
                        if i < j:
                            union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idxs in groups.values():
        members = [items[i] for i in idxs]
        cx = sum(m["x"] for m in members) / len(members)
        cy = sum(m["y"] for m in members) / len(members)
        by_sid: dict[str, int] = {}
        for m in members:
            by_sid[m["stable_id"]] = by_sid.get(m["stable_id"], 0) + 1
        hp_pct = (sum(m["hp"] / m["hp_max"] for m in members if m["hp_max"]) / len(members)
                  if any(m["hp_max"] for m in members) else None)
        # 离散度（I39）：成员到簇心的平均欧氏距离（格）。组心+数量看不出
        # 「聚齐成团」还是「拖线行军」——转 attack 前要不要等聚齐就靠它判。
        spread = sum(((m["x"] - cx) ** 2 + (m["y"] - cy) ** 2) ** 0.5
                     for m in members) / len(members)
        out.append({
            "center": (round(cx, 1), round(cy, 1)),
            "count": len(members),
            "by_stable_id": by_sid,
            "hp_pct": round(hp_pct * 100, 1) if hp_pct is not None else None,
            "hp_total": round(sum(m["hp"] for m in members), 1),
            "spread": round(spread, 1),
        })
    out.sort(key=lambda c: (-c["count"], c["center"]))
    return out
