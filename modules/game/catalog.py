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
KNOWN_RACES: frozenset[str] = frozenset({"terran", "protoss", "zerg"})

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
})


class Role(str, Enum):
    """单位/建筑的结构性角色（闭集：代码按角色分支，加值 = 架构级变更）。

    更细的兵种划分（army/caster/transport…）走 capabilities 数据层，不进这里。
    """

    WORKER = "worker"      # 工兵：采矿/建造/修理（如 SCV）
    COMBAT = "combat"      # 战斗单位：攻击/施法/运输（如 机枪兵、医疗运输机）
    BUILDING = "building"  # 建筑：生产/供给（如 指挥中心、补给站）


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
    role: Role              # 结构性角色：worker/combat/building（代码按它分支的闭集枚举）
    capabilities: tuple[str, ...]  # 能力标签词表（如 ("gather","build")；where(capability=...) 查询）
    cost: Cost              # 资源成本（晶体矿/瓦斯/补给；见 Cost）
    build_time: int         # 建造/训练时间（游戏秒；如 机枪兵 18、兵营 46）
    produced_by: str | None # 产出建筑 stable ID（如 marine 由 "terran/barracks" 产；起始建筑 = None）
    prerequisites: list[str]  # 前置 stable ID 列表（如 barracks 需 ["terran/supplydepot"]）
    size: int | None = None  # footprint 格边长（建筑：depot=2/兵营=3/基地=5；非建筑=None；放置 slot 按它过滤）


class Catalog:
    """只读注册表：stable_id ↔ burnysc2_name 双向查询 + 按 role/capability 过滤。"""

    def __init__(self) -> None:
        self._by_stable: dict[str, CatalogEntry] = {}
        self._by_burnysc2: dict[str, CatalogEntry] = {}

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
        e = CatalogEntry(
            stable_id=stable_id,
            burnysc2_name=burnysc2_name,
            display_name_zh=data.get("display_name_zh", stable_id),
            role=role,
            capabilities=capabilities,
            cost=cost,
            build_time=int(data["build_time"]),
            size=data.get("size"),
            produced_by=data.get("produced_by"),
            prerequisites=list(data.get("prerequisites") or []),
        )
        if e.burnysc2_name in self._by_burnysc2:
            raise ValueError(f"{stable_id}: burnysc2_name {e.burnysc2_name!r} 已被占用")
        self._by_stable[stable_id] = e
        self._by_burnysc2[e.burnysc2_name] = e

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


def load_terran() -> Catalog:
    """从 data/terran.json 加载 Terran catalog（加载失败直接抛，不静默降级）。"""
    cat = Catalog()
    data_path = Path(__file__).parent / "data" / "terran.json"
    with data_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for stable_id, entry_data in data.items():
        cat.register(stable_id, entry_data)
    return cat
