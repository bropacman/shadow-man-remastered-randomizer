cmake_minimum_required(VERSION 3.18)
project(ShadowManOverlay LANGUAGES C CXX)

if (NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "Build as x64 — thoth_x64_patched.exe is a 64-bit process, "
                         "a 32-bit DLL cannot be injected into it.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)

# We want raw source only, not upstream's own build system — MinHook ships
# a CMakeLists.txt that already defines a target named "minhook", which
# collides with the one we define below if we let FetchContent_MakeAvailable
# auto-add_subdirectory it. FetchContent_Populate fetches the source without
# building it, so we stay in full control of what actually gets compiled.
FetchContent_Declare(minhook_src
    GIT_REPOSITORY https://github.com/TsudaKageyu/minhook.git
    GIT_TAG        master
)
FetchContent_Declare(imgui_src
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG        master
)

FetchContent_GetProperties(minhook_src)
if (NOT minhook_src_POPULATED)
    FetchContent_Populate(minhook_src)
endif()

FetchContent_GetProperties(imgui_src)
if (NOT imgui_src_POPULATED)
    FetchContent_Populate(imgui_src)
endif()

# ── MinHook ───────────────────────────────────────────────────────────────
add_library(minhook STATIC
    ${minhook_src_SOURCE_DIR}/src/buffer.c
    ${minhook_src_SOURCE_DIR}/src/hook.c
    ${minhook_src_SOURCE_DIR}/src/trampoline.c
    ${minhook_src_SOURCE_DIR}/src/hde/hde32.c
    ${minhook_src_SOURCE_DIR}/src/hde/hde64.c
)
target_include_directories(minhook PUBLIC
    ${minhook_src_SOURCE_DIR}/include
    ${minhook_src_SOURCE_DIR}/src
)

# ── Dear ImGui (core + win32/dx11 backends only, no demo) ──────────────────
add_library(imgui STATIC
    ${imgui_src_SOURCE_DIR}/imgui.cpp
    ${imgui_src_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_src_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_src_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_src_SOURCE_DIR}/backends/imgui_impl_win32.cpp
    ${imgui_src_SOURCE_DIR}/backends/imgui_impl_dx11.cpp
)
target_include_directories(imgui PUBLIC
    ${imgui_src_SOURCE_DIR}
    ${imgui_src_SOURCE_DIR}/backends
)

# ── Overlay DLL ──────────────────────────────────────────────────────────
add_library(ShadowManOverlay SHARED
    src/dllmain.cpp
    src/hook_d3d11.cpp
    src/overlay.cpp
    src/ipc_server.cpp
)
target_link_libraries(ShadowManOverlay PRIVATE minhook imgui d3d11 dxgi ws2_32)
target_compile_definitions(ShadowManOverlay PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN)
set_target_properties(ShadowManOverlay PROPERTIES OUTPUT_NAME "ShadowManOverlay")

if (MSVC)
    target_compile_options(ShadowManOverlay PRIVATE /W3)
endif()
