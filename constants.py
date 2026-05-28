"""
Shadow Man Remastered Randomizer — Shared Constants
────────────────────────────────────────────────────
Single source of truth for level folders and RSC file types.
Imported by patcher.py, kpf_handler.py, and extracted_locations.py.
"""

LEVEL_FOLDERS = [
    "swampday", "tenement", "prison", "uground", "florida", "salvage",
    "swampnit", "ntenemnt", "nprison", "nuground", "nflorida", "nsalvage",
    "deadside", "wastland",
    "asylum", "as2exper", "as3schis", "as4dkeng",
    "t1tchgad", "t2wlkgad", "t3swmgad", "t4ndgad",
    "ah1cagew", "ah2playr", "ah3lavad", "ah4fogom",
    "asyiggy",
]

SOUL_RSC_FILES = {
    "quest.rsc",
    "instance.rsc",
    "fx.rsc",
    "resource.rsc",
    "pickups.rsc",
    "enemies.rsc",
}

ENEMY_RSC_FILES = {"enemies.rsc", "enemys.rsc","objects.rsc", "resource.rsc","events.rsc","day.rsc","night.rsc"}

# File extensions extracted from KPF for randomizer use
KPF_TARGET_EXTENSIONS = {".rsc", ".evt", ".e2o"}

# ── Item name constants ───────────────────────────────────────────────────────

_ECLIPSER_1      = "RSC_X_ECLIPSER_PART1"
_ECLIPSER_2      = "RSC_X_ECLIPSER_PART2"
_ECLIPSER_3      = "RSC_X_ECLIPSER_PART3"
_BATON           = "RSC_X_BATON"
_FLAMBEAU        = "RSC_X_FLAMBEAU"
_MARTEAU         = "RSC_X_MARTEAU"
_CALABASH        = "RSC_X_CALABASH"
_POIGNE          = "RSC_X_POIGNE"
_ENG_KEY         = "RSC_X_ENGINEERS_KEY"
_PRISON_KEY_CARD = "RSC_X_PRISON_KEY_CARD"
_ACCUMULATOR     = "RSC_X_ACCUMULATOR"
_GAD_PICKUP      = "RSC_X_GAD_PICKUP"

STARTING_ITEM_POOL: dict[str, str] = {
    "Engineers Key":      "RSC_X_ENGINEERS_KEY",
    "Baton":              "RSC_X_BATON",
    "Flashlight":         "RSC_X_FLASHLIGHT",
    "Poigne":             "RSC_X_POIGNE",
    "Calabash":           "RSC_X_CALABASH",
    "Flambeau":           "RSC_X_FLAMBEAU",
    "Marteau":            "RSC_X_MARTEAU",
    "Prison Key Card":    "RSC_X_PRISON_KEY_CARD",
    "Eclipser":           "RSC_X_ECLIPSER_PART1",
    "Retractor":          "RSC_X_RETRACT",
    "Accumulator":        "RSC_X_ACCUMULATOR",
    "Asson":              "RSC_X_ASSON",
    "Shotgun":            "RSC_X_SHOTGUN",
    "Sawed-off Shotgun":  "RSC_X_SHOTGUN2",
    "MP-909":             "RSC_X_MP5",
    "0.9-SMG":            "RSC_X_MAC10",
    "Enseigne":           "RSC_X_ENSEIGNE",
    "Tete de Mort":       "RSC_X_TETEDEMORT",
    "Book of Shadows":    "RSC_X_BOOK_OF_SHADOWS",
    "Book of Prophecy":   "RSC_X_PROPHECY",
    "Jacks Schematic":    "RSC_X_JACKS_SCHEMATIC",
    "Light Soul":         "RSC_X_LIGHT_SOUL",
    "Violator":           "RSC_Q_VIOLATOR",
    "Gad Power Upgrade":  "RSC_X_GAD_PICKUP",
}

# ── Asset overrides ──────────────────────────────────────────────────────────
# (source relative to randomizer root, dest relative to game dir)
# Applied unconditionally on every randomizer run.
ASSET_OVERRIDES: list[tuple[str, str]] = [
    (r"assets\smrr_title_logo.png", r"gfx\ShadowMan_logo.png"),
    # (r"data\pot.dds", r"hdtextures\meshes\items\pot\000pot.dds"),
    (r"assets\yellow_crate_1.dds", r"hdtextures\levels\uground\objects\tga\019crate.dds"),
    (r"assets\yellow_crate_2.dds", r"hdtextures\levels\uground\objects\tga\020crate.dds"),
    # (r"assets\shadow_man_shirt.dds", r"hdtextures\anims\mike\textures\005shirt.dds"),
]

# Applied only when shuffle_gad_temples is enabled — replaces the Book of
# Prophecy inventory icon with a Gad-themed one so the item doesn't mislead.
GAD_ASSET_OVERRIDES: list[tuple[str, str]] = [
    (r"assets\book_of_gad.png", r"invitems\PROPHECY.PNG"),
    (r"assets\hd_book_of_gad.png", r"hdtextures\invitems\PROPHECY.PNG"),
    (r"assets\hd_book_of_gad.tga", r"hdtextures\newitems\tga\085.tga"),
    (r"assets\book_of_gad.tga", r"newitems\tga\085.tga"),
]

# ── MSH overrides ────────────────────────────────────────────────────────────
# (source msh relative to randomizer root, dest internal KPF path, scale factor)
MSH_OVERRIDES: list[tuple[str, float]] = [
    (r"levels/uground/objects/crate.msh", 1.0),
]

# ── Gate constants ────────────────────────────────────────────────────────────

GATE_VANILLA_SL: dict[str, int] = {
    # ── Deadside gates ────────────────────────────────────────────────────────
    "GATE_DEADSIDE_MARROW"      :  0,  # ARC0  — Path of Shadows entry
    "GATE_DEADSIDE_WASTELAND"   :  1,  # ARC1  — deadside → wasteland
    "GATE_DEADSIDE_ASYLUM"      :  2,  # ARC2  — deadside → asylum
    "GATE_DEADSIDE_PATH_3"      :  3,  # ARC3a — lower la lune path
    "GATE_DEADSIDE_LALUNE"      :  3,  # ARC3b — upper la lune / flambeau path
    "GATE_DEADSIDE_CAGEWAYS"    :  4,  # ARC4  — cageways entry
    "GATE_DEADSIDE_PLAYROOMS"   :  5,  # ARC5  — playrooms entry
    "GATE_DEADSIDE_PATH_6"      :  6,  # ARC6  — le soleil path
    "GATE_DEADSIDE_PATH_7"      :  7,  # ARC7a — temple of prophecy upper
    "GATE_DEADSIDE_LAVADUCTS"   :  7,  # ARC7b — lavaducts path
    "GATE_DEADSIDE_LALAME"      :  7,  # ARC7c — la lame / lower deadside
    "GATE_DEADSIDE_BLOOD"       :  8,  # ARC8  — temple of blood approach
    "GATE_DEADSIDE_FOGOMETERS"  :  9,  # ARC9  — fogometers approach
    "GATE_DEADSIDE_MYSTERY"     : 10,  # ARC10 — final gate
    # ── Non-deadside gates ────────────────────────────────────────────────────
    "GATE_WASTELAND_ENSEIGNE"   :  6,  # wastland  — interior enseigne gate
    "GATE_FIRE_POIGNE"          :  4,  # t1tchgad  — Temple of Fire lower gate
    "GATE_FIRE_FLAMBEAU"        :  5,  # t1tchgad  — Temple of Fire upper gate
    "GATE_PROPHECY_INTERIOR"    :  7,  # t2wlkgad  — Temple of Prophecy interior
    "GATE_BLOOD_INTERIOR"       :  9,  # t3swmgad  — Temple of Blood interior
    "GATE_FOGOMETERS_INTERIOR"  : 10,  # ah4fogom  — Fogometers interior
}

# XZ positions for matching gate_id → physical location in game world.
# Used by the e2o patcher and the levels.txt coffingate patcher.
# Tolerance for matching: E2O_MATCH_RADIUS game units.
E2O_MATCH_RADIUS: int = 500
GATE_E2O_POSITIONS: dict[str, tuple[str, int, int]] = {
    "GATE_DEADSIDE_MARROW"      : ("deadside",    -836,  20326),
    "GATE_DEADSIDE_WASTELAND"   : ("deadside",     437,  23503),
    "GATE_DEADSIDE_ASYLUM"      : ("deadside",    -641,  25394),
    "GATE_DEADSIDE_PATH_3"      : ("deadside",   -2580,  26716),
    "GATE_DEADSIDE_LALUNE"      : ("deadside",   -3245,  29072),
    "GATE_DEADSIDE_CAGEWAYS"    : ("deadside",    2319,  24462),
    "GATE_DEADSIDE_PLAYROOMS"   : ("deadside",    4034,  21491),
    "GATE_DEADSIDE_PATH_6"      : ("deadside",    -989,  19729),
    "GATE_DEADSIDE_LAVADUCTS"   : ("deadside",    -509,  15790),
    "GATE_DEADSIDE_PATH_7"      : ("deadside",     305,  22806),
    "GATE_DEADSIDE_LALAME"      : ("deadside",   -1234,  11068),
    "GATE_DEADSIDE_BLOOD"       : ("deadside",   -3147,  15634),
    "GATE_DEADSIDE_FOGOMETERS"  : ("deadside",   -1746,  14396),
    "GATE_DEADSIDE_MYSTERY"     : ("deadside",   -2865,   5298),
    "GATE_WASTELAND_ENSEIGNE"   : ("wastland",    5057,   7727),
    "GATE_FIRE_POIGNE"          : ("t1tchgad",     920,   4399),
    "GATE_FIRE_FLAMBEAU"        : ("t1tchgad",    6322,   4686),
    "GATE_PROPHECY_INTERIOR"    : ("t2wlkgad",   -3940, -13135),
    "GATE_BLOOD_INTERIOR"       : ("t3swmgad",   -1899, -11809),
    "GATE_FOGOMETERS_INTERIOR"  : ("ah4fogom",  -14955,  11890),
}

# Gates that only guard a key item and a handful of checks
ITEM_GATE_IDS: frozenset[str] = frozenset({
    "GATE_DEADSIDE_LALUNE",
    "GATE_DEADSIDE_LALAME",
    "GATE_DEADSIDE_MYSTERY",
    "GATE_WASTELAND_ENSEIGNE",
    "GATE_FIRE_POIGNE",
    "GATE_FIRE_FLAMBEAU",
    "GATE_PROPHECY_INTERIOR",
    "GATE_BLOOD_INTERIOR",
    "GATE_FOGOMETERS_INTERIOR",
})

# Explicit open-gate order: the 7 linear deadside coffin gates in sequence.
# Beyond these, gates branch and have no clear linear order — open_gates_n > 7
# will shuffle the remainder randomly using the seed RNG.
COFFIN_GATE_ORDER: tuple[str, ...] = (
    "GATE_DEADSIDE_MARROW",     # 1 — Path of Shadows entry
    "GATE_DEADSIDE_WASTELAND",  # 2 — deadside → wasteland
    "GATE_DEADSIDE_ASYLUM",     # 3 — deadside → asylum
    "GATE_DEADSIDE_PATH_3",     # 4 — lower la lune path
    "GATE_DEADSIDE_CAGEWAYS",   # 5 — cageways entry
    "GATE_DEADSIDE_PLAYROOMS",  # 6 — playrooms entry
    "GATE_DEADSIDE_PATH_6",     # 7 — le soleil path
)

# Shuffle Gate Difficulties
_HARD_LOCKED: frozenset[str] = frozenset({
    "GATE_DEADSIDE_MARROW",
    "GATE_DEADSIDE_WASTELAND",
    "GATE_DEADSIDE_ASYLUM",
})

_EASY_LOCKED: frozenset[str] = _HARD_LOCKED | frozenset({
    "GATE_DEADSIDE_PATH_3",
    "GATE_DEADSIDE_CAGEWAYS",
    "GATE_DEADSIDE_PLAYROOMS",
    "GATE_DEADSIDE_PATH_6",
})

GATE_PRESETS: dict[str, dict] = {
    "open": {
        "shuffle_gates": False,
        "no_soul_gates": True,
        "open_gates_n": 0,      # moot — no_soul_gates already opens everything
        "lock_gates": frozenset(),
        "max_sl": None,
        "safe": True,
    },
    "easy": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "open_gates_n": 6,      # first 3 gates open by default on easy
        "lock_gates": _EASY_LOCKED,
        "max_sl": 7,
        "safe": True,
    },
    "medium": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "open_gates_n": 3,
        "lock_gates": _HARD_LOCKED,
        "max_sl": 8,
        "safe": True,
    },
    "hard": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "open_gates_n": 1,
        "lock_gates": _HARD_LOCKED,
        "max_sl": None,
        "safe": True,
    },
    "chaos": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "open_gates_n": 0,
        "lock_gates": frozenset(),
        "max_sl": None,
        "safe": False,
    },
}

# ── EXE item type IDs ─────────────────────────────────────────────────────────
# Source: item string table at VA 0x140011b00 in thoth_x64.exe

ITEM_IDS: dict[str, int] = {
    "IDD_ACCUMULATOR":  0x01,
    "IDD_ASSON":        0x02,
    "IDD_BATON":        0x03,
    "IDD_BOOKOFSHADOWS":0x04,
    "IDD_CADEAUX":      0x05,
    "IDD_CALABASH":     0x06,
    "IDD_DESERTEAGLE":  0x07,
    "IDD_ECLIPSER":     0x08,
    "IDD_ENGINEERSKEY": 0x09,
    "IDD_ENSEIGNE":     0x0a,
    "IDD_FLAMBEAU":     0x0b,
    "IDD_FLASHLIGHT":   0x0e,
    "IDD_HEALTH":       0x0f,
    "IDD_LALUNE":       0x10,
    "IDD_LALAME":       0x11,
    "IDD_MARTEAU":      0x12,
    "IDD_POIGNE":       0x13,
    "IDD_PRISM":        0x14,
    "IDD_PRISONCARD":   0x15,
    "IDD_PROPHECY":     0x16,
    "IDD_RETRACTOR":    0x17,
    "IDD_SCHEMATIC":    0x18,
    "IDD_SHADOWGUN":    0x19,
    "IDD_SHOTGUN":      0x1a,
    "IDD_SHOTGUNAMMO":  0x1b,
    "IDD_SOLEIL":       0x1c,
    "IDD_TEDDY":        0x1d,
    "IDD_VIOLATOR":     0x1e,
    "IDD_VIOLATORAMMO": 0x1f,
    "IDD_SHOTGUN2":     0x230,
}

ITEM_NAMES: dict[int, str] = {v: k for k, v in ITEM_IDS.items()}

RSC_TO_ITEM_ID: dict[str, int] = {
    "RSC_X_ACCUMULATOR":    ITEM_IDS["IDD_ACCUMULATOR"],
    "RSC_X_ASSON":          ITEM_IDS["IDD_ASSON"],
    "RSC_X_BATON":          ITEM_IDS["IDD_BATON"],
    "RSC_X_BOOK_OF_SHADOWS":ITEM_IDS["IDD_BOOKOFSHADOWS"],
    "RSC_CADEAUX":          ITEM_IDS["IDD_CADEAUX"],
    "RSC_X_CADEAUX":        ITEM_IDS["IDD_CADEAUX"],
    "RSC_PICKUP_CADEAUX":   ITEM_IDS["IDD_CADEAUX"],
    "RSC_X_CALABASH":       ITEM_IDS["IDD_CALABASH"],
    "RSC_X_DESERTEAGLE":    ITEM_IDS["IDD_DESERTEAGLE"],
    "RSC_X_ECLIPSER":       ITEM_IDS["IDD_ECLIPSER"],
    "RSC_X_ENGINEERS_KEY":  ITEM_IDS["IDD_ENGINEERSKEY"],
    "RSC_X_ENSEIGNE":       ITEM_IDS["IDD_ENSEIGNE"],
    "RSC_X_FLAMBEAU":       ITEM_IDS["IDD_FLAMBEAU"],
    "RSC_X_FLASHLIGHT":     ITEM_IDS["IDD_FLASHLIGHT"],
    "RSC_X_LALUNE":         ITEM_IDS["IDD_LALUNE"],
    "RSC_X_LALAME":         ITEM_IDS["IDD_LALAME"],
    "RSC_X_MARTEAU":        ITEM_IDS["IDD_MARTEAU"],
    "RSC_X_POIGNE":         ITEM_IDS["IDD_POIGNE"],
    "RSC_X_PRISM":          ITEM_IDS["IDD_PRISM"],
    "RSC_X_PRISON_KEY_CARD":ITEM_IDS["IDD_PRISONCARD"],
    "RSC_X_PROPHECY":       ITEM_IDS["IDD_PROPHECY"],
    "RSC_X_RETRACT":        ITEM_IDS["IDD_RETRACTOR"],
    "RSC_X_RETRACT1":       ITEM_IDS["IDD_RETRACTOR"],
    "RSC_X_RETRACT2":       ITEM_IDS["IDD_RETRACTOR"],
    "RSC_X_PATHSOFSHADOW":  ITEM_IDS["IDD_SCHEMATIC"],
    "RSC_X_SHADOWGUN":      ITEM_IDS["IDD_SHADOWGUN"],
    "RSC_X_SHOTGUN":        ITEM_IDS["IDD_SHOTGUN"],
    "RSC_X_SHOTGUNAMMO":    ITEM_IDS["IDD_SHOTGUNAMMO"],
    "RSC_X_SOLEIL":         ITEM_IDS["IDD_SOLEIL"],
    "RSC_X_TEDDY":          ITEM_IDS["IDD_TEDDY"],
    "RSC_X_VIOLATOR":       ITEM_IDS["IDD_VIOLATOR"],
}

GAD_PICKUP_RSC  = "RSC_X_GAD_PICKUP"
GAD_BLOCKER_RSC = "RSC_X_WEAPON_ALTAR"

# (folder, filename, x, y, z, zone) — injection sites for RSC_X_GAD_PICKUP.
GAD_INJECTION_SITES: list[tuple[str, str, float, float, float, int]] = [
    ("t1tchgad", "quest.rsc", -559.4,  360.0,  35710.8, 16),
    ("t2wlkgad", "quest.rsc",  256.0,  420.0,   1280.0,  9),
    ("t3swmgad", "quest.rsc", -1535.8, 680.0,  -4988.9,  7),
]

# (folder, x, y, z, zone) — placeholder coords matching GAD pickup sites.
# Update x/y/z once real platform-blocking positions are confirmed in-game.
GAD_BLOCKER_SITES: list[tuple[str, float, float, float, int]] = [
    ("t1tchgad", -559.4,  210.0,  35710.8, 16),
    ("t2wlkgad",  256.0,  270.0,   1280.0,  9),
    ("t3swmgad", -1535.8, 530.0,  -4988.9,  7),
]

# Expected file offsets for RSC_X_GAD_PICKUP records after setup_gad_records injection.
# If inject_record returns a different offset, extracted_locations.py needs updating.
GAD_PICKUP_EXPECTED_OFFSETS = {
    "t1tchgad": 0x3E52,
    "t2wlkgad": 0x3432,
    "t3swmgad": 0x3A1A,
}

# ── Soul instance ID sets ─────────────────────────────────────────────────────

# Dark souls dropped by the five liveside bosses.  These are never in the
# randomizer pool and their vanilla $darksoul entries must be preserved as-is.
BOSS_SOUL_IDS: frozenset[int] = frozenset({
    9,   # Avery Marx      (tenement)
    10,  # Victor Batrachian (prison)
    11,  # Jack the Ripper   (uground)
    12,  # Milton Pike       (florida)
    13,  # Marco Cruz        (salvage)
})

# Dark souls dropped by Trueform enemies.  These will eventually be shuffled
# but are left vanilla until in-game functionality is confirmed.
TRUE_FORM_SOUL_IDS: frozenset[int] = frozenset({
    36,                                 # as2exper
    38, 40, 41, 42, 43, 44, 45, 46,    # as4dkeng
    47, 48, 49, 52,                     # as4dkeng (cont.)
    87,                                 # ah2playr
    120,                                # ah4fogom
})

# Combined set of soul IDs whose $darksoul lines must never be stripped or
# moved by the tracker patcher — they stay at their vanilla locations.
PRESERVED_SOUL_IDS: frozenset[int] = BOSS_SOUL_IDS
# TRUE_FORM_SOUL_IDS are now handled dynamically via true_form_loc_remap in the
# tracker patcher — they are no longer preserved at vanilla positions.


# ── Tracker hint labels ───────────────────────────────────────────────────────
# Phrases shown in the map tracker hint panel.
# GAD_LABEL: generic label used for all three Gad powers when gad temples are
#   shuffled (so the hint doesn't name a specific power that may have moved).
# HINT_TIERS: categorical labels used in obscure-hints mode.  Each tier maps to
#   a cryptic phrase so item locations are hinted by category, not by name.
# DIRECTIVE_HINT_TIER: maps levels.txt directive names to a HINT_TIERS key.

GAD_LABEL: str = "Find Gad Power (won't clear)"

# Label for the Violator that unlocks via the accumulator mechanism (VIO_PLINTH).
# That mechanism is never triggered in a randomised run, so the tracker badge
# will not clear after the player collects the item.
VIOLATOR_PLINTH_LABEL: str = "Violator (won't clear)"

HINT_TIERS: dict[str, str] = {
    "progression": "Path of the Lord of Deadside",
    "darksoul":    "The Power of the Dark Souls are here",
    "lore":        "Something here should be wasteful",
    "weapon":      "Something here should be useful",
}

# Maps levels.txt directive names to their loc_english.txt i_* inventory key.
# Used in obscure-hints mode to override the inventory name that feeds the
# m_obj_findthe "{}" tracker fallback.  Note: this also changes the name shown
# in the player's inventory screen — acceptable for a full obscure experience.
DIRECTIVE_INVENTORY_KEY: dict[str, str] = {
    # Voodoo / key items
    "asson":         "i_asson",
    "baton":         "i_baton",
    "calabash":      "i_calabash",
    "engineerskey":  "i_engineers_key",
    "enseigne":      "i_enseigne",
    "flambeau":      "i_flambeau",
    "keycard":       "i_key_card",
    "marteau":       "i_marteau",
    # L'Eclipser pieces
    "lalune":        "i_eclipser_lune",
    "lesoleil":      "i_eclipser_soleil",
    "lalame":        "i_eclipser_lame",
    # Lore
    "bookofshadows": "i_book_of_shadows",
    "prophecy":      "i_prophecy",
    "journal":       "i_jacks_journal",
    "flashlight":    "i_flashlight",
    # Weapons
    "mac10":         "i_smg",
    "mp909":         "i_mp909",
    "sawedshotgun":  "i_shotgun2",
    "shotgun":       "i_shotgun",
    "tetedemort":    "i_tetedemort",
    "violator2":     "i_violator2",
}

DIRECTIVE_HINT_TIER: dict[str, str] = {
    # Voodoo / key items
    "poigne":        "progression",
    "flambeau":      "progression",
    "marteau":       "progression",
    "calabash":      "progression",
    "baton":         "progression",
    "asson":         "progression",
    "enseigne":      "progression",
    "engineerskey":  "progression",
    "keycard":       "progression",
    # L'Eclipser pieces
    "lalune":        "eclipser",
    "lesoleil":      "eclipser",
    "lalame":        "eclipser",
    # Gad powers
    "touchgad":      "progression",
    "walkgad":       "progression",
    "swimgad":       "progression",
    # Other progression
    "darksoul":      "darksoul",
    "lightsoul":     "progression",
    "accumulator":   "progression",
    "retractor":     "progression",
    # Lore
    "bookofshadows": "lore",
    "prophecy":      "lore",
    "journal":       "lore",
    # Plural forms (shown when multiple of the same item are in a level)
    "darksouls":     "darksoul",
    "retractors":    "progression",
    "accumulators":  "progression",
    # Weapons
    "violator":      "weapon",    # vanilla m_obj_violator key
    "violator2":     "weapon",
    "tetedemort":    "weapon",
    "mac10":         "weapon",
    "mp909":         "weapon",
    "shotgun":       "weapon",
    "sawedshotgun":  "weapon",
    "flashlight":    "lore",
}

# ── Item spawn height adjustments ─────────────────────────────────────────────

TALL_TYPES: set[str] = {"RSC_X_GOVI", "RSC_X_DARK_SOUL"}  # same as DARK_SOUL_TYPES

GOVI_HEIGHT_BOOST        =  120.0  # lift tall soul objects so they don't clip into the floor
CADEAU_HEIGHT_DROP       = -120.0  # drop short objects so they don't float replacing a tall one

PROGRESSION_IN_GOVI_LIFT    =  30.0  # raise key items placed in RSC_X_GOVI slots
DARK_SOUL_SLOT_ITEM_DROP    = -120.0  # lower key items placed in RSC_X_DARK_SOUL slots (dark souls float)
PROGRESSION_IN_CADEAUX_LIFT =  60.0  # raise key items placed in cadeaux slots
PROGRESSION_IN_BARREL_LIFT  =  60.0  # raise key items placed in barrel/crate slots

# RSC name spawned next to key items placed into soul/cadeaux slots (insanity mode).
# No level prefix → globally registered asset, renders across all level types.
SOUL_SLOT_MARKER_FX        = "RSC_X_WEAPON_ALTAR"
SOUL_SLOT_MARKER_FX_Y      = -120.0   # Y offset for RSC_X_GOVI and cadeaux slots
DARK_SOUL_SLOT_MARKER_FX_Y = -240.0   # Y offset for RSC_X_DARK_SOUL slots (120 lower — brings altar to ground)
BARREL_SLOT_MARKER_FX   = "RSC_UN_CRATES"
BARREL_SLOT_MARKER_FX_Y = 0.0

# RSC names to substitute during filler patching — used when an asset has been
# replaced with a custom visual (e.g. marker crate) and the vanilla name should
# not appear at filler locations as a result.
BARREL_RSC_SUBSTITUTIONS: dict[str, str] = {
    "RSC_UN_CRATES": "RSC_X_BARREL_D",   # altered asset → plain barrel
}

# Day/night mirror pairs — same physical space, different level folders.
# When a marker is injected for one side, it must also be injected into the other
# so the visual indicator appears regardless of which time-of-day the player enters.
DAY_NIGHT_MIRRORS: dict[str, str] = {
    "swampday": "swampnit",
    "swampnit": "swampday",
    "tenement": "ntenemnt",
    "ntenemnt": "tenement",
    "prison":   "nprison",
    "nprison":  "prison",
    "uground":  "nuground",
    "nuground": "uground",
    "florida":  "nflorida",
    "nflorida": "florida",
    "salvage":  "nsalvage",
    "nsalvage": "salvage",
}

# Per-item Y spawn adjustments for items that clip or float at the slot's native height.
# Applied on top of GOVI_HEIGHT_BOOST / CADEAU_HEIGHT_DROP when those also fire.
# Per-item Y adjustments — keyed by (new_name, old_name) or (new_name, None) for any slot
# None as old_name means "applies regardless of what was there before"
ITEM_Y_ADJUST: dict[tuple[str, str | None], float] = {
    ("RSC_X_CALABASH",   None): 0.0,
}

# Enemy difficulty tiers: 1 (easiest) → 5 (hardest)
# Used by difficulty-weighted depth placement mode.
ENEMY_DIFFICULTY: dict[str, int] = {
    # Tier 1 — basic, early game fodder
    "RSC_BAD_DEADWORM":           1,
    "RSC_D_DEADSIDER_F":          1,
    "RSC_D_DEADSIDER_M":          1,
    "RSC_D_DEADFISH":             1,
    "RSC_D_ZOMBI":                1,
    "RSC_TENEMENT_ZOMBIE":        1,
    "RSC_BAD_ZOMBIEU":            1,
    "RSC_D_BICEPHALOD":           1,
    "RSC_BAD_ZALIEN":             1,
    "RSC_DOG":                    1,
    "RSC_BAD_HD_KIL_L_CLUB":      1,
    "RSC_BAD_HD_GRD_L_CLUB":      1,
    "RSC_BAD_HD_GRD_P_CLUB":      1,
    "RSC_BAD_HD_IN_CLUB":         1,
    "RSC_BAD_HD_IN_L_CLUB":       1,
    "RSC_BAD_HD_IN_P_CLUB":       1,
    "RSC_BAD_GRD_STAND_CLUB":     1,
    "RSC_BAD_KIL_STAND_CLUB":     1,

    # Tier 2 — common early/mid enemies
    "RSC_A_GUARD":                2,
    "RSC_DOG_DUPPIE":             2,
    "RSC_DOG_TENEMENT":           2,
    "RSC_GATOR":                  2,
    "RSC_GATOR_WATER":            2,
    "RSC_D_DEADWING":             2,
    "RSC_A_HOOKMAN":              2,
    "RSC_A_DOGMAN":               2,
    "RSC_BAD_HD_IN_GUN":          2,
    "RSC_BAD_FLOATER":            2,
    "RSC_BAD_GRD_STAND_GUN":      2,
    "RSC_BAD_IN_STAND_GUN":       2,
    "RSC_BAD_HD_GRD_L_GUN":       2,
    "RSC_BAD_HD_GRD_P_GUN":       2,
    "RSC_BAD_HD_IN_L_GUN":        2,
    "RSC_BAD_HD_IN_P_GUN":        2,
    "RSC_BAD_HD_KIL_P_GUN":       2,
    "RSC_BAD_HD_KIL_L_GUN":       2,
    "RSC_BAD_KIL_STAND_GUN":      2,

    # Tier 3 — mid-game, more dangerous
    "RSC_A_O_GRINDER":            3,
    "RSC_A_SURGEON":              3,
    "RSC_A_O_GRINDER_SHIELD":     3,
    "RSC_BAD_SENTINEL":           3,
    "RSC_T_ACOLYTE":              3,

    # Tier 4 — elite, late game threats
    "RSC_BAD_PAINKILLER":         4,
    "RSC_BAD_SERAPH":             4,
    "RSC_T_ADEPT":                4,

    # Tier 5 — hardest regulars
    "RSC_T_MATRIARCH":            5,
    "RSC_CHOPPER":                5,

    # Special — true form placeholder, never placed by difficulty mode
    "RSC_X_TRUE_FORM":            0,
}

ENEMY_DIFFICULTY_DEFAULT = 2

# ── Weapon sound pools ─────────────────────────────────────────────────────────
#
# Each key is a sound category. Files within a pool shuffle among themselves.
# Files not listed here are left at vanilla.
# Populate after investigating weapon folder contents.

# Each inner list is one weapon's complete set of sounds for that category.
# Sets shuffle as units — calabash's 1 sound tiles across baton's 4 slots.

WEAPON_FIRE_SETS: list[list[str]] = [
    # deagle (primary)
    ["audio/sfx/weapons/deagle/defire000.wav",
     "audio/sfx/weapons/deagle/defire001.wav",
     "audio/sfx/weapons/deagle/defire002.wav",
     "audio/sfx/weapons/deagle/defire003.wav"],
    # deagle (variant)
    ["audio/sfx/weapons/deagle/de2fire000.wav",
     "audio/sfx/weapons/deagle/de2fire001.wav",
     "audio/sfx/weapons/deagle/de2fire002.wav",
     "audio/sfx/weapons/deagle/de2fire003.wav"],
    # asson
    ["audio/sfx/weapons/asson/shot000.wav",
     "audio/sfx/weapons/asson/shot001.wav",
     "audio/sfx/weapons/asson/shot002.wav",
     "audio/sfx/weapons/asson/shot003.wav"],
    # baton
    ["audio/sfx/weapons/baton/stab000.wav",
     "audio/sfx/weapons/baton/stab001.wav",
     "audio/sfx/weapons/baton/stab002.wav",
     "audio/sfx/weapons/baton/stab003.wav"],
    # calabash (1 sound — tiles when swapped into multi-sound slots)
    ["audio/sfx/weapons/calabash/set.wav"],
    # flambeau
    ["audio/sfx/weapons/flambeau/launch000.wav",
     "audio/sfx/weapons/flambeau/launch001.wav"],
    # mac10
    ["audio/sfx/weapons/mac10/shot000.wav",
     "audio/sfx/weapons/mac10/shot001.wav",
     "audio/sfx/weapons/mac10/shot002.wav"],
    # marteau (1 sound)
    ["audio/sfx/weapons/marteau/swing.wav"],
    # mp5
    ["audio/sfx/weapons/mp5/shot000.wav",
     "audio/sfx/weapons/mp5/shot001.wav",
     "audio/sfx/weapons/mp5/shot002.wav"],
    # shadgun
    ["audio/sfx/weapons/shadgun/sgfire.wav",
     "audio/sfx/weapons/shadgun/sgfire2.wav"],
    # shotgun
    ["audio/sfx/weapons/shotgun/sgshot000.wav",
     "audio/sfx/weapons/shotgun/sgshot001.wav",
     "audio/sfx/weapons/shotgun/sgshot002.wav"],
    # sshotgun
    ["audio/sfx/weapons/sshotgun/sshotgun000.wav",
     "audio/sfx/weapons/sshotgun/sshotgun001.wav",
     "audio/sfx/weapons/sshotgun/sshotgun002.wav"],
    # tetedemort
    ["audio/sfx/weapons/tetedemort/fire000.wav",
     "audio/sfx/weapons/tetedemort/fire001.wav",
     "audio/sfx/weapons/tetedemort/fire002.wav"],
    # violator
    ["audio/sfx/weapons/violator/fire000.wav",
     "audio/sfx/weapons/violator/fire001.wav",
     "audio/sfx/weapons/violator/fire002.wav"],
]

WEAPON_EMPTY_SETS: list[list[str]] = [
    ["audio/sfx/weapons/asson/shakout.wav"],
    ["audio/sfx/weapons/calabash/noammo.wav"],
    ["audio/sfx/weapons/flambeau/noammo.wav"],
    ["audio/sfx/weapons/mac10/empty.wav"],
    ["audio/sfx/weapons/marteau/noammo.wav"],
    ["audio/sfx/weapons/mp5/empty.wav"],
    ["audio/sfx/weapons/shotgun/sgempty.wav"],
    ["audio/sfx/weapons/sshotgun/sshotgun_empty.wav"],
]

WEAPON_WET_SETS: list[list[str]] = [
    ["audio/sfx/weapons/deagle/dewet.wav"],
    ["audio/sfx/weapons/deagle/de2wet.wav"],
    ["audio/sfx/weapons/mac10/uwshot.wav"],
    ["audio/sfx/weapons/mp5/uwshot.wav"],
    ["audio/sfx/weapons/shotgun/sgwet.wav"],
    ["audio/sfx/weapons/sshotgun/sshotgun_wet.wav"],
    ["audio/sfx/weapons/violator/wetfire000.wav",
     "audio/sfx/weapons/violator/wetfire001.wav",
     "audio/sfx/weapons/violator/wetfire002.wav"],
]

# Reload sounds — weapons that have a distinct reload animation sound.
# baton/blast.wav and marteau/launch.wav look fire-adjacent but appear to be
# alternate/charged-mode sounds; left out pending confirmation.
WEAPON_RELOAD_SETS: list[list[str]] = [
    ["audio/sfx/weapons/mac10/reload.wav"],
    ["audio/sfx/weapons/mp5/reload.wav"],
    ["audio/sfx/weapons/shotgun/sgreload.wav"],
    ["audio/sfx/weapons/sshotgun/sshotgun_reload.wav"],
]

# Bullet surface-impact sounds — one set per material type.
# Sets shuffle as groups so, e.g., concrete sounds can end up where
# flesh sounds were. File counts vary by surface; tiling applies as usual.
_WBI = "audio/sfx/weapons/bullet"
WEAPON_BULLET_IMPACT_SETS: list[list[str]] = [
    # Bone (5)
    [f"{_WBI}/Bone_{i:03d}.wav" for i in range(5)],
    # Concrete (6)
    [f"{_WBI}/Concrete_{i:03d}.wav" for i in range(6)],
    # dirt (6)
    [f"{_WBI}/dirt_{i:03d}.wav" for i in range(6)],
    # flesh (8)
    [f"{_WBI}/flesh_{i:03d}.wav" for i in range(8)],
    # foliage (7)
    [f"{_WBI}/foliage_{i:03d}.wav" for i in range(7)],
    # glass (5)
    [f"{_WBI}/glass_{i:03d}.wav" for i in range(5)],
    # grass (5)
    [f"{_WBI}/grass_{i:03d}.wav" for i in range(5)],
    # mesh (3)
    [f"{_WBI}/mesh_{i:03d}.wav" for i in range(3)],
    # metal (7)
    [f"{_WBI}/metal_{i:03d}.wav" for i in range(7)],
    # mud (4)
    [f"{_WBI}/mud_{i:03d}.wav" for i in range(4)],
    # water (6)
    [f"{_WBI}/water_{i:03d}.wav" for i in range(6)],
    # wood (10)
    [f"{_WBI}/wood_{i:03d}.wav" for i in range(10)],
]

# Ricochet sounds — two stylistically distinct sets that swap with each other.
_WRI = "audio/sfx/weapons/ricochet"
WEAPON_RICOCHET_SETS: list[list[str]] = [
    # classic ricochets (6)
    [f"{_WRI}/rico{i}.wav" for i in range(1, 7)],
    # bullet-impact-metal variants (3) — tiles to fill rico slot count
    [f"{_WRI}/bulimpm2.wav", f"{_WRI}/bulimpm3.wav", f"{_WRI}/bulimpmt.wav"],
]

# Projectile-impact sounds — what plays when a non-hitscan projectile hits
# something. flambeau (fire bolt), marteau (magical bolt), tetedemort (skull).
WEAPON_PROJ_IMPACT_SETS: list[list[str]] = [
    ["audio/sfx/weapons/flambeau/hit000.wav",
     "audio/sfx/weapons/flambeau/hit001.wav",
     "audio/sfx/weapons/flambeau/hit002.wav",
     "audio/sfx/weapons/flambeau/hit003.wav"],
    ["audio/sfx/weapons/marteau/hit000.wav",
     "audio/sfx/weapons/marteau/hit001.wav",
     "audio/sfx/weapons/marteau/hit002.wav"],
    ["audio/sfx/weapons/tetedemort/impact000.wav",
     "audio/sfx/weapons/tetedemort/impact001.wav",
     "audio/sfx/weapons/tetedemort/impact002.wav"],
]

# All pools — iterated by shuffle_sfx
WEAPON_SOUND_SETS: dict[str, list[list[str]]] = {
    "fire":          WEAPON_FIRE_SETS,
    "empty":         WEAPON_EMPTY_SETS,
    "wet":           WEAPON_WET_SETS,
    "reload":        WEAPON_RELOAD_SETS,
    "bullet_impact": WEAPON_BULLET_IMPACT_SETS,
    "ricochet":      WEAPON_RICOCHET_SETS,
    "proj_impact":   WEAPON_PROJ_IMPACT_SETS,
}


# ── Enemy SFX pools ──────────────────────────────────────────────────────────
#
# Each inner list = one enemy's sounds for that sound type, shuffled as a unit.
# Sets tile to fill larger slots when sizes differ (same as weapon pools).
# Excluded: death-by-weapon sounds (dethsgun/dethvdoo/dethbull — weapon
#   feedback), ambient creatures (canary, dfly, egret, flies, rat), mechanical
#   enemies (grinder, helicop, mgsentry), speech/voice lines, movement sounds.

_B = "audio/sfx/baddys"

ENEMY_PAIN_SETS: list[list[str]] = [
    # amarx (5)
    [f"{_B}/amarx/pain1.wav", f"{_B}/amarx/pain2.wav", f"{_B}/amarx/pain3.wav",
     f"{_B}/amarx/pain4.wav", f"{_B}/amarx/pain5.wav"],
    # batty (4)
    [f"{_B}/batty/pain1.wav", f"{_B}/batty/pain2.wav",
     f"{_B}/batty/pain3.wav", f"{_B}/batty/pain4.wav"],
    # biceph (3) — bipain prefix
    [f"{_B}/biceph/bipain1.wav", f"{_B}/biceph/bipain2.wav", f"{_B}/biceph/bipain3.wav"],
    # deadfem (3)
    [f"{_B}/deadfem/pain1.wav", f"{_B}/deadfem/pain2.wav", f"{_B}/deadfem/pain3.wav"],
    # deadmale (3) — dspain prefix
    [f"{_B}/deadmale/dspain1.wav", f"{_B}/deadmale/dspain2.wav", f"{_B}/deadmale/dspain3.wav"],
    # deadwing (4)
    [f"{_B}/deadwing/pain1.wav", f"{_B}/deadwing/pain2.wav",
     f"{_B}/deadwing/pain3.wav", f"{_B}/deadwing/pain4.wav"],
    # duppie (4)
    [f"{_B}/duppie/pain1.wav", f"{_B}/duppie/pain2.wav",
     f"{_B}/duppie/pain3.wav", f"{_B}/duppie/pain4.wav"],
    # gimpdog (4)
    [f"{_B}/gimpdog/pain1.wav", f"{_B}/gimpdog/pain2.wav",
     f"{_B}/gimpdog/pain3.wav", f"{_B}/gimpdog/pain4.wav"],
    # jack (4)
    [f"{_B}/jack/pain1.wav", f"{_B}/jack/pain2.wav",
     f"{_B}/jack/pain3.wav", f"{_B}/jack/pain4.wav"],
    # legion (3)
    [f"{_B}/legion/pain1.wav", f"{_B}/legion/pain2.wav", f"{_B}/legion/pain3.wav"],
    # marco (5)
    [f"{_B}/marco/pain1.wav", f"{_B}/marco/pain2.wav", f"{_B}/marco/pain3.wav",
     f"{_B}/marco/pain4.wav", f"{_B}/marco/pain5.wav"],
    # matriach (3)
    [f"{_B}/matriach/pain1.wav", f"{_B}/matriach/pain2.wav", f"{_B}/matriach/pain3.wav"],
    # seraph (12)
    [f"{_B}/seraph/pain000.wav", f"{_B}/seraph/pain001.wav", f"{_B}/seraph/pain002.wav",
     f"{_B}/seraph/pain003.wav", f"{_B}/seraph/pain004.wav", f"{_B}/seraph/pain005.wav",
     f"{_B}/seraph/pain006.wav", f"{_B}/seraph/pain007.wav", f"{_B}/seraph/pain008.wav",
     f"{_B}/seraph/pain009.wav", f"{_B}/seraph/pain010.wav", f"{_B}/seraph/pain011.wav"],
    # surgeon (6)
    [f"{_B}/surgeon/pain1.wav", f"{_B}/surgeon/pain2.wav", f"{_B}/surgeon/pain3.wav",
     f"{_B}/surgeon/pain4.wav", f"{_B}/surgeon/pain5.wav", f"{_B}/surgeon/pain6.wav"],
    # tflegion (2)
    [f"{_B}/tflegion/pain1.wav", f"{_B}/tflegion/pain2.wav"],
    # trueform (4)
    [f"{_B}/trueform/pain1.wav", f"{_B}/trueform/pain2.wav",
     f"{_B}/trueform/pain3.wav", f"{_B}/trueform/pain4.wav"],
    # yort (8)
    [f"{_B}/yort/pain000.wav", f"{_B}/yort/pain001.wav", f"{_B}/yort/pain002.wav",
     f"{_B}/yort/pain003.wav", f"{_B}/yort/pain004.wav", f"{_B}/yort/pain005.wav",
     f"{_B}/yort/pain006.wav", f"{_B}/yort/pain007.wav"],
]

# Startle / alert sounds — enemy reacts to spotting the player.
# Most are a single file; inmate has two. Sets tile as usual.
ENEMY_STARTLE_SETS: list[list[str]] = [
    [f"{_B}/batty/stikhit000.wav", f"{_B}/batty/stikhit001.wav",
     f"{_B}/batty/stikhit002.wav", f"{_B}/batty/stikhit003.wav",
     f"{_B}/batty/stikhit004.wav", f"{_B}/batty/stikhit005.wav"],
    [f"{_B}/biceph/startle.wav"],
    [f"{_B}/brutal/startle.wav"],
    [f"{_B}/deadwing/startle.wav"],
    [f"{_B}/deadworm/startle.wav"],
    [f"{_B}/dog/startle.wav"],
    [f"{_B}/dupdog/startle.wav"],
    [f"{_B}/duppie/startle.wav"],
    [f"{_B}/gimpdog/startle.wav"],
    [f"{_B}/inmate/startle.wav", f"{_B}/inmate/startle2.wav"],
    [f"{_B}/seraph/startle000.wav"],
    [f"{_B}/surgeon/startle.wav"],
    [f"{_B}/tenementzombie/startle.wav"],
]

# Attack sounds — enemies with generic attack1/attack2/… naming.
# Unique-mechanic attacks (hook swing, leg swipe, crocbite, etc.) are left
# in place since they're tied to specific enemy movement systems.
ENEMY_ATTACK_SETS: list[list[str]] = [
    # batty (3)
    [f"{_B}/batty/attack1.wav", f"{_B}/batty/attack2.wav", f"{_B}/batty/attack3.wav"],
    # deadfem (2)
    [f"{_B}/deadfem/attack1.wav", f"{_B}/deadfem/attack2.wav"],
    # duppie (4)
    [f"{_B}/duppie/attack1.wav", f"{_B}/duppie/attack2.wav",
     f"{_B}/duppie/attack3.wav", f"{_B}/duppie/attack4.wav"],
    # hookman (3)
    [f"{_B}/hookman/attack1.wav", f"{_B}/hookman/attack2.wav", f"{_B}/hookman/attack3.wav"],
    # seraph (5)
    [f"{_B}/seraph/attack000.wav", f"{_B}/seraph/attack001.wav", f"{_B}/seraph/attack002.wav",
     f"{_B}/seraph/attack003.wav", f"{_B}/seraph/attack004.wav"],
    # trueform (2)
    [f"{_B}/trueform/attack1.wav", f"{_B}/trueform/attack2.wav"],
    # yort (1)
    [f"{_B}/yort/attack000.wav"],
]

# Ambient / stationary sounds — growls, groans, idle loops, breath.
# All merged into one pool so any stationary sound can end up on any enemy.
ENEMY_AMBIENT_SETS: list[list[str]] = [
    # dog — growl + grrs
    [f"{_B}/dog/growl.wav", f"{_B}/dog/grr1.wav", f"{_B}/dog/grr2.wav"],
    # dupdog — same sound palette as dog
    [f"{_B}/dupdog/growl.wav", f"{_B}/dupdog/grr1.wav", f"{_B}/dupdog/grr2.wav"],
    # gimpdog
    [f"{_B}/gimpdog/growl.wav", f"{_B}/gimpdog/gruff.wav"],
    # trueform — growls
    [f"{_B}/trueform/growl.wav", f"{_B}/trueform/growl2.wav"],
    # deadmale — idle groans ("grown" spelling in filenames)
    [f"{_B}/deadmale/dsgrown.wav", f"{_B}/deadmale/dsgrown2.wav"],
    # tenementzombie — groans
    [f"{_B}/tenementzombie/groan000.wav", f"{_B}/tenementzombie/groan001.wav",
     f"{_B}/tenementzombie/groan002.wav", f"{_B}/tenementzombie/groan003.wav"],
    # tflegion — groans
    [f"{_B}/tflegion/groan.wav", f"{_B}/tflegion/groan2.wav"],
    # trueform — agroans (separate set from growls above)
    [f"{_B}/trueform/agroan.wav", f"{_B}/trueform/agroan2.wav"],
    # deadworm — idle
    [f"{_B}/deadworm/idle.wav"],
    # tenementzombie — idle
    [f"{_B}/tenementzombie/idle.wav"],
    # gator — breath
    [f"{_B}/gator/breath.wav"],
    # legion — breath
    [f"{_B}/legion/breath.wav"],
]

# Speech lines — all bosses and named enemies included together.
# Scripted numbered lines (Jack/Bat/Marco/marx) are boss encounter VO,
# seraph's named lines are boss taunts — all in the same pot.
ENEMY_SPEECH_SETS: list[list[str]] = [
    # amarx — numbered lines (013–021)
    [f"{_B}/amarx/marx013.wav", f"{_B}/amarx/marx014.wav", f"{_B}/amarx/marx015.wav",
     f"{_B}/amarx/marx016.wav", f"{_B}/amarx/marx017.wav", f"{_B}/amarx/marx018.wav",
     f"{_B}/amarx/marx019.wav", f"{_B}/amarx/marx020.wav", f"{_B}/amarx/marx021.wav"],
    # batty — numbered lines (037–041)
    [f"{_B}/batty/Bat037.wav", f"{_B}/batty/Bat038.wav", f"{_B}/batty/Bat039.wav",
     f"{_B}/batty/Bat040.wav", f"{_B}/batty/Bat041.wav"],
    # duppie (6 non-sequential)
    [f"{_B}/duppie/speech2.wav", f"{_B}/duppie/speech3.wav", f"{_B}/duppie/speech4.wav",
     f"{_B}/duppie/speech6.wav", f"{_B}/duppie/speech7.wav", f"{_B}/duppie/speech9.wav"],
    # jack — numbered lines (014–026)
    [f"{_B}/jack/Jack014.wav", f"{_B}/jack/Jack015.wav", f"{_B}/jack/Jack016.wav",
     f"{_B}/jack/Jack017.wav", f"{_B}/jack/Jack018.wav", f"{_B}/jack/Jack019.wav",
     f"{_B}/jack/Jack020.wav", f"{_B}/jack/jack021.wav", f"{_B}/jack/Jack022.wav",
     f"{_B}/jack/Jack023.wav", f"{_B}/jack/Jack024.wav", f"{_B}/jack/Jack025.wav",
     f"{_B}/jack/Jack026.wav"],
    # marco — numbered lines (013–020)
    [f"{_B}/marco/Marco013.wav", f"{_B}/marco/Marco014.wav", f"{_B}/marco/Marco015.wav",
     f"{_B}/marco/Marco016.wav", f"{_B}/marco/Marco017.wav", f"{_B}/marco/Marco018.wav",
     f"{_B}/marco/Marco019.wav", f"{_B}/marco/Marco020.wav"],
    # matriach (3)
    [f"{_B}/matriach/speech.wav", f"{_B}/matriach/speech2.wav", f"{_B}/matriach/speech3.wav"],
    # milton (7)
    [f"{_B}/milton/speech1.wav", f"{_B}/milton/speech2.wav", f"{_B}/milton/speech3.wav",
     f"{_B}/milton/speech4.wav", f"{_B}/milton/speech5.wav", f"{_B}/milton/speech6.wav",
     f"{_B}/milton/speech7.wav"],
    # seraph — named taunts grouped as one set
    [f"{_B}/seraph/lord_of_fools000.wav",   f"{_B}/seraph/lord_of_fools001.wav",
     f"{_B}/seraph/lord_of_fools002.wav",   f"{_B}/seraph/lord_of_fools003.wav",
     f"{_B}/seraph/lord_of_fools004.wav",
     f"{_B}/seraph/lord_of_nothing000.wav", f"{_B}/seraph/lord_of_nothing001.wav",
     f"{_B}/seraph/lord_of_nothing002.wav", f"{_B}/seraph/lord_of_nothing003.wav",
     f"{_B}/seraph/lord_of_nothing004.wav",
     f"{_B}/seraph/seeker_of_death000.wav", f"{_B}/seraph/seeker_of_death001.wav",
     f"{_B}/seraph/seeker_of_death002.wav", f"{_B}/seraph/seeker_of_death003.wav",
     f"{_B}/seraph/seeker_of_death004.wav",
     f"{_B}/seraph/shadow_born000.wav",     f"{_B}/seraph/shadow_born001.wav",
     f"{_B}/seraph/shadow_born002.wav",     f"{_B}/seraph/shadow_born003.wav",
     f"{_B}/seraph/shadowman000.wav",       f"{_B}/seraph/shadowman001.wav",
     f"{_B}/seraph/shadowman002.wav",       f"{_B}/seraph/shadowman003.wav",
     f"{_B}/seraph/shadowman004.wav",       f"{_B}/seraph/shadowman005.wav",
     f"{_B}/seraph/shadowman006.wav",
     f"{_B}/seraph/you_will_die000.wav",    f"{_B}/seraph/you_will_die001.wav",
     f"{_B}/seraph/you_will_die002.wav",    f"{_B}/seraph/you_will_die003.wav",
     f"{_B}/seraph/you_will_die004.wav",
     f"{_B}/seraph/embrace_death000.wav",   f"{_B}/seraph/embrace_death001.wav",
     f"{_B}/seraph/embrace_death002.wav",   f"{_B}/seraph/embrace_death003.wav",
     f"{_B}/seraph/empty_vessel000.wav",    f"{_B}/seraph/empty_vessel001.wav",
     f"{_B}/seraph/empty_vessel002.wav",    f"{_B}/seraph/empty_vessel003.wav",
     f"{_B}/seraph/empty_vessel004.wav"],
    # surgeon (9)
    [f"{_B}/surgeon/speech0.wav", f"{_B}/surgeon/speech1.wav", f"{_B}/surgeon/speech2.wav",
     f"{_B}/surgeon/speech3.wav", f"{_B}/surgeon/speech4.wav", f"{_B}/surgeon/speech5.wav",
     f"{_B}/surgeon/speech6.wav", f"{_B}/surgeon/speech7.wav", f"{_B}/surgeon/speech8.wav"],
    # yort (1)
    [f"{_B}/yort/speech000.wav"],
]

# All enemy pools — iterated by shuffle_sfx
ENEMY_SOUND_SETS: dict[str, list[list[str]]] = {
    "pain":    ENEMY_PAIN_SETS,
    "startle": ENEMY_STARTLE_SETS,
    "attack":  ENEMY_ATTACK_SETS,
    "ambient": ENEMY_AMBIENT_SETS,
    "speech":  ENEMY_SPEECH_SETS,
}

del _B, _WBI, _WRI  # cleanup module namespace

# ── Level folder → human-readable name ───────────────────────────────────────

LEVEL_NAMES: dict[str, str] = {
    # Liveside (day)
    "swampday":  "Louisiana Swampland",
    "tenement":  "New York Tenement",
    "prison":    "Texas Prison",
    "uground":   "London Underground",
    "florida":   "Florida Summer Camp",
    "salvage":   "Mojave Desert Salvage Yard",
    # Liveside (night)
    "swampnit":  "Louisiana Swampland (Night)",
    "ntenemnt":  "New York Tenement (Night)",
    "nprison":   "Texas Prison (Night)",
    "nuground":  "London Underground (Night)",
    "nflorida":  "Florida Summer Camp (Night)",
    "nsalvage":  "Mojave Desert Salvage Yard (Night)",
    # Deadside hub & spokes
    "deadside":  "Deadside Marrow Gates",
    "wastland":  "Deadside Wasteland",
    "asylum":    "Asylum Gateway",
    # Asylum interior
    "as2exper":  "Experimentation Rooms",
    "as3schis":  "Schism Chambers",
    "as4dkeng":  "Dark Engine",
    "asyiggy":   "Asylum (Iggy)",
    # Asylum hubs
    "ah1cagew":  "Cageways",
    "ah2playr":  "Playrooms",
    "ah3lavad":  "Lavaducts",
    "ah4fogom":  "Fogometers",
    # Gad Temples
    "t1tchgad":  "Touch Gad Temple",
    "t2wlkgad":  "Walk Gad Temple",
    "t3swmgad":  "Swim Gad Temple",
    "t4ndgad":   "Unknown Area",
}
