#include "mcp_bridge/pipe_server.h"
#include "mcp_bridge/bridge_health.h"
#include "mcp_bridge/core_api.h"
#include "mcp_bridge/core_host.h"

#include <cstring>

PipeServer::PipeServer(std::wstring pipe_name)
    : pipe_name_(std::move(pipe_name)) {}

PipeServer::~PipeServer() {
    Stop();
}

void PipeServer::Start() {
    if (running_.load()) return;
    running_ = true;
    accept_thread_ = std::thread(&PipeServer::AcceptLoop, this);
}

void PipeServer::Stop() {
    if (!running_.load()) return;
    running_ = false;

    // MCP clients keep their pipe open between calls, so their threads sit in
    // a read, and the accept thread waits for the next client. Cancelling
    // every instance's I/O ends those waits.
    {
        std::lock_guard<std::mutex> lock(instances_mutex_);
        for (HANDLE pipe : instances_) CancelIoEx(pipe, nullptr);
    }

    if (accept_thread_.joinable()) {
        accept_thread_.join();
    }

    // Join all client threads
    std::lock_guard<std::mutex> lock(threads_mutex_);
    for (auto& ct : client_threads_) {
        if (ct->thread.joinable()) {
            ct->thread.join();
        }
    }
    client_threads_.clear();
}

void PipeServer::Track(HANDLE pipe) {
    std::lock_guard<std::mutex> lock(instances_mutex_);
    instances_.insert(pipe);
}

void PipeServer::Untrack(HANDLE pipe) {
    std::lock_guard<std::mutex> lock(instances_mutex_);
    instances_.erase(pipe);
}

// Waits for a started overlapped operation on `pipe`. An operation started
// after Stop() swept the instances cancels itself here, so no wait outlives
// Stop(). GetOverlappedResult returns only once the kernel is done with `io`.
bool PipeServer::Await(HANDLE pipe, OVERLAPPED& io, DWORD& bytes) {
    if (!running_.load()) CancelIoEx(pipe, &io);
    return GetOverlappedResult(pipe, &io, &bytes, TRUE) != FALSE;
}

void PipeServer::CleanupFinishedThreads() {
    std::lock_guard<std::mutex> lock(threads_mutex_);
    auto it = client_threads_.begin();
    while (it != client_threads_.end()) {
        if ((*it)->done.load()) {
            if ((*it)->thread.joinable()) {
                (*it)->thread.join();
            }
            it = client_threads_.erase(it);
        } else {
            ++it;
        }
    }
}

void PipeServer::AcceptLoop() {
    OVERLAPPED io = {};
    io.hEvent = CreateEvent(nullptr, TRUE, FALSE, nullptr);

    while (running_.load()) {
        // Cleanup finished client threads periodically
        CleanupFinishedThreads();

        HANDLE pipe = CreateNamedPipeW(
            pipe_name_.c_str(),
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES,
            BUFFER_SIZE,
            BUFFER_SIZE,
            0,              // default timeout (use system default, fastest)
            nullptr         // default security (local machine only)
        );

        if (pipe == INVALID_HANDLE_VALUE) {
            Sleep(100);
            continue;
        }
        Track(pipe);

        DWORD unused = 0;
        BOOL connected = ConnectNamedPipe(pipe, &io);
        if (!connected) {
            const DWORD err = GetLastError();
            connected = err == ERROR_PIPE_CONNECTED ||  // the client was first
                        (err == ERROR_IO_PENDING && Await(pipe, io, unused));
        }
        if (!connected) {  // failed, or cancelled by Stop()
            Untrack(pipe);
            CloseHandle(pipe);
            continue;
        }

        // Spawn a thread for this client — accept loop immediately creates
        // the next pipe instance so another client can connect with zero delay
        auto ct = std::make_unique<ClientThread>();
        ClientThread* ctPtr = ct.get();
        ct->thread = std::thread([this, pipe, ctPtr]() {
            HandleClient(pipe);
            DisconnectNamedPipe(pipe);
            Untrack(pipe);
            CloseHandle(pipe);
            ctPtr->done.store(true);
        });

        std::lock_guard<std::mutex> lock(threads_mutex_);
        client_threads_.push_back(std::move(ct));
    }

    if (io.hEvent) CloseHandle(io.hEvent);
}

void PipeServer::HandleClient(HANDLE pipe) {
    static std::atomic<unsigned long long> next_client_id{1};
    const std::string client_id =
        "pipe-" + std::to_string(next_client_id.fetch_add(1));
    // health's client registry (bridge_health.h): connected clients and their requests.
    BridgeHealth::ClientConnected(client_id);

    // One operation at a time per client, so one OVERLAPPED serves them all.
    OVERLAPPED io = {};
    io.hEvent = CreateEvent(nullptr, TRUE, FALSE, nullptr);

    while (running_.load()) {
        std::string request = ReadRequest(pipe, io);
        // A request whose read completed as Stop() began is not dispatched.
        if (request.empty() || !running_.load()) break;

        std::string response;
        try {
            response = CoreHost::Dispatch(request, client_id);
        } catch (const std::exception& e) {
            response = McpInternalErrorResponse(e.what());
        }

        if (!WriteResponse(pipe, io, response)) {
            break;
        }
    }

    if (io.hEvent) CloseHandle(io.hEvent);
    CoreHost::ReleaseSession(client_id);
    BridgeHealth::ClientDisconnected(client_id);
}

std::string PipeServer::ReadRequest(HANDLE pipe, OVERLAPPED& io) {
    std::string data;
    char buf[4096];
    for (;;) {
        DWORD got = 0;
        if (!ReadFile(pipe, buf, sizeof(buf), nullptr, &io) && GetLastError() != ERROR_IO_PENDING)
            return {};
        // Cut short by a disconnect or by Stop(): a partial request is dropped.
        if (!Await(pipe, io, got) || got == 0) return {};
        data.append(buf, got);
        if (memchr(buf, '\n', got)) break;
    }

    while (!data.empty() && (data.back() == '\n' || data.back() == '\r')) {
        data.pop_back();
    }
    return data;
}

bool PipeServer::WriteResponse(HANDLE pipe, OVERLAPPED& io, const std::string& response) {
    const std::string out = response + "\n";
    size_t sent = 0;
    while (sent < out.size()) {
        DWORD wrote = 0;
        const DWORD chunk = static_cast<DWORD>(out.size() - sent);
        if (!WriteFile(pipe, out.data() + sent, chunk, nullptr, &io) && GetLastError() != ERROR_IO_PENDING)
            return false;
        if (!Await(pipe, io, wrote) || wrote == 0) return false;
        sent += wrote;
    }
    return true;
}
