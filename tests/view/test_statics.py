"""view.statics：静态面 —— 真实 catalog / 真实 LadderMap / 真实 flow 词表。

这三条测试守的是契约里最容易悄悄坏掉的三件事：
zh 名有没有下发（前端不做 i18n）、ADR-0027 的换算有没有算好、词表有没有被加工。
"""
from game.catalog import load_terran
from game.geometry import Point2
from tactical_map.base import instantiate_spawn, load_ladder_map
from tactical_map.placement import BuildSlot

from flow.vocab import dump_vocabulary
from view.encode import to_json
from view.statics import catalog_static, map_static, schema_static

#: LadderMap 某个出生点附近的指挥中心位置（取模板里第一个变体的 origin，保证确定性）
def _ladder_layer():
    tpl = load_ladder_map()
    spawn, layout = sorted(tpl.spawns.items())[0]
    cc = Point2(x=layout.origin.x, y=layout.origin.y)
    return spawn, instantiate_spawn(tpl, layout, cc)


# ---------------- catalog ----------------

def test_catalog_static_covers_everything_with_zh_names():
    cat = load_terran()
    out = catalog_static(cat)
    assert len(out.entries) == len(cat.where())
    assert [e.stable_id for e in out.entries] == sorted(e.stable_id for e in cat.where())
    for e in out.entries:
        assert e.display_name_zh, f"{e.stable_id} 缺中文名（前端不做 i18n 字典，红线 C4）"
        assert e.short_name_zh, f"{e.stable_id} 缺短名（B13：地图标签与 chip 字形，U6/C4）"
        assert e.stable_id.count("/") == 1


def test_catalog_static_keeps_footprint_size_for_buildings():
    """建筑必须带 size —— 前端画 footprint 靠它（经 adapt 换算，前端不算）。"""
    out = catalog_static(load_terran())
    by_id = {e.stable_id: e for e in out.entries}
    assert by_id["terran/supplydepot"].size == 2
    assert by_id["terran/barracks"].size == 3
    assert by_id["terran/commandcenter"].size == 5
    assert by_id["terran/marine"].size is None


def test_catalog_static_is_json_serializable():
    d = to_json(catalog_static(load_terran()))
    assert isinstance(d["entries"], list)
    assert isinstance(d["entries"][0]["cost"], dict)


# ---------------- map ----------------

def test_map_static_from_real_ladder_map():
    spawn, layer = _ladder_layer()
    out = map_static(layer, spawn)
    assert out.map_name == layer.map_name
    assert out.size == (layer.size[0], layer.size[1])
    assert out.spawn == spawn
    assert out.terrain is None, "B4 之前不得伪造地形（不静默）"
    assert out.build_slots, "LadderMap 模板应有建造槽位"
    assert out.regions.big, "应有大区"


def test_build_slot_geometry_is_precomputed_adr0027():
    """br / build_point / reported_position 后端算好，且能用权威 helper 往返回 TL。

    断言刻意走 `BuildSlot.tl_from_*` 而不是在测试里重写公式 —— ADR-0027 有**两个**反解
    （命令点 ceil、报告位 floor），测试里抄一份就等于给错误公式做背书。
    """
    spawn, layer = _ladder_layer()
    out = map_static(layer, spawn)
    assert out.build_slots
    for s in out.build_slots:
        assert s.br == (s.tl[0] + s.size - 1, s.tl[1] + s.size - 1)
        # 报告位 → TL 用 floor 反解，必须回到原 TL
        back = BuildSlot.tl_from_reported(Point2(*s.reported_position), s.size)
        assert (back.x, back.y) == s.tl
        # 命令点 → TL 用 ceil 反解（校准过的 pos 也必须落在同一 footprint）
        back2 = BuildSlot.tl_from_pos(Point2(*s.build_point), s.size)
        assert (back2.x, back2.y) == s.tl, f"{s.name} 的命令点不在自己的 footprint 里"
        assert s.kind in {"supply", "production", "addon"}


def test_map_static_sends_label_grid_not_per_region_masks():
    """区域几何是"一张标签网格 + 索引"，不是每区一份 mask（rev 3 的理由）。"""
    spawn, layer = _ladder_layer()
    out = map_static(layer, spawn)
    assert out.regions.big_grid is not None
    assert out.regions.big_grid.w == layer.size[0]
    assert out.regions.big_index, "标签值 → region id 的索引必须下发，否则位图无法解读"
    for leaf in out.regions.leaf:
        assert not hasattr(leaf, "cells"), "leaf 不再带 per-region mask"


# ---------------- schema ----------------

def test_schema_static_mirrors_flow_vocab_verbatim():
    """逐字镜像 flow.vocab —— 不得加工、不得重排、不得补默认值。

    这张表同时喂校验器与 LLM 提示词卡片；前端只是第三个消费方。任何加工都会让
    "UI 画得出、编译不过"重新变成可能。
    """
    v = dump_vocabulary()
    s = schema_static()
    assert s.predicates == v["predicates"]
    assert s.operators == v["operators"]
    assert s.actions == v["actions"]
    assert s.do_ops == v["do_ops"]
    assert s.forbidden == v["forbidden"]
    assert s.declarations == v["declarations"]
    assert s.node_forms == v["node_forms"]
    assert s.rules == v["rules"]


def test_schema_static_forwards_every_vocabulary_key():
    """漂移守卫：`dump_vocabulary()` 以后长出新顶层键时，这条测试会失败，逼前端把它转发出去。

    没有这条守卫，"词表新增了一类约束但 UI 不知道"会静默发生 —— 而后果是编辑器画出
    "看着对、编译不过"的方块，正是 flow/vocab.py 模块注释里要防的那件事。
    """
    from dataclasses import fields

    forwarded = {f.name for f in fields(schema_static())}
    missing = set(dump_vocabulary()) - forwarded
    assert not missing, f"词表新增了 {sorted(missing)}，static/schema 必须一起转发（并 rev+1）"


def test_forbidden_is_an_open_group_table_not_three_fixed_groups():
    """`forbidden` 的分组名不枚举 —— 后端新增一组必须自动流通。

    真实案例：后端加了 `composite_actions`（assign_workers 需要扇出层，ADR-0030 D1）
    与 `step_keys` 两组。整块转发的话 Python 侧自动就有了；如果这里改成固定三组，
    编辑器就会以为那些 op 可用 —— 这正是 rev 5 要防的静默。
    """
    forbidden = schema_static().forbidden
    assert set(forbidden) >= {"predicates", "spatial_tools", "do_ops"}
    assert len(forbidden) >= 5, f"后端目前有 {sorted(forbidden)}，整块转发应全部到位"
    for group, ops in forbidden.items():
        assert isinstance(ops, dict), group
        for op, reason in ops.items():
            assert reason, f"{group}.{op} 缺原因（不静默）"


def test_composite_actions_excluded_from_directly_usable_actions():
    """复合意图不出现在 `actions` 里（后端已在 vocab 层排除），但仍在 forbidden 里带原因。

    所以 AST 编辑器天然不会把 `assign_workers` 当成可直接发的 group_action。
    """
    s = schema_static()
    assert "assign_workers" not in s.actions
    assert "assign_workers" in s.forbidden.get("composite_actions", {})
    # 生产队列侧仍然支持它（QueueOp.ASSIGN_WORKERS）
    assert "assign_workers" in s.queue.ops


def test_schema_static_queue_side_has_reasons():
    """不支持的队列 op 必须带原因（UI 置灰要能解释为什么，不静默）。"""
    s = schema_static()
    assert set(s.queue.ops) >= {"build", "train", "assign_workers"}
    assert s.queue.unsupported_ops, "research/cancel 未实现，必须显式登记"
    for op, reason in s.queue.unsupported_ops.items():
        assert reason, f"{op} 缺原因"
    assert set(s.queue.worker_tasks) == {"mineral", "gas", "idle"}
    assert "group_center" in s.target_kinds


def test_schema_static_marks_value_predicates():
    """value 型谓词能被区分 —— 编辑器据此决定能否放进参数位。"""
    s = schema_static()
    kinds = {name: spec["kind"] for name, spec in s.predicates.items()}
    assert kinds["group_center"] == "value"
    assert kinds["region_center"] == "value"
    assert kinds["group_count"] == "bool"


def test_schema_static_is_json_serializable():
    d = to_json(schema_static())
    assert d["queue"]["worker_tasks"] == ["mineral", "gas", "idle"]
    assert isinstance(d["rules"], list)
