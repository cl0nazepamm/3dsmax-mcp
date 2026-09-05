#include "mcp_bridge/native_handlers.h"

#include "mcp_bridge/bridge_gup.h"

#include "mcp_bridge/native_tool_registry.h"

#include "mcp_bridge/handler_helpers.h"



#include <algorithm>

#include <cctype>

#include <chrono>

#include <string>



#if __has_include("generated/tool_smoke_cases.inc")

#include "generated/tool_smoke_cases.inc"

#else

struct SmokeCase {

    const char* tool;

    const char* inputJson;

    uint8_t tier;

    uint8_t flags;

};

static constexpr const char* kSmokeTargetToken = "MCP_SmokeTarget";

static constexpr const char* kSmokeSpawnToken = "MCP_SmokeSpawn";

static const SmokeCase kSmokeCases[] = {};

static const size_t kSmokeCaseCount = 0;

#endif



using json = nlohmann::json;



namespace {



constexpr uint8_t kFlagSkipDefault = 1;

constexpr uint8_t kFlagExpectError = 2;



static std::string LowerCopy(std::string value) {

    std::transform(value.begin(), value.end(), value.begin(),

        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });

    return value;

}



static bool LooksLikeToolError(const std::string& raw) {

    if (raw.empty()) return true;

    try {

        json parsed = json::parse(raw, nullptr, false);

        if (parsed.is_discarded()) {

            std::string lower = LowerCopy(raw);

            return lower.rfind("error", 0) == 0

                || lower.find("blocked by safe mode") != std::string::npos

                || lower.find(" not found:") != std::string::npos

                || lower.find("failed") != std::string::npos;

        }

        if (parsed.is_object() && parsed.contains("error")) {

            const json& errVal = parsed["error"];

            if (errVal.type() == json::value_t::string) {

                return !errVal.get<std::string>().empty();

            }

            return errVal.type() != json::value_t::null;

        }

        if (parsed.is_object() && parsed.contains("ok") && parsed["ok"].type() == json::value_t::boolean) {

            return !parsed["ok"].get<bool>();

        }

    } catch (...) {

        return true;

    }

    return false;

}



static int TierLimitFromName(const std::string& tier) {

    if (tier == "read") return 0;

    if (tier == "fixture") return 1;

    if (tier == "mutate" || tier == "native" || tier == "all") return 2;

    return 0;

}



static void ReplaceAll(std::string& value, const std::string& from, const std::string& to) {

    if (from.empty()) return;

    size_t pos = 0;

    while ((pos = value.find(from, pos)) != std::string::npos) {

        value.replace(pos, from.size(), to);

        pos += to.size();

    }

}



static std::string SubstituteSmokeTokens(const std::string& inputJson) {

    std::string out = inputJson;

    ReplaceAll(out, "${SMOKE_TARGET}", kSmokeTargetToken);

    ReplaceAll(out, "${SMOKE_SPAWN}", kSmokeSpawnToken);

    ReplaceAll(out, "MCP_SmokeTarget", kSmokeTargetToken);

    ReplaceAll(out, "MCP_SmokeSpawn", kSmokeSpawnToken);

    return out;

}



static void EnsureSmokeFixture(MCPBridgeGUP* gup) {

    json create = {

        {"type", "box"},

        {"name", kSmokeTargetToken},

        {"length", 10},

        {"width", 10},

        {"height", 10},

        {"pos", json::array({9999.0, 9999.0, 0.0})},

        {"pos_mode", "ground"},

    };

    try {

        NativeToolRegistry::ExecuteTool("create_object", create, gup);

        json assign = {

            {"names", json::array({kSmokeTargetToken})},

            {"material_class", "PhysicalMaterial"},

            {"material_name", "MCP_SmokeMtl"},

        };

        NativeToolRegistry::ExecuteTool("assign_material", assign, gup);

    } catch (...) {

        // Fixture may already exist from a prior partial run.

    }

}



static void CleanupSmokeFixture(MCPBridgeGUP* gup) {

    json cleanup = {

        {"names", json::array({

            kSmokeTargetToken,

            kSmokeSpawnToken,

            std::string(kSmokeTargetToken) + "001",

        })},

    };

    try {

        NativeToolRegistry::ExecuteTool("delete_objects", cleanup, gup);

    } catch (...) {

    }

}



static void SceneHold(MCPBridgeGUP* gup) {

    try {

        NativeToolRegistry::ExecuteTool("manage_scene", json{{"action", "hold"}}, gup);

    } catch (...) {

    }

}



static void SceneFetch(MCPBridgeGUP* gup) {

    try {

        NativeToolRegistry::ExecuteTool("manage_scene", json{{"action", "fetch"}}, gup);

    } catch (...) {

    }

}



static bool IsMetaSmokeTool(const char* toolName) {

    if (!toolName) return true;

    const std::string name = toolName;

    return name == "invoke_tool" || name == "run_tool_smoke";

}



static std::string RunToolSmokeImpl(const std::string& params, MCPBridgeGUP* gup) {

    static thread_local bool inSmokeRun = false;

    if (inSmokeRun) {

        throw std::runtime_error("run_tool_smoke cannot be invoked recursively");

    }

    inSmokeRun = true;

    struct SmokeGuard {

        ~SmokeGuard() { inSmokeRun = false; }

    } guard;



    json p = params.empty() ? json::object() : json::parse(params, nullptr, false);

    if (p.is_discarded()) p = json::object();



    const std::string tier = p.value("tier", "read");

    const bool includeSkipped = p.value("includeSkipped", false);

    const bool dryRun = p.value("dryRun", false);

    const int tierLimit = TierLimitFromName(tier);

    const bool needsFixture = tierLimit >= 1;

    const bool needsHold = tierLimit >= 2;



    json report;

    report["tier"] = tier;

    report["dryRun"] = dryRun;

    report["total"] = 0;

    report["passed"] = 0;

    report["failed"] = 0;

    report["skipped"] = 0;

    report["results"] = json::array();



    if (kSmokeCaseCount == 0) {

        report["warning"] = "No smoke cases generated — run scripts/gen_tool_smoke.py and rebuild native plugin.";

        return report.dump();

    }



    bool held = false;

    if (needsHold && !dryRun) {

        SceneHold(gup);

        held = true;

    }

    if (needsFixture && !dryRun) {

        EnsureSmokeFixture(gup);

    }



    for (size_t i = 0; i < kSmokeCaseCount; ++i) {

        const SmokeCase& sc = kSmokeCases[i];

        json row;

        row["tool"] = sc.tool;

        row["tier"] = sc.tier;

        row["flags"] = sc.flags;



        if (IsMetaSmokeTool(sc.tool)) {

            row["status"] = "skipped";

            row["reason"] = "meta_tool";

            report["skipped"] = report["skipped"].get<int>() + 1;

            report["results"].push_back(row);

            continue;

        }



        if (static_cast<int>(sc.tier) > tierLimit) {

            row["status"] = "skipped";

            row["reason"] = "tier";

            report["skipped"] = report["skipped"].get<int>() + 1;

            report["results"].push_back(row);

            continue;

        }



        if ((sc.flags & kFlagSkipDefault) && !includeSkipped) {

            row["status"] = "skipped";

            row["reason"] = "skip_default";

            report["skipped"] = report["skipped"].get<int>() + 1;

            report["results"].push_back(row);

            continue;

        }



        report["total"] = report["total"].get<int>() + 1;



        std::string inputJson = SubstituteSmokeTokens(sc.inputJson ? sc.inputJson : "{}");

        json input = json::parse(inputJson, nullptr, false);

        if (input.is_discarded()) input = json::object();

        row["input"] = input;



        if (dryRun) {

            row["status"] = "dry_run";

            report["passed"] = report["passed"].get<int>() + 1;

            report["results"].push_back(row);

            continue;

        }



        auto started = std::chrono::steady_clock::now();

        std::string raw;

        std::string err;

        try {

            raw = NativeToolRegistry::ExecuteTool(sc.tool, input, gup);

        } catch (const std::exception& e) {

            err = e.what();

        } catch (...) {

            err = "Unknown exception during tool execution";

        }

        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(

            std::chrono::steady_clock::now() - started).count();

        row["elapsedMs"] = elapsed;



        const bool expectError = (sc.flags & kFlagExpectError) != 0;

        const bool gotError = !err.empty() || LooksLikeToolError(raw);

        const bool passed = expectError ? gotError : !gotError;



        if (!err.empty()) row["error"] = err;

        if (!raw.empty()) {

            json parsed = json::parse(raw, nullptr, false);

            row["result"] = parsed.is_discarded() ? json(raw) : parsed;

        }



        if (passed) {

            row["status"] = "passed";

            report["passed"] = report["passed"].get<int>() + 1;

        } else {

            row["status"] = "failed";

            report["failed"] = report["failed"].get<int>() + 1;

        }

        report["results"].push_back(row);

    }



    if (needsFixture && !dryRun) {

        CleanupSmokeFixture(gup);

    }

    if (held) {

        SceneFetch(gup);

    }



    return report.dump();

}



}  // namespace



std::string NativeHandlers::InvokeTool(const std::string& params, MCPBridgeGUP* gup) {

    return gup->GetExecutor().ExecuteSync([&params, gup]() -> std::string {

        json p = params.empty() ? json::object() : json::parse(params, nullptr, false);

        if (p.is_discarded()) {

            throw std::runtime_error("Invalid JSON params for invoke_tool");

        }



        std::string tool = p.value("tool", "");

        if (tool.empty()) {

            throw std::runtime_error("tool is required");

        }



        json input = p.value("input", json::object());

        if (input.is_null()) input = json::object();



        return NativeToolRegistry::ExecuteTool(tool, input, gup);

    });

}



std::string NativeHandlers::RunToolSmoke(const std::string& params, MCPBridgeGUP* gup) {

    return gup->GetExecutor().ExecuteSync([&params, gup]() -> std::string {

        return RunToolSmokeImpl(params, gup);

    });

}


