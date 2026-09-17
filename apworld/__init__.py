"""
__init__.py
───────────
Shadow Man Remastered Archipelago World.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, Any
from BaseClasses import ItemClassification, Tutorial
from worlds.AutoWorld import World, WebWorld
from worlds.LauncherComponents import components, Component, launch_subprocess, Type, icon_paths

from .access_rules import R, make_location_rule, make_entrance_rule
from .constants import GATE_VANILLA_SL, GATE_PRESETS, COFFIN_GATE_ORDER
from .fill import _shuffle_gates, FIXED_SOUL_LOCS, CHECKABLE_LOCS, LIVESIDE_REGIONS, CADEAUX_666_LOCS, \
    DEPTH_BUCKETS
from .regions import create_regions, DEADSIDE_PORTAL_FILES, ASYLUM_ENGINE_BLOCK, \
    ASYLUM_ENGINE_BLOCK_LONDON, ASYLUM_ENGINE_BLOCK_PRISON, ASYLUM_ENGINE_BLOCK_FLORIDA, \
    ASYLUM_ENGINE_BLOCK_SALVAGE, ASYLUM_ENGINE_BLOCK_QUEENS, \
    compute_cadeaux_bundle_representatives, nested_round_robin_by_group
from .items import ShadowManItem, item_table, STACKABLE_COUNTS, AP_ITEM_TO_RSC, _UNIQUE_ITEM_RSC_NAMES, \
    is_cadeaux_item, cadeaux_bundle_item_name, CADEAUX_ITEM_NAMES, is_trap_bonus_item, \
    RETRACTOR_KEY_ITEM_NAMES
from .locations import ShadowManLocation, location_table
from .options import ShadowManOptions


# ── Launcher registration ─────────────────────────────────────────────────────

def launch_client() -> None:
    from . import client
    launch_subprocess(client.launch, name="ShadowManClient")


components.append(Component(
    "Shadow Man Remastered Client",
    func=launch_client,
    component_type=Type.CLIENT,
    icon="shadowman",
))

# Book of Gad — same asset the standalone patcher's GUI uses (see
# assets/hd_book_of_gad.png), copied in as worlds/shadowman/icon.png.
# "ap:module.name/path" is the Launcher's scheme for loading an icon that
# lives inside an installed apworld (same pattern peggle_deluxe.apworld
# uses for its own icon.jpg).
icon_paths["shadowman"] = f"ap:{__name__}/icon.png"


# ── Module-level helpers ──────────────────────────────────────────────────────

def _make_synthetic_raw(rsc_name: str, original_raw, save_idx=None):
    """
    Return a copy of original_raw with object replaced by rsc_name.
    If save_idx is given, also overrides the location's save_idx — used for
    the cross-game "Archipelago item" marker (see generate_output's
    location.item.player != self.player branch), which deliberately zeroes
    save_idx to match Book of Shadows' own native identity rather than
    keeping the original location's save_idx bucket.
    """
    if save_idx is not None:
        return original_raw._replace(object=rsc_name, save_idx=save_idx)
    return original_raw._replace(object=rsc_name)


# ── Web world ─────────────────────────────────────────────────────────────────

class ShadowManWebWorld(WebWorld):
    theme = "dirt"
    tutorials = [Tutorial(
        tutorial_name="Setup Guide",
        description="A guide to setting up Shadow Man Remastered randomizer with Archipelago.",
        language="English",
        file_name="guide_en.md",
        link="guide/en",
        authors=["you"],
    )]


# ── World ─────────────────────────────────────────────────────────────────────

class ShadowManWorld(World):
    """
    Shadow Man Remastered randomizer for Archipelago.
    Collect Dark Souls to open coffin gates and power the five Engine Blocks,
    then defeat Legion to complete the game. Goal completion is detected
    automatically — the client watches for Legion's death in-game and
    reports it to the server (see client.py's _goal_watcher_loop).
    """

    game              = "Shadow Man Remastered"

    options_dataclass = ShadowManOptions
    web               = ShadowManWebWorld()
    topology_present  = True

    # Built at class definition time from module-level tables.
    # item_table and location_table are populated when items.py / locations.py
    # are first imported, before this class body executes.
    item_name_to_id:     Dict[str, int] = {
        name: data.code for name, data in item_table.items()
    }
    location_name_to_id: Dict[str, int] = {
        name: data.code for name, data in location_table.items()
    }
    # Cadeaux Bundle Size (2026-07-27): "Cadeaux Bundle x{N}" (see items.py)
    # is a distinct concrete item per denomination, since the bundle's
    # value has to travel in the item's own name/identity to survive a
    # cross-world send -- but R.cadeaux_666() (access_rules.py) needs to
    # count all of them as one logical currency. AP's standard
    # state.count_group() does exactly that across every name in a group;
    # cadeaux_666() was updated the same day to use it instead of a single
    # state.count("Cadeaux", player) call.
    item_name_groups: Dict[str, set] = {
        "Cadeaux": set(CADEAUX_ITEM_NAMES),
    }

    # Per-instance, set in generate_early()
    gate_values: Dict[str, int]
    sl_thresholds: Dict[int, int]
    # Logic-only copy of sl_thresholds, padded by SoulLogicBuffer (see that
    # option's docstring) — used for fill/rule-building ONLY. sl_thresholds
    # itself stays exactly the real in-game numbers (slot_data / exe patch).
    sl_thresholds_logic: Dict[int, int]
    entrance_shuffle: Dict[str, str] | None
    # Cadeaux Bundle Size (2026-07-27): precomputed once here via self.random
    # so create_regions()'s location instantiation and create_items()'s
    # "Cadeaux" item-pool sizing can never disagree on which cadeaux rows
    # are real AP locations. See regions.py's
    # compute_cadeaux_bundle_representatives() for the full derivation.
    cadeaux_bundle_representatives: dict[str, int]
    # Capped AP-logic-only Fogometers Cadeaux requirement (2026-07-27) --
    # see generate_early()'s comment above where this is computed. Never
    # used for the real physical door requirement (that stays the raw,
    # uncapped option value in slot_data/the EXE patch).
    cadeaux_required_logic: int

    # ── Early generation ──────────────────────────────────────────────────────

    def generate_early(self) -> None:
        """
        Run gate shuffle before fill begins so gate_values is available
        for create_regions() and set_rules().

        Universal Tracker support (2026-07-28, Jon's report — "tracker
        seems to get tripped up by entrance rando"): UT reconstructs this
        world's region graph locally (calling generate_early()/
        create_regions() again on the player's machine) to run its own
        logic pass, entirely separately from the real generation run that
        actually produced the hosted seed. Four of this method's values
        below are chosen via self.random: gate_values, sl_thresholds,
        entrance_shuffle, and cadeaux_bundle_representatives. UT's regen
        does NOT replay the real generation run's exact self.random call
        sequence (it skips create_items()/fill() entirely), so a fresh
        self.random draw here during a UT regen produces DIFFERENT values
        than the real seed used -- for entrance_shuffle specifically, a
        totally different (and wrong) Deadside portal->region mapping,
        which corrupts UT's entire Deadside reachability graph. Confirmed
        this was made worse by entrance_shuffle not even being present in
        slot_data at all until this same fix (see fill_slot_data()) -- UT
        had no way to recover the real mapping even in principle.
        self._ut_passthrough (see interpret_slot_data()'s docstring) holds
        the real seed's actual values, recovered via UT's own re-gen-
        passthrough mechanism, when running under UT; each block below
        uses it instead of re-randomizing whenever it's available.
        """
        self._ut_passthrough = None
        # Diagnostic (2026-07-28, Jon: no crash/warning showed up in the
        # console, so the earlier "unhandled exception" theory looks
        # wrong -- next most likely explanation is re_gen_passthrough
        # simply never getting populated the way this code assumes in the
        # first place (still falling back to fresh randomization every
        # regen, same as before this passthrough support existed at all).
        # Unconditional so it fires on every generate_early() call --
        # both the real one-time generation run and (if UT ever actually
        # invokes it) each UT regen -- to see directly whether
        # re_gen_passthrough exists at all and whether this world's game
        # name is a key in it.
        _has_rgp = hasattr(self.multiworld, "re_gen_passthrough")
        print(
            f"  [UT-passthrough] hasattr(multiworld.re_gen_passthrough)="
            f"{_has_rgp}, keys={list(self.multiworld.re_gen_passthrough.keys()) if _has_rgp else None}, "
            f"this world's game={self.game!r}"
        )
        if _has_rgp and self.game in self.multiworld.re_gen_passthrough:
            _pt = self.multiworld.re_gen_passthrough[self.game]
            # Defensive validation (2026-07-28, Jon's report: after this
            # passthrough support first shipped, UT went from "wrong
            # reachability for entrance-shuffled locations" to "shows
            # NOTHING available at all" -- a much bigger regression than
            # intended. Most likely explanation: an unhandled exception
            # somewhere in the four blocks below while indexing into a
            # passthrough dict that didn't actually look exactly like this
            # code assumed (this is the first time this world has used
            # re_gen_passthrough -- the exact shape UT hands back was
            # inferred from worlds/tunic's pattern, not confirmed against
            # a live UT regen). An uncaught exception here would abort
            # generate_early() entirely, and with it UT's whole regen for
            # this world -- turning "one value wrong" into "everything
            # shows unreachable." Checking the expected shape up front,
            # once, rather than dict-indexing blind four separate times
            # below, means any mismatch just falls back to full fresh
            # randomization (the exact pre-passthrough behavior: still
            # logically sound, just not entrance-rando-accurate) instead
            # of taking down reachability entirely again.
            try:
                _pt["gate_values"].items()
                _pt["soul_thresholds"].items()
                _pt["cadeaux_bundle_representatives"].items()
                # entrance_shuffle is allowed to be missing/None --
                # entrance_mode "off" is a real, valid state, handled via
                # .get() below rather than a hard key requirement here.
                self._ut_passthrough = _pt
            except (KeyError, AttributeError, TypeError) as _exc:
                print(
                    f"  [WARN] UT re_gen_passthrough for {self.game!r} was "
                    f"present but not shaped as expected ({_exc!r}) -- "
                    f"ignoring it and falling back to fresh randomization "
                    f"for this regen rather than risk crashing it."
                )

        # Entrance randomizer (2026-07-21) — deadside_only mode only so far
        # (see options.py EntranceMode docstring; cross_hub, which also mixes
        # in the 5 Dark Engine soul gates, is not implemented yet). A pure
        # random bijection over the 9 Deadside portal files — deadside_only
        # has no extra placement constraints in the standalone either (its
        # two hard constraints, Asylum-behind-a-coffin-gate and Salvage's
        # soul gate never leading to a Gad Temple, are both specifically
        # about the Deadside/Dark-Engine crossover that only cross_hub
        # allows — confirmed by reading shuffle_unified() in the
        # standalone's randomizers/entrance_randomizer.py: deadside_only is
        # `dict(zip(shuffled, original))` with no filtering at all).
        #
        # Moved ahead of gate_values/_shuffle_gates() below (2026-08-07,
        # Jon's report of recurring "Marrow Gates / Wasteland" accessibility
        # failures under entrance_mode=deadside_only): _shuffle_gates()'s own
        # "safe" hierarchy needs to know the REAL, post-shuffle portal->gate
        # mapping to protect the right gates -- see its own updated
        # docstring in fill.py for the full root-cause writeup. Computing
        # entrance_shuffle first (it doesn't depend on gate_values/preset at
        # all) lets it be threaded straight into that call.
        if self._ut_passthrough is not None:
            # Recovered from the real seed (fill_slot_data()'s
            # "entrance_shuffle" key, added 2026-07-28 alongside this
            # passthrough support -- it didn't exist in slot_data at all
            # before, so there was previously no way for UT to recover
            # this even in principle). None (entrance_mode was "off" this
            # seed) round-trips through JSON as None correctly.
            _es = self._ut_passthrough.get("entrance_shuffle")
            self.entrance_shuffle = dict(_es) if _es else None
        elif self.options.entrance_mode.current_key == "deadside_only":
            dest_files = list(DEADSIDE_PORTAL_FILES)
            self.random.shuffle(dest_files)
            self.entrance_shuffle = dict(zip(DEADSIDE_PORTAL_FILES, dest_files))
        else:
            self.entrance_shuffle = None

        preset_name = self.options.gate_preset.current_key
        preset      = GATE_PRESETS.get(preset_name, {})

        if self._ut_passthrough is not None:
            # Recovered from the real seed's own slot_data -- gate ids are
            # already strings (JSON-safe), values plain ints.
            self.gate_values = {k: int(v) for k, v in self._ut_passthrough["gate_values"].items()}
        elif preset.get("shuffle_gates", False):
            # Effective cap = the lower of the preset's own cap and the
            # max_gate_sl option (10 = uncapped). Slack between the 120-soul
            # supply and the top requirement is what keeps AP fill reliable.
            caps = [c for c in (preset.get("max_sl"),
                                int(self.options.max_gate_sl)) if c is not None and c < 10]
            self.gate_values = _shuffle_gates(
                self.random,
                locked = preset.get("lock_gates", frozenset()),
                max_sl = min(caps) if caps else None,
                safe   = preset.get("safe", True),
                entrance_shuffle = self.entrance_shuffle,
            )
        elif preset.get("no_soul_gates", False):
            self.gate_values = {g: 0 for g in GATE_VANILLA_SL}
        else:
            self.gate_values = dict(GATE_VANILLA_SL)

        # "N gates open" — force the first N gates to SL0 (linear coffin
        # gates in fixed order first, then a seed-shuffled remainder).
        # open_gates_n option: -1 (default) means "use whatever the chosen
        # preset itself bakes in" (unchanged behavior for anyone who
        # doesn't touch this option — preserves every existing YAML/seed).
        # 0-6 explicitly overrides the preset's own value, mirroring the
        # standalone's --open-gates flag, which always wins when set. Runs
        # before create_regions()/set_rules(), so fill logic sees the
        # opened gates automatically via gate_values.
        override_n = int(self.options.open_gates_n)
        n = override_n if override_n >= 0 else (preset.get("open_gates_n", 0) or 0)
        # Skipped entirely under UT passthrough: self.gate_values was just
        # set to the real seed's FINAL, already-resolved values above
        # (open_gates_n forcing included) -- re-running this block would
        # both burn an unnecessary self.random.shuffle() and, worse, force
        # a freshly (and differently) shuffled remainder's gates to SL0 on
        # top of the already-correct recovered values, corrupting them.
        if self._ut_passthrough is None and n > 0:
            remainder = [g for g in GATE_VANILLA_SL if g not in COFFIN_GATE_ORDER]
            self.random.shuffle(remainder)
            for g in (list(COFFIN_GATE_ORDER) + remainder)[:n]:
                if g in self.gate_values:
                    self.gate_values[g] = 0

        # Soul Level thresholds (SL1-SL10 -> souls required), computed here via
        # self.random rather than inside patcher.py's own rng. generate_output()
        # and fill_slot_data() both need the SAME dict — generate_output() runs
        # its EXE patch on a background thread pool concurrently with
        # write_multidata() (which calls fill_slot_data()), per Main.py, so
        # letting patcher.py roll its own independent rng() draw would produce
        # thresholds that don't match what fill_slot_data() reports to the
        # client. Computing once, here, and threading the same dict through
        # both call sites keeps them in sync regardless of thread scheduling.
        # See soul_threshold_patch.py for the mode semantics.
        from .soul_threshold_patch import randomize_soul_thresholds, VANILLA_SOUL_THRESHOLDS
        # "full_random" is this Choice's display name for
        # soul_threshold_patch.py's own mode="random" sentinel — translate
        # here rather than renaming one side or the other. (Briefly removed
        # 2026-08-09, then re-added the same day once the real bug behind
        # the generation failures was found elsewhere — see options.py's
        # SoulThresholdMode docstring for the full history.)
        _mode_key = self.options.soul_threshold_mode.current_key
        _st_mode  = "random" if _mode_key == "full_random" else _mode_key
        if self._ut_passthrough is not None:
            # Recovered from the real seed (fill_slot_data()'s
            # "soul_thresholds" key) -- JSON round-trips dict keys as
            # strings, so int() them back like on_package()'s own
            # _raw_thresholds handling in client.py does.
            self.sl_thresholds = {int(k): int(v) for k, v in self._ut_passthrough["soul_thresholds"].items()}
        elif _st_mode != "off":
            self.sl_thresholds = randomize_soul_thresholds(self.random, mode=_st_mode)
        else:
            self.sl_thresholds = dict(VANILLA_SOUL_THRESHOLDS)

        # Soul Logic Buffer (2026-07-25, Jon's request): "AP fill placement
        # so seeds don't feel razor-tight on souls" — pad each gate's LOGIC
        # requirement above its real in-game threshold, so fill treats a
        # gate as needing more souls than it actually does and keeps that
        # many extra, already-reachable Dark Souls placed before it. Purely
        # a fill/logic-time construct: self.sl_thresholds (the REAL
        # requirement — what's patched into the exe and reported to the
        # client via fill_slot_data) is untouched; only this separate copy,
        # handed to create_regions() for rule-building, is padded. SL0 is
        # deliberately excluded from padding — _soul_level() treats an
        # exact threshold of 0 as "always free" (used by e.g. open_gates_n
        # forcing a gate to SL0); padding it would turn "no souls required
        # at all" into "buffer souls required", breaking that guarantee.
        # Capped at VANILLA_SOUL_THRESHOLDS[10] (120 — the hard ceiling,
        # SL10 is always exactly the total dark souls in the game) so
        # padding can never make a gate outright impossible to satisfy even
        # with every soul in the seed. SoulLogicBuffer is a named-tier
        # Choice (off/hard/medium/easy), not a raw number — see
        # options.py's SOUL_LOGIC_BUFFER_VALUES for what each tier means.
        from .options import SOUL_LOGIC_BUFFER_VALUES
        _soul_buffer = SOUL_LOGIC_BUFFER_VALUES.get(
            self.options.soul_logic_buffer.current_key, 0)
        # UT passthrough (2026-07-28, Jon: "we'd want it based on live
        # data in shadowman, not speculative data") -- the buffer's whole
        # purpose is a FILL-time placement safety margin (ensuring extra,
        # already-reachable souls exist before a gate so seeds don't feel
        # razor-tight -- see this block's own docstring above). That only
        # matters to the real, one-time server-side generation's own fill
        # sweep. UT reuses this exact same create_regions() call to build
        # its OWN copy of the region graph for LIVE reachability tracking
        # -- it never does any placement, so padding its rules the same
        # way just makes the tracker under-report reachability against
        # what the physical in-game gate actually requires (confirmed:
        # Jon had real SL1, satisfying GATE_DEADSIDE_WASTELAND's true
        # 2-soul requirement, but UT's /explain still said not reachable
        # because Soul Logic Buffer: Medium was padding the LOGIC
        # threshold to 4). self._ut_passthrough is only ever set during a
        # UT-triggered regen (never during the real generation run), so
        # it's the correct signal to skip padding on and use the real,
        # unbuffered self.sl_thresholds for rule-building instead.
        if _soul_buffer > 0 and self._ut_passthrough is None:
            _max_souls = VANILLA_SOUL_THRESHOLDS[10]
            self.sl_thresholds_logic = {
                lvl: (0 if lvl == 0 else min(v + _soul_buffer, _max_souls))
                for lvl, v in self.sl_thresholds.items()
            }
        else:
            self.sl_thresholds_logic = dict(self.sl_thresholds)

        # Baseline "free" Cadeaux (2026-07-24, Jon's call). Vanilla has 666
        # total Cadeaux, but only cadeaux_checkable_count of them are
        # is_verified/AP-trackable (currently 653 -- 13 short of the default
        # fogometers_cadeaux_required=666), which made the Fogometers Light
        # Soul location permanently unreachable in AP's full-accessibility
        # sweep once it was a live AP check. Rather than lowering the
        # requirement, that location is now gated behind its own
        # CadeauxGatedContent option (default off — excluded entirely, see
        # options.py/regions.py), and only when it's ON do we bother
        # precollecting the shortfall as starting Cadeaux -- same idea as a
        # real player already having picked up the un-randomized/unplaceable
        # cadeaux automatically. Also requires Cadeauxsanity (Cadeaux is only an
        # AP-tracked item then -- see cadeaux_666()'s docstring in
        # access_rules.py); zero deficit is a no-op either way.
        #
        # _CADEAUX_SLACK (2026-07-24): even with the 13-item deficit
        # precollected AND collect_item()'s Cadeaux-counting fix (see
        # create_item() below), a live repro topped out at 665/666 in one
        # seed -- Cadeaux items can land in the OTHER player's world (they're
        # ordinary filler, free to go anywhere once Cadeauxsanity is on), and one
        # apparently landed somewhere with its own reachability snag on that
        # side. Rather than chase down which specific cross-world placement
        # stalls in any given seed (varies seed to seed), add a flat safety
        # margin, mirroring the existing "+20" cushion create_items() already
        # uses for the soul-threshold math just below.
        # Cadeaux Bundle Size (2026-07-27, Jon's request): compute the
        # bundle-representative set ONCE here, via self.random, same as
        # every other per-seed randomization in this file (gate_values,
        # sl_thresholds, entrance_shuffle above). Both create_regions()
        # (which cadeaux rows become real AP locations) and create_items()/
        # the deficit calc just below (how many "Cadeaux" AP items can ever
        # exist) read this SAME set, so they can never disagree on the
        # count -- see regions.py's compute_cadeaux_bundle_representatives()
        # for the full derivation and why a mismatch there would break
        # AP's fill algorithm (items must match open locations).
        if self._ut_passthrough is not None:
            # Recovered from the real seed (fill_slot_data()'s
            # "cadeaux_bundle_representatives" key, added 2026-07-28
            # alongside this passthrough support). A UT regen calling
            # compute_cadeaux_bundle_representatives() fresh would both
            # pick a different representative set AND consume a
            # self.random.shuffle() draw that isn't otherwise needed here.
            self.cadeaux_bundle_representatives = {
                k: int(v) for k, v in self._ut_passthrough["cadeaux_bundle_representatives"].items()
            }
        else:
            self.cadeaux_bundle_representatives = compute_cadeaux_bundle_representatives(
                int(self.options.cadeaux_bundle_size), self.random)

        # Trap/Bonus barrel promotion (2026-08-01, Jon's request, renamed
        # from "Secret Trap" 2026-08-03 when the item grew past just
        # secrets): rather than trap_bonus_count items just displacing
        # existing Dark Soul
        # padding (see create_items()'s original self-balancing design),
        # promote that many previously-always-excluded "barrel" category
        # rows (fill.py's CHECKABLE_LOCS already carries all of them, fully
        # mapped/verified — is_verified=False rows are already filtered out
        # of CHECKABLE_LOCS entirely, same as every other category) to real
        # AP locations instead — genuinely more room, not a reshuffle of
        # what already exists. Computed once here via self.random (same
        # per-seed-RNG convention as cadeaux_bundle_representatives just
        # above), so create_regions() (which barrel rows become real
        # Locations) and create_items() (the matching open_location_count
        # adjustment) can never disagree on the set. UT passthrough follows
        # the identical pattern as cadeaux_bundle_representatives — see
        # fill_slot_data()/interpret_slot_data().
        #
        # source_file == "quest.rsc" restriction (2026-07-31, Jon's report):
        # a promoted barrel from ah4fogom turned out non-interactable in
        # practice despite is_verified=TRUE. is_verified was audited for
        # "this is a real, visible barrel in-game" in the standalone
        # randomizer's plain-filler-barrel context (any container works
        # there) — it was never meant to vouch for "safe to retype into an
        # arbitrary named pickup like RSC_X_BOOK_OF_SHADOWS." Checked
        # data/locations.csv: barrel rows outside quest.rsc come from
        # pickups.rsc/resource.rsc/instance.rsc/fx.rsc, and fx.rsc in
        # particular is documented (docs/TECHNICAL.md §10.5) as "particle
        # and light effects" — not a quest-object file at all, yet all 25
        # of its barrel rows read is_verified=TRUE (a particle effect is
        # trivially "visible," but that doesn't make it a
        # kexShadowManQuestObject the engine runs pickup logic against).
        # quest.rsc is the one file every other retype path (souls,
        # cadeaux, progression items) already proves safe, and restricting
        # to it still leaves ~1755 TRUE-verified candidates — far more than
        # TrapBonusCount's 200-item cap will ever need.
        # Diversity pass (2026-08-02, Jon's request: "picking slots with a
        # variety of subregions for more interesting logic placement...
        # too many slots may just be free or just eng_key"). Same
        # round_robin_by_group() helper and same (level_region, gate_raw)
        # grouping as compute_cadeaux_bundle_representatives() just above
        # -- see that function's docstring / round_robin_by_group()'s own
        # docstring for why a plain self.random.sample() (the previous
        # approach) doesn't achieve this: a uniform sample over the flat
        # candidate pool still represents each subregion/gate roughly
        # proportional to how many raw barrel candidates it happens to
        # have, so a big, densely-populated bucket (e.g. a "free"/no-gate
        # area, or a heavily-farmed gate like ENG_KEY) would dominate the
        # promoted set purely because it has more candidates, not because
        # that's actually desired. round_robin_by_group(..., n=_barrel_n)
        # instead guarantees near-even representation across whatever
        # subregion/gate groups quest.rsc barrels actually exist in,
        # regardless of each group's raw size.
        # Depth-bucket dimension (2026-08-18, Jon's idea): (level_region,
        # gate_raw) alone leaves liveside interiors nearly undiversified —
        # gate_raw is None for almost everything inside a liveside level (the
        # region's own entrance rule lives on the region CONNECTION, not on
        # individual locations), so most liveside barrel candidates collapsed
        # into one (region, None) group regardless of how far into the level
        # they physically sit. fill.py's DEPTH_BUCKETS adds a third grouping
        # dimension — early/mid/late thirds of that region's own zone range,
        # via the RSC record's own sector tag — so that flat group now splits
        # by physical progression too. See DEPTH_BUCKETS's own docstring in
        # fill.py for the full derivation and real-data verification.
        # Level-spread fix (2026-08-18, Jon's follow-up: "otherwise the
        # levels with the most checks are gonna have all the location checks
        # in them most likely, like the temple levels and certain asylum
        # levels"). A FLAT (level_region, gate_raw, depth_bucket) key still
        # let a level that fans out into more sub-groups (more gates, or a
        # region split like Engine Block's 6-way schism division) dominate
        # purely from having more competing groups at round_robin_by_group's
        # one flat tier — confirmed empirically (n=200, same seed):
        # as4dkeng got 23 picks, Underground/Cageways/Cathedral of Pain got
        # as few as 6, a ~4x disparity, exactly matching Jon's prediction.
        # Switched to nested_round_robin_by_group() with level_id as the
        # OUTERMOST tier — every physical level now gets one turn per round
        # regardless of how many gate/depth sub-groups it fans out into
        # beneath that. Same seed, same scenario: 12-13 picks across all 16
        # levels, within one pick of a perfectly even 200/16 = 12.5 share.
        # See nested_round_robin_by_group()'s own docstring in regions.py
        # for the full mechanism and verification.
        if self._ut_passthrough is not None:
            self.barrel_promoted_locs = frozenset(self._ut_passthrough["barrel_promoted_locs"])
        else:
            _barrel_raw_candidates = sorted(
                (l for l in CHECKABLE_LOCS
                 if l.category == "barrel" and l.source_file == "quest.rsc"
                 # can_softlock (2026-08-15, Jon's request): excludes ledges/
                 # one-way-drop spots from ever becoming real, pickable AP
                 # locations here -- independent of set_rules()'s item-type
                 # bans just below (those stop a required item from landing
                 # on a barrel; this stops the barrel itself from being
                 # somewhere collecting the check could physically strand the
                 # player, regardless of what item ends up there).
                 and not l.can_softlock),
                key=lambda l: l.loc_key)
            _barrel_n = min(int(self.options.trap_bonus_count), len(_barrel_raw_candidates))
            _barrel_picked = nested_round_robin_by_group(
                self.random, _barrel_raw_candidates,
                key_fns=[
                    lambda l: l.level_id,
                    lambda l: l.level_region,
                    lambda l: l.gate_raw,
                    lambda l: DEPTH_BUCKETS.get(l.loc_key, 1),
                ],
                n=_barrel_n)
            self.barrel_promoted_locs = frozenset(l.loc_key for l in _barrel_picked)

        # Cadeaux Bundle Size x Fogometers Cadeaux Required (2026-07-27,
        # Jon's report): capping the LOGIC-side requirement at however many
        # "Cadeaux" AP items can actually exist (cadeaux_bundle_representatives'
        # count) instead of leaving it at the raw option value (up to 666)
        # regardless of bundling. Before this fix, a bundle size that shrank
        # the checkable count well below the configured
        # fogometers_cadeaux_required fed straight into the deficit formula
        # below, precollecting hundreds of free starting "Cadeaux" items to
        # bridge the gap -- mathematically kept R.cadeaux_666() satisfiable,
        # but not remotely the intended behavior ("it just needs to verify
        # you will get whatever the cadeaux gate setting is... doesn't need
        # to give 500 cadeaux for free"). cadeaux_required_logic is threaded
        # into create_regions() below (BoundR/R.cadeaux_666()'s threshold)
        # INSTEAD of the raw option value -- this only affects the AP LOGIC
        # gate, never the real physical Fogometers door requirement
        # (fill_slot_data() still sends the raw, uncapped
        # fogometers_cadeaux_required for the EXE patch/client, since that
        # stays collectible from ordinary vanilla gameplay regardless of
        # bundling -- non-representative cadeaux stay physically present,
        # just not individually AP-tracked). Capping here guarantees
        # cadeaux_checkable - cadeaux_required_logic is always >= 0, so the
        # deficit formula below naturally collapses to just the flat
        # _CADEAUX_SLACK cushion (the original small "cross-world placement
        # snag" margin) instead of bridging a bundling-sized gap.
        #
        # Real-door floor (2026-08-07, Jon's report): cadeaux_patch.py --
        # the actual EXE patcher -- never patches the Fogometers door to
        # the raw fogometers_cadeaux_required value as-is. It floors it at
        # 5x altar_cadeaux_required first: `max(threshold * 5, min(666,
        # fog_required))` (cadeaux_patch.py's apply_cadeaux_patch()). That
        # floor is always <= 666 by construction (AltarCadeauxRequired's
        # own range caps at 133, and 133*5 = 665), so the door itself can
        # never become unopenable -- but AP's LOGIC side was still using
        # the raw, un-floored option value here, completely unaware of
        # that floor. With altar=129/fog=281 (a real seed Jon hit), the
        # physical door actually gets patched to require 645 cadeaux
        # (max(645, 281)), while AP's own reachability check thought 281
        # was enough -- a real desync: a seed could be considered
        # "complete"/reachable by AP's fill and tracker while the real,
        # patched door still can't physically be opened with what AP
        # thinks is sufficient. Mirroring cadeaux_patch.py's exact formula
        # here (fog_required's own range already caps at 666, so the
        # `min(666, ...)` half of that formula is redundant on this side)
        # keeps the two in sync regardless of what "random" rolls for
        # either option.
        _real_fog_required = max(
            int(self.options.fogometers_cadeaux_required),
            int(self.options.altar_cadeaux_required) * 5)
        self.cadeaux_required_logic = min(
            _real_fog_required,
            len(self.cadeaux_bundle_representatives))

        _CADEAUX_SLACK = 20
        if bool(self.options.cadeauxsanity) and bool(self.options.cadeaux_gated_content):
            _cadeaux_checkable = len(self.cadeaux_bundle_representatives)
            _cadeaux_deficit = (max(0, self.cadeaux_required_logic - _cadeaux_checkable)
                                + _CADEAUX_SLACK)
            for _ in range(_cadeaux_deficit):
                self.multiworld.push_precollected(self.create_item("Cadeaux"))

    # ── Regions ───────────────────────────────────────────────────────────────

    def create_regions(self) -> None:
        create_regions(
            self.multiworld,
            self.player,
            gate_values      = self.gate_values,
            location_factory = ShadowManLocation,
            # Padded (SoulLogicBuffer) copy for rule-building — NOT
            # self.sl_thresholds itself, which stays the real, unpadded
            # in-game requirement used elsewhere (fill_slot_data). See
            # generate_early()'s comment for the full reasoning.
            sl_thresholds    = self.sl_thresholds_logic,
            entrance_shuffle = self.entrance_shuffle,
            piston_combos    = bool(self.options.piston_combos),
            cadeauxsanity    = bool(self.options.cadeauxsanity),
            cadeaux_required = self.cadeaux_required_logic,
            cadeaux_gated_content = bool(self.options.cadeaux_gated_content),
            cadeaux_bundle_representatives = self.cadeaux_bundle_representatives,
            barrel_promoted_locs = self.barrel_promoted_locs,
            unique_retractor_keys = bool(self.options.unique_retractor_keys),
        )

        # "Defeat Legion" -> "Victory" event (2026-07-21). Standard AP
        # event-location/event-item idiom (BaseClasses.Region.add_event) —
        # added after noticing other worlds' spoiler playthroughs end on an
        # explicit goal line (e.g. Majora's Mask Recompiled's "Defeat
        # Majora: Victory"), which Shadow Man had no equivalent of: the
        # completion condition used to be a bare state-check with no
        # location/item backing it, so nothing ever showed up as a final
        # sphere entry. Lives in Asylum: Engine Block, the hub Legion is
        # fought from once all five Engine Blocks are powered. Uses the
        # SAME R.pistons() rule set_rules()'s completion_condition now reads
        # through this event's "Victory" item (state.has("Victory", player))
        # rather than duplicating the piston check independently, so the
        # spoiler-visible event and the actual win condition can't drift
        # apart from each other.
        engine_block = self.multiworld.get_region(ASYLUM_ENGINE_BLOCK, self.player)
        engine_block.add_event(
            "Defeat Legion",
            "Victory",
            rule=lambda state: R.pistons(state, self.player,
                                          require_schematic=bool(self.options.piston_combos)),
            item_type=ShadowManItem,
        )

        # Boss/true_form soul events (2026-07-24). FIXED_SOUL_LOCS entries are
        # NOT real AP locations (they're excluded from CHECKABLE_LOCS/
        # location_table by design — see create_items()'s open_location_count
        # note) and were never actually granted anything: pre_fill()'s
        # get_location()-based locked-item placement silently skips every
        # one of them, so soul-threshold gate logic could never see more
        # than checkable_soul_count souls, capping reachability short of
        # thresholds that need more. Fixed as real per-boss event locations
        # (standard AP idiom, same as "Defeat Legion" above) rather than as
        # precollected items: these are static, unshuffled vanilla pickups —
        # the player earns each one by actually reaching that boss's region
        # and satisfying its own gate_expr, same as any other soul, not
        # something logic should assume from turn 0. Each fires a "Dark
        # Soul" event item once genuinely reachable, so state.count("Dark
        # Soul", player) reflects reality instead of an assumed baseline.
        # Not shown in the spoiler playthrough (show_in_spoiler=False) —
        # these are logic bookkeeping, not a meaningful goal milestone like
        # Legion.
        for _soul_loc in FIXED_SOUL_LOCS:
            _region = self.multiworld.get_region(_soul_loc.level_region, self.player)
            _rule = None
            if _soul_loc.gate_expr:
                _rule = make_location_rule(
                    _soul_loc.gate_expr, self.gate_values, self.player,
                    # Padded (SoulLogicBuffer) copy, same as create_regions()
                    # — see generate_early()'s comment. self.sl_thresholds
                    # itself stays the real, unpadded requirement.
                    self.sl_thresholds_logic, bool(self.options.piston_combos),
                    # Capped AP-logic value (2026-07-27, see generate_early()'s
                    # comment) -- must match create_regions()'s cadeaux_required
                    # exactly, or a fixed soul location whose gate_expr
                    # references R.cadeaux_666() could gate on a different
                    # (uncapped) threshold than every other cadeaux_666()
                    # check in the seed.
                    self.cadeaux_required_logic,
                    bool(self.options.cadeauxsanity),
                )
            _region.add_event(
                f"{_soul_loc.friendly_name} ({_soul_loc.loc_key})",
                "Dark Soul",
                rule=_rule,
                item_type=ShadowManItem,
                show_in_spoiler=False,
            )

    # ── Items ─────────────────────────────────────────────────────────────────

    def _roll_trap_bonus_item_name(self) -> str:
        """
        Rolls ONE concrete Trap/Bonus item name (2026-08-05) -- category
        (secret/health/voodoo/ammo, gated on TrapBonus{Secrets,Health,
        Voodoo,Ammo}Enabled) and, for health/voodoo/ammo, which polarity
        (trap vs bonus) -- using self.random, AP's own per-world seeded
        RNG, at GENERATION time rather than client.py's runtime.

        This is the fix for the AP log/tracker/chat only ever showing a
        generic "Trap/Bonus" regardless of what an item turned out to be:
        that text is fixed at generation (whatever name Fill placed), not
        something client.py can retroactively change once the item is
        actually received in-game. Moving the roll here means the name
        itself IS the answer -- "Trap: Poison" / "Bonus: Ammo Max Hold" /
        etc. -- visible everywhere AP shows an item name, not just in
        client.py's own log.

        "secret" stays a single generic "Secret Effect" name (Jon's call,
        2026-08-05) -- WHICH of the ~17 safe cosmetic secrets a given
        "Secret Effect" copy becomes is still rolled at runtime in
        client.py's _apply_trap_bonus_now, same as before this change,
        since those are neutral/silly cosmetic swaps with no real
        trap-vs-bonus polarity and weren't judged worth 18 more reserved
        item names.

        Falls back to "secret" if every category is somehow disabled
        (e.g. a hand-edited YAML) -- shouldn't normally happen since
        create_items() only calls this when trap_bonus_count > 0, but
        "secret" is the one category with zero game-state risk either
        way (pure write_cvar_bool(), no CreateRemoteThread), so it's the
        safe thing to fall back to rather than raising.
        """
        enabled_categories = []
        if self.options.trap_bonus_secrets_enabled:
            enabled_categories.append("secret")
        if self.options.trap_bonus_health_enabled:
            enabled_categories.append("health")
        if self.options.trap_bonus_voodoo_enabled:
            enabled_categories.append("voodoo")
        if self.options.trap_bonus_ammo_enabled:
            enabled_categories.append("ammo")
        if not enabled_categories:
            enabled_categories = ["secret"]

        category = self.random.choice(enabled_categories)
        if category == "secret":
            return "Secret Effect"
        if category == "health":
            return "Trap: Poison" if self.random.choice([True, False]) else "Bonus: Recovery"
        if category == "voodoo":
            return "Trap: Voodoo Drain" if self.random.choice([True, False]) else "Bonus: Voodoo Max Hold"
        # category == "ammo"
        return "Trap: Ammo Drain" if self.random.choice([True, False]) else "Bonus: Ammo Max Hold"

    def create_items(self) -> None:
        pool = []

        # One of every unique item
        #
        # BUG FIX (2026-07-28, Jon's report -- "Player shadowman had 25
        # more items than locations"): unique_skip only ever excluded the
        # literal name "Cadeaux", but item_table also reserves 49
        # "Cadeaux Bundle x{N}" names (N=2..50, see items.py's
        # CADEAUX_MAX_BUNDLE / the static-superset reservation pattern for
        # this feature) that were never added here. Every one of those 49
        # names leaked through this "one of every unique item" loop and
        # got created unconditionally, once each, EVERY generation --
        # regardless of Cadeauxsanity or cadeaux_bundle_size -- on top of
        # whatever the dedicated cadeaux_bundle_representatives-driven
        # loop below legitimately creates. open_location_count's math
        # only ever budgets for the dedicated loop's output (one item per
        # real bundle representative), so these 49 phantom items were
        # pure, unaccounted-for surplus. Confirmed via a standalone diag
        # script run against item_table directly: 49 "Cadeaux Bundle x*"
        # names were NOT covered by unique_skip. Fixed by checking
        # is_cadeaux_item() instead of a fixed literal-name set, so the
        # WHOLE Cadeaux family (not just plain "Cadeaux") is excluded from
        # this blanket loop and left entirely to the dedicated loop below.
        unique_skip = {"Dark Soul", "Retractor", "Accumulator", "Gad Power",
                       "Jacks Schematic", *RETRACTOR_KEY_ITEM_NAMES.values()}
        for name, data in item_table.items():
            if name in unique_skip or is_cadeaux_item(name):
                continue
            pool.append(self.create_item(name))

        # Jack's Schematic — classification depends on PistonCombos (see
        # options.py's docstring / access_rules.py's pistons()). Off
        # (default): stays useful, item_table's normal value, same as before
        # this option existed. On: becomes progression — the piston
        # combinations are randomized per-seed and only readable via this
        # item's in-game journal entry, and set_rules() below requires
        # holding it to satisfy the completion condition, so AP's fill must
        # guarantee it lands somewhere reachable before Legion.
        _schematic_data = item_table["Jacks Schematic"]
        _schematic_cls = (ItemClassification.progression if self.options.piston_combos
                          else _schematic_data.classification)
        pool.append(ShadowManItem("Jacks Schematic", _schematic_cls,
                                  _schematic_data.code, self.player))

        # Stackable items — fixed counts from STACKABLE_COUNTS
        #
        # Unique Retractor Keys (2026-08-18): when the option is on, the 5
        # single-copy named items (each with a fixed region identity — see
        # items.py's RETRACTOR_KEY_ITEM_NAMES) replace the flat fungible
        # "Retractor" x5 stack entirely, mirroring regions.py's matching
        # branch in the liveside entrance rules built by create_regions().
        # Mutually exclusive by design: a seed is either flat-count mode or
        # named-key mode, never both, so the item pool always contains
        # exactly 5 retractor-family progression items either way.
        if bool(self.options.unique_retractor_keys):
            for _name in RETRACTOR_KEY_ITEM_NAMES.values():
                pool.append(self.create_item(_name))
        else:
            for _ in range(STACKABLE_COUNTS["Retractor"]):
                pool.append(self.create_item("Retractor"))
        for _ in range(STACKABLE_COUNTS["Accumulator"]):
            pool.append(self.create_item("Accumulator"))
        for _ in range(STACKABLE_COUNTS["Gad Power"]):
            pool.append(self.create_item("Gad Power"))

        # One Dark Soul per checkable soul slot.
        #
        # Only a subset is classified progression: AP logic needs at most
        # `need` souls (the largest gate threshold this seed actually uses,
        # floored at SL2 for liveside access), plus a placement margin.
        # The rest are `useful` — in-game ANY soul raises the counter, so
        # logic that relies on a fixed subset is strictly conservative.
        # Without this, 100 progression souls against thresholds that consume
        # nearly the whole supply wedge AP's assumed fill (~35% of hard seeds
        # failed generation before this change).
        checkable_soul_count = sum(
            1 for loc in CHECKABLE_LOCS
            if loc.category == "soul"
        )
        # BUG FIX (2026-08-09, Jon's report: Dark Soul sometimes missing
        # entirely from the sphere/progression breakdown): `need` used to be
        # computed from the fixed vanilla _SOUL_THRESHOLDS table regardless
        # of soul_threshold_mode, instead of this seed's own resolved
        # self.sl_thresholds (set above in generate_early(), and the same
        # table access_rules.py's real gate-check rules are built against —
        # see _soul_level()'s _soul_thresholds parameter). Confirmed via
        # simulation this was never an edge case: under "balanced" mode the
        # real per-SL threshold exceeded the vanilla value used here at
        # SL2-SL9 in 100% of 3000 simulated seeds (by as much as +45
        # souls), and "progressive" exceeded it in 20-83% of seeds
        # depending on SL. Whenever the real threshold for max_sl_used
        # exceeds what this formula assumed, `need` — and therefore
        # prog_in_pool below — comes out too small: too few Dark Soul
        # copies get created as real `progression` items for AP's own
        # state.count("Dark Soul") to EVER reach the seed's real, patched
        # threshold, since only progression-classified copies count toward
        # state tracking at all (`useful` copies are picked up in real
        # gameplay but never call state.add_item() — see collect_item()'s
        # own 2026-07-24 comment). That's an unsatisfiable access rule
        # regardless of how Fill places anything — a direct mechanism for
        # both "Dark Soul never shows in the sphere breakdown" and outright
        # generation failures, on top of (not a replacement for) the
        # separate SL9-top-gap and SoulLogicBuffer fixes made earlier.
        max_sl_used  = max(self.gate_values.values(), default=0)
        need         = max(self.sl_thresholds[max_sl_used], self.sl_thresholds[2])
        locked_souls = len(FIXED_SOUL_LOCS)
        prog_total   = min(checkable_soul_count + locked_souls, need + 20)
        prog_in_pool = max(0, prog_total - locked_souls)

        # NOTE (2026-07-24): the math above has always assumed these
        # `locked_souls` (boss/true_form kills) count toward soul-threshold
        # gate logic — access_rules.py's _soul_level() checks
        # state.count("Dark Soul", player), and pre_fill()'s own docstring
        # says these are meant to "count toward reachability." They're
        # granted correctly now via real per-boss event locations, gated by
        # each boss's own region/gate_expr (added in create_regions() below)
        # — NOT as precollected items. These are static, unshuffled vanilla
        # pickups the player reaches through normal progression, same as any
        # other soul; precollecting them would incorrectly make gate logic
        # assume the player already has them from turn 0, before actually
        # reaching or defeating anything.
        for i in range(checkable_soul_count):
            if i < prog_in_pool:
                pool.append(self.create_item("Dark Soul"))
            else:
                data = item_table["Dark Soul"]
                pool.append(ShadowManItem(
                    "Dark Soul", ItemClassification.useful, data.code, self.player))

        # Pad with Cadeaux filler so item count matches open location count.
        # Open locations = all location_table entries minus excluded-cadeaux
        # locations (when cadeauxsanity is off), which never become AP locations
        # at all in that case.
        #
        # BUG FIX (2026-07-24): this used to also subtract len(FIXED_SOUL_LOCS)
        # here, on the assumption that boss/true_form locations become real
        # entries in location_table and then get "removed" from the fillable
        # set via pre_fill()'s locked-item placement. They don't -- location_table
        # is built from CHECKABLE_LOCS only (see locations.py), which filters to
        # placement-relevant categories and never includes "boss"/"true_form" —
        # so FIXED_SOUL_LOCS's 20 entries were never counted in len(location_table)
        # to begin with. Subtracting them again silently starved the item pool
        # by exactly len(FIXED_SOUL_LOCS) (20), which nothing surfaced until
        # pre_fill()'s separate KeyError crash (fixed the same day) was resolved
        # and generation finally reached the actual Fill step: "Player shadowman
        # had 20 more locations than items" / FillError. This is the same class
        # of dead-assumption bug as pre_fill()'s "if loc is None" and the
        # _soul_idx_map_cache's "loc_key not in fixed_keys" check just above —
        # multiple places in this file assumed FIXED_SOUL_LOCS entries overlap
        # location_table when they never do; only this one actually broke
        # anything observable, since the others were harmless no-ops.
        #
        # Cadeauxsanity (renamed from "Insanity" 2026-08-23) gates whether
        # cadeaux-category locations are AP locations AT ALL (2026-07-21 —
        # see regions.py's _SKIP_CATS comment; off now means excluded
        # entirely, same as barrel, not "AP checks restricted to
        # Cadeaux-only items" like the 2026-07-20 -> 2026-07-21-morning
        # design). So the "Cadeaux" item and its dedicated supply-matching
        # math below only apply when cadeauxsanity is on:
        #   - off: no cadeaux locations exist for this player at all, so no
        #     "Cadeaux" item is needed either — open_location_count is
        #     reduced by cadeaux_checkable_count to match regions.py
        #     excluding those same locations from the region graph.
        #   - on: cadeaux locations exist with no item-type restriction (no
        #     item_rule at all anymore — see set_rules()), so the old "tight
        #     1:1 bipartite match" reasoning no longer strictly applies, but
        #     keeping "Cadeaux" filler sized to the cadeaux location count is
        #     still a reasonable amount of that item type to have in the
        #     pool (it's ordinary filler now, free to land anywhere, same as
        #     the extra useful-classified Dark Souls below).
        # Cadeaux Bundle Size (2026-07-27): cadeaux_checkable_count now means
        # "how many cadeaux rows are REAL AP locations for this seed", not
        # "how many cadeaux rows exist at all" -- those differ once bundling
        # groups multiple cadeaux into one representative check. total_
        # cadeaux_in_table is the old, unconditional count (every cadeaux
        # row reserved in location_table, matching what open_location_count
        # below starts at); cadeaux_checkable_count is the bundled-down
        # figure, computed once in generate_early() via
        # self.cadeaux_bundle_representatives so this always agrees with
        # what regions.py's create_regions() actually instantiated. At the
        # default bundle size of 1 these two counts are identical (no
        # bundling), so every branch below behaves exactly as before this
        # option existed.
        cadeauxsanity = bool(self.options.cadeauxsanity)
        total_cadeaux_in_table = sum(
            1 for loc in CHECKABLE_LOCS
            if loc.category == "cadeaux"
        )
        cadeaux_checkable_count = len(self.cadeaux_bundle_representatives) if cadeauxsanity else 0
        if cadeauxsanity:
            # Cadeaux Bundle Size (2026-07-27, Jon's report -- a foreign
            # Cadeaux item only granted 1 instead of the expected bundle
            # amount): one item per representative, named for that
            # representative's ACTUAL weight (cadeaux_bundle_representatives
            # maps loc_key -> true chunk size, not a flat bundle_size --
            # see regions.py's compute_cadeaux_bundle_representatives() for
            # why remainder chunks get their own honest size). At the
            # default bundle size of 1 every weight is 1, so
            # cadeaux_bundle_item_name(1) == "Cadeaux" and this is
            # byte-for-byte the same pool as before this option existed.
            for _weight in self.cadeaux_bundle_representatives.values():
                pool.append(self.create_item(cadeaux_bundle_item_name(_weight)))

        # CadeauxGatedContent (2026-07-24): off (default) excludes the
        # Fogometers Light Soul location from the region graph the same way
        # regions.py's _build_sub_regions() does (fill.py's CADEAUX_666_LOCS)
        # — location_table itself still contains it unconditionally (it's a
        # module-level dict, built without knowledge of any per-world
        # option), so open_location_count must be adjusted here or fill ends
        # up with one more location than item (same class of bug as the
        # cadeauxsanity/cadeaux_checkable_count case above and the FIXED_SOUL_LOCS
        # bug fixed the same day).
        cadeaux_gated_content = bool(self.options.cadeaux_gated_content)
        cadeaux_666_loc_count = sum(
            1 for k in CADEAUX_666_LOCS if k in location_table
        )

        # location_table reserves one ID per cadeaux row unconditionally
        # (total_cadeaux_in_table), regardless of cadeauxsanity/bundling -- so
        # the gap between that static reservation and however many are
        # ACTUALLY real locations this seed (cadeaux_checkable_count, 0
        # when cadeauxsanity is off) always needs subtracting here. Previously
        # this was an `if not cadeauxsanity` branch since bundling didn't exist
        # yet and cadeauxsanity=True meant "all of them are real" by
        # definition; now cadeauxsanity=True + bundle_size>1 also leaves a gap,
        # so the subtraction has to run unconditionally using the actual
        # counts rather than being gated on cadeauxsanity itself.
        # Trap/Bonus barrel promotion (2026-08-01): locations.py now
        # reserves an AP location ID for every "barrel" category row
        # unconditionally (mirrors how cadeaux rows are always reserved
        # regardless of Cadeauxsanity — see that file's _SKIP_CATS comment), but
        # only the specific loc_keys in self.barrel_promoted_locs actually
        # become real, connected Locations in the region graph (regions.py's
        # create_regions()). Every OTHER barrel row's reserved ID needs the
        # exact same "leaks into len(location_table) but was never
        # instantiated" subtraction cadeaux excess/bundling already gets —
        # same reasoning, same bug class if skipped (items-must-match-
        # open-locations invariant).
        total_barrel_in_table = sum(
            1 for loc in CHECKABLE_LOCS if loc.category == "barrel")

        open_location_count = len(location_table)
        open_location_count -= (total_cadeaux_in_table - cadeaux_checkable_count)
        open_location_count -= (total_barrel_in_table - len(self.barrel_promoted_locs))
        if not cadeaux_gated_content:
            open_location_count -= cadeaux_666_loc_count

        # Trap/Bonus (2026-08-01, renamed from "Secret Trap" 2026-08-03):
        # one item per promoted barrel location (self.barrel_promoted_locs,
        # computed once in generate_early() — see its comment) rather than
        # displacing existing Dark Soul padding. Using
        # len(self.barrel_promoted_locs) directly here (not re-deriving
        # from self.options.trap_bonus_count) guarantees this always
        # matches exactly how many new locations create_regions() actually
        # added, even in the edge case where trap_bonus_count exceeded the
        # number of available verified barrel candidates and
        # generate_early() had to cap it — no location-category
        # restriction on where these can land (see generate_output()'s
        # dedicated branch, always a generic Book of Shadows marker), so
        # they're free to land on any open location, not specifically the
        # promoted barrels themselves.
        for _ in range(len(self.barrel_promoted_locs)):
            # 2026-08-05: each copy's concrete name (category + polarity)
            # is now rolled here, once, at generation time -- see
            # _roll_trap_bonus_item_name()'s own docstring for why.
            pool.append(self.create_item(self._roll_trap_bonus_item_name()))

        while len(pool) < open_location_count:
            data = item_table["Dark Soul"]
            pool.append(ShadowManItem(
                "Dark Soul", ItemClassification.useful, data.code, self.player))

        self.multiworld.itempool += pool

    def create_item(self, name: str) -> ShadowManItem:
        data = item_table[name]
        return ShadowManItem(name, data.classification, data.code, self.player)

    def collect_item(self, state: "CollectionState", item, remove: bool = False):
        """
        BUG FIX (2026-07-24): "Cadeaux" is ItemClassification.filler (see
        items.py) — items.py/AutoWorld.py's base World.collect_item() only
        ever returns a name (which state.add_item()/state.count() then
        tracks) when item.advancement is True, i.e. only for `progression`
        classified items. filler items are silently dropped: no matter how
        many Cadeaux exist or get collected, state.count("Cadeaux", player)
        was permanently stuck at 0. This made R.cadeaux_666() (and thus the
        Fogometers Light Soul location gated behind it — see
        CadeauxGatedContent in options.py) impossible to ever satisfy,
        independent of supply/precollection math. Confirmed via a live
        Generate.py repro instrumented to print state.count("Cadeaux", ...)
        at each rule evaluation — it read 0 every single time until this
        override was added.

        Cadeaux is exactly the kind of "currency" item World.collect()'s own
        docstring calls out ("Useful for things such as progressive items or
        currency") — always counting it here, regardless of classification,
        is the standard AP pattern for that case. Everything else falls
        through to the default behavior unchanged.

        Cadeaux Bundle Size (2026-07-27): extended from a literal
        `item.name == "Cadeaux"` check to is_cadeaux_item(), which also
        matches every "Cadeaux Bundle x{N}" denomination (see items.py) --
        those need the exact same override for the exact same reason,
        state.count_group("Cadeaux", player) (see access_rules.py's
        cadeaux_666(), updated the same day to sum across the whole
        denomination family) would stay stuck at 0 for them otherwise.
        """
        if is_cadeaux_item(item.name):
            return item.name
        return super().collect_item(state, item, remove)

    # ── Dark Soul identity assignment ─────────────────────────────────────────

    def _soul_identity_map(self):
        """
        Returns (assignment, donor_neutralize):
          assignment:        loc_key (any category) holding one of our own
                              "Dark Soul" items → the save_idx it should
                              physically carry.
          donor_neutralize:  set of NATIVE soul-slot loc_keys whose own
                              identity got lent out to some OTHER Dark Soul
                              placement in `assignment` — generate_output()
                              must force THEIR OWN progression_placement
                              entry's save_idx to 0 regardless of what item
                              ended up there, or the donor's still-live
                              physical record keeps claiming the same
                              tracked index it just lent away.

        The engine's persistent dark-soul flag array (client.py
        DARKSOUL_FLAGARRAY_PTR_RVA) is indexed by the collected soul's
        save_idx — a per-level identity, not tied to any particular slot.
        AP items are fungible ("Dark Soul" × N), so a placed Dark Soul has
        no identity of its own by default — this assigns each one a real,
        unique native soul identity (mirroring the standalone randomizer,
        where a placed soul always keeps its own genuine save_idx) via a
        clean bijection:
          - Pass 1: a Dark Soul that landed on its OWN native soul slot
            keeps that slot's own identity (a no-op, matches vanilla).
          - Pass 2: a Dark Soul on a non-soul slot borrows the identity of
            some OTHER native soul slot whose own placement ISN'T itself a
            Dark Soul (so nothing else needs that identity) — deterministic
            (sorted loc_key ↔ sorted donor loc_key), no RNG needed.

        Jon's design call (2026-07-25): unlike the two previous attempts at
        this (see git history / __init__.py comment archaeology if curious
        — one lent an identity without neutralizing its donor, one tried to
        keep a Dark Soul's OWN native save_idx and only zero it out on a
        detected collision), this version always actively OVERWRITES the
        donor's own physical save_idx to 0 rather than leaving it alone or
        only reacting to known collisions. Reasoning: the donor's own item
        (whatever it is — it's not itself a Dark Soul, since Pass 1 already
        claimed those) never needed its own native save_idx to be
        meaningful in the first place, since cadeaux/barrel/key-item
        tracking doesn't depend on it — see _cadeaux_identity_map's
        docstring for the same reasoning applied to cadeaux donors. Forcing
        it to 0 makes the donor's own identity unambiguously safe (0 is a
        sentinel the client's (level, save_idx) lookup never registers
        anything against) instead of leaving a stale, still-claimed number
        lying around for some future placement to coincidentally collide
        with again.

        Fixed boss/true_form locations (FIXED_SOUL_LOCS) are excluded from
        everything here and keep their raw save_idx untouched.

        Used by BOTH generate_output (patched into the physical govi as
        its reward id, and to neutralize donors) and fill_slot_data
        (location_map instance_id override) — cached so all three agree.
        """
        cached = getattr(self, "_soul_idx_map_cache", None)
        if cached is not None:
            return cached

        fixed_keys = {ld.loc_key for ld in FIXED_SOUL_LOCS}

        # Identity pool: the save_idx of every shuffled soul slot.
        identity_pool: Dict[str, int] = {}
        for loc_key, loc_data in location_table.items():
            raw = loc_data.raw
            if (loc_key not in fixed_keys
                    and getattr(raw, "category", None) == "soul"
                    and getattr(raw, "save_idx", 0)):
                identity_pool[loc_key] = int(raw.save_idx)

        # BUG FIX (2026-07-24): the boss/true_form soul events added in
        # create_regions() (BaseClasses.Region.add_event()) create plain
        # Location instances, not ShadowManLocation -- they have no `.raw`
        # attribute at all. Those events' item is also named "Dark Soul" and
        # belongs to this player, so without the hasattr() guard below this
        # generator crashed with AttributeError: 'Location' object has no
        # attribute 'raw' as soon as fill_slot_data()/generate_output() ran
        # (found via a live Generate.py repro, past the earlier
        # balance_multiworld_progression/accessibility fixes). Real soul
        # locations are always ShadowManLocation and always have `.raw`, so
        # this only filters out the event locations, which is exactly right
        # — they were never real shuffled slots to begin with.
        placed = sorted(
            loc.raw.loc_key for loc in self.multiworld.get_locations(self.player)
            if loc.item is not None
            and loc.item.player == self.player
            and loc.item.name == "Dark Soul"
            and hasattr(loc, "raw")
            and loc.raw.loc_key in location_table
            and loc.raw.loc_key not in fixed_keys
        )

        assignment: Dict[str, int] = {}
        # Pass 1: a Dark Soul on its OWN native soul slot keeps that slot's
        # own identity — this is always safe (matches vanilla, no reassignment
        # happening at all) and is the fast path: the client's live
        # DARKSOUL_FLAGARRAY watcher resolves these instantly via
        # (level, save_idx), no save-file dependency.
        for loc_key in placed:
            own = identity_pool.get(loc_key)
            if own is not None:
                assignment[loc_key] = own

        # Pass 2 (2026-07-25, Jon's design — third iteration of this logic;
        # see the two prior, both wrong, attempts in git history/PR
        # discussion if curious): a Dark Soul on a non-soul slot borrows the
        # identity of some OTHER native soul slot not already claimed in
        # Pass 1 above (i.e. a native soul slot whose OWN item isn't itself
        # a Dark Soul). Deterministic, one-to-one: sorted leftover loc_keys
        # zipped against sorted donor loc_keys, so no two Dark Souls can
        # ever be assigned the same identity.
        used_donors = set(assignment.keys())  # Pass-1 loc_keys ARE their own donor
        donor_candidates = sorted(lk for lk in identity_pool if lk not in used_donors)
        leftover = [lk for lk in placed if lk not in assignment]

        donor_neutralize: set = set()
        for loc_key, donor_loc_key in zip(leftover, donor_candidates):
            assignment[loc_key] = identity_pool[donor_loc_key]
            donor_neutralize.add(donor_loc_key)

        if len(leftover) > len(donor_candidates):
            # More placed Dark Souls than spare native identities to borrow
            # from -- shouldn't happen (Dark Soul count == shuffled soul
            # slot count, and souls exported to other worlds only shrink
            # `placed`), but never fail generation over tracking metadata.
            # Falls back to save_idx=0 (always safe -- same sentinel used
            # for foreign-item markers and neutralized donors below) for
            # whichever ones didn't get a real identity.
            for loc_key in leftover[len(donor_candidates):]:
                assignment[loc_key] = 0
            print(f"  [WARN] {len(leftover) - len(donor_candidates)} placed "
                  f"Dark Soul(s) had no free soul identity to borrow — "
                  f"falling back to save_idx=0 (save-file position scan) "
                  f"for those.")

        result = (assignment, donor_neutralize)
        self._soul_idx_map_cache = result
        return result

    # ── Cadeaux identity assignment ───────────────────────────────────────────

    def _cadeaux_identity_map(self):
        """
        Returns (assignment, barrel_names):
          assignment:   loc_key of each AP location holding one of OUR OWN
                        "Cadeaux"-family filler items → the source cadeaux
                        RawLocation donating its physical identity (real
                        cadeaux RSC name + save_idx).
          barrel_names: donor loc_key → plain-barrel RSC name used to
                        backfill the donor's now-vacated slot.
          leftover_barrel_names (2026-07-27, Cadeaux Bundle Size): every
                        non-representative cadeaux loc_key NOT claimed
                        above as a demand-driven donor → plain-barrel RSC
                        name. These have no "recipient" needing their
                        identity; they just need to stop being live
                        vanilla cadeaux, since a bundle's whole value now
                        lives in its one "Cadeaux Bundle x{weight}" item
                        instead of being spread across its physical
                        pickups. generate_output() writes these directly,
                        since non-representative rows are never real AP
                        Locations and so are never visited by its main
                        per-Location loop.

        Mirrors the standalone's cadeaux displacement: a cadeaux moving to a
        new slot carries its RSC name and save_idx with it, and the slot it
        left stops being a cadeaux (here: becomes a plain barrel, save_idx=0).
        Physical cadeaux total is preserved exactly — one object leaves a
        cadeaux slot for every real cadeaux written at an AP location — so
        the engine's levels.txt-validated economy stays intact, and
        levels_txt_patcher's per-level $cadeaux recount (driven by
        category=="cadeaux" on these synthetic source entries) stays honest.

        Cadeaux slots are AP locations only when Cadeauxsanity (renamed
        from "Insanity" 2026-08-23) is on (2026-07-21 — see regions.py's
        _SKIP_CATS; off excludes them from the AP location pool entirely,
        same as barrel, and create_items() doesn't create any "Cadeaux"
        item in that case either — see its docstring). So this method is
        only ever non-empty under cadeauxsanity: no "Cadeaux" item exists
        in the pool at all when it's off, so `placed` below is naturally
        empty and this is a no-op.
        Under cadeauxsanity, a Cadeaux item usually lands directly on a native
        cadeaux location anyway (see generate_output()'s
        raw_loc.raw.category == "cadeaux" fast path); this donor map only
        matters for the cases where AP's fill put one somewhere else
        instead (no item_rule restricts that — see set_rules()).

        donor_pool below excludes cadeaux rows whose OWN AP location ended
        up holding a native "Cadeaux" item — that row's save_idx is
        already in use representing itself, so donating it elsewhere would
        double-write the same save_idx to two physical records. A cadeaux
        row whose own location got a DIFFERENT item (e.g. a key item under
        cadeauxsanity) is fair game: its physical slot no longer represents a
        cadeaux at all, so its identity is genuinely free to lend out.
        Cached so generate_output and fill_slot_data always agree.
        """
        cached = getattr(self, "_cadeaux_map_cache", None)
        if cached is not None:
            return cached

        from .fill import CHECKABLE_LOCS as _CHK

        # BUG FIX (2026-07-28, Jon's report — a self-found "Cadeaux Bundle
        # x10" placed on a Govi/Dark-Soul slot in Deadside Marrow Gates
        # never got a donor identity, fell through generate_output()'s
        # "no donor available" fallback to a plain RSC_X_BOOK_OF_SHADOWS
        # retype, and a nearby unrelated cadeaux location — "Cadeaux - Pot
        # 31" — recorded a check it shouldn't have on the next save).
        # These two filters were still the literal `item.name == "Cadeaux"`
        # comparison left over from before Cadeaux Bundle Size existed —
        # every other cadeaux-family touchpoint in this file
        # (collect_item(), generate_output()'s main branch) was already
        # switched to is_cadeaux_item() on 2026-07-27 specifically so
        # "Cadeaux Bundle x{N}" denominations get the same treatment as
        # plain "Cadeaux", but these two were missed. Concretely: a bundle
        # item landing on a non-cadeaux location (e.g. a Govi slot) was
        # invisible to `placed`, so `_cad_map.get(loc_key)` in
        # generate_output() always returned None for it, and that location
        # never received a real donor cadeaux identity — instead of being
        # physically converted into a genuine cadeaux pickup, it stayed
        # whatever it structurally already was (here: a Govi/AIGovi actor)
        # while only visually retyped to a Book-of-Shadows marker. Picking
        # it up still flips a GOVI state byte, not a QuestObject one, which
        # the save-file Govi position-scan (client.py's "ALWAYS runs"
        # block) then has to resolve against every candidate in the same
        # level — including nearby genuine cadeaux locations like Pot 31 —
        # instead of correctly resolving to its own donor-assigned identity.
        # Switching both filters to is_cadeaux_item() closes that gap: the
        # bundle item now gets a real donor cadeaux identity/save_idx like
        # any other displaced cadeaux-family item.
        self_cadeaux_loc_names = {
            loc.raw.loc_key for loc in self.multiworld.get_locations(self.player)
            if loc.item is not None
            and loc.item.player == self.player
            and is_cadeaux_item(loc.item.name)
        }

        # Only locations that actually need a donor: holding a Cadeaux(-family)
        # item but NOT themselves a native cadeaux slot (those are handled by
        # generate_output()'s raw_loc.raw.category == "cadeaux" fast path
        # and never consult this map — including them here would waste
        # donor supply on placements that don't need one).
        placed = sorted(
            loc.raw.loc_key for loc in self.multiworld.get_locations(self.player)
            if loc.item is not None
            and loc.item.player == self.player
            and is_cadeaux_item(loc.item.name)
            and loc.raw.loc_key in location_table
            and location_table[loc.raw.loc_key].raw.category != "cadeaux"
        )
        # Cadeaux Bundle Size (2026-07-27, Jon's call reversed from the
        # earlier "leave them untouched" plan): non-representative cadeaux
        # rows are exactly the right supply to draw identity-donors from --
        # they have no other purpose now (never real AP locations, never
        # hold an item), and every one of them needs to stop being a live
        # vanilla cadeaux pickup anyway, since a bundle's ENTIRE value now
        # lives in its single "Cadeaux Bundle x{weight}" item (see items.py)
        # rather than being spread across the bundle's physical pickups --
        # leaving them as untouched vanilla cadeaux would let each one keep
        # independently granting +1 to the live count, double-crediting
        # value the bundle's item already accounts for. donor_pool is every
        # non-representative cadeaux row; self_cadeaux_loc_names is kept as
        # a guard for parity with the pre-bundling logic even though it can
        # never actually match here (non-representative rows are never real
        # Locations, so they can never appear in that set).
        donor_pool = sorted(
            (l for l in _CHK
             if l.category == "cadeaux"
             and l.loc_key not in self_cadeaux_loc_names
             and l.loc_key not in self.cadeaux_bundle_representatives),
            key=lambda l: l.loc_key,
        )

        # Weighted plain-barrel pool (mirrors standalone fill.py
        # _PLAIN_BARREL_POOL, weights = vanilla frequency).
        _bnames   = ["RSC_X_BARREL_A", "RSC_X_BARREL_D", "RSC_X_BARREL_L",
                     "RSC_X_BARREL", "RSC_TE_PACKBOX1", "RSC_FL_CRATE",
                     "RSC_UN_CRATES", "RSC_TE_PACKBOX2"]
        _bweights = [1112, 804, 432, 111, 121, 62, 25, 14]

        assignment: Dict[str, Any] = {}
        barrel_names: Dict[str, str] = {}
        donor_keys_used: set[str] = set()
        if placed and donor_pool:
            n = min(len(placed), len(donor_pool))
            donors = self.random.sample(donor_pool, n)
            for loc_key, donor in zip(placed, donors):
                assignment[loc_key] = donor
                barrel_names[donor.loc_key] = self.random.choices(
                    _bnames, weights=_bweights, k=1)[0]
                donor_keys_used.add(donor.loc_key)
        if len(placed) > len(assignment):
            print(f"  [WARN] {len(placed) - len(assignment)} placed Cadeaux "
                  f"had no donor identity — those become AP markers instead.")

        # Cadeaux Bundle Size (2026-07-27): every non-representative row NOT
        # already claimed above as a demand-driven identity donor still
        # needs to become a barrel -- there's no "recipient" location
        # demanding its identity, it just needs to stop being a live
        # cadeaux. generate_output() writes these directly into
        # progression_placement (they're never visited by its main
        # per-Location loop, since they're never real Locations at all).
        leftover_barrel_names: Dict[str, str] = {
            l.loc_key: self.random.choices(_bnames, weights=_bweights, k=1)[0]
            for l in donor_pool if l.loc_key not in donor_keys_used
        }

        self._cadeaux_map_cache = (assignment, barrel_names, leftover_barrel_names)
        return self._cadeaux_map_cache

    # ── Pre-fill ──────────────────────────────────────────────────────────────

    def pre_fill(self) -> None:
        """
        Boss and true_form locations are auto-collected fixed checks.
        Lock a Dark Soul into each so AP's fill ignores them for placement
        but still counts them toward reachability.
        """
        # get_location() raises KeyError for an unregistered location name --
        # it does NOT return None -- so the previous "if loc is None: continue"
        # here never actually caught anything; any FIXED_SOUL_LOCS entry that
        # isn't a real Location in this world's region graph (e.g. an
        # is_verified=False/phantom boss-soul slot) crashed pre_fill outright.
        # Bug found 2026-07-24 via 'tenement:enemies.rsc:0x00BC' KeyError.
        location_cache = self.multiworld.regions.location_cache.get(self.player, {})
        for loc_data in FIXED_SOUL_LOCS:
            if loc_data.loc_key not in location_cache:
                continue
            loc = self.multiworld.get_location(loc_data.loc_key, self.player)
            loc.place_locked_item(self.create_item("Dark Soul"))

    # ── Rules ─────────────────────────────────────────────────────────────────

    def set_rules(self) -> None:
        """
        Location entrance/gate rules are set during create_regions() via
        _build_sub_regions(). This method sets the completion condition.

        Completion condition reads through the "Defeat Legion" -> "Victory"
        event (added in create_regions(), see its comment) rather than
        calling R.pistons() a second time independently — that event's own
        access_rule is the actual R.pistons() check, so this and the
        spoiler-visible event can never disagree with each other.

        No cadeaux item_rule lives here anymore (removed 2026-07-21). It
        used to matter because cadeaux locations were unconditionally real
        AP locations, with cadeauxsanity only toggling whether they accepted
        any item or just our own "Cadeaux" filler — that restriction needed
        an explicit item_rule since AP's core Fill has no per-location
        item-type restriction of its own. Now cadeauxsanity gates whether
        cadeaux locations exist as AP locations AT ALL (see regions.py's
        _SKIP_CATS / create_items()'s pool-size math): off means they're
        excluded entirely (same as barrel — no item_rule needed, there's
        nothing to restrict), on means they're included with no
        restriction (which is exactly what "cadeauxsanity=True lifts the
        restriction completely" already meant before). Either way, no
        item_rule is the correct state — see git history if you need the
        old "tight 1:1 bipartite match" reasoning (108-stranded-Cadeaux-item
        generation failure that motivated it in the first place).
        """
        self.multiworld.completion_condition[self.player] = (
            lambda state: state.has("Victory", self.player)
        )

        # LIVESIDE / ENGINE BLOCK CIRCULARITY GUARDS (2026-08-07, Jon's
        # report, generalized the same day once Jon walked through the
        # full set of liveside->Engine-Block exit requirements and asked
        # whether the same gap applies to them too — it does).
        #
        # Every one of these gates is a plain state.has()/state.count()
        # check with no region-topology awareness of its own, so nothing
        # stops Fill from placing the gating item on a location that is
        # itself downstream of that same gate — UNLESS the count-based
        # ones (Retractor >=5, Gad Power/gad3_swim >=3) happen to have
        # exactly that many total copies in the pool, in which case AP's
        # *restrictive* progression-fill pass is naturally safe (it
        # always assumes one fewer copy than the total when deciding
        # where the item being placed can go, so the threshold can never
        # be met inside its own gated region). That self-protection does
        # NOT cover: (a) AP's later, more permissive useful/filler pass,
        # and (b) trap_bonus_count's barrel promotion (2026-08-01), which
        # can throw 100+ brand-new, completely unrestricted locations
        # into these exact regions (the Engine Block hub + its 5
        # per-world rooms alone carry ~226 barrel candidates) where only
        # a handful of native checks existed before. Single-copy items
        # (the 3 Eclipser parts, Poigne, Prison Key Card, Engineers Key)
        # have no such self-protection at all, from either fill pass.
        #
        # A live seed (2026-08-07, Jon's report) got permanently stuck at
        # 81% shadowman reachability with "Defeat Legion" unreachable
        # even after progression balancing front-loaded every other
        # required item early — consistent with a Retractor (the first
        # one checked) having ended up placed inside one of these regions
        # via one of the less-guarded paths above.
        #
        # Exact requirements, cross-checked directly against regions.py's
        # own connection rules (not assumed) before writing this:
        #   - Reaching ANY liveside region (Florida/London/Prison/Queens/
        #     Salvage) at all needs SL2 + all 5 Retractors — so Retractor
        #     must be excluded from the liveside regions too, not just
        #     their Engine Block siblings.
        #   - Liveside -> its Engine Block room additionally needs
        #     "Night" == holding all 3 Eclipser parts (access_rules.py's
        #     _night()) for every one of the 5 rooms.
        #   - Prison's room additionally needs Prison Key Card; Queens'
        #     (tenement's) room additionally needs Poigne; Salvage's room
        #     additionally needs gad3_swim (Gad Power count >= 3, and
        #     exactly 3 total exist — same self-protecting math as
        #     Retractor, same defense-in-depth reasoning applied anyway).
        #     London and Florida need Night only, nothing extra.
        #   - The Engine Block HUB itself (Cageways -> hub) needs
        #     Engineers Key, independent of Night/liveside entirely, and
        #     every per-world room sits downstream of that same hub — so
        #     Engineers Key must be excluded from the hub AND all 5 rooms.
        #
        # Composed with whatever item_rule a location already carries
        # (none, anywhere, today — see this method's own docstring above
        # — but this stays correct if that ever changes) rather than
        # overwriting it outright.
        _engine_block_subregions = frozenset({
            ASYLUM_ENGINE_BLOCK_LONDON,
            ASYLUM_ENGINE_BLOCK_PRISON,
            ASYLUM_ENGINE_BLOCK_FLORIDA,
            ASYLUM_ENGINE_BLOCK_SALVAGE,
            ASYLUM_ENGINE_BLOCK_QUEENS,
        })
        _region_item_bans: dict[str, frozenset[str]] = {
            "La Lune":         _engine_block_subregions,   # Eclipser 1 (Night)
            "La Lame":         _engine_block_subregions,   # Eclipser 2 (Night)
            "Le Soleil":       _engine_block_subregions,   # Eclipser 3 (Night)
            "Prison Key Card": frozenset({ASYLUM_ENGINE_BLOCK_PRISON}),
            "Poigne":          frozenset({ASYLUM_ENGINE_BLOCK_QUEENS}),
            "Gad Power":       frozenset({ASYLUM_ENGINE_BLOCK_SALVAGE}),
            "Engineers Key":   frozenset({ASYLUM_ENGINE_BLOCK}) | _engine_block_subregions,
        }
        # Unique Retractor Keys (2026-08-18): the flat "Retractor" ban above
        # only applies in flat-count mode. In named-key mode each region has
        # its own dedicated item (RETRACTOR_KEY_ITEM_NAMES) gating only that
        # ONE region's entrance (regions.py's create_regions()), not all 5 —
        # so the self-referential cycle this guard exists to prevent is now
        # per-region too: "Retractor - Prison" must stay out of
        # LIVESIDE_PRISON and its own downstream Engine Block room
        # specifically, but placing it inside London/Florida/Salvage/Queens
        # or their rooms is perfectly legal (those regions don't require
        # Prison's key). Tighter than the flat-mode ban on purpose — banning
        # it from all 5 the way "Retractor" is banned would forbid
        # placements that are actually safe now that each key has its own
        # fixed regional identity.
        if bool(self.options.unique_retractor_keys):
            _retractor_key_engine_room: dict[str, str] = {
                "Down Street Station, London": ASYLUM_ENGINE_BLOCK_LONDON,
                "Gardelle County Jail, Texas": ASYLUM_ENGINE_BLOCK_PRISON,
                "Summer Camp, Florida":        ASYLUM_ENGINE_BLOCK_FLORIDA,
                "Salvage Yard, Mojave Desert": ASYLUM_ENGINE_BLOCK_SALVAGE,
                "Mordant Street, Queens, NY":  ASYLUM_ENGINE_BLOCK_QUEENS,
            }
            for _region_name, _item_name in RETRACTOR_KEY_ITEM_NAMES.items():
                _region_item_bans[_item_name] = frozenset(
                    {_region_name, _retractor_key_engine_room[_region_name]}
                )
        else:
            _region_item_bans["Retractor"] = frozenset(LIVESIDE_REGIONS) | _engine_block_subregions

        # BARREL / PROGRESSION-ITEM GUARD (2026-08-07, generalized from
        # Jon's follow-up: "any progression item that would allow someone
        # else to get dark souls", not just Dark Soul behind an SL10 gate
        # specifically).
        #
        # fill.py's own SLOT_ACCEPTS/PROG_SLOT_CATS system — used by the
        # STANDALONE randomizer's assumed_fill() — already encodes this
        # exact principle: FILLER_SLOT_CATS = {"barrel", "cadeaux"}, "no
        # logic dependency, filled in filler phase". The standalone world
        # has never been able to hit this bug class at all, because it
        # structurally never lets a progression item land on a barrel.
        #
        # The AP world gave that property up. Cadeaux's item_rule was
        # removed 2026-07-21 for an unrelated reason (a bipartite-matching
        # generation failure, "108-stranded-Cadeaux-item"), and barrel
        # promotion (2026-08-01) copied that same "no item_rule" choice
        # ("same as barrel -- no item_rule needed") without re-examining
        # whether it was safe for barrels specifically. It isn't: unlike
        # Retractor/Gad Power (protected under AP's *restrictive* fill
        # pass by an exact count-vs-threshold match — see the guard above),
        # Dark Soul has a large deliberate "+20" placement margin
        # (create_items()'s prog_in_pool math) specifically so Fill has
        # room to place any ONE progression soul without needing every
        # other one simultaneously assumed held — which is exactly what
        # defeats that same self-protection for souls. Nothing else (any
        # other progression item — Baton, Marteau, Flambeau, Calabash,
        # Prison Key Card, Poigne, Engineers Key, the Eclipser parts, a
        # progression Dark Soul, etc.) has any protection at all once it
        # can land on an unrestricted barrel.
        #
        # Rather than keep enumerating specific item->region pairs one
        # failure at a time (the approach above, which stays in place for
        # the cases it already covers — it also protects NATIVE, non-barrel
        # locations like Govi/cadeaux/lore checks inside those regions,
        # which this new rule does not), this restores the standalone's
        # actual invariant directly: no progression-classified item
        # belonging to this player may ever be placed on a "barrel"
        # category location, full stop, regardless of which region it's
        # in. This trades a small amount of placement variety (promoted
        # barrels can no longer hold a key/unique item) for eliminating
        # the entire class of "a required item ended up somewhere that
        # itself required it" bugs at the source, rather than chasing
        # each new instance individually.
        for location in self.multiworld.get_locations(self.player):
            region = getattr(location, "parent_region", None)
            banned_here = frozenset(
                name for name, banned_regions in _region_item_bans.items()
                if region is not None and region.name in banned_regions
            )
            is_barrel = getattr(location, "raw", None) is not None and location.raw.category == "barrel"
            if not banned_here and not is_barrel:
                continue

            def _ban_items(item, _banned=banned_here, _no_progression=is_barrel,
                            _prior_rule=location.item_rule):
                if item.player == self.player:
                    if item.name in _banned:
                        return False
                    if _no_progression and item.advancement:
                        return False
                return _prior_rule(item)

            location.item_rule = _ban_items

    # ── Output ────────────────────────────────────────────────────────────────

    def generate_output(self, output_directory: str) -> None:
        """
        Build the placement dict ap_patcher.py (shadow-man-remastered-randomizer
        repo) expects.

        Writes a small portable "*.apshadowman" file into
        output_directory — everything apply_ap_seed.py (same repo, calls
        into ap_patcher.py's run_patcher()) needs to do the actual local
        patching later, with no dependency on this machine having the game
        installed. This is what makes the file safe to bundle into AP's
        hosted multiworld zip (see Main.py: every file in output_directory
        gets zipped flat into AP_<seed>.zip).

        2026-08-31 (Jon's ask): the file itself is now a real zip container
        (archipelago.json manifest + patch_data.json payload), not a bare
        JSON file — AP's own hosted-room download route
        (WebHostLib/downloads.py: download_patch) gates entirely on
        `zipfile.is_zipfile(...)`, returning "Old Patch file, no longer
        compatible." for anything that isn't an actual zip, regardless of
        extension. A bare JSON file (the previous format) downloaded fine
        when generating and playing locally, but was silently undownloadable
        from someone else's hosted room — this is what every other AP world
        already does for exactly that reason, whether or not the world's
        patch is a real binary rom-patch. The manifest carries
        "patch_file_ending": ".apshadowman" so the download route can name
        the file correctly without this world needing to register with AP's
        AutoPatchRegister (which forbids ".zip" as a registered ending and
        requires implementing the actual apply-a-binary-patch interface —
        neither of which fits this world's own out-of-band
        apply_ap_seed.py-driven patching, see below).

        2026-07-21: this used to also support a "hybrid" path — patching
        immediately during generation if a local game_dir option was set,
        via this world's own worlds/shadowman/patcher.py. That path and its
        patcher.py have been removed entirely: patcher.py had fallen far
        behind ap_patcher.py's feature set (entrance randomizer, piston
        combos, asset/mesh overrides, insanity marker FX, Book of
        Archipelago rename — none of it ever got ported into the hybrid
        copy) and maintaining two patchers in parallel wasn't worth it.
        Every seed now goes through apply_ap_seed.py, whether generated
        locally or on the AP website.
        """
        progression_placement: dict = {}

        for location in self.multiworld.get_locations(self.player):
            if location.item is None:
                continue

            # BUG FIX (2026-07-24): boss/true_form soul events (and "Defeat
            # Legion") added in create_regions() are plain Location
            # instances with no `.raw` attribute — only real ShadowManLocation
            # instances (fill-placed, real RSC slots) have one. Both always
            # have a non-None locked item, so without this guard this loop
            # crashed with AttributeError as soon as generate_output() ran.
            # Nothing here needs to patch an event location anyway (there's
            # no RSC record behind them), so skipping is correct.
            if not hasattr(location, "raw"):
                continue

            loc_key = location.raw.loc_key
            raw_loc = location_table.get(loc_key)
            if raw_loc is None:
                continue

            if raw_loc.raw.category in ("boss", "true_form"):
                # FIXED_SOUL_LOCS — pre_fill() locks a "Dark Soul" item here
                # purely so AP's fill ignores the slot for placement while
                # still counting it toward reachability (see pre_fill()'s
                # docstring: "auto-collected fixed checks"). These loc_keys
                # point at the boss's own enemy actor record (e.g.
                # RSC_X_AVERY_MARX in enemies.rsc), not a placeable item
                # pickup — there is no RSC record here write_placement_patches()
                # can retype, and there never should be; the boss's vanilla
                # soul drop on death is untouched, same as the standalone
                # randomizer (its own placement dict, built by assumed_fill()
                # over CHECKABLE_LOCS only, never includes FIXED_SOUL_LOCS
                # either). Previously this fell through to the Dark Soul
                # retype branch below and got queued into
                # progression_placement anyway, producing a "placement not
                # written to files" validation error for every boss location
                # (found 2026-07-21 via a real patch-log with 7 such errors,
                # 3 of which were boss locations: prison/florida/tenement).
                continue

            if location.item.player != self.player:
                # Item belongs to another player's world. Shadow Man has no
                # native way to represent a foreign game's item, so the
                # physical pickup is retyped to a generic "this is an
                # Archipelago item" marker — Book of Shadows
                # (RSC_X_BOOK_OF_SHADOWS) — with save_idx zeroed to match
                # its own native vanilla identity rather than the original
                # location's save_idx bucket (multiple retyped locations can
                # safely share save_idx=0 on the Shadow Man side; the game
                # still tracks each physical record's own collected state
                # independently). AP-side detection resolves WHICH location
                # fired via position matching, not save_idx — the same
                # mechanism already used for Govi/Dark Soul (see
                # _parse_govi_states / _match_govi_position_scan in
                # client.py, generalized for RSC_X_BOOK_OF_SHADOWS).
                progression_placement[loc_key] = _make_synthetic_raw(
                    "RSC_X_BOOK_OF_SHADOWS", raw_loc.raw, save_idx=0)
                continue

            item_name = location.item.name

            if item_name == "Dark Soul":
                # BUG FIX (2026-07-19): this used to be
                # `progression_placement[loc_key] = raw_loc.raw` — leaving
                # the RSC record completely untouched. That's only correct
                # if fill.py only ever places Dark Soul items back onto
                # locations that were ALREADY category=="soul" (i.e.
                # already RSC_X_GOVI). It doesn't restrict placement that
                # way (see the weighted slot choice in fill.py, which has no
                # item/location category match requirement) — a Dark Soul
                # can legitimately land on e.g. Calabash's own slot. Retype
                # unconditionally instead (which of the two dark-soul RSC
                # types — see below).
                #
                # SECOND BUG FIX (2026-07-19, later): the retype used to
                # keep the TARGET slot's save_idx, on the assumption Govi
                # detection is position-based. It isn't primarily — the
                # engine's dark-soul flag array is keyed by the collected
                # soul's save_idx (its global identity), and the standalone
                # randomizer preserves that identity when a soul moves to a
                # new slot. Keeping the target's save_idx (0 for most
                # key-item slots) made such souls invisible to the flag
                # array — confirmed live. Assign each placed soul a unique
                # identity instead (see _soul_identity_map); fill_slot_data
                # mirrors the same assignment into location_map so the
                # client's (level, save_idx) lookup resolves it directly.
                #
                # THIRD FIX (2026-07-21): RSC_X_GOVI has an engine quirk —
                # confirmed by the user, matches the standalone's own
                # fill.py _liveside_ok() safeguard and the CSV's "NIGHT
                # required only for GOVI" notes — where a Govi situated in
                # a liveside level (London/Florida/Prison/Queens/Salvage)
                # can only be collected at Night, regardless of whatever
                # access_rule the location itself otherwise has. Unlike the
                # standalone (which only ever places souls in liveside when
                # Night is already reachable at that fill step, via
                # _liveside_ok), AP places Dark Soul items with no
                # location-category restriction at all, so this was a live,
                # unhandled gap: a Dark Soul could land on any liveside
                # location — say, a weapon slot reachable without Night —
                # and become silently uncollectible until Night despite
                # AP's logic thinking it's already reachable. Retyping to
                # RSC_X_DARK_SOUL instead of RSC_X_GOVI for liveside targets
                # sidesteps this — a distinct, real RSC type (see
                # constants.py's TALL_TYPES/DARK_SOUL_SLOT_MARKER_FX_Y) that
                # does not carry the Night requirement. ap_patcher.py
                # already fully supports it as a write target (its own
                # DARK_SOUL_TYPES set + marker-FX handling), and client-side
                # detection (SOUL_COUNT_RVA / the dark-soul flag array /
                # kexShadowManAIGovi save-file parsing) is generic to any
                # dark soul object, not keyed by RSC name — so no patcher or
                # client changes were needed, only this choice of which name
                # to write.
                _soul_rsc_name = (
                    "RSC_X_DARK_SOUL" if raw_loc.raw.level_region in LIVESIDE_REGIONS
                    else "RSC_X_GOVI"
                )
                _soul_map, _ = self._soul_identity_map()
                progression_placement[loc_key] = _make_synthetic_raw(
                    _soul_rsc_name, raw_loc.raw,
                    save_idx=_soul_map.get(loc_key))
            elif is_trap_bonus_item(item_name):
                # Trap/Bonus (2026-08-01, renamed from "Secret Trap"
                # 2026-08-03, split into 7 concretely-named items
                # 2026-08-05 -- see items.py's is_trap_bonus_item()):
                # none of the 7 have a vanilla RSC identity of their own
                # -- each is a synthetic AP-only effect (the specific
                # effect is now baked into the item's own name, chosen at
                # generation time by _roll_trap_bonus_item_name(); only
                # "Secret Effect" still has a further runtime sub-roll,
                # see client.py's _apply_trap_bonus_now), not a real game
                # item with an inventory slot. Always
                # retyped to the generic "this is an Archipelago item"
                # marker with save_idx forced to 0, EXACTLY the same
                # treatment as another player's item landing here (see the
                # `location.item.player != self.player` branch above) --
                # whether this copy is self-found or received from someone
                # else's world makes no difference to how it's physically
                # represented. Deliberately does NOT fall through to the
                # generic `else` branch below, which would instead keep
                # whatever save_idx the target slot natively had (correct
                # for a real progression/weapon/lore item landing there,
                # wrong here — a nonzero save_idx on a Book of Shadows
                # marker would make it collide with that slot's original
                # native identity in the engine's flag-array/position-scan
                # detection, same reasoning as the foreign-item branch's
                # own comment).
                progression_placement[loc_key] = _make_synthetic_raw(
                    "RSC_X_BOOK_OF_SHADOWS", raw_loc.raw, save_idx=0)
            elif is_cadeaux_item(item_name):
                # Cadeaux Bundle Size (2026-07-27): was a literal
                # `item_name == "Cadeaux"` check -- now matches every
                # "Cadeaux Bundle x{N}" denomination too (see items.py).
                # Physically identical placement/patching either way; only
                # the AP-side item NAME differs by weight, which client.py
                # recovers via cadeaux_item_weight() rather than anything
                # written into the RSC record itself.
                if raw_loc.raw.category == "cadeaux":
                    # Native placement (2026-07-20 — the common case now
                    # that cadeaux locations are real AP checks, and
                    # set_rules()'s item_rule restricts "Cadeaux" items to
                    # cadeaux-category locations only). raw_loc.raw is
                    # already a genuine cadeaux CSV row — real RSC name,
                    # own correct save_idx — so no retyping is needed at
                    # all, unlike the displacement path below.
                    progression_placement[loc_key] = raw_loc.raw
                else:
                    # Displacement path (2026-07-19, mirrors standalone).
                    # NOT a defensive-only fallback in practice — the
                    # item_rule that used to restrict "Cadeaux" items to
                    # cadeaux-category locations was removed 2026-07-21 (see
                    # set_rules()'s docstring), so under cadeauxsanity a Cadeaux
                    # filler item can and routinely does land on any
                    # location, including this one. A placed Cadeaux here
                    # carries a donor cadeaux slot's RSC name + save_idx
                    # (identity travels with the item — same rule as
                    # souls); the donor slot's OWN progression_placement
                    # entry gets its category corrected to "barrel" below
                    # (see BUG FIX note there — 2026-07-25).
                    _cad_map, _, _ = self._cadeaux_identity_map()
                    donor = _cad_map.get(loc_key)
                    if donor is not None:
                        progression_placement[loc_key] = raw_loc.raw._replace(
                            object=donor.object,
                            save_idx=donor.save_idx,
                            category="cadeaux")
                    else:
                        # No donor identity available — fall back to an AP
                        # marker so we never write an invalid RSC name.
                        progression_placement[loc_key] = _make_synthetic_raw(
                            "RSC_X_BOOK_OF_SHADOWS", raw_loc.raw, save_idx=0)
            else:
                # Map AP item name back to RSC name for patcher
                rsc_name = AP_ITEM_TO_RSC.get(item_name, item_name)
                progression_placement[loc_key] = _make_synthetic_raw(rsc_name, raw_loc.raw)

        # Correct each cadeaux donor slot's CATEGORY to "barrel" (its
        # cadeaux identity now lives at the AP location it donated to,
        # assigned above) — without touching what item is actually
        # physically written there.
        #
        # BUG FIX (2026-07-25, Jon's report — Louisiana Swampland Cadeaux
        # 16 & 26 never received their assigned items in-game, staying
        # plain vanilla barrels): the 2026-07-21 version of this loop
        # unconditionally did
        #   progression_placement[donor.loc_key] = donor._replace(
        #       object=_cad_barrels[donor.loc_key], save_idx=0,
        #       category="barrel")
        # which overwrites `.object`/`.save_idx` too, not just `.category`.
        # Every donor loc_key is a real cadeaux-category AP location, so it
        # ALWAYS already has its own entry in progression_placement from
        # the main loop above — sometimes a genuinely different item (e.g.
        # Cadeaux 26 → Engineers Key, Cadeaux 16 → another player's Fire
        # Arrow) if cadeauxsanity let something other than "Cadeaux" land there.
        # Unconditionally overwriting `.object`/`.save_idx` destroyed that
        # real placement and turned the location back into an inert
        # unpatched barrel. Confirmed via the user's own patcher log:
        # validate_final_seed() reported "✅ Patch validation passed" even
        # though the items never appeared in-game — validation only checks
        # that progression_placement's keys ended up in patches_by_folder,
        # not that the values are the CORRECT ones, so it can't catch a
        # later step silently overwriting an already-correct entry.
        #
        # The 2026-07-21 fix's actual goal (stopping levels_txt_patcher's
        # per-level $cadeaux recount from double-counting a donor alongside
        # its recipient — the "742-750 vs vanilla 666" overcount) only
        # needs `.category` corrected, never `.object`/`.save_idx`. That
        # goal is currently moot anyway: patch_levels_txt() (the only
        # consumer that ever reads category=="cadeaux" for counting) has
        # been disabled for the whole AP path since 2026-07-22 — only
        # strip_levels_txt() runs now, which ap_patcher.py's own comment
        # says never touches $darksoul/$cadeaux counts. Left as a
        # category-only fix (not deleted outright) so this keeps doing the
        # right thing if patch_levels_txt() for AP seeds is ever
        # re-enabled.
        _cad_map, _cad_barrels, _cad_leftover_barrels = self._cadeaux_identity_map()
        for donor in _cad_map.values():
            existing = progression_placement.get(donor.loc_key)
            if existing is not None:
                progression_placement[donor.loc_key] = existing._replace(category="barrel")
            else:
                # Should be unreachable (see note above — every donor is
                # always a real AP location by construction), but kept as
                # a safety net so a donor never goes unwritten.
                progression_placement[donor.loc_key] = donor._replace(
                    object=_cad_barrels[donor.loc_key],
                    save_idx=0,
                    category="barrel")

        # Cadeaux Bundle Size (2026-07-27): non-representative cadeaux rows
        # that weren't claimed as identity donors above still need to
        # become barrels -- unlike every other entry in progression_placement,
        # these were NEVER visited by the main per-Location loop (they're
        # never real AP Locations at all), so this is a plain write, not a
        # correction of an existing entry the way the donor loop above is.
        for _loc_key, _barrel_name in _cad_leftover_barrels.items():
            _raw = location_table[_loc_key].raw
            progression_placement[_loc_key] = _raw._replace(
                object=_barrel_name, save_idx=0, category="barrel")

        # Neutralize soul donors (2026-07-25, Jon's design — see
        # _soul_identity_map's docstring for the full reasoning): a native
        # soul slot whose own identity got lent to a Dark Soul placed
        # elsewhere keeps whatever item the main loop above already wrote
        # for it (never touch .object here — only .save_idx) but always
        # gets its OWN save_idx forced to 0, regardless of what that item
        # is. Without this, the donor's still-live physical record keeps
        # claiming the same per-level tracked index it just lent away, and
        # collecting either object credits both (confirmed live twice this
        # session before this design). 0 is always safe — never touched by
        # the client's (level, save_idx) lookup.
        _, _soul_donor_neutralize = self._soul_identity_map()
        for _donor_loc_key in _soul_donor_neutralize:
            _existing = progression_placement.get(_donor_loc_key)
            if _existing is not None:
                progression_placement[_donor_loc_key] = _existing._replace(save_idx=0)

        config = {
            "shuffle_progression":   True,
            "gate_preset":           self.options.gate_preset.current_key,
            # shuffle_gad_temples removed here (2026-08-18, following the
            # 2026-08-15 options.py removal — see UniqueRetractorKeys's
            # neighboring comment in options.py for the full story): the
            # option no longer exists on self.options at all, and
            # ap_patcher.py's run_patcher() dropped the parameter in the
            # same 2026-08-15 pass, so this was dead data being read off a
            # nonexistent attribute — AttributeError on every AP generate,
            # not just ones with unique_retractor_keys on. Gad temples are
            # unconditionally normal shuffled pickups now; nothing downstream
            # needs this key anymore.
            "shuffle_weapons":       bool(self.options.shuffle_weapons),
            "shuffle_lore":          bool(self.options.shuffle_lore),
            "shuffle_bonus":         bool(self.options.shuffle_bonus),
            "shuffle_enemies":       bool(self.options.shuffle_enemies),
            "deadside_guns":         bool(self.options.deadside_guns),
            "enemy_mode":            self.options.enemy_mode.current_key,
            "enemy_mix_movement":    bool(self.options.enemy_mix_movement),
            "enemy_uncap_counts":    bool(self.options.enemy_uncap_counts),
            "shuffle_true_forms":    bool(self.options.shuffle_true_forms),
            "shuffle_ambients":      bool(self.options.shuffle_ambients),
            "ambient_mode":          self.options.ambient_mode.current_key,
            "shuffle_music":         bool(self.options.shuffle_music),
            "shuffle_voices":        bool(self.options.shuffle_voices),
            "shuffle_weapons_sfx":   bool(self.options.shuffle_weapons_sfx),
            "shuffle_enemies_sfx":   bool(self.options.shuffle_enemies_sfx),
            "combine_voice_and_enemy_sfx": bool(self.options.combine_voice_and_enemy_sfx),
            "shuffle_sky":           bool(self.options.shuffle_sky),
            "progression_balancing": int(self.options.progression_balancing),
            "cadeauxsanity":         bool(self.options.cadeauxsanity),
            "starting_health":       int(self.options.starting_health),
            "altar_health_grant":    int(self.options.altar_health_grant),
            "altar_cadeaux_required":      int(self.options.altar_cadeaux_required),
            "fogometers_cadeaux_required": int(self.options.fogometers_cadeaux_required),
            "death_penalty":         int(self.options.death_penalty) / 10,
            "sprint_multiplier":     int(self.options.sprint_multiplier) / 10,
            # 2026-07-21 (entrance randomizer) — informational only; the
            # actual mapping ap_patcher.py needs to write ExitLevelPos
            # patches lives in the top-level "entrance_shuffle" key below
            # (dict[str,str] or None), not here. Kept in config too so the
            # spoiler log header can report the mode without threading a
            # second parameter through write_spoiler_log().
            "entrance_mode":         self.options.entrance_mode.current_key,
            # 2026-07-21 (piston combo randomizer, Task 27) — string
            # "on"/"off" (NOT a bare bool) because ap_patcher.py's
            # run_patcher() passes this config dict straight into the
            # standalone's dark_engine_patch.randomize_dark_engine(), which
            # checks `str(config.get("piston_combos","off")) == "off"` —
            # str(False) == "False", not "off", so a bare bool would always
            # take the randomize branch even when disabled.
            "piston_combos":         "on" if bool(self.options.piston_combos) else "off",
            # AP's native "random" YAML keyword picks uniformly among the
            # declared choices, including full_random — so this Choice's
            # current_key is already fully random-capable. "full_random" is
            # translated to the literal string "random" here because that's
            # the sentinel soul_threshold_patch.randomize_soul_thresholds()
            # itself expects for its own (different) fully-random mode.
            # (Briefly removed 2026-08-09, re-added same day — see
            # options.py's SoulThresholdMode docstring for the full history.)
            "soul_threshold_mode":   (
                "random" if self.options.soul_threshold_mode.current_key == "full_random"
                else self.options.soul_threshold_mode.current_key
            ),
            # Precomputed in generate_early() via self.random so this exactly
            # matches what fill_slot_data() reports to the client — see the
            # comment there for why patcher.py must NOT re-roll its own via
            # its separate rng. patcher.py's Step 6e uses this dict directly
            # instead of calling randomize_soul_thresholds() itself whenever
            # it's present.
            "soul_thresholds_precomputed": (
                dict(self.sl_thresholds) if self.options.soul_threshold_mode.current_key != "off" else None
            ),
            # 2026-08-01 -- lets ap_patcher.py decide whether to also run
            # secret_mode_section_patch.py's EXE poller patch (the 9-cvar
            # .apcode/.apdata dispatcher hook that makes g_dogmode and its
            # 8 siblings apply live with no level transition needed). Only
            # meaningful when > 0 AND the secrets category is actually
            # enabled (trap_bonus_secrets_enabled) — no point touching the
            # exe for a poller that can never fire if secrets are turned
            # off entirely. Renamed from secret_trap_count 2026-08-03 when
            # this grew into 4 categories (health/voodoo/ammo don't need
            # any EXE-side patch — see CLAUDE.md's 2026-08-01 "poller not
            # being reapplied after a fresh seed apply" writeup for why
            # this needed to move from a manual step into the seed
            # pipeline in the first place).
            "trap_bonus_count": int(self.options.trap_bonus_count),
            "trap_bonus_secrets_enabled": bool(self.options.trap_bonus_secrets_enabled),
            # Unique Retractor Keys (2026-08-18) — plain bool (not the
            # "on"/"off" string piston_combos uses above; that convention is
            # specific to how dark_engine_patch.randomize_dark_engine() reads
            # it). ap_patcher.py's Step 7 gates the whole exe-patch block on
            # config.get("unique_retractor_keys", False); the actual
            # placement data it needs lives in the top-level
            # "retractor_key_locs" key below, not here.
            "unique_retractor_keys": bool(self.options.unique_retractor_keys),
        }

        # Unique Retractor Keys (2026-08-18) — {liveside_region_name: loc_key}
        # for wherever AP's own Fill placed each of the 5 named
        # "Retractor - <region>" items in THIS player's own world. Only
        # self-found placements are meaningful here: a copy sent to another
        # player's game has no physical Shadow Man pickup in this world at
        # all (it's granted remotely at runtime via client.py instead), so
        # there's nothing for ap_patcher.py's position table to resolve for
        # that one. Same "which loc_key did fill actually place THIS unique
        # item at" pattern fill_slot_data()'s inventory_flag_locs already
        # uses for one-of-a-kind items — retractor keys need identical
        # treatment despite living in items.py's "stackable-shaped" section.
        # Deliberately keyed by region name (not by item name): this exact
        # shape is what unique_retractor_keys_patch.build_retractor_table_from_ap()
        # expects, matching LIVESIDE_TO_IVAR1's own keys one-for-one, so
        # ap_patcher.py needs no extra name->region translation step.
        retractor_key_locs: dict[str, str] = {}
        if bool(self.options.unique_retractor_keys):
            _item_to_region = {v: k for k, v in RETRACTOR_KEY_ITEM_NAMES.items()}
            for _loc in self.multiworld.get_locations(self.player):
                _item = _loc.item
                if (_item is not None
                        and _item.player == self.player
                        and _item.name in _item_to_region
                        and hasattr(_loc, "raw")):
                    _region = _item_to_region[_item.name]
                    if _region not in retractor_key_locs:
                        retractor_key_locs[_region] = _loc.raw.loc_key

        # ── Always: write the portable patch-data file ──────────────────────
        # Safe for AP's hosting zip — small JSON, no game files touched, no
        # dependency on this machine having Shadow Man Remastered installed.
        # apply_ap_seed.py (shadow-man-remastered-randomizer repo) consumes
        # this to do the actual local patching against the player's own copy
        # of the game.
        #
        # Per-location fields: patcher.py's run_patcher() itself only reads
        # .object/.save_idx off each progression_placement value, but
        # patchers/levels_txt_patcher.py (called deeper in the same pipeline,
        # for the in-game hint tracker) also reads .level_id (required — a
        # missing one raises AttributeError, caught live 2026-07-20 via
        # apply_ap_seed.py) and .category (soft-required, read via getattr
        # with a default, but needed for correct $cadeaux per-level counts).
        # Preserving all four straight from `raw` — rather than trying to
        # re-derive level_id from loc_key client-side, which happens to work
        # today but isn't a documented invariant — keeps this correct even
        # if generate_output()'s placement construction changes later.
        #
        # gate_remap and config are passed through as-is (both already
        # JSON-safe: bools/ints/strings, plus soul_thresholds_precomputed's
        # int keys, which patcher.py's Step 6e already round-trips through
        # str keys because slot_data does the same over the network).
        portable_patch_data = {
            "format_version": 1,
            "game": "Shadow Man Remastered",
            "seed": self.multiworld.seed,
            "player": self.multiworld.get_file_safe_player_name(self.player),
            "config": config,
            "gate_remap": dict(self.gate_values),
            # 2026-07-21 (entrance randomizer, Task 20) — dict[portal_file,
            # dest_portal_file] over the 9 Deadside "LE_*.cut" filenames (see
            # DEADSIDE_PORTAL_FILES in regions.py), or None when
            # entrance_mode is "off". ap_patcher.py's run_patcher() consumes
            # this directly to drive apply_unified_shuffle() (ported from the
            # standalone's randomizers/entrance_randomizer.py) against the
            # extracted cutscene/scripts/*.cut files. Top-level (not nested
            # under "config") to match gate_remap's placement — both are
            # placement-affecting data the patcher needs structurally, not
            # just informational settings.
            "entrance_shuffle": dict(self.entrance_shuffle) if self.entrance_shuffle else None,
            "progression_placement": {
                loc_key: {
                    "object":   raw.object,
                    "save_idx": raw.save_idx,
                    "level_id": raw.level_id,
                    "category": raw.category,
                }
                for loc_key, raw in progression_placement.items()
            },
            # Unique Retractor Keys (2026-08-18) — see the comment where
            # retractor_key_locs is built above. {} when the option is off,
            # matching every other AP seed's absence of this key gracefully
            # (apply_ap_seed.py reads it via .get("retractor_key_locs", {})).
            "retractor_key_locs": retractor_key_locs,
        }
        patch_data_path = (Path(output_directory) /
                            f"{self.multiworld.get_out_file_name_base(self.player)}.apshadowman")

        # Real zip container (2026-08-31, Jon's ask) -- see generate_output()'s
        # own docstring above for why this can't be a bare JSON file anymore.
        # Deliberately hand-rolled rather than subclassing worlds.Files'
        # APContainer/APPatch: those pull in AutoPatchRegister's "must
        # implement patch(), can't use .zip as your own ending" requirements,
        # which don't fit this world's out-of-band apply_ap_seed.py flow --
        # all that actually matters for AP's hosted-download route is that
        # the bytes are a real zip and archipelago.json carries
        # "patch_file_ending", both of which this satisfies directly.
        manifest = {
            "compatible_version": 6,
            "version": 6,
            "game": "Shadow Man Remastered",
            # Left blank at generation time, same as AP's own
            # APPlayerContainer default -- WebHostLib's download_patch route
            # fills this in itself for a real hosted room; nothing here reads
            # it back (our client.py's connect panel takes server/name/
            # password from the player directly, see overlay_dll).
            "server": "",
            "player": self.player,
            "player_name": self.multiworld.get_file_safe_player_name(self.player),
            "patch_file_ending": ".apshadowman",
            "procedure": "custom",
        }
        with zipfile.ZipFile(patch_data_path, "w", zipfile.ZIP_DEFLATED, True, 9) as zf:
            zf.writestr("archipelago.json", json.dumps(manifest))
            zf.writestr("patch_data.json", json.dumps(portable_patch_data))

    # ── Slot data ─────────────────────────────────────────────────────────────

    def fill_slot_data(self) -> Dict[str, Any]:
        """
        Build the location map the client uses to translate KSAV state changes
        into AP location checks.

        Each entry is keyed by location name and contains:
            ap_id        – AP numeric location ID
            level_id     – level folder name (e.g. "deadside")
            instance_id  – KSAV actor instance ID (uint32); 0 means not yet known
            category     – location category string (used by client to choose parser)
            source_file  – RSC source file (determines KSAV class):
                            "quest.rsc"    → kexShadowManQuestObject   (state +0x32)
                            "instance.rsc" → class TBD (future)
                            "resource.rsc" → class TBD (future)
            x, y, z      – world position (or None), extracted from level geometry.
                            Needed by the client for Govi (dark soul) records, which
                            have no usable instance_id — see GOVI_POS_OFFSET in
                            client.py. Category-independent: the patcher can retype
                            ANY location slot into a govi in a given seed, so the
                            client matches against every location's position, not
                            just category == "soul" ones.

        Locations with instance_id == 0 are included but skipped by the client
        (for instance_id-based lookup) until the extraction tool has been run
        to populate them. x/y/z are unaffected by this and still included.
        """
        # BUG FIX (2026-08-23, Jon's report — dark soul pickups not logging
        # as checks): this loop used to iterate location_table.items()
        # UNCONDITIONALLY — that's the full, per-seed-independent static
        # catalog, not the subset of locations regions.py's create_regions()/
        # _build_sub_regions() actually instantiated as real Locations this
        # seed (it applies its own skip_cats/skip_loc_keys/_cadeaux_ok
        # filtering — see that file's _SKIP_CATS/CADEAUX_666_LOCS/
        # cadeaux_bundle_representatives — e.g. EVERY "cadeaux" category
        # location is entirely excluded whenever the cadeauxsanity option is
        # off, "barrel" is excluded unconditionally, and non-representative
        # rows are excluded under Cadeaux Bundle Size). Every one of those
        # excluded rows stays physically untouched/vanilla in-game — see
        # regions.py's own "never becomes an AP location, stays physically
        # untouched/vanilla" docstring language — yet was still showing up
        # in slot_data's location_map with its own real ap_id and native
        # save_idx. client.py's live dark-soul-flag-array watcher resolves
        # ANY (level, save_idx) via this table with no awareness that the
        # location behind it was never actually created — so an entirely
        # ordinary VANILLA pickup (e.g. a Cadeaux, with cadeauxsanity off) logs
        # a successful "resolved to ap_id=..." line and attempts a
        # LocationChecks send for an ap_id the server never registered for
        # this player, which the server just silently drops: no item, no
        # "found their X" notification, no error either — exactly Jon's
        # report. Filtered to the real, this-seed location set (same
        # pattern already used correctly by _soul_identity_map()'s `placed`
        # a few methods up, and by inventory_flag_locs just below) so
        # phantom/vanilla rows never reach the client's live watchers at
        # all.
        _real_loc_keys = {
            loc.raw.loc_key for loc in self.multiworld.get_locations(self.player)
            if hasattr(loc, "raw")
        }
        location_map: Dict[str, Any] = {}
        for loc_name, loc_data in location_table.items():
            if loc_name not in _real_loc_keys:
                continue
            raw = loc_data.raw
            iid = getattr(raw, "save_idx", None)
            if iid is None:
                continue
            location_map[loc_name] = {
                "ap_id":       loc_data.code,
                "level_id":    raw.level_id,
                "instance_id": iid,
                "category":    raw.category,
                "source_file": getattr(raw, "source_file", "quest.rsc"),
                "x":           raw.x,
                "y":           raw.y,
                "z":           raw.z,
            }

        # Dark Soul identity override (2026-07-19, see _soul_identity_map):
        # the physical govi written at each of these locations carries the
        # ASSIGNED soul identity as its reward id — the engine's dark-soul
        # flag array flips at that index on collection. The client resolves
        # flips via (level_id, instance_id), so instance_id here must be
        # the same assigned identity, not the slot's raw save_idx.
        _soul_map, _soul_donor_neutralize = self._soul_identity_map()
        for _loc_key, _soul_idx in _soul_map.items():
            _entry = location_map.get(_loc_key)
            if _entry is not None:
                _entry["instance_id"] = _soul_idx
        # Neutralized soul donors (2026-07-25, see _soul_identity_map's
        # docstring) also need instance_id=0 here, matching the save_idx=0
        # generate_output() forces into their own physical record.
        for _loc_key in _soul_donor_neutralize:
            _entry = location_map.get(_loc_key)
            if _entry is not None:
                _entry["instance_id"] = 0

        # Cadeaux identity override (2026-07-19, see _cadeaux_identity_map):
        # a placed Cadeaux is written with its donor's save_idx as its reward
        # id, so client-side (level, instance_id) lookups must use the same
        # value. Donors with save_idx 0 write 0 — skipped by the client's
        # iid map; the pickup log covers those by position.
        _cad_map, _, _ = self._cadeaux_identity_map()
        for _loc_key, _donor in _cad_map.items():
            _entry = location_map.get(_loc_key)
            if _entry is not None:
                _entry["instance_id"] = _donor.save_idx

        # Map each of this player's own unique RSC_X_* items to the loc_key
        # where fill physically placed it in THIS world (2026-07-19). The
        # client's live inventory-flag watcher uses this to attribute a
        # possession-flag flip to the item's actual randomized location.
        # Attributing to the item's VANILLA location (the old behavior,
        # via a loc_key-keyed RVA table) sent false checks whenever a
        # unique item was shuffled elsewhere — and on the first AP-marker
        # pickup, since the marker object is a Book of Shadows and flips
        # the BoS flag. Only self-owned placements matter: locations
        # holding other players' items are physical AP markers and never
        # touch these flags. The client filters to the items it has
        # confirmed flag RVAs for (ITEM_FLAG_RVAS in client.py).
        inventory_flag_locs: Dict[str, str] = {}
        for loc in self.multiworld.get_locations(self.player):
            item = loc.item
            # item.name is now a friendly display name (see items.py's
            # _UNIQUE_ITEM_RSC_NAMES / locations.py's FRIENDLY_NAMES for
            # the full rationale) -- membership in _UNIQUE_ITEM_RSC_NAMES
            # replaces the old `item.name.startswith("RSC_X_")` check,
            # which identified exactly the same set of one-of-a-kind items
            # back when name == RSC name coincided. loc.raw.loc_key (not
            # loc.name) is the key location_map is actually keyed by.
            if (item is not None
                    and item.player == self.player
                    and item.name in _UNIQUE_ITEM_RSC_NAMES
                    and item.name not in inventory_flag_locs
                    and loc.raw.loc_key in location_map):
                inventory_flag_locs[item.name] = loc.raw.loc_key

        return {
            "gate_values":         self.gate_values,
            "gate_preset":         self.options.gate_preset.current_key,
            "seed":                self.multiworld.seed,
            "location_map":        location_map,
            "inventory_flag_locs": inventory_flag_locs,
            "death_link":          bool(self.options.death_link),
            "death_link_threshold": int(self.options.death_link_threshold),
            # SL1-SL10 -> souls required for THIS seed (vanilla values if
            # soul_threshold_mode is off). JSON/NetUtils round-trips dict keys
            # as strings, so the client must int() the keys back on receipt —
            # see _on_connected in client.py.
            "soul_thresholds":     {sl: v for sl, v in self.sl_thresholds.items()},
            # BUG FIX (2026-07-28, Jon's report): client.py's "Shadow Man"
            # tab reads slot_data.get("piston_combos") to decide whether
            # Jacks Schematic belongs in the Go Mode prerequisite checklist
            # (see _go_mode_prerequisites()/on_package's "Connected"
            # handler). That key was never actually written here — the
            # ONLY "piston_combos" write in this file lives in
            # generate_output()'s separate local-patcher config dict (the
            # one embedded in the .apshadowman file for ap_patcher.py's
            # own use, "on"/"off" string, entirely different from this
            # method's return value). fill_slot_data() is what actually
            # becomes the live AP session's slot_data — that dict never
            # had this key at all, confirmed live via Jon's own client log
            # ("slot_data['piston_combos']=None") even with piston_combos
            # correctly true at generation time and a fresh reconnect.
            # Plain bool here (not the patcher config's "on"/"off" string —
            # this dict has no reason to match that unrelated convention;
            # see the matching read-side fix in client.py's on_package()).
            "piston_combos":       bool(self.options.piston_combos),
            # Unique Retractor Keys (2026-08-18) — plain bool, same
            # slot_data pattern as piston_combos just above. Read by
            # client.py's on_package() into self.unique_retractor_keys_on,
            # used by _go_mode_prerequisites() to show the 5 named keys
            # individually instead of the flat "Retractors x/5" count (which
            # would always misreport 0/5 in this mode — there's no fungible
            # "Retractor" item in the pool at all when this option is on).
            "unique_retractor_keys": bool(self.options.unique_retractor_keys),
            # Universal Tracker support (2026-07-28, see generate_early()'s
            # UT-passthrough comment and interpret_slot_data() below for
            # the full rationale). Neither of these two keys existed in
            # slot_data before this fix -- both are self.random-derived
            # per-seed values that a UT local regen needs the REAL value
            # for, not a freshly re-randomized one, or its whole region
            # graph (Deadside portal topology for entrance_shuffle; which
            # cadeaux rows are real AP locations for
            # cadeaux_bundle_representatives) silently diverges from the
            # actual hosted seed.
            "entrance_shuffle": dict(self.entrance_shuffle) if self.entrance_shuffle else None,
            "cadeaux_bundle_representatives": dict(self.cadeaux_bundle_representatives),
            # Trap/Bonus (2026-08-01, renamed from "Secret Trap" 2026-08-03
            # when this grew into 4 categories) -- read by client.py's
            # on_package() "Connected" handler into
            # self.trap_bonus_mode/self.trap_bonus_duration/
            # self.trap_bonus_*_enabled, consumed by
            # _apply_trap_bonus_now(). current_key gives the plain string
            # ("always_temporary" etc.) rather than the numeric option
            # value, same convention as gate_preset above.
            "trap_bonus_mode":            self.options.trap_bonus_mode.current_key,
            "trap_bonus_duration":        int(self.options.trap_bonus_duration),
            "trap_bonus_secrets_enabled": bool(self.options.trap_bonus_secrets_enabled),
            "trap_bonus_health_enabled":  bool(self.options.trap_bonus_health_enabled),
            "trap_bonus_voodoo_enabled":  bool(self.options.trap_bonus_voodoo_enabled),
            "trap_bonus_ammo_enabled":    bool(self.options.trap_bonus_ammo_enabled),
            # Barrel promotion (2026-08-01) -- UT passthrough, same pattern
            # and reasoning as cadeaux_bundle_representatives above (a UT
            # regen needs the REAL promoted set, not a freshly re-randomized
            # one, or its region graph would include the wrong barrel
            # locations). Not currently read by client.py.
            "barrel_promoted_locs": sorted(self.barrel_promoted_locs),
        }

    @staticmethod
    def interpret_slot_data(slot_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Universal Tracker support (re-gen passthrough) -- see
        https://github.com/FarisTheAncient/Archipelago/blob/tracker/worlds/tracker/docs/re-gen-passthrough.md
        and worlds/tunic/__init__.py's own interpret_slot_data (same
        pattern, adopted here 2026-07-28 for the identical reason its own
        comment gives: "due to complexities with ER" [entrance rando]).

        Returning the whole slot_data dict unchanged makes it available as
        self.multiworld.re_gen_passthrough[self.game] the next time UT
        regenerates this world locally -- generate_early() checks for it
        at the very top and, wherever present, uses the real seed's
        recovered gate_values/soul_thresholds/entrance_shuffle/
        cadeaux_bundle_representatives instead of re-randomizing them.
        Without this, a UT regen's own self.random draws for those values
        (it doesn't replay generation's real self.random call sequence)
        would essentially always differ from what the actual hosted seed
        used, corrupting UT's reachability calculations for anything
        gated behind gates, Deadside entrances, or cadeaux locations.
        """
        return slot_data