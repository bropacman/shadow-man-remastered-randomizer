"""
tools/check_pe_headers.py
==========================
Read-only feasibility check for adding a brand-new PE section to
thoth_x64(_patched).exe, as an alternative to hunting for "probably dead"
bytes inside existing .text/.rdata/.data (find_cave.py's approach).

Why this instead of find_cave.py for persistent trap-effect state:
  .text's 0xCC gaps are a real, well-understood convention (16-byte
  function alignment padding) -- safe to reuse. But .rdata/.data "empty"
  runs are just an inference from the file looking zero right now; large
  runs in particular are far more likely to be live runtime buffers than
  genuine dead space, and there's no way to fully rule that out by
  inspecting the file alone. Adding a whole new section sidesteps the
  question entirely: it's space that didn't exist before we added it, so
  there's no ambiguity about whether the game already owns it.

This script does NOT modify the exe. It only reports whether there's room
to add one more IMAGE_SECTION_HEADER (40 bytes) in the header slack before
the first section's raw data begins -- the standard constraint for adding
a section without having to relocate every existing section's file offset.

Usage:
    python tools/check_pe_headers.py "C:\\path\\to\\thoth_x64_patched.exe"
"""
import struct
import sys
from pathlib import Path


def u16(b, off): return struct.unpack_from("<H", b, off)[0]
def u32(b, off): return struct.unpack_from("<I", b, off)[0]
def u64(b, off): return struct.unpack_from("<Q", b, off)[0]


def main():
    if len(sys.argv) != 2:
        print("Usage: python tools/check_pe_headers.py <path-to-exe>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    data = path.read_bytes()

    # --- DOS header ---
    if data[0:2] != b"MZ":
        print("ERROR: not an MZ file")
        sys.exit(1)
    pe_off = u32(data, 0x3C)
    if data[pe_off:pe_off + 4] != b"PE\x00\x00":
        print("ERROR: PE signature not found at e_lfanew")
        sys.exit(1)

    # --- COFF header (right after PE signature) ---
    coff_off = pe_off + 4
    machine = u16(data, coff_off + 0)
    num_sections = u16(data, coff_off + 2)
    size_opt_hdr = u16(data, coff_off + 16)
    characteristics = u16(data, coff_off + 18)

    opt_off = coff_off + 20
    magic = u16(data, opt_off)
    is_pe32plus = (magic == 0x20B)
    print(f"Machine: 0x{machine:X}   PE32+: {is_pe32plus}   Sections: {num_sections}")
    if not is_pe32plus:
        print("WARNING: not PE32+ (not x64) -- offsets below assume PE32+, double-check")

    # PE32+ optional header field offsets (relative to opt_off)
    section_alignment = u32(data, opt_off + 32)
    file_alignment = u32(data, opt_off + 36)
    size_of_image = u32(data, opt_off + 56)
    size_of_headers = u32(data, opt_off + 60)
    image_base = u64(data, opt_off + 24)

    print(f"ImageBase: 0x{image_base:X}")
    print(f"SectionAlignment: 0x{section_alignment:X}   FileAlignment: 0x{file_alignment:X}")
    print(f"SizeOfImage: 0x{size_of_image:X}   SizeOfHeaders: 0x{size_of_headers:X}")

    # --- Section table ---
    sect_table_off = opt_off + size_opt_hdr
    sections = []
    for i in range(num_sections):
        off = sect_table_off + i * 40
        name = data[off:off + 8].rstrip(b"\x00").decode(errors="replace")
        virt_size = u32(data, off + 8)
        virt_addr = u32(data, off + 12)
        raw_size = u32(data, off + 16)
        raw_ptr = u32(data, off + 20)
        chars = u32(data, off + 36)
        sections.append({
            "name": name, "virt_size": virt_size, "virt_addr": virt_addr,
            "raw_size": raw_size, "raw_ptr": raw_ptr, "chars": chars,
        })

    print("\nExisting sections:")
    for s in sections:
        r = "R" if s["chars"] & 0x40000000 else "-"
        w = "W" if s["chars"] & 0x80000000 else "-"
        x = "X" if s["chars"] & 0x20000000 else "-"
        print(f"  {s['name']:<10} VA 0x{image_base + s['virt_addr']:X}  "
              f"VirtSize 0x{s['virt_size']:X}  RawPtr 0x{s['raw_ptr']:X}  "
              f"RawSize 0x{s['raw_size']:X}  [{r}{w}{x}]")

    sect_table_end = sect_table_off + num_sections * 40
    first_raw_ptr = min(s["raw_ptr"] for s in sections if s["raw_ptr"] > 0)

    print(f"\nSection table ends at file offset 0x{sect_table_end:X}")
    print(f"First section's raw data begins at file offset 0x{first_raw_ptr:X}")
    slack = first_raw_ptr - sect_table_end
    print(f"Header slack available: {slack} bytes ({slack // 40} more "
          f"section header(s) worth of room)")

    can_add = slack >= 40
    print(f"\n=> Room for one more IMAGE_SECTION_HEADER (40 bytes): "
          f"{'YES' if can_add else 'NO'}")

    if can_add:
        last = max(sections, key=lambda s: s["virt_addr"])

        def align_up(v, a):
            return (v + a - 1) // a * a

        new_virt_addr = align_up(last["virt_addr"] + last["virt_size"], section_alignment)
        new_raw_ptr = align_up(len(data), file_alignment)
        new_va = image_base + new_virt_addr

        print(f"\nProposed new section would start at:")
        print(f"  VA:              0x{new_va:X}")
        print(f"  File offset:     0x{new_raw_ptr:X}  (appended past current EOF, len=0x{len(data):X})")
        print(f"  Must round its VirtualSize/RawSize up to multiples of "
              f"0x{section_alignment:X} / 0x{file_alignment:X} respectively")
        print(f"  SizeOfImage would need to grow from 0x{size_of_image:X} to at least "
              f"0x{align_up(new_virt_addr + section_alignment, section_alignment):X} "
              f"(for a minimal 1-page section)")
        print(f"\n  NumberOfSections would need to increment from {num_sections} to {num_sections + 1}")
        print(f"  Characteristics for a R+W(+X) data/code section: "
              f"0xC0000040 (R+W) or 0xE0000020 (R+X+shared-code-style) -- "
              f"pick R+W only if no code will live there, R+W+X if it will")
    else:
        print("\nNo room for a new section header without relocating the "
              "section table (and therefore every section's file offsets) "
              "-- would need a different approach (e.g. extending the "
              "last section's VirtualSize into its own alignment padding, "
              "if any exists).")


if __name__ == "__main__":
    main()
