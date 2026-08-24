"""会话图层合并（PLAN-V2 批 2，ADR-0033）行为锁：一份图层三种解析。

- 默认规划裸名 = home 区槽位表（自动放置只吃默认份，声明序）；
- 全部规划 `规划id/名字` 命名空间键（显式引用，热切默认不影响它）；
- 预设固定点全局裸名；同一出生端平移（所有规划同 spawn_key 分支到同一 CC）。
"""
from pathlib import Path

import yaml
from game import Point2
from game.catalog import load_all
from tactical_map.merge import load_plan_templates, merged_layer
from tactical_map.mine_areas import load_mine_areas
from tactical_map.reserved import reserved_marks

CAT = load_all()

BL_ORIGIN = [48.5, 28.5]
TR_ORIGIN = [131.5, 127.5]


def _write(dir: Path, pid: str, *, slots: dict, marks: dict | None = None,
           spawns: tuple = ("bl", "tr")) -> None:
    """双分支规划文件（bl/tr 的槽位故意错开一单位，验证分支选择正确）。"""
    s = {}
    for side in spawns:
        ox, oy = (BL_ORIGIN if side == "bl" else TR_ORIGIN)
        s[side] = {
            "origin": [ox, oy], "anchor": [ox, oy],
            "build_slots": {n: {"pos": [ox + i * 2.0, oy], "size": 2, "kind": "supply"}
                            for i, n in enumerate(slots)} if side == "bl" else
                           {n: {"pos": [ox - i * 2.0, oy], "size": 2, "kind": "supply"}
                            for i, n in enumerate(slots)},
            "pos_marks": marks or {},
        }
    (dir / f"{pid}.yaml").write_text(yaml.safe_dump({
        "id": pid, "title_zh": pid, "map_name": "LadderMap", "spawns": s}, allow_unicode=True),
        encoding="utf-8")


def _merged(tmp_path, default_id, spawn="bl", cc=(48.5, 28.5)):
    templates = load_plan_templates(tmp_path)
    return merged_layer(templates, default_id, spawn, Point2(*cc),
                        reserved_marks=reserved_marks(CAT))


def test_default_bare_names_are_the_auto_consume_surface(tmp_path):
    _write(tmp_path, "alpha", slots={"D1", "D2"})
    _write(tmp_path, "beta", slots={"D9"})
    m = _merged(tmp_path, "alpha")
    home = m.layer.regions["home"]
    # home 区槽位表 = 默认规划的裸名（自动放置 null=auto 的消费面）—— 不含 beta
    assert set(home.build_slots) == {"D1", "D2"}
    assert m.default_id == "alpha" and m.spawn_key == "bl"


def test_all_plans_addressable_by_namespaced_keys(tmp_path):
    _write(tmp_path, "alpha", slots={"D1"})
    _write(tmp_path, "beta", slots={"D9"})
    m = _merged(tmp_path, "alpha")
    names = set(m.layer.build_slots)
    assert {"D1", "alpha/D1", "beta/D9"} <= names, "含默认自己的命名空间键（热切不换走显式引用）"
    # 命名空间键可被 PlacementExact 直接解析（带斜杠的 mark 原样可查）
    assert m.layer.build_slots["beta/D9"].size == 2


def test_factory_fallback_when_default_missing(tmp_path):
    _write(tmp_path, "alpha", slots={"D1"})
    m = _merged(tmp_path, "nope")            # 不存在 → 出厂模板兜底，默认身份清空
    assert m.default_id is None
    assert len(m.layer.regions["home"].build_slots) > 0   # 出厂 bl 布局
    assert "alpha/D1" in m.layer.build_slots               # 其余规划照常合并


def test_spawn_branch_picks_same_side_for_all_plans(tmp_path):
    """同一出生端平移：tr 视图用每个规划的 tr 分支（各自的世界坐标直接可用）。"""
    _write(tmp_path, "alpha", slots={"D1"})
    m = _merged(tmp_path, "alpha", spawn="tr", cc=tuple(TR_ORIGIN))
    # tr 分支的槽位在 tr 附近（没有从 bl 平移过来的错位坐标）
    d1 = m.layer.build_slots["alpha/D1"]
    assert d1.pos.x > 120, f"tr 分支槽位应在右上，拿到 {d1.pos}"
    assert m.spawn_key == "tr"


def test_reserved_marks_are_global_bare_names(tmp_path):
    _write(tmp_path, "alpha", slots={"D1"})
    m = _merged(tmp_path, "alpha")
    assert "蓝方主矿气井1" in m.layer.pos_marks, "预设固定点与装载哪份规划无关"


def test_single_branch_legacy_file_still_loads(tmp_path):
    """单分支旧格式（spawn: + 平铺）在合并层里只贡献自己那一侧的命名空间键。"""
    (tmp_path / "legacy.yaml").write_text(yaml.safe_dump({
        "id": "legacy", "title_zh": "旧格式", "map_name": "LadderMap",
        "spawn": "bl", "origin": BL_ORIGIN, "anchor": BL_ORIGIN,
        "build_slots": {"D5": {"pos": [50.5, 30.5], "size": 2, "kind": "supply"}},
        "pos_marks": {}}), encoding="utf-8")
    m = _merged(tmp_path, None)
    assert "legacy/D5" in m.layer.build_slots
    m_tr = _merged(tmp_path, None, spawn="tr", cc=tuple(TR_ORIGIN))
    assert "legacy/D5" not in m_tr.layer.build_slots, "旧格式没有 tr 分支：tr 侧没有它的份"


def test_mine_areas_table_loads_with_user_draft(tmp_path):
    """矿区基础数据（D4）：六矿区草案表可载、contains 判定正确（批 4 验收基准）。"""
    areas = load_mine_areas()
    assert [a.name for a in areas] == ["蓝方主矿", "蓝方二矿", "中岛矿",
                                       "红方主矿", "红方二矿", "红方三矿"]
    assert {a.side for a in areas} == {"bl", "tr", "neutral"}
    blue_main = areas[0]
    assert blue_main.contains(44, 32) and not blue_main.contains(60, 32)
    red_main = next(a for a in areas if a.name == "红方主矿")
    assert red_main.contains(114, 70)
