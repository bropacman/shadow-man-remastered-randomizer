"""
check_cadeaux_counts.py — Compare CSV cadeaux counts vs levels.txt per level.

Usage:
    python tools/check_cadeaux_counts.py
    python tools/check_cadeaux_counts.py --csv data/locations.csv
    python tools/check_cadeaux_counts.py --levels-txt reference/levels.txt
"""

import argparse
import csv
import re
from collections import defaultdict

LEVEL_DIR_MAP = {
    0:  ["swampday", "swampnit"],
    1:  ["tenement", "ntenemnt"],
    2:  ["prison",   "nprison"],
    3:  ["uground",  "nuground"],
    4:  ["florida",  "nflorida"],
    5:  ["salvage",  "nsalvage"],
    6:  ["deadside"],
    7:  ["wastland"],
    8:  ["asylum"],
    9:  ["as2exper"],
    10: ["as3schis"],
    11: ["as4dkeng"],
    12: ["t1tchgad"],
    13: ["ah1cagew"],
    14: ["ah2playr"],
    15: ["t2wlkgad"],
    16: ["ah3lavad"],
    17: ["t3swmgad"],
    18: ["ah4fogom"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default="data/locations.csv")
    parser.add_argument("--levels-txt", default="reference/levels.txt")
    args = parser.parse_args()

    with open(args.levels_txt) as f:
        content = f.read()

    level_counts: dict[int, int] = {}
    for block in re.split(r"(?=^\$level\s)", content, flags=re.MULTILINE):
        m_lvl = re.match(r"\$level\s+(\d+)", block)
        m_cad = re.search(r"\$cadeaux\s+(\d+)", block)
        if m_lvl and m_cad:
            level_counts[int(m_lvl.group(1))] = int(m_cad.group(1))

    csv_counts: dict[str, int] = defaultdict(int)
    with open(args.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("category", "").strip().lower() == "cadeaux":
                csv_counts[row["level_id"]] += 1

    print("%-4s %-32s %5s %5s %5s" % ("Lvl", "Name", "txt", "csv", "diff"))
    print("-" * 58)
    total_txt = total_csv = 0
    problems = []
    for lvl_num in sorted(LEVEL_DIR_MAP):
        dirs = LEVEL_DIR_MAP[lvl_num]
        txt = level_counts.get(lvl_num, 0)
        csv_n = sum(csv_counts[d] for d in dirs)
        diff = csv_n - txt
        m = re.search(r"\$level\s+%d\s+//(.+)" % lvl_num, content)
        name = (m.group(1).strip() if m else "?")[:32]
        flag = "" if diff == 0 else ("  ← %+d" % diff)
        if diff != 0:
            problems.append((lvl_num, name, diff))
        print("%-4d %-32s %5d %5d%s" % (lvl_num, name, txt, csv_n, flag))
        total_txt += txt
        total_csv += csv_n

    print("-" * 58)
    net = total_csv - total_txt
    print("%-37s %5d %5d  %+d" % ("TOTAL", total_txt, total_csv, net))

    if problems:
        print(f"\nLevels with mismatches ({len(problems)}):")
        for lvl_num, name, diff in problems:
            print(f"  level {lvl_num} {name}: {'+' if diff>0 else ''}{diff}")
    else:
        print("\nAll levels match! ✓")


if __name__ == "__main__":
    main()