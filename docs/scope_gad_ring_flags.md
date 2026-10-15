# Scope: Gad temple rings and the CF_GOT*GAD save flags

Status: predicate patch built (2026-10-15), needs an in-game check. Everything else is research.

## Question

The maintainer's `dumpsaveflags` lists `CF_GOTTOUCHGAD` (67),
`CF_GOTWALKGAD` (68) and `CF_GOTSWIMGAD` (69). Could setting them make
the Gad temple rings non-interactable when temples are shuffled, and so
replace or simplify the exe patching in `exe_patches/gad_pickup_patch.py`?

## Recommendation

**Keep `gad_pickup_patch.py` as it is. Don't write these flags from the
client.**

The flags record "this power was obtained". They do switch off the ring
cutscene, so in principle they could make a ring inert. They can't replace
the gad patch, though: most of that patch builds the shuffled
`RSC_X_GAD_PICKUP` item, which has nothing to do with the rings. The only
part they overlap is the 8 temple NOPs, and the NOPs are the better tool
for that job (static, always on, no timing).

The maintainer confirmed the ring cutscene is a real problem in shuffled
seeds: it moves the player and throws them into lava. So
`gad_pickup_patch.py` now also applies a **3-site static exe patch** that
makes the three ring-cutscene predicates return false. The rings do
nothing in the standalone and AP builds alike, with no client
involvement.

## What the flags actually do (vanilla exe, read-only disassembly)

### Who sets them

Only the ring cutscene scripts, in `ShadowManEX01.kpf`
(`cutscene/scripts/t1tchgad/getgad.cut`, `t2wlkgad/wlkgad.cut`,
`t3swmgad/swmgad.cut`). Each one does:

```
SetFlag            (CF_GOTxxxGAD)
ChangeGadTextures  ()
PlayAnim           (ACTOR_SHADOWMAN, NS_GAD_JUMP, 0)   // jump into lava/coals
```

No exe code sets 67–69 by immediate, apart from one debug/give-all style
block at `0x140459ec8` that has no direct callers.

### Who reads them

| Reader | What it does with 67–69 |
|---|---|
| `FUN_140459c00` (`ChangeGadTextures`, also called from the vanilla Poigne pickup case) | Builds `mask = 67 \| 68<<1 \| 69<<2 \| 70(CF_GOTPOIGNE)<<3`, switches on it, writes the gad level `[0x140F9C1A0]` (= client `GAD_LEVEL_RVA`), then tail-calls `FUN_140459d50` (apply). **The 8 `TEMPLE_NOP_SITES` in `gad_pickup_patch.py` are exactly this switch's writes.** |
| Cutscene predicates `0x1402ad450` / `0x1402ad490` / `0x1402ad4c0` | Each is registered with its cutscene in the static cutscene table (`{predicate, path}` pairs built in static init around `0x14002c017`). The cutscene may only play while the predicate returns true:<br>• touch (`getgad.cut`): `[0x140D838E0] == 5 && !CF_GOTTOUCHGAD`<br>• walk (`wlkgad.cut`): `!CF_GOTWALKGAD`<br>• swim (`swmgad.cut`): `[0x140D838E4] == 5 && !CF_GOTSWIMGAD`<br>`0x140D838E0/E4` are the temple 1/3 ring-stage counters (incremented in `0x1404327b0`, saved to `CF_G1_/CF_G3_GADRINGSTAGE1..5` in `0x1402ef800`), so for temples 1 and 3 "== 5" means the ring puzzle is finished. |
| Objectives UI (`FUN_140306180` in `shadowmanplayer.cpp`, `$m_obj_*` strings) | Display only: shows the gad as obtained. |

`kexShadowManG1Ring`, `G1RingTop`, `G1RingBottom` and `G2Ring` themselves
make no flag calls.

### SetFlag side effects

`SetFlag` (`FUN_14033ee50`, vtable +0x08) has a special-case switch over
`index - 26`. Indices 67–69 (with 45, 98–104, 155, 176, 179) all go to the
same case: under a game-mode check, it calls flag-manager vtable +0x158
(`0x140342c60`, save-limit bookkeeping on `CF_SAVE_LIMIT*` 332/333) and
stores 2 into a global. So nothing gameplay-related happens. A raw bit
write (`write_named_flag`) skips that bookkeeping. That's harmless, but it
is a difference: the docstring's "safe only outside [26, 193]" warning
applies here, and this is what that case actually does.

### Evidence it already works this way in randomized seeds

The maintainer's dump (taken in a randomized game) has
`CF_GOTWALKGAD = 1`, `CF_GOTTOUCHGAD = 0`. So the walk-ring cutscene ran
and set its flag, and the gad level didn't change because the NOPs stopped
`ChangeGadTextures` from writing it. The NOPs are doing their job today,
and the ring cutscene still plays in shuffled seeds.

## Why client-written flags are the wrong tool

- **There's no client in the standalone build.** Making a ring inert there
  would need a save edit or an exe patch anyway.
- **Timing.** The flag has to be set before the player finishes the ring.
  Client writes are gated on being in game (CLAUDE.md §7), and anyone
  playing offline or disconnected would get the vanilla cutscene. The
  static NOPs have no such race.
- **They wouldn't remove the NOPs.** If the NOPs were dropped, the vanilla
  Poigne pickup case and any `ChangeGadTextures` run would recompute the
  gad level from the CF bitmask. That bitmask isn't progressive, and it
  would fight the client's progressive tier model (`_resync_gad_level`,
  "never lower an already-set temple flag").
- **Side effects:** the objectives UI would show unearned gads, and the
  client's gad tier logic reads `GAD_1..4_RVA`, not these flags, so it
  gains nothing.

## Ring cutscene predicate patch (built)

Overwrite the first bytes of each predicate with `xor al, al ; ret`
(`32 C0 C3`). Hard-abort if the bytes aren't vanilla (CLAUDE.md §8):

| Predicate | File offset | Vanilla first bytes |
|---|---|---|
| `0x1402ad450` (touch) | `0x2AC850` | `48 83 EC 28 83 3D 85 64` |
| `0x1402ad490` (walk) | `0x2AC890` | `48 83 EC 28 E8 C7 A0 09` |
| `0x1402ad4c0` (swim) | `0x2AC8C0` | `48 83 EC 28 83 3D 19 64` |

Implemented as `RING_CUTSCENE_PREDICATES` in both `exe_patches/gad_pickup_patch.py`
and `apworld/gad_pickup_patch.py` (step 8, inside the `shuffle_temples`
branch, after the temple NOPs, which stay as a backstop). It runs whenever
temples are shuffled: always in AP (`ap_patcher.py` passes
`shuffle_temples=True`), and in the standalone build when
`shuffle_gad_temples` is on. Non-vanilla predicate bytes raise.

Checked offline on a copy of the vanilla exe: both copies produce the same
output, Capstone reads `xor al, al ; ret` at all three sites, the exe
still parses, `shuffle_temples=False` leaves the predicates untouched, and
a pre-modified predicate is refused.

Unknowns this patch can't answer statically:
- Is the ring cutscene also the temple's way out (the jump down)? If
  suppressing it leaves the player on the platform/column with no route
  back, it's worse than today.
- The predicate may be consulted by a trigger volume that also does
  something else. Old dead code in `patcher.py`
  (`_zero_gad_cutscene_triggers`, `_zero_gad_cutscene_evt`, never called)
  tried trigger-side suppression and notes that the t2/t3 exit-cutscene
  records share a signature with the gad record. The predicate approach
  avoids that, because it's per-cutscene and not per-trigger.

`patcher.py`'s two dead `_zero_gad_cutscene_*` helpers can be deleted
whichever way this goes.

## Possible extra use (not proposed)

Because the flag is set by the ring cutscene itself, the client could
*read* 67–69, like `CF_LIFEALTAR00..04`, to treat "finished a temple ring"
as an AP check. That would only matter if we wanted ring completion as a
location as well as the injected gad pickup.

## In-game checks for the maintainer

1. In a shuffled seed, finish a ring you haven't done yet (touch or swim;
   walk is already flagged on the current save). What happens after the
   cutscene: does Shadow Man jump into lava/coals without the power and
   die? Does the HUD or gad texture flash change?
2. Is the cutscene's jump the only practical way back out of that temple
   area?
3. (1) is confirmed a problem and the predicate patch is built. On a newly
   patched seed, check that finishing a ring plays no cutscene and doesn't
   move you, and that the temple can still be left normally.
