// dllmain.cpp — entry point for the injected overlay DLL.
//
// Heavy init (MinHook, dummy D3D device creation, socket listener) is
// deliberately NOT done inline in DllMain — DllMain runs under the
// loader lock, and creating a D3D device / window / thread from inside
// it is a well-known source of deadlocks. We spawn a worker thread and
// do everything there instead.
#include <windows.h>
#include <MinHook.h>
#include <string>
#include <vector>

#include "hook_d3d11.h"
#include "ipc_server.h"

// Must match OVERLAY_IPC_PORT in client.py.
constexpr unsigned short kIpcPort = 31727;

// How long to wait after injecting before assuming client.py isn't
// already running elsewhere and trying to launch it ourselves — see
// TryAutoLaunchClient's own comment.
//
// Widened 4000 -> 15000 (2026-08-05): Jon reported a redundant SECOND
// console window opening in addition to the real client's — root cause
// was this racing against a legitimately-slow client.py startup rather
// than detecting a genuinely absent client. A real client launched via
// ap_gui.py's own "Launch Game + Client" button (or launch_game.bat) has
// to go through Launcher.py -> multiprocessing "spawn" -> a full re-import
// of the entire Archipelago/kivy module tree in the new child process
// before it ever reaches client.py's own launch() -- easily several
// seconds on a cold disk cache, well past the old 4s window -- and only
// AFTER that does it connect to this DLL's IPC socket, which is the only
// signal HasClientEverConnected() could see. See IsClientMutexHeld()
// below for the other half of this fix — an earlier-arriving signal that
// narrows, but doesn't fully close, the same race.
constexpr DWORD kAutoLaunchGraceMs = 15000;

static HANDLE g_initThread = nullptr;
static HMODULE g_hModule = nullptr;

// Directory this DLL itself lives in — same technique overlay.cpp's own
// GetOverlayDllDir() uses; duplicated here (a handful of lines) rather
// than shared through a header, since dllmain.cpp otherwise has no
// dependency on overlay.cpp at all.
static std::wstring GetSelfDir() {
    wchar_t path[MAX_PATH];
    DWORD len = GetModuleFileNameW(g_hModule, path, MAX_PATH);
    if (len == 0 || len == MAX_PATH) return L"";
    std::wstring full(path);
    size_t slash = full.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"" : full.substr(0, slash);
}

// True if a client.py instance already exists, whether or not it's
// reached the point of connecting to our IPC socket yet (2026-08-05) —
// checks client.py's own named single-instance mutex
// (Global\ShadowManAPClientSingleton, see client.py's
// _acquire_singleton_lock()) directly via a plain OpenMutexW, rather than
// only trusting IPC connection state. client.py creates this mutex very
// early in its own launch() — well before it finishes the slow
// import/connect chain described above — so this often notices a
// legitimately-starting client sooner than the IPC socket ever could.
// Pure OS handle query, zero execution risk, same safety class as every
// other "check without touching the game" helper in this codebase. Still
// not a perfect fix for the race on its own (the mutex itself doesn't
// exist until multiprocessing's spawn re-import has gotten far enough to
// reach launch()) — that's why kAutoLaunchGraceMs was also widened above
// rather than relying on this check alone.
static bool IsClientMutexHeld() {
    HANDLE h = OpenMutexW(SYNCHRONIZE, FALSE, L"Global\\ShadowManAPClientSingleton");
    if (h) {
        CloseHandle(h);
        return true;
    }
    return false;
}

// Optional one-time convenience (2026-08-04): if a `launch_client.bat`
// file sits next to this DLL AND nothing has connected to the overlay IPC
// socket within a few seconds of injection, run it — same as if the
// player had double-clicked it themselves. Entirely inert by default:
// nothing about this changes today's behavior unless that file exists.
// Deliberately never guesses at a python.exe/venv/client.py path itself —
// that's on whoever sets up launch_client.bat (the player, or a future
// installer step) to get right for their own setup, since this DLL has no
// reliable way to know it. This is also a fundamentally different, much
// lower-risk kind of "reach outside this process" than everything else
// this project's CLAUDE.md documents: CreateProcess starts a brand new,
// completely independent process (exactly like Explorer double-clicking
// the file would), never touches the game's own memory or threads the way
// the CreateRemoteThread-based injection helpers in client.py do.
static void TryAutoLaunchClient() {
    Sleep(kAutoLaunchGraceMs);
    // Either signal is enough to skip -- HasClientEverConnected() catches
    // a client that's already fully up and talking to us; IsClientMutexHeld()
    // additionally catches one that's still mid-startup (see its own
    // comment above for why that matters).
    if (GetIpcServer().HasClientEverConnected() || IsClientMutexHeld()) return;

    std::wstring batPath = GetSelfDir() + L"\\launch_client.bat";
    if (GetFileAttributesW(batPath.c_str()) == INVALID_FILE_ATTRIBUTES) {
        return; // nothing to launch — silently do nothing, same as today
    }

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};

    // cmd.exe /c "<batPath>" — runs it exactly like double-clicking it, in
    // its own new console/process, independent of the game's lifetime.
    // CreateProcessW requires a mutable command-line buffer.
    std::wstring cmdLine = L"cmd.exe /c \"" + batPath + L"\"";
    std::vector<wchar_t> cmdLineBuf(cmdLine.begin(), cmdLine.end());
    cmdLineBuf.push_back(L'\0');

    if (CreateProcessW(nullptr, cmdLineBuf.data(), nullptr, nullptr, FALSE,
                        CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
}

static DWORD WINAPI InitThread(LPVOID) {
    GetIpcServer().Start(kIpcPort);
    HookD3D11::Install();
    TryAutoLaunchClient();
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    switch (reason) {
        case DLL_PROCESS_ATTACH:
            g_hModule = hModule;
            DisableThreadLibraryCalls(hModule);
            g_initThread = CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
            break;
        case DLL_PROCESS_DETACH:
            HookD3D11::Uninstall();
            GetIpcServer().Stop();
            if (g_initThread) CloseHandle(g_initThread);
            break;
    }
    return TRUE;
}
