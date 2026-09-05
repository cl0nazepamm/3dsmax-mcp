// Optional live SDK harness. Executes the same implementation without replacing
// or unloading the user's running GUP. Call only through Max's Python main thread.
#include "mcp_bridge/agent_viewport.h"
#include "mcp_bridge/scene_journal.h"
#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/modifier_helpers.h"
#include <exception>
#include <string>
#include <fstream>
#include <filesystem>
// This harness never initializes an executor window or registers its UI macros.
void ClaimNativeInstance() {}
void RunToolSmokeMacro() {}
extern "C" __declspec(dllexport) const char* MCPAgentViewportProbe(const char* input) {
    static std::string result;
    try {
        if (GetCurrentThreadId()!=GetWindowThreadProcessId(GetCOREInterface()->GetMAXHWnd(),nullptr))
            throw std::runtime_error("Max main thread required");
        auto params=nlohmann::json::parse(input);
        if(params.value("action","")=="restore_minimized_camera") {
            const auto saved=AgentViewport::SaveCamera();
            try {
                AgentViewport::Execute({{"action","pan"},{"x",17},{"y",-11}});
                AgentViewport::Execute({{"action","minimize"}});
                AgentViewport::RestoreCamera(saved);
                const auto parked=AgentViewport::Execute({{"action","status"}});
                bool captureRejected=false;
                try { AgentViewport::Get(); } catch(const std::exception&) { captureRejected=true; }
                AgentViewport::Execute({{"action","restore"}});
                const auto restored=AgentViewport::SaveCamera();
                float maxError=0;
                for(int row=0;row<4;++row) for(int column=0;column<3;++column)
                    maxError=std::max(maxError,std::abs(saved.tm.GetRow(row)[column]-restored.tm.GetRow(row)[column]));
                result=nlohmann::json({{"window_state",parked.at("window_state")},
                    {"capture_ready",parked.at("capture_ready")},{"capture_rejected",captureRejected},
                    {"camera_max_error",maxError},{"width_error",std::abs(saved.width-restored.width)}}).dump();
            } catch(...) {
                try { AgentViewport::RestoreCamera(saved); } catch(...) {}
                try { AgentViewport::Execute({{"action","restore"}}); } catch(...) {}
                throw;
            }
            return result.c_str();
        }
        if(params.value("action","")=="modifier_class") {
            using namespace HandlerHelpers;
            const std::string name=params.at("name").get<std::string>();
            const auto describe=[](ClassDesc* descriptor) -> nlohmann::json {
                if(!descriptor) return nullptr;
                return {{"class", ScriptClassName(descriptor)},
                    {"superclass", descriptor->SuperClassID()},
                    {"class_id", {descriptor->ClassID().PartA(),descriptor->ClassID().PartB()}}};
            };
            // Baseline lookup is metadata-only: NEVER instantiate its result.
            ClassDesc* old=FindClassDescByName(name,OSM_CLASS_ID);
            if(!old) old=FindClassDescByName(name,WSM_CLASS_ID);
            if(!old) old=FindClassDescByName(name);
            nlohmann::json details={{"name",name},{"old_lookup",describe(old)},
                {"new_lookup",describe(ModifierHelpers::FindModifierClass(name))}};
            if(params.value("create",false)) {
                try {
                    Modifier* modifier=ModifierHelpers::CreateModifier(name);
                    const auto cleanup=[](Modifier* value) { value->DeleteThis(); };
                    std::unique_ptr<Modifier,decltype(cleanup)> owned(modifier,cleanup);
                    if(params.contains("parameters")) {
                        for(auto& entry:params["parameters"].items()) {
                            const std::string value=entry.value().get<std::string>();
                            if(!ModifierHelpers::SetParameter(modifier,entry.key(),value,GetCOREInterface()->GetTime()))
                                throw std::runtime_error("Unsupported modifier parameter: "+entry.key());
                            const auto handle=Animatable::GetHandleByAnim(modifier);
                            details["readback"][entry.key()]=RunMAXScript(
                                "(getProperty (getAnimByHandle "+std::to_string(handle)+") (\""+
                                JsonEscape(entry.key())+"\" as name)) as string");
                        }
                    }
                    details["created_class"]=MaxScriptVisibleClassName(modifier);
                    details["created_superclass"]=modifier->SuperClassID();
                    details["properties"]=ModifierHelpers::InspectLegacyParameters(modifier);
                } catch(const std::exception& ex) { details["creation_error"]=ex.what(); }
            }
            result=details.dump();
            return result.c_str();
        }
        if(params.value("action","")=="open") SceneJournal::Register();
        params["source"]="agent";
        if(params.value("action","")=="capture_screen") result=NativeHandlers::CaptureScreen(params.dump(),nullptr);
        else if(params.value("action","")=="capture") result=NativeHandlers::CaptureViewportMainThread(params.dump());
        else if(params.value("action","")=="capture_multi") result=NativeHandlers::CaptureMultiViewMainThread(params.dump());
        else result=AgentViewport::Execute(params).dump();
        if(params.value("action","")=="release") SceneJournal::Unregister();
    } catch(const std::exception& ex) { result=nlohmann::json({{"error",ex.what()}}).dump(); }
    catch(...) { result="{\"error\":\"Native exception\"}"; }
    return result.c_str();
}

// Python.ExecuteFile can own an interpreter undo hold. Run one deferred probe
// after that request unwinds, with the production USER_BUSY guard still intact.
// No retries, no suspension/cancellation of any user hold, no GUP replacement.
namespace {
std::string deferredInput, deferredPath;
UINT_PTR deferredTimer=0;
void CALLBACK DeferredProbe(HWND,UINT,UINT_PTR timer,DWORD) {
    KillTimer(nullptr,timer); deferredTimer=0;
    const std::string result=MCPAgentViewportProbe(deferredInput.c_str());
    std::ofstream file(std::filesystem::path(HandlerHelpers::Utf8ToWide(deferredPath)),std::ios::binary);
    file<<result;
}
}
extern "C" __declspec(dllexport) bool MCPAgentViewportProbeDeferred(const char* input,const char* path) {
    if(GetCurrentThreadId()!=GetWindowThreadProcessId(GetCOREInterface()->GetMAXHWnd(),nullptr) || deferredTimer) return false;
    deferredInput=input; deferredPath=path;
    deferredTimer=SetTimer(nullptr,0,100,DeferredProbe);
    return deferredTimer!=0;
}
