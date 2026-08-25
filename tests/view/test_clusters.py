"""view.clusters 直测（I39 批 A：spread 离散度——组心坐标无法区分「聚齐」与「拖线」）。"""
import pytest

from view.clusters import cluster_units


def _u(x, y, sid="terran/marine", hp=45.0, hp_max=45.0):
    return {"x": x, "y": y, "stable_id": sid, "hp": hp, "hp_max": hp_max}


@pytest.mark.parametrize(("pts", "want"), [
    ([(3.0, 4.0)], 0.0),                                        # 单单位 = 0（约定）
    ([(0.0, 0.0), (2.0, 0.0)], 1.0),                            # 两点对望 → 各距心 1
    ([(0.0, 0.0), (0.0, 2.0), (2.0, 0.0), (2.0, 2.0)], 2 ** 0.5),   # 方阵四角 → 各距心 √2
    ([(0.0, 0.0), (0.0, 0.0), (4.0, 0.0)], 16 / 9),             # 质心偏向两人堆（4/3,4/3,8/3 的均值）
], ids=["单单位", "两点", "方阵", "两人堆+拖一个"])
def test_cluster_spread_is_mean_distance_to_centroid(pts, want):
    clusters = cluster_units([_u(x, y) for x, y in pts])
    assert len(clusters) == 1
    # payload 与 center/hp 同精度四舍五入到 0.1 格，容差随rounding放宽
    assert clusters[0]["spread"] == pytest.approx(want, abs=0.06)


def test_far_units_split_into_own_clusters_each_zero():
    """相距 40 格不聚（CLUSTER_RADIUS=5）——各自成簇，spread=0。"""
    clusters = cluster_units([_u(0, 0), _u(40, 40)])
    assert len(clusters) == 2
    assert all(c["spread"] == 0.0 for c in clusters)


def test_realistic_marching_column_reports_large_spread():
    """行军纵队（拖成一条线）：组心数字看不出，spread 直接给量级。
    8 点等距 2.5（线长 17.5），对均心的平均距离 = 5.0（离散精确值）。"""
    column = [(10.0 + i * 2.5, 20.0) for i in range(8)]
    (c,) = cluster_units([_u(x, y) for x, y in column])
    assert c["spread"] == pytest.approx(5.0, abs=0.05)
