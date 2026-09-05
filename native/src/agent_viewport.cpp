#include "mcp_bridge/agent_viewport.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/scene_journal.h"
#include <IViewPanel.h>
#include <IViewPanelManager.h>
#include <Graphics/IViewportViewSetting.h>
#include <Graphics/ViewSystem/IActiveshadeFragment.h>
#if MAX_SDK_VERSION >= 2024
#include <Graphics/ViewSystem/IViewportFragmentManager.h>
#endif
#include <Rendering/IActiveShadeFragmentManager.h>
#include <Rendering/IInteractiveRenderer.h>
#include <hold.h>
#include <GraphicsWindow.h>
#include <IPerViewportFilter.h>
#include <mesh.h>
#include <notify.h>
#include <cmath>
#include <iomanip>
#include <sstream>

using json = nlohmann::json;
using namespace HandlerHelpers;
namespace AgentViewport {
namespace {
constexpr wchar_t kTitle[] = L"AGENT VIEWPORT";
constexpr wchar_t kOwner[] = L"3dsmax-mcp.AgentViewport";
constexpr float kPi = 3.14159265358979323846f;
int floatingID = 0;
int viewID = -1;
HWND panelWindow = nullptr;
Point3 orbitTarget(0,0,0);
unsigned long long generation = 0;
unsigned long long draws = 0;
bool registered = false;
bool ownsVFB = false;
bool vfbStarted = false;
int previousRenderView = -1;
BOOL previousUseActive = TRUE;
ULONG_PTR previewRenderer = 0;
struct PreviewProperty { ULONG_PTR handle; std::string property, before, applied; };
std::vector<PreviewProperty> previewProperties;
ULONG_PTR addedDenoiser = 0;
std::string previousElementsActive;
std::string previousDenoiserLayerActive;

std::string PropertyExpression(ULONG_PTR handle, const std::string& property) {
    return "(getAnimByHandle "+std::to_string(handle)+")."+property;
}

void LeaseProperty(ULONG_PTR handle, const std::string& property, const std::string& value) {
    const auto expression=PropertyExpression(handle,property);
    const auto before=RunMAXScript("("+expression+") as string");
    if(before==value) return;
    previewProperties.push_back({handle,property,before,value});
    RunMAXScript(expression+"="+value);
    if(RunMAXScript("("+expression+") as string")!=value)
        throw std::runtime_error("Preview setting did not apply: "+property);
}

void RestorePreviewSettings() {
    // Compare before restoring: retain edits made by the user during preview.
    for(auto i=previewProperties.rbegin();i!=previewProperties.rend();++i)
        RunMAXScript("(local r=getAnimByHandle "+std::to_string(i->handle)+"; if r!=undefined and "
            "isProperty r #"+i->property+" and (r."+i->property+" as string)==\""+i->applied+
            "\" do r."+i->property+"="+i->before+")");
    previewProperties.clear();
    if(addedDenoiser) {
        RunMAXScript("(local d=getAnimByHandle "+std::to_string(addedDenoiser)+
            "; if d!=undefined do (maxOps.GetCurRenderElementMgr()).RemoveRenderElement d)");
        addedDenoiser=0;
    }
    if(previousElementsActive=="false")
        RunMAXScript("(maxOps.GetCurRenderElementMgr()).SetElementsActive false");
    if(previousDenoiserLayerActive=="false")
        RunMAXScript("(vfbControl #getLayerMgr)[1].denoiserLayer.active=false");
    previousDenoiserLayerActive.clear();
    previousElementsActive.clear(); previewRenderer=0;
}

void PrepareVRayPreview() {
    auto* renderer=GetCOREInterface()->GetRenderer(RS_Production);
    if(!renderer) throw std::runtime_error("No production renderer");
    previewRenderer=Animatable::GetHandleByAnim(renderer);
    previousDenoiserLayerActive=RunMAXScript("((vfbControl #getLayerMgr)[1].denoiserLayer.active) as string");
    const auto handle=std::to_string(previewRenderer);
    if(RunMAXScript("(isProperty (getAnimByHandle "+handle+") #ipr_progressiveMode) as string")!="true")
        throw std::runtime_error("UNSUPPORTED: V-Ray preview requires the progressive IPR API");
    LeaseProperty(previewRenderer,"ipr_progressiveMode","true");
    LeaseProperty(previewRenderer,"system_effects_progressive_update","100");
    // One denoiser only. Preserve its engine/preset; a temporary default denoiser
    // works without requiring an NVIDIA GPU and is removed when preview stops.
    const auto found=RunMAXScript("(local m=maxOps.GetCurRenderElementMgr(); local ds=#(); "
        "for i=0 to (m.NumRenderElements()-1) do (local d=m.GetRenderElement i; "
        "if classof d==VRayDenoiser do append ds d); if ds.count>1 do throw \"Multiple VRayDenoisers\"; "
        "if ds.count==0 then \"0\" else ((getHandleByAnim ds[1]) as integer64) as string)");
    ULONG_PTR denoiser=std::stoull(found);
    if(!denoiser) {
        denoiser=std::stoull(RunMAXScript("(local d=VRayDenoiser(); "
            "if not ((maxOps.GetCurRenderElementMgr()).AddRenderElement d) do throw \"Cannot add preview denoiser\"; "
            "((getHandleByAnim d) as integer64) as string)"));
        addedDenoiser=denoiser;
    }
    LeaseProperty(denoiser,"enabled","true");
    LeaseProperty(denoiser,"vrayVFB","true");
    LeaseProperty(denoiser,"mode","2");
    previousElementsActive=RunMAXScript("((maxOps.GetCurRenderElementMgr()).GetElementsActive()) as string");
    RunMAXScript("(maxOps.GetCurRenderElementMgr()).SetElementsActive true");
    RunMAXScript("(vfbControl #getLayerMgr)[1].denoiserLayer.active=true");
}

bool VFBViewIsOurs() {
    auto* ip=GetCOREInterface16();
    auto* renderer=GetCOREInterface()->GetRenderer(RS_Production);
    return ownsVFB && renderer && Animatable::GetHandleByAnim(renderer)==previewRenderer &&
        !ip->GetRendUseActiveView() && ip->GetRendViewID(RS_Production)==viewID;
}

void RestoreRenderView() {
    auto* ip=GetCOREInterface16();
    if(!ip->GetRendUseActiveView() && ip->GetRendViewID(RS_Production)==viewID) {
        ip->SetRendViewID(RS_Production,previousRenderView);
        ip->SetRendUseActiveView(previousUseActive);
    }
    ownsVFB=false;
}
void SceneChanged(void*, NotifyInfo*) {
    ++generation; viewID=-1;
    // Handles and render-view IDs belong to the old scene. Never apply saved
    // settings through potentially reused handles after a load/reset.
    ownsVFB=false; vfbStarted=false; previewRenderer=0; addedDenoiser=0;
    previewProperties.clear(); previousElementsActive.clear(); previousDenoiserLayerActive.clear();
}
void RequireMainThread() {
    if(GetCurrentThreadId()!=GetWindowThreadProcessId(GetCOREInterface()->GetMAXHWnd(),nullptr))
        throw std::runtime_error("Agent viewport requires Max's main thread");
}

IViewPanel* Panel() {
    if (!floatingID) return nullptr;
    auto* manager = GetViewPanelManager();
    if (!manager) return nullptr;
    const int index = manager->GetViewPanelIndexFromFloatingID(floatingID);
    if (index < 0) return nullptr;
    auto* panel = manager->GetViewPanel(index);
    if (!panel || panel->GetHWnd() != panelWindow || !IsWindow(panelWindow) ||
        GetPropW(panelWindow, kOwner) != reinterpret_cast<HANDLE>(&floatingID)) return nullptr;
    return panel;
}

ViewExp* View() {
    auto* panel = Panel();
    if (!panel || panel->GetLayout() != VP_LAYOUT_1) return nullptr;
    auto& view = panel->GetViewExpByIndex(0);
    return view.IsAlive() && view.GetViewID() == viewID ? &view : nullptr;
}

HWND FloatingWindow() {
    if (!Panel()) return nullptr;
    HWND top=GetAncestor(panelWindow,GA_ROOT);
    return top && top!=GetCOREInterface()->GetMAXHWnd() ? top : nullptr;
}

// Showing a panel through Max's public API also activates it. Restore the
// user's panel, viewport and keyboard focus, including on initialization errors.
// SetActiveWindow is deliberately thread-local; never force Max to foreground.
class RestoreActivation {
    IViewPanel* previousPanel=GetViewPanelManager()->GetActiveViewPanel();
    HWND previousView=GetCOREInterface()->GetActiveViewExp().GetHWnd();
    HWND previousWindow=GetActiveWindow();
    HWND previousFocus=GetFocus();
public:
    ~RestoreActivation() {
        auto* manager=GetViewPanelManager();
        if(previousPanel) {
            int index=manager->GetViewPanelIndex(previousPanel);
            if(index>=0) manager->SetActiveViewPanel(index);
        }
        if(IsWindow(previousView)) GetCOREInterface()->SetActiveViewport(previousView);
        if(IsWindow(previousWindow) && !IsIconic(previousWindow) &&
           GetWindowThreadProcessId(previousWindow,nullptr)==GetCurrentThreadId())
            SetActiveWindow(previousWindow);
        if(IsWindow(previousFocus) && IsWindowVisible(previousFocus) &&
           !IsIconic(GetAncestor(previousFocus,GA_ROOT)) &&
           GetWindowThreadProcessId(previousFocus,nullptr)==GetCurrentThreadId())
            SetFocus(previousFocus);
    }
};

// V-Ray exposes viewport IPR through the same function used by its installed
// menu macros: engine 0=CPU/1=GPU, command 1=enabled/2=checked/3=toggle.
// Scope every viewport query and toggle to the owned panel. The global VFB stop
// is used only while our renderer identity and native render-view lock match.
class ActivateOwnedView {
    RestoreActivation restore;
public:
    ActivateOwnedView() {
        auto* panel=Panel();
        auto* view=View();
        if(!panel || !view) throw std::runtime_error("Agent viewport ownership lost");
        auto* manager=GetViewPanelManager();
        manager->SetActiveViewPanel(manager->GetViewPanelIndex(panel));
        auto* current=View();
        if(!current) throw std::runtime_error("Agent viewport ownership lost during activation");
        GetCOREInterface()->SetActiveViewport(current->GetHWnd());
        if(!View() || GetCOREInterface()->GetActiveViewExp().GetViewID()!=viewID)
            throw std::runtime_error("Could not target AGENT VIEWPORT");
    }
};

MaxSDK::Graphics::IActiveShadeFragment* ShadeFragment() {
    auto* view=View();
    if(!view) return nullptr;
#if MAX_SDK_VERSION >= 2024
    auto* manager=MaxSDK::Graphics::GetIViewportFragmentManager();
    return manager ? manager->GetActiveShadeFragment(*view) : nullptr;
#else
    ActivateOwnedView activation;
    return MaxSDK::Graphics::GetActiveShadeFragmentFromActiveViewport();
#endif
}

struct RenderState {
    bool activeShade=false, vrayAvailable=false;
    bool vfb=false;
    std::string rendererClass;
    int vrayGlobal=0, cpu=0, gpu=0, cpuEnabled=0, gpuEnabled=0;
    bool Rendered() const { return activeShade || cpu || gpu || vfb; }
    std::string Mode() const { return vfb ? "vray_vfb" : cpu || gpu ? "vray_ipr" : activeShade ? "activeshade" : "shaded"; }
};

RenderState ReadRenderState() {
    RenderState state;
    if(auto* renderer=GetCOREInterface()->GetRenderer(RS_Production))
        state.rendererClass=MaxScriptVisibleClassName(renderer);
    if(auto* fragment=ShadeFragment()) state.activeShade=fragment->IsEnabled();
    // Inspect installation before activating any viewport; no missing-function
    // dialogs and no script-defined global callbacks are installed.
    if(RunMAXScript("(globalVars.isGlobal #vrayViewportIPRControl and vrayViewportIPRControl != undefined) as string")!="true")
        return state;
    ActivateOwnedView activation;
    const std::string result=RunMAXScript(
        "(local s=stringStream \"\"; format \"% % % % %\" (vrayIsRenderingIPR()) "
        "(if vrayViewportIPRControl 0 2 then 1 else 0) (if vrayViewportIPRControl 1 2 then 1 else 0) "
        "(if (vrayViewportIPRControl 0 0 and vrayViewportIPRControl 0 1) then 1 else 0) "
        "(if (vrayViewportIPRControl 1 0 and vrayViewportIPRControl 1 1) then 1 else 0) to:s; s as string)");
    if(!View()) throw std::runtime_error("Agent viewport ownership lost while querying IPR");
    std::istringstream values(result);
    if(!(values>>state.vrayGlobal>>state.cpu>>state.gpu>>state.cpuEnabled>>state.gpuEnabled))
        throw std::runtime_error("Could not read V-Ray viewport IPR state: "+result);
    state.vrayAvailable=true;
    // vrayStartIPR posts its launch to Max's UI queue. Keep the pending lease
    // before vrayIsRenderingIPR becomes 1; do not tear down its settings early.
    state.vfb=VFBViewIsOurs() && (state.vrayGlobal==0 || state.vrayGlobal==1);
    if(state.vfb && state.vrayGlobal==1) vfbStarted=true;
    return state;
}

json RenderInfo(const RenderState& state) {
    return {{"mode",state.Mode()},{"activeshade_enabled",state.activeShade},
        {"vray_viewport_api",state.vrayAvailable},{"vray_ipr_location",state.vrayGlobal},
        // Both CPU and GPU menu checks can be true for one CPU IPR session.
        {"production_renderer",state.rendererClass},
        {"capture_target",state.vfb ? "vray_vfb" : "agent"},
        {"session_state",state.vfb ? (state.vrayGlobal==1 ? "running" : vfbStarted ? "stopped" : "starting") : state.Rendered() ? "running" : "stopped"},
        {"progressive_requested",previewRenderer!=0},{"denoiser_requested",previewRenderer!=0},
        {"vray_cpu_available",state.cpuEnabled!=0},{"vray_gpu_available",state.gpuEnabled!=0},
        {"targeting_supported",!state.Rendered()},
        {"image_current",state.Rendered() ? json(nullptr) : json(true)},
        {"converged",nullptr}};
}

void ToggleVRay(int engine, bool desired) {
    ActivateOwnedView activation;
    const std::string prefix="vrayViewportIPRControl "+std::to_string(engine);
    const std::string checked=RunMAXScript("("+prefix+" 2) as string");
    if(checked!="true" && checked!="false") throw std::runtime_error("Invalid V-Ray IPR check result");
    if((checked=="true")!=desired) {
        if(!View() || GetCOREInterface()->GetActiveViewExp().GetViewID()!=viewID)
            throw std::runtime_error("Agent viewport ownership lost before IPR toggle");
        RunMAXScript(prefix+" 3");
        if(!View() || RunMAXScript("("+prefix+" 2) as string")!=(desired ? "true" : "false"))
            throw std::runtime_error("V-Ray did not apply the requested viewport IPR state");
    }
}

void StopOwnedRender() {
    const auto state=ReadRenderState();
    if(ownsVFB) {
        if(state.vrayGlobal==0 && !vfbStarted)
            throw std::runtime_error("RENDER_STARTING: VFB launch has not been observed yet; read status again before stopping");
        if(state.vrayGlobal && !state.vfb)
            throw std::runtime_error("RENDER_OWNERSHIP_LOST: VFB renderer/view changed; stop rendering there before releasing preview settings");
        if(state.vfb) {
            RunMAXScript("vrayStopIPR()");
            if(RunMAXScript("(vrayIsRenderingIPR()) as string")!="0")
                throw std::runtime_error("V-Ray has not stopped; keep the VFB visible and retry status");
        }
        RestoreRenderView();
    }
    if(state.cpu) ToggleVRay(0,false);
    if(state.gpu) ToggleVRay(1,false);
    if(auto* fragment=ShadeFragment(); fragment && fragment->IsEnabled()) fragment->Enable(false);
    RestorePreviewSettings();
    ++generation;
}

void SetRenderMode(const json& p) {
    const std::string mode=p.value("mode","");
    const std::string rendererSource=p.value("renderer_source","activeshade");
    if(mode!="shaded" && mode!="activeshade" && mode!="vray_ipr" && mode!="vray_vfb")
        throw std::runtime_error("mode must be shaded, activeshade, vray_ipr, or vray_vfb");
    if(rendererSource!="activeshade" && rendererSource!="production")
        throw std::runtime_error("renderer_source must be activeshade or production");
    if(theHold.Holding()) throw std::runtime_error(StructuredErrorPayload("USER_BUSY",
        "Finish the current user operation before switching interactive rendering",{{"retryable",true}}));
    const auto state=ReadRenderState();
    if(mode==state.Mode() && !(mode=="shaded" && (ownsVFB || previewRenderer))) return;
    if(mode!="shaded") {
        if(state.vrayGlobal!=0 && !state.cpu && !state.gpu && !state.vfb)
            throw std::runtime_error("RENDER_BUSY: V-Ray IPR is running outside AGENT VIEWPORT; stop it there first");
        auto* manager=static_cast<MaxSDK::IActiveShadeFragmentManager*>(GetCOREInterface(IACTIVE_SHADE_VIEWPORT_MANAGER_INTERFACE));
        if(manager && manager->IsRunningInAtLeastOneViewport() && !state.activeShade)
            throw std::runtime_error("RENDER_BUSY: ActiveShade is running in another viewport");
        // The floating ActiveShade manager is separate from viewport fragments.
        if(!state.activeShade && RunMAXScript("(local b=GetActiveShadeBitmap wait:false; local present=b!=undefined; if b!=undefined do close b; present as string)")=="true")
            throw std::runtime_error("RENDER_BUSY: another ActiveShade session is running");
    }
    int engine=0;
    if(mode=="vray_ipr" || mode=="vray_vfb") {
        if(!state.vrayAvailable || (!state.cpuEnabled && !state.gpuEnabled))
            throw std::runtime_error("UNSUPPORTED: select V-Ray or V-Ray GPU as the production renderer first");
        engine=state.rendererClass.find("GPU")!=std::string::npos || state.rendererClass=="VRayRT" ? 1 : 0;
    }
    if(mode=="activeshade") {
        auto* renderer=GetCOREInterface()->GetRenderer(rendererSource=="production" ? RS_Production : RS_IReshade);
        if(!ShadeFragment() || !renderer || !renderer->GetIInteractiveRender() ||
           MaxScriptVisibleClassName(renderer)=="Default_Scanline_Renderer")
            throw std::runtime_error("UNSUPPORTED: the assigned renderer does not support viewport ActiveShade");
    }
    StopOwnedRender();
    try {
        if(mode=="vray_ipr" || mode=="vray_vfb") {
            PrepareVRayPreview();
            if(mode=="vray_ipr") ToggleVRay(engine,true);
            else {
                auto* ip=GetCOREInterface16();
                previousRenderView=ip->GetRendViewID(RS_Production);
                previousUseActive=ip->GetRendUseActiveView();
                ip->SetRendViewID(RS_Production,viewID);
                ip->SetRendUseActiveView(FALSE); ownsVFB=true; vfbStarted=false;
                ActivateOwnedView activation;
                RunMAXScript("vfbControl #show true; vrayStartIPR()");
            }
        }
        else if(mode=="activeshade") {
            auto* fragment=ShadeFragment();
            if(!fragment) throw std::runtime_error("ActiveShade fragment is unavailable");
            auto* renderer=GetCOREInterface()->GetRenderer(rendererSource=="production" ? RS_Production : RS_IReshade);
            fragment->SetActiveShadeRenderer(renderer);
            fragment->Enable(true);
        } else {
            if(auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(Get().GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID)))
                settings->SetViewportVisualStyle(MaxSDK::Graphics::VisualStyleShaded);
        }
        ++generation;
        Redraw();
        if(ReadRenderState().Mode()!=mode) throw std::runtime_error("Requested render mode was not applied");
    } catch(...) {
        // Return to a stopped preview after partial start; never globally abort.
        if(View()) try { StopOwnedRender(); } catch(...) {}
        throw;
    }
}

std::string WindowState() {
    auto* view=View();
    if(!view) return "unavailable";
    HWND top=FloatingWindow();
    if(top && IsIconic(top)) return "minimized";
    if(!Panel()->IsViewPanelVisible() || !IsWindowVisible(view->GetHWnd())) return "hidden";
    RECT bounds{};
    if(!GetWindowRect(view->GetHWnd(),&bounds) || !MonitorFromRect(&bounds,MONITOR_DEFAULTTONULL))
        return "offscreen";
    return "visible";
}

json Status() {
    const std::string state=WindowState();
    if(state=="visible") return Snapshot();
    json out={{"owned",floatingID!=0},{"available",View()!=nullptr},
        {"capture_ready",false},{"window_state",state}};
    if(floatingID) {
        out["owner"]="agent"; out["label"]="AGENT VIEWPORT";
        out["floating_id"]=floatingID; out["view_id"]=viewID;
        out["next_action"]=state=="minimized" ? "restore" : "release then open";
    }
    return out;
}

void SetMinimized(bool minimize) {
    if(!View()) throw std::runtime_error("Agent viewport is unavailable or its layout changed; release then open");
    const std::string state=WindowState();
    if(state=="hidden") throw std::runtime_error("Agent viewport was closed; release then open");
    HWND top=FloatingWindow();
    if(!top) throw std::runtime_error("Owned floating viewport window is unavailable");
    if(minimize==(state=="minimized")) return;
    if(minimize && ReadRenderState().Rendered())
        throw std::runtime_error("Switch AGENT VIEWPORT to render mode shaded before minimizing it");
    // RestoreActivation must not select a viewport inside the window we are
    // minimizing. Refuse before mutation instead of guessing the user's panel.
    if(minimize && GetViewPanelManager()->GetActiveViewPanel()==Panel())
        throw std::runtime_error("Activate your own viewport before minimizing AGENT VIEWPORT");
    RestoreActivation activation;
    ShowWindow(top,minimize ? SW_SHOWMINNOACTIVE : SW_SHOWNOACTIVATE);
    ++generation; // A restored viewport must be captured again before targeting.
    if((IsIconic(top)!=FALSE)!=minimize) throw std::runtime_error("Could not change AGENT VIEWPORT minimized state");
}

class Label final : public ViewportDisplayCallback {
public:
    void Display(TimeValue, ViewExp* view, int) override {
        if (!view || view->GetViewID() != viewID || !Panel()) return;
        ++draws;
        auto* gw = view->getGW();
        if (!gw) return;
        gw->setColor(TEXT_COLOR, 0.25f, 0.9f, 1.0f);
        IPoint3 at(16, 48, 0);
        gw->wText(&at, kTitle);
    }
    void GetViewportRect(TimeValue, ViewExp* view, Rect* rect) override {
        rect->SetEmpty();
        if (view && view->GetViewID() == viewID) { *rect += IPoint2(0,0); *rect += IPoint2(400,80); }
    }
    BOOL Foreground() override { return FALSE; }
} label;

float Number(const json& p, const char* key, float fallback, float lo, float hi) {
    if (!p.contains(key)) return fallback;
    const auto& value = p.at(key);
    if (!value.is_number()) throw std::runtime_error(std::string(key)+" must be numeric");
    double x = value.get<double>();
    if (!std::isfinite(x) || x < lo || x > hi)
        throw std::runtime_error(std::string(key)+" is outside the supported range");
    return static_cast<float>(x);
}

Point3 Point(const json& v) {
    if (v.type()!=json::value_t::array || v.size()!=3) throw std::runtime_error("Expected three finite coordinates");
    Point3 out;
    for (int i=0;i<3;++i) {
        if (!v[i].is_number()) throw std::runtime_error("Expected numeric coordinates");
        double x = v[i].get<double>();
        if (!std::isfinite(x) || std::abs(x)>1e12) throw std::runtime_error("Invalid coordinate");
        out[i] = static_cast<float>(x);
    }
    return out;
}

ViewExp10* Extended(ViewExp& view) {
    auto* ext = reinterpret_cast<ViewExp10*>(view.Execute(ViewExp::kEXECUTE_GET_VIEWEXP_10));
    if (!ext) throw std::runtime_error("Extended viewport API unavailable");
    return ext;
}

Matrix3 Look(const Point3& eye, const Point3& target) {
    Point3 z = eye-target;
    if (Length(z)<1e-6f) throw std::runtime_error("eye and target must differ");
    z = Normalize(z);
    Point3 up = std::abs(z.z)>0.999f ? Point3(0,1,0) : Point3(0,0,1);
    Point3 x = Normalize(CrossProd(up,z));
    return Inverse(Matrix3(x, Normalize(CrossProd(z,x)), z, eye));
}

void Descendants(INode* node, std::vector<INode*>& nodes) {
    nodes.push_back(node);
    for(int i=0;i<node->NumberOfChildren();++i) Descendants(node->GetChildNode(i),nodes);
}

Box3 Bounds(const json& p) {
    std::vector<INode*> nodes;
    if (p.contains("frame_names") && !p.at("frame_names").empty()) {
        for (const auto& name : p.at("frame_names")) {
            if (name.type()!=json::value_t::string) throw std::runtime_error("frame_names must contain names");
            auto matches = CollectNodesByExactName(name.get<std::string>());
            if (matches.size()!=1) throw std::runtime_error("Framing name must resolve uniquely: "+name.get<std::string>());
            Descendants(matches.front(),nodes);
        }
    } else CollectNodes(GetCOREInterface()->GetRootNode(),nodes);
    Box3 box; box.Init();
    for (auto* node : nodes) {
        if (node->IsNodeHidden()) continue;
        auto* obj = node->EvalWorldState(GetCOREInterface()->GetTime()).obj;
        Get(); // Evaluation can pump UI messages and replace the owned viewport.
        if (!obj || (obj->SuperClassID()!=GEOMOBJECT_CLASS_ID && obj->SuperClassID()!=SHAPE_CLASS_ID)) continue;
        Box3 part = SpatialSnapshot::WorldBoundingBox(node,GetCOREInterface()->GetTime());
        Get();
        if (!part.IsEmpty()) { box += part.Min(); box += part.Max(); }
    }
    if (box.IsEmpty()) throw std::runtime_error("No visible geometry to frame");
    for (int i=0;i<3;++i) if (!std::isfinite(box.Min()[i]) || !std::isfinite(box.Max()[i]))
        throw std::runtime_error("Non-finite scene bounds");
    return box;
}

void Frame(ViewExp& view, const Box3& box, float padding) {
    Matrix3 tm; view.GetAffineTM(tm);
    Matrix3 camera = Inverse(tm);
    Point3 center = box.Center();
    float halfWidth = 0, halfHeight = 0, distance = 1;
    auto* gw = view.getGW();
    float aspect = static_cast<float>(gw->getWinSizeX()) / std::max(1,gw->getWinSizeY());
    float tanH = std::tan(view.GetFOV()*0.5f);
    for (int i=0;i<8;++i) {
        Point3 q = box[i]-center;
        float x = DotProd(q,camera.GetRow(0))*padding;
        float y = DotProd(q,camera.GetRow(1))*padding;
        float z = DotProd(q,camera.GetRow(2));
        halfWidth = std::max(halfWidth,std::abs(x));
        halfHeight = std::max(halfHeight,std::abs(y));
        distance = std::max(distance,std::max(std::abs(x)/tanH,std::abs(y)*aspect/tanH)+z);
    }
    if (!view.IsPerspView()) distance = std::max(1.f,Length(box.Width())*2);
    camera.SetTrans(center+camera.GetRow(2)*distance);
    view.SetAffineTM(Inverse(camera));
    view.SetFocalDist(distance); Extended(view)->SetFocalDistance(distance);
    if (!view.IsPerspView()) {
        float wanted = std::max(0.001f,2*std::max(halfWidth,halfHeight*aspect));
        float current = view.GetVPWorldWidth(center);
        if (current>0) Extended(view)->Zoom(wanted/current);
        view.SetAffineTM(Inverse(camera));
    }
    orbitTarget = center;
}

std::string Token(ViewExp& view, const RenderState* renderState=nullptr) {
    SceneJournal::FlushPending();
    Matrix3 tm; view.GetAffineTM(tm);
    std::ostringstream s; s << std::setprecision(9) << generation << ':' << viewID << ':' << view.GetViewType();
    for(int r=0;r<4;++r) for(int c=0;c<3;++c) s << ':' << tm.GetRow(r)[c];
    s << ':' << view.GetFOV() << ':' << view.GetVPWorldWidth(orbitTarget)
      << ':' << view.getGW()->getWinSizeX() << ':' << view.getGW()->getWinSizeY();
    s << ':' << GetCOREInterface()->GetTime() << ':' << SceneJournal::CurrentMutationSeq();
    s << ':' << view.IsGridVisible();
    auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
    if(settings) s << ':' << settings->GetViewportVisualStyle() << ':' << settings->GetShowEdgedFaces();
    const auto state=renderState ? *renderState : ReadRenderState();
    s << ':' << state.activeShade << ':' << state.cpu << ':' << state.gpu;
    // Stable deterministic token; navigation by either the user or agent invalidates it.
    uint64_t hash = 14695981039346656037ull;
    for (unsigned char ch : s.str()) { hash ^= ch; hash *= 1099511628211ull; }
    std::ostringstream out; out << std::hex << hash; return out.str();
}

void Open(const json& p) {
    const int width=static_cast<int>(Number(p,"width",1000,320,4096));
    const int height=static_cast<int>(Number(p,"height",740,240,4096));
    const bool startMinimized=p.value("start_minimized",false);
    if (Panel()) {
        if(!View()) throw std::runtime_error("Agent viewport layout changed. Release it before opening a fresh one.");
        SetMinimized(startMinimized);
        if(!startMinimized) Redraw();
        return;
    }
    auto* manager = GetViewPanelManager();
    if (!manager) throw std::runtime_error("Floating view panels unavailable");
    IViewPanel* candidate = nullptr;
    int candidateID = 0;
    for (int i=1;i<=manager->GetNumFloatingViewPanels();++i) {
        int index = manager->GetViewPanelIndexFromFloatingID(i);
        auto* panel = index>=0 ? manager->GetViewPanel(index) : nullptr;
        if (panel && !panel->IsViewPanelVisible() &&
            !IsIconic(GetAncestor(panel->GetHWnd(),GA_ROOT)) && !GetPropW(panel->GetHWnd(),kOwner)) {
            candidate = panel; candidateID = i; break;
        }
    }
    if (!candidate) throw std::runtime_error("All floating viewports are in use; no user viewport was taken over");
    RestoreActivation activation;
    Matrix3 seed; GetCOREInterface()->GetActiveViewExp().GetAffineTM(seed);
    bool perspective = GetCOREInterface()->GetActiveViewExp().IsPerspView()!=0;
    float fov = GetCOREInterface()->GetActiveViewExp().GetFOV();
    try {
        floatingID = candidateID;
        panelWindow = candidate->GetHWnd();
        if (!SetPropW(panelWindow,kOwner,reinterpret_cast<HANDLE>(&floatingID))) throw std::runtime_error("Cannot reserve viewport ownership");
        candidate->SetViewPanelName(MSTR(kTitle));
        candidate->SetLayout(VP_LAYOUT_1);
        auto& view = candidate->GetViewExpByIndex(0);
        viewID = view.GetViewID(); ++generation;
        view.SetViewUser(perspective);
        if (perspective) Extended(view)->SetFOV(fov);
        view.SetAffineTM(seed);
        orbitTarget = Inverse(seed).GetTrans()-Inverse(seed).GetRow(2)*std::max(1.f,view.GetFocalDist());
        view.SetGridVisibility(FALSE);
        auto* settings = static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
        if (settings) {
            settings->SetViewportVisualStyle(MaxSDK::Graphics::VisualStyleShaded);
            settings->SetShowEdgedFaces(false);
            settings->SetUseTexture(true);
            settings->SetProgressiveRenderingEnabled(false);
            settings->SetShowHighLight(false);
            settings->SetShowSelectionBrackets(false);
            settings->SetSelectedEdgedFaces(false);
            settings->SetShadeSelectedObjects(false);
            settings->SetViewportDisable(false);
        }
        if (!registered) {
            GetCOREInterface()->RegisterViewportDisplayCallback(FALSE,&label);
            RegisterNotification(SceneChanged,nullptr,NOTIFY_FILE_POST_OPEN);
            RegisterNotification(SceneChanged,nullptr,NOTIFY_SYSTEM_POST_RESET);
            RegisterNotification(SceneChanged,nullptr,NOTIFY_SYSTEM_POST_NEW);
            registered=true;
        }
        manager->SetFloatingViewPanelVisibility(floatingID,true);
        HWND top = FloatingWindow();
        if(!top) throw std::runtime_error("Owned floating viewport window is unavailable");
        if (top) {
            SetWindowTextW(top,kTitle);
            MONITORINFO info{}; info.cbSize=sizeof(info);
            GetMonitorInfoW(MonitorFromWindow(top,MONITOR_DEFAULTTONEAREST),&info);
            const RECT& area=info.rcWork;
            int w=std::min(width,static_cast<int>(area.right-area.left));
            int h=std::min(height,static_cast<int>(area.bottom-area.top));
            SetWindowPos(top,nullptr,area.right-w,area.top,w,h,SWP_NOACTIVATE|SWP_NOZORDER);
            ShowWindow(top,startMinimized ? SW_SHOWMINNOACTIVE : SW_SHOWNOACTIVATE);
            if((IsIconic(top)!=FALSE)!=startMinimized)
                throw std::runtime_error("Could not set AGENT VIEWPORT initial minimized state");
        }
    } catch (...) {
        Shutdown(); throw;
    }
    // Minimized Nitrous panels cannot produce trustworthy images. The caller
    // explicitly restores this panel before navigation or capture.
    if(!startMinimized) Redraw();
}
}

ViewExp& Get() {
    RequireMainThread();
    auto* view = View();
    if (!view) throw std::runtime_error("Agent viewport is unavailable or its layout changed; use agent_viewport open/release");
    const std::string state=WindowState();
    if(state=="minimized") throw std::runtime_error("AGENT VIEWPORT is minimized; use agent_viewport restore before navigation or capture");
    if(state=="hidden") throw std::runtime_error("AGENT VIEWPORT must remain visible for fresh Nitrous captures");
    if(state=="offscreen")
        throw std::runtime_error("AGENT VIEWPORT is offscreen; move it onto a monitor before capture");
    if(state!="visible") throw std::runtime_error("Agent viewport identity changed; release then open");
    return *view;
}
bool IsOwned() { return floatingID!=0; }
CameraState SaveCamera() {
    auto& view=Get(); CameraState state;
    view.GetAffineTM(state.tm); state.target=orbitTarget; state.fov=view.GetFOV();
    state.focal=view.GetFocalDist(); state.width=view.GetVPWorldWidth(orbitTarget);
    state.perspective=view.IsPerspView()!=0; state.grid=view.IsGridVisible()!=0;
    state.style=-1; state.edges=false;
    auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
    if(settings) { state.style=static_cast<int>(settings->GetViewportVisualStyle()); state.edges=settings->GetShowEdgedFaces(); }
    return state;
}
void RestoreCamera(const CameraState& state) {
    RequireMainThread();
    // Cleanup must restore the camera even if the user minimized this window
    // during a multi-view capture. Ownership/layout checks still apply; only
    // the visibility requirement is relaxed, and no capture is enabled here.
    auto* ownedView=View();
    if(!ownedView) throw std::runtime_error("Cannot restore camera: owned agent viewport identity changed");
    auto& view=*ownedView; view.SetViewUser(state.perspective);
    if(state.perspective) Extended(view)->SetFOV(state.fov);
    view.SetAffineTM(state.tm); orbitTarget=state.target;
    view.SetFocalDist(state.focal); Extended(view)->SetFocalDistance(state.focal);
    view.SetGridVisibility(state.grid);
    auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
    if(settings && state.style>=0) {
        settings->SetViewportVisualStyle(static_cast<MaxSDK::Graphics::VisualStyle>(state.style));
        settings->SetShowEdgedFaces(state.edges);
    }
    if(!state.perspective) {
        float current=view.GetVPWorldWidth(state.target);
        if(current>0 && state.width>0) Extended(view)->Zoom(state.width/current);
        view.SetAffineTM(state.tm);
    }
    if(WindowState()=="visible") Redraw();
}
bool IsRequested(const json& p) {
    std::string source = p.value("source", "auto");
    if (source!="auto" && source!="agent" && source!="active") throw std::runtime_error("source must be auto, agent, or active");
    return source=="agent" || (source=="auto" && IsOwned());
}
void Redraw() {
    auto before=draws;
    {
        auto& view=Get();
        Extended(view)->Invalidate(false);
    }
    GetCOREInterface()->ForceCompleteRedraw(FALSE);
    // An invalid ViewExp silently routes SDK calls to a default viewport.
    // Redraw/evaluation may pump UI messages: resolve ownership again before
    // touching the window, and validate once more after synchronous painting.
    HWND window=Get().GetHWnd();
    RedrawWindow(window,nullptr,nullptr,RDW_INVALIDATE|RDW_UPDATENOW);
    Get();
    if(draws==before) throw std::runtime_error("AGENT VIEWPORT has not redrawn; uncover the panel and retry");
}
json Snapshot() {
    const auto renderState=ReadRenderState();
    auto& view = Get();
    Matrix3 tm; view.GetAffineTM(tm); Matrix3 camera=Inverse(tm);
    json out={{"owner","agent"},{"owned",true},{"available",true},{"capture_ready",true},
        {"window_state","visible"},{"label","AGENT VIEWPORT"},{"floating_id",floatingID},
        {"view_id",viewID},{"view_token",Token(view,&renderState)},{"perspective",view.IsPerspView()!=0},
        {"eye",SpatialSnapshot::PointJson(camera.GetTrans())},{"target",SpatialSnapshot::PointJson(orbitTarget)},
        {"fov",view.GetFOV()*180/kPi},{"width",view.getGW()->getWinSizeX()},{"height",view.getGW()->getWinSizeY()},
        {"draw_count",draws},{"isolation_supported",false},{"render",RenderInfo(renderState)}};
    auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
    if(settings) {
        const auto style=settings->GetViewportVisualStyle();
        out["shading"]=style==MaxSDK::Graphics::VisualStyleWireframe ? "wireframe" :
            style==MaxSDK::Graphics::VisualStyleConsistentColor ? "flat" :
            style==MaxSDK::Graphics::VisualStyleShaded ? "smooth" : "other";
        out["edges"]=settings->GetShowEdgedFaces();
    }
    return out;
}
json Project(const json& points) {
    if(ReadRenderState().Rendered()) throw std::runtime_error("Switch to shaded mode before projecting component labels");
    auto& view=Get(); Matrix3 tm; view.GetAffineTM(tm);
    if(points.type()!=json::value_t::array || points.size()>2000) throw std::runtime_error("Expected at most 2000 points");
    json output=json::array();
    for(const auto& p:points) {
        Point3 v=Point(p)*tm;
        if(view.IsPerspView() && v.z>=-1e-6f) { output.push_back(nullptr); continue; }
        Point2 screen=view.MapViewToScreen(v);
        output.push_back({screen.x,screen.y});
    }
    return output;
}
void Configure(const json& p) {
    Get();
    float padding=Number(p,"padding",1.15f,1,3), fov=Number(p,"fov",45,1,175);
    std::string name=p.value("view","perspective"), shading=p.value("shading","smooth");
    Point3 eye(1000,-1500,900), target(0,0,0);
    bool perspective=name=="perspective";
    if (name=="front") eye=Point3(0,-1000,0);
    else if(name=="back") eye=Point3(0,1000,0);
    else if(name=="left") eye=Point3(-1000,0,0);
    else if(name=="right") eye=Point3(1000,0,0);
    else if(name=="top") eye=Point3(0,0,1000);
    else if(name=="bottom") eye=Point3(0,0,-1000);
    else if(name!="perspective" && name!="orthographic") throw std::runtime_error("Unknown view");
    if(p.contains("eye")!=p.contains("target")) throw std::runtime_error("eye and target are required together");
    if(p.contains("eye")) { eye=Point(p.at("eye")); target=Point(p.at("target")); }
    Matrix3 tm=Look(eye,target);
    Box3 bounds; bool frame=p.contains("frame_names") || p.value("frame_all",false);
    if(frame) bounds=Bounds(p);
    if(shading!="smooth" && shading!="wireframe" && shading!="flat") throw std::runtime_error("Invalid shading");
    auto& view=Get(); // Bounds evaluates geometry; never reuse a pre-evaluation view.
    view.SetViewUser(perspective);
    if(perspective) Extended(view)->SetFOV(fov*kPi/180);
    if(!view.SetAffineTM(tm)) throw std::runtime_error("Could not set agent view transform");
    orbitTarget=target;
    view.SetFocalDist(Length(eye-target)); Extended(view)->SetFocalDistance(Length(eye-target));
    if(frame) Frame(view,bounds,padding);
    view.SetGridVisibility(p.value("grid",false));
    auto* settings=static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
    if(settings) {
        settings->SetShowEdgedFaces(p.value("edges",false));
        settings->SetViewportVisualStyle(shading=="wireframe" ? MaxSDK::Graphics::VisualStyleWireframe :
            shading=="flat" ? MaxSDK::Graphics::VisualStyleConsistentColor : MaxSDK::Graphics::VisualStyleShaded);
    }
    Redraw();
}
json Execute(const json& p) {
    RequireMainThread();
    if(!p.is_object()) throw std::runtime_error("Expected object payload");
    std::string action=p.value("action","status");
    if(action=="release") { Shutdown(); return {{"owned",false}}; }
    if(action=="open") { Open(p); return Status(); }
    if(action=="status") return Status();
    if(action=="minimize" || action=="restore") {
        SetMinimized(action=="minimize");
        if(action=="restore") Redraw();
        return Status();
    }
    Get();
    if(p.contains("expected_view") && p.at("expected_view")!=Token(Get())) throw std::runtime_error("STALE_VIEW: capture again before targeting");
    if(action=="render") SetRenderMode(p);
    else if(action=="set") Configure(p);
    else if(action=="frame") {
        const float padding=Number(p,"padding",1.15f,1,3);
        const Box3 bounds=Bounds(p);
        Frame(Get(),bounds,padding);
        Redraw();
    }
    else if(action=="orbit") {
        auto& view=Get();
        float yaw=Number(p,"yaw",0,-360,360)*kPi/180, pitch=Number(p,"pitch",0,-179,179)*kPi/180;
        Matrix3 tm; view.GetAffineTM(tm); Matrix3 cam=Inverse(tm);
        Point3 delta=cam.GetTrans()-orbitTarget;
        float radius=std::max(0.001f,Length(delta));
        float azimuth=std::atan2(delta.y,delta.x)+yaw;
        float elevation=std::asin(std::clamp(delta.z/radius,-1.f,1.f))+pitch;
        elevation=std::clamp(elevation,-kPi*0.495f,kPi*0.495f);
        Point3 eye=orbitTarget+Point3(std::cos(azimuth)*std::cos(elevation),std::sin(azimuth)*std::cos(elevation),std::sin(elevation))*radius;
        view.SetAffineTM(Look(eye,orbitTarget)); Redraw();
    } else if(action=="pan") {
        auto& view=Get();
        float x=Number(p,"x",0,-1e9,1e9),y=Number(p,"y",0,-1e9,1e9);
        Matrix3 tm; view.GetAffineTM(tm); Matrix3 cam=Inverse(tm);
        Point3 delta=cam.GetRow(0)*x+cam.GetRow(1)*y;
        cam.SetTrans(cam.GetTrans()+delta); orbitTarget+=delta;
        view.SetAffineTM(Inverse(cam)); Redraw();
    } else if(action=="zoom") {
        auto& view=Get();
        float factor=Number(p,"factor",1,0.01f,100);
        if(view.IsPerspView()) {
            Matrix3 tm; view.GetAffineTM(tm); Matrix3 cam=Inverse(tm);
            Point3 eye=orbitTarget+(cam.GetTrans()-orbitTarget)*factor;
            view.SetAffineTM(Look(eye,orbitTarget));
            view.SetFocalDist(Length(eye-orbitTarget)); Extended(view)->SetFocalDistance(Length(eye-orbitTarget));
        } else Extended(view)->Zoom(factor);
        Redraw();
    } else if(action=="ray" || action=="pick") {
        if(ReadRenderState().Rendered()) throw std::runtime_error("Switch to shaded mode and capture again before geometry targeting");
        float x=Number(p,"x",0,0,1),y=Number(p,"y",0,0,1);
        Ray ray;
        {
            auto& view=Get();
            view.MapScreenToWorldRay(x*(view.getGW()->getWinSizeX()-1),y*(view.getGW()->getWinSizeY()-1),ray);
        }
        ray.dir=Normalize(ray.dir);
        json out=Snapshot(); out["origin"]=SpatialSnapshot::PointJson(ray.p); out["direction"]=SpatialSnapshot::PointJson(ray.dir);
        if(action=="pick") {
            TimeValue time=GetCOREInterface()->GetTime();
            std::vector<INode*> nodes; CollectNodes(GetCOREInterface()->GetRootNode(),nodes);
            float nearest=std::numeric_limits<float>::max(); INode* hit=nullptr;
            Point3 hitPoint, hitNormal; bool hitShape=false;
            for(auto* node:nodes) {
                if(node->IsNodeHidden()) continue;
                {
                    auto* filter=static_cast<ViewExp23&>(Get()).GetIPerViewportFilter();
                    if(filter && filter->IsNodeFiltered(node)) continue;
                }
                auto* object=node->EvalWorldState(time).obj;
                Get();
                if(!object) continue;
                const bool isShape=object->SuperClassID()==SHAPE_CLASS_ID;
                if(!isShape && object->SuperClassID()!=GEOMOBJECT_CLASS_ID) continue;
                Matrix3 objectTM=node->GetObjTMAfterWSM(time);
                Get();
                if(std::abs(DotProd(objectTM.GetRow(0),CrossProd(objectTM.GetRow(1),objectTM.GetRow(2))))<1e-12f) continue;
                Matrix3 inverse=Inverse(objectTM);
                Ray localRay; localRay.p=ray.p*inverse; localRay.dir=Normalize(VectorTransform(inverse,ray.dir));
                float distance=0; Point3 normal;
                int intersects=0;
                if(isShape) {
                    // ShapeObject's default IntersectRay returns FALSE. Intersect
                    // the visible tube/ribbon mesh, using the same thickness and
                    // tessellation settings as the viewport. No node conversion.
                    auto* shape=static_cast<ShapeObject*>(object);
                    if(!shape->GetDispRenderMesh()) continue;
                    Mesh displayMesh;
                    shape->GenerateMesh(time,shape->GetUseViewport() ? GENMESH_VIEWPORT : GENMESH_RENDER,&displayMesh);
                    Get();
                    intersects=displayMesh.IntersectRay(localRay,distance,normal);
                } else intersects=object->IntersectRay(time,localRay,distance,normal);
                Get();
                if(!intersects || distance<0) continue;
                Point3 worldPoint=(localRay.p+localRay.dir*distance)*objectTM;
                float worldDistance=DotProd(worldPoint-ray.p,ray.dir);
                if(worldDistance>=0 && worldDistance<nearest) {
                    nearest=worldDistance; hit=node; hitPoint=worldPoint; hitShape=isShape;
                    // Inverse transpose for normals under nonuniform scale.
                    hitNormal=Normalize(Point3(DotProd(normal,inverse.GetRow(0)),
                        DotProd(normal,inverse.GetRow(1)),DotProd(normal,inverse.GetRow(2))));
                }
            }
            if(out.at("view_token")!=Token(Get()))
                throw std::runtime_error("STALE_VIEW: view or scene changed during surface picking; capture again");
            out["hit"]=hit ? json{{"handle",NodeHandle(hit)},{"name",WideToUtf8(hit->GetName())},
                {"point",SpatialSnapshot::PointJson(hitPoint)},{"normal",SpatialSnapshot::PointJson(hitNormal)},{"distance",nearest},
                {"surface",hitShape ? "viewport spline mesh; use draw_spline to inspect knots" :
                    "evaluated object; inspect base cage before component edits"}} : json(nullptr);
        }
        return out;
    } else if(action=="project") {
        json out=Snapshot(); out["pixels"]=Project(p.at("points"));
        auto& view=Get();
        Matrix3 tm; view.GetAffineTM(tm);
        json projections=json::array();
        for(size_t i=0;i<p.at("points").size();++i) {
            const Point3 cameraPoint=Point(p.at("points")[i])*tm;
            const auto& pixel=out["pixels"][i];
            const bool inFront=!view.IsPerspView() || cameraPoint.z < -1e-6f;
            bool inFrame=false;
            if(!pixel.is_null()) {
                const float x=pixel[0].get<float>(), y=pixel[1].get<float>();
                inFrame=x>=0 && y>=0 && x<view.getGW()->getWinSizeX() && y<view.getGW()->getWinSizeY();
            }
            projections.push_back({{"pixel",pixel},{"depth",-cameraPoint.z},
                {"in_front",inFront},{"in_frame",inFrame}});
        }
        out["projections"]=std::move(projections);
        return out;
    } else throw std::runtime_error("Unknown agent viewport action");
    return Snapshot();
}
void Shutdown(bool processExit) {
    if(View()) {
        HWND top=FloatingWindow();
        if(top && IsIconic(top)) ShowWindow(top,SW_SHOWNOACTIVATE);
        // Keep the ownership until renderer shutdown succeeds; a failed stop
        // must not orphan a render and allow a subsequent slot to take it over.
        try { StopOwnedRender(); }
        catch(...) {
            if(!processExit) throw;
            // During Max shutdown, always detach callbacks before the GUP can
            // unload; renderer teardown continues through Max's own shutdown.
            OutputDebugStringW(L"3dsmax-mcp: preview stop incomplete during Max shutdown\n");
        }
    }
    if(registered) {
        GetCOREInterface()->UnRegisterViewportDisplayCallback(FALSE,&label);
        UnRegisterNotification(SceneChanged,nullptr,NOTIFY_FILE_POST_OPEN);
        UnRegisterNotification(SceneChanged,nullptr,NOTIFY_SYSTEM_POST_RESET);
        UnRegisterNotification(SceneChanged,nullptr,NOTIFY_SYSTEM_POST_NEW);
        registered=false;
    }
    if(Panel()) {
        // Max's hide API leaves WS_MINIMIZE set. Normalize only our owned
        // window before hiding it, otherwise the next open skips this slot
        // as a user-minimized viewport and each release leaks a usable panel.
        HWND top=FloatingWindow();
        if(top && IsIconic(top)) ShowWindow(top,SW_SHOWNOACTIVATE);
        // Show/hide can dispatch Max window callbacks: re-resolve ownership.
        if(Panel()) GetViewPanelManager()->SetFloatingViewPanelVisibility(floatingID,false);
        if(Panel()) RemovePropW(panelWindow,kOwner);
    }
    floatingID=0; viewID=-1; panelWindow=nullptr; ++generation;
}
}
