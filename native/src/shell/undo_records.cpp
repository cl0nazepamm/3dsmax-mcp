// Undo records and reference pins, moved verbatim from scene_patch_handlers.cpp
// (NodeFlags, ApplyFlagsSnapshot, NodeFlagsRestore) and plugin_access.cpp
// (Pin, EnvironmentRestore). See undo_records.h.
#include "mcp_bridge/undo_records.h"
#include "mcp_bridge/host_api.h"
#include <hold.h>

namespace UndoRecords {
namespace {

struct NodeFlags {
    bool hidden = false;
    bool frozen = false;
    bool renderable = true;
    bool castShadows = true;
    bool receiveShadows = true;
};

NodeFlags FromBits(unsigned bits) {
    NodeFlags flags;
    flags.hidden = (bits & kMcpNodeHidden) != 0;
    flags.frozen = (bits & kMcpNodeFrozen) != 0;
    flags.renderable = (bits & kMcpNodeRenderable) != 0;
    flags.castShadows = (bits & kMcpNodeCastShadows) != 0;
    flags.receiveShadows = (bits & kMcpNodeReceiveShadows) != 0;
    return flags;
}

void ApplyFlagsSnapshot(INode* node, const NodeFlags& flags) {
    if (!node) return;
    node->Hide(flags.hidden ? TRUE : FALSE);
    node->Freeze(flags.frozen ? TRUE : FALSE);
    node->SetRenderable(flags.renderable ? TRUE : FALSE);
    node->SetCastShadows(flags.castShadows ? TRUE : FALSE);
    node->SetRcvShadows(flags.receiveShadows ? TRUE : FALSE);
}

class NodeFlagsRestore final : public RestoreObj {
public:
    NodeFlagsRestore(INode* node, const NodeFlags& before, const NodeFlags& after)
        : node_(node), before_(before), after_(after) {}

    void Restore(int /*isUndo*/) override {
        ApplyFlagsSnapshot(node_, before_);
    }

    void Redo() override {
        ApplyFlagsSnapshot(node_, after_);
    }

    int Size() override {
        return static_cast<int>(sizeof(*this));
    }

private:
    INode* node_ = nullptr;
    NodeFlags before_;
    NodeFlags after_;
};

class Pin : public ReferenceMaker {
    RefTargetHandle value_=nullptr;
public:
    explicit Pin(RefTargetHandle value) { HoldSuspend suspend; ReplaceReference(0,value); }
    ~Pin() override { HoldSuspend suspend; DeleteAllRefsFromMe(); }
    int NumRefs() override { return 1; }
    RefTargetHandle GetReference(int) override { return value_; }
    void SetReference(int,RefTargetHandle value) override { value_=value; }
    RefResult NotifyRefChanged(const Interval&,RefTargetHandle,PartID&,RefMessage,BOOL) override { return REF_SUCCEED; }
};
class EnvironmentRestore final : public RestoreObj {
    Pin before_,after_; BOOL beforeOn_,afterOn_;
public:
    EnvironmentRestore(Texmap* before,BOOL beforeOn,Texmap* after,BOOL afterOn):before_(before),after_(after),beforeOn_(beforeOn),afterOn_(afterOn) {}
    void Restore(int) override { GetCOREInterface()->SetEnvironmentMap(static_cast<Texmap*>(before_.GetReference(0))); GetCOREInterface()->SetUseEnvironmentMap(beforeOn_); }
    void Redo() override { GetCOREInterface()->SetEnvironmentMap(static_cast<Texmap*>(after_.GetReference(0))); GetCOREInterface()->SetUseEnvironmentMap(afterOn_); }
    MSTR Description() override { return _M("MCP Environment Binding"); }
};

}  // namespace

void PutNodeFlagsRestore(INode* node, unsigned before, unsigned after) {
    theHold.Put(new NodeFlagsRestore(node, FromBits(before), FromBits(after)));
}

void PutEnvironmentRestore(Texmap* before, BOOL beforeOn, Texmap* after, BOOL afterOn) {
    theHold.Put(new EnvironmentRestore(before, beforeOn, after, afterOn));
}

ReferenceMaker* MakePin(ReferenceTarget* target) { return new Pin(target); }

void DestroyPin(ReferenceMaker* pin) { delete static_cast<Pin*>(pin); }

ReferenceTarget* PinTarget(ReferenceMaker* pin) { return static_cast<Pin*>(pin)->GetReference(0); }

bool IsPin(ReferenceMaker* maker) { return dynamic_cast<Pin*>(maker) != nullptr; }

}  // namespace UndoRecords
