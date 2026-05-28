#!/usr/bin/env python3
"""
fix_csv_coords.py

Reads a locations CSV and a directory of RSC files, then for every row that
has a source_file ending in .rsc, re-reads zone, x, y, z directly from the
record whose Name field is at the stored `offset` in the binary.

Usage:
    python fix_csv_coords.py locations.csv rsc_dir/ > locations_fixed.csv

The script expects RSC files to be found at:
    rsc_dir/<level_id>/<source_file>
e.g. rsc_dir/salvage/resource.rsc

Columns (0-indexed):
  0  level_id
  1  source_file
  2  offset (hex, e.g. 0x0A92)
  3  zone
  4  category
  5  rsc_name
  6  display_name
  7  region
  8  req
  9  is_cadeaux
 10  sub_region   <- zone lives here in the ORIGINAL (was always 0 due to bug)
 11  x
 12  y
 13  z
 14  track_type
 15  ...rest preserved verbatim

The fix: for each row, open the RSC file, seek to (offset - 0x22), read the
72-byte record, extract zone from +0x11 and XYZ from +0x04..+0x0F.

Section-break awareness: if the RSC file has a 16-byte non-zero blob inserted
(like salvage/resource.rsc at 0x5300), the name_off stored in the CSV is still
correct — we always seek to (name_off - 0x22) directly, so no adjustment needed.
"""

import sys
import os
import struct
import csv

NAME_OFF = 0x22
RECORD_SIZE = 72

def read_record(rsc_path, name_off):
    """Read zone and XYZ from the record at name_off in the given RSC file."""
    rec_base = name_off - NAME_OFF
    if rec_base < 0:
        return None
    try:
        with open(rsc_path, 'rb') as f:
            f.seek(rec_base)
            rec = f.read(RECORD_SIZE)
        if len(rec) < RECORD_SIZE:
            return None
        # Verify name field is a non-empty RSC_ string
        name_bytes = rec[NAME_OFF:NAME_OFF+30].split(b'\x00')[0]
        if not name_bytes or not name_bytes.startswith(b'RSC_'):
            return None
        x, y, z = struct.unpack('<fff', rec[0x04:0x10])
        zone = rec[0x11]
        return zone, x, y, z
    except (OSError, struct.error):
        return None

def fmt_float(v):
    """Format float: strip trailing zeros, max 4 decimal places."""
    s = f"{v:.4f}".rstrip('0').rstrip('.')
    return s if s != '-0' else '0'

def main():
    if len(sys.argv) < 3:
        print("Usage: fix_csv_coords.py <locations.csv> <rsc_dir/>", file=sys.stderr)
        sys.exit(1)

    csv_path = sys.argv[1]
    rsc_dir = sys.argv[2].rstrip('/')

    fixed = 0
    skipped = 0
    not_found = 0

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    out_rows = []
    for row in rows:
        if len(row) < 14:
            out_rows.append(row)
            continue

        level_id    = row[0].strip()
        source_file = row[1].strip()
        offset_str  = row[2].strip()
        # columns 3-onward
        # col 10 = sub_region (zone), col 11 = x, col 12 = y, col 13 = z

        if not source_file.endswith('.rsc'):
            out_rows.append(row)
            skipped += 1
            continue

        try:
            name_off = int(offset_str, 16)
        except ValueError:
            out_rows.append(row)
            skipped += 1
            continue

        rsc_path = os.path.join(rsc_dir, level_id, source_file)
        if not os.path.exists(rsc_path):
            print(f"WARN: not found: {rsc_path}", file=sys.stderr)
            out_rows.append(row)
            not_found += 1
            continue

        result = read_record(rsc_path, name_off)
        if result is None:
            print(f"WARN: bad record at {source_file}:0x{name_off:04X}", file=sys.stderr)
            out_rows.append(row)
            skipped += 1
            continue

        zone, x, y, z = result
        old_zone = row[10]
        old_x, old_y, old_z = row[11], row[12], row[13]

        row = list(row)  # make mutable
        row[10] = str(zone)
        row[11] = fmt_float(x)
        row[12] = fmt_float(y)
        row[13] = fmt_float(z)
        out_rows.append(row)

        changed = (old_zone != row[10] or old_x != row[11] or
                   old_y != row[12] or old_z != row[13])
        if changed:
            fixed += 1
            print(f"FIX {level_id}/{source_file}:0x{name_off:04X}  "
                  f"zone:{old_zone}->{row[10]}  "
                  f"xyz:({old_x},{old_y},{old_z})->({row[11]},{row[12]},{row[13]})",
                  file=sys.stderr)

    writer = csv.writer(sys.stdout, lineterminator='\n')
    writer.writerows(out_rows)

    print(f"\nDone: {fixed} rows fixed, {skipped} skipped, {not_found} RSC files not found",
          file=sys.stderr)

if __name__ == '__main__':
    main()
