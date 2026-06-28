"""
verify_retractor_xyz.py
=======================
After running the randomizer, verify that the levels_hints.txt directive
XYZ values match the RSC binary XYZ values for retractors/accumulators.

Usage:
    python tools/verify_retractor_xyz.py <work_dir>

<work_dir> is the _randomizer_work_<seed> folder next to thoth_x64.exe.
Example:
    python tools/verify_retractor_xyz.py "C:/ShadowMan/_randomizer_work_12345"
"""
import struct, re, sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python tools/verify_retractor_xyz.py <work_dir>")
    sys.exit(1)

work = Path(sys.argv[1])
hints_txt = work / "scripts" / "levels_hints.txt"
levels_dir = work / "levels"

if not hints_txt.exists():
    print(f"Not found: {hints_txt}")
    sys.exit(1)
if not levels_dir.exists():
    print(f"Not found: {levels_dir}")
    sys.exit(1)

RSC_TARGETS = {"RSC_X_RETRACT", "RSC_X_RETRACT1", "RSC_X_RETRACT2", "RSC_X_ACCUMULATOR"}
NAME_OFF, XYZ_OFF, REC_SIZE = 0x22, 0x04, 72

def find_rsc_records(rsc_path):
    data = rsc_path.read_bytes()
    out = []
    n = (len(data) - 8) // REC_SIZE
    for i in range(n):
        base = 8 + i * REC_SIZE
        name = data[base + NAME_OFF: base + NAME_OFF + 30].split(b'\x00')[0].decode('ascii', errors='replace')
        if name in RSC_TARGETS:
            x, y, z = struct.unpack_from('<fff', data, base + XYZ_OFF)
            out.append((name, x, y, z, rsc_path.parent.name))
    return out

rsc_records = []
for rsc_path in sorted(levels_dir.rglob("*.rsc")):
    rsc_records.extend(find_rsc_records(rsc_path))

print(f"RSC records found: {len(rsc_records)}")

directive_re = re.compile(
    r'^\s*\$(retractor|accumulator)\s+"[^"]*"\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)',
    re.MULTILINE | re.IGNORECASE
)
directives = [(m.group(1).lower(), float(m.group(2)), float(m.group(3)), float(m.group(4)))
              for m in directive_re.finditer(hints_txt.read_text())]
print(f"Directive entries found: {len(directives)}\n")

EPS = 1e-5
all_ok = True
for name, rx, ry, rz, level in rsc_records:
    dtype_key = "accumulator" if "ACCUMULATOR" in name else "retractor"
    best, best_err = None, float('inf')
    for dtype, dx, dy, dz in directives:
        if dtype != dtype_key:
            continue
        err = max(abs(dx - rx), abs(dy - ry), abs(dz - rz))
        if err < best_err:
            best_err, best = err, (dx, dy, dz)

    ok = best_err < EPS
    if not ok:
        all_ok = False
    status = "✓" if ok else "✗ MISMATCH"
    print(f"{status}  {name} in {level}")
    print(f"     RSC  X={rx:.6f}  Y={ry:.6f}  Z={rz:.6f}")
    if best:
        dx, dy, dz = best
        print(f"     DIR  X={dx:.6f}  Y={dy:.6f}  Z={dz:.6f}")
        print(f"     maxΔ = {best_err:.2e}")
    else:
        print(f"     DIR  (no matching directive found)")
    print()

print("All match within 1e-5." if all_ok else "MISMATCHES FOUND — directive Y != RSC Y, badge won't clear.")
