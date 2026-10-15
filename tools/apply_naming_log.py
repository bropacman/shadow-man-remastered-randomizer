"""Merge data/naming_log.csv (written by tools/ce_location_namer.lua) into
data/locations.csv.

For every logged entry (latest wins per location) this sets friendly_name,
sub_region, can_softlock, is_verified and notes. Afterwards, any name the log touched that ends up as the only
"<base> 1" in its level with no "<base> 2" is shortened to "<base>", matching
the existing convention ("Calabash Tomb Govi", "Enseigne Altar").

The result is also copied to apworld/data/locations.csv, which is kept
identical to data/locations.csv by hand. The applied log is renamed to
naming_log.applied-<timestamp>.csv.

Usage:
    python tools/apply_naming_log.py            # apply
    python tools/apply_naming_log.py --dry-run  # show what would change
Then regenerate extracted_locations.py as usual (tools/generate.py).
"""
import argparse
import csv
import io
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSV_PATH = REPO / "data" / "locations.csv"
AP_CSV_PATH = REPO / "apworld" / "data" / "locations.csv"
LOG_PATH = REPO / "data" / "naming_log.csv"
EDIT_FIELDS = ("friendly_name", "sub_region", "can_softlock", "is_verified", "notes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log", type=Path, default=LOG_PATH, help="log to apply (default: %(default)s)")
    args = ap.parse_args()
    log_path = args.log

    if not log_path.exists():
        print(f"No log at {log_path}")
        return 0

    raw = CSV_PATH.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
    header, body = rows[0], rows[1:]
    col = {h: i for i, h in enumerate(header)}
    index = {(r[col["level_id"]], r[col["source_file"]], r[col["offset"]]): r for r in body}

    updates = {}
    with log_path.open(encoding="utf-8", newline="") as f:
        for e in csv.DictReader(f):
            updates[(e["level_id"], e["source_file"], e["offset"])] = e

    missing, changed, touched = [], 0, []
    for key, e in updates.items():
        row = index.get(key)
        if row is None:
            missing.append(key)
            continue
        diffs = []
        for field in EDIT_FIELDS:
            new = e.get(field)
            if new is None or (field == "sub_region" and new == ""):
                continue  # column absent in older logs, or nothing to set
            if row[col[field]] != new:
                diffs.append(f"{field}: {row[col[field]]!r} -> {new!r}")
                row[col[field]] = new
        if diffs:
            changed += 1
            print(f"{':'.join(key)}\n    " + "\n    ".join(diffs))
        touched.append(row)

    # Strip " 1" from bases that turned out to be singletons.
    num_re = re.compile(r"^(.*) (\d+)$")
    for row in touched:
        m = num_re.match(row[col["friendly_name"]])
        if not m or m.group(2) != "1":
            continue
        base, level = m.group(1), row[col["level_id"]]
        siblings = [r for r in body if r[col["level_id"]] == level and
                    (r[col["friendly_name"]] == base or num_re.match(r[col["friendly_name"]]) and
                     num_re.match(r[col["friendly_name"]]).group(1) == base)]
        if len(siblings) == 1:
            print(f"  singleton: {row[col['friendly_name']]!r} -> {base!r}")
            row[col["friendly_name"]] = base

    for key in missing:
        print(f"WARNING: no row for {':'.join(key)} (skipped)")
    print(f"{changed} rows changed, {len(missing)} missing.")

    if args.dry_run:
        print("Dry run - nothing written.")
        return 0

    out = io.StringIO()
    csv.writer(out, lineterminator=newline).writerows([header] + body)
    data = out.getvalue().encode("utf-8")
    CSV_PATH.write_bytes(data)
    # apworld/ keeps its own identical copy (see CLAUDE.md, Key Files > Data).
    AP_CSV_PATH.write_bytes(data)
    log_path.rename(log_path.with_name(f"naming_log.applied-{datetime.now():%Y%m%d-%H%M%S}.csv"))
    print(f"Written to {CSV_PATH} and {AP_CSV_PATH}.\n"
          "Now regenerate both: python tools/generate.py  and  python apworld/tools/generate.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
