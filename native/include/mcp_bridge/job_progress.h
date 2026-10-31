#pragma once
#include <nlohmann/json.hpp>
#include <string>

// Progress of a long native call, keyed by the progress_key its client sent.
// The call writes it on Max's main thread; native:job_progress reads it on a
// pipe worker while that thread is still busy. Core memory: a reload drops it.
namespace JobProgress {
void Set(const std::string& key, nlohmann::json state);
nlohmann::json Get(const std::string& key);  // null when the key is unknown
}  // namespace JobProgress
