#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/job_progress.h"

#include <chrono>
#include <map>
#include <mutex>
#include <stdexcept>

using json = nlohmann::json;

namespace {

// Small and bounded: an entry outlives its call so a status read that races
// the reply still sees the last state, and the oldest go first.
constexpr size_t kMaxEntries = 64;

struct Entry {
    json state;
    std::chrono::steady_clock::time_point updated;
};

std::mutex g_mutex;
std::map<std::string, Entry> g_entries;

}  // namespace

void JobProgress::Set(const std::string& key, json state) {
    if (key.empty()) return;
    std::lock_guard<std::mutex> lock(g_mutex);
    g_entries[key] = Entry{std::move(state), std::chrono::steady_clock::now()};
    while (g_entries.size() > kMaxEntries) {
        auto oldest = g_entries.begin();
        for (auto it = g_entries.begin(); it != g_entries.end(); ++it)
            if (it->second.updated < oldest->second.updated) oldest = it;
        g_entries.erase(oldest);
    }
}

json JobProgress::Get(const std::string& key) {
    std::lock_guard<std::mutex> lock(g_mutex);
    const auto found = g_entries.find(key);
    if (found == g_entries.end()) return nullptr;
    json state = found->second.state;
    state["age_ms"] = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - found->second.updated).count();
    return state;
}

// ── native:job_progress ─────────────────────────────────────
// A direct handler: it reads only this table, never the scene, so it answers
// while the call it reports on holds Max's main thread.
std::string NativeHandlers::GetJobProgress(const std::string& params, MCPBridgeGUP* /*gup*/) {
    const json p = params.empty() ? json::object() : json::parse(params, nullptr, false);
    if (p.is_discarded() || !p.is_object()) throw std::runtime_error("Invalid JSON payload");
    const json keys = p.value("keys", json::array());
    if (keys.type() != json::value_t::array || keys.empty() || keys.size() > 16)
        throw std::runtime_error("keys must list 1 to 16 progress keys");
    json progress = json::object();
    for (const auto& key : keys) {
        if (key.type() != json::value_t::string) throw std::runtime_error("Each progress key must be a string");
        progress[key.get<std::string>()] = JobProgress::Get(key.get<std::string>());
    }
    return json{{"progress", progress}}.dump();
}
