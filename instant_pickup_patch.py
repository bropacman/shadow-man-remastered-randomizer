"""
instant_pickup_patch.py
========================
Removes the camera-lock / DOF-blur / player-freeze "read" sequence that
FUN_140446500 (the shared pickup-type dispatch handler) runs before EVERY
pickup reaches its type-specific switch case -- including RSC_X_BOOK_OF_SHADOWS
(AP's foreign-item marker, type_id 0x4), RSC_X_PROPHECY (0x16), and every
other type routed through this function.

Reverse-engineered 2026-07-24 via Ghidra against a live install
(thoth_x64.exe, Steam2 build) -- verify against your own copy before trusting
the vanilla-byte anchors below. See CLAUDE.md "Next Up" for the full trace
that led here (tools/lookup_lore_type_ids.py, tools/find_lore_case_handler.py,
tools/diag_case_handler.py).

WHAT THE VANILLA CODE DOES (a state machine on the picked-up object's own
+0x24 "state" field):
  - First call (state==0): after the distance check passes, it locks the
    camera manager into read-mode, sweeps in a DOF-blur cvar set, zeroes
    several of the player object's fields (velocity-looking state, from
    FUN_140458680()'s return -- effectively a freeze), sets state=1, seeds a
    frame counter at [item+0x290] to -260.0f, and falls into
    switch(type_id) for a type-specific SFX/subtitle-id call.
  - Every subsequent call while state==1: increments that frame counter by
    1.0 and early-returns until it exceeds -30.0 (~230 frames, ~4-8 real
    seconds) -- only THEN does it restore the camera, add the item to
    inventory (via FUN_140310a60()'s kexShadowManInventoryLocal singleton),
    and resume normal play. That ~230-frame wait is "the slowdown."

THIS PATCH (3 edits, all in FUN_140446500):
  1. JMP over the camera-lock + FUN_1401ae3c0 + player-freeze block
     (0x140446987-0x140446a76). Confirmed safe by inspection: the finalize
     code (which we do NOT touch) reads/writes a totally different camera
     sub-object (offset-0x540-based) than the one this block writes
     (offset-0xa90-based), so finalize doesn't depend on anything this
     block did.
  2. Re-seed the frame counter (the -260.0f immediate at 0x140446a8b) to
     0.0f, so the SAME finalize logic -- which actually grants the item --
     runs on the very next call instead of ~230 calls later.
  3. JMP over the DOF-blur cvar-set block (0x140446aa7-0x140446b19).

  4. NOP the single CALL FUN_1401b0830(RCX=?, RDX="inventory", R8D=0) at
     0x1404470fd, in the finalize block's item-grant logic. This call passes
     the literal string "inventory" as an argument -- strong signal it's a
     generic "notify the UI system" dispatch that pops the inventory screen
     open. It's the very last step of that logic, after the item-count/slot
     bookkeeping (which is left untouched), so removing it should suppress
     the screen-swap without affecting whether the item is actually granted.
     Lower confidence than patches 1-3 (inferred from the string argument,
     not fully traced) -- test that items still show up correctly when you
     open the inventory manually afterward.
  5. Zero the magnitude argument (XMM2, was 1.0) of the once-per-pickup
     CALL FUN_1402c5e20(player+0x368, 0, magnitude, 1) in the switch tail --
     reads like a small deliberate camera-kick/impulse pickup-feedback
     effect, previously masked by the camera-lock/blur. Call still fires
     (in case it does other bookkeeping), just with zero magnitude.
  6. Skip the finalize block's camera-restore/commit sequence
     (0x140446ec0-0x140446f57) -- with setup's lock skipped (patch 1), this
     "restore the camera back to normal" logic has nothing to restore from,
     and forcing an "activate" on a fresh snapshot the renderer wasn't
     tracking is the leading suspect for residual jitter after patches 1-5.
     Also skips one incidental zone-tracking write ([cam_mgr+0x2030] =
     value from [live_cam+0x500]) -- probably harmless for a single pickup
     event, flagged in case anything zone-dependent looks off.

DELIBERATELY NOT TOUCHED (semantics uncertain, not obviously camera/blur/
freeze -- see CLAUDE.md for the full reasoning, don't remove without more
investigation):
  - The FUN_140347560()-singleton call at vtable+0x158
    (0x140446a96-0x140446aa6) -- possibly a quest/stat tracking hook, paired
    with a vtable+0x60 call later in the finalize block. Left alone in case
    something depends on the pairing.
  - The FUN_140310a60()-singleton call at vtable+0x30
    (0x140446b19-0x140446b36) -- this singleton IS the inventory manager
    (confirmed: its vtable gets checked against kexShadowManInventoryLocal
    a few instructions later, inside the finalize block's actual item-grant
    logic), so this call may be required setup for the grant to work. Too
    risky to remove without isolating it in its own live test first.

This intentionally affects EVERY type_id that flows through FUN_140446500,
not just RSC_X_BOOK_OF_SHADOWS -- confirmed scope (2026-07-24 session): all
pickups instant, not just AP items. Compatible with gad_pickup_patch.py's
case 0x16/0x13 hijack, which redirects deeper in the switch, well after the
ranges this patch touches.

STATUS: untested in-game. Verify on a backup/copy first: pick up a regular
item, a Prophecy book, and a Poigne, and confirm (a) no camera lock/blur/
freeze happens, (b) the item still registers in inventory, (c) nothing else
breaks.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gad_pickup_patch import IMAGE_BASE, _va_to_file  # noqa: E402  (reuse the same section table)

# ── Patch 1: camera-lock + FUN_1401ae3c0 + player-freeze block ──────────────
BLOCK1_VA     = 0x140446987
BLOCK1_END_VA = 0x140446a76          # first instruction to KEEP (state=1 setup)
BLOCK1_LEN    = BLOCK1_END_VA - BLOCK1_VA   # 0xEF = 239 bytes
BLOCK1_ANCHOR = bytes([              # first 16 bytes at BLOCK1_VA
    0xe8, 0xe4, 0xde, 0xe8, 0xff,
    0x44, 0x0f, 0x28, 0x8c, 0x24, 0xb0, 0x00, 0x00, 0x00,
    0x48, 0x8d,
])

# ── Patch 2: frame-counter seed (the -260.0f immediate) ─────────────────────
# The finalize/restore-to-idle logic (which we don't touch) fires once this
# counter, incremented by 1.0 per call, exceeds -30.0. Seeding it all the way
# to 0.0 (immediate pass) cuts off the setup path's own player pose-blend
# animation (FUN_1403ee5d0, "kneel and reach for the object", ~20-frame blend
# implied by its own 20.0 argument) before it finishes, which then visibly
# snaps the player back to idle mid-lean -- reported as the player being
# "repositioned away from the object." Use --delay-frames to tune: seed is
# computed as -delay_frames, so finalize fires ~delay_frames calls after
# setup instead of immediately. Default (20) aims to just clear the reach
# animation; raise it if the snap is still visible, lower it if pickup still
# feels sluggish.
SEED_VA          = 0x140446a8b
SEED_LEN         = 4
SEED_VANILLA     = bytes([0x00, 0x00, 0x82, 0xc3])   # -260.0f
DEFAULT_DELAY_FRAMES = 20.0


def _seed_bytes(delay_frames: float) -> bytes:
    return struct.pack('<f', -abs(delay_frames))

# ── Patch 3: DOF-blur cvar-set block ─────────────────────────────────────────
BLOCK2_VA     = 0x140446aa7
BLOCK2_END_VA = 0x140446b19          # first instruction to KEEP (inventory-singleton call)
BLOCK2_LEN    = BLOCK2_END_VA - BLOCK2_VA   # 0x72 = 114 bytes
BLOCK2_ANCHOR = bytes([              # first 16 bytes at BLOCK2_VA
    0x48, 0x8b, 0x0d, 0xf2, 0xb2, 0x93, 0x00,
    0x48, 0x8d, 0x15, 0xdb, 0xe1, 0x29, 0x00,
    0xe8, 0x26,
])

# ── Patch 4: NOP the "inventory" screen-swap notification call ──────────────
# CALL FUN_1401b0830(RCX=?, RDX="inventory", R8D=0) -- last step of the
# item-grant logic, after slot/count bookkeeping. Anchor covers the 3
# instructions leading up to and including the CALL opcode byte; only the
# 5-byte CALL itself gets NOPed.
NOTIFY_ANCHOR_VA = 0x1404470ee
NOTIFY_ANCHOR     = bytes([          # 20 bytes at NOTIFY_ANCHOR_VA -- covers the full CALL, not just its opcode byte
    0x45, 0x33, 0xc0,
    0x48, 0x89, 0x74, 0x24, 0x20,
    0x48, 0x8d, 0x15, 0xf3, 0x55, 0x2a, 0x00,
    0xe8, 0x2e, 0x97, 0xd6, 0xff,
])
NOTIFY_CALL_VA  = 0x1404470fd
NOTIFY_CALL_LEN = 5

# ── Patch 5: zero the camera-kick magnitude ─────────────────────────────────
# CALL FUN_1402c5e20(RCX=player+0x368, EDX=0, XMM2=1.0, R9D=1) fires once per
# pickup in the switch tail, regardless of type -- looks like a deliberate
# small camera impulse/kick for pickup feedback (magnitude 1.0), previously
# imperceptible under the camera-lock/blur we removed above, now visible as
# "a little camera jitter." Rather than remove the call (may do other
# bookkeeping), zero the XMM2 magnitude argument (MOVAPS XMM2,XMM10 ->
# XORPS XMM2,XMM2) so the call still fires but the kick has no magnitude.
KICK_ANCHOR_VA = 0x140446e34
KICK_ANCHOR    = bytes([             # 15 bytes at KICK_ANCHOR_VA
    0x40, 0x88, 0x74, 0x24, 0x20,
    0x41, 0xb9, 0x01, 0x00, 0x00, 0x00,
    0x41, 0x0f, 0x28, 0xd2,
])
KICK_VA       = 0x140446e3f
KICK_LEN      = 4
KICK_VANILLA  = bytes([0x41, 0x0f, 0x28, 0xd2])   # MOVAPS XMM2,XMM10
KICK_PATCHED  = bytes([0x0f, 0x57, 0xd2, 0x90])   # XORPS XMM2,XMM2 + NOP

# ── Patch 6: skip the finalize block's camera restore/commit ────────────────
# Even with setup's camera-lock skipped (patch 1), the finalize block still
# copies "live" camera params into a target sub-struct (offset-0x540-based,
# same object family as the +0xa90-based one setup used) and fires the same
# vtable+0x18 "activate" calls setup used to engage the lock. If the camera
# was never locked, telling it to "activate" a fresh snapshot it wasn't
# tracking is a good candidate for the residual one-frame camera pop/jitter
# reported after patches 1-5. Skipping this whole block also skips one
# incidental write ([FUN_1402d4870()+0x2030] = zone/type value from
# [RDI+0x500]) that looks like camera-zone bookkeeping unrelated to
# positioning -- probably harmless to miss for a single pickup event, but
# flagging it in case anything zone-dependent looks off after this patch.
BLOCK3_VA     = 0x140446ec0
BLOCK3_END_VA = 0x140446f57          # first instruction to KEEP (0x298 pointer cleanup)
BLOCK3_LEN    = BLOCK3_END_VA - BLOCK3_VA   # 0x97 = 151 bytes
BLOCK3_ANCHOR = bytes([              # first 16 bytes at BLOCK3_VA
    0xe8, 0xab, 0xd9, 0xe8, 0xff,
    0x48, 0x8d, 0xb0, 0x40, 0x05, 0x00, 0x00,
    0xe8, 0x9f, 0xd9, 0xe8,
])

# ── Patch 7: skip the "kneel and reach" pose-entry call ──────────────────────
# Longer delays (patch 2) didn't stop the snap-back -- it turns out finalize
# unconditionally restores the player's pre-pickup pose/animation via
# FUN_1403ee5d0(40.0, saved_pose, saved_flag, 0x7f), using values captured
# even earlier than anything we touch. That restore always fires and always
# looks like a snap, no matter how long we wait, because setup unconditionally
# enters a "kneel and reach for the object" pose first via this call:
# FUN_1403ee5d0(20.0, pose_id, 1, 0x7f). If the player never enters that pose
# in the first place, finalize's restore has nothing to snap back FROM (it
# just re-applies the pose the player is already in) -- so instead of timing
# the restore, we skip the pose-entry call itself.
REACH_ANCHOR_VA = 0x1404468c2
REACH_ANCHOR    = bytes([            # 15 bytes at REACH_ANCHOR_VA
    0x41, 0x0f, 0x28, 0xc1,
    0x41, 0xb8, 0x01, 0x00, 0x00, 0x00,
    0xe8, 0xff, 0x7c, 0xfa, 0xff,
])
REACH_CALL_VA  = 0x1404468cc
REACH_CALL_LEN = 5

# ── Patch 8: skip the now-unmatched camera "release control" pair ──────────
# Setup's camera-lock/acquire calls are gone (patch 1), but finalize still
# unconditionally calls CAMERA_MGR->vtable[0x68]() ("release control") and
# sets [CAMERA_MGR+0x25a8]=1. Releasing control that was never acquired may
# force the camera to recompute its follow framing from scratch in one
# frame, which would look exactly like the reported "character repositioned
# away from the object" -- likely a camera reframe, not an actual player
# position change (nothing in FUN_140446500, FUN_1402d4870, or FUN_1403ee5d0
# writes to player position anywhere we've traced). Untested guess, same as
# patches 5-7 -- if this isn't it, the next step is opening whatever's
# actually at kexShadowManCameraMgrLocal::vftable+0x68.
BLOCK4_VA     = 0x140446fd1
BLOCK4_END_VA = 0x140446fee          # first instruction to KEEP ([DAT_140fb3120+0x24]=2 UI-state set)
BLOCK4_LEN    = BLOCK4_END_VA - BLOCK4_VA   # 0x1D = 29 bytes
BLOCK4_ANCHOR = bytes([              # first 16 bytes at BLOCK4_VA
    0xe8, 0x9a, 0xd8, 0xe8, 0xff,
    0x48, 0x8b, 0xc8,
    0x48, 0x8b, 0x10,
    0xff, 0x52, 0x68,
    0xe8, 0x8c,
])


def _jmp_over(src_va: int, dst_va: int, total_len: int) -> bytes:
    rip = src_va + 5
    rel = dst_va - rip
    return bytes([0xE9]) + struct.pack('<i', rel) + bytes([0x90] * (total_len - 5))


def patch_instant_pickup(exe_path: str, *, dry_run: bool = False, in_place: bool = False,
                          delay_frames: float = DEFAULT_DELAY_FRAMES) -> str:
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"EXE not found: {exe_path}")

    data = bytearray(path.read_bytes())

    block1_foff = _va_to_file(BLOCK1_VA)
    seed_foff   = _va_to_file(SEED_VA)
    block2_foff = _va_to_file(BLOCK2_VA)
    notify_foff = _va_to_file(NOTIFY_ANCHOR_VA)
    kick_foff   = _va_to_file(KICK_ANCHOR_VA)
    block3_foff = _va_to_file(BLOCK3_VA)
    reach_foff  = _va_to_file(REACH_ANCHOR_VA)
    block4_foff = _va_to_file(BLOCK4_VA)
    if (block1_foff is None or seed_foff is None or block2_foff is None or notify_foff is None
            or kick_foff is None or block3_foff is None or reach_foff is None or block4_foff is None):
        raise RuntimeError("Could not resolve one or more patch VAs to file offsets -- wrong exe version?")
    notify_call_foff = notify_foff + (NOTIFY_CALL_VA - NOTIFY_ANCHOR_VA)
    kick_target_foff  = kick_foff + (KICK_VA - KICK_ANCHOR_VA)
    reach_call_foff   = reach_foff + (REACH_CALL_VA - REACH_ANCHOR_VA)

    checks = [
        ("camera-lock block", block1_foff, BLOCK1_ANCHOR),
        ("frame-counter seed", seed_foff, SEED_VANILLA),
        ("dof-blur block", block2_foff, BLOCK2_ANCHOR),
        ("inventory-notify call", notify_foff, NOTIFY_ANCHOR),
        ("camera-kick magnitude", kick_foff, KICK_ANCHOR),
        ("finalize camera-restore block", block3_foff, BLOCK3_ANCHOR),
        ("reach pose-entry call", reach_foff, REACH_ANCHOR),
        ("camera release-control pair", block4_foff, BLOCK4_ANCHOR),
    ]
    already_patched_variants = {
        "inventory-notify call": NOTIFY_ANCHOR[:-NOTIFY_CALL_LEN] + bytes([0x90] * NOTIFY_CALL_LEN),
        "camera-kick magnitude": KICK_ANCHOR[:-KICK_LEN] + KICK_PATCHED,
        "reach pose-entry call": REACH_ANCHOR[:-REACH_CALL_LEN] + bytes([0x90] * REACH_CALL_LEN),
    }
    for name, off, expected in checks:
        actual = bytes(data[off: off + len(expected)])
        if actual == expected:
            continue
        if actual == already_patched_variants.get(name):
            continue  # already patched
        if name == "frame-counter seed":
            # delay_frames is tunable across runs -- accept any plausible
            # already-patched float rather than one fixed constant.
            try:
                val = struct.unpack('<f', actual)[0]
                if -300.0 <= val <= 0.0:
                    continue
            except struct.error:
                pass
        raise RuntimeError(
            f"Vanilla verify failed at '{name}' (file offset 0x{off:x}).\n"
            f"  Expected: {expected.hex(' ')}\n"
            f"  Got:      {actual.hex(' ')}\n"
            f"  EXE may be a different version, or these anchor bytes were "
            f"mis-transcribed from the Ghidra listing -- re-confirm before "
            f"proceeding."
        )
    print("  [instant_pickup] All vanilla anchors verified OK")

    if dry_run:
        print("  [instant_pickup] DRY RUN -- no changes written")
        return exe_path

    data[block1_foff: block1_foff + BLOCK1_LEN] = _jmp_over(BLOCK1_VA, BLOCK1_END_VA, BLOCK1_LEN)
    print(f"  [instant_pickup] Skipped camera-lock/freeze block (0x{BLOCK1_VA:x}-0x{BLOCK1_END_VA:x})")

    data[seed_foff: seed_foff + SEED_LEN] = _seed_bytes(delay_frames)
    print(f"  [instant_pickup] Re-seeded frame counter for ~{delay_frames:g}-frame delay @ 0x{SEED_VA:x}")

    data[block2_foff: block2_foff + BLOCK2_LEN] = _jmp_over(BLOCK2_VA, BLOCK2_END_VA, BLOCK2_LEN)
    print(f"  [instant_pickup] Skipped DOF-blur block (0x{BLOCK2_VA:x}-0x{BLOCK2_END_VA:x})")

    data[notify_call_foff: notify_call_foff + NOTIFY_CALL_LEN] = bytes([0x90] * NOTIFY_CALL_LEN)
    print(f"  [instant_pickup] NOPed inventory-notify call @ 0x{NOTIFY_CALL_VA:x}")

    data[kick_target_foff: kick_target_foff + KICK_LEN] = KICK_PATCHED
    print(f"  [instant_pickup] Zeroed camera-kick magnitude @ 0x{KICK_VA:x}")

    data[block3_foff: block3_foff + BLOCK3_LEN] = _jmp_over(BLOCK3_VA, BLOCK3_END_VA, BLOCK3_LEN)
    print(f"  [instant_pickup] Skipped finalize camera-restore block (0x{BLOCK3_VA:x}-0x{BLOCK3_END_VA:x})")

    data[reach_call_foff: reach_call_foff + REACH_CALL_LEN] = bytes([0x90] * REACH_CALL_LEN)
    print(f"  [instant_pickup] NOPed reach/kneel pose-entry call @ 0x{REACH_CALL_VA:x}")

    data[block4_foff: block4_foff + BLOCK4_LEN] = _jmp_over(BLOCK4_VA, BLOCK4_END_VA, BLOCK4_LEN)
    print(f"  [instant_pickup] Skipped camera release-control pair (0x{BLOCK4_VA:x}-0x{BLOCK4_END_VA:x})")

    out_path = exe_path if in_place else str(path.with_stem(path.stem + "_patched"))
    Path(out_path).write_bytes(data)
    print(f"  [instant_pickup] Written: {out_path}")
    return out_path


def apply_instant_pickup_patch(exe_path: str, *, dry_run: bool = False,
                                delay_frames: float = DEFAULT_DELAY_FRAMES) -> bool:
    """Matches the apply_*_patch(exe_path) -> bool convention used by
    gad_pickup_patch.py / health_patch.py / death_penalty_patch.py etc. --
    always patches in place (the exe_path passed in is ap_patcher.py's
    already-copied "thoth_x64_patched.exe" working file, patched
    progressively by each apply_*_patch call in sequence), idempotent if
    already applied."""
    if not Path(exe_path).exists():
        print(f"  [instant_pickup] EXE not found: {exe_path} -- skipping")
        return False
    try:
        if verify_instant_pickup(exe_path):
            print("  [instant_pickup] Patch already applied -- skipping")
            return True
    except RuntimeError as e:
        print(f"  [instant_pickup] WARNING: {e}")
        return False
    try:
        patch_instant_pickup(exe_path, dry_run=dry_run, in_place=True, delay_frames=delay_frames)
        return True
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  [instant_pickup] ERROR: {e}")
        return False


def verify_instant_pickup(exe_path: str) -> bool:
    data = Path(exe_path).read_bytes()
    seed_foff = _va_to_file(SEED_VA)
    actual = bytes(data[seed_foff: seed_foff + SEED_LEN])
    if actual == bytes(SEED_VANILLA):
        return False
    try:
        val = struct.unpack('<f', actual)[0]
    except struct.error:
        val = None
    if val is not None and -300.0 <= val <= 0.0:
        return True
    raise RuntimeError(f"Unexpected bytes at seed site: {actual.hex(' ')} -- wrong version or partially patched.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Remove camera-lock/DOF-blur/freeze from all pickups (Shadow Man Remastered)"
    )
    parser.add_argument("exe", help="Path to thoth_x64.exe")
    parser.add_argument("--dry-run", action="store_true", help="Verify vanilla bytes only, write nothing")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the given exe instead of writing a _patched copy")
    parser.add_argument("--verify", action="store_true", help="Report whether this exe already has the patch applied")
    parser.add_argument("--delay-frames", type=float, default=DEFAULT_DELAY_FRAMES,
                         help=f"Frames to wait before finalize fires (default {DEFAULT_DELAY_FRAMES:g}). "
                              f"Raise if the player still snaps/repositions; lower if pickup feels sluggish again.")
    args = parser.parse_args()

    if args.verify:
        try:
            applied = verify_instant_pickup(args.exe)
            print(f"Instant pickup patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    try:
        patch_instant_pickup(args.exe, dry_run=args.dry_run, in_place=args.in_place, delay_frames=args.delay_frames)
        print("Done.")
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
