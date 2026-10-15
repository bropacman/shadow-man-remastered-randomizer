# Scope: "N of 6 pistons required" via CF_DARKENG_STOPPED1..6

Status: research only, nothing built. Date: 2026-10-15. Picks up the
CLAUDE.md §11 open thread and the 2026-08-06 plan in the history archive
("Next Up: N of 6 pistons required"), which was blocked on finding the
piston completion flags.

## Recommendation

**Build it as a small static exe patch to `kexShadowManPistonControl`,
plus a count-based logic rule. No client writes.**

Once N pistons are stopped, every remaining piston control that ticks
treats its bars as solved and runs its *own* vanilla stop sequence:
animation, sounds, and `SetFlag(CF_DARKENG_STOPPEDn)`. Downstream, the
game sees six natural solves. This works for the standalone and AP builds
alike, needs no client, no timing gate and no level reload, and doesn't
require knowing exactly what triggers the Legion floor collapse.

## Findings (vanilla exe, read-only disassembly)

### The flags are the piston completion flags

`CF_DARKENG_STOPPED1..6` = indices 98–103 (name table `0x140ca2a90`).
They are set by **`kexShadowManPistonControl`** (vtable `0x140720178`,
main tick = slot 4, `0x140423850`):

- A local table `{0, 98, 99, 100, 101, 102, 103}` is loaded from
  `0x14072e5d0` and indexed by the control's piston id `[this+0x294]`
  (1–6).
- **State 2 (active):** `0x140423a9e` checks the three linked
  `kexShadowManPistonLevel` bars (`[this+0x298/0x2a0/0x2a8]->+0x30`, byte
  `+0x29c` = "bar at target"). When all three match, it sets state 3,
  plays sounds `0xfd`/`0xfe`, and at `0x140423b5e` calls
  `SetFlag(table[id])`.
- **State 3 → 4:** the stopping animation finishes, sets `[target+0x240] = 1`
  on the object at `[this+0x2b0]`, and calls `0x140422d70`, `0x140331570`
  and `0x1403330f0`.
- **State 0 (level load):** at `0x1404240e0` it does
  `GetFlag(table[id])`. If set, it snaps all three bars to their targets,
  goes straight to state 4, and runs **the same** `[target+0x240] = 1` /
  `0x140422d70` / `0x140331570` / `0x1403330f0` tail. A flag-stopped
  piston is therefore indistinguishable from a just-solved one after a
  reload.

Per-bar solving (`kexShadowManPistonLevel::Execute`, `0x140424510`) is
the combination logic `dark_engine_patch.py` already patches. Its piston
id is `instance/10` (1–6) and its bar is `instance%10` (0–2).

### SetFlag side effects for 98–103

These indices share `SetFlag`'s special case with 67–69: save-limit
bookkeeping only (`0x140342c60`, `CF_SAVE_LIMIT*` 332/333). Nothing
gameplay-related. See `docs/scope_gad_ring_flags.md`.

### Nothing in the exe requires "all six" for gameplay

The only immediate readers of 98–103 are the objectives UI:
`FUN_140305a10`'s requirement bit 9 checks all six, and `FUN_140306180`
reads `98 + n` per piston. They're display-only (`$m_obj_*`,
`shadowmanplayer.cpp`). No code loops over or counts them. The other
`mov edx, 0x62..0x67` hits are unrelated calls.

The actual Legion gate:

- `kexShadowManAITFLegion` (true-form Legion) reads `CF_A4_FLOORGONE`
  (142), and `kexShadowManAS4FakeFloor` reads `CF_A4_FLOORHIDE` (177) and
  `CF_A4_FLOORGONE`.
- Both are set by `cutscene/scripts/as4dkeng/trans0259.cut` (the floor
  collapse). Its cutscene-table entry has no code predicate, so whatever
  starts it is level data (events/links), downstream of the piston
  controls' stop sequence. I didn't trace that trigger statically.

That last point is why the patch makes the remaining pistons stop *for
real*, rather than just setting flags: whatever aggregates the six stops
sees exactly the vanilla sequence.

### Piston order (to confirm in game)

`STOPPEDn` is keyed by `kexShadowManPistonControl[+0x294]`. If that id
matches the `PistonLevel` table id (very likely), then using
`dark_engine_patch.py`'s table order: STOPPED1 = cable car (5-5-5),
STOPPED2 = Milton Pike, STOPPED3 = Marco Cruz, STOPPED4 = Jack the Ripper,
STOPPED5 = Avery Marx, STOPPED6 = Victor Batrachian. The N-of-6 design
doesn't depend on this; it only matters for a per-piston tracker display.

## Design

### Exe patch (`exe_patches/pistons_required_patch.py` + AP copy)

Hook the state-2 bar check at `0x140423a9e` (file `0x422E9E`). It's 72
bytes, three identical blocks:

```
mov rax, [rdi+0x298]  ; mov rcx, [rax+0x30] ; cmp byte [rcx+0x29c], r15b ; je 0x1404243d8
mov rax, [rdi+0x2a0]  ; ...                                                ; je 0x1404243d8
mov rax, [rdi+0x2a8]  ; ...                                                ; je 0x1404243d8
; fall through to 0x140423ae6 = "piston solved" path
```

Vanilla bytes:
`488b8798020000488b48304438b99c0200000f8422090000`
`488b87a0020000488b48304438b99c0200000f840a090000`
`488b87a8020000488b48304438b99c0200000f84f2080000`

Replace the first instruction with `jmp cave` (+ NOPs). The cave:

```
  ; original three bar checks; on a miss jump to .count instead of 0x1404243d8
  ...                     ; all three solved -> jmp 0x140423ae6
.count:
  mov  rax, [rip -> 0x140F9C680 + 0x140a8]   ; m_pBits (same field read_named_flag uses)
  test rax, rax
  jz   .fail
  movzx eax, byte [rax + 12]                 ; flags 96..103
  shr  eax, 2                                ; bits 2..7 = 98..103
  popcnt eax, eax                            ; (or a 6-step bit loop if we'd rather not need SSE4.2)
  cmp  eax, N                                ; N baked in at patch time
  jae  0x140423ae6                           ; forced solve -> vanilla stop sequence + SetFlag
.fail:
  jmp  0x1404243d8                           ; vanilla "not solved"
```

- Only volatile registers (`rax`, `rcx`) are clobbered, and the solved path
  reloads everything it needs (`lea rdx, [rdi+0x20]`, …). `r15` stays 0
  as the vanilla code expects.
- The cave goes in a new gapless PE section (CLAUDE.md §8: VA from
  `align_up(SizeOfImage, SectionAlignment)` read at apply time; vanilla
  `SizeOfImage` = `0x102B000`). Assemble with Keystone, verify with
  Capstone, and run the synthetic-PE round trip before touching a real
  exe. Hard-abort on non-vanilla bytes.
- Skip the patch entirely when N = 6 (vanilla behaviour, nothing to change).
- The patch must coexist with `dark_engine_patch.py`, which only touches
  the `.rdata` combination table, and with any other appended section.
  Read `SizeOfImage` fresh each time and apply after any earlier
  section-appending patch.

What the player sees: after their Nth real solve, any other piston control
that ticks stops itself, with its sounds, within a frame. A piston whose
control isn't ticking yet (different sector, or not visited) stops when it
next ticks or on the next level load (state 0 → 2 → forced). Its bars are
left wherever they were until the next load snaps them.

### Fallback: client writes (AP only, not recommended)

Once the client reads ≥ N of flags 98–103 set (`read_named_flag`, in game
only), it `write_named_flag`s the rest. That's enough for the state-0
check on the next as4dkeng load, but it needs a level reload, does nothing
for the standalone build, and skips `SetFlag`'s save-limit bookkeeping.
Keep it only as a debug command (`/forcepiston n`) to test the
"floor opens after forced stops" assumption.

### AP world (`apworld/`)

- `options.py`: `PistonsRequired(Range)`, 1–6, default 6, display name
  "Pistons Required". Native `random` applies. 0 is technically possible
  (every control force-stops on its first tick), but there's no reason to
  offer it.
- `access_rules.py` `R.pistons()`: switch from `all(...)` over the 5
  Engine Block sections to a count. The cable-car piston counts as 1 for
  free: its only requirement is Engine Block hub access, which the
  "Defeat Legion" event's region already implies. So
  `1 + sum(can_reach(s) for s in sections) >= required`. Thread
  `required` the same way `require_schematic` is threaded today (BoundR
  override + the event rule in `__init__.py` ~line 765). The 23
  `as4dkeng` barrel locations that use `R.pistons` follow automatically.
- `PistonCombos` + Jack's Schematic: still required when combos are
  randomized, because even one real solve needs the combinations.
- `slot_data["pistons_required"]` so the client can show "k / N pistons
  stopped" (a read-only poll of 98–103 is cheap; optional).
- `ap_patcher.py`: apply the patch when `pistons_required < 6`.
- `ap_gui.py` YAML generator: add the option. No combination rules needed.

### Standalone (repo root)

- `patcher.py`: a `--pistons-required N` flag (+ `--pistons-required-random`)
  resolved in `_resolve_random_config()`, applying the exe patch when N < 6.
- Root `access_rules.py` `pistons()`: the same count change, via a module
  global like `_piston_combos_random`. Root `fill.py`'s goal check
  (`R.pistons(final_state, PLAYER)`, ~line 1635) and its "Missing for
  PISTONS" report pick it up.
- `gui.py`: the five places from CLAUDE.md §4 (input + 🎲, `getConfig`,
  `FIELD_SHORT`, `FIELD_DEFAULTS`, `applyConfig`) + `_build_cmd()`.
- Spoiler log line: "Pistons required: N / 6".

### Duplicated-code checklist

The patch module exists in two copies (`exe_patches/` and flat in
`apworld/`, checked with `tools/check_apworld_sync.py`). `pistons()` is in
both `access_rules.py` copies, which deliberately diverge, so edit both by
hand. Grep for every `R.pistons` / `pistons(` call site before calling it
done.

## In-game checks for the maintainer

1. **Flag ↔ piston mapping:** `dumpsaveflags`, solve one piston,
   `dumpsaveflags` again. Exactly one of 98–103 should flip, and it should
   persist across a save/reload. Note which piston it was.
2. **Load-time snap (no patch needed):** on a save with that piston
   stopped, re-enter the Dark Engine. The piston should show stopped with
   its bars at target.
3. **The real question, once the patch exists:** with N = 2 (say), solve 2
   pistons. The others should stop on their own (sounds play, the flags
   flip in a dump), and the path to Legion (`trans0259` floor collapse,
   then `CF_A4_FLOORGONE` = 142) should open exactly as in vanilla. If it
   doesn't, the floor trigger depends on something beyond the six stop
   sequences, and we look at the as4dkeng events/links data next.
4. Check that a piston control in another part of the level (not loaded
   or ticking) does stop when you get there.
