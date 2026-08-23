"""view.schema：ViewFrame v0.1 数据模型（契约 rev 2）。

**唯一真相源是 `docs/plan-frontend.md` §2**（消费方定义、本模块实现）。字段名必须逐字一致 ——
前端有 zod 逐字段校验，任何改名都会在那里当场炸，而不是变成静默的 undefined。

依赖红线：本文件**只依赖 game**（几何/枚举），不 import flow/production/planner/tactical_map。
"从引擎取数"隔离在 view.adapt / view.statics，这样 schema 可以独立于任何重构演进。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 契约版本。改任何字段 = REV+1 + §2 修订 + 前端同步（红线 C8）。
#: rev 10：B12+B13 一轮两字段（F11 地图视觉语言需要）：
#:   ① `frame/economy.nodes[]` 增 `base_tag`（节点归属基地 = 最近的己方 dropoff 建筑 tag）——
#:     前端把它与 frame/world.units 按 tag 直接 join，主基地标签才能写「矿 12/16 气 3/6」，
#:     不需要在 TS 里做任何空间匹配（A3：派生量后端算）。
#:   ② `static/catalog.entries[]` 增 `short_name_zh`（≤2 字中文短名）—— footprint 内标签与
#:     单位聚类 chip 的字形。后端加字段而不是前端截断 display_name_zh（PLAN §12 结论 2：
#:     U6/C4 规定 zh 文案一律来自后端，前端截断会在别的词上出洋相）。
#: rev 9：新增 topic `static/terrain`（B4：driver 从 game_info 导出地形）。
#:   它是**事件式静态面**：`static/map` 先到（地形为 null），game_info 就绪后地形帧补到。
#:   不进 `static/map` 的原因是驱动顺序 —— 真机上 game_info 在 bot 第一个 on_step 才可用，
#:   而 static/map 在那之前就得发出去。
#: rev 8：`frame/projection` 增 `skipped`；`source.kind="live_queue"` 终于有真值。
#:   缺口修好了：`Planner.project` 与 `ProductionRuntime` 之前没有互转（一个吃
#:   ProductionModuleInstance、一个执行 QueueItem），所以"当前队列的投影"产不出来。
#:   `view.projection` 补上这条桥（并让 `planner.expand` 透传裸 Op）。
#:   `skipped` 是"不静默"：队列里有项投不了（如 cancel）必须让用户看见，
#:   否则投影会悄悄少算一段。
#: rev 7：新增 topic `static/strategy` —— 策略图结构（steps/branches/edges/声明节）。
#:   `frame/flow` 只有"现在在哪个 step"，**图本身不在任何帧里**，F4 的状态图与 F9 的 AST 编辑器
#:   都没法画。它每个 flow 版本只变一次，所以归静态面；hot-edit（S8）落地后再改成事件驱动。
#:   刻意**原样下发 branches 的值树**（不摊平）：F4 只需要 step/edge，但 F9 的编辑器需要完整 AST，
#:   摊平一次就得再补一条通道。
#: rev 6：新增 topic `frame/economy`（ADR-0030 的经济维持器已落地 → 原 B11 预留转为实装）。
#:   payload 按 `EconomyKeeper.snapshot()` **实际能产出什么**校准：`emitted` 改成 `emitted_count`
#:   （维持器只记条数不记内容）、去掉 `retask`（防抖帧未暴露，不为它改人家刚写的文件）。
#: rev 5：`forbidden` 定为**开放分组表**（`dict[组名, {op: 原因}]`）而不是固定三组。
#:   后端刚给它加了 `composite_actions` 与 `step_keys` 两组：Python 侧因为整块转发自动拿到了，
#:   但前端 zod 把它写成封闭对象会**静默 strip** 新分组 —— 编辑器就会以为那些 op 可用。
#:   这正是 rev 2"逐字镜像、不加工"该有的样子：连分组名都不枚举。
#: rev 4（B1 落地）：按后端**实际能产出什么**校准三处 ——
#:   ① StrategyView 增 eval_diagnostics（引擎本来就在记"条件其实没求出来"，不暴露等于丢掉一个功能）；
#:   ② QueueItemView.status 收窄为 队首阻塞/未处理，并删 resolved_point
#:      （队首门控语义下，已发出的项已出队或进了 in_flight，队列里不可能有"已发出"）；
#:   ③ InFlightView 删 timeout_frames/confirmed（前者要复制一份魔法公式、后者恒 False），
#:      改增 queue + attempted_slots（摆放调试叠加要画"试过哪几个槽位"）。
#: rev 3：区域几何改为"一张标签网格 + 索引"（原 leaf[].cells 的 per-region mask 不可扩展）。
#: rev 2：static/schema 逐字镜像 flow.vocab.dump_vocabulary()；production 队列增 blocked。
#: rev 10：B12/B13 —— frame/economy.nodes[] 增 base_tag；static/catalog 增 short_name_zh。
#: rev 11：B14 —— 提案 hunks.kind 枚举增 map_plan 的六种操作（add_mark/move_mark/
#:   rename_mark/del_mark/add_slot/del_slot）；preview.map_overlay 增 changed_marks。
#: rev 12：策略可读性（I1/I2/I4 一轮）：
#:   ① `static/schema` 的 predicates/operators/actions 每项**值内**加 `name_zh`
#:     （rev 5 先例：值内加键也走 REV+1 —— 前端 zod 逐字段校验，不认的键会被静默 strip）；
#:   ② `static/strategy` 增 `display_name_zh`/`description_zh`（策略级）、
#:     `steps[].display_name_zh`/`description_zh`（step 级）、`reasons`（reason 标识符→中文，
#:     edges 切换原因与 exit 终局原因共用）、`group_names`（group_id→中文，来自 assembly）。
#:     目的：`formup/advance/G_INF` 这类裸标识符读不出意图（用户原话），zh 单一真相源在后端（C4）。
#: rev 13：`world.units[].producing[].progress` 收窄为可空 —— SC2 订单不带进度（协议没有该
#:   字段），原先恒发 `0.0` 是把"未知"伪装成"刚开始"；改为 None（前端 zod 同步 nullable）。
REV = 13

Pt = tuple[float, float]      # 世界坐标（左下原点浮点）
Cell = tuple[int, int]        # 建筑格点

TOPICS = (
    "static/map", "static/catalog", "static/schema", "static/strategy",
    "static/terrain",
    "frame/session", "frame/world", "frame/flow", "frame/production",
    "frame/economy", "frame/ops", "frame/projection", "frame/alerts", "proposals",
)
STATIC_TOPICS = ("static/map", "static/catalog", "static/schema", "static/strategy",
                  "static/terrain")


@dataclass(slots=True)
class Envelope:
    """帧信封。`game_time` 是唯一时间基准（游戏秒）；`wall_ms` 仅诊断，不参与语义。"""

    topic: str
    seq: int
    game_time: float
    wall_ms: int
    payload: Any
    rev: int = REV


# ---------------- 通用 ----------------

@dataclass(slots=True)
class GridB64:
    """栅格：行主序 data[y][x] 的 uint8 + base64。禁发嵌套数组（红线 C5）。"""

    w: int
    h: int
    data_b64: str
    bpp: int = 8


# ---------------- static/map ----------------

@dataclass(slots=True)
class TerrainView:
    """地形三图。任一可为 None：game_info 里三张图的可用性不保证一致，
    缺哪张画哪张（不伪造全 0 网格）。"""

    height: GridB64 | None
    pathable: GridB64 | None
    placeable: GridB64 | None


@dataclass(slots=True)
class BigRegionView:
    stable_id: str
    anchor: Pt
    display_name_zh: str
    aliases_zh: list[str]
    children: list[str]


@dataclass(slots=True)
class LeafRegionView:
    stable_id: str
    parent: str
    anchor: Pt
    display_name_zh: str
    aliases_zh: list[str]
    build_slots: list[str]


@dataclass(slots=True)
class RegionsView:
    """区域几何用**一张标签网格 + 索引**下发，而不是每个区域一份 mask。

    直接镜像 `tactical_map.RegionLayer` 的模型，也是唯一可扩展的形状：
    LadderMap 是 176×160=28160 格，一份 mask ≈37KB；20 个区域按 mask 发就是 750KB，
    按标签网格发始终只有 37KB。前端画区域时是"一张位图 + 一份调色板"，也更简单。
    索引键在 JSON 里是字符串（网格里存的是 int 标签）。
    """

    big: list[BigRegionView]
    leaf: list[LeafRegionView]
    big_grid: GridB64 | None
    leaf_grid: GridB64 | None
    big_index: dict[str, str]     # 标签值 -> big region stable_id
    leaf_index: dict[str, str]    # 标签值 -> leaf region stable_id


@dataclass(slots=True)
class BuildSlotView:
    """br / build_point / reported_position 由**后端**按 ADR-0027 算好下发。

    前端零几何换算（红线 C2）：那套"偶数尺寸命令点 = TL+(size-1)/2 而非 tl+size/2"
    在真机上反复踩坑，TS 里绝不允许出现第二份。
    """

    name: str
    tl: Cell
    br: Cell
    size: int
    kind: str          # supply / production / addon
    build_point: Pt
    reported_position: Pt


@dataclass(slots=True)
class PosMarkView:
    name: str
    pos: Pt
    description_zh: str


@dataclass(slots=True)
class ResourceNodeView:
    tag: int
    stable_id: str
    pos: Pt
    kind: str          # mineral / geyser


@dataclass(slots=True)
class MapStatic:
    map_name: str
    size: tuple[int, int]
    spawn: str
    #: B4 之后真机有值；`static/terrain` 晚到时由**前端合并**（不是重发 static/map）
    terrain: TerrainView | None
    regions: RegionsView
    build_slots: list[BuildSlotView]
    pos_marks: list[PosMarkView]
    resource_nodes: list[ResourceNodeView]


# ---------------- static/catalog ----------------

@dataclass(slots=True)
class CostView:
    minerals: int
    vespene: int
    supply: int


@dataclass(slots=True)
class CatalogEntryView:
    stable_id: str
    display_name_zh: str
    short_name_zh: str             # ≤2 字短名（B13）：地图 footprint 标签与聚类 chip 字形
    role: str
    capabilities: list[str]
    cost: CostView
    build_time: float
    produced_by: str | None
    prerequisites: list[str]
    size: int | None                # footprint 格边长（建筑）
    attack_range: float | None
    siege_range: float | None
    burnysc2_name: str              # 仅允许调试面板显示；语义一律用 stable_id（红线 C1）


@dataclass(slots=True)
class CatalogStatic:
    entries: list[CatalogEntryView]


# ---------------- static/schema ----------------

@dataclass(slots=True)
class QueueSchemaView:
    ops: list[str]
    unsupported_ops: dict[str, str]   # op -> 原因（不静默）
    worker_tasks: list[str]


@dataclass(slots=True)
class SchemaStatic:
    """前 8 个字段**逐字**来自 `flow.vocab.dump_vocabulary()`，不得加工/重排/补默认值。

    那张表同时喂校验器与 LLM 提示词卡片，前端只是第三个消费方；任何加工都会让
    "UI 画得出、编译不过"重新变成可能。
    """

    predicates: dict[str, dict]       # name -> {name_zh, params:[{name,required}], kind:"value"|"bool"}
    operators: dict[str, dict]        # name -> {name_zh, min_args, max_args|None}
    actions: dict[str, dict]          # name -> {name_zh, params:[{name,type,required}]}
    do_ops: list[str]
    #: 开放分组表：`{组名: {op: 原因}}`。**不枚举组名** —— 后端新增一组（如
    #: composite_actions / step_keys）时自动流通，不需要改契约。
    forbidden: dict[str, dict[str, str]]
    declarations: dict[str, list[str]]
    node_forms: dict[str, str]
    rules: list[str]
    queue: QueueSchemaView
    target_kinds: list[str]


# ---------------- static/strategy ----------------

@dataclass(slots=True)
class StepView:
    """一个 step 的原始定义。

    `branches` **原样带值树**（`when` 的 AST、`do` 的动作列表都不摊平）：
    F4 的状态图只用到 step_id 与出口，但 F9 的 AST 编辑器需要完整结构 ——
    摊平一次就得再补一条通道，而且两条通道迟早不一致。
    `display_name_zh`/`description_zh`（rev 12，I2）："" = 没写，UI 退回 step_id。
    """

    step_id: str
    branches: list[dict]
    display_name_zh: str = ""
    description_zh: str = ""
    # 不带 `locals`：step 局部变量在编译期被拒（UNIMPLEMENTED_STEP_KEYS，与 set_local 对称），
    # 所以那会是个恒空的死字段。T8 放回 timer/local 时一并加。


@dataclass(slots=True)
class EdgeView:
    """step 之间的转移边。

    `(kind, reason)` 必须与某个 `exit_step` 匹配（编译期已校验：没有对应 exit_step 的边是死边）。
    UI 在边上显示 reason —— 这就是 ADR-0023 §2.4 要的"悬停显示切换原因"。
    """

    from_step: str
    to: str
    kind: str
    reason: str

    RENAME = {"from_step": "from"}


@dataclass(slots=True)
class StrategyStatic:
    """策略图结构（每个 flow 版本只变一次）。

    与 `frame/flow` 的分工：这里是**图**（不变的结构），那里是**状态**（每帧的位置）。
    合在一起才画得出"当前在哪个节点、从哪条边过来的、还有哪些没走过"。
    """

    id: str
    version: int
    group_slots: list[str]
    #: 参数/变量的**声明**（type + default），不是运行时的值（值在 frame/flow）
    params: dict[str, dict]
    variables: dict[str, dict]
    definitions: dict[str, Any]
    initial_step: str
    steps: list[StepView]
    edges: list[EdgeView]
    loop_limits: dict[str, int]
    #: assembly 侧：实例绑定（slot → group_id），画图时标注每个 slot 落在哪个组
    bindings: dict[str, str]
    #: rev 12（I2/I4）：可读名与 reason 中文创 —— 全部 "" / 空 = 没写，UI 退回 identifier
    display_name_zh: str = ""
    description_zh: str = ""
    #: reason 标识符 → 中文（FORMED → 成型）；edges 的切换原因与 exit 的终局原因共用
    reasons: dict[str, str] = field(default_factory=dict)
    #: group_id → 中文（G_INF → 步兵组），来自 assembly 的 GroupSpec.display_name_zh
    group_names: dict[str, str] = field(default_factory=dict)


# ---------------- frame/session ----------------

@dataclass(slots=True)
class SessionFrame:
    state: str            # 未连接 / 启动中 / 对局中 / 已结束 / 崩溃
    frame_source: str     # live / replay / fixture
    map_name: str | None
    my_race: str | None
    enemy_race: str | None
    game_time: float
    error: str | None


# ---------------- frame/world ----------------

@dataclass(slots=True)
class EconomyView:
    minerals: int
    vespene: int
    supply_used: int
    supply_cap: int


@dataclass(slots=True)
class OrderView:
    atom: str | None       # 映射到 OP_CATALOG 的原子名；映射不出则 None（不猜）
    ability_raw: str       # SC2 原生能力名（诊断用）
    target_tag: int | None
    target_pos: Pt | None
    is_auto: bool


@dataclass(slots=True)
class FootprintView:
    tl: Cell
    br: Cell


@dataclass(slots=True)
class ProducingView:
    stable_id: str
    progress: float | None    # SC2 订单不带进度 → None（rev 13；曾恒发假 0.0）


@dataclass(slots=True)
class UnitView:
    tag: int
    stable_id: str          # 已由后端翻译并归一变体；禁出现 burnysc2 名（红线 C1）
    form: str | None        # 形态变体（"sieged"）；None = 主形态
    owner: str
    pos: Pt
    facing: float
    hp: float
    hp_max: float
    shield: float
    energy: float
    build_progress: float
    group_id: str | None    # flow 分组归属，后端 join
    order: OrderView | None
    footprint: FootprintView | None
    producing: list[ProducingView] | None
    addon: str | None       # reactor / techlab
    carrying: str | None    # minerals / vespene
    buffs: list[str]


@dataclass(slots=True)
class EnemyClusterView:
    center: Pt
    radius: float
    count: int
    by_stable_id: dict[str, int]


@dataclass(slots=True)
class ResourceStateView:
    tag: int
    remaining: float | None
    workers: int


@dataclass(slots=True)
class GridsView:
    creep: GridB64 | None = None
    visibility: GridB64 | None = None


@dataclass(slots=True)
class WorldFrame:
    economy: EconomyView
    units: list[UnitView]
    enemy_clusters: list[EnemyClusterView] | None   # 聚类未实现 → None，前端降级
    resource_state: list[ResourceStateView]
    grids: GridsView | None                          # 仅变化时下发


# ---------------- frame/flow ----------------

@dataclass(slots=True)
class BranchHitView:
    step_id: str
    branch_id: str | None
    index: int


@dataclass(slots=True)
class TransitionView:
    from_step: str
    to: str
    kind: str | None
    reason: str | None
    at: float

    #: 契约里字段名是 `from`（Python 保留字）→ 编码时改名，见 encode.FIELD_RENAMES
    RENAME = {"from_step": "from"}


@dataclass(slots=True)
class EvalDiagnosticView:
    """求值诊断（引擎 H6）：比较遇 None 降级为 False 等路径的留痕。

    引擎的模块注释写着"UI/agent/真机日志读它就知道条件其实没求出来" —— 不暴露就等于
    白记了。调试页（F5）显示它。
    """

    step_id: str
    kind: str
    detail: str
    count: int


@dataclass(slots=True)
class StrategyView:
    instance_id: str
    strategy_ref: str
    version: int
    params: dict
    variables: dict
    locals: dict
    definitions: dict
    active_step: str
    step_entered_at: float
    step_elapsed: float
    step_entry_count: int
    branch_hit: BranchHitView | None
    transitions: list[TransitionView]
    transition_count: int
    transition_limit: int
    done: bool
    exit_record: dict | None
    bindings: dict[str, str]
    eval_diagnostics: list[EvalDiagnosticView]


@dataclass(slots=True)
class CompositionView:
    min: int
    target: int
    max: int
    current: int


@dataclass(slots=True)
class GroupView:
    group_id: str
    composition: dict[str, CompositionView]
    refill_state: str      # 满足 / 滞回区 / 补兵中 / 已截断（后端按滞回语义判定，前端不复算）
    leased_tags: list[int]
    center: Pt | None
    hp_ratio: float | None


@dataclass(slots=True)
class FlowFrame:
    strategies: list[StrategyView]   # V1 恒长度 1；列表形状为多实例预留（红线 C6）
    groups: list[GroupView]


# ---------------- frame/production ----------------

@dataclass(slots=True)
class QueueItemView:
    index: int
    op: str
    stable_id: str | None
    count: int
    placement: dict | None    # {kind:"exact",mark} | {kind:"in_region",region,index}
    task: str | None
    # 只有两种：队首阻塞 / 未处理。队首门控语义下，已发出的项已出队或进了 in_flight，
    # 所以"已发出/在途"不会出现在队列里（rev 4 校准）。
    status: str
    block_reason: str | None


@dataclass(slots=True)
class BlockedView:
    """队首阻塞详情（来自 ProductionRuntime.blocked）。

    `warned` = 已持续超过后端 STALL_WARN_SECS 阈值。阈值判断在后端，前端只显示（红线 C3）。
    """

    reason: str
    since: float
    waited: float
    warned: bool


@dataclass(slots=True)
class QueueView:
    name: str
    head_status: str          # 空 / 可执行 / 阻塞
    blocked: BlockedView | None
    items: list[QueueItemView]


@dataclass(slots=True)
class InFlightView:
    """在途建造确认（真机教训：SC2 对非法放置位静默丢弃命令）。

    `builder_tag is None` = 上帧失败、本帧待重试。
    `attempted_slots` 给摆放调试叠加层用：画出"这个建筑已经试过哪几个槽位"。
    """

    queue: str
    stable_id: str
    kind: str                 # build / addon / gas
    builder_tag: int | None
    expect_pos: Pt | None
    radius: float
    frames_waited: int
    retries: int
    attempted_slots: list[str]


@dataclass(slots=True)
class DroppedView:
    #: 后端丢弃 QueueItem 时未记 game_time → None，UI 显示"未知"而不是编一个时间（不静默）
    at: float | None
    op: str
    stable_id: str | None
    reason: str


@dataclass(slots=True)
class ProductionFrame:
    queues: list[QueueView]
    in_flight: list[InFlightView]
    dropped: list[DroppedView]


# ---------------- frame/economy ----------------

@dataclass(slots=True)
class EconomyTaskView:
    """一个采集任务的配额 / 可达目标 / 实际人数。

    **三个数都要给**，因为它们回答的是不同问题：
    - `quota`：有人显式设过的绝对配额（None = 没设，按 per-node 比例）。它是**持久**的 ——
      "精炼厂还没建好就设了 gas 6"时 quota=6 会一直挂着（这正是 issues P9 的修复点）；
    - `target`：本帧**可达**目标（被节点容量与领地人数夹紧；没精炼厂时 gas target=0）；
    - `actual`：本帧真在这个任务上的人数（从 orders 派生）。

    只给 target 的话，用户看到"气目标 0"会以为意图又蒸发了 —— 恰好与 P9 的修复相反。
    语义是**维持 N 个**（ADR-0030 D2），不是"再派 N 个"：UI 文案必须写"维持"。
    注意气优先于矿（D4.3）：设了 gas 配额会挤掉矿的可达目标。
    """

    task: str            # mineral / gas / idle
    quota: int | None
    target: int
    actual: int


@dataclass(slots=True)
class EconomyNodeView:
    """一个资源节点的饱和度。`capacity` 由后端给（矿 2 / 气 3），前端不硬编码。

    `base_tag`（B12）= 归属基地的 unit tag（最近的己方 dropoff 建筑），与 frame/world.units
    按 tag 直接 join；无基地时为 None。"""

    tag: int
    kind: str            # mineral / gas
    workers: int
    capacity: int
    saturated: bool
    base_tag: int | None


@dataclass(slots=True)
class EconomyReservationView:
    """建造征用（ADR-0030 D3.3）：`owner` 如 "production/build#3"。

    有了它，"抢走正在建造的 SCV"从"碰巧没发生"变成结构性不可能（issues P14）。
    """

    tag: int
    owner: str


@dataclass(slots=True)
class EconomyFrame:
    """经济维持器的收敛状态（ADR-0030 D4.6）。

    它是"SCV 去哪了、为什么闲置"的唯一解释来源：目标/实际/差量并排看才解释得清
    "采空矿自动回收"这类收敛副作用。`emitted_count == 0` 就是验收 6 的"稳定态零命令"。
    """

    tasks: list[EconomyTaskView]
    nodes: list[EconomyNodeView]
    reserved: list[EconomyReservationView]
    #: 本帧发出的差量条数。维持器只记条数不记内容，所以这里不是列表（rev 6 校准）
    emitted_count: int
    #: 维持器领地大小（没被战斗组租用、也没被征用的工兵数）
    domain_workers: int
    quotas: dict[str, int]


# ---------------- frame/ops ----------------

@dataclass(slots=True)
class ApplyView:
    #: ok=None = 已受理、待裁决（异步应用未回）—— 这是"Operation 下一 step 生效"的如实反映
    ok: bool | None
    failed: bool
    detail: str | None


@dataclass(slots=True)
class LandingView:
    kind: str                 # confirmed / timeout / retried / unknown
    at: float | None
    detail: str | None


@dataclass(slots=True)
class OpView:
    op_id: int
    seq: int
    at: float
    action: str
    unit_tags: list[int]
    params: dict
    origin: str               # flow / production / worker / user / agent（RecordingPort 打标）
    apply: ApplyView | None   # B9（D6）之前恒 None → 前端显示"未知"而非空白
    landing: LandingView | None


@dataclass(slots=True)
class OpsFrame:
    ops: list[OpView]


# ---------------- frame/projection ----------------

@dataclass(slots=True)
class ProjectionPointView:
    t: float
    minerals: float
    gas: float
    supply_used: int
    supply_cap: int
    mineral_workers: int
    gas_workers: int
    buildings: dict[str, int]
    units: dict[str, int]
    in_flight_count: int


@dataclass(slots=True)
class ProjectionEventView:
    kind: str                 # started / completed / stalled
    stable_id: str | None
    t: float
    reason: str | None


@dataclass(slots=True)
class ProjectionFrame:
    based_on_seq: int
    based_on_game_time: float
    horizon: float
    source: dict              # {kind:"live_queue",queue_name} | {kind:"draft",plan_id}
    points: list[ProjectionPointView]
    events: list[ProjectionEventView]
    #: 队列里投不进来的项 + 原因（如 `cancel`）。不静默：否则投影会悄悄少算一段
    skipped: list[dict] = field(default_factory=list)


# ---------------- frame/alerts ----------------

@dataclass(slots=True)
class AlertView:
    id: str
    kind: str
    severity: str             # info / warn / error
    at: float
    eta: float | None
    text_zh: str              # 文案由后端生成（红线 C4：前端无 i18n 字典）
    source: str               # projection / production / session
    payload: dict


@dataclass(slots=True)
class AlertsFrame:
    alerts: list[AlertView]


# ---------------- proposals（B7） ----------------

@dataclass(slots=True)
class HunkView:
    id: str
    kind: str                 # insert / delete / reorder / modify
    text_zh: str
    payload: dict


@dataclass(slots=True)
class ProposalView:
    """草稿提案：**agent 的唯一产出面**（不是 UI 专用结构）。

    agent 写 flow / 改队列 / 划区域全部经此通道，`author="agent"`；
    `rationale_zh` 对 agent 是强制字段（空理由不可接受），拒绝理由回流作为下一轮输入。
    """

    id: str
    author: str               # agent / user
    created_at: float
    anchor: dict | None       # {seq, game_time}；过期即"已失效"，禁止盲接受
    kind: str
    title_zh: str
    rationale_zh: str
    target: dict
    hunks: list[HunkView]
    validation: dict | None
    preview: dict | None
    status: str               # 待审批 / 已接受 / 部分接受 / 已拒绝 / 已失效
    decision: dict | None


@dataclass(slots=True)
class ProposalsFrame:
    #: 允许直接放 `ProposalStore.list()` 的 dict 列表（它已经是契约形态；
    #: 再套一层 dataclass 只是为了让类型检查开心，实际会多一次无意义的搬运）
    proposals: list[ProposalView] | list[dict] = field(default_factory=list)