#pragma once
#include <windows.h>
#include <string>
#include <thread>
#include <atomic>

class MCPBridgeGUP;

class PipeServer {
public:
    explicit PipeServer(MCPBridgeGUP* gup);
    ~PipeServer();

    void Start();
    void Stop();
    bool IsRunning() const { return running_.load(); }

private:
    void ServerLoop();
    void HandleClient(HANDLE pipe);
    std::string ReadRequest(HANDLE pipe);
    bool WriteResponse(HANDLE pipe, const std::string& response);

    MCPBridgeGUP* gup_;
    std::thread server_thread_;
    std::atomic<bool> running_{false};
    HANDLE shutdown_event_ = nullptr;

    static constexpr const wchar_t* PIPE_NAME = L"\\\\.\\pipe\\3dsmax-mcp";
    static constexpr DWORD BUFFER_SIZE = 64 * 1024;
};
