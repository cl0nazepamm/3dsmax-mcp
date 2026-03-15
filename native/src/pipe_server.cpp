#include "mcp_bridge/pipe_server.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/command_dispatcher.h"

PipeServer::PipeServer(MCPBridgeGUP* gup) : gup_(gup) {
    shutdown_event_ = CreateEvent(nullptr, TRUE, FALSE, nullptr);
}

PipeServer::~PipeServer() {
    Stop();
    if (shutdown_event_) {
        CloseHandle(shutdown_event_);
        shutdown_event_ = nullptr;
    }
}

void PipeServer::Start() {
    if (running_.load()) return;
    running_ = true;
    ResetEvent(shutdown_event_);
    server_thread_ = std::thread(&PipeServer::ServerLoop, this);
}

void PipeServer::Stop() {
    if (!running_.load()) return;
    running_ = false;
    SetEvent(shutdown_event_);  // wake the server thread
    if (server_thread_.joinable()) {
        server_thread_.join();
    }
}

void PipeServer::ServerLoop() {
    while (running_.load()) {
        // Create a new pipe instance for each connection
        HANDLE pipe = CreateNamedPipeW(
            PIPE_NAME,
            PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,              // single instance
            BUFFER_SIZE,
            BUFFER_SIZE,
            5000,           // default timeout
            nullptr         // default security (local machine only)
        );

        if (pipe == INVALID_HANDLE_VALUE) {
            Sleep(100);
            continue;
        }

        // Wait for client with overlapped I/O so we can also detect shutdown
        OVERLAPPED overlapped = {};
        overlapped.hEvent = CreateEvent(nullptr, TRUE, FALSE, nullptr);

        BOOL connected = ConnectNamedPipe(pipe, &overlapped);
        if (!connected) {
            DWORD err = GetLastError();
            if (err == ERROR_IO_PENDING) {
                // Wait for either: client connects OR shutdown signaled
                HANDLE events[2] = { overlapped.hEvent, shutdown_event_ };
                DWORD wait = WaitForMultipleObjects(2, events, FALSE, INFINITE);

                if (wait == WAIT_OBJECT_0 + 1) {
                    // Shutdown
                    CancelIoEx(pipe, &overlapped);
                    CloseHandle(overlapped.hEvent);
                    CloseHandle(pipe);
                    break;
                }
                // wait == WAIT_OBJECT_0 => client connected, continue
            } else if (err != ERROR_PIPE_CONNECTED) {
                // Unexpected error
                CloseHandle(overlapped.hEvent);
                CloseHandle(pipe);
                continue;
            }
            // ERROR_PIPE_CONNECTED means client connected between Create and Connect — fine
        }

        CloseHandle(overlapped.hEvent);

        // Handle the client
        HandleClient(pipe);

        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }
}

void PipeServer::HandleClient(HANDLE pipe) {
    std::string request = ReadRequest(pipe);
    if (request.empty()) return;

    std::string response;
    try {
        response = CommandDispatcher::Dispatch(request, gup_);
    } catch (const std::exception& e) {
        // Build a minimal error response
        response = "{\"success\":false,\"error\":\"Internal bridge error: ";
        // Escape the error message for JSON
        std::string msg = e.what();
        for (auto& c : msg) {
            if (c == '"') response += "\\\"";
            else if (c == '\\') response += "\\\\";
            else if (c == '\n') response += "\\n";
            else response += c;
        }
        response += "\",\"meta\":{\"transport\":\"namedpipe\"}}";
    }

    WriteResponse(pipe, response);
}

std::string PipeServer::ReadRequest(HANDLE pipe) {
    std::string data;
    char buf[4096];
    DWORD bytes_read = 0;

    while (true) {
        BOOL ok = ReadFile(pipe, buf, sizeof(buf), &bytes_read, nullptr);
        if (bytes_read > 0) {
            data.append(buf, bytes_read);
        }
        // Check if we have a complete request (newline-terminated)
        if (data.find('\n') != std::string::npos) {
            break;
        }
        if (!ok) {
            DWORD err = GetLastError();
            if (err == ERROR_MORE_DATA) {
                continue;  // more data available
            }
            break;  // pipe closed or error
        }
        if (bytes_read == 0) {
            break;
        }
    }

    // Trim trailing newline
    while (!data.empty() && (data.back() == '\n' || data.back() == '\r')) {
        data.pop_back();
    }
    return data;
}

bool PipeServer::WriteResponse(HANDLE pipe, const std::string& response) {
    std::string out = response + "\n";
    DWORD written = 0;
    DWORD total = static_cast<DWORD>(out.size());
    const char* ptr = out.c_str();

    while (total > 0) {
        BOOL ok = WriteFile(pipe, ptr, total, &written, nullptr);
        if (!ok) return false;
        ptr += written;
        total -= written;
    }
    FlushFileBuffers(pipe);
    return true;
}
