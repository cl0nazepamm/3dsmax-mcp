#include "mcp_bridge/bridge_health.h"

#include <chrono>
#include <map>
#include <mutex>
#include <set>

namespace BridgeHealth {
namespace {

struct Request {
    std::string request_id;
    std::string cmd_type;
    std::chrono::steady_clock::time_point started{};
};

std::mutex g_mutex;
std::set<std::string> g_connected;
// Keyed by client id. A stack because a handler can dispatch nested requests
// (invoke_tool), and those may use a client id that never connected.
std::map<std::string, std::vector<Request>> g_requests;
unsigned long long g_total_connections = 0;

}  // namespace

void ClientConnected(const std::string& client_id) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_connected.insert(client_id);
    ++g_total_connections;
}

void ClientDisconnected(const std::string& client_id) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_connected.erase(client_id);
    g_requests.erase(client_id);
}

void RequestStarted(const std::string& client_id, const std::string& request_id,
                    const std::string& cmd_type) {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_requests[client_id].push_back({request_id, cmd_type, std::chrono::steady_clock::now()});
}

void RequestFinished(const std::string& client_id) {
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_requests.find(client_id);
    if (it == g_requests.end()) return;
    if (!it->second.empty()) it->second.pop_back();
    if (it->second.empty()) g_requests.erase(it);
}

ClientsSnapshot Snapshot() {
    const auto now = std::chrono::steady_clock::now();
    ClientsSnapshot snapshot;
    std::lock_guard<std::mutex> lock(g_mutex);
    snapshot.connected = static_cast<int>(g_connected.size());
    snapshot.total_connections = g_total_connections;
    for (const auto& [client_id, stack] : g_requests) {
        if (stack.empty()) continue;
        const Request& outer = stack.front();
        RequestInfo info;
        info.client_id = client_id;
        info.request_id = outer.request_id;
        info.cmd_type = outer.cmd_type;
        info.elapsed_ms = static_cast<long long>(
            std::chrono::duration_cast<std::chrono::milliseconds>(now - outer.started).count());
        info.nested = static_cast<int>(stack.size()) - 1;
        info.internal = g_connected.count(client_id) == 0;
        snapshot.inflight.push_back(std::move(info));
    }
    return snapshot;
}

}  // namespace BridgeHealth
