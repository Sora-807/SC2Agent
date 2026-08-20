"""view.statics：静态面（每局一次）—— static/map、static/catalog、static/schema。

三条都只做**转发与几何换算**，不做业务判断：
- catalog：把 CatalogEntry 摊平（含 zh 名 —— 前端因此不需要 i18n 字典，红线 C4）；
- map：把 RegionLayer 摊平，并按 ADR-0027 把 BuildSlot 的 br/build_point/reported_position
  **算好再下发**（红线 C2：TS 里绝不允许出现第二份换算）；
- schema：`flow.vocab.dump_vocabulary()` **逐字**下发 + 生产/目标解析侧闭集。
  逐字的理由见 flow/vocab.py 的模块注释：那张表同时喂校验器与 LLM 提示词卡片，
  前端只是第三个消费方；任何加工都会让"UI 画得出、编译不过"重新变成可能。
"""
from __future__ import annotations

from game.catalog import Catalog
from game.geometry import Grid
from game.production import QueueOp, WorkerTask

from flow.vocab import dump_vocabulary
from production.runtime import UNSUPPORTED_QUEUE_OPS
from tactical_map.region import RegionLayer

from view.encode import grid_to_b64
from view.schema import (
    BigRegionView,
    EdgeView,
    StepView,
    StrategyStatic,
    BuildSlotView,
    CatalogEntryView,
    CatalogStatic,
    CostView,
    LeafRegionView,
    MapStatic,
    PosMarkView,
    QueueSchemaView,
    RegionsView,
    ResourceNodeView,
    SchemaStatic,
    TerrainView,
)

#: 目标解析类型（tactical_map.resolver 支持的形态；spec-003 §4 的 target 解析表）
TARGET_KINDS = ("point", "region", "group_center", "nearest_enemy")

#: 中性资源类型的 stable id（catalog 不收中性物，这里给前端一个稳定名用于图标区分）
_MINERAL_STABLE = "neutral/mineralfield"
_GEYSER_STABLE = "neutral/vespenegeyser"


def catalog_static(catalog: Catalog) -> CatalogStatic:
    """全量目录（按 stable_id 排序，便于前端稳定渲染与 diff）。"""
    entries = [
        CatalogEntryView(
            stable_id=e.stable_id,
            display_name_zh=e.display_name_zh,
            role=e.role.value,
            capabilities=list(e.capabilities),
            cost=CostView(minerals=e.cost.minerals, vespene=e.cost.vespene, supply=e.cost.supply),
            build_time=float(e.build_time),
            produced_by=e.produced_by,
            prerequisites=list(e.prerequisites),
            size=e.size,
            attack_range=e.attack_range,
            siege_range=e.siege_range,
            burnysc2_name=e.burnysc2_name,
        )
        for e in sorted(catalog.where(), key=lambda x: x.stable_id)
    ]
    return CatalogStatic(entries=entries)


def map_static(
    layer: RegionLayer,
    spawn: str,
    *,
    terrain: TerrainView | None = None,
    resource_nodes: list[ResourceNodeView] | None = None,
) -> MapStatic:
    """RegionLayer → static/map。

    `terrain` 在 B4（driver 导出 game_info）之前一律 None，前端降级为纯色底 —— 这是"不静默"：
    宁可让 UI 明确显示"地形未下发"，也不要发一张假的全 0 网格让人以为地图是平的。
    """
    big = [
        BigRegionView(
            stable_id=r.stable_id,
            anchor=(r.anchor.x, r.anchor.y),
            display_name_zh=r.display_name_zh,
            aliases_zh=list(r.aliases_zh),
            children=sorted(r.children),
        )
        for r in sorted(layer.big_regions.values(), key=lambda x: x.stable_id)
    ]
    leaf = [
        LeafRegionView(
            stable_id=r.stable_id,
            parent=r.parent,
            anchor=(r.anchor.x, r.anchor.y),
            display_name_zh=r.display_name_zh,
            aliases_zh=list(r.aliases_zh),
            build_slots=list(r.build_slots),
        )
        for r in sorted(layer.regions.values(), key=lambda x: x.stable_id)
    ]
    slots = [_slot_view(bs) for bs in sorted(layer.build_slots.values(), key=lambda x: x.name)]
    marks = [
        PosMarkView(name=m.name, pos=(m.pos.x, m.pos.y), description_zh=m.description_zh)
        for m in sorted(layer.pos_marks.values(), key=lambda x: x.name)
    ]
    return MapStatic(
        map_name=layer.map_name,
        size=(int(layer.size[0]), int(layer.size[1])),
        spawn=spawn,
        terrain=terrain,
        regions=RegionsView(
            big=big,
            leaf=leaf,
            big_grid=_grid_or_none(layer.big_grid),
            leaf_grid=_grid_or_none(layer.leaf_grid),
            big_index={str(k): v for k, v in sorted(layer.big_index.items())},
            leaf_index={str(k): v for k, v in sorted(layer.leaf_index.items())},
        ),
        build_slots=slots,
        pos_marks=marks,
        resource_nodes=list(resource_nodes or []),
    )


def resource_nodes_from_state(resources) -> list[ResourceNodeView]:
    """GameState.resources（world 拆出的中性矿脉/气井）→ 静态面的资源点。

    位置不变所以进静态面；**剩余量与采集人数是动态的**，走 `frame/world.resource_state`。
    """
    out: list[ResourceNodeView] = []
    for u in resources:
        is_geyser = "GEYSER" in u.type_name.upper()
        out.append(
            ResourceNodeView(
                tag=u.tag,
                stable_id=_GEYSER_STABLE if is_geyser else _MINERAL_STABLE,
                pos=(u.position.x, u.position.y),
                kind="geyser" if is_geyser else "mineral",
            )
        )
    return sorted(out, key=lambda r: r.tag)


def schema_static() -> SchemaStatic:
    """flow 词表逐字 + 生产/目标解析侧闭集。"""
    v = dump_vocabulary()
    return SchemaStatic(
        predicates=v["predicates"],
        operators=v["operators"],
        actions=v["actions"],
        do_ops=v["do_ops"],
        forbidden=v["forbidden"],
        declarations=v["declarations"],
        node_forms=v["node_forms"],
        rules=v["rules"],
        queue=QueueSchemaView(
            ops=[op.value for op in QueueOp],
            unsupported_ops={op.value: reason for op, reason in UNSUPPORTED_QUEUE_OPS.items()},
            worker_tasks=[t.value for t in WorkerTask],
        ),
        target_kinds=list(TARGET_KINDS),
    )


# ---------------- 内部 ----------------

def _slot_view(bs) -> BuildSlotView:
    """ADR-0027 的换算一律在这里做完（前端零几何换算）。"""
    return BuildSlotView(
        name=bs.name,
        tl=(bs.tl.x, bs.tl.y),
        br=(bs.br.x, bs.br.y),
        size=bs.size,
        kind=bs.kind,
        build_point=(bs.build_point.x, bs.build_point.y),
        reported_position=(bs.reported_position.x, bs.reported_position.y),
    )


def _grid_or_none(g: Grid | None):
    return None if g is None else grid_to_b64(g)

def strategy_static(manifest, assembly) -> StrategyStatic:
    """StrategyManifest + FlowAssembly → static/strategy。

    纯转发：steps 的 `branches` 值树原样带出（F9 的 AST 编辑器需要完整结构），
    edges 的 `from` 因为是 Python 保留字，在 schema 里叫 `from_step`、编码时改名。
    """
    steps = [
        StepView(step_id=step_id, branches=list(step.get("branches", [])))
        for step_id, step in manifest.steps.items()
    ]
    edges = [
        EdgeView(from_step=e["from"], to=e["to"], kind=e["kind"], reason=e["reason"])
        for e in manifest.edges
    ]
    instance = assembly.strategy_instances[0]
    return StrategyStatic(
        id=manifest.id,
        version=int(manifest.version),
        group_slots=list(manifest.group_slots),
        params=dict(manifest.params),
        variables=dict(manifest.variables),
        definitions=dict(manifest.definitions),
        initial_step=manifest.initial_step,
        steps=steps,
        edges=edges,
        loop_limits=dict(manifest.loop_limits),
        bindings=dict(instance.bindings),
    )
