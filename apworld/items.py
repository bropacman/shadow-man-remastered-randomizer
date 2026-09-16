"""
items.py
────────
Item definitions and ID table for Shadow Man Remastered.

ID range: 1_000_000_000 – 1_000_000_999

Stackable items (placed multiple times, counted by AP):
    Dark Soul      — 120 total, gates coffin gates via soul level
    Retractor      — 5 total, gates liveside level entry
    Accumulator    — 3 total, gates the Violator pickup location (as2exper)
    Gad Power      — 3 total, gates temple completion + engine blocks

Unique items (placed exactly once, AP tracks by name):
    All progression key items, weapons, lore, filler
"""

from __future__ import annotations
from dataclasses import dataclass
from BaseClasses import ItemClassification, Item


BASE_ID = 1_000_000_000


@dataclass(frozen=True)
class ItemData:
    code:           int
    classification: ItemClassification


class ShadowManItem(Item):
    game = "Shadow Man Remastered"


# AP display name -> canonical RSC object name. Built alongside
# _ITEM_DEFINITIONS below (2026-07-22) so Location/Item names shown in
# spoiler logs, hints, and console output are readable (see
# locations.py's FRIENDLY_NAMES for the location-side equivalent and its
# full rationale). Every "unique" item used to have name == RSC name
# directly; now the friendly name is what AP calls the item, and this
# dict is how patcher/client code recovers the real RSC identity.
#
# NOTE: this is also reused as-is for AP_ITEM_TO_RSC below (the patcher's
# write-target mapping) for every item EXCEPT Violator, where the two
# diverge on purpose: the item's own canonical identity is RSC_X_VIOLATOR
# (what client.py's ITEM_FLAG_RVAS/AP_ITEM_INJECTION key on for
# flag-matching), but the patcher must WRITE RSC_Q_VIOLATOR instead
# (RSC_X_VIOLATOR has no working pickup handler in the vanilla engine —
# see the AP_ITEM_TO_RSC comment below for the full story). Keep that
# override in AP_ITEM_TO_RSC only; this dict stays canonical.
_UNIQUE_ITEM_RSC_NAMES: dict[str, str] = {
    # BUG FIX (2026-07-28, Jon's report -- Louisiana Swampland Govi Dark
    # Soul 5 spoilered as "Le Soleil" but showed/granted "La Lame"
    # in-game): PART2/PART3 were swapped here relative to the canonical
    # mapping in data/locations.csv (which Jon independently verified
    # in-game at each part's native vanilla location) and
    # patchers/levels_txt_patcher.py's ECLIPSER_LEVELS_TXT_NAMES (which
    # already agreed with the CSV): PART1=La Lune, PART2=Le Soleil,
    # PART3=La Lame. AP_ITEM_TO_RSC.get("Le Soleil") was returning
    # RSC_X_ECLIPSER_PART3 (La Lame's real RSC name) instead of
    # RSC_X_ECLIPSER_PART2, so every self-found "Le Soleil" placement
    # physically wrote the La Lame object/pickup instead. client.py's own
    # ITEM_FLAG_RVAS / give_item type-code tables (keyed by these same AP
    # item name strings, e.g. "La Lame": 0x11 / 0xF9C3F8) were NOT
    # affected -- those govern inventory granting/detection for a
    # RECEIVED item and were already correct -- so no client.py change
    # needed, only this write-back table.
    "La Lune":           "RSC_X_ECLIPSER_PART1",
    "Le Soleil":         "RSC_X_ECLIPSER_PART2",
    "La Lame":           "RSC_X_ECLIPSER_PART3",
    "Baton":             "RSC_X_BATON",
    "Flambeau":          "RSC_X_FLAMBEAU",
    "Marteau":           "RSC_X_MARTEAU",
    "Calabash":          "RSC_X_CALABASH",
    "Poigne":            "RSC_X_POIGNE",
    "Engineers Key":     "RSC_X_ENGINEERS_KEY",
    "Prison Key Card":   "RSC_X_PRISON_KEY_CARD",
    "Asson":             "RSC_X_ASSON",
    "Shotgun":           "RSC_X_SHOTGUN",
    "Sawed-off Shotgun": "RSC_X_SHOTGUN2",
    "Enseigne":          "RSC_X_ENSEIGNE",
    "MP-909":            "RSC_X_MP5",
    "Tete de Mort":      "RSC_X_TETEDEMORT",
    "Violator":          "RSC_X_VIOLATOR",
    "Flashlight":        "RSC_X_FLASHLIGHT",
    "Book of Shadows":   "RSC_X_BOOK_OF_SHADOWS",
    # "Book of Prophecy" REMOVED from the shufflable pool entirely
    # (2026-08-02, Jon's report + diagnosis) -- see _ITEM_DEFINITIONS'
    # matching comment for the full story. Left here, commented out,
    # purely as a historical marker so a future session doesn't
    # re-add "RSC_X_PROPHECY" here without reading that comment first.
    # "Book of Prophecy":  "RSC_X_PROPHECY",
    "Jacks Schematic":   "RSC_X_JACKS_SCHEMATIC",
    "Light Soul":        "RSC_X_LIGHT_SOUL",
}

_ITEM_DEFINITIONS = [
    # ── Unique progression ────────────────────────────────────────────────────
    ("La Lune",              ItemClassification.progression),
    ("La Lame",              ItemClassification.progression),
    ("Le Soleil",            ItemClassification.progression),
    ("Baton",                ItemClassification.progression),
    ("Flambeau",             ItemClassification.progression),
    ("Marteau",              ItemClassification.progression),
    ("Calabash",             ItemClassification.progression),
    ("Poigne",               ItemClassification.progression),
    ("Engineers Key",        ItemClassification.progression),
    ("Prison Key Card",      ItemClassification.progression),

    # ── Stackable progression ─────────────────────────────────────────────────
    ("Dark Soul",             ItemClassification.progression_skip_balancing),
    ("Retractor",             ItemClassification.progression),
    ("Accumulator",           ItemClassification.progression),
    ("Gad Power",             ItemClassification.progression),

    # ── Unique Retractor Keys (2026-08-18) ────────────────────────────────────
    # 5 distinctly-named, single-copy progression items, one per liveside
    # region -- an alternative to the flat "Retractor" x5 stack above. Only
    # created by create_items() when options.py's UniqueRetractorKeys is on
    # (mutually exclusive with the fungible "Retractor" entry -- see
    # __init__.py's create_items() branch); always reserved here regardless
    # of the option, matching this file's existing "static superset, only
    # instantiate what a given seed uses" pattern (see CADEAUX_MAX_BUNDLE's
    # comment above). Keys/values duplicated from RETRACTOR_KEY_ITEM_NAMES
    # below deliberately kept in this same file (single source of truth),
    # unlike the LIVESIDE_* region-name duplication elsewhere in this
    # codebase, which has to be duplicated across files to avoid a circular
    # import (see access_rules.py's own comment on that).
    ("Retractor - London",    ItemClassification.progression),
    ("Retractor - Prison",    ItemClassification.progression),
    ("Retractor - Florida",   ItemClassification.progression),
    ("Retractor - Salvage",   ItemClassification.progression),
    ("Retractor - Queens",    ItemClassification.progression),

    # ── Weapons ───────────────────────────────────────────────────────────────
    ("Asson",                ItemClassification.useful),
    ("Shotgun",              ItemClassification.useful),
    ("Sawed-off Shotgun",    ItemClassification.useful),
    ("Enseigne",             ItemClassification.useful),
    ("MP-909",               ItemClassification.useful),
    ("Tete de Mort",         ItemClassification.useful),
    # RSC_X_DESERTEAGLE excluded: not interactable in-game (is_verified=False),
    # no physical pickup slot exists for it.
    ("Violator",             ItemClassification.useful),
    ("Flashlight",           ItemClassification.useful),

    # ── Lore ──────────────────────────────────────────────────────────────────
    ("Book of Shadows",      ItemClassification.useful),
    # "Book of Prophecy" REMOVED from the shufflable item pool (2026-08-02,
    # Jon's report + diagnosis: "if we're shuffling gad powers we have to
    # take book of prophecy standard out of the shuffle pool. because book
    # of prophecy patched asset is how we represent gad powers"). Root
    # cause: AP_ITEM_TO_RSC maps "Gad Power" to the synthetic RSC name
    # "RSC_X_GAD_PICKUP" (see that dict below) -- a name that only means
    # anything because gad_pickup_patch.py's dispatch-table patch aliases
    # it to the SAME type_id (0x16) that the real "RSC_X_PROPHECY" (Book
    # of Prophecy) object already uses. So at the game-engine level, ANY
    # placed Gad Power pickup and the real Book of Prophecy are
    # mechanically indistinguishable -- confirmed live: touching ONLY the
    # Gad Power pickup at Louisiana Swampland's Cadeaux 16 fired BOTH
    # "found their Gad Power" (correct) AND "found their Book of Prophecy"
    # (a completely different location, Asylum's Cathedral of Pain
    # Cadeaux-Barrel 3, incorrectly credited) in the same moment. As long
    # as Gad Power keeps reusing the Prophecy book's dispatch case for its
    # own physical representation, "Book of Prophecy" can never be safely
    # placed as a real, independently-trackable AP item anywhere -- any
    # Gad Power pickup, at any location, would falsely credit whichever
    # location currently holds it. Removing it from the pool here doesn't
    # remove the "Deadside Marrow Gates - Book of Prophecy" LOCATION
    # itself (that's a separate, real physical spot handled in
    # locations.py/regions.py and keeps receiving whatever OTHER item
    # Fill assigns it, same as any other donor slot) -- only the ITEM,
    # so it's never the thing Fill needs to place (and therefore detect)
    # anywhere. Self-balancing: create_items()'s Dark-Soul padding loop
    # already tops the pool back up to open_location_count regardless of
    # which unique items exist, so removing one line here doesn't require
    # any other accounting change.
    ("Jacks Schematic",      ItemClassification.useful),

    # ── Filler ────────────────────────────────────────────────────────────────
    ("Light Soul", ItemClassification.useful),  # permanent invincibility
    ("Cadeaux", ItemClassification.filler),
    # Trap/Bonus (2026-08-05): split from a single generic "Trap/Bonus"
    # item into 7 concretely-named items, one per category+polarity, so
    # the AP multiworld log/tracker/chat can show exactly what an item
    # is ("Trap: Poison", "Bonus: Ammo Max Hold", ...) instead of a
    # generic "Trap/Bonus" that never revealed which effect it'd become.
    # This matters because AP's log/chat text is fixed at GENERATION
    # time (whatever name Fill placed) -- it can't be changed later by
    # client.py once the item is actually received, so the roll of
    # "which effect is this copy" had to move from client.py's runtime
    # _apply_trap_bonus_now (as it worked before this date) to
    # __init__.py's create_items(), using self.random the same way every
    # other per-seed randomization in this world already does. See
    # __init__.py's _roll_trap_bonus_item_name() for where that roll now
    # happens, and options.py's TrapBonusCount/TrapBonusMode/
    # TrapBonusDuration/TrapBonus{Secrets,Health,Voodoo,Ammo}Enabled for
    # the options that still control it (category enable/disable and how
    # many total copies are still decided the same way as before; only
    # WHICH concrete name a given copy becomes moved earlier).
    #
    # The four categories map to these 7 concrete items:
    #   secret — cosmetic secret cvar effect (Big Head, Wireframe, Disco
    #            Lights, etc.) — the original Secret Trap behavior, see
    #            TRAP_SAFE_SECRETS/_apply_trap_bonus_now's "secret"
    #            branch. Deliberately kept as ONE generic "Secret Effect"
    #            item rather than splitting into 18 per-secret items
    #            (Jon's call, 2026-08-05) -- these are neutral/silly
    #            cosmetic swaps with no real trap-vs-bonus polarity, so
    #            revealing the specific secret in the AP log wasn't
    #            judged worth 18 more reserved item names. WHICH secret
    #            is still rolled at runtime (still fully seed/idx
    #            reproducible), same as before.
    #   health — poison (drains the whole health pool over ~1 min, ending
    #            in a real death) or a gradual heal — see
    #            start_health_effect/_run_health_effect.
    #   voodoo — instant drain to 0, or a hold at the live Soul-Level cap
    #            — see trigger_voodoo_drain/start_voodoo_max_hold.
    #   ammo   — instant drain to 0, or a hold at max, across Shotgun/
    #            Violator/9mm at once — see trigger_ammo_drain/
    #            start_ammo_max_hold.
    # None of the 7 have a vanilla RSC identity of their own (all
    # synthetic AP-only effects, not real game items) -- generate_output()
    # in __init__.py always retypes their physical pickup to
    # RSC_X_BOOK_OF_SHADOWS with save_idx=0 (via is_trap_bonus_item()
    # below), the same generic "this is an Archipelago item" marker
    # already used for foreign players' items, regardless of whether a
    # given copy is self-found or received from someone else's world.
    # Classification kept as `filler` for all 7, matching the original
    # single item's classification exactly -- this change is scoped to
    # NAMING only, not to opting into AP's native `trap` classification
    # (which would carry its own behavioral side effects, e.g. TrapLink
    # eligibility) — that's a separate decision, not made here.
    ("Secret Effect",          ItemClassification.filler),
    ("Trap: Poison",           ItemClassification.filler),
    ("Bonus: Recovery",        ItemClassification.filler),
    ("Trap: Voodoo Drain",     ItemClassification.filler),
    ("Bonus: Voodoo Max Hold", ItemClassification.filler),
    ("Trap: Ammo Drain",       ItemClassification.filler),
    ("Bonus: Ammo Max Hold",   ItemClassification.filler),
    # Cadeaux Bundle Size (2026-07-27): a bundle representative is worth
    # more than one physical cadeaux, and that value has to survive being
    # sent to another player's world -- a generic "Cadeaux" item received
    # cross-world carries no record of which location (and therefore which
    # bundle size) it came from. Encoding the weight directly in the item's
    # NAME instead (own name/identity travels correctly through AP
    # regardless of world) sidesteps that entirely -- see
    # cadeaux_bundle_item_name() below. Reserved for every possible weight
    # up to CADEAUX_MAX_BUNDLE (matches options.py's CadeauxBundleSize
    # range_end) since item IDs, like location IDs, are a static superset
    # reserved once at import time -- most seeds will only ever create a
    # handful of these concrete names, same "reserve the superset, only
    # instantiate what a given seed uses" pattern as locations.py's
    # location_table.
] + [
    (f"Cadeaux Bundle x{_n}", ItemClassification.filler)
    for _n in range(2, 51)  # CADEAUX_MAX_BUNDLE == 50
]

item_table: dict[str, ItemData] = {
    name: ItemData(
        code           = BASE_ID + i,
        classification = classification,
    )
    for i, (name, classification) in enumerate(_ITEM_DEFINITIONS)
}

# ── Cadeaux Bundle Size helpers (2026-07-27) ───────────────────────────────────
#
# CADEAUX_ITEM_NAMES / is_cadeaux_item() / cadeaux_bundle_item_name() /
# cadeaux_item_weight(): every place that used to do a literal
# `item_name == "Cadeaux"` comparison (generate_output()'s native/
# displacement placement, _cadeaux_identity_map()'s donor pool,
# collect_item()'s filler-counting override, client.py's received-item
# handling) now needs to recognize the WHOLE "Cadeaux"/"Cadeaux Bundle x*"
# family as one logical item, since a bundle representative's item name
# now varies with that bundle's actual size (see regions.py's
# compute_cadeaux_bundle_representatives(), which returns a per-
# representative weight rather than a flat bundle_size, so remainder
# chunks get their own honestly-sized bundle instead of being
# over-credited to a full bundle_size).
CADEAUX_MAX_BUNDLE = 50  # matches options.py's CadeauxBundleSize range_end

CADEAUX_ITEM_NAMES: frozenset[str] = frozenset(
    {"Cadeaux"} | {f"Cadeaux Bundle x{n}" for n in range(2, CADEAUX_MAX_BUNDLE + 1)}
)


def is_cadeaux_item(name: str) -> bool:
    return name in CADEAUX_ITEM_NAMES


def cadeaux_bundle_item_name(weight: int) -> str:
    """
    AP item name for a cadeaux bundle representative worth `weight`
    physical cadeaux. weight<=1 is plain "Cadeaux" -- identical to every
    cadeaux item before this option existed, so bundle_size=1 (the
    default) introduces zero new item names. weight>1 is
    "Cadeaux Bundle x{weight}", a distinct concrete item per denomination
    -- the name itself is what carries the value across a cross-world
    send, since a receiving client has no other way to know which
    location (and therefore which bundle) a foreign item came from.
    """
    return "Cadeaux" if weight <= 1 else f"Cadeaux Bundle x{weight}"


TRAP_BONUS_ITEM_NAMES: frozenset[str] = frozenset({
    "Secret Effect",
    "Trap: Poison", "Bonus: Recovery",
    "Trap: Voodoo Drain", "Bonus: Voodoo Max Hold",
    "Trap: Ammo Drain", "Bonus: Ammo Max Hold",
})


def is_trap_bonus_item(name: str) -> bool:
    """
    True for any of the 7 concrete Trap/Bonus items (2026-08-05 split --
    see the _ITEM_DEFINITIONS comment above). Mirrors is_cadeaux_item()'s
    shape: every place that used to do a literal
    `item_name == "Trap/Bonus"` comparison (generate_output()'s physical-
    pickup retyping, the AP_ITEM_TO_RSC fallback) now needs to recognize
    the whole family as one logical "this is a Trap/Bonus item" concept,
    since the concrete name now varies per-copy.
    """
    return name in TRAP_BONUS_ITEM_NAMES


def cadeaux_item_weight(name: str) -> int:
    """
    Inverse of cadeaux_bundle_item_name() -- recovers the physical cadeaux
    value a received item name represents. "Cadeaux" (or anything
    unrecognized) is worth 1, matching pre-bundling behavior exactly.
    Used by client.py to know how much to actually grant per received
    item, instead of assuming every "Cadeaux"-family item is worth 1.
    """
    if name == "Cadeaux":
        return 1
    if name.startswith("Cadeaux Bundle x"):
        try:
            return int(name.rsplit("x", 1)[1])
        except ValueError:
            return 1
    return 1

# ── Stackable item counts ─────────────────────────────────────────────────────
# Used by create_items() to know how many copies to add to the pool.
# Soul count is derived from CHECKABLE_LOCS at world init — defined here
# as a sentinel and overwritten in __init__.py after locations are known.

STACKABLE_COUNTS: dict[str, int] = {
    "Retractor":  5,
    "Accumulator": 3,
    "Gad Power":   3,
    # "Dark Soul" count is set dynamically — see __init__.py
    # NOTE: the 5 "Retractor - <region>" items (unique_retractor_keys mode)
    # are deliberately NOT listed here -- each is a single-copy item, not a
    # stack, so STACKABLE_COUNTS doesn't apply. See RETRACTOR_KEY_ITEM_NAMES
    # below and __init__.py's create_items().
}

# ── Unique Retractor Keys (2026-08-18) ─────────────────────────────────────────
# Maps each liveside region's full display name to its dedicated Retractor
# item name (see _ITEM_DEFINITIONS above). Static/fixed, not per-seed
# randomized -- unlike the standalone randomizer's retractor_level_assignment
# (which has to randomly decide which NATIVE retractor pickup unlocks which
# region, since standalone's 5 retractors are otherwise identical/fungible),
# AP's fill naturally handles "where is each key found" on its own once the
# item itself has a fixed identity: Fill can place "Retractor - Prison"
# anywhere logic allows (own world or another player's), same as any other
# named progression item, so no separate assignment step is needed here.
#
# Keys are string literals, not the LIVESIDE_* constants from regions.py --
# regions.py imports from this module (`from .items import ...`), so
# importing back from regions.py here would be circular. Must exactly match
# regions.py's LIVESIDE_LONDON/LIVESIDE_PRISON/LIVESIDE_FLORIDA/
# LIVESIDE_SALVAGE/LIVESIDE_QUEENS values (same convention access_rules.py
# already uses for its own duplicated _LIVESIDE_* constants).
RETRACTOR_KEY_ITEM_NAMES: dict[str, str] = {
    "Down Street Station, London": "Retractor - London",
    "Gardelle County Jail, Texas": "Retractor - Prison",
    "Summer Camp, Florida":        "Retractor - Florida",
    "Salvage Yard, Mojave Desert": "Retractor - Salvage",
    "Mordant Street, Queens, NY":  "Retractor - Queens",
}

# ── RSC name mapping ──────────────────────────────────────────────────────────
# Maps AP item names back to the RSC object names patcher.py expects.
# Stackable items need this since their AP name differs from their RSC name.

AP_ITEM_TO_RSC: dict[str, str] = {
    "Retractor":   "RSC_X_RETRACT",
    "Accumulator": "RSC_X_ACCUMULATOR",
    "Gad Power":   "RSC_X_GAD_PICKUP",
    # Unique Retractor Keys: all 5 write back to the same RSC pickup as
    # plain "Retractor" -- world identity is 100% position-based at the exe
    # level (unique_retractor_keys_patch.py's per-portal CF_CUSTOM flags),
    # not RSC-name-based, so any of RSC_X_RETRACT/RETRACT1/RETRACT2 can back
    # any of these 5 AP items without conflict. Matches the standalone
    # randomizer's own design for this feature.
    "Retractor - London":  "RSC_X_RETRACT",
    "Retractor - Prison":  "RSC_X_RETRACT",
    "Retractor - Florida": "RSC_X_RETRACT",
    "Retractor - Salvage": "RSC_X_RETRACT",
    "Retractor - Queens":  "RSC_X_RETRACT",
    # Every other one-of-a-kind item's write-target is its own canonical RSC
    # name (_UNIQUE_ITEM_RSC_NAMES above) -- seeded here so the patcher
    # (generate_output's `AP_ITEM_TO_RSC.get(item_name, item_name)`) keeps
    # resolving correctly now that item_name is a friendly display string
    # instead of the RSC name itself.
    **_UNIQUE_ITEM_RSC_NAMES,
    # EXCEPT Violator: RSC_X_VIOLATOR has no physical pickup handler in the
    # vanilla engine. The patcher rewrites it to RSC_Q_VIOLATOR, which does
    # have a working pickup — this mirrors what the standalone randomizer
    # does at patch time. Deliberately overrides the canonical-name entry
    # from _UNIQUE_ITEM_RSC_NAMES above (dict literal order means this one
    # wins) -- client.py's ITEM_FLAG_RVAS/AP_ITEM_INJECTION still key on the
    # canonical "RSC_X_VIOLATOR" for flag-matching/injection, only the
    # patcher's write-target diverges.
    "Violator": "RSC_Q_VIOLATOR",
    # Trap/Bonus (2026-08-05 split, see _ITEM_DEFINITIONS's comment): none
    # of the 7 concrete names have a real RSC identity -- __init__.py's
    # generate_output() actually hardcodes RSC_X_BOOK_OF_SHADOWS +
    # save_idx=0 directly for any of them (via is_trap_bonus_item(),
    # matching the foreign-item marker branch exactly, self-found or
    # not), so these entries are never consulted by that path. Kept here
    # anyway for any generic AP_ITEM_TO_RSC.get() consumer.
    "Secret Effect":          "RSC_X_BOOK_OF_SHADOWS",
    "Trap: Poison":           "RSC_X_BOOK_OF_SHADOWS",
    "Bonus: Recovery":        "RSC_X_BOOK_OF_SHADOWS",
    "Trap: Voodoo Drain":     "RSC_X_BOOK_OF_SHADOWS",
    "Bonus: Voodoo Max Hold": "RSC_X_BOOK_OF_SHADOWS",
    "Trap: Ammo Drain":       "RSC_X_BOOK_OF_SHADOWS",
    "Bonus: Ammo Max Hold":   "RSC_X_BOOK_OF_SHADOWS",
}