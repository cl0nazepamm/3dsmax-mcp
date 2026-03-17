#pragma once
#include <windows.h>
#include <string>
#include <thread>
#include <atomic>
#include <vector>
#include <mutex>

class MCPBridgeGUP;

class PipeServer {
public:
    explicit PipeServer(MCPBridgeGUP* gup);
    ~PipeServer();

    void Start();
    void Stop();
    bool IsRunning() const { return running_.load(); }

private:
    void AcceptLoop();
    void HandleClient(HANDLE pipe);
    std::string ReadRequest(HANDLE pipe);
    bool WriteResponse(HANDLE pipe, const std::string& response);
    void CleanupFinishedThreads();

    MCPBridgeGUP* gup_;
    std::thread accept_thread_;
    std::atomic<bool> running_{false};
    HANDLE shutdown_event_ = nullptr;

    // Track client threads for cleanup
    std::mutex threads_mutex_;
    struct ClientThread {
        std::thread thread;
        std::atomic<bool> done{false};
    };
    std::vector<std::unique_ptr<ClientThread>> client_threads_;

    static constexpr const wchar_t* PIPE_NAME = L"\\\\.\\pipe\\3dsmax-mcp";
    static constexpr DWORD BUFFER_SIZE = 64 * 1024;
};
