"""game catalog：单位/建筑数据注册表（从 JSON 加载，只读查询）。

提供稳定 ID ↔ burnysc2 UnitTypeId 名 的映射（game 不 import sc2；driver 负责名→枚举）。
mechanics/constraint/生产运行时 都查这个。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CatalogEntry:
    stable_id: str  # 稳定类型 ID（跨族统一，如 "terran/marine"）
    burnysc2_name: str  # burnysc2 UnitTypeId 的 .name（如 "MARINE"；driver 负责名→枚举）
    display_name_zh: str  # 中文名（如 "机枪兵"）
    role: str  # 角色：worker | combat | building
    capabilities: list[str]  # 能力标签（如 ["gather","build"]；where(capability=...) 查用）
    cost: dict  # 造价 {minerals, vespene, supply}
    build_time: int  # 建造/训练时间（游戏秒）
    produced_by: str | None  # 产出建筑 stable ID（如 marine 由 "terran/barracks" 产）
    prerequisites: list[str]  # 前置 stable ID 列表（如 barracks 需 ["terran/supplydepot"]）


class Catalog:
    """只读注册表。"""

    def __init__(self) -> None:
        self._by_stable: dict[str, CatalogEntry] = {}
        self._by_burnysc2: dict[str, CatalogEntry] = {}

    def register(self, stable_id: str, data: dict) -> None:
        e = CatalogEntry(
            stable_id=stable_id,
            burnysc2_name=data["burnysc2_name"],
            display_name_zh=data.get("display_name_zh", stable_id),
            role=data.get("role", ""),
            capabilities=data.get("capabilities", []),
            cost=data.get("cost", {}),
            build_time=data.get("build_time", 0),
            produced_by=data.get("produced_by"),
            prerequisites=data.get("prerequisites", []),
        )
        self._by_stable[stable_id] = e
        self._by_burnysc2[e.burnysc2_name] = e

    def by_stable_id(self, stable_id: str) -> CatalogEntry | None:
        return self._by_stable.get(stable_id)

    def by_burnysc2_name(self, name: str) -> CatalogEntry | None:
        return self._by_burnysc2.get(name)

    def where(self, role: str | None = None, capability: str | None = None) -> list[CatalogEntry]:
        return [
            e for e in self._by_stable.values()
            if (role is None or e.role == role)
            and (capability is None or capability in e.capabilities)
        ]

    def stable_id_for(self, burnysc2_name: str) -> str | None:
        e = self._by_burnysc2.get(burnysc2_name)
        return e.stable_id if e else None

    def burnysc2_name_for(self, stable_id: str) -> str | None:
        e = self._by_stable.get(stable_id)
        return e.burnysc2_name if e else None


def load_terran() -> Catalog:
    """从 terran.json 加载 Terran catalog。"""
    cat = Catalog()
    data_path = Path(__file__).parent / "data" / "terran.json"
    with data_path.open(encoding="utf-8") as f:
        data = json.load(f)
    for stable_id, entry_data in data.items():
        cat.register(stable_id, entry_data)
    return cat
