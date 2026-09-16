#include "overlay.h"
#include "ipc_server.h"
#include "json_mini.h"

#include "imgui.h"
#include "backends/imgui_impl_win32.h"
#include "backends/imgui_impl_dx11.h"

// imgui_impl_win32.h deliberately does NOT declare this for you -- its own
// prototype is wrapped in "#if 0" specifically so the header doesn't force a
// <windows.h> dependency on every consumer. Its own comment says to copy the
// line into your .cpp instead, which is what this is (windows.h is already
// pulled in via overlay.h, included above, so HWND/UINT/WPARAM/LPARAM are
// available here). The function itself is still compiled into the imgui
// static library via imgui_impl_win32.cpp and links fine -- omitting this
// forward declaration is a compile-time-only error ("identifier not found"),
// not a missing symbol.
extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam);

#include <deque>
#include <chrono>
#include <string>
#include <cstring>
#include <vector>
#include <fstream>
#include <sstream>

namespace {

struct Toast {
    std::string kind;
    std::string title;
    std::string subtitle;
    std::chrono::steady_clock::time_point spawnedAt;
};

// "stay on screen a little longer" — was 4500/350.
constexpr int kToastLifetimeMs = 7500;
constexpr int kFadeMs = 400;
constexpr size_t kMaxVisible = 5;
constexpr float kTitleFontSize = 30.0f;   // was ImGui's default ~13px
constexpr float kSubtitleFontScale = 0.68f; // relative to kTitleFontSize
constexpr size_t kHistoryMaxEntries = 15;
// Shared by toasts, history, and the connect panel so all three HUD
// elements line up in the same top-right column at the same width.
constexpr float kHudMargin = 20.0f;
constexpr float kHudWidth = 380.0f;

bool g_initialized = false;
HWND g_hwnd = nullptr;
std::deque<Toast> g_toasts;
std::deque<Toast> g_history; // never expires on its own, just caps at kHistoryMaxEntries
ImFont* g_font = nullptr; // custom themed font if found, else scaled-up default
bool g_historyVisible = true;
constexpr int kHistoryToggleKey = VK_F2; // was VK_F9, then briefly VK_F1 (swapped with panel 2026-08-30, Jon's ask)

// ── Connect / console panel (2026-08-04, redesigned 2026-08-05) ─────────
// See CLAUDE.md's "client injected into the DLL" writeup for the full
// rationale: client.py keeps owning the real AP connection and all of its
// already-hardened injection/safety logic unchanged — this panel is only
// a thin in-game front end that queues a couple of JSON lines for
// client.py to act on (ipc_server.cpp's SendToClient), the same way a
// person would type a server address or a /command into client.py's own
// terminal window.
//
// REVERTED 2026-08-05 (same day as the split-flag attempt below was
// tried): the WantCaptureMouse/WantCaptureKeyboard selective-forwarding
// design couldn't actually be reached in practice — while the cursor
// isn't grabbed, the game still owns and clips/hides it for camera-look,
// so there was never any way to move the OS cursor onto the panel to
// hover it in the first place, regardless of how forwarding worked once
// grabbed. Back to a single flag and the original "F10 grabs the cursor
// AND owns input while open" modal design, which was already proven
// working live. What's new this round instead: the panel now defaults to
// a MINIMIZED one-line hint (matching the F9/history log's own minimized
// state) rather than being fully invisible until F10 — so a new player
// sees it exists without it taking over anything by default.
bool g_panelExpanded = false; // F1 toggles this: minimized hint <-> full interactive panel
constexpr int kPanelToggleKey = VK_F1; // was VK_F10, then briefly VK_F2 (swapped with history 2026-08-30, Jon's ask)
WNDPROC g_originalWndProc = nullptr;

// F2 can now also pop the cursor out on its own (2026-08-30, Jon's ask) --
// previously ONLY g_panelExpanded (F1) gated cursor-grab/input-forwarding,
// so repositioning the history log required opening the connect panel
// too. g_historyGrabWanted is F2's own independent "I want the cursor"
// flag (see its toggle in Render() below); g_cursorGrabbed is the union of
// both, recomputed once per frame in Render() and read by WndProcHook and
// everything else that used to check g_panelExpanded directly for cursor/
// input purposes -- g_panelExpanded itself still means exactly what it
// always did (is the connect panel's own UI shown), nothing else changed
// about it.
bool g_historyGrabWanted = false;
bool g_cursorGrabbed = false;

// Persistent connect-panel state (2026-08-05) — separate from the
// transient toast/history feed below, which still shows every one of
// these events the normal way too. Driven by client.py's own connection
// lifecycle hooks (ShadowManContext.handle_connection_loss/
// connection_closed/on_package "Connected"), forwarded here as ordinary
// IPC events so the panel can show a real Connect/Disconnect button and
// state banner instead of the click having no visible effect until (or
// unless) a toast happens to show up.
enum class ApConnState { Disconnected, Connecting, Connected, Failed };
ApConnState g_apConnState = ApConnState::Disconnected;
std::string g_apConnDetail; // "Connected to ..." text, or a failure reason

// Cursor visibility/clipping state (2026-08-05). Live testing found the
// panel's mouse was "jittery and blinks in and out" and clicks didn't
// register -- neither is a WndProc-hookable problem: many games (this one
// included, evidently) call ClipCursor() every frame to pin the OS cursor
// for camera-look, and/or call ShowCursor(FALSE) in their own per-frame
// input loop, completely independent of window messages. WndProcHook can
// only ever affect messages routed through the window's message queue, so
// it can't intercept either of these direct Win32 API calls made from
// inside the game's own code. Fixed by fighting both explicitly, every
// frame, while the panel is open, and cleanly restoring exactly what we
// changed on close.
int g_cursorShowCount = 0;      // net ShowCursor(TRUE) calls we've made, to unwind precisely on close
RECT g_savedClipRect{};
bool g_hadSavedClip = false;

// Last known screen (client-area) rect of the expanded connect panel
// (2026-08-05) — set by RenderConnectPanel() itself (via GetWindowPos()/
// GetWindowSize(), same window the player actually sees) right after it
// draws each frame. Used by the click-outside-to-minimize check below,
// which runs BEFORE this frame's own RenderConnectPanel() call (it has to:
// ImGui needs the mouse-click event fed in before NewFrame(), while the
// panel itself isn't drawn until after) — so it's always exactly one frame
// stale. Harmless: the panel's position is fixed and its size only changes
// when its own content changes (e.g. "no client detected" vs. connected),
// which isn't something that happens in the same frame as a click.
ImVec2 g_panelRectPos{};
ImVec2 g_panelRectSize{};

// Same idea as g_panelRectPos/g_panelRectSize above, but for the draggable
// history log window (2026-08-30, Jon's ask) -- the click-outside-to-
// minimize check below needs to also exempt this rect now that the
// history window can sit somewhere other than directly under the panel.
// Only valid (g_historyRectValid) on frames where the full, draggable
// ##ap_history window actually drew -- not the fixed ##ap_history_hint.
ImVec2 g_historyRectPos{};
ImVec2 g_historyRectSize{};
bool g_historyRectValid = false;

// Where the live item-toast stack should start (2026-08-31, Jon's ask) --
// toasts used to always anchor to the fixed top-right corner below the
// connect panel, ignoring wherever the history log/hint had actually been
// dragged to. Same one-frame-stale idea as g_panelRectPos/g_historyRectPos
// above: the history block (further down in Render()) overwrites this with
// its own just-drawn bottom-left corner every frame, and the toast loop
// (which runs BEFORE the history block this same frame) reads whatever was
// left here by the PREVIOUS frame -- imperceptible at 60fps, and avoids
// having to reorder rendering or predict this frame's history layout
// ahead of time. Valid unless history hasn't rendered even once yet (e.g.
// the very first launch, before any location check exists to log) --
// toasts fall back to the original below-the-panel column in that case.
ImVec2 g_historyBottomAnchor{};
bool g_historyBottomValid = false;

// The actual OS-level cursor cleanup (2026-08-30, Jon's ask) -- restores
// whatever cursor-clip the game had before we grabbed it, unwinds our own
// ShowCursor(TRUE) calls, and turns NavEnableKeyboard back off. Factored
// out of CollapsePanel() (which used to do this directly) so it can be
// triggered by g_cursorGrabbed -- the UNION of g_panelExpanded and
// g_historyGrabWanted -- going to false in Render(), rather than only the
// panel's own toggle: F2 can now hold the grab open on its own even after
// F1's panel has closed, and this only actually releases the OS resources
// once NEITHER wants it anymore.
void ReleaseCursorGrab() {
    ImGui::GetIO().ConfigFlags &= ~ImGuiConfigFlags_NavEnableKeyboard;
    if (g_hadSavedClip) {
        ClipCursor(&g_savedClipRect);
        g_hadSavedClip = false;
    }
    // Unwind exactly as many ShowCursor(TRUE) calls as were made in the
    // per-frame block while grabbed. ShowCursor's visibility counter is
    // one process-wide shared integer, so undoing our own net contribution
    // is correct regardless of how the game's own ShowCursor(FALSE) calls
    // were interleaved with ours while grabbed.
    while (g_cursorShowCount > 0) {
        ShowCursor(FALSE);
        --g_cursorShowCount;
    }
}

// Closes the connect panel's own UI (2026-08-05) — both the F1 toggle-off
// path and the click-outside path used to do cursor cleanup here directly;
// now that's handled generically by ReleaseCursorGrab() above once the
// combined grab state actually drops to false, so this only ever flips
// g_panelExpanded itself.
void CollapsePanel() {
    if (!g_panelExpanded) return; // already collapsed — nothing to undo
    g_panelExpanded = false;
}

// Releases every independent grab-wanter at once (2026-08-30, Jon's ask) --
// used by the click-outside-to-dismiss path below, so a stray click during
// normal play fully lets go of the cursor regardless of whether F1, F2, or
// both asked for it. Doesn't touch g_historyVisible -- whether the history
// log's content is shown is a separate, persistent preference from the
// transient "is the cursor currently grabbed for repositioning" state.
void DismissHud() {
    CollapsePanel();
    g_historyGrabWanted = false;
}

// REVERTED back to one combined check (2026-08-05) — see g_panelExpanded's
// own comment above for why the split mouse/keyboard, WantCapture-gated
// version was abandoned. Swallows everything while the panel is expanded,
// same proven-working modal behavior as before that attempt.
bool IsInputMessage(UINT msg) {
    switch (msg) {
        case WM_MOUSEMOVE:
        case WM_LBUTTONDOWN: case WM_LBUTTONUP: case WM_LBUTTONDBLCLK:
        case WM_RBUTTONDOWN: case WM_RBUTTONUP: case WM_RBUTTONDBLCLK:
        case WM_MBUTTONDOWN: case WM_MBUTTONUP: case WM_MBUTTONDBLCLK:
        case WM_MOUSEWHEEL: case WM_MOUSEHWHEEL:
        case WM_KEYDOWN: case WM_KEYUP:
        case WM_SYSKEYDOWN: case WM_SYSKEYUP:
        case WM_CHAR: case WM_SYSCHAR:
        case WM_INPUT:
        // Swallowing this specifically stops the game's own cursor-hiding
        // logic from undoing ImGui's SetCursor call every time the mouse
        // moves -- WM_SETCURSOR fires on every WM_MOUSEMOVE, and
        // ImGui_ImplWin32_WndProcHandler (called just above this check,
        // unconditionally, before we ever get here) already handles it and
        // sets the cursor itself. Forwarding it to the game afterward let
        // the game's own handler immediately re-hide the cursor on the
        // same message, which is what live testing showed as the cursor
        // "flashing in and out."
        case WM_SETCURSOR:
            return true;
        default:
            return false;
    }
}

// Subclasses the game's own window so ImGui can actually receive input —
// distinct from (and much lower-risk than) everything else this project's
// CLAUDE.md documents about touching the running game: this never
// executes code inside the game process or reaches across threads, it
// only intercepts window messages the OS already routes through this
// window's message queue, via the same SetWindowLongPtr subclassing
// technique essentially every ImGui-based game overlay/trainer uses.
//
// REVERTED to the original modal design (2026-08-05, same day as the
// WantCaptureMouse/Keyboard attempt above) — while g_panelExpanded is
// false (the default, minimized-hint state), this behaves EXACTLY like no
// hook exists, zero risk to normal gameplay input. Once F10 expands the
// panel, it hands every mouse/keyboard message to ImGui first and then
// swallows it instead of forwarding to the game — the panel owns input
// while it's open, same proven-working behavior confirmed live before the
// selective-forwarding experiment (which couldn't actually be reached in
// practice, since the game clips/hides the cursor for camera-look
// whenever it isn't grabbed, so there was never a way to hover the panel
// to trigger the selective path in the first place). Every non-input
// message (WM_SIZE, WM_ACTIVATE, WM_CLOSE, etc.) always passes through
// regardless of expand state, so window management is never affected
// either way.
LRESULT CALLBACK WndProcHook(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    // Gates on the combined g_cursorGrabbed now, not just g_panelExpanded
    // (2026-08-30, Jon's ask) -- F2 alone can hold this open too, see
    // g_historyGrabWanted's own comment above.
    if (!g_cursorGrabbed) {
        return CallWindowProcW(g_originalWndProc, hwnd, msg, wParam, lParam);
    }

    ImGui_ImplWin32_WndProcHandler(hwnd, msg, wParam, lParam);

    if (IsInputMessage(msg)) {
        return 0;
    }
    return CallWindowProcW(g_originalWndProc, hwnd, msg, wParam, lParam);
}

// Directory the DLL itself lives in, so the custom font can sit right next
// to ShadowManOverlay.dll without needing an absolute path baked in.
std::wstring GetOverlayDllDir() {
    HMODULE hSelf = nullptr;
    GetModuleHandleExW(
        GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&GetOverlayDllDir), &hSelf);
    if (!hSelf) return L"";

    wchar_t path[MAX_PATH];
    DWORD len = GetModuleFileNameW(hSelf, path, MAX_PATH);
    if (len == 0 || len == MAX_PATH) return L"";

    std::wstring full(path);
    size_t slash = full.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"" : full.substr(0, slash);
}

std::string WideToUtf8(const std::wstring& wide) {
    if (wide.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string out(size - 1, '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.c_str(), -1, out.data(), size, nullptr, nullptr);
    return out;
}

// ── Persisted overlay prefs (2026-08-30, Jon's ask) ─────────────────────
// Small flat JSON file living next to the DLL (same directory convention
// as fonts/toast_font.ttf and launch_client.bat above -- see
// GetOverlayDllDir()), remembering where the player last dragged the
// connect panel and history log to, plus the last-used server/name/
// password so the connect panel doesn't start blank every game launch.
// Uses the same json_mini::Parse/Escape machinery already used for the
// IPC protocol below (SendJsonToClient) -- it's a flat string-valued
// object, same shape, just written to disk instead of a socket.
//
// NOTE: the password is stored in plain text in this file, same as
// everywhere else in this project (client.py/ap_gui.py don't encrypt it
// either). Archipelago room passwords are typically low-stakes (shared
// with your co-op group), but worth knowing if this file might ever end
// up somewhere more exposed than your own machine.
struct OverlayPrefs {
    bool hasPanelPos = false;
    ImVec2 panelPos{};
    bool hasHistoryPos = false;
    ImVec2 historyPos{};
    std::string server = "archipelago.gg:38281";
    std::string name;
    std::string password;
};

std::wstring GetPrefsPath() {
    std::wstring dir = GetOverlayDllDir();
    if (dir.empty()) return L"";
    return dir + L"\\ap_overlay_prefs.json";
}

OverlayPrefs LoadPrefsFromDisk() {
    OverlayPrefs prefs;
    std::wstring path = GetPrefsPath();
    if (path.empty()) return prefs;

    std::ifstream in(path, std::ios::binary);
    if (!in) return prefs; // no file yet -- first run ever, defaults stand

    std::ostringstream ss;
    ss << in.rdbuf();
    std::unordered_map<std::string, std::string> fields;
    if (!json_mini::Parse(ss.str(), fields)) return prefs;

    auto getFloat = [&](const char* key, float& out) -> bool {
        auto it = fields.find(key);
        if (it == fields.end() || it->second.empty()) return false;
        try {
            out = std::stof(it->second);
            return true;
        } catch (...) {
            return false;
        }
    };

    float px, py, hx, hy;
    if (getFloat("panel_x", px) && getFloat("panel_y", py)) {
        prefs.hasPanelPos = true;
        prefs.panelPos = ImVec2(px, py);
    }
    if (getFloat("history_x", hx) && getFloat("history_y", hy)) {
        prefs.hasHistoryPos = true;
        prefs.historyPos = ImVec2(hx, hy);
    }
    auto itServer = fields.find("server");
    if (itServer != fields.end() && !itServer->second.empty()) prefs.server = itServer->second;
    auto itName = fields.find("name");
    if (itName != fields.end()) prefs.name = itName->second;
    auto itPassword = fields.find("password");
    if (itPassword != fields.end()) prefs.password = itPassword->second;
    return prefs;
}

// Mirrors SendJsonToClient's own flat-object-builder pattern further down
// this file, just writing to disk instead of the IPC socket.
void SavePrefsToDisk(const OverlayPrefs& prefs) {
    std::wstring path = GetPrefsPath();
    if (path.empty()) return;

    std::ostringstream json;
    json << "{";
    json << "\"panel_x\":\"" << prefs.panelPos.x << "\",";
    json << "\"panel_y\":\"" << prefs.panelPos.y << "\",";
    json << "\"history_x\":\"" << prefs.historyPos.x << "\",";
    json << "\"history_y\":\"" << prefs.historyPos.y << "\",";
    json << "\"server\":\"" << json_mini::Escape(prefs.server) << "\",";
    json << "\"name\":\"" << json_mini::Escape(prefs.name) << "\",";
    json << "\"password\":\"" << json_mini::Escape(prefs.password) << "\"";
    json << "}";

    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) return;
    out << json.str();
}

// Loaded once, lazily, the first time anything needs it -- Init() runs
// before any window has a size/position to seed defaults from, so there's
// no earlier point that would actually be useful to load at.
OverlayPrefs& GetPrefs() {
    static OverlayPrefs prefs = LoadPrefsFromDisk();
    return prefs;
}

// Bounded copy into a fixed ImGui input buffer, used to seed serverBuf/
// nameBuf/passwordBuf from a loaded std::string without pulling in
// strncpy_s (CRT-version-specific) just for this.
void SeedBuf(char* buf, size_t bufSize, const std::string& value) {
    if (value.empty() || bufSize == 0) return;
    size_t n = value.size() < bufSize - 1 ? value.size() : bufSize - 1;
    std::memcpy(buf, value.data(), n);
    buf[n] = '\0';
}

// Same mutex check as dllmain.cpp's IsClientMutexHeld() (2026-08-05) —
// duplicated here for the same "small helper, separate translation unit"
// reason as GetOverlayDllDir()/GetSelfDir(). Used by the button below so a
// manual click while a client is already running (or still starting up
// from an earlier automatic/manual launch) doesn't spawn a redundant
// second instance and its own extra console window — see dllmain.cpp's
// own comment for the full "why a mutex check, not just IPC state"
// reasoning.
bool IsClientMutexHeld() {
    HANDLE h = OpenMutexW(SYNCHRONIZE, FALSE, L"Global\\ShadowManAPClientSingleton");
    if (h) {
        CloseHandle(h);
        return true;
    }
    return false;
}

// Manual "Launch Client" button support (2026-08-05) — mirrors dllmain.cpp's
// TryAutoLaunchClient's CreateProcess call exactly. Duplicated here (a
// handful of lines) rather than shared through a header, following this
// file's own existing GetOverlayDllDir()/dllmain.cpp's GetSelfDir()
// precedent for the same reason. This is the same fundamentally low-risk
// "reach outside this process" as that auto-launch: CreateProcess starts a
// brand new, independent process, never touches the game's own memory or
// threads.
bool LaunchClientBat() {
    std::wstring dir = GetOverlayDllDir();
    if (dir.empty()) return false;

    std::wstring batPath = dir + L"\\launch_client.bat";
    if (GetFileAttributesW(batPath.c_str()) == INVALID_FILE_ATTRIBUTES) {
        return false; // nothing to launch
    }

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};

    std::wstring cmdLine = L"cmd.exe /c \"" + batPath + L"\"";
    std::vector<wchar_t> cmdLineBuf(cmdLine.begin(), cmdLine.end());
    cmdLineBuf.push_back(L'\0');

    if (CreateProcessW(nullptr, cmdLineBuf.data(), nullptr, nullptr, FALSE,
                        CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        return true;
    }
    return false;
}

// Looks for <dll_dir>\fonts\toast_font.ttf — drop a font there to reskin the
// overlay. Nightdive KEX titles often ship their own UI font as a loose
// .ttf/.otf somewhere in the game install; using the game's actual font
// (if you can find it) will match better than any lookalike. A free
// distressed/typewriter-style font (e.g. "Special Elite" or similar horror-
// themed font from Google Fonts) is a reasonable stand-in otherwise. Falls
// back to ImGui's built-in font, just rasterized bigger, if nothing's found.
ImFont* LoadThemedFont(ImGuiIO& io) {
    std::wstring dir = GetOverlayDllDir();
    if (!dir.empty()) {
        std::wstring fontPath = dir + L"\\fonts\\toast_font.ttf";
        DWORD attrs = GetFileAttributesW(fontPath.c_str());
        if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY)) {
            std::string utf8Path = WideToUtf8(fontPath);
            ImFont* font = io.Fonts->AddFontFromFileTTF(utf8Path.c_str(), kTitleFontSize);
            if (font) return font;
        }
    }

    ImFontConfig cfg;
    cfg.SizePixels = kTitleFontSize;
    return io.Fonts->AddFontDefault(&cfg);
}

float AlphaFor(const Toast& t) {
    auto ageMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - t.spawnedAt).count();
    if (ageMs < kFadeMs) {
        return static_cast<float>(ageMs) / kFadeMs;
    }
    if (ageMs > kToastLifetimeMs - kFadeMs) {
        float remaining = static_cast<float>(kToastLifetimeMs - ageMs);
        if (remaining < 0.0f) remaining = 0.0f;
        return remaining / kFadeMs;
    }
    return 1.0f;
}

// Swamp/voodoo palette instead of stock UI blue/green — sickly soul-green
// for received, torchlit amber for sent, blood red for disconnects, worn
// bone/parchment for everything else.
ImVec4 AccentColorFor(const std::string& kind) {
    if (kind == "item_received") return ImVec4(0.53f, 0.85f, 0.42f, 1.0f); // sickly green
    if (kind == "item_sent")     return ImVec4(0.90f, 0.62f, 0.24f, 1.0f); // torchlit amber
    if (kind == "connected")     return ImVec4(0.53f, 0.85f, 0.42f, 1.0f);
    if (kind == "disconnected")  return ImVec4(0.80f, 0.20f, 0.20f, 1.0f); // blood red
    return ImVec4(0.78f, 0.72f, 0.58f, 1.0f); // status / default — worn bone
}

// Builds the flat single-level JSON object client.py's own json.loads()
// expects (see _OverlayIPC.poll_incoming in client.py) and queues it.
// Deliberately hand-rolled rather than pulling in a general JSON writer —
// same "only the one flat shape we actually need" scope as json_mini.h's
// Parse().
void SendJsonToClient(std::initializer_list<std::pair<const char*, std::string>> fields) {
    std::string json = "{";
    bool first = true;
    for (auto& kv : fields) {
        if (!first) json += ",";
        first = false;
        json += "\"";
        json += kv.first;
        json += "\":\"";
        json += json_mini::Escape(kv.second);
        json += "\"";
    }
    json += "}";
    GetIpcServer().SendToClient(json);
}

// Minimized, non-interactive stand-in shown whenever the panel isn't
// expanded (2026-08-05) — mirrors the "F2: show history" hint below it
// exactly (same flags, same font scale, same dim "worn bone" color) so the
// panel is never fully invisible: the player always sees that F10 is an
// option, without it grabbing the cursor or stealing input until they
// actually press it. NoInputs, same as the history hint — purely
// decorative text, nothing to click here.
//
// Returns its measured window height (2026-08-05) so Render() can stack
// the toast/history column starting right below it — both this and the
// expanded RenderConnectPanel() live at a fixed top-right anchor, and
// without this the toast/history stack (which used to just start at a
// fixed top margin of its own) would draw underneath/through whichever of
// the two is currently showing instead of forming one single column.
float RenderConnectPanelHint() {
    // Anchored to the same remembered spot as the expanded panel
    // (2026-08-30, Jon's ask) -- this hint window is its own separate,
    // non-interactive ImGui window (never draggable itself), so without
    // this it always snapped back to the hardcoded top-right corner the
    // instant you toggled the panel closed, undoing wherever you'd just
    // dragged the expanded panel to. Falls back to the same top-right
    // default the expanded panel itself uses until something's actually
    // been dragged and saved.
    {
        const OverlayPrefs& prefs = GetPrefs();
        ImVec2 defaultPos(ImGui::GetIO().DisplaySize.x - kHudWidth - kHudMargin, kHudMargin);
        ImGui::SetNextWindowPos(prefs.hasPanelPos ? prefs.panelPos : defaultPos);
    }
    ImGui::SetNextWindowSize(ImVec2(kHudWidth, 0));
    ImGui::SetNextWindowBgAlpha(0.35f);

    ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration
        | ImGuiWindowFlags_NoInputs
        | ImGuiWindowFlags_NoSavedSettings
        | ImGuiWindowFlags_NoFocusOnAppearing
        | ImGuiWindowFlags_NoNav
        | ImGuiWindowFlags_AlwaysAutoResize;

    if (g_font) ImGui::PushFont(g_font);
    ImGui::Begin("##ap_connect_hint", nullptr, flags);
    ImGui::SetWindowFontScale(kSubtitleFontScale);
    ImVec4 hintColor(0.55f, 0.51f, 0.44f, 0.7f);
    ImGui::PushStyleColor(ImGuiCol_Text, hintColor);
    ImGui::TextUnformatted("F1: Archipelago Connect / Console");
    ImGui::PopStyleColor();
    ImGui::SetWindowFontScale(1.0f);
    // Measured before End() — GetWindowSize() after End() doesn't see this
    // window at all, same lesson the toast-stacking code above already
    // learned the hard way.
    float height = ImGui::GetWindowSize().y;
    ImGui::End();
    if (g_font) ImGui::PopFont();
    return height;
}

// The in-game front end for connecting and for running client.py's own
// /commands (e.g. /siminject, /secret, /status) without alt-tabbing to its
// console window. Every field here is just forwarded verbatim to
// client.py, which does the actual work through its existing, already-
// hardened connect()/command_processor() code paths — see this file's own
// header comment and CLAUDE.md's 2026-08-04 writeup for the full
// rationale.
// Compact, fixed top-right HUD panel (2026-08-05 redesign) — same corner,
// same width, and the same muted "worn bone" header tone as the toast/
// history log below it, rather than a full default-ImGui titlebar window
// floating wherever it last landed. Hint text inside each field replaces
// the old separate label lines so the whole thing reads more like a compact
// HUD element than a settings dialog. Only ever drawn while g_panelExpanded
// is true (F10 pressed) — see RenderConnectPanelHint() above for the
// minimized default state.
//
// Returns its measured window height (2026-08-05) — see
// RenderConnectPanelHint()'s own comment for why: the toast/history column
// below needs to know how tall whichever of the two is currently showing
// actually is, so the whole thing reads as one column (F10 panel, then F9
// history, then item toasts) instead of two things fighting over the same
// top-right corner.
float RenderConnectPanel() {
    static char serverBuf[128]   = "archipelago.gg:38281";
    static char nameBuf[64]      = "";
    static char passwordBuf[64]  = "";
    static char cmdBuf[256]      = "";
    // Seeded once from disk (2026-08-30, Jon's ask) -- remembers the last
    // server/name/password entered across game launches. Injection
    // happens fresh each game launch, so "once per DLL load" (this whole
    // function only ever runs while the panel is expanded) is the right
    // granularity -- no need to re-check every frame.
    static bool prefsSeeded = false;
    if (!prefsSeeded) {
        const OverlayPrefs& prefs = GetPrefs();
        SeedBuf(serverBuf, sizeof(serverBuf), prefs.server);
        SeedBuf(nameBuf, sizeof(nameBuf), prefs.name);
        SeedBuf(passwordBuf, sizeof(passwordBuf), prefs.password);
        prefsSeeded = true;
    }
    // Purely local UI feedback for the Command box specifically (2026-08-05)
    // -- fades out after a few seconds, just confirms a /command reached
    // client.py. Connect/Disconnect now has real persistent state instead
    // (g_apConnState below), driven by client.py's own connection
    // lifecycle rather than a one-shot "sent" message.
    static std::string lastCmdStatus;
    static std::chrono::steady_clock::time_point lastCmdAt;

    {
        OverlayPrefs& prefs = GetPrefs();
        ImVec2 defaultPos(ImGui::GetIO().DisplaySize.x - kHudWidth - kHudMargin, kHudMargin);
        // FirstUseEver -- only seeds position the very first time this
        // window is ever Begin()'d this run (or, via prefs, ever at all).
        // Every later frame leaves it wherever it currently is, i.e.
        // wherever the player last dragged it.
        ImGui::SetNextWindowPos(prefs.hasPanelPos ? prefs.panelPos : defaultPos, ImGuiCond_FirstUseEver);
    }
    ImGui::SetNextWindowSize(ImVec2(kHudWidth, 0));
    ImGui::SetNextWindowBgAlpha(0.90f);

    // NoMove removed (2026-08-30, Jon's ask) -- draggable now by clicking
    // any empty area of the panel body (no title bar, but ImGui still
    // lets you drag from empty background when NoMove isn't set -- see
    // io.ConfigWindowsMoveFromTitleBarOnly, left at its default false).
    // The resulting position is captured into g_panelRectPos right after
    // End() below (already done for the click-outside-to-minimize check)
    // and persisted to disk by the "Persist dragged positions" block near
    // the end of Render().
    ImGuiWindowFlags flags = ImGuiWindowFlags_NoTitleBar
        | ImGuiWindowFlags_NoSavedSettings
        | ImGuiWindowFlags_NoResize
        | ImGuiWindowFlags_AlwaysAutoResize;

    if (g_font) ImGui::PushFont(g_font);

    if (ImGui::Begin("##ap_connect_panel", nullptr, flags)) {
        ImGui::SetWindowFontScale(kSubtitleFontScale);

        ImVec4 headerColor(0.60f, 0.56f, 0.48f, 0.85f); // same muted tone as the "Recent" history header
        ImGui::PushStyleColor(ImGuiCol_Text, headerColor);
        ImGui::TextUnformatted("Archipelago  (F1 to close)");
        ImGui::PopStyleColor();

        // No client.py currently attached at all (2026-08-05) -- the fields/
        // buttons below would just queue messages into a void, so skip
        // straight to a clear "nothing's listening" state plus a one-click
        // way to start it, instead of a panel that looks functional but
        // silently does nothing.
        if (!GetIpcServer().IsClientConnected()) {
            static std::string launchMsg;
            static std::chrono::steady_clock::time_point launchMsgAt;

            ImVec4 warnColor(0.90f, 0.62f, 0.24f, 1.0f); // torchlit amber, matches item_sent toasts
            ImGui::PushStyleColor(ImGuiCol_Text, warnColor);
            ImGui::TextWrapped("No AP client detected.");
            ImGui::PopStyleColor();

            if (ImGui::Button("Launch Client", ImVec2(-1, 0))) {
                // Checked first (2026-08-05) so a click while a client is
                // already running or still starting up (mutex exists, but
                // it hasn't reached us over IPC yet -- see this file's own
                // IsClientMutexHeld() comment) doesn't spawn a redundant
                // second instance and its own extra console window.
                if (IsClientMutexHeld()) {
                    launchMsg = "A client already appears to be running or starting -- give it a moment to connect.";
                } else {
                    bool ok = LaunchClientBat();
                    launchMsg = ok
                        ? "Launching client.py -- give it a few seconds to connect."
                        : "Couldn't find launch_client.bat next to the DLL.";
                }
                launchMsgAt = std::chrono::steady_clock::now();
            }
            if (!launchMsg.empty()) {
                auto ageMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - launchMsgAt).count();
                if (ageMs < 6000) {
                    ImVec4 dimColor(0.78f, 0.72f, 0.58f, 1.0f); // worn parchment, matches toast subtitles
                    ImGui::PushStyleColor(ImGuiCol_Text, dimColor);
                    ImGui::TextWrapped("%s", launchMsg.c_str());
                    ImGui::PopStyleColor();
                }
            }

            ImGui::SetWindowFontScale(1.0f);
            float earlyHeight = ImGui::GetWindowSize().y;
            g_panelRectPos = ImGui::GetWindowPos();
            g_panelRectSize = ImVec2(kHudWidth, earlyHeight);
            ImGui::End();
            if (g_font) ImGui::PopFont();
            return earlyHeight;
        }

        // Persistent connection-state banner -- always visible, reflects
        // g_apConnState (updated from client.py's actual connection
        // lifecycle in Render()'s event loop above), not just whatever the
        // last click happened to do.
        {
            ImVec4 col;
            std::string label;
            switch (g_apConnState) {
                case ApConnState::Connected:
                    col = ImVec4(0.53f, 0.85f, 0.42f, 1.0f); // sickly green, matches item_received toasts
                    label = g_apConnDetail.empty() ? "Connected" : g_apConnDetail;
                    break;
                case ApConnState::Connecting:
                    col = ImVec4(0.90f, 0.62f, 0.24f, 1.0f); // torchlit amber, matches item_sent toasts
                    label = "Connecting...";
                    break;
                case ApConnState::Failed:
                    col = ImVec4(0.80f, 0.20f, 0.20f, 1.0f); // blood red, matches disconnect toasts
                    label = g_apConnDetail.empty() ? "Connection failed" : ("Failed: " + g_apConnDetail);
                    break;
                case ApConnState::Disconnected:
                default:
                    col = ImVec4(0.55f, 0.51f, 0.44f, 0.7f); // dim "worn bone" hint tone
                    label = "Not connected";
                    break;
            }
            ImGui::PushStyleColor(ImGuiCol_Text, col);
            ImGui::TextWrapped("%s", label.c_str());
            ImGui::PopStyleColor();
        }
        ImGui::Separator();

        bool connected = (g_apConnState == ApConnState::Connected);
        // Locked once actually connected -- editing the address/name mid-
        // session doesn't do anything until you disconnect and reconnect
        // anyway, so leaving them editable was misleading rather than
        // useful. Still editable in every other state (including a failed
        // attempt) so you can fix a typo and retry immediately.
        ImGuiInputTextFlags fieldFlags = connected ? ImGuiInputTextFlags_ReadOnly : 0;

        ImGui::SetNextItemWidth(-1);
        ImGui::InputTextWithHint("##ap_server", "Server address", serverBuf, sizeof(serverBuf), fieldFlags);
        ImGui::SetNextItemWidth(-1);
        ImGui::InputTextWithHint("##ap_name", "Name (blank = keep current)", nameBuf, sizeof(nameBuf), fieldFlags);
        ImGui::SetNextItemWidth(-1);
        ImGui::InputTextWithHint("##ap_password", "Password (if required)", passwordBuf, sizeof(passwordBuf),
                                  fieldFlags | ImGuiInputTextFlags_Password);

        if (ImGui::Button(connected ? "Disconnect" : "Connect", ImVec2(-1, 0))) {
            if (connected) {
                SendJsonToClient({{"type", "disconnect_request"}});
            } else if (serverBuf[0] != '\0') {
                SendJsonToClient({
                    {"type", "connect_request"},
                    {"server", serverBuf},
                    {"name", nameBuf},
                    {"password", passwordBuf},
                });
                // Optimistic -- overwritten the moment the real "connected"
                // or "connect_failed" event arrives from client.py. Clears
                // any stale failure reason from a previous attempt so the
                // banner doesn't show an old error while a new one is
                // actually in flight.
                g_apConnState = ApConnState::Connecting;
                g_apConnDetail.clear();

                // Remember these for next launch (2026-08-30, Jon's ask).
                OverlayPrefs& prefs = GetPrefs();
                prefs.server = serverBuf;
                prefs.name = nameBuf;
                prefs.password = passwordBuf;
                SavePrefsToDisk(prefs);
            }
        }

        ImGui::Separator();
        ImGui::SetNextItemWidth(-1);
        bool enterPressed = ImGui::InputTextWithHint(
            "##ap_cmd", "Command (e.g. /status)", cmdBuf, sizeof(cmdBuf), ImGuiInputTextFlags_EnterReturnsTrue);
        ImGui::SameLine();
        bool sendClicked = ImGui::Button("Send");

        if ((enterPressed || sendClicked) && cmdBuf[0] != '\0') {
            SendJsonToClient({
                {"type", "console_input"},
                {"text", cmdBuf},
            });
            lastCmdStatus = std::string("Sent: ") + cmdBuf;
            lastCmdAt = std::chrono::steady_clock::now();
            cmdBuf[0] = '\0';
        }

        // Fades out after a few seconds rather than sitting there stale
        // forever — it's just a "the command reached client.py"
        // confirmation, not a live status (the toast/history log above is
        // still the source of truth for whatever the command actually did).
        if (!lastCmdStatus.empty()) {
            auto ageMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - lastCmdAt).count();
            if (ageMs < 4000) {
                ImVec4 statusColor(0.78f, 0.72f, 0.58f, 1.0f); // worn parchment, matches toast subtitles
                ImGui::PushStyleColor(ImGuiCol_Text, statusColor);
                ImGui::TextWrapped("%s", lastCmdStatus.c_str());
                ImGui::PopStyleColor();
            }
        }

        ImGui::SetWindowFontScale(1.0f);
    }
    // Measured before End() — same reasoning as the early-return path above
    // and the toast-stacking code elsewhere in this file.
    float panelHeight = ImGui::GetWindowSize().y;
    g_panelRectPos = ImGui::GetWindowPos();
    g_panelRectSize = ImVec2(kHudWidth, panelHeight);
    ImGui::End();

    if (g_font) ImGui::PopFont();
    return panelHeight;
}

} // namespace

namespace Overlay {

bool Init(ID3D11Device* device, ID3D11DeviceContext* context, HWND hwnd) {
    if (g_initialized) return true;

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr; // never touch disk
    io.ConfigFlags &= ~ImGuiConfigFlags_NavEnableKeyboard;

    ImGui::StyleColorsDark();

    // Must add fonts before the backends' first NewFrame() builds the atlas.
    g_font = LoadThemedFont(io);

    // Toasts themselves stay display-only (ImGuiWindowFlags_NoInputs, see
    // the toast/history windows below) — they never needed a WndProc hook.
    // The connect/console panel added 2026-08-04 does, though, so it can
    // actually receive typed text; see WndProcHook's own comment above for
    // why this is safe to add without touching how toasts already work.
    if (!ImGui_ImplWin32_Init(hwnd)) return false;
    if (!ImGui_ImplDX11_Init(device, context)) return false;

    g_originalWndProc = reinterpret_cast<WNDPROC>(
        SetWindowLongPtrW(hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(WndProcHook)));

    g_hwnd = hwnd;
    g_initialized = true;
    return true;
}

void Render() {
    if (!g_initialized) return;

    // Poll-based hotkeys: GetAsyncKeyState reads real physical key state
    // without hooking WndProc, so it never intercepts or blocks the key
    // from also reaching the game — same "don't steal input" rule the rest
    // of this overlay follows. ShowCursor's internal display counter just
    // needs each grab/release pair balanced, which the g_cursorGrabbed
    // union below already guarantees. NavEnableKeyboard is restored to
    // "off" the instant the cursor is released, matching the existing
    // toast code's principle of never leaving anything changed for the
    // game once our own UI isn't actively in use — though it only affects
    // ImGui's own internal widget-navigation state, never anything the
    // game itself reads.
    //
    // 2026-08-05: reverted back to the original modal design — F1 toggles
    // g_panelExpanded, which gates which of RenderConnectPanel/
    // RenderConnectPanelHint gets drawn each frame. See g_panelExpanded's
    // own header comment for why the selective WantCaptureMouse/Keyboard
    // forwarding attempt was abandoned. Cursor-grab/input-forwarding
    // itself is now gated on g_cursorGrabbed (2026-08-30) rather than
    // g_panelExpanded directly -- see its own comment above.
    {
        static bool prevDown = false;
        bool down = (GetAsyncKeyState(kPanelToggleKey) & 0x8000) != 0;
        if (down && !prevDown) {
            if (g_panelExpanded) {
                CollapsePanel();
            } else {
                g_panelExpanded = true;
            }
        }
        prevDown = down;
    }

    // Same poll-based-hotkey pattern as kPanelToggleKey (F1) above.
    // g_historyGrabWanted mirrors g_historyVisible's new state on every
    // press (2026-08-30, Jon's ask: F2 should also pop the cursor out, not
    // just F1, so the history log can be repositioned without opening the
    // connect panel too) rather than being its own separate toggle -- that
    // way two consecutive presses can't desync into "log hidden but cursor
    // still grabbed" or vice versa: grabbed exactly when F2 has most
    // recently shown the full log. Only an actual press ever sets this, so
    // a fresh launch still starts with the cursor locked for normal play
    // even though g_historyVisible itself defaults to true.
    {
        static bool prevDown = false;
        bool down = (GetAsyncKeyState(kHistoryToggleKey) & 0x8000) != 0;
        if (down && !prevDown) {
            g_historyVisible = !g_historyVisible;
            g_historyGrabWanted = g_historyVisible;
        }
        prevDown = down;
    }

    // g_cursorGrabbed is the union of both keys' wants, recomputed every
    // frame (2026-08-30, Jon's ask) -- capturing/releasing the actual OS
    // cursor-clip state happens exactly once, on the frame this union
    // value changes, not per-key: pressing one key while the other already
    // holds the grab open is a no-op here, and releasing only happens once
    // BOTH have let go (see ReleaseCursorGrab()).
    g_cursorGrabbed = g_panelExpanded || g_historyGrabWanted;
    {
        static bool prevGrabbed = false;
        if (g_cursorGrabbed && !prevGrabbed) {
            ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
            // Release whatever cursor-clip region the game has active
            // (camera-look games commonly pin the OS cursor to the window
            // or its center every frame via ClipCursor) so the mouse can
            // actually reach the panel/history log. Remember it so it can
            // be put back exactly on release -- GetClipCursor always
            // succeeds and returns the full virtual screen rect if no clip
            // is currently set, so this is safe even if the game never
            // called ClipCursor at all.
            g_hadSavedClip = GetClipCursor(&g_savedClipRect) != 0;
            ClipCursor(nullptr);
        } else if (!g_cursorGrabbed && prevGrabbed) {
            ReleaseCursorGrab();
        }
        prevGrabbed = g_cursorGrabbed;
    }

    if (g_cursorGrabbed) {
        // Fight ClipCursor every frame too, not just once on the transition
        // above (2026-08-30) -- same "loop every frame, don't trust a
        // one-shot win" fix as the ShowCursor loop below. A one-shot
        // ClipCursor(nullptr) only wins until the next frame the game
        // itself calls ClipCursor() again for its own camera-look -- which
        // re-traps the OS cursor to whatever rect it uses (often
        // centered/tiny), same root cause as the ShowCursor "blinking"
        // problem this loop already exists to fix. Live testing showed
        // this specifically once actually connected and playing rather
        // than sitting at a menu/loading screen -- that's when the game's
        // own per-frame camera-look clipping is actually active. Re-
        // releasing it here every frame wins the race the same way.
        ClipCursor(nullptr);

        // Force the cursor visible EVERY frame the cursor is grabbed, not
        // just once on the transition -- a one-shot ShowCursor(TRUE) loses
        // a race against a game that calls ShowCursor(FALSE) on its own
        // every frame (very common for games that hide the OS cursor
        // during normal play), which is what live testing showed as the
        // cursor "blinking in and out." Looping until the counter is
        // non-negative pins it visible for at least this frame regardless
        // of how far negative the game drove it since the last check.
        int count;
        do {
            count = ShowCursor(TRUE);
            ++g_cursorShowCount;
        } while (count < 0);
    }

    for (auto& ev : GetIpcServer().DrainEvents()) {
        Toast t;
        t.kind = ev.kind;
        t.title = ev.title;
        t.subtitle = ev.subtitle;
        t.spawnedAt = std::chrono::steady_clock::now();
        g_toasts.push_back(t);   // big fading popup
        g_history.push_back(std::move(t)); // permanent-ish scroll-back log
        while (g_history.size() > kHistoryMaxEntries) g_history.pop_front();

        // Persistent connect-panel state, alongside (not instead of) the
        // toast/history entry above.
        if (ev.kind == "connected") {
            g_apConnState = ApConnState::Connected;
            g_apConnDetail = ev.title;
        } else if (ev.kind == "connect_failed") {
            g_apConnState = ApConnState::Failed;
            g_apConnDetail = ev.title;
        } else if (ev.kind == "disconnected") {
            // A failed connect attempt also unconditionally triggers
            // CommonContext's own connection_closed() right after
            // handle_connection_loss() (client.py's server_loop calls both
            // for the same failed attempt, one from the except block, one
            // from finally) — don't let this plain "disconnected" arriving
            // a moment later silently blank out the red failure reason
            // that was just shown for the exact same attempt.
            if (g_apConnState != ApConnState::Failed) {
                g_apConnState = ApConnState::Disconnected;
                g_apConnDetail.clear();
            }
        }
    }

    while (!g_toasts.empty()) {
        auto ageMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - g_toasts.front().spawnedAt).count();
        if (ageMs > kToastLifetimeMs) {
            g_toasts.pop_front();
        } else {
            break;
        }
    }

    ImGui_ImplDX11_NewFrame();
    ImGui_ImplWin32_NewFrame();

    if (g_cursorGrabbed) {
        // Polling fallback for clicks (2026-08-05): live testing showed
        // typing works but clicking the Connect button doesn't. The Win32
        // backend's WM_LBUTTONDOWN/UP handling is purely message-based
        // with no polling fallback of its own -- if this game registers
        // raw mouse input with RIDEV_NOLEGACY for camera-look (common in
        // FPS-style engines), Windows stops generating those legacy
        // button messages for the window ENTIRELY, for every handler,
        // hooked or not -- WndProcHook can't bring back a message the OS
        // never sends. GetCursorPos/GetAsyncKeyState read real physical
        // OS state that's unaffected by that raw-input mode, so polling
        // them directly here works regardless of whether the legacy
        // messages are actually arriving. Safe to always do both: if the
        // messages ARE arriving too, this just reports the same state a
        // second way (AddMouseButtonEvent/AddMousePosEvent only update
        // current state, they don't double-fire a click), so there's no
        // harm in leaving this on unconditionally rather than trying to
        // detect which case applies.
        ImGuiIO& pollIo = ImGui::GetIO();
        POINT pt;
        if (GetCursorPos(&pt) && ScreenToClient(g_hwnd, &pt)) {
            pollIo.AddMousePosEvent(static_cast<float>(pt.x), static_cast<float>(pt.y));
        }
        static bool prevLButton = false;
        bool lButton = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
        if (lButton != prevLButton) {
            pollIo.AddMouseButtonEvent(ImGuiMouseButton_Left, lButton);
            prevLButton = lButton;

            // Click-outside-to-minimize (2026-08-05, Jon's ask): a fresh
            // press anywhere outside the panel's own rect collapses it,
            // same cleanup as pressing F1 again -- lets a stray click
            // during normal play (aiming, firing, etc.) dismiss the panel
            // instead of it sitting there stuck open. Rect is one frame
            // stale -- see g_panelRectPos/g_panelRectSize's own comment
            // for why that's fine for a static HUD element like this.
            //
            // Also exempts the draggable history log's rect (2026-08-30,
            // Jon's ask) -- it can now sit anywhere on screen, not just
            // directly under the panel, so a click meant to start
            // dragging it must not be treated as "outside" and collapse
            // the panel out from under the drag.
            if (lButton) {
                bool insidePanel =
                    pt.x >= g_panelRectPos.x && pt.x <= g_panelRectPos.x + g_panelRectSize.x &&
                    pt.y >= g_panelRectPos.y && pt.y <= g_panelRectPos.y + g_panelRectSize.y;
                bool insideHistory = g_historyRectValid &&
                    pt.x >= g_historyRectPos.x && pt.x <= g_historyRectPos.x + g_historyRectSize.x &&
                    pt.y >= g_historyRectPos.y && pt.y <= g_historyRectPos.y + g_historyRectSize.y;
                if (!insidePanel && !insideHistory) {
                    DismissHud();
                }
            }
        }
    }

    ImGui::NewFrame();

    ImGuiIO& io = ImGui::GetIO();
    const float margin = kHudMargin;
    const float toastWidth = kHudWidth; // wider, to fit the bigger font

    // Connect/console panel (or its minimized hint) anchors the top of the
    // whole HUD column now (2026-08-05) — rendered first so its real
    // measured height can offset everything drawn below it. Forms one
    // single stacked column: F10 panel, then F9 history, then live item
    // toasts — instead of the toast/history stack and the panel both
    // separately anchoring to the same fixed top-right corner and
    // overlapping whenever the panel is expanded.
    float panelHeight = g_panelExpanded ? RenderConnectPanel() : RenderConnectPanelHint();

    // Toasts stack below wherever the history log/hint last ended up, not
    // a fixed corner (2026-08-31, Jon's ask) -- see g_historyBottomAnchor's
    // own comment above. Falls back to the original below-the-panel column
    // whenever history hasn't drawn at least once yet.
    float toastX = io.DisplaySize.x - toastWidth - margin;
    float y;
    if (g_historyBottomValid) {
        toastX = g_historyBottomAnchor.x;
        y = g_historyBottomAnchor.y + 10.0f; // same 10px gap the toast stack itself uses between entries
    } else {
        y = margin + panelHeight + 10.0f; // same 10px gap the toast stack itself uses between entries
    }

    if (g_font) ImGui::PushFont(g_font);

    size_t shown = 0;
    for (auto it = g_toasts.rbegin(); it != g_toasts.rend() && shown < kMaxVisible; ++it, ++shown) {
        const Toast& t = *it;
        float alpha = AlphaFor(t);
        if (alpha <= 0.01f) continue;

        ImGui::SetNextWindowPos(ImVec2(toastX, y));
        ImGui::SetNextWindowSize(ImVec2(toastWidth, 0));
        ImGui::SetNextWindowBgAlpha(0.85f * alpha);

        ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration
            | ImGuiWindowFlags_NoInputs
            | ImGuiWindowFlags_NoSavedSettings
            | ImGuiWindowFlags_NoFocusOnAppearing
            | ImGuiWindowFlags_NoNav
            | ImGuiWindowFlags_AlwaysAutoResize;

        std::string windowId = "##toast" + std::to_string(
            reinterpret_cast<uintptr_t>(&t));
        ImGui::Begin(windowId.c_str(), nullptr, flags);

        // A little breathing room around the text — the bigger font reads
        // cramped without it.
        ImGui::Dummy(ImVec2(0.0f, 2.0f));

        ImVec4 accent = AccentColorFor(t.kind);
        accent.w = alpha;
        ImGui::PushStyleColor(ImGuiCol_Text, accent);
        ImGui::TextWrapped("%s", t.title.empty() ? "Archipelago" : t.title.c_str());
        ImGui::PopStyleColor();

        if (!t.subtitle.empty()) {
            ImVec4 subColor(0.82f, 0.78f, 0.70f, alpha); // warm parchment tint, not flat grey
            ImGui::PushStyleColor(ImGuiCol_Text, subColor);
            ImGui::SetWindowFontScale(kSubtitleFontScale);
            ImGui::TextWrapped("%s", t.subtitle.c_str());
            ImGui::SetWindowFontScale(1.0f);
            ImGui::PopStyleColor();
        }

        ImGui::Dummy(ImVec2(0.0f, 2.0f));

        // Must read the window's height BEFORE End() — GetItemRectSize()
        // after End() doesn't see the window at all, it sees whatever the
        // last widget INSIDE it was (here, that trailing Dummy — a couple
        // pixels tall), which is what was making every toast after the
        // first stack at nearly the same Y and overlap.
        float windowHeight = ImGui::GetWindowSize().y;
        ImGui::End();

        y += windowHeight + 10.0f;
    }

    // ── Persistent history log — smaller, plain text, doesn't fade or
    // expire on its own (just caps at kHistoryMaxEntries). Sits right
    // below the live toasts so a burst of pickups doesn't just vanish once
    // the big popups clear. Toggle with F2 (kHistoryToggleKey).
    //
    // Reset every frame (2026-08-30) -- only re-validated below when the
    // full, draggable window actually draws this frame. Stops a stale
    // rect from a previous frame (e.g. right after toggling to the hint
    // variant) being treated as still current by the click-outside check
    // and the position-persist block further down.
    g_historyRectValid = false;
    g_historyBottomValid = false;
    if (!g_history.empty()) {
        ImGuiWindowFlags historyFlags = ImGuiWindowFlags_NoDecoration
            | ImGuiWindowFlags_NoInputs
            | ImGuiWindowFlags_NoSavedSettings
            | ImGuiWindowFlags_NoFocusOnAppearing
            | ImGuiWindowFlags_NoNav
            | ImGuiWindowFlags_AlwaysAutoResize;

        if (g_historyVisible) {
            OverlayPrefs& prefs = GetPrefs();
            ImVec2 defaultPos(io.DisplaySize.x - toastWidth - margin, y);
            ImGui::SetNextWindowPos(prefs.hasHistoryPos ? prefs.historyPos : defaultPos, ImGuiCond_FirstUseEver);
            ImGui::SetNextWindowSize(ImVec2(toastWidth, 0));
            ImGui::SetNextWindowBgAlpha(0.55f);

            // NoInputs removed for this window specifically (2026-08-30,
            // Jon's ask) -- draggable to reposition, same background-drag
            // mechanism as the connect panel above. Only actually
            // reachable while the connect panel is expanded (F1) -- that's
            // the only state where the OS cursor is unlocked/visible and
            // WndProcHook is forwarding input to ImGui at all (see
            // g_panelExpanded's own comment further up for why). The
            // minimized hint variant below (##ap_history_hint) stays
            // fixed/non-interactive, unchanged.
            ImGuiWindowFlags draggableFlags = historyFlags & ~ImGuiWindowFlags_NoInputs;
            ImGui::Begin("##ap_history", nullptr, draggableFlags);
            ImGui::SetWindowFontScale(kSubtitleFontScale);

            ImVec4 headerColor(0.60f, 0.56f, 0.48f, 0.85f);
            ImGui::PushStyleColor(ImGuiCol_Text, headerColor);
            ImGui::TextUnformatted("Recent  (F2 to hide)");
            ImGui::PopStyleColor();

            // Newest first.
            for (auto it = g_history.rbegin(); it != g_history.rend(); ++it) {
                ImVec4 accent = AccentColorFor(it->kind);
                accent.w = 0.9f;
                ImGui::PushStyleColor(ImGuiCol_Text, accent);
                if (!it->subtitle.empty()) {
                    ImGui::Text("%s  -  %s", it->title.c_str(), it->subtitle.c_str());
                } else {
                    ImGui::TextUnformatted(it->title.c_str());
                }
                ImGui::PopStyleColor();
            }

            ImGui::SetWindowFontScale(1.0f);
            g_historyRectPos = ImGui::GetWindowPos();
            g_historyRectSize = ImGui::GetWindowSize();
            g_historyRectValid = true;
            g_historyBottomAnchor = ImVec2(g_historyRectPos.x, g_historyRectPos.y + g_historyRectSize.y);
            g_historyBottomValid = true;
            ImGui::End();
        } else {
            // Minimal always-there reminder so the toggle doesn't get
            // forgotten once hidden — a single dim line, no per-item list.
            //
            // Anchored to the same remembered spot as the expanded history
            // log (2026-08-30, Jon's ask) -- same reasoning as
            // RenderConnectPanelHint() above: this hint is its own
            // separate, non-interactive window, so without this it always
            // snapped back to the default stacked position on toggle-off.
            // Falls back to that same default (still stacked under
            // whichever of the panel/hint is showing, via 'y') until
            // something's actually been dragged and saved.
            {
                OverlayPrefs& prefs = GetPrefs();
                ImVec2 defaultPos(io.DisplaySize.x - toastWidth - margin, y);
                ImGui::SetNextWindowPos(prefs.hasHistoryPos ? prefs.historyPos : defaultPos);
            }
            ImGui::SetNextWindowSize(ImVec2(toastWidth, 0));
            ImGui::SetNextWindowBgAlpha(0.35f);

            ImGui::Begin("##ap_history_hint", nullptr, historyFlags);
            ImGui::SetWindowFontScale(kSubtitleFontScale);
            ImVec4 hintColor(0.55f, 0.51f, 0.44f, 0.7f);
            ImGui::PushStyleColor(ImGuiCol_Text, hintColor);
            ImGui::TextUnformatted("F2: show history");
            ImGui::PopStyleColor();
            ImGui::SetWindowFontScale(1.0f);
            {
                ImVec2 hintPos = ImGui::GetWindowPos();
                ImVec2 hintSize = ImGui::GetWindowSize();
                g_historyBottomAnchor = ImVec2(hintPos.x, hintPos.y + hintSize.y);
                g_historyBottomValid = true;
            }
            ImGui::End();
        }
    }

    if (g_font) ImGui::PopFont();

    // Persist dragged positions (2026-08-30, Jon's ask) -- compares
    // against what's already saved and only writes when something
    // actually changed AND the left mouse button is currently up, so this
    // never fires mid-drag (which would mean a disk write on every one of
    // 60-ish frames/sec) and never fires at all for a player who never
    // touches these windows. Only checked while the cursor is grabbed --
    // that's the only state either window can have moved in this frame
    // (see g_cursorGrabbed's own comment for why the cursor can't reach
    // either window otherwise).
    if (g_cursorGrabbed) {
        static ImVec2 lastSavedPanelPos = GetPrefs().hasPanelPos ? GetPrefs().panelPos : ImVec2(-1.0f, -1.0f);
        static ImVec2 lastSavedHistoryPos = GetPrefs().hasHistoryPos ? GetPrefs().historyPos : ImVec2(-1.0f, -1.0f);
        bool mouseDown = (GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
        if (!mouseDown) {
            bool changed = false;
            OverlayPrefs& prefs = GetPrefs();
            if (g_panelRectPos.x != lastSavedPanelPos.x || g_panelRectPos.y != lastSavedPanelPos.y) {
                prefs.panelPos = g_panelRectPos;
                prefs.hasPanelPos = true;
                lastSavedPanelPos = g_panelRectPos;
                changed = true;
            }
            if (g_historyRectValid &&
                (g_historyRectPos.x != lastSavedHistoryPos.x || g_historyRectPos.y != lastSavedHistoryPos.y)) {
                prefs.historyPos = g_historyRectPos;
                prefs.hasHistoryPos = true;
                lastSavedHistoryPos = g_historyRectPos;
                changed = true;
            }
            if (changed) SavePrefsToDisk(prefs);
        }
    }

    ImGui::Render();
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
}

void OnPreResize() {
    if (!g_initialized) return;
    ImGui_ImplDX11_InvalidateDeviceObjects();
}

void Shutdown() {
    if (!g_initialized) return;

    // Must restore the real WndProc BEFORE this DLL can ever be unloaded —
    // otherwise the window would keep pointing at WndProcHook's address
    // inside our own module after FreeLibrary, and the next message the OS
    // delivers to it would jump into freed memory.
    if (g_hwnd && g_originalWndProc) {
        SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(g_originalWndProc));
        g_originalWndProc = nullptr;
    }

    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();
    g_initialized = false;
}

} // namespace Overlay
