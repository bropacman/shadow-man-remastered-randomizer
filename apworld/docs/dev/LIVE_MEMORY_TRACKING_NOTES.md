# Live memory tracking — design notes (2026-07-14)

Working notes for replacing save-file-based location detection with live
process-memory reads. Captured immediately after the investigation so the
reasoning chain isn't lost — this took a full session of live Cheat Engine
debugging + Ghidra cross-referencing to nail down, and several dead ends
along the way are recorded here deliberately so they aren't repeated.

## Why we're doing this at all

Save-file polling (`client.py`'s original approach, still in place for
QuestObject-based items) has two real problems for Govi/dark-soul detection
specifically:

1. **Latency**: detection is gated by whenever the game next writes the
   save file (autosave/checkpoint), not by when the player actually
   collects something. Could lag noticeably behind the real event.
2. **Precision vs. jitter**: a govi's position is NOT static — it's
   actively animated by a per-frame chain/sway physics simulation (see
   below). A position sampled at an arbitrary moment (whether from a save
   file or live memory) can be tens of world units away from
   `locations.csv`'s canonical rest coordinate. This was root-caused live
   and is why `GOVI_POS_TOLERANCE` was loosened from 0.05 to 100.0 units in
   `client.py` (see that constant's comment for the full story).

Switching to live memory reads fixes problem (1) directly (no dependency on
save-write timing) and doesn't make problem (2) any worse (jitter is
inherent to the object, not an artifact of save-file staleness) — the same
wide-tolerance + ambiguity-margin matching approach already built for the
save-file path applies unchanged.

**Constraint from Jon (2026-07-14):** the live tracker must work for BOTH
vanilla and randomized seeds. The patcher (shared by the standalone
randomizer and this AP world's `generate_output()`) can retype ANY location
slot into a govi (or vice versa) depending on fill result — confirmed
empirically via `tools/diagnose_govi_position_offset.py`, which matched
live-collected souls against CSV rows categorized "barrel" and "cadeaux",
not just "soul". So position-matching must always run against the FULL,
unfiltered `locations.csv` (all categories), never a category-prefiltered
subset — same principle already implemented in the save-file matcher
(`_match_govi_position_scan` in `client.py`).

## Methodology that got us here (reusable pattern)

Static Ghidra analysis alone couldn't find any of this — indirect/virtual
calls don't generate normal function-level Xrefs, and guessing byte offsets
from a decompiled save-parser dead-ended repeatedly. What worked:

1. Set a **hardware write-watch** in Cheat Engine on a known live memory
   address (here: the Dark Soul counter global, `thoth_x64.exe+F9BEC8`,
   already documented in `client.py`'s MEMORY MAP). Right-click the address
   in the main CE window's address list → "Find out what writes to this
   address" — NOT "browse this memory region" / disassemble (that just
   shows the DATA address misread as code, a dead end we hit first).
2. Trigger the event in-game. The watch popup fills with the actual
   instruction address that performs the write.
3. Set a REAL breakpoint at that instruction (Debug menu → Toggle
   Breakpoint), trigger again, and this time the game actually freezes.
   Read the **Registers** and **Call Stack** panels while still paused —
   do NOT hit Run before copying values down, or the snapshot is lost
   (learned the hard way; also: one attempt at breakpointing a very hot
   per-frame path froze the whole computer, not just the game — pick
   breakpoint locations conservatively, and prefer read-only polling over
   breakpoints/hooks for anything in a per-object per-frame update path).
4. Walk UP the call stack from the write instruction, frame by frame,
   pasting each frame's decompiled function into chat, until landing on a
   function that holds a meaningful object pointer (not just a global
   counter). Ghidra's decompiler variable naming (`param_1`, register
   names in disassembly) reliably tracks which register holds which
   value at a given instruction — cross-referencing the disassembly
   listing against the registers captured at a breakpoint is what
   resolved every ambiguity here.
5. To find a function's owning CLASS/vtable (needed for heap-scanning):
   check that function's **Xrefs**. A `(*)` reference means "used as DATA"
   — i.e., stored as a function pointer, almost always inside a vtable
   array. Jump to that address in Ghidra's **Listing/Data view** (not the
   decompiler) to see the array and its label — Ghidra auto-labels
   RTTI-recognized vtables as `ClassName::vftable`.

## What we found

### The write we hooked

`thoth_x64.exe+2DFCE7`: `mov [r14+08], r10d` — increments the global Dark
Soul counter (`r14` = `base+0xF9BEC0`, target = `base+0xF9BEC8`, matches
the already-documented counter). This instruction itself is several calls
removed from any govi-specific object — not useful on its own, but its
call stack led everywhere else below.

### The Think() function

`FUN_1402dc040` (RVA `0x2DC040`) — a large per-object per-frame update
function. Confirmed via live registers that its single argument (in `RDI`
inside the function body — MSVC moves `RCX` into `RDI` at entry) is NOT
the polymorphic DarkSoul object itself (see below), but has these
confirmed fields (offsets relative to its own `param_1`/`RDI`):

- `+0x0` : position, `(x, y, z)` as three consecutive float32 LE — this is
  what's continuously jittered by the chain/sway simulation.
- `+0x20B8` : collected/opened state byte (0 = intact, 1 = opened).
  Confirmed live via disassembly: `1402dc9a1: MOV byte ptr [RDI+0x20B8],0x1`
  — this instruction only executes once the player-proximity check earlier
  in the function passes (see "trigger logic" below).
- `+0x40C` onward: a 10-segment array used by the jitter/chain-physics
  simulation (irrelevant to detection, just context for why position
  moves).
- Calls `FUN_140458680()` (the already-known player-pointer accessor, same
  one used for DeathLink) to get the player's own position at `+0x20/
  +0x24/+0x28` for the proximity check — note the PLAYER's own position
  fields are at a DIFFERENT relative offset (`+0x20` etc.) than the govi
  data struct's position (`+0x0`). Different object layouts; don't assume
  they match.

**Trigger logic** (why the state flag flips): computes squared distance
between player and `param_1`'s own position using a fast inverse-sqrt bit
trick, checks it against a ~80-unit radius, checks the player isn't in a
blocked camera mode (`kexShadowManCameraMgrLocal` check), and checks two
"already triggered" guard fields before flipping `+0x20B8` and playing an
effect (`FUN_1403e9cb0(0x14, ...)`, sound/particle ID 20).

### The real class and object layout (the key discovery)

`FUN_1402dc040` has vtable slot references AND a direct-call reference.
Vtable slot `[4]` (offset `+0x20`) of **`kexShadowManDarkSoul::vftable`**
(absolute address `0x1406EA838`, i.e. **`thoth_x64.exe+6EA838`**) points to
it — this is the real class name, NOT `kexShadowManAIGovi` (that class
exists too, found earlier via its own constructor `FUN_1403926a0`
`kexShadowManAIGovi::vftable` at RVA `0x70C400`, but it is a DIFFERENT,
apparently unrelated object — likely the visual jar/shell actor, separate
from the actual soul reward entity. Not yet confirmed how or if these two
relate; only `kexShadowManDarkSoul` has been confirmed to hold the
position/state fields we need).

The direct-call reference resolved the object-layout mystery: `FUN_1402dc040`
is also called directly (non-virtually, presumably for per-frame perf) from
`FUN_1402de490`, whose relevant code is:

```c
lVar3 = *(longlong *)(param_1 + 0x30);
if (*(int *)(lVar5 + 0x94) == 0) {
    FUN_1402dc040(lVar3);
    return;
}
```

So **`param_1` here is the true polymorphic `kexShadowManDarkSoul` instance**
(vtable pointer at its own offset 0, per normal C++ ABI — confirmed
indirectly, since the raw bytes we read at the OTHER pointer's offset 0
were clearly NOT a valid pointer, ruling out that one being the polymorphic
object). `lVar3` — captured live as `RDI` inside `FUN_1402dc040` — is a
plain, non-polymorphic data sub-struct reached via `soul_obj + 0x30`. That
sub-struct is what actually holds position and collected-state.

(There's also a second mode, `*(lVar5+0x94) == 1`, with heavy inline
particle/effect-spawning code — looks like the "soul flying toward the
player" animation phase after collection. Not needed for detection, noted
for completeness in case it matters later — e.g. if detection should
trigger on this transition instead of/in addition to the `+0x20B8` flag.)

## The confirmed recipe

```
soul_obj   = <address found via heap-scan for kexShadowManDarkSoul::vftable @ thoth_x64.exe+6EA838>
pos_ptr    = read_int64(soul_obj + 0x30)
x, y, z    = read_float32, read_float32, read_float32  starting at (pos_ptr + 0x0)   # 12 bytes
state      = read_byte(pos_ptr + 0x20B8)   # 0 = intact, 1 = opened
```

Cross-validated two independent ways: (a) a live read at this layout for a
real collected soul matched `locations.csv`'s "Govi - Dark Soul 58" in
`t1tchgad` within ~20 world units (consistent with jitter magnitude); (b)
the SAME location was independently found by the offline save-file
diagnostic tool at a completely different byte offset (`+777` relative to
the `kexShadowManAIGovi` class-name string in the KSAV save format) with
much tighter precision (~0.005 units, because that save had sat settled).
Two unrelated data sources agreeing on the same real-world object is about
as strong a confirmation as this kind of reverse engineering gets.

## Remaining work (not yet started)

1. **Heap-scanning implementation**: write a pymem-based routine that walks
   the process's memory regions (via `VirtualQueryEx`-equivalent) and finds
   8-byte-aligned occurrences of `0x1406EA838` (the vtable pointer value —
   note: this is a static address assuming the module loads at its
   preferred base `0x140000000`; needs the actual runtime module base
   added if ASLR relocates it, same handling already used elsewhere in
   client.py for `module.exe+RVA`-style addressing). Each hit is a live
   `kexShadowManDarkSoul` instance.
2. **Polling loop integration**: wire the enumerated instances into
   `client.py`'s existing poll loop (same `POLL_INTERVAL`), tracking state
   per `pos_ptr` (or per `soul_obj`) across polls the same way
   `_slot_govi_states` already does for the save-file path, and reuse
   `_match_govi_position_scan`'s CSV-matching + ambiguity-margin logic
   unchanged (position jitter and the "any category could be a govi"
   requirement apply identically here).
3. **Level-change handling**: `soul_obj`/`pos_ptr` addresses become invalid
   once a level unloads (heap gets freed/reused) — the scan needs to
   re-run periodically, not just once, and stale/dead addresses need to be
   dropped safely (guard against reading freed memory — validate the
   vtable pointer is still intact at `soul_obj+0` before trusting a
   previously-found address, or simply re-scan from scratch every poll
   cycle if performance allows).
4. **Key items / QuestObject-equivalent**: Jon's requirement is this needs
   to cover ALL trackable categories, not just souls. We have NOT yet found
   the equivalent vtable/class/layout for `kexShadowManQuestObject`-based
   pickups (progression, weapons, lore, cadeaux). Same methodology applies:
   find a QuestObject-specific memory write (state-change trigger) via a
   CE watch, walk the call stack, find its class's vtable via Xrefs.
   **UPDATE (2026-07-18): partially solved for one-of-a-kind progression
   items via a different mechanism — inventory possession, not
   QuestObject/SaveIdx at all. See "Inventory possession tracking" section
   below.** QuestObject's own live layout (needed for weapons/lore/cadeaux
   generally) is still not found.
5. **Decide save-file vs. live-memory split**: current thinking is to keep
   the save-file-based `_identify_level` for level-scoping purposes (low
   frequency, staleness doesn't matter there) while replacing only the
   actual govi/item state+position detection with live memory. Worth
   reconsidering once QuestObject's live layout is known — might make sense
   to fully retire the save-file poll loop if live memory covers
   everything.

## Update (2026-07-18): inventory possession tracking for one-of-a-kind items

### Why this exists — SaveIdx isn't usable for every item

While fixing a real bug in `tools/extract_instance_ids.py` and `patcher.py`
(both were reading/writing the RSC `SaveIdx` field as a truncated 1-byte
value at `+0x21` instead of the real 4-byte big-endian field at `+0x1E` —
see `patcher.py`'s `SAVE_IDX_OFF` comment for the full story, standalone
repo's `TECHNICAL.md` §10.4 is the authoritative reference), we confirmed
against the standalone repo's known-good `data/locations.csv` that several
one-of-a-kind progression items (Baton, Calabash, Engineers Key, Flambeau,
Marteau, Prison Key Card, the three Eclipser pieces) genuinely have
`SaveIdx = 0` in the shipped RSC files — not a bug, just how the game ships
them. These items apparently aren't tracked via a per-object save slot ID
at all; the RSC-record/SaveIdx approach (and therefore instance_id/position
matching generally) can never work for them, confirmed not guessed.

Jon's own theory (stated before this was confirmed): these items are
tracked by inventory possession, not by a world-object save slot. Correct —
and there's a much better way in than QuestObject reverse-engineering: the
game's own `giveinv <N>` debug console command already exists and gives any
of ~30 named items by a small dense ID (community-documented list, ID → item
name, e.g. `3` = Baton, `6` = Calabash, `9` = Engineer's Key, `11` =
Flambeau, `16`/`17`/`28` = the three Eclipsers, `18` = Marteau, `21` = Key
Card, `20` = Prism, `23` = Retractor, `1` = Accumulator). `client.py`
already calls into the same underlying function for item injection
(`inject_give_item()` / `_build_give_item_shellcode()`,
`kexShadowManInventoryLocal::GiveItem(this, item_id_u16, flags_i32)` at
vtable slot `+0x30` off the singleton at `INVENTORY_RVA = 0xF9C380`) — so
`giveinv` almost certainly calls this exact function, and reversing
`GiveItem` itself (rather than a save-parser) was the fast path.

### Methodology

Breakpointed `GiveItem`'s entry directly (resolved via the known vtable
chase — no CE hardware-watch/call-stack-walk needed this time, since the
function address was already known from the injection code):

1. Address of `GiveItem` itself: read the QWORD at
   `thoth_x64_patched.exe+F9C380` (that IS `this` — see layout note below,
   no extra pointer dereference needed) to get the vtable pointer, then read
   the QWORD at `vtable+0x30` to get `GiveItem`'s real address.
2. Set a breakpoint at that address, run `giveinv <N>` in the live console,
   read the disassembly with the debugger paused.
3. Once the DWORD array (below) was identified, set breakpoints on its two
   write sites instead, run `giveinv <N>` for each target item, and read
   `RBX` off the register panel at the breakpoint to get that item's
   resolved array slot index.

**Pitfall hit and worth recording:** Cheat Engine's register panel displays
values in hex with no `0x` prefix. First attempt at reading RBX off the
panel was misread as decimal (`11` taken to mean eleven instead of `0x11` =
17), which pointed at the wrong array slot and read back `0` instead of the
expected `3`. Always treat CE register-panel values as hex unless the panel
is explicitly configured otherwise.

### Object layout (`kexShadowManInventoryLocal`, confirmed via disassembly)

`this` = `thoth_x64_patched.exe+F9C380` directly — this is a static
singleton object, not a pointer-to-object; the QWORD stored AT that address
is the object's own vtable pointer (standard C++ layout, vtable ptr is
always a live object's first field). No extra dereference needed to reach
`this`, unlike the DarkSoul heap-scan case.

```
this + 0x08 : DWORD[30]   per-item possession state, one slot per game item
this + 0x80 : WORD        Prism count       (item_id 20 — counted item)
this + 0x82 : WORD        Retractor count   (item_id 23 — counted item)
this + 0x84 : WORD        Accumulator count (item_id 1  — counted item)
```

The `0x08 + 30×4 = 0x80` boundary lines up exactly with where the three
counters start — strong internal-consistency confirmation this is the
right array bounds.

Confirmed possession-state values for a DWORD slot (live-toggled and
observed in-game, not just inferred from the write instruction):

| Value | Meaning |
|-------|---------|
| 0     | not owned / removed from inventory |
| 1     | owned, equipped in left hand |
| 2     | owned, equipped in right hand |
| 3     | owned, in inventory (not equipped) |

`GiveItem`'s disassembly only ever writes literal `3` (plain "you now own
it" grant) — states 1/2 are written elsewhere (equip logic, not reversed),
but are relevant for us to know about since a naive `!= 0` check is
sufficient for "has been collected at all," which is all AP location
detection needs — equip state doesn't matter for that purpose.

### Item ID → array slot index is NOT 1:1

`GiveItem` resolves `item_id` (passed in `RDX`, confirmed live via the
`item_id 25 aliases to 7` remap in the disassembly — matches `giveinv`'s own
list where both `7` and `25` give "Handgun") to an array slot index via a
lookup table at `thoth_x64_patched.exe+C9AF00` (30 entries × 32 bytes,
first WORD of each entry = the item_id it matches). The resolved index
(`RBX` at the point of the write) is what actually indexes the DWORD array
— it is NOT the same number as `item_id` itself. There's also a secondary
lookup path (second breakpoint site, `+309745` vs. the primary `+30964B`)
used when an item's first-matching slot is already occupied — relevant for
items with more than one copy (confirmed: item `30`, Violator, per
`giveinv`'s own note "gives the player the second violator if you type it
again" — has two table entries, two array slots). Not relevant for the
one-of-a-kind progression items this section is about, but worth knowing if
this table ever needs revisiting.

### Confirmed recipe (Baton, fully validated live)

```
this        = thoth_x64_patched.exe+F9C380
slot_index  = 0x11   (RBX at the GiveItem write breakpoint for item_id 3)
address     = this + 0x08 + (slot_index × 4)   = thoth_x64_patched.exe+F9C3CC
```

Live-toggled at that exact address: `0` removed Baton from inventory, `1`
equipped it left hand, `2` right hand, `3` returned it to plain "owned"
state — full round-trip confirmation, not just a one-directional read.

### Full lookup table dump (`thoth_x64_patched.exe+C9AF00`, 30×32-byte entries)

Rather than breakpointing every item individually (breakpointing repeatedly
destabilized the game — got real crashes after 3-4 hits, consistent with
this doc's existing caution about breakpoints under load), the remaining
items were resolved from a single static dump of the lookup table `GiveItem`
uses internally (`r13` in the disassembly, 30 entries × 32 bytes, first
DWORD of each entry = the `item_id` it matches — confirmed by cross-checking
entry index 17 = item_id `3` against Baton's already-live-confirmed
`RBX=0x11=17`, exact match). Entries 15 and 23 both hold item_id `30`
(Violator) — matches `giveinv`'s own note about a second Violator on
repeat use. Three entries (19, 21, 24) hold large out-of-range values,
purpose unknown, not relevant to any item below.

`slot_index` = table entry index (0-based). Address formula, same as
Baton's: `this + 0x08 + (slot_index × 4)`, `this = thoth_x64_patched.exe+F9C380`.

### Confirmed items — progression/key (SaveIdx=0, one-of-a-kind)

| Item | item_id | slot_index | Address | Status |
|------|---------|------------|---------|--------|
| Baton | 3 | 17 (0x11) | `F9C3CC` | live-confirmed (0↔1↔2↔3 round-trip) |
| Calabash | 6 | 22 (0x16) | `F9C3E0` | from table dump |
| Engineers Key | 9 | 10 (0x0A) | `F9C3B0` | from table dump |
| Flambeau | 11 | 11 (0x0B) | `F9C3B4` | from table dump |
| Eclipser: La Lune | 16 | 26 (0x1A) | `F9C3F0` | from table dump |
| Eclipser: La Lame | 17 | 28 (0x1C) | `F9C3F8` | from table dump |
| Marteau | 18 | 16 (0x10) | `F9C3C8` | from table dump |
| Prison Key Card | 21 | 14 (0x0E) | `F9C3C0` | from table dump |
| Eclipser: Le Soleil | 28 | 27 (0x1B) | `F9C3F4` | from table dump |

Poigne (also SaveIdx=0, also progression) does NOT need this mechanism —
already tracked via the existing `POIGNE_RVA = 0xF9C1A4` byte flag in
`client.py`'s MEMORY MAP. Same for the three Gad Pickup records — existing
`GAD_1..4_RVA` counters cover them.

### Confirmed items — weapons/lore (also SaveIdx=0, also one-of-a-kind)

Jon confirmed "Jack's Schematics" (CSV) and "Jack's Journal" (`giveinv`
list) are the same in-game item — that mapping below is trusted.

| Item | item_id | slot_index | Address |
|------|---------|------------|---------|
| Jack's Journal / Schematics | 24 | 2 | `F9C390` |
| Book of Shadows | 4 | 3 | `F9C394` |
| Enseigne | 10 | 6 | `F9C3A0` |
| Asson | 2 | 7 | `F9C3A4` |
| 0.9-SMG | 12 | 8 | `F9C3A8` |
| Flashlight | 14 | 9 | `F9C3AC` |
| Handgun | 7 | 12 | `F9C3B8` |
| Shotgun | 26 | 13 | `F9C3BC` |
| Violator (Loose) | 30 | 15 | `F9C3C4` |
| MP-909 | 13 | 18 | `F9C3D0` |
| Violator (Accumulator Reward) | 30 | 23 | `F9C3E4` |
| Book of Prophecy | 22 | (not yet pulled — same lookup, TODO) | — |

### Explicitly excluded — cannot use this mechanism

Accumulators (5 world locations) and Retractors (5 world locations) are
*counted*, not individually tracked — `WORD` fields at `this+0x84` /
`this+0x82` respectively. Inventory only reports a running total, not which
specific world location produced the Nth one. These need real per-object
tracking (position-matching, same category as souls/cadeaux/barrels), not
this trick. Prism (`this+0x80`) is the same story if/when it needs tracking.

### RESOLVED (2026-07-18): all 30 array slots identified

Jon swept every slot address (`thoth_x64_patched.exe+F9C388` through
`+F9C3FC`, all 30, 4 bytes apart) live in Cheat Engine and read off the
real item name at each. Full table now known — canonical source of truth
is `data/inventory_item_offsets.csv` (not duplicated here to avoid drift;
update the CSV, not this doc, when anything changes).

Confirms: Sawed-off Shotgun and Tete De Mort each have their own distinct
slot (not shared with anything else). Turned up three items not seen
anywhere before — Nettie's File, Teddy Bear (`giveinv 29`), Bloody Teddy
Bear — none with a known `locations.csv` row yet. And **Light Soul is
confirmed NOT in this array at all** (full sweep, no match) — it's tied to
some other mechanism (`cadeaux_666`/Fogometers reward logic in
`cadeaux_patch.py` is the leading guess), needs its own separate
investigation whenever that's prioritized.

A few slots (Nettie's File, Sawed-off Shotgun, Tete De Mort, Bloody Teddy
Bear) have confirmed-live addresses but an internal table item_id that
doesn't look like a normal `giveinv`-reachable 1-30 value — doesn't matter
for detection (all we need is the address), only would matter if injection
into these specific items is wanted later.

### Next step

Wire a poll into `client.py` reading each confirmed address from the CSV
as a DWORD (`!= 0` = collected) the same way `_slot_govi_states` tracks
per-object state today, and add the addresses to `client.py`'s top-of-file
MEMORY MAP docstring. Skip the `counted`/`existing-mechanism`/`unresolved`
rows in the CSV — those need a different approach (position-matching for
multi-copy items, already-implemented byte flags for Poigne/Gad, and
further investigation for Light Soul) rather than this poll.

## Update (2026-07-22): cadeaux HUD meter / achievement subsystem — traced, NOT usable for per-slot identity

While independently chasing "make cadeaux tracking as reliable as souls," a
CE breakpoint on `CADEAUX_COUNT_RVA` (`thoth_x64_patched.exe+DB221C`) led
into a fully separate system from the item-pickup event log documented
above. Recorded here so nobody re-walks this path expecting per-slot
identity out of it.

**`kexShadowManMeterLocal::vftable`** (`thoth_x64_patched.exe+700B48`,
24 virtual slots) backs a single global instance at
`thoth_x64_patched.exe+DB21E0`. Slot `[5]` (`FUN_14032d120`,
`RVA 0x32D120`, vtable offset `+0x28`) is a generic 10-sub-stat
accumulator, dispatched by an `EDX` index (0–9) via a jump table at
`+0x32D420`. Slot index `8` (struct offset `+0x3C`) is the cadeaux HUD
counter, capped at `0x29a` (666) — this exactly matches the
`meter_cap_cmp` (`0x32D3E2`), `meter_cap_mov`/`meter adjuster`
(`0x32D3ED`), and `meter_add` (`0x32D3F2`) offsets already hardcoded in
`cadeaux_patch.py`'s `CADEAU_TOTAL_OFFSETS`/docstring — good independent
confirmation those constants are correct, found by two unrelated methods.

The caller, `FUN_1403397d0` (`thoth_x64_patched.exe+3397D0`), is the full
cadeaux-collect handler: game-state guards → AABB containment test
(player vs. the trigger volume at `[RSI+0x30]`) → a save-manager dedup
check (`FUN_14033f7b0`, keyed on `[RBX+0xD0]` + a companion pointer at
`[RBX+0x38]`) that gates the grant → `meter->Add(8, 1)` via the vtable
slot above (delta is **hardcoded to 1**, always — never variable; an
earlier `+3` observation was three separate pickups landing between two
manual memory-viewer reads, not one triple-grant) → a pickup-sound call →
inventory/achievement-progress bookkeeping that unlocks `"ACH_CADEAUX"`
once its internal counter crosses a threshold.

**Why this is a dead end for identity:** live-tested via a real multiplayer
session, the `edx_val` field captured by the *actual* item-pickup event
log (`FUN_14033f510`, `ITEM_PICKUP_COUNTER_RVA`, documented above) turned
out to be the object's **zone number** (e.g. `ah3lavad` zone 3 or zone 5),
not a unique per-object ID — two confirmed-different cadeaux in the same
zone produced the same `edx_val`. `r9d_val` is `1002` (`0x3EA`) for every
single cadeaux, also useless for disambiguation. Whether `[RBX+0xD0]` from
`FUN_1403397d0`'s dedup check is the same underlying value as the logged
`edx_val`, or a genuinely different (possibly more useful) per-object
index, was **not confirmed live** — the two functions have different
addresses (`F7B0` vs. `F510`) and may or may not share data. Do not assume
`[RBX+0xD0]` is a usable save index without testing it directly; until
then, the existing position-matching system (item-pickup log +
`locations.csv` coordinate match) remains the correct/only confirmed
mechanism for cadeaux per-slot identity, and it already works (confirmed
live, 5/5 resolved correctly in a real session on 2026-07-22).

Position-matching is also confirmed safe under randomization: cadeaux
reward shuffling in this project patches the granted `item_id`/Gad-flag at
the fixed script call site (see `docs/shadowman_rando_findings.md`'s
"cadeaux — special case" section), not the object's `x,y,z` — world
geometry is seed-invariant, only what's awarded per slot changes.

## Update (2026-07-22): Light Soul — SOLVED

Confirmed live. Not in the 30-slot inventory array (per the 2026-07-18
sweep), and not a `QuestObject` at all — `save_idx=0` in
`data/locations.csv`, same category as the other one-of-a-kind
progression items, but tracked through neither of those mechanisms.

Found via the `givelightsoul` debug console command rather than a
pickup-write breakpoint. Command dispatch table entry at
`thoth_x64_patched.exe+C9CBF0` (`"givelightsoul"` string) points to a
handler function pointer at `+0x10` → `FUN_140327410`. That function,
after a cheat-mode-enabled check, calls the same global
`kexShadowManMeterLocal` instance (`DAT_140db21e0`, see the meter
subsystem section above) through a *different* virtual method than the
stat accumulator — vtable slot `[21]` (`+0xA8` → `FUN_14032b360`),
passing index `3`.

`FUN_14032b360` is itself a 10-way dispatcher over one-off boolean flags
living just past the stat array (which ends at `+0x40`), starting at
`+0x44`. Indices `0–5` all collapse onto the same case:

```
MOV byte ptr [RCX+0x44], 1
MOV dword ptr [RCX+0x8], 0xB4   ; shared HUD-notification timer, not state
```

**`thoth_x64_patched.exe+DB2224`** (byte) = Light Soul possession flag.
`1` = granted, `0` = not. **Confirmed live 2026-07-22**: flips `0→1` when
`givelightsoul` runs.

Caveats:
- Indices `0–5` all write this same byte — it's shared across whatever
  other debug grants map to those indices (plausibly other
  invincibility-adjacent cheats), not exclusively a "Light Soul" bit by
  design, just functionally equivalent for our purposes. Indices `6/7/8/9`
  have their own distinct bytes (`+0x45`/`+0x46`/`+0x48`/`+0x47`) if any
  of those ever need tracking later.
- The flag self-heals: manually clearing it to `0` via Cheat Engine
  reverts to `1` shortly after. This means something re-derives it from a
  canonical condition (almost certainly the real `CADEAUX_666` completion
  state) every tick, rather than it being a one-shot write — good news for
  polling (always valid, no race with a transient event) but it can't be
  used to test an "un-grant" path.

**Wired in (2026-07-22)**: `LIGHT_SOUL_FLAG_RVA` (`0xDB2224`) is polled
every tick by `_poll_live_light_soul`, called from the same place as
`_poll_live_inventory` in `client.py`. Resolved to an `ap_id` via a new
`LIGHT_SOUL_LOC_KEY` direct lookup in `_build_location_map` (bypasses
`_loc_map`'s instance_id keying, since Light Soul's save_idx=0 excludes it
from that path — same reasoning as every other zero-save_idx item, but
there's only one Light Soul location in the whole game so a hardcoded
loc_key is safe rather than needing the general fix). Direct byte read,
no heap-scan, no save-file dependency — same shape as the dark-soul
flag-array watcher.
