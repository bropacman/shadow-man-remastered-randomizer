# ShadowManOverlay — in-game AP popup toasts

An injected DLL that renders small "item received / sent" toast popups in
the corner of the screen, styled after how the OOT/MM and Dark Souls AP
clients do it. It does **not** connect to the Archipelago server itself —
`client.py` keeps doing that exactly as it does today. The DLL just draws
whatever `client.py` tells it to draw, over a local TCP socket
(127.0.0.1:31727). See the module docstring in `src/ipc_server.h` for why
it's one-directional.

## Status

Working end to end as of 2026-07-24 — injection, IPC, and live toasts on
real pickups all confirmed against the actual game. Renderer is D3D11
(confirmed via `tools/detect_renderer.py` against `thoth_x64_patched.exe`).

## Building

Prerequisites:
- Visual Studio 2022 (Desktop development with C++ workload) or the standalone Build Tools
- CMake 3.18+
- Git (CMake's `FetchContent` pulls MinHook and Dear ImGui source automatically — needs internet access at configure time)

```
cd overlay_dll
cmake -B build -A x64
cmake --build build --config Release
```

Output: `build/Release/ShadowManOverlay.dll` (64-bit — must match the
game's architecture; `thoth_x64_patched.exe` is x64).

## How it gets loaded

`client.py` injects it automatically once it attaches to
`thoth_x64_patched.exe` for memory polling — see `_inject_overlay_dll()` and
`OVERLAY_DLL_PATH` near the top of `client.py`. By default it looks for
`ShadowManOverlay.dll` next to `client.py` itself; copy the built DLL there
(or update `OVERLAY_DLL_PATH`).

If you'd rather test the DLL in isolation before wiring up client.py, any
generic injector (Process Hacker's "Inject DLL", Extreme Injector, etc.)
will work too — it doesn't know or care how it got loaded.

## Toast wire format

Newline-delimited JSON, one object per line, sent over the TCP socket:

```json
{"type": "item_received", "item": "Baton", "from": "Alice"}
{"type": "item_sent", "item": "Dark Soul", "to": "Bob"}
{"type": "status", "text": "Connected to Archipelago"}
{"type": "connected", "text": "Connected to Archipelago"}
{"type": "disconnected", "text": "Lost connection to Archipelago"}
```

`item_received` renders sickly green, `item_sent` torchlit amber,
`connected` green, `disconnected` blood red, everything else worn bone.
Toasts stack top-right, fade in over 400ms, hold ~7.5s, fade out over 400ms.

`item_sent` gets its real item name + recipient from the server's
`ItemSend` broadcast (`ShadowManContext.on_print_json` in `client.py`), not
from a guess made at check-time — every client already receives this
packet after each location check resolves, so no `LocationScouts` request
was needed after all. Both the sender and recipient are shown as
`PlayerName (GameName)`.

## History log

Below the live toasts is a small "Recent" panel (last 15 events, newest on
top) that doesn't fade or expire — so a burst of pickups isn't just gone
once the big popups clear. Press **F9** to hide/show it (polled via
`GetAsyncKeyState`, not a WndProc hook, so it never blocks the key from
also reaching the game). When hidden, a single dim "F9: show history" line
stays up so the toggle doesn't get forgotten.

## Font

Renders at 30px by default (ImGui's built-in font, just rasterized bigger —
looks a little blocky since it's a bitmap font not built for that size).

To reskin it: drop a `.ttf` at `fonts/toast_font.ttf`, next to
`ShadowManOverlay.dll` (i.e. next to `client.py`), and it's picked up
automatically on next injection — no rebuild needed. Two ways to get one:

- Check the game's own install folder for a loose `.ttf`/`.otf` — Nightdive
  KEX titles often ship their UI font as a real file rather than baking it
  into a resource archive. If you find the one used in the menus (the
  distressed stencil-typewriter style), that'll match perfectly.
- Otherwise grab a free horror/distressed typewriter font — "Special Elite"
  (Google Fonts, OFL-licensed) is a reasonable stand-in for that
  worn/creepy-journal look.

## Known limitations (v1)

- No "connect to AP server from in-game menu" UI — the ask was specifically
  for popups like MM/DS3 show, and building a full in-game connect flow is
  a separate, much bigger project (would need a text input widget, which
  means it *would* need real keyboard input routed to ImGui, which is a
  can of worms this version deliberately avoids). `client.py` remains the
  place you type your server address / slot name, same as today.
- One overlay connection at a time. If client.py restarts, it should just
  reconnect — the DLL's accept loop goes back to listening.
