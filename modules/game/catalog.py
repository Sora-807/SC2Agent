"""game.catalog：单位/建筑数据注册表（从 JSON 加载，只读查询）。

提供稳定 ID ↔ burnysc2 UnitTypeId 名 的映射（game 不 import sc2；driver 负责名→枚举）。
mechanics/constraint/生产运行时 都查这个。

数据词汇分层（防裸 str/dict 漂移）：
- 结构性闭集（代码按值分支）→ 枚举：Role
- 结构化小数据 → dataclass：Cost
- 数据驱动词表（新值 = 改 JSON，不改代码）→ str + 加载校验：stable_id / capabilities
- SC2 镜像词表 → 不透明 str：burnysc2_name
加载边界校验在 register() 内：非法值当场 ValueError（R7：上层降级告警，不静默带病运行）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# 稳定 ID 的族前缀（两段式 race/name 的第一段；加族 = 架构级变更，不是数据变更）
KNOWN_RACES: frozenset[str] = frozenset({"terran", "protoss", "zerg", "neutral"})

# 能力标签词表（代码按标签查询，如 where(capability=...)；未知标签加载时报错，防拼写漂移）
# 加新能力 = 这里登记一条 + JSON 使用上（引入新查询语义时动代码是合理的）
KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    "gather",     # 采矿（如 SCV）
    "build",      # 建造建筑（如 SCV）
    "repair",     # 修理（如 SCV）
    "attack",     # 攻击（如 机枪兵）
    "move",       # 可受命移动（如 机枪兵/医疗运输机）
    "heal",       # 治疗（如 医疗运输机）
    "transport",  # 运输装载（如 医疗运输机）
    "train",      # 训练单位（如 指挥中心/兵营）
    "dropoff",    # 资源上交点（如 指挥中心）
    "supply",     # 提供补给（如 补给站）
    "addon",      # 挂件（反应堆/科技实验室；由母建筑自建，贴右下 2×2）
    "gas",        # 气矿建筑（精炼厂；建在气井上）
    "research",   # 研究升级（工程站/军械库；planner Research op 的产建筑）
})


class Role(str, Enum):
    """单位/建筑的结构性角色（闭集：代码按角色分支，加值 = 架构级变更）。

    更细的兵种划分（army/caster/transport…）走 capabilities 数据层，不进这里。
    """

    WORKER = "worker"      # 工兵：采矿/建造/修理（如 SCV）
    COMBAT = "combat"      # 战斗单位：攻击/施法/运输（如 机枪兵、医疗运输机）
    BUILDING = "building"  # 建筑：生产/供给（如 指挥中心、补给站）
    UPGRADE = "upgrade"    # 升级（如 步兵武器1/装甲1；planner Research op 产出，不占 GameState 实体）


@dataclass(frozen=True, slots=True)
class Cost:
    """生产/建造的资源成本（constraint/mechanics/planner 对它做算术）。

    注：Zerg 幼虫成本属种族机制，由 mechanics 层处理，不进这里（ADR-0002 §4）。
    """

    minerals: int = 0   # 晶体矿成本（如 机枪兵 = 50）
    vespene: int = 0    # 高能瓦斯成本（如 医疗运输机 = 100）
    supply: int = 0     # 补给占用（如 机枪兵 = 1；建筑 = 0）


@dataclass(slots=True)
class CatalogEntry:
    """一个单位/建筑类型的数据目录条目。"""

    stable_id: str          # 稳定类型 ID：两段式 race/name（如 "terran/marine"；注册表键）
    burnysc2_name: str      # burnysc2 UnitTypeId 的 .name（如 "MARINE"；driver 负责名→枚举）
    display_name_zh: str    # 中文显示名（如 "机枪兵"；只用于展示/输入解析，不进运行时语义）
    short_name_zh: str      # 中文短名（≤2 字，如 "枪兵"；地图 footprint 内标签与聚类 chip 用，B13）
    role: Role              # 结构性角色：worker/combat/building（代码按它分支的闭集枚举）
    capabilities: tuple[str, ...]  # 能力标签词表（如 ("gather","build")；where(capability=...) 查询）
    cost: Cost              # 资源成本（晶体矿/瓦斯/补给；见 Cost）
    build_time: int         # 建造/训练时间（游戏秒；如 机枪兵 18、兵营 46）
    produced_by: str | None # 产出建筑 stable ID（如 marine 由 "terran/barracks" 产；起始建筑 = None）
    prerequisites: list[str]  # 前置 stable ID 列表（如 barracks 需 ["terran/supplydepot"]）
    size: int | None = None  # footprint 格边长（建筑：depot=2/兵营=3/基地=5；非建筑=None；放置 slot 按它过滤）
    # 挂件专用（真机锁定）：实体类型是父建筑专属（BARRACKSREACTOR 等），但建造能力是通用
    # BUILD_REACTOR/BUILD_TECHLAB（burnysc2 的 per-parent 能力如 BUILD_REACTOR_BARRACKS 被接受却无实体产出；
    # Unit.build() 也因 creation_ability 为 None 静默返回 False）。driver 按 build_ability 发命令。
    build_ability: str | None = None    # 通用建造能力 burnysc2 名（仅挂件条目填；如 "BUILD_REACTOR"）
    build_order_name: str | None = None  # 母建筑执行建造时的订单按钮名（如 "Reactor"；在途确认用）
    attack_range: float | None = None    # 常规射程（坦克未架起 5；None=无攻击/不参与射程门；T6 真机锁定）
    siege_range: float | None = None      # 架起射程（坦克 13；架起门用 0.8×siege_range=10.4）
    variants: tuple[str, ...] = ()        # 形态变体 burnysc2 名（SIEGETANK → ("SIEGETANKSIEGED",)；T3 反查归一化主名）


class Catalog:
    """只读注册表：stable_id ↔ burnysc2_name 双向查询 + 按 role/capability 过滤。"""

    def __init__(self) -> None:
        self._by_stable: dict[str, CatalogEntry] = {}
        self._by_burnysc2: dict[str, CatalogEntry] = {}
        self._variant_index: dict[str, str] = {}  # 变体 burnysc2 名 → 主名（T3 归一化反查）

    def register(self, stable_id: str, data: dict) -> None:
        """登记一条（加载边界校验：非法数据当场 ValueError，R7 上层降级告警）。"""
        if stable_id.count("/") != 1 or stable_id.split("/", 1)[0] not in KNOWN_RACES:
            raise ValueError(
                f"非法 stable_id {stable_id!r}：需 race/name 两段，race ∈ {sorted(KNOWN_RACES)}"
            )
        if stable_id in self._by_stable:
            raise ValueError(f"重复注册 stable_id {stable_id!r}")
        try:
            role = Role(data.get("role", ""))
        except ValueError:
            raise ValueError(
                f"{stable_id}: 未知 role {data.get('role')!r}（合法值 {[r.value for r in Role]}）"
            ) from None
        capabilities = tuple(data.get("capabilities") or [])
        unknown = set(capabilities) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(
                f"{stable_id}: 未登记 capability {sorted(unknown)}（KNOWN_CAPABILITIES 里没有）"
            )
        cost_data = data.get("cost")
        if not isinstance(cost_data, dict) or not {"minerals", "vespene", "supply"} <= cost_data.keys():
            raise ValueError(f"{stable_id}: cost 必须含 minerals/vespene/supply 三个字段")
        cost = Cost(
            minerals=int(cost_data["minerals"]),
            vespene=int(cost_data["vespene"]),
            supply=int(cost_data["supply"]),
        )
        if "build_time" not in data:
            raise ValueError(f"{stable_id}: 缺字段 build_time")
        burnysc2_name = data.get("burnysc2_name")
        if not burnysc2_name:
            raise ValueError(f"{stable_id}: 缺字段 burnysc2_name")
        short_name_zh = data.get("short_name_zh")
        if not short_name_zh:
            # B13：短名是契约字段（static/catalog），缺了前端 footprint 标签就没有等价替代品
            raise ValueError(f"{stable_id}: 缺字段 short_name_zh（≤2 字中文短名，地图标签用）")
        build_ability = data.get("build_ability")
        build_order_name = data.get("build_order_name")
        # 挂件必须给通用建造能力（真机锁定：实体类型是 BARRACKSREACTOR 等父建筑专属名，
        # 用实体名拼 BUILD_* 不存在；缺 build_ability 的挂件条目 = 无法下发的坏数据）
        if "addon" in capabilities and not build_ability:
            raise ValueError(f"{stable_id}: addon 挂件条目必须提供 build_ability（通用建造能力名）")
        attack_range = data.get("attack_range")
        if attack_range is not None:
            attack_range = float(attack_range)
            if attack_range < 0:
                raise ValueError(f"{stable_id}: attack_range 不能为负数")
        siege_range = data.get("siege_range")
        if siege_range is not None:
            siege_range = float(siege_range)
            if siege_range < 0:
                raise ValueError(f"{stable_id}: siege_range 不能为负数")
        variants = tuple(data.get("variants") or [])
        e = CatalogEntry(
            stable_id=stable_id,
            burnysc2_name=burnysc2_name,
            display_name_zh=data.get("display_name_zh", stable_id),
            short_name_zh=short_name_zh,
            role=role,
            capabilities=capabilities,
            cost=cost,
            build_time=int(data["build_time"]),
            size=data.get("size"),
            produced_by=data.get("produced_by"),
            prerequisites=list(data.get("prerequisites") or []),
            build_ability=build_ability,
            build_order_name=build_order_name,
            attack_range=attack_range,
            siege_range=siege_range,
            variants=variants,
        )
        if e.burnysc2_name in self._by_burnysc2:
            raise ValueError(f"{stable_id}: burnysc2_name {e.burnysc2_name!r} 已被占用")
        self._by_stable[stable_id] = e
        self._by_burnysc2[e.burnysc2_name] = e
        # 变体归一反向索引（T3）：变体名 → 主名；同变体被两个主名声明 = 坏数据
        for v in e.variants:
            prior = self._variant_index.get(v)
            if prior is not None and prior != e.burnysc2_name:
                raise ValueError(f"变体 {v!r} 同时被 {prior!r} 与 {e.burnysc2_name!r} 声明为主名")
            self._variant_index[v] = e.burnysc2_name

    def by_stable_id(self, stable_id: str) -> CatalogEntry | None:
        """按稳定 ID 查条目（如 "terran/marine" → CatalogEntry）。"""
        return self._by_stable.get(stable_id)

    def by_burnysc2_name(self, name: str) -> CatalogEntry | None:
        """按 burnysc2 枚举名查条目（如 "MARINE" → CatalogEntry）。"""
        return self._by_burnysc2.get(name)

    def where(self, role: Role | str | None = None, capability: str | None = None) -> list[CatalogEntry]:
        """按结构性角色 / 能力标签过滤（如 where(role=Role.WORKER)、where(capability="train")）。"""
        if isinstance(role, str) and not isinstance(role, Role):
            role = Role(role)  # 字符串归一化；非法值直接 ValueError（fail fast）
        return [
            e for e in self._by_stable.values()
            if (role is None or e.role is role)
            and (capability is None or capability in e.capabilities)
        ]

    def stable_id_for(self, burnysc2_name: str) -> str | None:
        """burnysc2 枚举名 → 稳定 ID（driver 抽取层用；未知名 → None）。"""
        e = self._by_burnysc2.get(burnysc2_name)
        return e.stable_id if e else None

    def burnysc2_name_for(self, stable_id: str) -> str | None:
        """稳定 ID → burnysc2 枚举名（driver 翻译层用；未知 ID → None）。"""
        e = self._by_stable.get(stable_id)
        return e.burnysc2_name if e else None

    def normalize_burnysc2_name(self, name: str) -> str:
        """burnysc2 名归一（T3）：变体名（如 SIEGETANKSIEGED）→ 主名（SIEGETANK）；
        主名/未知名原样返回。消费方见 flow.predicates.unit_is_type（stable id → 主名单侧归一）。"""
        return self._variant_index.get(name, name)


def load_terran() -> Catalog:
    """从 data/terran.json 加载 Terran catalog（加载失败直接抛，不静默降级）。"""
    cat = Catalog()
    data_path = Path(__file__).parent / "data" / "terran.json"
    with data_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for stable_id, entry_data in data.items():
        cat.register(stable_id, entry_data)
    return cat


def _load_race(cat: Catalog, race: str) -> None:
    """从 data/{race}.json 追加加载一个种族（加载失败直接抛）。"""
    data_path = Path(__file__).parent / "data" / f"{race}.json"
    with data_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for stable_id, entry_data in data.items():
        cat.register(stable_id, entry_data)


def load_protoss() -> Catalog:
    """从 data/protoss.json 加载 Protoss catalog。"""
    cat = Catalog()
    _load_race(cat, "protoss")
    return cat


def load_zerg() -> Catalog:
    """从 data/zerg.json 加载 Zerg catalog。"""
    cat = Catalog()
    _load_race(cat, "zerg")
    return cat


def load_all() -> Catalog:
    """加载三族 catalog（terran + protoss + zerg）。

    对手是 Computer(Race.Random)，catalog 加载时还不知道对手种族，
    必须三族同时加载才能 by_burnysc2_name 查到任意种族的单位。
    """
    cat = Catalog()
    for race in KNOWN_RACES:
        _load_race(cat, race)
    return cat


# ---------------- 中立物名称分类（ISSUES I25 单一事实源） ----------------

#: 中立物 type 名关键词 → stable_id 尾段（小写匹配、子串即可）。
#: 按模式判、不枚举 type 名白名单：SC2 可破坏障碍物每张图自带几十种子类型
#: （DESTRUCTIBLEROCK*/DEBRIS*/RAMP*/COLLAPSIBLE*/...），白名单永远漏。
_NEUTRAL_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mineral", "mineralfield"),
    ("geyser", "vespenegeyser"),
    ("xelnagatower", "xelnagatower"),
    # 可破坏障碍物（岩石/残骸/斜坡/墙/门/可崩塌塔子类型 → 通用"障碍物"）
    ("destructible", "destructible"),
    ("debris", "destructible"),
    ("ramp", "destructible"),
    ("collapsible", "destructible"),
    ("blocker", "destructible"),
    ("barrier", "destructible"),
    ("unbuildable", "destructible"),
    ("sandbag", "destructible"),
    ("rockcover", "destructible"),
)


def neutral_kind(type_name: str) -> str | None:
    """type 名 → 中立物类别（neutral/<kind> 的尾段）；None = 不是中立物。

    单一事实源（ISSUES I25）：world.adapter（把中立物过滤进 resources、不当 ENEMY）
    与 view.adapt（归一 stable_id）共用这一份关键词表。此前 view 有关键词、world 是
    12 个 type 的白名单，岩石子类型只在 view 认得、world 把它按 alliance=3 归成
    Owner.ENEMY —— 正是"敌方踪迹……最后出现 障碍物"假警报的根因。
    """
    name = str(type_name).lower()
    for kw, kind in _NEUTRAL_KIND_PATTERNS:
        if kw in name:
            return kind
    return None
