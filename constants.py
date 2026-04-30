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

ENEMY_RSC_FILES = {"enemies.rsc", "enemys.rsc","objects.rsc", "resource.rsc","events.rsc"}

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

# ── Gate constants ────────────────────────────────────────────────────────────

GATE_VANILLA_SL: dict[str, int] = {
    # ── Deadside gates ────────────────────────────────────────────────────────
    "GATE_DEADSIDE_MARROW"      :  0,  # ARC0  — Path of Shadows entry          LOCKED
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
    "GATE_DEADSIDE_MYSTERY"     : 10,  # ARC10 — final gate                     LOCKED
    # ── Non-deadside gates ────────────────────────────────────────────────────
    "GATE_WASTELAND_ENSEIGNE"   :  6,  # wastland  — interior enseigne gate
    "GATE_FIRE_POIGNE"          :  4,  # t1tchgad  — Temple of Fire lower gate
    "GATE_FIRE_FLAMBEAU"        :  5,  # t1tchgad  — Temple of Fire upper gate
    "GATE_PROPHECY_INTERIOR"    :  7,  # t2wlkgad  — Temple of Prophecy interior
    "GATE_BLOOD_INTERIOR"       :  9,  # t3swmgad  — Temple of Blood interior
    "GATE_FOGOMETERS_INTERIOR"  : 10,  # ah4fogom  — Fogometers interior         LOCKED
}

# Shuffle Gate Difficulties
_HARD_LOCKED: frozenset[str] = frozenset({
    "GATE_DEADSIDE_MARROW",
    "GATE_DEADSIDE_WASTELAND",
    "GATE_DEADSIDE_ASYLUM",
    # "GATE_DEADSIDE_MYSTERY",
    # "GATE_FOGOMETERS_INTERIOR",
})

_EASY_LOCKED: frozenset[str] = _HARD_LOCKED | frozenset({
    "GATE_DEADSIDE_PATH_3",
    "GATE_DEADSIDE_CAGEWAYS",
    "GATE_DEADSIDE_PLAYROOMS",
    "GATE_DEADSIDE_PATH_6",
})

GATE_PRESETS: dict[str, dict] = {
    "story": {
        "shuffle_gates": False,
        "no_soul_gates": True,
        "lock_gates":    frozenset(),
        "max_sl":        None,
        "safe":          True,
    },
    "easy": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "lock_gates":    _EASY_LOCKED,
        "max_sl":        8,
        "safe":          True,
    },
    "hard": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "lock_gates":    _HARD_LOCKED,
        "max_sl":        None,
        "safe":          True,
    },
    "chaos": {
        "shuffle_gates": True,
        "no_soul_gates": False,
        "lock_gates":    frozenset(),
        "max_sl":        None,
        "safe":          False,
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

GAD_PICKUP_RSC = "RSC_X_GAD_PICKUP"

# ── Item spawn height adjustments ─────────────────────────────────────────────

TALL_TYPES: set[str] = {"RSC_X_GOVI", "RSC_X_DARK_SOUL"}  # same as DARK_SOUL_TYPES

GOVI_HEIGHT_BOOST        =  120.0  # lift tall soul objects so they don't clip into the floor
CADEAU_HEIGHT_DROP       = -120.0  # drop short objects so they don't float replacing a tall one
PROGRESSION_IN_SOUL_LIFT =  0.0  # raise key/weapon/lore items placed in soul or cadeaux slots

# RSC name spawned next to key items placed into soul/cadeaux slots (insanity mode).
# Swap this out to try different visual effects.
SOUL_SLOT_MARKER_FX        = "RSC_X_HALO" # RSC_X_HALO confirmed working
SOUL_SLOT_MARKER_FX_Y      = -100.0   # Y offset the slot's native position

# Per-item Y spawn adjustments for items that clip or float at the slot's native height.
# Applied on top of GOVI_HEIGHT_BOOST / CADEAU_HEIGHT_DROP when those also fire.
# Per-item Y adjustments — keyed by (new_name, old_name) or (new_name, None) for any slot
# None as old_name means "applies regardless of what was there before"
ITEM_Y_ADJUST: dict[tuple[str, str | None], float] = {
    ("RSC_X_CALABASH",   None): 120.0,
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

# All pools — iterated by shuffle_sfx
WEAPON_SOUND_SETS: dict[str, list[list[str]]] = {
    "fire":  WEAPON_FIRE_SETS,
    "empty": WEAPON_EMPTY_SETS,
    "wet":   WEAPON_WET_SETS,
}