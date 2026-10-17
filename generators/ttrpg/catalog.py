"""The tabletop RPG page catalog: page types, their layout variants, our own titles, and the
sections each variant prints (docs/PACKS.md section 1).

A variant's builder returns (sections, layout recipe) from the game system's sheet fields
(core.systems.sheet_fields_for): the character sheet, spell tracker, inventory and the
combat columns of the trackers follow the system's field list; everything else is
system-neutral. All wording here is ours and generic; no game, setting or publisher names.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

TYPE_LABELS: dict[str, str] = {
    "character_sheet": "Character Sheet",
    "spell_tracker": "Spell and Ability Tracker",
    "inventory": "Inventory and Loot",
    "campaign_tracker": "Campaign Tracker",
    "session_notes": "Session Notes",
    "npc_tracker": "NPC Tracker",
    "quest_log": "Quest Log",
    "initiative_tracker": "Initiative Tracker",
    "encounter_planner": "Encounter Planner",
    "worldbuilding_town": "World Building: Town",
    "worldbuilding_faction": "World Building: Faction",
    "worldbuilding_deity": "World Building: Deity",
    "worldbuilding_region": "World Building: Region",
    "hex_map": "Hex Map Paper",
    "grid_map": "Square Grid Map Paper",
    "dungeon_grid": "Dungeon Grid",
    "dice_log": "Dice and Roll Log",
}
PAGE_TYPES: tuple[str, ...] = tuple(TYPE_LABELS)
SYSTEM_AWARE = ("character_sheet", "spell_tracker", "inventory", "campaign_tracker", "npc_tracker",
                "initiative_tracker", "encounter_planner")

# Neutral page titles; GENRE_TITLES overrides a few per style.
TITLES: dict[str, tuple[str, ...]] = {
    "character_sheet": ("Character Record", "Hero Sheet", "Adventurer Record"),
    "spell_tracker": ("Spellbook", "Spells and Abilities", "Powers and Spells"),
    "inventory": ("Inventory", "Gear and Loot", "Pack and Pouch"),
    "campaign_tracker": ("Campaign Tracker", "Campaign Chronicle", "The Story So Far"),
    "session_notes": ("Session Notes", "Session Log", "Adventure Log"),
    "npc_tracker": ("People of Note", "Character Tracker", "Faces and Names"),
    "quest_log": ("Quest Log", "Quests and Rumors", "Job Board"),
    "initiative_tracker": ("Initiative Tracker", "Turn Order", "Combat Tracker"),
    "encounter_planner": ("Encounter Planner", "Battle Plan", "Encounter Notes"),
    "worldbuilding_town": ("Town Builder", "Settlement Notes", "Town Gazetteer"),
    "worldbuilding_faction": ("Faction Builder", "Factions and Guilds", "Power Groups"),
    "worldbuilding_deity": ("Deity Builder", "Gods and Faiths", "Divine Powers"),
    "worldbuilding_region": ("Region Builder", "Lands and Realms", "Region Gazetteer"),
    "hex_map": ("Hex Map", "Overland Hex Map", "Hex Crawl Map"),
    "grid_map": ("Battle Map", "Grid Map", "Tactical Grid"),
    "dungeon_grid": ("Dungeon Map", "Dungeon Grid", "Dungeon Planner"),
    "dice_log": ("Dice Log", "Roll Log", "Dice Tally"),
}
GENRE_TITLES: dict[str, dict[str, tuple[str, ...]]] = {
    "classic_fantasy": {"character_sheet": ("Adventurer Record", "Hero Sheet", "Character Record")},
    "dark_fantasy": {"character_sheet": ("Wanderer's Record", "Character Record", "Oathbound Record"),
                     "quest_log": ("Oaths and Quests", "Quest Log", "Grim Tasks")},
    "scifi": {"character_sheet": ("Crew Dossier", "Personnel File", "Character Profile"),
              "spell_tracker": ("Tech and Powers", "Abilities Log", "Power Matrix"),
              "session_notes": ("Mission Log", "Session Log", "Flight Log"),
              "quest_log": ("Mission Board", "Contracts", "Quest Log"),
              "worldbuilding_town": ("Station Builder", "Colony Notes", "Settlement Notes"),
              "worldbuilding_region": ("Sector Builder", "Planet Notes", "Region Builder"),
              "hex_map": ("Sector Hex Map", "Star Map Hexes", "Hex Map"),
              "dungeon_grid": ("Deck Plan Grid", "Facility Map", "Dungeon Grid")},
    "cosmic_horror": {"character_sheet": ("Investigator File", "Case Dossier", "Character Record"),
                      "spell_tracker": ("Rituals and Spells", "Forbidden Knowledge", "Spells and Abilities"),
                      "session_notes": ("Case Notes", "Investigation Log", "Session Notes"),
                      "npc_tracker": ("Persons of Interest", "Witnesses and Suspects", "People of Note"),
                      "quest_log": ("Open Cases", "Leads and Clues", "Quest Log")},
    "cyberpunk": {"character_sheet": ("Runner Profile", "Operative File", "Character Profile"),
                  "spell_tracker": ("Programs and Powers", "Abilities Log", "Tech and Powers"),
                  "session_notes": ("Run Log", "Session Log", "Job Notes"),
                  "npc_tracker": ("Contacts", "Fixers and Faces", "People of Note"),
                  "quest_log": ("Gigs and Jobs", "Contracts", "Job Board"),
                  "worldbuilding_faction": ("Corporations and Gangs", "Faction Builder", "Power Groups")},
    "pirate": {"character_sheet": ("Crew Articles", "Sailor's Record", "Character Record"),
               "session_notes": ("Ship's Log", "Voyage Log", "Session Notes"),
               "inventory": ("Cargo and Plunder", "Inventory", "Gear and Loot"),
               "hex_map": ("Sea Chart Hexes", "Voyage Map", "Hex Map")},
    "western": {"character_sheet": ("Drifter's Record", "Frontier Record", "Character Record"),
                "quest_log": ("Bounties and Jobs", "Wanted Board", "Quest Log"),
                "session_notes": ("Trail Journal", "Session Log", "Session Notes"),
                "worldbuilding_town": ("Frontier Town", "Boomtown Notes", "Town Builder")},
    "cozy": {"character_sheet": ("Adventurer Journal", "Hero Notes", "Character Record"),
             "session_notes": ("Adventure Journal", "Story Notes", "Session Notes"),
             "inventory": ("Satchel and Treasures", "Inventory", "Gear and Loot")},
}


def titles_for(genre: str, page_type: str) -> tuple[str, ...]:
    return (GENRE_TITLES.get(genre) or {}).get(page_type) or TITLES[page_type]


# --- section constructors -------------------------------------------------------------------

def lines(*fields: str, cols: int | None = None, title: str | None = None, **kw) -> dict:
    d = {"kind": "lines", "fields": list(fields)}
    if cols:
        d["cols"] = cols
    if title:
        d["title"] = title
    return {**d, **kw}


def notes(title: str, n: int = 3, **kw) -> dict:
    return {"kind": "notes", "title": title, "lines": n, **kw}


def table(title: str, columns: list[str], weights: list[float] | None = None, rows: int = 3, **kw) -> dict:
    d = {"kind": "table", "title": title, "columns": list(columns), "rows": rows}
    if weights:
        d["weights"] = list(weights)
    return {**d, **kw}


def boxes(title: str, fields: list[str], cols: int | None = None, **kw) -> dict:
    d = {"kind": "boxes", "title": title, "fields": list(fields)}
    if cols:
        d["cols"] = cols
    return {**d, **kw}


def prompts(title: str, fields: list[str], n: int = 2, **kw) -> dict:
    return {"kind": "prompts", "title": title, "fields": list(fields), "lines": n, **kw}


def cards(title: str, count: int, cols: int, card: list[dict], **kw) -> dict:
    return {"kind": "cards", "title": title, "count": count, "cols": cols, "card": card, "span": "full", **kw}


def grid(shape: str, pitch_in: float, **kw) -> dict:
    return {"kind": "grid", "shape": shape, "pitch_in": pitch_in, **kw}


def sketch(title: str, min_mm: float = 30, **kw) -> dict:
    return {"kind": "sketch", "title": title, "min_mm": min_mm, **kw}


def track(title: str, items: list[tuple[str, int]], **kw) -> dict:
    return {"kind": "track", "title": title, "fields": [{"label": a, "count": b} for a, b in items], **kw}


def line_el(label: str) -> dict:
    return {"t": "line", "label": label}


def lines_el(label: str, n: int = 1) -> dict:
    return {"t": "lines", "label": label, "n": n}


# --- builders ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Build:
    """What a builder may use: the system's sheet fields (core.systems) and the alt number."""

    fields: dict
    alt: int = 0

    @property
    def combat(self) -> dict:
        return self.fields["combat"]

    @property
    def currency(self) -> list[str]:
        return list(self.fields["currency"])

    @property
    def spell_card(self) -> list[str]:
        return list(self.fields["spell_card"])


Recipe = dict
Builder = Callable[[Build], tuple[list[dict], Recipe]]


def _two(secs) -> tuple[list[dict], Recipe]:
    return secs, {"cols": 2}


def _one(secs) -> tuple[list[dict], Recipe]:
    return secs, {"cols": 1}


def cs_core(b: Build):
    return copy.deepcopy(b.fields["core"]), {"cols": 2}


def cs_core_alt(b: Build):
    return copy.deepcopy(b.fields["core"]), {"cols": 2, "strip": True}


def cs_details(b: Build):
    return copy.deepcopy(b.fields["details"]), {"cols": 2}


def cs_backstory(b: Build):
    return _two([
        lines("Character Name", "Player", cols=2),
        sketch("Portrait", 58, span="column"),
        boxes("Appearance", ["Age", "Height", "Build", "Eyes", "Hair", "Distinguishing Marks"], cols=2),
        prompts("Personality", ["Goals", "Fears", "Secrets"], 2),
        notes("Backstory", 6, flex=2.0),
        notes("Allies and Rivals", 3),
    ])


def spell_book(b: Build):
    return copy.deepcopy(b.fields["spells"]), {"cols": 2}


def spell_cards(b: Build):
    card = [line_el("Name"), {"t": "boxes", "labels": b.spell_card[:5]}, lines_el("Effect", 2),
            {"t": "checks", "labels": ["Prepared", "Used"]}]
    return _one([lines("Character Name", "Source of Power", cols=2), cards("Spell and Ability Cards", 6, 2, card)])


def spell_list(b: Build):
    first = b.spell_card[0]
    return _one([lines("Character Name", "Source of Power", cols=2),
                 table("Spell and Ability List", [first, "Name", "Uses", "Notes"], [1, 4, 1, 4], rows=14)])


def inv_pack(b: Build):
    return _one([lines("Character Name", "Carrying Limit", cols=2),
                 boxes("Coins and Wealth", b.currency, cols=len(b.currency), span="full"),
                 table("Equipment", ["Item", "Qty", "Weight", "Value", "Notes"], [4, 1, 1, 1, 3], rows=10),
                 notes("Stored Elsewhere", 3, flex=0.25)])


def inv_loot(b: Build):
    return _one([lines("Party", "Session", cols=2),
                 boxes("Party Funds", b.currency, cols=len(b.currency), span="full"),
                 table("Party Loot", ["Item", "Value", "Found At", "Claimed By"], [4, 1.2, 3, 2], rows=10),
                 notes("Shares and Debts", 2, flex=0.25)])


def inv_treasure(b: Build):
    card = [line_el("Item"), {"t": "uses", "label": "Charges", "count": 8}, lines_el("Properties", 2),
            {"t": "checks", "labels": ["Identified", "Equipped", "Carried"]}]
    return _one([lines("Character Name", "Player", cols=2), cards("Special Items", 6, 2, card)])


def camp_overview(b: Build):
    return _one([lines("Campaign", "Game Master", "Started", "Setting", cols=2),
                 notes("Premise", 3, flex=0),
                 table("Party", ["Player", "Character", "Role", "Level"], [2, 3, 2, 1], rows=5, flex=0),
                 table("Story Arcs", ["Arc", "Status", "Notes"], [3, 1, 4], rows=4),
                 notes("Mysteries and Threads", 3)])


def camp_timeline(b: Build):
    return _one([lines("Campaign", "Party", cols=2),
                 table("Session Timeline", ["Session", "Date", "In-World Date", "Key Events"], [1, 1.3, 1.6, 6],
                       rows=14)])


def camp_party(b: Build):
    card = [line_el("Character"), line_el("Player"),
            {"t": "boxes", "labels": ["Role", "Level", b.combat["defense"], b.combat["health"]]},
            lines_el("Notes", 2)]
    return _one([lines("Campaign", "Party Name", cols=2), cards("Party Members", 6, 2, card)])


def sess_lined(b: Build):
    return _one([lines("Session", "Date", "In-World Date", cols=3),
                 notes("Session Notes", 10, flex=3.0),
                 boxes("Rewards", ["Experience", "Treasure", "Milestones"], cols=3, span="full")])


def sess_structured(b: Build):
    return _two([lines("Session", "Date", "In-World Date", cols=3),
                 notes("Recap", 4, span="full", flex=0.6),
                 prompts("What Happened", ["Key Moments", "People Met", "Places Visited"], 2),
                 prompts("Threads", ["Loot and Rewards", "Open Questions", "Next Time"], 2)])


def sess_prep(b: Build):
    return _two([lines("Session", "Date", cols=2),
                 notes("Opening Scene", 3, span="full", flex=0),
                 table("Possible Scenes", ["Scene", "Location", "Used"], [4, 3, 1.2], rows=5),
                 table("Clues to Reveal", ["Clue", "Revealed"], [5, 1.6], rows=5),
                 prompts("Cast and Places", ["Key Characters", "Locations"], 2),
                 notes("Rewards", 2)])


def npc_table(b: Build):
    return _one([lines("Campaign", "Region", cols=2),
                 table("Characters Met", ["Name", "Role", "Location", "Attitude", "Notes"], [2.4, 2, 2, 1.4, 4],
                       rows=15)])


def npc_profiles(b: Build):
    card = [{"t": "portrait", "mm": 22}, line_el("Name"), line_el("Role"), line_el("Location"),
            {"t": "boxes", "labels": [b.combat["defense"], b.combat["health"], "Attitude"]},
            lines_el("Wants", 1), lines_el("Secret", 1), lines_el("Notes", 1)]
    return _one([cards("Character Profiles", 4, 2, card)])


def npc_relations(b: Build):
    return _one([lines("Campaign", "Party", cols=2),
                 table("Allies and Friends", ["Name", "Role", "Where Met", "Debts and Favors"], [2.4, 2, 2, 3.6],
                       rows=8),
                 table("Rivals and Enemies", ["Name", "Role", "Grudge", "Last Seen"], [2.4, 2, 3.6, 2], rows=8)])


def quest_log(b: Build):
    return _one([lines("Campaign", "Party", cols=2),
                 table("Quests", ["Quest", "Given By", "Reward", "Status"], [4, 2, 2, 1.3], rows=15)])


def quest_cards(b: Build):
    card = [line_el("Quest"), line_el("Given By"), lines_el("Objective", 2), line_el("Reward"),
            lines_el("Complications", 1), {"t": "checks", "labels": ["Accepted", "Completed", "Failed"]}]
    return _one([cards("Quest Cards", 4, 2, card)])


def quest_rumors(b: Build):
    return _one([lines("Campaign", "Party", cols=2),
                 table("Rumors and Leads", ["Rumor or Lead", "Heard From", "Verified", "Followed Up"],
                       [5, 2.4, 1.2, 1.6], rows=10),
                 table("Finished Quests", ["Quest", "Session", "Outcome and Reward"], [4, 1.2, 4], rows=6)])


def _order_cols(b: Build) -> list[str]:
    return [b.combat["order"], "Name", b.combat["defense"], b.combat["health"], "Conditions"]


def init_table(b: Build):
    return _one([lines("Encounter", "Location", cols=2),
                 {"kind": "rounds", "title": "Rounds", "count": 12},
                 table("Turn Order", _order_cols(b), [1.3, 3, 1.3, 1.3, 3], rows=12)])


def init_rounds(b: Build):
    secs = []
    for x in ("A", "B", "C"):
        secs.append(table(f"Encounter {x}", _order_cols(b), [1.3, 3, 1.3, 1.3, 3], rows=5))
        secs.append({"kind": "rounds", "title": f"Encounter {x} Rounds", "count": 10})
    return _one(secs)


def enc_single(b: Build):
    return _one([lines("Encounter", "Location", "Difficulty", cols=3),
                 notes("Objective", 2, flex=0),
                 table("Opponents", ["Opponent", "Count", b.combat["defense"], b.combat["health"], "Notes"],
                       [3, 1, 1.3, 1.3, 3], rows=5, flex=0),
                 sketch("Battlefield Sketch", 55, dots=True, flex=2.0),
                 prompts("Plans", ["Tactics", "Terrain and Hazards", "Rewards"], 1)])


def enc_double(b: Build):
    secs = []
    for x in ("A", "B"):
        secs += [lines("Name", "Location", "Difficulty", cols=3, title=f"Encounter {x}"),
                 table(f"Opponents {x}", ["Opponent", "Count", b.combat["defense"], b.combat["health"], "Notes"],
                       [3, 1, 1.3, 1.3, 3], rows=4),
                 prompts(f"Plans {x}", ["Tactics", "Rewards"], 1)]
    return _one(secs)


def town_overview(b: Build):
    return _two([lines("Name", "Population", "Ruled By", "Region", cols=4),
                 prompts("At a Glance", ["Economy", "Defenses", "Mood and Look"], 1),
                 notes("Rumors and Hooks", 3),
                 table("Notable Places", ["Place", "Description"], [2, 5], rows=5, span="full"),
                 table("Notable People", ["Name", "Role", "Notes"], [2, 2, 4], rows=5, span="full")])


def town_map(b: Build):
    return _one([lines("Name", "Region", cols=2),
                 grid("square", 0.25, major=4, title="Town Map"),
                 table("Map Key", ["No.", "Location", "Notes"], [1, 4, 4], rows=5, flex=0, span="full")])


def faction_overview(b: Build):
    return _two([lines("Faction", "Leader", "Headquarters", cols=3),
                 sketch("Symbol", 38, flex=0),
                 prompts("Purpose", ["Goals", "Methods", "Resources"], 2),
                 table("Ranks", ["Rank", "Title", "Duties"], [1, 2, 4], rows=5, span="full"),
                 prompts("Relations", ["Allies", "Rivals", "Secrets"], 1, span="full")])


def faction_relations(b: Build):
    return _one([lines("Faction", "Leader", cols=2),
                 table("Standing With Other Groups",
                       ["Group", "Hostile", "Wary", "Neutral", "Friendly", "Allied", "Notes"],
                       [3, 1.1, 1, 1.1, 1.1, 1, 3], rows=10),
                 track("Influence", [("Territory", 10), ("Wealth", 10), ("Reach", 10)], span="full"),
                 notes("Recent Events", 3)])


def deity_profile(b: Build):
    return _two([lines("Name", "Titles", "Domains", cols=3),
                 sketch("Holy Symbol", 38, flex=0),
                 prompts("Faith", ["Tenets", "Worshippers", "Holy Days"], 2),
                 prompts("Places", ["Temples and Shrines", "Sacred Sites"], 2, span="full"),
                 notes("Myths and Legends", 4, span="full")])


def deity_pantheon(b: Build):
    return _one([lines("Pantheon", "Setting", cols=2),
                 table("Pantheon", ["Deity", "Domains", "Symbol", "Followers", "Notes"], [2, 2, 1.5, 2, 3], rows=15)])


def region_overview(b: Build):
    return _two([lines("Region", "Climate", "Terrain", cols=3),
                 grid("hex", 0.5, title="Sketch Map", flex=1.5),
                 prompts("Places", ["Settlements", "Landmarks"], 2),
                 prompts("Travel", ["Dangers", "Resources", "Travel Notes"], 1)])


def region_map(b: Build):
    return _one([lines("Region", "Scale", cols=2),
                 grid("hex", 0.5, title="Region Map"),
                 table("Map Key", ["Hex", "Feature", "Notes"], [1, 3, 4], rows=5, flex=0, span="full")])


def _map_style(b: Build, sec: dict) -> dict:
    """Repeats of a map layout alternate line styles (dotted lines, center dots)."""
    if b.alt % 2:
        if sec["shape"] == "square":
            sec["style"] = "dotted"
        elif sec["shape"].startswith("hex"):
            sec["centers"] = True
    return sec


def _hex(pitch: float, shape: str = "hex"):
    def build(b: Build):
        return _one([lines("Map", "Scale", cols=2), _map_style(b, grid(shape, pitch))])
    return build


def hex_region(b: Build):
    return _one([lines("Map", "Scale", cols=2), _map_style(b, grid("hex", 0.5)),
                 table("Key", ["Hex", "Notes"], [1, 6], rows=4, flex=0, span="full")])


def _square(pitch: float, major: int = 0, shape: str = "square"):
    def build(b: Build):
        return _one([lines("Map", "Scale", cols=2), _map_style(b, grid(shape, pitch, major=major))])
    return build


def grid_notes(b: Build):
    return _one([lines("Map", "Scale", cols=2), _map_style(b, grid("square", 0.25, major=4)),
                 notes("Notes", 3, flex=0, span="full")])


def dungeon_key(b: Build):
    return _one([lines("Dungeon", "Level", cols=2), _map_style(b, grid("square", 0.25, major=4)),
                 {"kind": "legend", "title": "Map Symbols", "span": "full"},
                 table("Room Key", ["Room", "Contents", "Cleared"], [1, 6, 1.3], rows=4, flex=0, span="full")])


def dungeon_dots(b: Build):
    return _one([lines("Dungeon", "Level", cols=2), grid("dots", 0.25),
                 {"kind": "legend", "title": "Map Symbols", "span": "full"}])


def dungeon_iso(b: Build):
    return _one([lines("Dungeon", "Level", cols=2), grid("iso", 0.3),
                 table("Room Key", ["Room", "Contents", "Cleared"], [1, 6, 1.3], rows=3, flex=0, span="full")])


def dungeon_rooms(b: Build):
    return _two([lines("Dungeon", "Level", cols=2),
                 _map_style(b, grid("square", 0.25, major=5, span="column", flex=3.0)),
                 table("Rooms", ["Room", "Contents", "Cleared"], [1, 4, 1.6], rows=10, span="column")])


def dice_rolls(b: Build):
    return _one([lines("Character", "Campaign", cols=2),
                 table("Roll Log", ["Session", "Roll For", "Die", "Result", "Notes"], [1, 3, 1, 1, 3], rows=15),
                 track("Highs and Lows", [("Highest Rolls", 10), ("Lowest Rolls", 10)], span="full")])


def dice_stats(b: Build):
    return _one([lines("Dice Set", "Campaign", cols=2),
                 {"kind": "tally", "title": "Roll Tally", "dice": ["d4", "d6", "d8", "d10", "d12", "d20", "d100"],
                  "span": "full"},
                 notes("Lucky and Unlucky Moments", 3)])


@dataclass(frozen=True)
class Variant:
    name: str
    label: str
    build: Builder


def _v(name: str, label: str, build: Builder) -> Variant:
    return Variant(name, label, build)


VARIANTS: dict[str, tuple[Variant, ...]] = {
    "character_sheet": (_v("core", "Core Stats", cs_core), _v("details", "Details and Gear", cs_details),
                        _v("core_alt", "Core Stats, Score Column", cs_core_alt),
                        _v("backstory", "Backstory and Portrait", cs_backstory)),
    "spell_tracker": (_v("spellbook", "Slots and Spells", spell_book), _v("cards", "Spell Cards", spell_cards),
                      _v("list", "Spell List", spell_list)),
    "inventory": (_v("pack", "Equipment and Coins", inv_pack), _v("loot", "Party Loot", inv_loot),
                  _v("treasure", "Special Items", inv_treasure)),
    "campaign_tracker": (_v("overview", "Overview", camp_overview), _v("timeline", "Session Timeline", camp_timeline),
                         _v("party", "Party Roster", camp_party)),
    "session_notes": (_v("lined", "Lined Notes", sess_lined), _v("structured", "Guided Recap", sess_structured),
                      _v("prep", "Game Prep", sess_prep)),
    "npc_tracker": (_v("table", "Contact List", npc_table), _v("profiles", "Profiles", npc_profiles),
                    _v("relations", "Allies and Rivals", npc_relations)),
    "quest_log": (_v("log", "Quest List", quest_log), _v("cards", "Quest Cards", quest_cards),
                  _v("rumors", "Rumors and Leads", quest_rumors)),
    "initiative_tracker": (_v("table", "Turn Order", init_table), _v("rounds", "Three Encounters", init_rounds)),
    "encounter_planner": (_v("single", "One Encounter", enc_single), _v("double", "Two Encounters", enc_double)),
    "worldbuilding_town": (_v("overview", "Overview", town_overview), _v("map", "Town Map", town_map)),
    "worldbuilding_faction": (_v("overview", "Overview", faction_overview),
                              _v("relations", "Relations", faction_relations)),
    "worldbuilding_deity": (_v("profile", "Profile", deity_profile), _v("pantheon", "Pantheon", deity_pantheon)),
    "worldbuilding_region": (_v("overview", "Overview", region_overview), _v("map", "Region Map", region_map)),
    "hex_map": (_v("medium", "3/4 in (19 mm) Hexes", _hex(0.75)), _v("small", "1/2 in (13 mm) Hexes", _hex(0.5)),
                _v("large", "1 in (25 mm) Hexes", _hex(1.0)), _v("region", "1/2 in Hexes with Key", hex_region),
                _v("pointy", "3/4 in Pointed Hexes", _hex(0.75, "hex_pointy"))),
    "grid_map": (_v("inch", "1 in (25 mm) Squares", _square(1.0)),
                 _v("half", "1/2 in (13 mm) Squares", _square(0.5, 2)),
                 _v("quarter", "1/4 in (6 mm) Squares", _square(0.25, 4)),
                 _v("notes", "1/4 in Squares with Notes", grid_notes),
                 _v("dots", "1/4 in Dot Grid", _square(0.25, 0, "dots"))),
    "dungeon_grid": (_v("key", "Grid with Map Key", dungeon_key), _v("dots", "Dot Grid with Symbols", dungeon_dots),
                     _v("iso", "Isometric Dots", dungeon_iso), _v("rooms", "Grid with Room List", dungeon_rooms)),
    "dice_log": (_v("rolls", "Roll Log", dice_rolls), _v("stats", "Dice Tally", dice_stats)),
}
assert set(VARIANTS) == set(PAGE_TYPES)


def variant(page_type: str, name: str) -> Variant:
    for v in VARIANTS[page_type]:
        if v.name == name:
            return v
    raise KeyError(f"{page_type} has no layout {name!r}")


def subject_for(page_type: str, variant_name: str, alt: int = 0) -> str:
    """The page's subject (what the design set records): our own words, e.g.
    "character sheet: core stats", "hex map paper: 1/2 in (13 mm) hexes, version 2"."""
    base = f"{TYPE_LABELS[page_type].lower()}: {variant(page_type, variant_name).label.lower()}"
    return base if alt == 0 else f"{base}, version {alt + 1}"
