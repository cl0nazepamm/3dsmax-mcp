#pragma once

#include <cstddef>
#include <cstdint>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace SceneJournal {

void Register();
void Unregister();
void Reset();

bool IsRegistered();
bool FlushPending();
// Activity sequence includes interaction-only events such as selection changes.
unsigned long long CurrentSeq();
// Mutation sequence advances only when persistent scene state changes.
unsigned long long CurrentMutationSeq();
unsigned long long AppendSynthetic(const std::string& type, const json& details = json::object());
json ChangesSince(unsigned long long since, size_t limit = 256);
json MutationChangesSince(unsigned long long since, size_t limit = 256);

} // namespace SceneJournal
