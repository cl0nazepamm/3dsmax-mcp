#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"

#include <notify.h>
#include <triobj.h>
#include <vector>

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── native:set_parent ───────────────────────────────────────
std::string NativeHandlers::SetParent(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto children = p.value("children", std::vector<std::string>{});
        std::string parentName = p.value("parent", "");

        if (children.empty()) throw std::runtime_error("children is required");

        Interface* ip = GetCOREInterface();

        // Find parent node (nullptr = unparent to scene root)
        INode* parentNode = nullptr;
        if (!parentName.empty()) {
            parentNode = FindUniqueNodeByName(parentName);
            if (!parentNode) throw std::runtime_error("Parent not found: " + parentName);
        }

        json done = json::array();
        json rejected = json::array();

        // Resolve every child before the first edit. A parent cannot become a
        // child of itself or of one of its own descendants.
        const NamedNodes resolved = ResolveNodesByNames(children);
        const json& notFound = resolved.notFound;
        std::vector<std::pair<std::string, INode*>> targets;
        for (INode* child : resolved.nodes) {
            const std::string childName = WideToUtf8(child->GetName());
            bool cycle = false;
            for (INode* cursor = parentNode; cursor && !cursor->IsRootNode(); cursor = cursor->GetParentNode()) {
                if (cursor == child) { cycle = true; break; }
            }
            if (cycle) {
                rejected.push_back({{"name", childName}, {"reason", "would create a hierarchy cycle"}});
                continue;
            }
            targets.push_back({childName, child});
        }
        if (targets.empty()) {
            throw std::runtime_error(StructuredErrorPayload(
                resolved.Unresolved() ? UnresolvedNamesCode(resolved) : "HIERARCHY_CYCLE",
                "No requested child could be parented; the hierarchy was not changed.",
                UnresolvedNamesHint(resolved, {{"rejected", rejected}})));
        }

        INode* newParent = parentNode ? parentNode : ip->GetRootNode();
        for (const auto& [childName, child] : targets) {
            newParent->AttachChild(child, TRUE);
            if (child->GetParentNode() == newParent) done.push_back(childName);
            else rejected.push_back({{"name", childName}, {"reason", "Max kept the previous parent"}});
        }
        if (done.empty())
            throw std::runtime_error("Max did not reparent any of the requested children.");

        ip->RedrawViews(ip->GetTime());

        std::string msg;
        if (parentNode) {
            msg = "Parented " + std::to_string(done.size()) + " objects under " + WideToUtf8(parentNode->GetName());
        } else {
            msg = "Unparented " + std::to_string(done.size()) + " objects";
        }
        json result;
        result["parent"] = parentNode ? json(WideToUtf8(parentNode->GetName())) : json(nullptr);
        result["done"] = done;
        result["notFound"] = notFound;
        json warnings = json::array();
        if (!notFound.empty()) {
            msg += " | " + std::to_string(notFound.size()) + " not found";
            warnings.push_back(std::to_string(notFound.size()) +
                " requested child name(s) matched no object: " + JoinNames(notFound));
        }
        if (!rejected.empty()) {
            result["rejected"] = rejected;
            warnings.push_back(std::to_string(rejected.size()) +
                " child(ren) kept their previous parent; see rejected for the reason.");
        }
        result["message"] = msg;
        if (!warnings.empty()) {
            result["status"] = "partial";
            result["warnings"] = warnings;
        }
        AddAmbiguousNamesWarning(result, resolved.ambiguous);
        return result.dump();
    });
}

// ── native:batch_rename_objects ──────────────────────────────
std::string NativeHandlers::BatchRenameObjects(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        auto renames = p.value("renames", std::vector<json>{});

        if (renames.empty()) throw std::runtime_error("renames is required");

        json renamed = json::array();
        json notFound = json::array();
        json ambiguous = json::array();
        json invalid = json::array();
        json duplicates = json::array();

        // Entries apply in order, so a swap through a temporary name works:
        // each old name is looked up when its entry applies.
        std::vector<INode*> renamedNodes;
        for (size_t i = 0; i < renames.size(); ++i) {
            const json& r = renames[i];
            std::string oldName = r.is_object() ? r.value("old_name", "") : "";
            std::string newName = r.is_object() ? r.value("new_name", "") : "";
            if (oldName.empty() || newName.empty()) {
                invalid.push_back(i);
                continue;
            }

            const std::vector<INode*> matches = CollectNodesByName(oldName);
            if (matches.empty()) {
                notFound.push_back(oldName);
                continue;
            }
            if (matches.size() > 1) {
                ambiguous.push_back(AmbiguousNameJson(oldName, matches));
                continue;
            }
            INode* node = matches.front();

            std::wstring wNewName = Utf8ToWide(newName);
            node->SetName(wNewName.c_str());
            renamed.push_back(oldName + " -> " + WideToUtf8(node->GetName()));
            renamedNodes.push_back(node);
        }
        if (renamed.empty()) {
            json hint = {{"notFound", notFound}, {"invalidEntries", invalid}};
            if (!ambiguous.empty()) {
                hint["ambiguous"] = ambiguous;
                hint["message"] = kUniqueNamesAdvice;
            }
            throw std::runtime_error(StructuredErrorPayload(
                !notFound.empty() ? "NOT_FOUND" : !ambiguous.empty() ? "AMBIGUOUS" : "BAD_PARAM",
                "No rename could be applied; every entry was missing old_name/new_name, matched no object "
                "or named several objects.",
                hint));
        }
        // Max allows duplicate object names; tools that take names then
        // refuse them as ambiguous.
        for (INode* node : renamedNodes) {
            const std::string current = WideToUtf8(node->GetName());
            if (CollectNodesByName(current).size() > 1 &&
                std::find(duplicates.begin(), duplicates.end(), current) == duplicates.end())
                duplicates.push_back(current);
        }

        std::string msg = "Renamed: " + std::to_string(renamed.size());
        json result;
        result["renamed"] = renamed;
        result["notFound"] = notFound;
        json warnings = json::array();
        if (!notFound.empty()) {
            msg += " | " + std::to_string(notFound.size()) + " not found";
            warnings.push_back(std::to_string(notFound.size()) +
                " old name(s) matched no object: " + JoinNames(notFound));
        }
        if (!invalid.empty()) {
            result["invalidEntries"] = invalid;
            warnings.push_back(std::to_string(invalid.size()) +
                " entr(ies) lacked old_name or new_name and were skipped.");
        }
        if (!duplicates.empty()) {
            result["duplicateNames"] = duplicates;
            warnings.push_back("These names are now shared by more than one object: " + JoinNames(duplicates));
        }
        result["message"] = msg;
        if (!warnings.empty()) {
            result["status"] = "partial";
            result["warnings"] = warnings;
        }
        AddAmbiguousNamesWarning(result, ambiguous);
        return result.dump();
    });
}

// ── native:manage_scene ─────────────────────────────────────
std::string NativeHandlers::ManageScene(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);
        std::string action = p.value("action", "");
        std::transform(action.begin(), action.end(), action.begin(), ::tolower);

        Interface* ip = GetCOREInterface();

        if (action == "hold") {
            ip->FileHold();
            return "Hold saved successfully";
        }
        if (action == "fetch") {
            // Fetch replaces the scene with the Hold buffer after a
            // confirmation; keep it visible even if something left Max quiet.
            TempQuietMode prompts(FALSE);
            ip->FileFetch();
            NativeHandlers::ResetSceneDeltaSessions();
            return "Fetched (restored) held state";
        }
        if (action == "reset") {
            // noPrompt=FALSE: Max asks to confirm (and to save unsaved changes)
            // unless it is in quiet mode, which is forced off here, so unsaved
            // work is never discarded silently; the prompt reaches the agent
            // as BLOCKED_BY_DIALOG.
            TempQuietMode prompts(FALSE);
            ip->FileReset(FALSE);
            NativeHandlers::ResetSceneDeltaSessions();
            return "Scene reset to empty";
        }
        if (action == "save") {
            const MSTR currentPath = ip->GetCurFilePath();
            const MCHAR* filePath = currentPath.data();
            if (!filePath || !*filePath) {
                throw std::runtime_error(
                    "Scene was not saved: it has no file path yet. Use File > Save As first.");
            }
            if (!ip->SaveToFile(filePath))
                throw std::runtime_error("3ds Max failed to save " + WideToUtf8(filePath));
            return "Saved: " + WideToUtf8(filePath);
        }
        if (action == "info") {
            // File path
            std::string fp = WideToUtf8(ip->GetCurFilePath().data());
            if (fp.empty()) fp = "(unsaved)";

            // Object count + poly count
            INode* root = ip->GetRootNode();
            std::vector<INode*> allNodes;
            CollectNodes(root, allNodes);
            int objCount = (int)allNodes.size();

            TimeValue t = ip->GetTime();
            int polyCount = 0;
            for (INode* node : allNodes) {
                ObjectState os = node->EvalWorldState(t);
                if (!os.obj) continue;
                if (os.obj->CanConvertToType(triObjectClassID)) {
                    TriObject* tri = (TriObject*)os.obj->ConvertToType(t, triObjectClassID);
                    if (tri) {
                        polyCount += tri->GetMesh().getNumFaces();
                        if (tri != os.obj) tri->MaybeAutoDelete();
                    }
                }
            }

            json result;
            result["filePath"] = fp;
            result["objectCount"] = objCount;
            result["polyCount"] = polyCount;
            return result.dump();
        }

        throw std::runtime_error("Unknown action: " + action + ". Use hold, fetch, reset, save, or info.");
    });
}

// ── native:undo_last ────────────────────────────────────────────
// Max broadcasts NOTIFY_SCENE_UNDO with the entry's name for every undo it
// performs; "max undo" with an empty undo stack broadcasts nothing.
static void RecordUndo(void* param, NotifyInfo* info) {
    auto* undone = static_cast<std::vector<std::string>*>(param);
#if MAX_SDK_VERSION >= 2027
    const MCHAR* entry = info ? GetNotifyParam<NOTIFY_SCENE_UNDO>(info) : nullptr;
#else
    const MCHAR* entry = info ? static_cast<const MCHAR*>(info->callParam) : nullptr;
#endif
    undone->push_back(entry ? WideToUtf8(entry) : std::string());
}

std::string NativeHandlers::UndoLast(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([]() -> std::string {
        std::vector<std::string> undone;
        RegisterNotification(RecordUndo, &undone, NOTIFY_SCENE_UNDO);
        try {
            RunMAXScriptChecked("max undo");
        } catch (...) {
            UnRegisterNotification(RecordUndo, &undone, NOTIFY_SCENE_UNDO);
            throw;
        }
        UnRegisterNotification(RecordUndo, &undone, NOTIFY_SCENE_UNDO);

        json result;
        result["undone"] = !undone.empty();
        if (undone.empty()) {
            result["status"] = "nothing_to_undo";
            result["message"] = "Nothing was undone: the undo stack is empty";
            result["warnings"] = json::array({result["message"]});
        } else {
            result["entry"] = undone.back();
            result["message"] = undone.back().empty() ? "Undid the last scene operation"
                                                      : "Undid \"" + undone.back() + "\"";
        }
        return result.dump();
    });
}
