"""从真机 SC2 提取全部单位/升级数据，输出 docs/game_data_dump.json。

用法：python dump_game_data.py
用于生成 terran/protoss/zerg 三族 catalog JSON 的准确经济数据（cost/build_time/supply）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer

try:
    from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
except ImportError:
    UNIT_TRAINED_FROM = {}

DUMP = Path(__file__).parent / "docs" / "game_data_dump.json"
FPS = 22.4  # frames per game second


def f2s(frames: float | int | None) -> int:
    """frames → game seconds（round(frames / 22.4)）。"""
    if not frames:
        return 0
    return round(frames / FPS)


class DumpBot(BotAI):
    async def on_start(self) -> None:
        self.unit_command_uses_self_do = True
        out: dict[str, dict] = {"units": {}, "upgrades": {}, "trained_from": {}}

        for uid_val, ud in self.game_data.units.items():
            race = ud.race.name  # "Terran" / "Protoss" / "Zerg" / "Neutral"
            # Include all races (Neutral too) so we can catalog map decorations
            p = ud._proto
            cab = ud.creation_ability
            morph = ud.morph_cost  # Cost | None
            corrected = ud.cost_zerg_corrected

            # effective cost: morph 优先（如 OrbitalCommand 只算升级差价），
            # 其次 zerg 建筑用 corrected（减去 drone 50），其余用 raw cost
            if morph is not None:
                eff = morph
            elif race == "Zerg" and "Structure" in [str(a) for a in ud.attributes]:
                eff = corrected
            else:
                eff = ud.cost

            entry = {
                "name": ud.name,
                "race": race,
                "id_value": uid_val,
                "minerals": eff.minerals,
                "vespene": eff.vespene,
                "build_time": f2s(eff.time),
                "supply": int(p.food_required),
                "food_provided": int(p.food_provided) if p.food_provided else 0,
                "tech_requirement": ud.tech_requirement.name if ud.tech_requirement else None,
                "tech_alias": [t.name for t in ud.tech_alias] if ud.tech_alias else None,
                "unit_alias": ud.unit_alias.name if ud.unit_alias else None,
                "creation_ability_button": cab.button_name if cab else None,
                "creation_ability_link": cab.link_name if cab else None,
                "creation_ability_remapped": str(cab.id) if cab else None,
                "attributes": [str(a).replace("Attribute.", "") for a in ud.attributes],
                "morph_cost_minerals": morph.minerals if morph else None,
                "morph_cost_vespene": morph.vespene if morph else None,
                "raw_minerals": int(p.mineral_cost),
                "raw_vespene": int(p.vespene_cost),
                "raw_build_time_frames": int(p.build_time),
                "footprint_radius": ud.footprint_radius,
            }
            out["units"][ud.name] = entry

        for uid_val, ud in self.game_data.upgrades.items():
            p = ud._proto
            ra = ud.research_ability
            # 推断种族：upgrade name 前缀
            name = ud.name
            race = "Unknown"
            for r in ("Terran", "Protoss", "Zerg"):
                if name.startswith(r):
                    race = r
                    break
            entry = {
                "name": name,
                "race": race,
                "id_value": uid_val,
                "minerals": int(p.mineral_cost),
                "vespene": int(p.vespene_cost),
                "build_time": f2s(p.research_time),
                "research_ability_button": ra.button_name if ra else None,
                "research_ability_link": ra.link_name if ra else None,
            }
            out["upgrades"][name] = entry

        # UNIT_TRAINED_FROM: UnitTypeId → [producer UnitTypeId, ...]
        for utid, producers in UNIT_TRAINED_FROM.items():
            out["trained_from"][utid.name] = [p.name for p in producers]

        DUMP.parent.mkdir(exist_ok=True)
        with DUMP.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"dumped {len(out['units'])} units, {len(out['upgrades'])} upgrades → {DUMP}")

        await self.client.leave()


def main() -> None:
    DUMP.unlink(missing_ok=True)
    run_game(
        maps.get("LadderMap"),
        [Bot(Race.Terran, DumpBot()), Computer(Race.Protoss, Difficulty.Easy)],
        realtime=False,
        game_time_limit=60,
    )


if __name__ == "__main__":
    main()
