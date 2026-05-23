"""
rsc_utils.py
============
Canonical RSC record injection utility for Shadow Man Remastered.

All RSC file patching that needs to insert new records into a quest.rsc,
pickups.rsc, or similar file should use inject_rsc_record(). This ensures
consistent handling of:
  - EXEX sentinel detection and push-forward
  - data[9] live-window count management
  - Headroom slot selection
  - File expansion when no headroom exists
"""

import struct
from pathlib import Path

# ── RSC format constants ──────────────────────────────────────────────────────
HEADER_SIZE  = 8
RECORD_SIZE  = 72
NAME_OFF     = 0x22
NAME_MAXLEN  = 30
ZONE_OFF      = 0x11
TRACK_TYPE_OFF = 0x1C  # 2-byte big-endian TrackType: 0x0002 = persistent/cadeaux
SAVE_IDX_OFF  = 0x1E  # 4-byte big-endian save-profile index (bytes 0x1E–0x21)
XYZ_OFF       = 0x04
COUNT_BYTE   = 9

EXEX_SIGNATURE = b'EXEX'   # at record offset +4 (the X float field)
HEADROOM_SLOTS = 8          # slots to add when expanding a full file


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_exex_slot(data: bytes | bytearray) -> int | None:
    """Return the slot index of the EXEX sentinel, or None if absent."""
    n_full = (len(data) - HEADER_SIZE) // RECORD_SIZE
    for i in range(n_full):
        off = HEADER_SIZE + i * RECORD_SIZE
        if data[off + 4 : off + 8] == EXEX_SIGNATURE:
            return i
    return None


def build_rsc_record(
    rsc_name: str,
    x: float, y: float, z: float,
    zone: int,
    save_idx: int = 0,
) -> bytes:
    """Build a 72-byte RSC record."""
    record = bytearray(RECORD_SIZE)
    struct.pack_into("<fff", record, XYZ_OFF, x, y, z)
    record[ZONE_OFF] = zone & 0xFF
    struct.pack_into(">I", record, SAVE_IDX_OFF, save_idx)
    name_bytes = rsc_name.encode("ascii")[:NAME_MAXLEN - 1]
    record[NAME_OFF : NAME_OFF + len(name_bytes)] = name_bytes
    return bytes(record)


# ── Canonical injection ───────────────────────────────────────────────────────

def inject_rsc_record(
    data: bytearray,
    record: bytes,
    *,
    allow_expand: bool = True,
) -> int | None:
    """
    Insert a 72-byte record into an RSC file (bytearray, modified in-place).

    Strategy:
      1. If EXEX sentinel exists: overwrite it directly. EXEX is destroyed
         but the engine doesn't need it — having it in the live window
         corrupts key item initialization.
      2. If no EXEX: find first fully-zeroed slot in headroom
         (data[9] <= slot < n_full - 1), write there, bump data[9].
      3. If no headroom: expand by HEADROOM_SLOTS zeroed records
         (only if allow_expand=True), then use first new slot.

    Returns the slot index written, or None if injection was not possible
    (allow_expand=False and no space available).

    Callers compute name_offset as:
        HEADER_SIZE + slot * RECORD_SIZE + NAME_OFF
    """
    assert len(record) == RECORD_SIZE, f"record must be {RECORD_SIZE} bytes"

    n_full = (len(data) - HEADER_SIZE) // RECORD_SIZE
    trailer_start = HEADER_SIZE + n_full * RECORD_SIZE

    def _n_full():
        return (len(data) - HEADER_SIZE - (len(data) - trailer_start)) // RECORD_SIZE

    def _expand():
        insert_at = HEADER_SIZE + _n_full() * RECORD_SIZE
        data[insert_at:insert_at] = bytearray(RECORD_SIZE * HEADROOM_SLOTS)

    # ── Strategy 1: overwrite EXEX directly ──────────────────────────────────
    exex_slot = find_exex_slot(data)
    if exex_slot is not None:
        slot = exex_slot
        data[HEADER_SIZE + slot*RECORD_SIZE : HEADER_SIZE + (slot+1)*RECORD_SIZE] = record
        if slot >= data[COUNT_BYTE]:
            data[COUNT_BYTE] = min(slot + 1, 255)
        return slot

    # ── Strategy 2: headroom slot (no EXEX) ──────────────────────────────────
    count = data[COUNT_BYTE]
    cur_n = _n_full()

    slot = None
    for i in range(count, cur_n - 1):
        chunk = data[HEADER_SIZE + i*RECORD_SIZE : HEADER_SIZE + (i+1)*RECORD_SIZE]
        if bytes(chunk) == bytes(RECORD_SIZE):
            slot = i
            break

    if slot is None:
        if not allow_expand:
            return None
        _expand()
        cur_n = _n_full()
        slot = count

    data[HEADER_SIZE + slot*RECORD_SIZE : HEADER_SIZE + (slot+1)*RECORD_SIZE] = record
    # Zero any dirty slots between old count and new slot
    for i in range(data[COUNT_BYTE], slot):
        data[HEADER_SIZE + i*RECORD_SIZE : HEADER_SIZE + (i+1)*RECORD_SIZE] = bytes(RECORD_SIZE)
    if slot >= data[COUNT_BYTE]:
        data[COUNT_BYTE] = min(slot + 1, 255)
    return slot