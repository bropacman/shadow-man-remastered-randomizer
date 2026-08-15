"""
detect_renderer.py — figure out which graphics API thoth_x64_patched.exe uses,
so the overlay DLL hooks the right present function.

Pure-stdlib PE import-table parser. Run it on your Windows machine, in the
game's install folder (or pass the path):

    python detect_renderer.py "C:\\Path\\To\\thoth_x64_patched.exe"

No dependencies, no need for Ghidra/CE for this one. Paste the output back.
"""
import struct
import sys
from pathlib import Path


def read_pe_imports(path: Path) -> list[str]:
    data = path.read_bytes()

    if data[:2] != b"MZ":
        raise ValueError("not a PE file (missing MZ header)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]

    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("not a PE file (missing PE signature)")

    coff_off = e_lfanew + 4
    machine, num_sections = struct.unpack_from("<HH", data, coff_off)
    opt_header_size = struct.unpack_from("<H", data, coff_off + 16)[0]
    opt_header_off = coff_off + 20
    magic = struct.unpack_from("<H", data, opt_header_off)[0]
    is_pe32_plus = magic == 0x20B  # PE32+ (x64)

    # Data directories: RVA/size pairs, import table is entry #1 (index 1)
    if is_pe32_plus:
        num_rva_and_sizes_off = opt_header_off + 108
        data_dir_off = opt_header_off + 112
    else:
        num_rva_and_sizes_off = opt_header_off + 92
        data_dir_off = opt_header_off + 96

    import_dir_rva, import_dir_size = struct.unpack_from(
        "<II", data, data_dir_off + 8 * 1)

    # Section table, right after optional header
    section_table_off = opt_header_off + opt_header_size
    sections = []
    for i in range(num_sections):
        off = section_table_off + i * 40
        name = data[off:off + 8].rstrip(b"\x00").decode(errors="replace")
        virt_size, virt_addr = struct.unpack_from("<II", data, off + 8)
        raw_size, raw_ptr = struct.unpack_from("<II", data, off + 16)
        sections.append((name, virt_addr, virt_size, raw_ptr, raw_size))

    def rva_to_offset(rva: int) -> int:
        for name, virt_addr, virt_size, raw_ptr, raw_size in sections:
            if virt_addr <= rva < virt_addr + max(virt_size, raw_size):
                return raw_ptr + (rva - virt_addr)
        raise ValueError(f"RVA 0x{rva:X} not in any section")

    def read_cstr(offset: int) -> str:
        end = data.index(b"\x00", offset)
        return data[offset:end].decode(errors="replace")

    dll_names = []
    entry_off = rva_to_offset(import_dir_rva)
    IMAGE_IMPORT_DESCRIPTOR_SIZE = 20
    while True:
        (orig_first_thunk, timestamp, forwarder_chain, name_rva,
         first_thunk) = struct.unpack_from("<IIIII", data, entry_off)
        if name_rva == 0:
            break
        dll_names.append(read_cstr(rva_to_offset(name_rva)))
        entry_off += IMAGE_IMPORT_DESCRIPTOR_SIZE

    return dll_names


GRAPHICS_DLLS = {
    "d3d9.dll":        "Direct3D 9",
    "d3d10.dll":       "Direct3D 10",
    "d3d11.dll":       "Direct3D 11",
    "d3d12.dll":       "Direct3D 12",
    "dxgi.dll":        "DXGI (used by D3D10/11/12 — check which d3dNN.dll is also present)",
    "opengl32.dll":    "OpenGL",
    "vulkan-1.dll":    "Vulkan",
    "sdl2.dll":        "SDL2 (windowing — doesn't tell us the render API by itself)",
}


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        path = Path("thoth_x64_patched.exe")

    if not path.exists():
        print(f"File not found: {path}")
        print("Pass the full path as an argument, e.g.:")
        print(r'  python detect_renderer.py "C:\Games\Shadow Man Remastered\thoth_x64_patched.exe"')
        sys.exit(1)

    print(f"Reading imports from: {path}\n")
    imports = read_pe_imports(path)

    print("All imported DLLs:")
    for name in imports:
        print(f"  {name}")

    print("\nGraphics-related imports found:")
    hits = [n for n in imports if n.lower() in GRAPHICS_DLLS]
    if not hits:
        print("  (none matched known graphics DLL names directly — game may load")
        print("   its renderer dynamically via LoadLibrary at runtime instead of")
        print("   static import. If so, this static scan won't catch it — let me")
        print("   know and we'll check loaded modules at runtime instead, e.g.")
        print("   via Process Explorer / Cheat Engine's module list while the")
        print("   game is running.)")
    for name in hits:
        print(f"  {name:15} -> {GRAPHICS_DLLS[name.lower()]}")


if __name__ == "__main__":
    main()
