"""
dark_engine_patch.py
====================
Patches the Dark Engine schematic combination values in thoth_x64.exe.

BACKGROUND
----------
The dark engine has 6 pistons, each with 3 fluid bars.  Each bar has a target
fill level 1–5.  The player cycles a bar through 1→2→3→4→5→1 by interacting;
when all 3 bars on a piston match the targets the piston is solved.

Vanilla combinations (bar0, bar1, bar2):
    Piston 1 (cable car / Cageways): 5-5-5
    Piston 2 (Jack the Ripper gate): 1-2-4
    Piston 3 (Milton Pike gate):     4-3-1
    Piston 4 (Avery Marx gate):      3-1-2
    Piston 5 (Marco Cruz gate):      1-5-3
    Piston 6 (Victor Batrachian):    2-4-5

DATA LOCATION (EXE)
-------------------
kexShadowManPistonLevel::Execute (FUN_140424510) reads the target value from a
static table in the EXE:

    target = table[piston_id * 3 + bar_index]   (uint32 LE, piston_id 1-based)

The table base is at VA 0x1406b71f4; entries 0–2 are unused (piston_id=0).
Used entries are indices 3–20, i.e. 18 × uint32 at VA 0x1406b7200.

TABLE ORDER (piston_id in table → game piston):
    table[1] = game piston 1  (5-5-5)
    table[2] = game piston 3  (4-3-1)
    table[3] = game piston 5  (1-5-3)
    table[4] = game piston 2  (1-2-4)
    table[5] = game piston 4  (3-1-2)
    table[6] = game piston 6  (2-4-5)

FILE OFFSET
-----------
VA 0x1406b7200 → .rdata section (VMA base 0x14064b000, file base 0x649800)
  file_offset = 0x649800 + (0x1406b7200 - 0x14064b000) = 0x6B5A00

TABLE_FILE_OFFSET = 0x6B5A00  (18 × uint32 LE, 72 bytes)

Vanilla bytes at the 18-entry table (VA 0x1406b7200):
    05 00 00 00  05 00 00 00  05 00 00 00   ← piston_id 1 (game piston 1)
    04 00 00 00  03 00 00 00  01 00 00 00   ← piston_id 2 (game piston 3)
    01 00 00 00  05 00 00 00  03 00 00 00   ← piston_id 3 (game piston 5)
    01 00 00 00  02 00 00 00  04 00 00 00   ← piston_id 4 (game piston 2)
    03 00 00 00  01 00 00 00  02 00 00 00   ← piston_id 5 (game piston 4)
    02 00 00 00  04 00 00 00  05 00 00 00   ← piston_id 6 (game piston 6)
"""

import re
import random
import struct
import tempfile
from pathlib import Path

# ── Vanilla combination table ─────────────────────────────────────────────────
# 18 entries: table[piston_id * 3 + bar_index], piston_id 1–6, bar_index 0–2.
# Stored as (bar0, bar1, bar2) per piston_id.

# Maps table piston_id → (bar0, bar1, bar2)
VANILLA_TABLE: dict[int, tuple[int, int, int]] = {
    1: (5, 5, 5),   # game piston 1
    2: (4, 3, 1),   # game piston 3
    3: (1, 5, 3),   # game piston 5
    4: (1, 2, 4),   # game piston 2
    5: (3, 1, 2),   # game piston 4
    6: (2, 4, 5),   # game piston 6
}

# Human-readable names keyed by table piston_id
PISTON_NAMES: dict[int, str] = {
    1: "Piston 1 (cable car / Cageways)",
    2: "Piston 3 (Milton Pike gate)",
    3: "Piston 5 (Marco Cruz gate)",
    4: "Piston 2 (Jack the Ripper gate)",
    5: "Piston 4 (Avery Marx gate)",
    6: "Piston 6 (Victor Batrachian gate)",
}

# File offset of the 18-entry combination table in thoth_x64.exe.
# VA 0x1406b7200, .rdata section (VMA 0x14064b000, file base 0x649800).
TABLE_FILE_OFFSET = 0x6B5A00


# ── Read / write helpers ──────────────────────────────────────────────────────

def _read_table(exe_data: bytes, offset: int) -> dict[int, tuple[int, int, int]]:
    """Read current combination values from exe_data at offset."""
    result = {}
    for pid in range(1, 7):
        base = offset + (pid - 1) * 12   # 3 × uint32 per piston_id
        b0, b1, b2 = struct.unpack_from("<III", exe_data, base)
        result[pid] = (b0, b1, b2)
    return result


def _write_table(
    data: bytearray,
    offset: int,
    table: dict[int, tuple[int, int, int]],
) -> None:
    """Write combination values into data at offset."""
    for pid in range(1, 7):
        base = offset + (pid - 1) * 12
        struct.pack_into("<III", data, base, *table[pid])


# ── Randomization ─────────────────────────────────────────────────────────────

def randomize_dark_engine(
    rng: random.Random,
    config: dict,
) -> dict[int, tuple[int, int, int]]:
    """
    Generate randomized combination values.

    config keys:
        piston_combos  "on" | "random" | "off" (default "off" → vanilla)

    Each bar is independently assigned a value 1–5.
    Returns dict[table_piston_id → (bar0, bar1, bar2)].
    """
    mode = config.get("piston_combos", "off")
    if str(mode) == "off":
        return dict(VANILLA_TABLE)

    table = {}
    for pid in range(1, 7):
        table[pid] = (
            rng.randint(1, 5),
            rng.randint(1, 5),
            rng.randint(1, 5),
        )
    return table


# ── Main patch function ───────────────────────────────────────────────────────

def apply_dark_engine_patch(
    exe_path: str,
    table: dict[int, tuple[int, int, int]],
    *,
    dry_run: bool = False,
) -> None:
    """
    Write combination values to thoth_x64.exe.

    table: dict returned by randomize_dark_engine() — keys 1–6 (table piston_id).
    Each value is (bar0, bar1, bar2) with each bar in range 1–5.
    """
    if not Path(exe_path).exists():
        print(f"  [dark_engine] EXE not found: {exe_path} — skipping")
        return

    # Validate
    for pid, bars in table.items():
        for i, v in enumerate(bars):
            if not (1 <= v <= 5):
                raise ValueError(
                    f"  [dark_engine] piston_id={pid} bar{i}={v} out of range 1–5"
                )

    data = bytearray(Path(exe_path).read_bytes())
    offset = TABLE_FILE_OFFSET

    current = _read_table(bytes(data), offset)
    vanilla = VANILLA_TABLE

    changed = False
    for pid in range(1, 7):
        cur = current[pid]
        new = table[pid]
        van = vanilla[pid]
        label = PISTON_NAMES[pid]
        if new != van:
            tag = f"{van[0]}-{van[1]}-{van[2]} → {new[0]}-{new[1]}-{new[2]}"
            changed = True
        else:
            tag = f"{van[0]}-{van[1]}-{van[2]} (unchanged)"
        print(f"  [dark_engine] {label}: {tag}")

    if changed and not dry_run:
        _write_table(data, offset, table)
        Path(exe_path).write_bytes(data)
        print(f"  [dark_engine] Written to {Path(exe_path).name}")
    elif not changed:
        print("  [dark_engine] All combinations unchanged (vanilla)")


# ── Journal MUp patch ────────────────────────────────────────────────────────
#
# journal/11.MUp is a plain-text layout script for the schematic journal page.
# The 6 combination values appear as STRING entries after fixed POSITION coords:
#
#   POSITION  <235, 410>  → piston_id 1  (Console Room One)
#   POSITION  <235, 355>  → piston_id 2  (Console Room Three)
#   POSITION  <235, 300>  → piston_id 3  (Console Room Five)
#   POSITION  <520, 378>  → piston_id 4  (Console Room Two)
#   POSITION  <520, 328>  → piston_id 5  (Console Room Four)
#   POSITION  <520, 278>  → piston_id 6  (Console Room Six)
#
# Each combination is written as a 3-digit string: bar0 bar1 bar2 concatenated.

JOURNAL_MUP_PATH = "journal/11.MUp"

# Maps (x, y) position → table piston_id
_JOURNAL_POSITIONS: dict[tuple[int, int], int] = {
    (235, 410): 1,
    (235, 355): 2,
    (235, 300): 3,
    (520, 378): 4,
    (520, 328): 5,
    (520, 278): 6,
}


def patch_journal_mup(
    mup_bytes: bytes,
    table: dict[int, tuple[int, int, int]],
) -> bytes:
    """
    Patch the combination STRING values in journal/11.MUp content.

    mup_bytes: raw bytes of the original 11.MUp file.
    table:     dict[piston_id → (bar0, bar1, bar2)] from randomize_dark_engine().
    Returns patched bytes (CRLF line endings preserved).
    """
    text = mup_bytes.decode("ascii")

    # Match: POSITION<tabs><x, y><CRLF>STRING<tabs><"digits"><CRLF>
    # Only replace STRING values that are purely numeric (the combination slots).
    def _replacer(m):
        x = int(m.group('x'))
        y = int(m.group('y'))
        pid = _JOURNAL_POSITIONS.get((x, y))
        if pid is None:
            return m.group(0)  # not a combination position — leave untouched
        b0, b1, b2 = table[pid]
        new_combo = f"{b0}{b1}{b2}"
        return (
            m.group("pos_line")
            + m.group("str_open")
            + new_combo
            + m.group("str_close")
        )

    patched = re.sub(
        r"(?P<pos_line>POSITION\t+<(?P<x>\d+),\s*(?P<y>\d+)>\r?\n)"
        r"(?P<str_open>STRING\t+<\")(?P<digits>\d+)(?P<str_close>\">\r?\n)",
        _replacer,
        text,
    )
    return patched.encode("ascii")


def extract_and_patch_journal(
    kpf_files: list[str],
    table: dict[int, tuple[int, int, int]],
    tmp_dir: str,
) -> str | None:
    """
    Extract journal/11.MUp from the base KPFs, patch it, write to tmp_dir.
    Returns the local path of the patched file, or None if not found.
    """
    from kpf_handler import extract_file_from_kpf, build_kpf_index, find_file_in_kpf

    index = build_kpf_index(kpf_files)
    matches = find_file_in_kpf(index, JOURNAL_MUP_PATH)
    if not matches:
        matches = find_file_in_kpf(index, "journal/11.mup")
    if not matches:
        print(f"  [dark_engine] WARNING: {JOURNAL_MUP_PATH} not found in KPFs")
        return None

    internal_path, kpf_name = matches[0]
    kpf_path = str(Path(kpf_files[0]).parent / kpf_name)

    tmp_path = str(Path(tmp_dir) / "journal" / "11.MUp")
    if not extract_file_from_kpf(kpf_path, internal_path, tmp_path):
        print(f"  [dark_engine] WARNING: failed to extract {internal_path}")
        return None

    original = Path(tmp_path).read_bytes()
    patched  = patch_journal_mup(original, table)
    Path(tmp_path).write_bytes(patched)

    vanilla = VANILLA_TABLE
    for pid in range(1, 7):
        van = vanilla[pid]
        new = table[pid]
        van_str = f"{van[0]}{van[1]}{van[2]}"
        new_str = f"{new[0]}{new[1]}{new[2]}"
        if new_str != van_str:
            print(f"  [dark_engine] Journal {PISTON_NAMES[pid]}: {van_str} → {new_str}")

    return tmp_path


# ── Standalone test / diagnostic ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Dark engine combination patcher")
    parser.add_argument("exe", help="Path to thoth_x64.exe")
    parser.add_argument(
        "--locate", action="store_true",
        help="Locate and print the table file offset, then dump current values",
    )
    parser.add_argument(
        "--test-444", action="store_true",
        help="Patch piston 1 (table id=1) to 4-4-4 as a test (modifies EXE)",
    )
    parser.add_argument(
        "--restore", action="store_true",
        help="Restore all combinations to vanilla",
    )
    args = parser.parse_args()

    exe_data = Path(args.exe).read_bytes()
    offset = TABLE_FILE_OFFSET
    print(f"Table file offset: 0x{offset:X}")

    current = _read_table(exe_data, offset)
    print("\nCurrent values:")
    for pid in range(1, 7):
        b = current[pid]
        v = VANILLA_TABLE[pid]
        match = "✓ vanilla" if b == v else "MODIFIED"
        print(f"  piston_id={pid}  bars={b[0]}-{b[1]}-{b[2]}  [{match}]  {PISTON_NAMES[pid]}")

    if args.test_444:
        test_table = dict(VANILLA_TABLE)
        test_table[1] = (4, 4, 4)
        apply_dark_engine_patch(args.exe, test_table)
        print("\nTest patch applied: piston_id=1 → 4-4-4")

    if args.restore:
        apply_dark_engine_patch(args.exe, dict(VANILLA_TABLE))
        print("\nRestored to vanilla.")
