// ipc_server.h — local TCP listener that receives toast events from
// client.py (the external, already-working AP client) and hands them to
// the overlay to render.
//
// Bidirectional as of 2026-08-04, for the in-game connect/console panel
// (overlay.cpp's RenderConnectPanel): the panel's Connect button and
// command box call SendToClient() to queue a line for client.py; the
// existing accept-loop's per-connection thread (ClientLoop) flushes that
// queue down the SAME socket client.py already holds open to send us
// toast events, alongside its existing recv side. One physical connection,
// used in both directions — client.py only ever holds one connection open
// at a time, same as before this change.
#pragma once
#include <string>
#include <deque>
#include <mutex>
#include <thread>
#include <atomic>

struct ToastEvent {
    std::string kind;      // "item_received" | "item_sent" | "status" | "connected" | "disconnected"
    std::string title;     // main line, e.g. "Baton"
    std::string subtitle;  // secondary line, e.g. "from Alice"
};

class IpcServer {
public:
    // Starts the accept-loop on a background thread. Safe to call once.
    void Start(unsigned short port);
    void Stop();

    // Pops every event queued since the last call. Call this once per
    // frame from the render thread.
    std::deque<ToastEvent> DrainEvents();

    // Queues a raw JSON line (no trailing newline — ClientLoop appends
    // one) to send down to client.py, e.g. {"type":"connect_request",...}.
    // Safe to call from the render thread. Best-effort: if client.py isn't
    // currently connected, the line just sits queued until it is (or
    // forever, if it never connects this session) — same "cheap no-op if
    // the other side isn't there" philosophy client.py's own _OverlayIPC
    // already uses for its send direction.
    void SendToClient(std::string jsonLine);

    // True once any client.py connection has ever been accepted this
    // session (2026-08-04) — dllmain.cpp's optional auto-launch
    // convenience checks this before spawning a second client.py.
    bool HasClientEverConnected() const { return m_everConnected; }

    // True only while a client.py connection is CURRENTLY open (2026-08-05)
    // — distinct from HasClientEverConnected(), which stays true forever
    // once set even if client.py later closes/crashes. The connect panel
    // uses this to show a clear "no client detected" state (with a manual
    // Launch Client button) whenever there's genuinely nothing listening
    // right now, not just "never connected since injection."
    bool IsClientConnected() const { return m_clientConnected; }

private:
    void AcceptLoop(unsigned short port);
    void ClientLoop(uintptr_t clientSocket);
    void PushEvent(ToastEvent ev);
    std::deque<std::string> DrainOutgoing();

    std::atomic<bool> m_running{false};
    std::thread m_acceptThread;
    std::mutex m_queueMutex;
    std::deque<ToastEvent> m_queue;

    std::mutex m_outMutex;
    std::deque<std::string> m_outQueue;

    std::atomic<bool> m_everConnected{false};
    std::atomic<bool> m_clientConnected{false};
};

// Process-wide singleton, simplest option for a single-purpose overlay DLL.
IpcServer& GetIpcServer();
