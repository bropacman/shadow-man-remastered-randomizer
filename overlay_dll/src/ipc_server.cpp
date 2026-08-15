#include "ipc_server.h"
#include "json_mini.h"

#define _WINSOCK_DEPRECATED_NO_WARNINGS // inet_addr — fine for a loopback-only listener
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#pragma comment(lib, "ws2_32.lib")

static IpcServer g_ipcServer;
IpcServer& GetIpcServer() { return g_ipcServer; }

void IpcServer::Start(unsigned short port) {
    if (m_running.exchange(true)) return; // already running
    m_acceptThread = std::thread([this, port] { AcceptLoop(port); });
}

void IpcServer::Stop() {
    m_running = false;
    if (m_acceptThread.joinable()) {
        // AcceptLoop is blocked in accept()/recv() with no clean interrupt
        // mechanism here; detach rather than risk hanging DLL_PROCESS_DETACH.
        m_acceptThread.detach();
    }
}

void IpcServer::PushEvent(ToastEvent ev) {
    std::lock_guard<std::mutex> lock(m_queueMutex);
    m_queue.push_back(std::move(ev));
}

std::deque<ToastEvent> IpcServer::DrainEvents() {
    std::lock_guard<std::mutex> lock(m_queueMutex);
    std::deque<ToastEvent> out;
    out.swap(m_queue);
    return out;
}

void IpcServer::SendToClient(std::string jsonLine) {
    std::lock_guard<std::mutex> lock(m_outMutex);
    m_outQueue.push_back(std::move(jsonLine));
}

std::deque<std::string> IpcServer::DrainOutgoing() {
    std::lock_guard<std::mutex> lock(m_outMutex);
    std::deque<std::string> out;
    out.swap(m_outQueue);
    return out;
}

static ToastEvent EventFromFields(const std::unordered_map<std::string, std::string>& f) {
    ToastEvent ev;
    auto get = [&](const char* key) -> std::string {
        auto it = f.find(key);
        return it != f.end() ? it->second : std::string();
    };

    ev.kind = get("type");
    if (ev.kind == "item_received") {
        ev.title = get("item");
        std::string from = get("from");
        ev.subtitle = from.empty() ? std::string() : ("from " + from);
    } else if (ev.kind == "item_sent") {
        std::string item = get("item");
        std::string to = get("to");
        std::string loc = get("location");
        ev.title = item.empty() ? loc : item;
        ev.subtitle = to.empty() ? loc : ("to " + to);
    } else if (ev.kind == "status" || ev.kind == "connected" || ev.kind == "disconnected") {
        ev.title = get("text");
        ev.subtitle.clear();
    } else {
        ev.title = get("text");
        ev.subtitle.clear();
    }
    return ev;
}

void IpcServer::ClientLoop(uintptr_t clientSocketRaw) {
    SOCKET clientSocket = static_cast<SOCKET>(clientSocketRaw);
    std::string buffer;
    char recvBuf[4096];

    // Bidirectional as of 2026-08-04: was a plain blocking recv() loop
    // (fine when this only ever read from client.py). Now polls with a
    // short select() timeout instead, so a queued outbound line (from the
    // connect/console panel's SendToClient()) gets flushed promptly even
    // when client.py isn't currently sending us anything — a real gap a
    // blocking recv() would otherwise leave stuck until the next toast
    // event happened to arrive from the other direction.
    while (m_running) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(clientSocket, &readSet);
        timeval tv{0, 100000}; // 100ms — responsive enough for a button
                                // click to feel immediate, cheap enough to
                                // poll indefinitely for the life of the
                                // connection.

        int sel = select(0, &readSet, nullptr, nullptr, &tv);
        if (sel == SOCKET_ERROR) break; // real socket error — go back to accept()

        if (sel > 0) {
            int n = recv(clientSocket, recvBuf, sizeof(recvBuf), 0);
            if (n <= 0) break; // client.py disconnected or error — go back to accept()

            buffer.append(recvBuf, n);

            size_t pos;
            while ((pos = buffer.find('\n')) != std::string::npos) {
                std::string line = buffer.substr(0, pos);
                buffer.erase(0, pos + 1);
                if (line.empty()) continue;

                std::unordered_map<std::string, std::string> fields;
                if (json_mini::Parse(line, fields)) {
                    PushEvent(EventFromFields(fields));
                }
            }
        }

        // Flush anything the connect/console panel queued for client.py
        // since the last iteration. Same socket client.py already holds
        // open in the other direction — see SendToClient()'s own comment.
        std::deque<std::string> outgoing = DrainOutgoing();
        if (!outgoing.empty()) {
            for (auto& line : outgoing) {
                std::string withNewline = line + "\n";
                int sent = send(clientSocket, withNewline.c_str(),
                                 static_cast<int>(withNewline.size()), 0);
                if (sent == SOCKET_ERROR) {
                    closesocket(clientSocket);
                    return; // treat a failed send exactly like a failed recv
                }
            }
        }
    }
    closesocket(clientSocket);
}

void IpcServer::AcceptLoop(unsigned short port) {
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        m_running = false;
        return;
    }

    SOCKET listenSocket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listenSocket == INVALID_SOCKET) {
        WSACleanup();
        m_running = false;
        return;
    }

    // Loopback only — this is a local trust boundary between the game
    // process and client.py, never meant to be reachable off-box.
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    addr.sin_port = htons(port);

    int reuse = 1;
    setsockopt(listenSocket, SOL_SOCKET, SO_REUSEADDR, (const char*)&reuse, sizeof(reuse));

    if (bind(listenSocket, (sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR) {
        closesocket(listenSocket);
        WSACleanup();
        m_running = false;
        return;
    }

    if (listen(listenSocket, 1) == SOCKET_ERROR) {
        closesocket(listenSocket);
        WSACleanup();
        m_running = false;
        return;
    }

    // Accept with a timeout so we can notice m_running flipping to false.
    while (m_running) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(listenSocket, &readSet);
        timeval tv{1, 0}; // 1 second

        int sel = select(0, &readSet, nullptr, nullptr, &tv);
        if (sel <= 0) continue; // timeout or error, loop and recheck m_running

        SOCKET clientSocket = accept(listenSocket, nullptr, nullptr);
        if (clientSocket == INVALID_SOCKET) continue;

        m_everConnected = true;
        m_clientConnected = true;

        // client.py only ever holds one connection open at a time; handle
        // it inline (blocks this thread until it disconnects) rather than
        // spawning per-connection threads we'd have to track and join.
        ClientLoop(static_cast<uintptr_t>(clientSocket));

        // ClientLoop only returns once the connection has actually ended
        // (client.py disconnected, or a send/recv error) -- reflect that
        // immediately rather than waiting for a new one to be accepted.
        m_clientConnected = false;
    }

    closesocket(listenSocket);
    WSACleanup();
}
