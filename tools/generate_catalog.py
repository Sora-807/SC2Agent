"""从 docs/data/game_data_dump.json + 手工编排的结构/中文名 → 生成三族 catalog JSON。

用法：python tools/generate_catalog.py
前置：先跑 dump_game_data.py 生成 docs/data/game_data_dump.json
输出：modules/game/data/{terran,protoss,zerg}.json
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DUMP = ROOT / "docs" / "data" / "game_data_dump.json"
OUT_DIR = ROOT / "modules" / "game" / "data"

# ─── helpers ────────────────────────────────────────────────────────────────

def load_dump() -> dict:
    with DUMP.open(encoding="utf-8") as f:
        return json.load(f)


def size_of(e: dict) -> int | None:
    """footprint_radius → grid edge length (None for units)."""
    r = e.get("footprint_radius")
    if r is None or r == 0:
        return None
    return int(round(r * 2))


def zerg_corrected(e: dict) -> dict:
    """Zerg 非变形建筑：raw minerals - 50（drone correction）。"""
    out = dict(e)
    if out["minerals"] > 0:
        out["minerals"] = out["minerals"] - 50
    return out


# ─── TERRAN ──────────────────────────────────────────────────────────────────

# burnysc2_name → {stable_id suffix, zh, short, role, caps, produced_by, prereqs, size_override, variants, build_ability, build_order_name, supply_override, minerals_override, vespene_override}
TERRAN: list[dict] = [
    # ── worker ──
    dict(b="SCV", s="scv", zh="农民", short="农民", role="worker",
         caps=["gather","build","repair"], pb="terran/commandcenter", pre=[]),
    # ── combat (Barracks) ──
    dict(b="MARINE", s="marine", zh="机枪兵", short="枪兵", role="combat",
         caps=["attack","move"], pb="terran/barracks", pre=["terran/barracks"]),
    dict(b="REAPER", s="reaper", zh="收割者", short="收割", role="combat",
         caps=["attack","move"], pb="terran/barracks", pre=["terran/barracks"]),
    dict(b="MARAUDER", s="marauder", zh="劫掠者", short="劫掠", role="combat",
         caps=["attack","move"], pb="terran/barracks", pre=["terran/techlab"]),
    dict(b="GHOST", s="ghost", zh="幽灵", short="幽灵", role="combat",
         caps=["attack","move"], pb="terran/barracks", pre=["terran/ghostacademy"]),
    # ── combat (Factory) ──
    dict(b="HELLION", s="hellion", zh="恶火", short="恶火", role="combat",
         caps=["attack","move"], pb="terran/factory", pre=["terran/factory"],
         variants=["HELLIONTANK"]),
    dict(b="WIDOWMINE", s="widowmine", zh="寡妇雷", short="寡雷", role="combat",
         caps=["attack","move"], pb="terran/factory", pre=["terran/factory"],
         variants=["WIDOWMINEBURROWED"]),
    dict(b="CYCLONE", s="cyclone", zh="旋风", short="旋风", role="combat",
         caps=["attack","move"], pb="terran/factory", pre=["terran/factorytechlab"]),
    dict(b="SIEGETANK", s="siegetank", zh="攻城坦克", short="坦克", role="combat",
         caps=["attack","move"], pb="terran/factory", pre=["terran/factorytechlab"],
         attack_range=5, siege_range=13, variants=["SIEGETANKSIEGED"]),
    dict(b="THOR", s="thor", zh="雷神", short="雷神", role="combat",
         caps=["attack","move"], pb="terran/factory", pre=["terran/armory"],
         variants=["THORAP"]),
    # ── combat (Starport) ──
    dict(b="VIKINGFIGHTER", s="viking", zh="维京战机", short="维京", role="combat",
         caps=["attack","move"], pb="terran/starport", pre=["terran/starport"],
         variants=["VIKINGASSAULT","VIKING"]),
    dict(b="MEDIVAC", s="medivac", zh="医疗运输机", short="医疗", role="combat",
         caps=["heal","transport","move"], pb="terran/starport", pre=["terran/starport"]),
    dict(b="LIBERATOR", s="liberator", zh="解放者", short="解放", role="combat",
         caps=["attack","move"], pb="terran/starport", pre=["terran/starport"],
         variants=["LIBERATORAG"]),
    dict(b="RAVEN", s="raven", zh="乌鸦", short="乌鸦", role="combat",
         caps=["move"], pb="terran/starport", pre=["terran/starporttechlab"]),
    dict(b="BANSHEE", s="banshee", zh="女妖", short="女妖", role="combat",
         caps=["attack","move"], pb="terran/starport", pre=["terran/starporttechlab"]),
    dict(b="BATTLECRUISER", s="battlecruiser", zh="战列巡洋舰", short="战巡", role="combat",
         caps=["attack","move"], pb="terran/starport", pre=["terran/fusioncore"]),
    # ── buildings ──
    dict(b="COMMANDCENTER", s="commandcenter", zh="指挥中心", short="基地", role="building",
         caps=["train","dropoff"], pb=None, pre=[],
         variants=["COMMANDCENTERFLYING"]),
    dict(b="ORBITALCOMMAND", s="orbitalcommand", zh="轨道指令", short="轨道", role="building",
         caps=["train","dropoff"], pb="terran/commandcenter", pre=["terran/barracks"],
         variants=["ORBITALCOMMANDFLYING"]),
    dict(b="PLANETARYFORTRESS", s="planetaryfortress", zh="行星要塞", short="要塞", role="building",
         caps=["train","dropoff"], pb="terran/commandcenter", pre=["terran/engineeringbay"]),
    dict(b="SUPPLYDEPOT", s="supplydepot", zh="补给站", short="补给", role="building",
         caps=["supply"], pb=None, pre=["terran/commandcenter"],
         variants=["SUPPLYDEPOTLOWERED"]),
    dict(b="BARRACKS", s="barracks", zh="兵营", short="兵营", role="building",
         caps=["train"], pb=None, pre=["terran/supplydepot"],
         variants=["BARRACKSFLYING"]),
    dict(b="REFINERY", s="refinery", zh="精炼厂", short="精炼", role="building",
         caps=["gas"], pb=None, pre=["terran/commandcenter"],
         variants=["REFINERYRICH"]),
    dict(b="ENGINEERINGBAY", s="engineeringbay", zh="工程站", short="工站", role="building",
         caps=["research"], pb=None, pre=["terran/commandcenter"]),
    dict(b="ARMORY", s="armory", zh="军械库", short="军械", role="building",
         caps=["research"], pb=None, pre=["terran/factory"]),
    dict(b="FACTORY", s="factory", zh="工厂", short="工厂", role="building",
         caps=["train"], pb=None, pre=["terran/barracks"],
         variants=["FACTORYFLYING"]),
    dict(b="STARPORT", s="starport", zh="星轨", short="星轨", role="building",
         caps=["train"], pb=None, pre=["terran/factory"],
         variants=["STARPORTFLYING"]),
    dict(b="BUNKER", s="bunker", zh="地堡", short="地堡", role="building",
         caps=[], pb=None, pre=["terran/barracks"]),
    dict(b="MISSILETURRET", s="missileturret", zh="防空炮", short="防空", role="building",
         caps=[], pb=None, pre=["terran/engineeringbay"]),
    dict(b="SENSORTOWER", s="sensortower", zh="感应塔", short="感应", role="building",
         caps=[], pb=None, pre=["terran/engineeringbay"]),
    dict(b="GHOSTACADEMY", s="ghostacademy", zh="幽灵军校", short="军校", role="building",
         caps=["research"], pb=None, pre=["terran/barracks"]),
    dict(b="FUSIONCORE", s="fusioncore", zh="聚变芯", short="聚变", role="building",
         caps=["research"], pb=None, pre=["terran/starport"]),
    # ── addons ──
    dict(b="BARRACKSREACTOR", s="reactor", zh="双倍", short="双倍", role="building",
         caps=["addon"], pb="terran/barracks", pre=["terran/barracks"], size_ov=2,
         ba="BUILD_REACTOR", bon="Reactor"),
    dict(b="BARRACKSTECHLAB", s="techlab", zh="科技实验室", short="科技", role="building",
         caps=["addon"], pb="terran/barracks", pre=["terran/barracks"], size_ov=2,
         ba="BUILD_TECHLAB", bon="Techlab"),
    dict(b="FACTORYREACTOR", s="factoryreactor", zh="双倍", short="双倍", role="building",
         caps=["addon"], pb="terran/factory", pre=["terran/factory"], size_ov=2,
         ba="BUILD_REACTOR", bon="Reactor"),
    dict(b="FACTORYTECHLAB", s="factorytechlab", zh="科技", short="科技", role="building",
         caps=["addon"], pb="terran/factory", pre=["terran/factory"], size_ov=2,
         ba="BUILD_TECHLAB", bon="Techlab"),
    dict(b="STARPORTREACTOR", s="starportreactor", zh="双倍", short="双倍", role="building",
         caps=["addon"], pb="terran/starport", pre=["terran/starport"], size_ov=2,
         ba="BUILD_REACTOR", bon="Reactor"),
    dict(b="STARPORTTECHLAB", s="starporttechlab", zh="科技", short="科技", role="building",
         caps=["addon"], pb="terran/starport", pre=["terran/starport"], size_ov=2,
         ba="BUILD_TECHLAB", bon="Techlab"),
    # ── summoned/ability units (appear on map; not trained/built) ──
    dict(b="MULE", s="mule", zh="矿骡", short="矿骡", role="worker",
         caps=["gather"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="AUTOTURRET", s="autoturret", zh="自动炮塔", short="炮塔", role="building",
         caps=[], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="POINTDEFENSEDRONE", s="pointdefensedrone", zh="防御无人机", short="防机", role="combat",
         caps=["move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="RAVENREPAIRDRONE", s="ravendrone", zh="维修无人机", short="维修", role="combat",
         caps=["move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="NUKE", s="nuke", zh="核弹", short="核弹", role="combat",
         caps=[], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="KD8CHARGE", s="kd8charge", zh="KD8炸药", short="炸药", role="combat",
         caps=[], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
]

TERRAN_UPGRADES: list[dict] = [
    # infantry (Engineering Bay) — costs from game_data_dump
    dict(b="INFANTRYWEAPONSLEVEL1", s="infantryweapons1", zh="步兵武器 1 级", short="步武", pb="terran/engineeringbay", pre=["terran/engineeringbay"], m=100, g=100, t=114),
    dict(b="INFANTRYWEAPONSLEVEL2", s="infantryweapons2", zh="步兵武器 2 级", short="步武", pb="terran/engineeringbay", pre=["terran/engineeringbay","terran/infantryweapons1"], m=150, g=150, t=136),
    dict(b="INFANTRYWEAPONSLEVEL3", s="infantryweapons3", zh="步兵武器 3 级", short="步武", pb="terran/engineeringbay", pre=["terran/engineeringbay","terran/infantryweapons2"], m=200, g=200, t=157),
    dict(b="INFANTRYARMORLEVEL1", s="infantryarmor1", zh="步兵装甲 1 级", short="步甲", pb="terran/engineeringbay", pre=["terran/engineeringbay"], m=100, g=100, t=114),
    dict(b="INFANTRYARMORLEVEL2", s="infantryarmor2", zh="步兵装甲 2 级", short="步甲", pb="terran/engineeringbay", pre=["terran/engineeringbay","terran/infantryarmor1"], m=150, g=150, t=136),
    dict(b="INFANTRYARMORLEVEL3", s="infantryarmor3", zh="步兵装甲 3 级", short="步甲", pb="terran/engineeringbay", pre=["terran/engineeringbay","terran/infantryarmor2"], m=200, g=200, t=157),
    # vehicle+ship (Armory, LotV merged)
    dict(b="VEHICLEANDSHIPWEAPONSLEVEL1", s="vehicleshipweapons1", zh="载具舰船武器 1 级", short="车武", pb="terran/armory", pre=["terran/armory"], m=100, g=100, t=114),
    dict(b="VEHICLEANDSHIPWEAPONSLEVEL2", s="vehicleshipweapons2", zh="载具舰船武器 2 级", short="车武", pb="terran/armory", pre=["terran/armory","terran/vehicleshipweapons1"], m=175, g=175, t=136),
    dict(b="VEHICLEANDSHIPWEAPONSLEVEL3", s="vehicleshipweapons3", zh="载具舰船武器 3 级", short="车武", pb="terran/armory", pre=["terran/armory","terran/vehicleshipweapons2"], m=250, g=250, t=157),
    dict(b="VEHICLEANDSHIPARMORSLEVEL1", s="vehicleshiparmor1", zh="载具舰船装甲 1 级", short="车甲", pb="terran/armory", pre=["terran/armory"], m=100, g=100, t=114),
    dict(b="VEHICLEANDSHIPARMORSLEVEL2", s="vehicleshiparmor2", zh="载具舰船装甲 2 级", short="车甲", pb="terran/armory", pre=["terran/armory","terran/vehicleshiparmor1"], m=175, g=175, t=136),
    dict(b="VEHICLEANDSHIPARMORSLEVEL3", s="vehicleshiparmor3", zh="载具舰船装甲 3 级", short="车甲", pb="terran/armory", pre=["terran/armory","terran/vehicleshiparmor2"], m=250, g=250, t=157),
    # building armor (Engineering Bay)
    dict(b="TERRANBUILDINGARMOR", s="buildingarmor", zh="建筑装甲", short="建甲", pb="terran/engineeringbay", pre=["terran/engineeringbay"], m=150, g=150, t=100),
]

# ─── PROTOSS ─────────────────────────────────────────────────────────────────

PROTOSS: list[dict] = [
    # ── worker ──
    dict(b="PROBE", s="probe", zh="探针", short="探针", role="worker",
         caps=["gather","build"], pb="protoss/nexus", pre=[]),
    # ── Gateway units ──
    dict(b="ZEALOT", s="zealot", zh="狂热者", short="狂热", role="combat",
         caps=["attack","move"], pb="protoss/gateway", pre=["protoss/gateway"]),
    dict(b="STALKER", s="stalker", zh="追猎者", short="追猎", role="combat",
         caps=["attack","move"], pb="protoss/gateway", pre=["protoss/cyberneticscore"]),
    dict(b="SENTRY", s="sentry", zh="哨兵", short="哨兵", role="combat",
         caps=["attack","move"], pb="protoss/gateway", pre=["protoss/cyberneticscore"]),
    dict(b="ADEPT", s="adept", zh="升华者", short="升华", role="combat",
         caps=["attack","move"], pb="protoss/gateway", pre=["protoss/cyberneticscore"],
         variants=["ADEPTPHASESHIFT"]),
    dict(b="HIGHTEMPLAR", s="hightemplar", zh="高阶圣堂武士", short="高圣", role="combat",
         caps=["move"], pb="protoss/gateway", pre=["protoss/templararchive"]),
    dict(b="DARKTEMPLAR", s="darktemplar", zh="黑暗圣堂武士", short="黑圣", role="combat",
         caps=["attack","move"], pb="protoss/gateway", pre=["protoss/darkshrine"]),
    dict(b="ARCHON", s="archon", zh="执政官", short="执政", role="combat",
         caps=["attack","move"], pb=None, pre=[]),
    # ── Robotics Facility units ──
    dict(b="OBSERVER", s="observer", zh="观察者", short="观察", role="combat",
         caps=["move"], pb="protoss/roboticsfacility", pre=["protoss/roboticsfacility"],
         variants=["OBSERVERSIEGEMODE"]),
    dict(b="IMMORTAL", s="immortal", zh="不朽者", short="不朽", role="combat",
         caps=["attack","move"], pb="protoss/roboticsfacility", pre=["protoss/roboticsfacility"]),
    dict(b="WARPPRISM", s="warpprism", zh="折跃棱镜", short="棱镜", role="combat",
         caps=["transport","move"], pb="protoss/roboticsfacility", pre=["protoss/roboticsfacility"],
         variants=["WARPPRISMPHASING"]),
    dict(b="COLOSSUS", s="colossus", zh="巨像", short="巨像", role="combat",
         caps=["attack","move"], pb="protoss/roboticsfacility", pre=["protoss/roboticsbay"]),
    dict(b="DISRUPTOR", s="disruptor", zh="干扰者", short="干扰", role="combat",
         caps=["attack","move"], pb="protoss/roboticsfacility", pre=["protoss/roboticsbay"],
         variants=["DISRUPTORPHASED"]),
    # ── Stargate units ──
    dict(b="PHOENIX", s="phoenix", zh="凤凰", short="凤凰", role="combat",
         caps=["attack","move"], pb="protoss/stargate", pre=["protoss/stargate"]),
    dict(b="VOIDRAY", s="voidray", zh="虚空辉光舰", short="虚空", role="combat",
         caps=["attack","move"], pb="protoss/stargate", pre=["protoss/stargate"]),
    dict(b="ORACLE", s="oracle", zh="先知", short="先知", role="combat",
         caps=["attack","move"], pb="protoss/stargate", pre=["protoss/stargate"]),
    dict(b="TEMPEST", s="tempest", zh="暴风舰", short="暴风", role="combat",
         caps=["attack","move"], pb="protoss/stargate", pre=["protoss/fleetbeacon"]),
    dict(b="CARRIER", s="carrier", zh="航母", short="航母", role="combat",
         caps=["attack","move"], pb="protoss/stargate", pre=["protoss/fleetbeacon"]),
    dict(b="MOTHERSHIP", s="mothership", zh="母舰", short="母舰", role="combat",
         caps=["attack","move"], pb="protoss/nexus", pre=["protoss/fleetbeacon"]),
    # ── summoned unit (Carrier's interceptor; appears on map) ──
    dict(b="INTERCEPTOR", s="interceptor", zh="拦截机", short="拦截", role="combat",
         caps=["attack","move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    # ── summoned ability units (appear on map) ──
    dict(b="FORCEFIELD", s="forcefield", zh="力场", short="力场", role="building",
         caps=[], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0),
    # ── buildings ──
    dict(b="NEXUS", s="nexus", zh="枢纽", short="枢纽", role="building",
         caps=["train","dropoff"], pb=None, pre=[]),
    dict(b="PYLON", s="pylon", zh="水晶塔", short="水晶", role="building",
         caps=["supply"], pb=None, pre=["protoss/nexus"],
         variants=["PYLONOVERCHARGED"]),
    dict(b="ASSIMILATOR", s="assimilator", zh="同化者", short="同化", role="building",
         caps=["gas"], pb=None, pre=["protoss/nexus"],
         variants=["ASSIMILATORRICH"]),
    dict(b="GATEWAY", s="gateway", zh="传送门", short="门", role="building",
         caps=["train"], pb=None, pre=["protoss/nexus"]),
    dict(b="WARPGATE", s="warpgate", zh="折跃门", short="折跃", role="building",
         caps=["train"], pb="protoss/gateway", pre=["protoss/cyberneticscore"]),
    dict(b="FORGE", s="forge", zh="熔炉", short="熔炉", role="building",
         caps=["research"], pb=None, pre=["protoss/nexus"]),
    dict(b="CYBERNETICSCORE", s="cyberneticscore", zh="控制核心", short="控核", role="building",
         caps=["research"], pb=None, pre=["protoss/gateway"]),
    dict(b="PHOTONCANNON", s="photoncannon", zh="光子炮", short="光炮", role="building",
         caps=[], pb=None, pre=["protoss/forge"]),
    dict(b="SHIELDBATTERY", s="shieldbattery", zh="护盾电池", short="护盾", role="building",
         caps=[], pb=None, pre=["protoss/cyberneticscore"]),
    dict(b="ROBOTICSFACILITY", s="roboticsfacility", zh="机器人设施", short="机器", role="building",
         caps=["train"], pb=None, pre=["protoss/cyberneticscore"]),
    dict(b="ROBOTICSBAY", s="roboticsbay", zh="机器人港口", short="机港", role="building",
         caps=["research"], pb=None, pre=["protoss/roboticsfacility"]),
    dict(b="TWILIGHTCOUNCIL", s="twilightcouncil", zh="黄昏议会", short="议会", role="building",
         caps=["research"], pb=None, pre=["protoss/cyberneticscore"]),
    dict(b="TEMPLARARCHIVE", s="templararchive", zh="圣堂文献", short="文献", role="building",
         caps=["research"], pb=None, pre=["protoss/twilightcouncil"]),
    dict(b="DARKSHRINE", s="darkshrine", zh="黑暗圣所", short="圣所", role="building",
         caps=["research"], pb=None, pre=["protoss/twilightcouncil"]),
    dict(b="STARGATE", s="stargate", zh="星门", short="星门", role="building",
         caps=["train"], pb=None, pre=["protoss/cyberneticscore"]),
    dict(b="FLEETBEACON", s="fleetbeacon", zh="舰队航标", short="航标", role="building",
         caps=["research"], pb=None, pre=["protoss/stargate"]),
]

PROTOSS_UPGRADES: list[dict] = [
    # Forge: ground weapons/armors/shields — costs from game_data_dump
    dict(b="PROTOSSGROUNDWEAPONSLEVEL1", s="groundweapons1", zh="地面武器 1 级", short="地武", pb="protoss/forge", pre=["protoss/forge"], m=100, g=100, t=121),
    dict(b="PROTOSSGROUNDWEAPONSLEVEL2", s="groundweapons2", zh="地面武器 2 级", short="地武", pb="protoss/forge", pre=["protoss/forge","protoss/groundweapons1"], m=150, g=150, t=145),
    dict(b="PROTOSSGROUNDWEAPONSLEVEL3", s="groundweapons3", zh="地面武器 3 级", short="地武", pb="protoss/forge", pre=["protoss/forge","protoss/groundweapons2"], m=200, g=200, t=168),
    dict(b="PROTOSSGROUNDARMORSLEVEL1", s="groundarmors1", zh="地面装甲 1 级", short="地甲", pb="protoss/forge", pre=["protoss/forge"], m=100, g=100, t=121),
    dict(b="PROTOSSGROUNDARMORSLEVEL2", s="groundarmors2", zh="地面装甲 2 级", short="地甲", pb="protoss/forge", pre=["protoss/forge","protoss/groundarmors1"], m=150, g=150, t=145),
    dict(b="PROTOSSGROUNDARMORSLEVEL3", s="groundarmors3", zh="地面装甲 3 级", short="地甲", pb="protoss/forge", pre=["protoss/forge","protoss/groundarmors2"], m=200, g=200, t=168),
    dict(b="PROTOSSSHIELDSLEVEL1", s="shields1", zh="护盾 1 级", short="护盾", pb="protoss/forge", pre=["protoss/forge"], m=150, g=150, t=121),
    dict(b="PROTOSSSHIELDSLEVEL2", s="shields2", zh="护盾 2 级", short="护盾", pb="protoss/forge", pre=["protoss/forge","protoss/shields1"], m=200, g=200, t=145),
    dict(b="PROTOSSSHIELDSLEVEL3", s="shields3", zh="护盾 3 级", short="护盾", pb="protoss/forge", pre=["protoss/forge","protoss/shields2"], m=250, g=250, t=168),
    # Cybernetics Core: air weapons/armors
    dict(b="PROTOSSAIRWEAPONSLEVEL1", s="airweapons1", zh="空军武器 1 级", short="空武", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore"], m=100, g=100, t=129),
    dict(b="PROTOSSAIRWEAPONSLEVEL2", s="airweapons2", zh="空军武器 2 级", short="空武", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore","protoss/airweapons1"], m=175, g=175, t=154),
    dict(b="PROTOSSAIRWEAPONSLEVEL3", s="airweapons3", zh="空军武器 3 级", short="空武", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore","protoss/airweapons2"], m=250, g=250, t=179),
    dict(b="PROTOSSAIRARMORSLEVEL1", s="airarmors1", zh="空军装甲 1 级", short="空甲", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore"], m=100, g=100, t=129),
    dict(b="PROTOSSAIRARMORSLEVEL2", s="airarmors2", zh="空军装甲 2 级", short="空甲", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore","protoss/airarmors1"], m=175, g=175, t=154),
    dict(b="PROTOSSAIRARMORSLEVEL3", s="airarmors3", zh="空军装甲 3 级", short="空甲", pb="protoss/cyberneticscore", pre=["protoss/cyberneticscore","protoss/airarmors2"], m=250, g=250, t=179),
]

# ─── ZERG ────────────────────────────────────────────────────────────────────

ZERG: list[dict] = [
    # ── worker ──
    dict(b="DRONE", s="drone", zh="工蜂", short="工蜂", role="worker",
         caps=["gather","build"], pb="zerg/hatchery", pre=[],
         variants=["DRONEBURROWED"]),
    # ── Larva-trained units ──
    dict(b="ZERGLING", s="zergling", zh="跳虫", short="虫", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/spawningpool"],
         m_ov=50, sup_ov=1,  # pair cost (25*2), pair supply
         variants=["ZERGLINGBURROWED"]),
    dict(b="OVERLORD", s="overlord", zh="王虫", short="王虫", role="combat",
         caps=["move","supply"], pb="zerg/hatchery", pre=[],
         variants=["OVERLORDCOCOON","TRANSPORTOVERLORDCOCOON"]),
    dict(b="ROACH", s="roach", zh="蟑螂", short="蟑螂", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/roachwarren"],
         variants=["ROACHBURROWED"]),
    dict(b="HYDRALISK", s="hydralisk", zh="刺蛇", short="刺蛇", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/hydraliskden"],
         variants=["HYDRALISKBURROWED"]),
    dict(b="MUTALISK", s="mutalisk", zh="飞龙", short="飞龙", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/spire"]),
    dict(b="CORRUPTOR", s="corruptor", zh="腐化者", short="腐化", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/spire"]),
    dict(b="INFESTOR", s="infestor", zh="感染者", short="感染", role="combat",
         caps=["move"], pb="zerg/hatchery", pre=["zerg/infestationpit"],
         variants=["INFESTORBURROWED"]),
    dict(b="SWARMHOSTMP", s="swarmhost", zh="虫群宿主", short="虫宿", role="combat",
         caps=["move"], pb="zerg/hatchery", pre=["zerg/infestationpit"],
         variants=["SWARMHOSTBURROWEDMP"]),
    dict(b="ULTRALISK", s="ultralisk", zh="雷兽", short="雷兽", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/ultraliskcavern"],
         variants=["ULTRALISKBURROWED"]),
    dict(b="VIPER", s="viper", zh="蝰蛇", short="蝰蛇", role="combat",
         caps=["move"], pb="zerg/hatchery", pre=["zerg/hive"]),
    # ── Queen (trained by townhall) ──
    dict(b="QUEEN", s="queen", zh="虫后", short="虫后", role="combat",
         caps=["attack","move"], pb="zerg/hatchery", pre=["zerg/spawningpool"],
         variants=["QUEENBURROWED"]),
    # ── Morph units (cocoon = intermediate state during morph) ──
    dict(b="BANELING", s="baneling", zh="爆虫", short="爆虫", role="combat",
         caps=["attack","move"], pb="zerg/zergling", pre=["zerg/banelingnest"],
         variants=["BANELINGBURROWED","BANELINGCOCOON"]),
    dict(b="RAVAGER", s="ravager", zh="破坏者", short="破坏", role="combat",
         caps=["attack","move"], pb="zerg/roach", pre=["zerg/roachwarren"],
         variants=["RAVAGERBURROWED","RAVAGERCOCOON"]),
    dict(b="OVERSEER", s="overseer", zh="眼虫", short="眼虫", role="combat",
         caps=["move"], pb="zerg/overlord", pre=["zerg/lair"],
         variants=["OVERSEERSIEGEMODE"]),
    dict(b="LURKERMP", s="lurker", zh="潜伏者", short="潜伏", role="combat",
         caps=["attack","move"], pb="zerg/hydralisk", pre=["zerg/lurkerden"],
         variants=["LURKERMPBURROWED","LURKERMPEGG"]),
    dict(b="BROODLORD", s="broodlord", zh="巢虫领主", short="巢虫", role="combat",
         caps=["attack","move"], pb="zerg/corruptor", pre=["zerg/greaterspire"],
         variants=["BROODLORDCOCOON"]),
    dict(b="OVERLORDTRANSPORT", s="overlordtransport", zh="运输王虫", short="运输", role="combat",
         caps=["transport","move","supply"], pb="zerg/overlord", pre=["zerg/lair"]),
    # ── buildings ──
    dict(b="HATCHERY", s="hatchery", zh="孵化场", short="孵化", role="building",
         caps=["train","dropoff"], pb=None, pre=[]),
    dict(b="LAIR", s="lair", zh="虫穴", short="虫穴", role="building",
         caps=["train","dropoff"], pb="zerg/hatchery", pre=["zerg/spawningpool"]),
    dict(b="HIVE", s="hive", zh="蜂巢", short="蜂巢", role="building",
         caps=["train","dropoff"], pb="zerg/lair", pre=["zerg/infestationpit"]),
    dict(b="EXTRACTOR", s="extractor", zh="萃取器", short="萃取", role="building",
         caps=["gas"], pb=None, pre=[],
         variants=["EXTRACTORRICH"]),
    dict(b="SPAWNINGPOOL", s="spawningpool", zh="孵化池", short="水池", role="building",
         caps=["research"], pb=None, pre=["zerg/hatchery"]),
    dict(b="EVOLUTIONCHAMBER", s="evolutionchamber", zh="进化腔", short="进化", role="building",
         caps=["research"], pb=None, pre=["zerg/hatchery"]),
    dict(b="BANELINGNEST", s="banelingnest", zh="爆虫巢", short="爆巢", role="building",
         caps=["research"], pb=None, pre=["zerg/spawningpool"]),
    dict(b="ROACHWARREN", s="roachwarren", zh="蟑螂穴", short="蟑穴", role="building",
         caps=["research"], pb=None, pre=["zerg/spawningpool"]),
    dict(b="HYDRALISKDEN", s="hydraliskden", zh="刺蛇穴", short="刺穴", role="building",
         caps=["research"], pb=None, pre=["zerg/lair"]),
    dict(b="INFESTATIONPIT", s="infestationpit", zh="感染坑", short="感坑", role="building",
         caps=["research"], pb=None, pre=["zerg/lair"]),
    dict(b="SPIRE", s="spire", zh="尖塔", short="尖塔", role="building",
         caps=["research"], pb=None, pre=["zerg/lair"]),
    dict(b="GREATERSPIRE", s="greaterspire", zh="大尖塔", short="大塔", role="building",
         caps=["research"], pb="zerg/spire", pre=["zerg/hive"]),
    dict(b="NYDUSNETWORK", s="nydusnetwork", zh="虫道网络", short="虫道", role="building",
         caps=["train"], pb=None, pre=["zerg/lair"]),
    dict(b="NYDUSCANAL", s="nyduscanal", zh="虫道运河", short="运河", role="building",
         caps=[], pb="zerg/nydusnetwork", pre=["zerg/nydusnetwork"], m_ov=75, g_ov=75),
    dict(b="ULTRALISKCAVERN", s="ultraliskcavern", zh="雷兽穴", short="雷穴", role="building",
         caps=["research"], pb=None, pre=["zerg/hive"]),
    dict(b="LURKERDENMP", s="lurkerden", zh="潜伏穴", short="潜穴", role="building",
         caps=["research"], pb=None, pre=["zerg/hydraliskden"]),
    dict(b="SPINECRAWLER", s="spinecrawler", zh="脊针爬虫", short="脊针", role="building",
         caps=[], pb=None, pre=["zerg/spawningpool"],
         variants=["SPINECRAWLERUPROOTED"]),
    dict(b="SPORECRAWLER", s="sporecrawler", zh="孢子爬虫", short="孢子", role="building",
         caps=[], pb=None, pre=["zerg/spawningpool"],
         variants=["SPORECRAWLERUPROOTED"]),
    dict(b="CREEPTUMOR", s="creeptumor", zh="菌瘤", short="菌瘤", role="building",
         caps=[], pb=None, pre=[],
         variants=["CREEPTUMORQUEEN","CREEPTUMORBURROWED"]),
    # ── summoned/ability units (appear on map in multiplayer) ──
    dict(b="LARVA", s="larva", zh="幼虫", short="幼虫", role="combat",
         caps=[], pb="zerg/hatchery", pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="EGG", s="egg", zh="卵", short="卵", role="combat",
         caps=[], pb="zerg/hatchery", pre=[], m_ov=0, g_ov=0, sup_ov=0),
    dict(b="BROODLING", s="broodling", zh="巢虫", short="巢虫", role="combat",
         caps=["attack","move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0,
         variants=["BROODLINGESCORT"]),
    dict(b="LOCUSTMP", s="locust", zh="蝗虫", short="蝗虫", role="combat",
         caps=["attack","move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0,
         variants=["LOCUSTMPFLYING"]),
    dict(b="INFESTORTERRAN", s="infestedterran", zh="感染人", short="感人", role="combat",
         caps=["attack","move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0,
         variants=["INFESTORTERRANBURROWED","INFESTEDTERRANSEGG"]),
    dict(b="CHANGELING", s="changeling", zh="变形虫", short="变形", role="combat",
         caps=["move"], pb=None, pre=[], m_ov=0, g_ov=0, sup_ov=0,
         variants=["CHANGELINGMARINE","CHANGELINGMARINESHIELD","CHANGELINGZEALOT",
                   "CHANGELINGZERGLING","CHANGELINGZERGLINGWINGS"]),
]

ZERG_UPGRADES: list[dict] = [
    # Evolution Chamber: melee/missile/ground armor — costs from game_data_dump
    dict(b="ZERGMELEEWEAPONSLEVEL1", s="meleeweapons1", zh="近战武器 1 级", short="近武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber"], m=100, g=100, t=114),
    dict(b="ZERGMELEEWEAPONSLEVEL2", s="meleeweapons2", zh="近战武器 2 级", short="近武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/meleeweapons1"], m=150, g=150, t=136),
    dict(b="ZERGMELEEWEAPONSLEVEL3", s="meleeweapons3", zh="近战武器 3 级", short="近武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/meleeweapons2"], m=200, g=200, t=157),
    dict(b="ZERGMISSILEWEAPONSLEVEL1", s="missileweapons1", zh="远程武器 1 级", short="远武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber"], m=100, g=100, t=114),
    dict(b="ZERGMISSILEWEAPONSLEVEL2", s="missileweapons2", zh="远程武器 2 级", short="远武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/missileweapons1"], m=150, g=150, t=136),
    dict(b="ZERGMISSILEWEAPONSLEVEL3", s="missileweapons3", zh="远程武器 3 级", short="远武", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/missileweapons2"], m=200, g=200, t=157),
    dict(b="ZERGGROUNDARMORSLEVEL1", s="groundarmors1", zh="地面装甲 1 级", short="地甲", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber"], m=100, g=100, t=114),
    dict(b="ZERGGROUNDARMORSLEVEL2", s="groundarmors2", zh="地面装甲 2 级", short="地甲", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/groundarmors1"], m=150, g=150, t=136),
    dict(b="ZERGGROUNDARMORSLEVEL3", s="groundarmors3", zh="地面装甲 3 级", short="地甲", pb="zerg/evolutionchamber", pre=["zerg/evolutionchamber","zerg/groundarmors2"], m=200, g=200, t=157),
    # Spire: flyer weapons/armor
    dict(b="ZERGFLYERWEAPONSLEVEL1", s="flyerweapons1", zh="飞行武器 1 级", short="飞武", pb="zerg/spire", pre=["zerg/spire"], m=100, g=100, t=114),
    dict(b="ZERGFLYERWEAPONSLEVEL2", s="flyerweapons2", zh="飞行武器 2 级", short="飞武", pb="zerg/spire", pre=["zerg/spire","zerg/flyerweapons1"], m=175, g=175, t=136),
    dict(b="ZERGFLYERWEAPONSLEVEL3", s="flyerweapons3", zh="飞行武器 3 级", short="飞武", pb="zerg/spire", pre=["zerg/spire","zerg/flyerweapons2"], m=250, g=250, t=157),
    dict(b="ZERGFLYERARMORSLEVEL1", s="flyerarmors1", zh="飞行装甲 1 级", short="飞甲", pb="zerg/spire", pre=["zerg/spire"], m=100, g=100, t=114),
    dict(b="ZERGFLYERARMORSLEVEL2", s="flyerarmors2", zh="飞行装甲 2 级", short="飞甲", pb="zerg/spire", pre=["zerg/spire","zerg/flyerarmors1"], m=175, g=175, t=136),
    dict(b="ZERGFLYERARMORSLEVEL3", s="flyerarmors3", zh="飞行装甲 3 级", short="飞甲", pb="zerg/spire", pre=["zerg/spire","zerg/flyerarmors2"], m=250, g=250, t=157),
]

# ─── generation ──────────────────────────────────────────────────────────────

# Zerg morph units/buildings: their dump cost is morph_cost, which is correct.
ZERG_MORPH_BURNYSC2 = {"BANELING","RAVAGER","OVERSEER","LURKERMP","BROODLORD","OVERLORDTRANSPORT","LAIR","HIVE","GREATERSPIRE","NYDUSCANAL"}


def gen_unit_entry(d: dict, dump_units_upper: dict, race: str) -> dict:
    b = d["b"]
    e = dump_units_upper.get(b)
    if e is None:
        raise KeyError(f"burnysc2_name {b!r} not in dump (units)")

    # cost: use dump values (already morph_cost for morph units)
    minerals = d.get("m_ov", e["minerals"])
    vespene = d.get("v_ov", e["vespene"])
    supply = d.get("sup_ov", e["supply"])

    # Zerg non-morph building: correct for drone (-50 minerals)
    if race == "zerg" and b not in ZERG_MORPH_BURNYSC2 and "Structure" in e.get("attributes", []):
        if minerals > 0:
            minerals = minerals - 50

    entry: dict = {
        "burnysc2_name": b,
        "display_name_zh": d["zh"],
        "short_name_zh": d["short"],
        "role": d["role"],
        "capabilities": d["caps"],
        "cost": {"minerals": minerals, "vespene": vespene, "supply": supply},
        "build_time": e["build_time"],
        "produced_by": d["pb"],
        "prerequisites": d["pre"],
    }
    # 提供的供给（B6 三族单源）：基地/补给建筑/Overlord 族等 food_provided > 0 才写，
    # 其余省略（loader 默认 0）。数值与本机 dump 一致（CC/Nexus=13、Depot/Pylon=8、
    # Hatchery 族=4、Overlord 族=8；与 planner.economy 校准值同源）。
    if e["food_provided"]:
        entry["supply_provided"] = e["food_provided"]
    sz = d.get("size_ov")
    if sz is None:
        sz = size_of(e)
    if sz is not None:
        entry["size"] = sz
    if d.get("variants"):
        entry["variants"] = list(d["variants"])
    if d.get("ba"):
        entry["build_ability"] = d["ba"]
    if d.get("bon"):
        entry["build_order_name"] = d["bon"]
    if d.get("attack_range") is not None:
        entry["attack_range"] = d["attack_range"]
    if d.get("siege_range") is not None:
        entry["siege_range"] = d["siege_range"]
    return entry


def gen_upgrade_entry(d: dict, race: str) -> dict:
    """Upgrades carry embedded cost data (m/g/t) from the dump — no lookup needed."""
    return {
        "burnysc2_name": d["b"],
        "display_name_zh": d["zh"],
        "short_name_zh": d["short"],
        "role": "upgrade",
        "capabilities": [],
        "cost": {"minerals": d["m"], "vespene": d["g"], "supply": 0},
        "build_time": d["t"],
        "produced_by": d["pb"],
        "prerequisites": d["pre"],
    }


def generate_race(race: str, units: list[dict], upgrades: list[dict],
                  dump: dict) -> dict[str, dict]:
    # dump keys are proto names (mixed case); catalog needs enum names (uppercase)
    dump_units_upper = {k.upper(): v for k, v in dump["units"].items()}
    out: dict[str, dict] = {}
    for d in units:
        sid = f"{race}/{d['s']}"
        out[sid] = gen_unit_entry(d, dump_units_upper, race)
    for d in upgrades:
        sid = f"{race}/{d['s']}"
        out[sid] = gen_upgrade_entry(d, race)
    return out


# ─── NEUTRAL ─────────────────────────────────────────────────────────────────
# 中立单位（矿脉/气井/瞭望塔/岩石等）不在 game_data.units（available=False），
# 所以没有 dump 数据 — 全部硬编码。cost/build_time=0（不可生产）。

NEUTRAL: list[dict] = [
    # ── 资源节点 ──
    dict(b="MINERALFIELD", s="mineralfield", zh="矿脉", short="矿",
         variants=["MINERALFIELD450","MINERALFIELD500","MINERALFIELD750",
                   "MINERALFIELD1000","MINERALFIELD1500","MINERALFIELD2000",
                   "BATTLESTATIONMINERALFIELD","BATTLESTATIONMINERALFIELD750",
                   "PURIFIERMINERALFIELD","PURIFIERMINERALFIELD750",
                   "PURIFIERMINERALFIELD1000","PURIFIERMINERALFIELD1500"]),
    dict(b="RICHMINERALFIELD", s="richmineralfield", zh="富矿脉", short="富矿",
         variants=["RICHMINERALFIELD750","RICHMINERALFIELD1000","RICHMINERALFIELD1500"]),
    dict(b="VESPENEGEYSER", s="vespenegeyser", zh="气井", short="气井",
         variants=["RICHVESPENEGEYSER","SPACEPLATFORMGEYSER"]),
    # ── 瞭望塔（地图中心常见；提供视野） ──
    dict(b="XELNAGATOWER", s="xelnagatower", zh="瞭望塔", short="塔",
         variants=["XELNAGATOWERRANGEINDICATORDUMMY"]),
    # ── 可破坏岩石/斜坡（堵路） ──
    dict(b="DESTRUCTIBLEROCKS6X6", s="destructiblerocks", zh="可破坏岩石", short="岩石",
         variants=["DESTRUCTIBLEROCKS2X2","DESTRUCTIBLEROCKS4X4","RAMP6X6"]),
    # 通用障碍物（adapt._unknown_stable_id 模式匹配归一到此：debris/ramp/wall/gate/ice 等）
    dict(b="DESTRUCTIBLEDEBRIS6X6", s="destructible", zh="障碍物", short="障碍"),
    # ── 可崩塌塔（新地图机制；站在上面可触发崩塌堵路） ──
    dict(b="COLLAPSIBLEROCKTOWER", s="collapsibletower", zh="可崩塌塔", short="崩塔",
         variants=["COLLAPSIBLETERRANTOWER","COLLAPSIBLEPURIFIERTOWER",
                   "COLLAPSIBLEROCKTOWERDEBRIS","COLLAPSIBLEROCKTOWERDIAGONAL",
                   "COLLAPSIBLEROCKTOWERPUSHUNIT",
                   "COLLAPSIBLEROCKTOWERRAMPLEFT","COLLAPSIBLEROCKTOWERRAMPRIGHT",
                   "COLLAPSIBLETERRANTOWERDEBRIS","COLLAPSIBLETERRANTOWERDIAGONAL",
                   "COLLAPSIBLETERRANTOWERPUSHUNIT",
                   "COLLAPSIBLETERRANTOWERRAMPLEFT","COLLAPSIBLETERRANTOWERRAMPRIGHT",
                   "COLLAPSIBLEPURIFIERTOWERDEBRIS","COLLAPSIBLEPURIFIERTOWERDIAGONAL",
                   "COLLAPSIBLEPURIFIERTOWERPUSHUNIT"]),
    # ── 其他常见中立物件 ──
    dict(b="DEBRIS2X2NONCONJOINED", s="debris", zh="残骸", short="残骸"),
]


def gen_neutral_entry(d: dict) -> dict:
    """Neutral units: all hardcoded (no dump data; available=False in game_data)."""
    entry: dict = {
        "burnysc2_name": d["b"],
        "display_name_zh": d["zh"],
        "short_name_zh": d["short"],
        "role": "building",
        "capabilities": [],
        "cost": {"minerals": 0, "vespene": 0, "supply": 0},
        "build_time": 0,
        "produced_by": None,
        "prerequisites": [],
    }
    if d.get("variants"):
        entry["variants"] = list(d["variants"])
    return entry


def main() -> None:
    dump = load_dump()
    for race, units, upgrades in [
        ("terran", TERRAN, TERRAN_UPGRADES),
        ("protoss", PROTOSS, PROTOSS_UPGRADES),
        ("zerg", ZERG, ZERG_UPGRADES),
    ]:
        data = generate_race(race, units, upgrades, dump)
        path = OUT_DIR / f"{race}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"{race}.json: {len(data)} entries → {path}")
    # Neutral units (no dump data — all hardcoded)
    neutral_data = {f"neutral/{d['s']}": gen_neutral_entry(d) for d in NEUTRAL}
    npath = OUT_DIR / "neutral.json"
    with npath.open("w", encoding="utf-8") as f:
        json.dump(neutral_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"neutral.json: {len(neutral_data)} entries → {npath}")


if __name__ == "__main__":
    main()
