"""
options.py
──────────
Archipelago YAML options for Shadow Man Remastered.
Maps directly to patcher.py config keys and fill.py parameters.
"""

from dataclasses import dataclass
from Options import (
    Choice,
    Toggle,
    Range,
    DeathLink,
    PerGameCommonOptions,
    StartInventoryPool,
)


class GatePreset(Choice):
    """
    Controls how coffin gate soul requirements are shuffled.
    - story:  All gates open (SL0). No soul requirements anywhere.
    - easy:   Gates shuffled, capped at SL7. First 6 coffin gates start open.
    - medium: Gates shuffled, capped at SL8. First 3 coffin gates start open.
    - hard:   Full shuffle with safety caps. First coffin gate starts open.
    - chaos:  Fully unconstrained shuffle. Some seeds may be extremely difficult.
    """
    display_name = "Gate Shuffle Preset"
    option_story  = 0
    option_easy   = 1
    option_medium = 2
    option_hard   = 3
    option_chaos  = 4
    default = 3


class ShuffleWeapons(Toggle):
    """
    If enabled, weapons are shuffled into the randomizer pool.
    If disabled, weapons appear at their vanilla locations.
    """
    display_name = "Shuffle Weapons"
    default = 1


class ShuffleLore(Toggle):
    """
    If enabled, lore items (Book of Shadows, Prophecy, Jack's Schematic)
    are shuffled into the item pool.
    """
    display_name = "Shuffle Lore Items"
    default = 1


class ShuffleBonus(Toggle):
    """
    If enabled, the Light Soul (permanent invincibility reward for collecting
    all 666 Cadeaux) is shuffled into the item pool.
    """
    display_name = "Shuffle Bonus Items"
    default = 0


class ShuffleEnemies(Toggle):
    """
    If enabled, enemy types are randomized within each level.
    Does not affect item placement logic.
    """
    display_name = "Shuffle Enemies"
    default = 0


class DeadsideGuns(Toggle):
    """
    If enabled, forces the vanilla "I like Dead Side Guns" secret on at
    patch time -- Deadside weapons work on Liveside and vice versa --
    without needing to find its hidden in-world unlock trigger (Florida
    Summer Camp, near the final mine shaft). Applied by editing the
    player's kexengine.cfg directly; requires having launched the game at
    least once already so that file exists.
    """
    display_name = "Deadside Guns"
    default = 0


class EnemyMode(Choice):
    """
    Controls how enemies are shuffled when Shuffle Enemies is enabled.
    - difficulty:  Difficulty-weighted — harder enemies land in deeper areas.
    - full:        Draw from all enemy types globally.
    - contextual:  Shuffle within themed pools per level.
    """
    display_name = "Enemy Shuffle Mode"
    option_difficulty  = 0
    option_full        = 1
    option_contextual  = 2
    default = 0


class EnemyMixMovement(Toggle):
    """
    If enabled, enemy shuffle may swap enemies across movement types
    (ground/flying/swimming). Ignored unless Shuffle Enemies is on.
    """
    display_name = "Enemy Shuffle: Mix Movement Types"
    default = 0


class EnemyUncapCounts(Toggle):
    """
    If enabled, enemy shuffle draws each slot's replacement independently
    instead of preserving per-type counts — some enemy types may appear far
    more (or less) often than vanilla. Ignored unless Shuffle Enemies is on.
    """
    display_name = "Enemy Shuffle: Uncap Type Counts"
    default = 0


class ShuffleAmbients(Toggle):
    """
    If enabled, friendly/ambient creatures (rats, egrets, flies, butterflies,
    friendly fish) are shuffled across their spawn slots. Cosmetic only.
    """
    display_name = "Shuffle Ambient Creatures"
    default = 0


class AmbientMode(Choice):
    """
    Controls how ambient creatures are shuffled when Shuffle Ambients is enabled.
    - global:      One pool, no bucketing — a rat can become a fish.
    - full:        Shuffle within movement type (ground/flying/swimming).
    - contextual:  Shuffle within context group + movement type.
    """
    display_name = "Ambient Shuffle Mode"
    option_global      = 0
    option_full        = 1
    option_contextual  = 2
    default = 0


class ShuffleTrueForms(Toggle):
    """
    If enabled, true form boss enemies (which drop Dark Souls) can swap
    positions with regular enemies. Soul reachability logic is preserved.
    """
    display_name = "Shuffle True Form Enemies"
    default = 0


class ShuffleMusic(Toggle):
    """
    If enabled, music tracks are shuffled globally across all levels.
    Requires a KPF repack — applied by running apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Shuffle Music"
    default = 0


class ShuffleVoices(Toggle):
    """
    If enabled, Shadow Man's generic voice lines are shuffled.
    """
    display_name = "Shuffle Voice Lines"
    default = 0


class ShuffleWeaponsSfx(Toggle):  # was ShuffleWeaponSfx
    """
    If enabled, weapon fire and reload sounds are shuffled within
    each weapon category.
    """
    display_name = "Shuffle Weapon SFX"
    default = 0


class ShuffleEnemiesSfx(Toggle):
    """
    If enabled, enemy pain/startle/attack sounds are shuffled between enemy
    types. Cosmetic only.
    """
    display_name = "Shuffle Enemy SFX"
    default = 0


class CombineVoiceAndEnemySfx(Toggle):
    """
    If enabled, Shadow Man's voice lines and every enemy's sounds are
    shuffled together as one shared pool, instead of shuffling separately.
    Shadow Man can end up grunting with an enemy's pain sound, and an enemy
    can end up screaming in Shadow Man's voice. Requires both Shuffle Voice
    Lines and Shuffle Enemy SFX to also be enabled -- otherwise has no
    effect. Cosmetic only.
    """
    display_name = "Shuffle Voice & Enemy SFX Together"
    default = 0


class ShuffleSky(Toggle):
    """
    If enabled, level skyboxes are shuffled between levels. Cosmetic only.
    """
    display_name = "Shuffle Skyboxes"
    default = 0


class StartingHealth(Range):
    """
    Starting (and max) health, as a multiple of 1000. Vanilla is 5 (5000 HP).
    EXE patch — applied by running apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Starting Health (x1000)"
    range_start = 1
    range_end   = 10
    default     = 5


class AltarHealthGrant(Range):
    """
    Health granted per life altar interaction, as a multiple of 1000.
    Vanilla is 1 (1000 HP per altar, 5 altars total). EXE patch — only
    applies when you run apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Altar Health Grant (x1000)"
    range_start = 1
    range_end   = 10
    default     = 1


class AltarCadeauxRequired(Range):
    """
    Minimum Cadeaux required to interact with a life altar or the
    Fogometers door (cost per interaction always matches). Vanilla is 100.
    Does not affect AP logic — cadeaux_666 in access_rules.py is currently
    a no-op. EXE patch — applied by running apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Altar Cadeaux Required"
    range_start = 1
    range_end   = 133
    default     = 100


class FogometersCadeauxRequired(Range):
    """
    Total Cadeaux required to open the Fogometers door. Vanilla is 666.
    Clamped to at least 5x Altar Cadeaux Required. EXE patch — applied by
    running apply_ap_seed.py (shadow-man-remastered-randomizer repo)
    against your generated seed, not during AP generation itself.

    CORRECTION (2026-07-24): this docstring used to say "Does not affect
    AP logic" — that stopped being true the same day cadeaux_666() was
    fixed to actually check state.count("Cadeaux", player) instead of an
    unconditional True (see access_rules.py). This value IS what the
    Fogometers Light Soul location's logic checks against when Cadeaux
    Gated Content (below) is on. Left at the 666 vanilla default, only
    653 of the 666 vanilla Cadeaux are ever AP-trackable/verified, so
    generate_early() precollects the 13-item shortfall as free starting
    Cadeaux to keep that location solvable — see its comment in
    __init__.py.
    """
    display_name = "Fogometers Cadeaux Required"
    range_start = 0
    range_end   = 666
    default     = 666


class CadeauxGatedContent(Toggle):
    """
    Controls whether the Fogometers Light Soul location (gated behind
    SL10 + collecting Fogometers Cadeaux Required worth of Cadeaux) exists
    as an AP check at all.

    Off (default): excluded from the AP location pool entirely — stays
    untouched/vanilla, same treatment as barrels and enemy checks. On: it
    becomes a real AP check. Only meaningful when Cadeauxsanity is also on
    (Cadeaux is only an AP-tracked item then); with Cadeauxsanity off the
    location's own rule always passes, same as before this option existed.

    Renamed display_name (2026-07-28, Jon's request): the old name
    "Cadeaux Gated Content" didn't make clear WHICH content. Settled on
    "Fog Door Check" — short and pairs with FogometersCadeauxRequired's
    GUI label ("Fog door:", the threshold THIS toggle's gate checks
    against when on) rather than spelling out "Fogometers Light Soul Gate
    in Logic" in full; the docstring above carries the detail instead.

    Found and added 2026-07-24 after this location's rule turned out to be
    unreachable-in-full-accessibility for FillError diagnosis reasons — see
    __init__.py's generate_early() comment and access_rules.py's
    cadeaux_666() docstring for the full history.
    """
    display_name = "Fog Door Check"
    default = False


class DeathPenalty(Range):
    """
    Max health lost per death, as tenths (10 = -1000 HP/death, 0 = off).
    Floor equals the penalty amount (health never drops below one step).
    EXE patch — applied by running apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Death Penalty (x100)"
    range_start = 0
    range_end   = 100
    default     = 0


class SprintMultiplier(Range):
    """
    Hold Shift to move at this multiple of normal speed, on both land and
    in water, as tenths (20 = 2.0x, 0 = off/vanilla -- no Shift-sprint).
    EXE patch — applied by running apply_ap_seed.py (shadow-man-remastered-randomizer repo) against your generated seed, not during AP generation itself.
    """
    display_name = "Shift-Sprint Multiplier (x10)"
    range_start = 0
    range_end   = 50
    default     = 0


class SoulThresholdMode(Choice):
    """
    Randomizes the real in-game soul counts required for each Soul Level
    (SL1-SL10) via EXE patch. AP's own logic (access_rules.py's
    _soul_level(), fed by this world's resolved self.sl_thresholds) has
    respected whatever mode is chosen here since 2026-07-20 — it no longer
    assumes vanilla thresholds, so a shuffled seed's logic and its real
    in-game requirements stay in sync. See SoulLogicBuffer below if
    shuffled thresholds are producing seeds that feel razor-tight.
    - off:         Vanilla thresholds (1,3,7,15,23,35,51,71,95,120).
    - progressive: Gaps grow geometrically — early SLs cheap, late SLs steep.
    - balanced:    Roughly equal-sized gaps between thresholds.
    - full_random: Fully random ascending thresholds (mode="random"
                    internally).

    full_random's history: existed 2026-07-20, hardened 2026-08-07 (flat
    minimum gap between every consecutive SL) after being confirmed live
    to occasionally produce AP-unsatisfiable seeds. Removed outright
    2026-08-09 after Jon's own live A/B test (switching a failing seed
    from full_random to progressive fixed it) — at the time this looked
    like proof full_random itself was uniquely unsafe.

    Re-added 2026-08-09 once the REAL root cause was found and fixed: it
    was never full_random specifically. create_items()'s Dark Soul
    progression-count math was reading the fixed VANILLA threshold table
    to decide `need`, instead of this seed's own resolved
    self.sl_thresholds — so whenever a mode's real per-SL threshold
    exceeded the vanilla value at that same index, too few Dark Souls got
    classified as real `progression` items, making the seed's actual gate
    unsatisfiable in AP's own logic regardless of Fill placement.
    Simulation showed this hit "balanced" in 100% of 3000 seeds (by up to
    +45 souls) and "progressive" in 20-83% of seeds depending on the SL —
    i.e. progressive and balanced were exposed to the same underlying bug
    the whole time, just less visibly. Fixing that (create_items() now
    uses self.sl_thresholds) plus full_random's own 2026-08-07 minimum-gap
    hardening and a second 2026-08-09 hardening pass (an additional, much
    larger floor on the final SL9->SL10 gap specifically, since SL10 is
    hardcoded to require literally every soul in the game and a thin top
    gap left SL9 almost as fragile) closes both layers full_random's risk
    ever came from. Confirmed via 10 real generation runs each of
    progressive and balanced post-fix, zero failures, before re-adding.
    See soul_threshold_patch.py's randomize_soul_thresholds() for the
    generator-side mode="random" implementation.
    """
    display_name = "Soul Threshold Mode"
    option_off         = 0
    option_progressive = 1
    option_balanced     = 2
    option_full_random  = 3
    default = 0


class SoulLogicBuffer(Choice):
    """
    Pads how many souls AP's LOGIC requires before a soul gate, above and
    beyond the gate's real in-game requirement. E.g. under "easy", a gate
    that actually only needs 3 souls in-game won't be considered passable
    by AP's fill/logic until several MORE souls than that have been placed
    somewhere reachable before it. The real, in-game requirement (what's
    patched into the exe and shown to the client) is completely
    unaffected — this only changes how much slack AP leaves itself when
    deciding where things can go, so a real playthrough that doesn't grab
    every single reachable soul (or grabs them in a suboptimal order)
    still comfortably clears each gate instead of landing exactly on the
    wire. Every gate's padded requirement is still capped at 120 (the
    maximum possible souls in the game), so this can never make a gate
    outright impossible.
    - off:    no padding (default) — exact previous behavior.
    - hard:   +2 souls of slack per gate.
    - medium: +4 souls of slack per gate.
    - easy:   +6 souls of slack per gate.
    See SOUL_LOGIC_BUFFER_VALUES below for the actual numbers each tier
    resolves to — change them there, not here, if they ever need tuning.

    Briefly defaulted off -> easy (+6) 2026-08-07 as a stopgap alongside
    the soul_threshold_patch.py "random"-mode top-gap fix, while the real
    driver of that era's generation failures was still being chased.
    Reverted back to off 2026-08-09 once the actual root cause
    (create_items() computing Dark Soul progression counts from vanilla
    thresholds instead of this seed's own resolved ones — see
    SoulThresholdMode's docstring above) was found and fixed directly, and
    confirmed clean via real generation batches. This is a genuinely
    optional extra-slack knob now, not a safety net standing in for a bug
    fix — still worth reaching for if a specific seed's soul economy feels
    razor-tight, but no longer needed by default.
    """
    display_name = "Soul Logic Buffer"
    option_off    = 0
    option_hard   = 1
    option_medium = 2
    option_easy   = 3
    default = 0


# Choice key -> actual soul count padded onto every gate's LOGIC
# requirement (see SoulLogicBuffer above and __init__.py's generate_early(),
# which is the only reader). Single source of truth for the tier -> number
# mapping so it never drifts out of sync with the option's own docstring.
SOUL_LOGIC_BUFFER_VALUES: dict[str, int] = {
    "off":    0,
    "hard":   2,
    "medium": 4,
    "easy":   6,
}


class MaxGateSL(Range):
    """
    Caps the highest Soul Level a shuffled coffin gate can require.
    10 = no cap (vanilla SL pool, top gate needs all 120 souls — zero slack;
    generation may fail and retry). Lower values leave slack between the
    120-soul supply and the largest requirement, which makes generation far
    more reliable, especially in multiworlds. Applied on top of the preset's
    own cap (whichever is lower). Ignored by the story preset.
    """
    display_name = "Max Gate Soul Level"
    range_start = 3
    range_end   = 10
    default     = 8


class OpenGatesN(Range):
    """
    Overrides the gate preset's own "N gates open" count, forcing the first
    N coffin gates (a fixed linear order: Marrow, Wasteland, Asylum, Temple,
    Cageways, Playrooms) to SL0 regardless of which preset is chosen.
    -1 (default) leaves each preset's own baked-in value alone (story/chaos:
    0, easy: 6, medium: 3, hard: 1). 0-6 explicitly overrides it. Resolved
    before regions/rules are built, so fill logic sees the opened gates
    correctly either way. Has no visible effect on the story preset, which
    already sets every gate to SL0.
    """
    display_name = "Open Gates Override"
    range_start = -1
    range_end = 6
    default = -1


class EntranceMode(Choice):
    """
    Shuffles which physical portal in Deadside Marrow Gates leads to which
    destination — the door that vanilla sends you to (say) the Cageways
    could instead lead to a Gad Temple, and vice versa.

    - off: vanilla connections (default).
    - deadside_only: the 9 Deadside hub portals (Wasteland, Asylum Gateways,
      the 3 Gad Temples, Cageways, Playrooms, Lavaducts, Fogometers) are
      shuffled among themselves. Each portal keeps its own physical soul
      threshold (fixed by where it actually sits in Deadside) — only where
      it leads changes.

    cross_hub mode (mixing in the 5 Dark Engine soul gates too, matching the
    standalone randomizer) is not implemented yet.
    """
    display_name = "Entrance Mode"
    option_off = 0
    option_deadside_only = 1
    default = 0


class ProgressionBalancing(Range):
    """
    Controls how aggressively key items are pushed toward deeper locations.
    0  = uniform random placement.
    100 = key items strongly biased toward hardest-to-reach locations.
    """
    display_name = "Progression Balancing"
    range_start = 0
    range_end   = 100
    default     = 50


class Cadeauxsanity(Toggle):
    """
    Controls whether cadeaux (statue/altar) locations exist as AP checks at
    all.

    Off (default): cadeaux locations are excluded from the AP location pool
    entirely — same treatment as barrels. They stay untouched/vanilla (no
    checks, no hints, no "Cadeaux" AP item in the pool either). On: all
    ~657 cadeaux locations become real AP checks with no item-type
    restriction — any item, including other players' key items, can land
    there, and a "Cadeaux" filler item is added to the pool to match. May
    produce more difficult seeds since key items can end up further from a
    natural playthrough path.

    Soul (Govi/Dark Soul altar) locations are unaffected either way — they've
    always existed as checks eligible to hold any item type. Note this is a
    narrower scope than the standalone shadow-man-remastered-randomizer
    tool's own graded "insanity" tiers, which can also open up weapon/lore/
    bonus/barrel slots (barrels alone would add ~2,085 more locations) —
    that broader mode isn't implemented here.

    Renamed from "Insanity" to "Cadeauxsanity" (2026-08-23, Jon's request) —
    the old name collided in conversation/docs with the standalone
    randomizer's own, differently-scoped graded "insanity" tiers (see
    above); this option only ever controlled cadeaux, so the name says so
    now. Old YAMLs using `insanity:` need updating to `cadeauxsanity:`.
    """
    display_name = "Cadeauxsanity"
    default = 0


class CadeauxBundleSize(Range):
    """
    Groups cadeaux pickups into a single AP check instead of one check per
    cadeaux. Only meaningful when Cadeauxsanity is also on (cadeaux
    locations don't exist as AP checks at all otherwise).

    GLOBAL bundling (redesigned 2026-07-28, Jon's explicit call after
    seeing ~87 scattered sub-size remainder bundles from the original
    per-bucket design): every eligible cadeaux across the WHOLE game is
    shuffled together and chunked into groups of this size, with AT MOST
    ONE remainder bundle for the entire seed (e.g. 653 cadeaux at 5 ->
    130 bundles of x5, one bundle of x3) instead of one remainder per
    region/gate cluster. The tradeoff: a bundle's representative location
    can now sit behind a different gate than some of the cadeaux whose
    value it absorbed, so the reward economy's pacing no longer strictly
    tracks region/gate progression the way it used to (an early-game
    cadeaux can end up hollowed out to fund a payout that only becomes
    collectible much later, or vice versa). This does NOT affect AP's
    fill/logic correctness or seed completability -- every representative
    still gets its own individually-correct reachability rule from
    wherever it physically sits (see regions.py's
    compute_cadeaux_bundle_representatives()) -- it only changes how the
    cadeaux economy FEELS relative to game progression.

    1 (default): no bundling, same behavior as before this option existed
    -- every cadeaux-category location is its own AP check (~657 of them).
    Higher values cap chunk size at that value; one cadeaux per chunk is
    chosen at random (per-seed) to remain a real AP location, the rest
    stay physically untouched/vanilla cadeaux (same treatment as any
    other non-AP-tracked location) -- collectible in game for the normal
    vanilla altar/Fogometers cadeaux economy, just without their own
    individual AP check. E.g. a cap of 5 turns ~657 individual checks
    into ~131 bundle checks (130 at x5, 1 remainder).

    Random per seed (YAML "random", or "random-range-X-Y") is supported
    the same as any other Range option here.

    Shrinks how many "Cadeaux" AP items can ever exist in the pool to
    match the reduced bundle count -- generate_early()'s existing
    Fogometers-Cadeaux-Required deficit precollection (see
    FogometersCadeauxRequired's docstring) automatically scales to cover
    the larger gap this can create, same mechanism, no separate handling
    needed.
    """
    display_name = "Max Cadeaux Bundle Size"
    range_start = 1
    range_end   = 50
    default     = 1


class PistonCombos(Toggle):
    """
    Randomizes the 6 Dark Engine piston combination values (via EXE patch).
    Each piston normally sits at a fixed 3-digit combination; when this is
    enabled the combinations are rolled per-seed and the in-game journal
    (Jack's Schematic entry) is rewritten to show the new values.

    When enabled, Jack's Schematic (the item that unlocks that journal
    entry) becomes required progression — you must find it before you can
    learn the piston combinations needed to shut the pistons off and reach
    Legion (the final boss), guaranteeing it's always reachable before the
    game's completion condition. When off (default), the pistons stay at
    their vanilla combinations and Jack's Schematic is just a regular lore
    item like any other.
    """
    display_name = "Piston Combo Randomizer"
    default = 0


class UniqueRetractorKeys(Toggle):
    """
    Splits the 5 Retractors (the items that let you teleport from Deadside
    to a liveside region through its schism) into 5 distinctly-named items
    -- one per liveside region -- instead of one fungible "Retractor" stack.

    When enabled, each liveside region's schism requires its own specific
    Retractor rather than any 5 of the shared stack: London needs
    "Retractor - London", Prison needs "Retractor - Prison", and so on.
    Each is placed by Fill like any other progression item (your own world
    or another player's), so hints and other players' trackers show exactly
    which retractor is where and which region it opens -- not just a
    generic count. When off (default), all 5 liveside regions share the
    vanilla flat "any 5 Retractors" requirement.

    Ported from the standalone (non-AP) randomizer's own Unique Retractor
    Keys feature (2026-08-18) -- same exe patch/save-flag enforcement, same
    in-game Nettie's-file tracker.
    """
    display_name = "Unique Retractor Keys"
    default = 0


class TrapBonusCount(Range):
    """
    How many "Trap/Bonus" filler items are added to the pool this seed.

    Renamed from SecretTrapCount (2026-08-03) when this system grew past
    just cosmetic secrets — each received item now rolls a random effect
    from whichever categories are enabled (see TrapBonusSecretsEnabled/
    TrapBonusHealthEnabled/TrapBonusVoodooEnabled/TrapBonusAmmoEnabled):
      secret — a random cosmetic secret effect (Big Head, Wireframe,
               Disco Lights, etc.), the original Secret Trap behavior.
               Drawn only from secrets confirmed safe to toggle live via
               client.py's direct-memory-write approach (see CLAUDE.md's
               2026-07-31 "Generalizing past g_dogmode" writeup) --
               deliberately excludes secrets whose on-change callback
               hasn't been independently verified.
      health — poison (drains your whole health pool over ~1 min, ending
               in a real death if it isn't stopped by finding health
               first) or a gradual heal (restores well past full over the
               same window).
      voodoo — instantly drains voodoo power to 0, or pins it at its live
               cap (the Soul Level meter) for a while.
      ammo   — instantly drains Shotgun/Violator/9mm ammo to 0, or fills
               and holds them all at max for a while.
    See TrapBonusMode/TrapBonusDuration for how long the "hold"-style
    effects (secret, voodoo hold, ammo hold) stick around -- poison and
    heal have their own fixed internal duration regardless of those
    options, since they're one-shot processes rather than a toggle.

    0 (default) adds none, same as before this option existed. Random per
    seed (YAML "random", or "random-range-X-Y") is supported the same as
    any other Range option here.

    Capped at 1000 (2026-08-04, raised from 200, which itself was raised
    2026-07-31 from an arbitrary initial 30) -- the real ceiling is
    however many verified barrel-category locations exist to promote
    alongside them (~2000+, see generate_early()'s barrel_promoted_locs),
    so 1000 still leaves headroom without reaching that limit.
    """
    display_name = "Trap/Bonus Count"
    range_start = 0
    range_end   = 1000
    default     = 0


class TrapBonusMode(Choice):
    """
    Controls how long a Trap/Bonus's effect lasts once received, for the
    categories where "how long" is meaningful — secret, voodoo hold, and
    ammo hold. Poison and heal ignore this entirely (they're one-shot
    processes with their own fixed internal duration); voodoo/ammo drain
    are instant and have nothing to revert either.

    - always_temporary: reverts automatically after Trap/Bonus Duration
      seconds (the "for fun" default -- big head for a minute, unlimited
      voodoo for a minute, etc.).
    - always_permanent: stays on for the rest of the session once
      received (more of a curse-or-blessing than a temporary trap) --
      for secret, reverts on its own the next time you receive ANOTHER
      trap that happens to roll the same secret; for voodoo/ammo hold,
      keeps re-asserting indefinitely until superseded by that same
      category's drain effect.
    - mixed: each individual Trap/Bonus independently rolls temporary vs
      permanent when received, so a single seed can have both.
    Controlled at the seed level, same as every other randomized setting
    in this project.
    """
    display_name = "Trap/Bonus Mode"
    option_always_temporary = 0
    option_always_permanent = 1
    option_mixed            = 2
    default = 0


class TrapBonusDuration(Range):
    """
    Seconds a temporary Trap/Bonus effect lasts before automatically
    reverting (see Trap/Bonus Mode) — applies to secret, voodoo hold, and
    ammo hold. Only matters when a given trap actually goes temporary.
    Random per seed (YAML "random", or "random-range-X-Y") is supported
    the same as any other Range option here.
    """
    display_name = "Trap/Bonus Duration"
    range_start = 10
    range_end   = 300
    default     = 60


class TrapBonusSecretsEnabled(Toggle):
    """
    If enabled, received Trap/Bonus items can roll a random cosmetic
    secret effect (Big Head, Wireframe, Disco Lights, etc.). On by default
    to match this category's original (Secret Trap) behavior.
    """
    display_name = "Trap/Bonus: Secrets"
    default = 1


class TrapBonusHealthEnabled(Toggle):
    """
    If enabled, received Trap/Bonus items can roll poison (drains your
    whole health pool over ~1 min, ending in a real death if you don't
    find health first) or a gradual heal (restores well past full over
    the same window).
    """
    display_name = "Trap/Bonus: Health (Poison/Heal)"
    default = 1


class TrapBonusVoodooEnabled(Toggle):
    """
    If enabled, received Trap/Bonus items can roll an instant voodoo
    power drain to 0, or pin voodoo power at its live cap for a while.
    """
    display_name = "Trap/Bonus: Voodoo Power"
    default = 1


class TrapBonusAmmoEnabled(Toggle):
    """
    If enabled, received Trap/Bonus items can roll an instant ammo drain
    to 0 (Shotgun/Violator/9mm all at once), or fill and hold all three
    at max for a while.
    """
    display_name = "Trap/Bonus: Ammo"
    default = 1


class DeathLinkThreshold(Range):
    """
    How many of your own deaths it takes to send one Death Link. 1
    (default) sends on every death — the same behavior as before this
    option existed. 5 (the max) means only every 5th death of yours actually
    broadcasts to the rest of your team; the other 4 die "for free," with
    no Death Link sent. Only matters when Death Link is also on.

    Deaths caused by an incoming Death Link (i.e. someone else on your
    team died) never count toward your own threshold and are never
    filtered — you always die immediately when the team dies, regardless
    of this setting. This only throttles what you send out, not what you
    receive. See client.py's _health_watcher_loop() for the counter.
    """
    display_name = "Death Link Threshold"
    range_start = 1
    range_end   = 5
    default     = 1


@dataclass
class ShadowManOptions(PerGameCommonOptions):
    gate_preset:           GatePreset
    max_gate_sl:           MaxGateSL
    open_gates_n:          OpenGatesN
    entrance_mode:         EntranceMode
    # shuffle_gad_temples removed (2026-08-15): the "off" state was a real
    # correctness bug, not a supported mode. Gad temple locations were never
    # excluded from the AP location pool (regions.py's _SKIP_CATS never
    # covered "gad", unlike cadeaux/barrel), so AP's fill could place any
    # item there regardless of this option -- but ap_patcher.py's
    # write_placement_patches() silently discarded whatever was placed at a
    # gad temple slot and wrote a decorative barrel instead whenever this
    # was off, permanently losing that check. Best case a lost filler item,
    # worst case an unbeatable seed if a required progression item landed
    # there. Gad temples are now always treated as normal shuffled pickups
    # (the old "on" behavior, unconditionally) -- ap_patcher.py's
    # write_placement_patches()/apply_gad_pickup_patch() no longer branch on
    # it. A player who wants Gad Powers guaranteed from the start without
    # exploring for them should use start_inventory_from_pool instead
    # (already-supported standard AP mechanism, no custom option needed).
    shuffle_weapons:       ShuffleWeapons
    shuffle_lore:          ShuffleLore
    shuffle_bonus:         ShuffleBonus
    shuffle_enemies:       ShuffleEnemies
    deadside_guns:         DeadsideGuns
    enemy_mode:            EnemyMode
    enemy_mix_movement:    EnemyMixMovement
    enemy_uncap_counts:    EnemyUncapCounts
    shuffle_true_forms:    ShuffleTrueForms
    shuffle_ambients:      ShuffleAmbients
    ambient_mode:          AmbientMode
    shuffle_music:         ShuffleMusic
    shuffle_voices:        ShuffleVoices
    shuffle_weapons_sfx:   ShuffleWeaponsSfx  # was shuffle_weapon_sfx
    shuffle_enemies_sfx:   ShuffleEnemiesSfx
    combine_voice_and_enemy_sfx: CombineVoiceAndEnemySfx
    shuffle_sky:           ShuffleSky
    progression_balancing: ProgressionBalancing
    cadeauxsanity:         Cadeauxsanity
    cadeaux_bundle_size:   CadeauxBundleSize
    starting_health:       StartingHealth
    altar_health_grant:    AltarHealthGrant
    altar_cadeaux_required:      AltarCadeauxRequired
    fogometers_cadeaux_required: FogometersCadeauxRequired
    cadeaux_gated_content:       CadeauxGatedContent
    death_penalty:         DeathPenalty
    sprint_multiplier:     SprintMultiplier
    soul_threshold_mode:   SoulThresholdMode
    soul_logic_buffer:     SoulLogicBuffer
    piston_combos:         PistonCombos
    unique_retractor_keys: UniqueRetractorKeys
    # patch_tracker (formerly "In-Game Tracker Hints" / GUI "Teddy Bear
    # Hints") removed 2026-08-05 per Jon: turning it on could break AP
    # seeds (ap_patcher.py never actually applied the accurate-hints
    # patch for AP — see the 2026-07-22 note in that file — so the
    # option was already a no-op, but still exposed a checkbox that
    # looked functional and wasn't). The standalone (non-AP) randomizer's
    # own patch_tracker option in patcher.py/gui.py is unaffected and
    # still fully functional — this removal is AP-only. ap_patcher.py's
    # own levels.txt step always behaves as if this were off (strips
    # hints, vanilla-safe) regardless, so no patcher behavior changed.
    trap_bonus_count:            TrapBonusCount
    trap_bonus_mode:             TrapBonusMode
    trap_bonus_duration:         TrapBonusDuration
    trap_bonus_secrets_enabled:  TrapBonusSecretsEnabled
    trap_bonus_health_enabled:   TrapBonusHealthEnabled
    trap_bonus_voodoo_enabled:   TrapBonusVoodooEnabled
    trap_bonus_ammo_enabled:     TrapBonusAmmoEnabled
    # game_dir (GameDir FreeText option) removed 2026-07-21 — the "hybrid"
    # immediate-local-patch path it drove (worlds/shadowman/patcher.py) had
    # fallen far behind ap_patcher.py's feature set (entrance/piston/asset/
    # insanity-FX/Book-of-AP patches all missing from it) and was no longer
    # worth maintaining as a second patcher. Every seed now goes through the
    # portable .apshadowman + apply_ap_seed.py workflow only — see
    # generate_output()'s docstring in __init__.py.
    death_link:            DeathLink
    death_link_threshold:  DeathLinkThreshold
    start_inventory_from_pool: StartInventoryPool