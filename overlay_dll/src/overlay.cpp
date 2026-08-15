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
constexpr int kHistoryToggleKey = VK_F9;

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
bool g_panelExpanded = false; // F10 toggles this: minimized hint <-> full interactive panel
constexpr int kPanelToggleKey = VK_F10;
WNDPROC g_originalWndProc = nullptr;

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

// Shared close/collapse logic (2026-08-05) — both the F10 toggle-off path
// and the new click-outside-the-panel path need to do the exact same
// cleanup (stop swallowing input, restore whatever cursor clip the game
// had, unwind our own ShowCursor(TRUE) calls), so it lives here once
// rather than being duplicated at both call sites.
void CollapsePanel() {
    if (!g_panelExpanded) return; // already collapsed — nothing to undo
    g_panelExpanded = false;
    ImGui::GetIO().ConfigFlags &= ~ImGuiConfigFlags_NavEnableKeyboard;
    if (g_hadSavedClip) {
        ClipCursor(&g_savedClipRect);
        g_hadSavedClip = false;
    }
    // Unwind exactly as many ShowCursor(TRUE) calls as were made in the
    // per-frame block while expanded. ShowCursor's visibility counter is
    // one process-wide shared integer, so undoing our own net contribution
    // is correct regardless of how the game's own ShowCursor(FALSE) calls
    // were interleaved with ours while the panel was expanded.
    while (g_cursorShowCount > 0) {
        ShowCursor(FALSE);
        --g_cursorShowCount;
    }
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
    if (!g_panelExpanded) {
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
// expanded (2026-08-05) — mirrors the "F9: show history" hint below it
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
    ImGui::SetNextWindowPos(ImVec2(ImGui::GetIO().DisplaySize.x - kHudWidth - kHudMargin, kHudMargin));
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
    ImGui::TextUnformatted("F10: Archipelago Connect / Console");
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
    // Purely local UI feedback for the Command box specifically (2026-08-05)
    // -- fades out after a few seconds, just confirms a /command reached
    // client.py. Connect/Disconnect now has real persistent state instead
    // (g_apConnState below), driven by client.py's own connection
    // lifecycle rather than a one-shot "sent" message.
    static std::string lastCmdStatus;
    static std::chrono::steady_clock::time_point lastCmdAt;

    ImGui::SetNextWindowPos(ImVec2(ImGui::GetIO().DisplaySize.x - kHudWidth - kHudMargin, kHudMargin));
    ImGui::SetNextWindowSize(ImVec2(kHudWidth, 0));
    ImGui::SetNextWindowBgAlpha(0.90f);

    ImGuiWindowFlags flags = ImGuiWindowFlags_NoTitleBar
        | ImGuiWindowFlags_NoSavedSettings
        | ImGuiWindowFlags_NoMove
        | ImGuiWindowFlags_NoResize
        | ImGuiWindowFlags_AlwaysAutoResize;

    if (g_font) ImGui::PushFont(g_font);

    if (ImGui::Begin("##ap_connect_panel", nullptr, flags)) {
        ImGui::SetWindowFontScale(kSubtitleFontScale);

        ImVec4 headerColor(0.60f, 0.56f, 0.48f, 0.85f); // same muted tone as the "Recent" history header
        ImGui::PushStyleColor(ImGuiCol_Text, headerColor);
        ImGui::TextUnformatted("Archipelago  (F10 to close)");
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

    // Poll-based hotkey: GetAsyncKeyState reads real physical key state
    // without hooking WndProc, so it never intercepts or blocks the key
    // from also reaching the game — same "don't steal input" rule the rest
    // of this overlay follows.
    {
        static bool prevDown = false;
        bool down = (GetAsyncKeyState(kHistoryToggleKey) & 0x8000) != 0;
        if (down && !prevDown) g_historyVisible = !g_historyVisible;
        prevDown = down;
    }

    // Same poll-based-hotkey pattern as F9 above, so the toggle itself
    // always works via GetAsyncKeyState regardless of whether WndProcHook
    // is currently forwarding input elsewhere (it never touches this key's
    // physical state, only what WM_KEYDOWN/UP messages the game sees).
    // ShowCursor's internal display counter just needs each grab/release
    // pair balanced, which a simple bool flip already guarantees.
    // NavEnableKeyboard is restored to "off" the instant the cursor is
    // released, matching the existing toast code's principle of never
    // leaving anything changed for the game once our own UI isn't actively
    // in use — though it only affects ImGui's own internal widget-
    // navigation state, never anything the game itself reads.
    //
    // 2026-08-05: reverted back to the original modal design — this
    // toggles g_panelExpanded, which both gates WndProcHook's input
    // swallowing AND which of RenderConnectPanel/RenderConnectPanelHint
    // gets drawn each frame. See g_panelExpanded's own header comment for
    // why the selective WantCaptureMouse/Keyboard forwarding attempt was
    // abandoned.
    {
        static bool prevDown = false;
        bool down = (GetAsyncKeyState(kPanelToggleKey) & 0x8000) != 0;
        if (down && !prevDown) {
            if (g_panelExpanded) {
                CollapsePanel();
            } else {
                g_panelExpanded = true;
                ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
                // Release whatever cursor-clip region the game has active
                // (camera-look games commonly pin the OS cursor to the
                // window or its center every frame via ClipCursor) so the
                // mouse can actually reach the panel. Remember it so it can
                // be put back exactly on release -- GetClipCursor always
                // succeeds and returns the full virtual screen rect if no
                // clip is currently set, so this is safe even if the game
                // never called ClipCursor at all.
                g_hadSavedClip = GetClipCursor(&g_savedClipRect) != 0;
                ClipCursor(nullptr);
            }
        }
        prevDown = down;
    }

    if (g_panelExpanded) {
        // Force the cursor visible EVERY frame the panel is expanded, not
        // just once on toggle -- a one-shot ShowCursor(TRUE) loses a race
        // against a game that calls ShowCursor(FALSE) on its own every
        // frame (very common for games that hide the OS cursor during
        // normal play), which is what live testing showed as the cursor
        // "blinking in and out." Looping until the counter is non-negative
        // pins it visible for at least this frame regardless of how far
        // negative the game drove it since the last check.
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

    if (g_panelExpanded) {
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
            // same cleanup as pressing F10 again -- lets a stray click
            // during normal play (aiming, firing, etc.) dismiss the panel
            // instead of it sitting there stuck open. Rect is one frame
            // stale -- see g_panelRectPos/g_panelRectSize's own comment
            // for why that's fine for a static HUD element like this.
            if (lButton) {
                bool inside =
                    pt.x >= g_panelRectPos.x && pt.x <= g_panelRectPos.x + g_panelRectSize.x &&
                    pt.y >= g_panelRectPos.y && pt.y <= g_panelRectPos.y + g_panelRectSize.y;
                if (!inside) {
                    CollapsePanel();
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
    float y = margin + panelHeight + 10.0f; // same 10px gap the toast stack itself uses between entries

    if (g_font) ImGui::PushFont(g_font);

    size_t shown = 0;
    for (auto it = g_toasts.rbegin(); it != g_toasts.rend() && shown < kMaxVisible; ++it, ++shown) {
        const Toast& t = *it;
        float alpha = AlphaFor(t);
        if (alpha <= 0.01f) continue;

        ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - toastWidth - margin, y));
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
    // the big popups clear. Toggle with F9 (kHistoryToggleKey).
    if (!g_history.empty()) {
        ImGuiWindowFlags historyFlags = ImGuiWindowFlags_NoDecoration
            | ImGuiWindowFlags_NoInputs
            | ImGuiWindowFlags_NoSavedSettings
            | ImGuiWindowFlags_NoFocusOnAppearing
            | ImGuiWindowFlags_NoNav
            | ImGuiWindowFlags_AlwaysAutoResize;

        if (g_historyVisible) {
            ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - toastWidth - margin, y));
            ImGui::SetNextWindowSize(ImVec2(toastWidth, 0));
            ImGui::SetNextWindowBgAlpha(0.55f);

            ImGui::Begin("##ap_history", nullptr, historyFlags);
            ImGui::SetWindowFontScale(kSubtitleFontScale);

            ImVec4 headerColor(0.60f, 0.56f, 0.48f, 0.85f);
            ImGui::PushStyleColor(ImGuiCol_Text, headerColor);
            ImGui::TextUnformatted("Recent  (F9 to hide)");
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
            ImGui::End();
        } else {
            // Minimal always-there reminder so the toggle doesn't get
            // forgotten once hidden — a single dim line, no per-item list.
            ImGui::SetNextWindowPos(ImVec2(io.DisplaySize.x - toastWidth - margin, y));
            ImGui::SetNextWindowSize(ImVec2(toastWidth, 0));
            ImGui::SetNextWindowBgAlpha(0.35f);

            ImGui::Begin("##ap_history_hint", nullptr, historyFlags);
            ImGui::SetWindowFontScale(kSubtitleFontScale);
            ImVec4 hintColor(0.55f, 0.51f, 0.44f, 0.7f);
            ImGui::PushStyleColor(ImGuiCol_Text, hintColor);
            ImGui::TextUnformatted("F9: show history");
            ImGui::PopStyleColor();
            ImGui::SetWindowFontScale(1.0f);
            ImGui::End();
        }
    }

    if (g_font) ImGui::PopFont();

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
