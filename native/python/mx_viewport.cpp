// Viewports and captures: the user's active view, the agent's own floating
// AGENT VIEWPORT, view control, surface picking and saved images.
//
// The AGENT VIEWPORT belongs to the bridge's shell, so it is driven through
// the native:agent_viewport handler, and a call meant for it never falls back
// into the user's view. Captures run the bridge's capture handlers in-process
// and return a Capture: the image file and compact metadata.
#include "mx_common.h"

#include <GraphicsWindow.h>
#include <Graphics/IViewportViewSetting.h>
#include <maxcom.h>

#include <filesystem>
#include <fstream>

namespace mx {
inline namespace MX_ABI_TAG {

using json = nlohmann::json;
using MaxSDK::Graphics::VisualStyle;

namespace {

// ── Objects ──────────────────────────────────────────────────────

// A view, resolved on every call: the user's active viewport, or the AGENT VIEWPORT.
struct ViewRef { bool agent = false; };
struct AgentViewRef : ViewRef { AgentViewRef() { agent = true; } };

struct Capture {
    std::string path;
    int width = 0;
    int height = 0;
    std::string source;
    nb::object views = nb::list();
    std::vector<int> frames;
    std::pair<int, int> grid{1, 1};
    std::vector<Node> nodes;
    std::string token;  // the agent view it shows, so pick/ray/project can refuse a changed view
};

struct Hit {
    Node node;
    nb::object point;
    nb::object normal;
    double distance = 0;
};

constexpr int kMaxWidth = 1600;

nb::object TupleOf(const json& p) {
    return nb::make_tuple(p.at(0).get<double>(), p.at(1).get<double>(), p.at(2).get<double>());
}

json JsonOf(const Point3& p) { return json::array({p.x, p.y, p.z}); }

bool Finite(const Point3& p) { return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z); }

// The bridge frames nodes by name, so each name must be the node's own.
std::string FramingName(INode* node) {
    const std::string name = NodeName(node);
    const size_t count = HandlerHelpers::CollectNodesByName(name).size();
    if (count != 1)
        throw Ambiguous(std::to_string(count) + " nodes are named '" + name + "' and views frame by name; rename one");
    return name;
}

json FramingNames(nb::handle nodes) {
    json names = json::array();
    for (INode* node : NodeList(nodes, "frame")) names.push_back(FramingName(node));
    return names;
}

// ── AGENT VIEWPORT ───────────────────────────────────────────────

json Agent(const std::string& action, json args = json::object()) {
    args["action"] = action;
    args["source"] = "agent";
    return NativeJson("native:agent_viewport", args);
}

bool AgentOpen() { return Agent("status").value("owned", false); }

// The open, visible AGENT VIEWPORT's state: camera, size, view token, render mode.
json AgentState() {
    json state = Agent("status");
    if (!state.value("owned", false)) throw NotFound("the AGENT VIEWPORT is not open; open it with agent_viewport()");
    if (state.value("capture_ready", false)) return state;
    const std::string window = state.value("window_state", "unavailable");
    if (window == "minimized") throw MaxError("the AGENT VIEWPORT is minimized; call restore() first");
    if (window == "offscreen") throw MaxError("the AGENT VIEWPORT is offscreen; move it onto a monitor");
    throw MaxError("the AGENT VIEWPORT is " + window + "; release() it, then open it again with agent_viewport()");
}

// expected_view: coordinates taken from a capture are refused once its view changed.
void Expect(json& args, const std::optional<Capture>& capture) {
    if (!capture) return;
    if (capture->token.empty())
        throw BadParam("that capture is not of the shaded AGENT VIEWPORT; pass one from agent_viewport().capture()");
    args["expected_view"] = capture->token;
}

json ImagePoint(double x, double y) {
    if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1))
        throw BadParam("x and y are image coordinates from 0 to 1, measured from the top-left");
    return {{"x", x}, {"y", y}};
}

// ── Reading a view ───────────────────────────────────────────────

struct ViewInfo {
    std::string type;
    Point3 eye;
    Point3 target;
    std::optional<double> fov;  // perspective views only
    int width = 0;
    int height = 0;
    std::string shading;
    bool edges = false;
};

MaxSDK::Graphics::IViewportViewSetting* Settings(ViewExp& view) {
    return static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
}

ViewExp10* Extended(ViewExp& view) {
    auto* extended = reinterpret_cast<ViewExp10*>(view.Execute(ViewExp::kEXECUTE_GET_VIEWEXP_10));
    if (!extended) throw MaxError("this viewport has no camera controls");
    return extended;
}

// The names the AGENT VIEWPORT reports, so both kinds of view read alike.
const char* StyleName(VisualStyle style) {
    using namespace MaxSDK::Graphics;
    switch (style) {
    case VisualStyleRealistic: return "realistic";
    case VisualStyleShaded: return "smooth";
    case VisualStyleFacets: return "facets";
    case VisualStyleConsistentColor: return "flat";
    case VisualStyleHiddenLine: return "hidden_line";
    case VisualStyleWireframe: return "wireframe";
    case VisualStyleBoundingBox: return "bounding_box";
    case VisualStyleClay: return "clay";
    case VisualStyleModelAssist: return "model_assist";
    default: return "stylized";
    }
}

const char* ViewTypeName(int type) {
    switch (type) {
    case VIEW_LEFT: return "left";
    case VIEW_RIGHT: return "right";
    case VIEW_TOP: return "top";
    case VIEW_BOTTOM: return "bottom";
    case VIEW_FRONT: return "front";
    case VIEW_BACK: return "back";
    case VIEW_ISO_USER: return "orthographic";
    case VIEW_PERSP_USER: return "perspective";
    case VIEW_CAMERA: return "camera";
    case VIEW_SPOT: return "light";
    default: return "other";
    }
}

ViewExp& ActiveView() {
    RequireMain();
    return GetCOREInterface()->GetActiveViewExp();
}

ViewInfo ActiveInfo() {
    ViewExp& view = ActiveView();
    Matrix3 tm;
    view.GetAffineTM(tm);
    const Matrix3 camera = Inverse(tm);
    ViewInfo info;
    info.type = ViewTypeName(view.GetViewType());
    info.eye = camera.GetTrans();
    info.target = info.eye - Normalize(camera.GetRow(2)) * view.GetFocalDist();
    if (view.IsPerspView()) info.fov = RadToDeg(view.GetFOV());
    if (GraphicsWindow* gw = view.getGW()) {
        info.width = gw->getWinSizeX();
        info.height = gw->getWinSizeY();
    }
    if (auto* settings = Settings(view)) {
        info.shading = StyleName(settings->GetViewportVisualStyle());
        info.edges = settings->GetShowEdgedFaces();
    }
    return info;
}

Point3 PointOf(const json& p) { return Point3(p.at(0).get<float>(), p.at(1).get<float>(), p.at(2).get<float>()); }

ViewInfo AgentInfo() {
    const json state = AgentState();
    const bool perspective = state.value("perspective", true);
    ViewInfo info;
    info.type = perspective ? "perspective" : "orthographic";
    info.eye = PointOf(state.at("eye"));
    info.target = PointOf(state.at("target"));
    if (perspective) info.fov = state.value("fov", 0.0);
    info.width = state.value("width", 0);
    info.height = state.value("height", 0);
    info.shading = state.value("shading", "");
    info.edges = state.value("edges", false);
    return info;
}

ViewInfo InfoOf(const ViewRef& v) { return v.agent ? AgentInfo() : ActiveInfo(); }

std::string ViewRepr(const ViewRef& v) {
    if (!v.agent) {
        const ViewInfo info = ActiveInfo();
        return "<Viewport active " + info.type + " " + std::to_string(info.width) + "x" + std::to_string(info.height) + ">";
    }
    try {
        const json state = Agent("status");
        if (!state.value("owned", false)) return "<AgentViewport closed>";
        if (!state.value("capture_ready", false)) return "<AgentViewport " + state.value("window_state", "unavailable") + ">";
        return "<AgentViewport " + std::string(state.value("perspective", true) ? "perspective " : "orthographic ") +
               std::to_string(state.value("width", 0)) + "x" + std::to_string(state.value("height", 0)) + ">";
    } catch (const MaxError&) {
        return "<AgentViewport unavailable>";
    }
}

// ── Setting a view ───────────────────────────────────────────────

const char* const kViews[] = {"perspective", "orthographic", "front", "back", "left", "right", "top", "bottom"};

struct FixedView { const char* name; int command; int type; };
const FixedView kFixedViews[] = {
    {"front", MAXCOM_VPT_FRONT, VIEW_FRONT}, {"back", MAXCOM_VPT_BACK, VIEW_BACK},
    {"left", MAXCOM_VPT_LEFT, VIEW_LEFT},    {"right", MAXCOM_VPT_RIGHT, VIEW_RIGHT},
    {"top", MAXCOM_VPT_TOP, VIEW_TOP},       {"bottom", MAXCOM_VPT_BOTTOM, VIEW_BOTTOM},
};

using Aim = std::pair<Point3, Point3>;  // eye, target

void SetFixedView(const std::string& view) {
    Interface* ip = GetCOREInterface();
    for (const FixedView& fixed : kFixedViews) {
        if (view != fixed.name) continue;
        ip->ExecuteMAXCommand(fixed.command);
        if (ip->GetActiveViewExp().GetViewType() != fixed.type)
            throw MaxError("3ds Max did not switch the active viewport to " + view);
        return;
    }
}

// Camera-to-world for an eye looking at target, world Z up (+Y up when looking straight down or up).
Matrix3 CameraAt(const Point3& eye, const Point3& target) {
    const Point3 z = Normalize(eye - target);
    const Point3 up = std::abs(z.z) > 0.999f ? Point3(0, 1, 0) : Point3(0, 0, 1);
    const Point3 x = Normalize(CrossProd(up, z));
    return Matrix3(x, Normalize(CrossProd(z, x)), z, eye);
}

void SetFocus(ViewExp& view, float distance) {
    view.SetFocalDist(distance);
    Extended(view)->SetFocalDistance(distance);
}

Box3 WorldBounds(const std::vector<INode*>& nodes) {
    const TimeValue t = GetCOREInterface()->GetTime();
    Box3 box;
    for (INode* node : nodes) {
        Box3 part;
        const ObjectState os = node->EvalWorldState(t);
        if (os.obj) {
            Matrix3 tm = node->GetObjectTM(t);
            os.obj->GetDeformBBox(t, part, &tm, FALSE);
        }
        if (part.IsEmpty()) part += node->GetNodeTM(t).GetTrans();
        box += part;
    }
    return box;
}

// Fits the nodes' world bounds, padded, keeping the view's direction. Zoom to
// bounds alone can leave a perspective eye on a stale orbit, so perspective
// views then move the eye to the distance that fits all eight corners.
void FitActive(const std::vector<INode*>& nodes, float padding, float fov, const Matrix3* perspective) {
    Interface* ip = GetCOREInterface();
    const Box3 box = WorldBounds(nodes);
    const Point3 center = box.Center();
    const Point3 extent = (box.pmax - box.pmin) * (0.5f * padding);
    ip->ZoomToBounds(FALSE, Box3(center - extent, center + extent));
    if (!perspective) return;
    ViewExp& view = ip->GetActiveViewExp();
    GraphicsWindow* gw = view.getGW();
    const float tanH = std::tan(DegToRad(fov) * 0.5f);
    const float tanV = gw ? tanH * gw->getWinSizeY() / std::max(1, gw->getWinSizeX()) : tanH;
    Matrix3 camera = *perspective;
    float distance = 1;
    for (int i = 0; i < 8; ++i) {
        const Point3 q((i & 1) ? extent.x : -extent.x, (i & 2) ? extent.y : -extent.y, (i & 4) ? extent.z : -extent.z);
        const float depth = DotProd(q, camera.GetRow(2));
        distance = std::max({distance, std::abs(DotProd(q, camera.GetRow(0))) / tanH + depth,
                             std::abs(DotProd(q, camera.GetRow(1))) / tanV + depth});
    }
    camera.SetTrans(center + camera.GetRow(2) * distance);
    view.SetAffineTM(Inverse(camera));
    SetFocus(view, distance);
}

VisualStyle StyleOf(const std::string& shading) {
    if (shading == "wireframe") return MaxSDK::Graphics::VisualStyleWireframe;
    if (shading == "flat") return MaxSDK::Graphics::VisualStyleConsistentColor;
    return MaxSDK::Graphics::VisualStyleShaded;
}

void SetActive(const std::string& view, const std::optional<Aim>& aim, const std::vector<INode*>& nodes,
               float fov, float padding, const std::string& shading, bool edges, bool grid) {
    Interface* ip = GetCOREInterface();
    const bool perspective = view == "perspective";
    const bool free = perspective || view == "orthographic";
    if (free) ip->GetActiveViewExp().SetViewUser(perspective);
    else SetFixedView(view);
    ViewExp& vp = ip->GetActiveViewExp();
    if (perspective) Extended(vp)->SetFOV(DegToRad(fov));
    Matrix3 camera;
    if (free) {
        const Aim at = aim ? *aim : Aim(Point3(1000, -1500, 900), Point3(0, 0, 0));
        camera = CameraAt(at.first, at.second);
        if (!vp.SetAffineTM(Inverse(camera))) throw MaxError("3ds Max refused the view transform");
        SetFocus(vp, Length(at.first - at.second));
    }
    if (!nodes.empty()) FitActive(nodes, padding, fov, perspective ? &camera : nullptr);
    vp.SetGridVisibility(grid ? TRUE : FALSE);
    if (auto* settings = Settings(vp)) {
        settings->SetViewportVisualStyle(StyleOf(shading));
        settings->SetShowEdgedFaces(edges);
    }
    ip->ForceCompleteRedraw(FALSE);
}

void SetView(const ViewRef& v, const std::string& viewName, nb::handle eye, nb::handle target, nb::handle frame,
             double fov, double padding, const std::string& shadingName, bool edges, bool grid) {
    RequireMain();
    const std::string view = Lower(viewName);
    const std::string shading = Lower(shadingName);
    const std::vector<std::string> views(std::begin(kViews), std::end(kViews));
    if (std::find(views.begin(), views.end(), view) == views.end())
        throw BadParam("view must be " + Join(views) + ", not '" + viewName + "'." + Suggest(viewName, views));
    if (shading != "smooth" && shading != "wireframe" && shading != "flat")
        throw BadParam("shading must be smooth, wireframe or flat, not '" + shadingName + "'");
    if (!std::isfinite(fov) || fov < 1 || fov > 175) throw BadParam("fov must be 1-175 degrees");
    if (!std::isfinite(padding) || padding < 1 || padding > 3) throw BadParam("padding must be 1-3");
    if (eye.is_none() != target.is_none()) throw BadParam("pass eye and target together");
    std::optional<Aim> aim;
    if (!eye.is_none()) {
        if (view != "perspective" && view != "orthographic")
            throw BadParam("eye and target need view='perspective' or 'orthographic'");
        aim = Aim(ToPoint3(eye, "eye"), ToPoint3(target, "target"));
        if (!Finite(aim->first) || !Finite(aim->second)) throw BadParam("eye and target must be finite");
        if (Length(aim->first - aim->second) < 1e-6f) throw BadParam("eye and target must be different points");
    }
    if (v.agent) {
        json args = {{"view", view}, {"fov", fov}, {"padding", padding}, {"shading", shading}, {"edges", edges}, {"grid", grid}};
        if (aim) {
            args["eye"] = JsonOf(aim->first);
            args["target"] = JsonOf(aim->second);
        }
        if (!frame.is_none()) args["frame_names"] = FramingNames(frame);
        Agent("set", args);
        return;
    }
    const std::vector<INode*> nodes = frame.is_none() ? std::vector<INode*>() : NodeList(frame, "frame");
    SetActive(view, aim, nodes, float(fov), float(padding), shading, edges, grid);
}

// ── Captures ─────────────────────────────────────────────────────

Capture ImageOf(const json& reply, const std::string& source) {
    Capture c;
    c.path = reply.at("file").get<std::string>();
    c.width = reply.value("width", 0);
    c.height = reply.value("height", 0);
    c.source = source;
    return c;
}

json ImageArgs(const ViewRef& v, const std::string& method, int maxWidth, int maxHeight) {
    return {{"source", v.agent ? "agent" : "active"}, {"capture_method", method},
            {"max_width", maxWidth}, {"max_height", maxHeight}};
}

Capture CaptureViewport(const ViewRef& v, const std::string& method, int maxWidth, int maxHeight) {
    const json reply = NativeJson("native:capture_viewport", ImageArgs(v, method, maxWidth, maxHeight));
    Capture c = ImageOf(reply, reply.value("source", "active"));
    if (reply.contains("agent_viewport")) c.token = reply.at("agent_viewport").value("view_token", "");
    return c;
}

Capture CaptureScreen(const std::string& target, nb::handle crop, int maxWidth, int maxHeight) {
    json args = {{"target", target}, {"max_width", maxWidth}, {"max_height", maxHeight}};
    if (!crop.is_none()) args["crop"] = FromPython(crop);
    return ImageOf(NativeJson("native:capture_screen", args), target);
}

// The agent panel, or the frame buffer its V-Ray/Corona preview renders into.
Capture CaptureAgent(const ViewRef& v, nb::handle crop, const std::string& method, int maxWidth, int maxHeight) {
    const json render = AgentState().value("render", json::object());
    if (render.value("session_state", "") == "starting")
        throw MaxError("the " + render.value("mode", "") +
                       " preview is still starting; capture again once it renders (the VFB still shows older pixels)");
    const std::string target = render.value("capture_target", "agent");
    if (target == "vray_vfb" || target == "corona_vfb") return CaptureScreen(target, crop, maxWidth, maxHeight);
    if (!crop.is_none()) throw BadParam("crop applies to a VFB preview only: render('vray_vfb') or render('corona_vfb')");
    return CaptureViewport(v, method, maxWidth, maxHeight);
}

Capture CaptureTiles(const ViewRef& v, json args, nb::handle subject, const std::optional<std::string>& shading,
                     std::optional<bool> edges, const std::string& method, int maxWidth, int maxHeight) {
    args.update(ImageArgs(v, method, maxWidth, maxHeight));
    if (!subject.is_none()) args["frame_root"] = FramingName(Resolve(FindNode(subject)));
    if (shading) args["shading"] = *shading;
    if (edges) args["edges"] = *edges;
    const json reply = NativeJson("native:capture_multi_view", args);
    Capture c = ImageOf(reply, reply.value("source", "active"));
    nb::list views;
    for (const json& view : reply.value("views", json::array()))
        views.append(view.type() == json::value_t::string ? nb::object(nb::str(view.get<std::string>().c_str())) : TupleOf(view));
    c.views = views;
    if (reply.contains("frames")) c.frames = reply.at("frames").get<std::vector<int>>();
    const std::string grid = reply.value("grid", "1x1");
    const size_t x = grid.find('x');
    if (x != std::string::npos) c.grid = {std::stoi(grid.substr(0, x)), std::stoi(grid.substr(x + 1))};
    return c;
}

// A count spread over the animation range, or explicit frame numbers.
json StripFrames(nb::handle frames) {
    const char* usage = "frames is a count, capture_frames(6), or frame numbers, capture_frames([0, 10, 20])";
    if (nb::isinstance<nb::int_>(frames) && !nb::isinstance<nb::bool_>(frames))
        return {{"frame_count", nb::cast<long long>(frames)}};
    if (!nb::isinstance<nb::sequence>(frames) || nb::isinstance<nb::str>(frames)) throw BadParam(usage);
    json list = json::array();
    for (nb::handle frame : frames) {
        if (!nb::isinstance<nb::int_>(frame) || nb::isinstance<nb::bool_>(frame)) throw BadParam(usage);
        list.push_back(nb::cast<long long>(frame));
    }
    return {{"frames", list}};
}

// ── Isolated captures ────────────────────────────────────────────

// Selects nodes for one call, then gives the user's selection back.
struct SelectionSwap {
    INodeTab saved;
    bool swapped = false;
    SelectionSwap() = default;
    SelectionSwap(const SelectionSwap&) = delete;
    SelectionSwap& operator=(const SelectionSwap&) = delete;
    void Select(const std::vector<INode*>& nodes) {
        Interface* ip = GetCOREInterface();
        for (int i = 0; i < ip->GetSelNodeCount(); ++i) saved.AppendNode(ip->GetSelNode(i));
        INodeTab tab;
        for (INode* node : nodes) tab.AppendNode(node);
        swapped = true;
        ip->ClearNodeSelection(FALSE);
        ip->SelectNodeTab(tab, TRUE, FALSE);
    }
    ~SelectionSwap() {
        if (!swapped) return;
        GetCOREInterface()->ClearNodeSelection(FALSE);
        GetCOREInterface()->SelectNodeTab(saved, TRUE, FALSE);
    }
};

// The selection's top-level hierarchies grouped by shared base object, in the
// order native:isolate_and_capture_selected captures them (one image a group).
std::vector<std::vector<INode*>> IsolatedGroups() {
    Interface* ip = GetCOREInterface();
    INode* root = ip->GetRootNode();
    std::vector<INode*> tops;
    for (int i = 0; i < ip->GetSelNodeCount(); ++i) {
        INode* top = ip->GetSelNode(i);
        while (top->GetParentNode() && top->GetParentNode() != root) top = top->GetParentNode();
        if (std::find(tops.begin(), tops.end(), top) == tops.end()) tops.push_back(top);
    }
    std::vector<Object*> bases;
    std::vector<std::vector<INode*>> groups;
    for (INode* top : tops) {
        Object* base = top->GetObjectRef();
        while (base && base->SuperClassID() == GEN_DERIVOB_CLASS_ID) base = static_cast<IDerivedObject*>(base)->GetObjRef();
        const auto found = base ? std::find(bases.begin(), bases.end(), base) : bases.end();
        if (found != bases.end()) {
            groups[found - bases.begin()].push_back(top);
        } else {
            bases.push_back(base);
            groups.push_back({top});
        }
    }
    return groups;
}

// A fresh folder per call: the handler names the files after the nodes.
std::string IsolateFolder() {
    wchar_t temp[MAX_PATH] = {};
    if (!GetTempPathW(MAX_PATH, temp)) throw MaxError("could not locate the temporary folder");
    static unsigned long long counter = 0;
    const std::wstring folder = std::wstring(temp) + L"3dsmax-mcp\\identify\\mx_" + std::to_wstring(GetCurrentProcessId()) +
                                L"_" + std::to_wstring(GetTickCount64()) + L"_" + std::to_wstring(++counter);
    return WideToUtf8(folder.c_str());
}

std::pair<int, int> PngSize(const std::string& path) {
    std::ifstream file(std::filesystem::path(Utf8ToWide(path)), std::ios::binary);
    unsigned char head[24] = {};
    if (!file.read(reinterpret_cast<char*>(head), sizeof(head)) || std::memcmp(head + 12, "IHDR", 4) != 0)
        throw MaxError("the capture " + path + " is not a readable PNG");
    const auto word = [&](int at) { return int(head[at]) << 24 | int(head[at + 1]) << 16 | int(head[at + 2]) << 8 | int(head[at + 3]); };
    return {word(16), word(20)};
}

std::vector<Capture> CaptureIsolated(nb::handle nodes) {
    RequireMain();
    if (AgentOpen())
        throw MaxError("capture_isolated hides the rest of the scene, which is off while the AGENT VIEWPORT is open; "
                       "use agent_viewport().frame(node) and .capture()");
    SelectionSwap swap;
    if (!nodes.is_none()) swap.Select(NodeList(nodes, "capture_isolated"));
    const std::vector<std::vector<INode*>> groups = IsolatedGroups();
    if (groups.empty()) throw BadParam("nothing is selected; select nodes or pass them: capture_isolated(['Chair', 'Table'])");
    const json reply = NativeJson("native:isolate_and_capture_selected", {{"capture_dir", IsolateFolder()}});
    if (reply.type() != json::value_t::array || reply.size() != groups.size())
        throw MaxError("native:isolate_and_capture_selected returned " + std::to_string(reply.size()) + " images for " +
                       std::to_string(groups.size()) + " hierarchies");
    std::vector<Capture> out;
    for (size_t i = 0; i < groups.size(); ++i) {
        Capture c;
        c.path = reply[i].at("image_path").get<std::string>();
        std::tie(c.width, c.height) = PngSize(c.path);
        c.source = "active";
        for (INode* node : groups[i]) c.nodes.push_back(Wrap(node));
        out.push_back(c);
    }
    return out;
}

// ── Reprs ────────────────────────────────────────────────────────

std::string CaptureRepr(const Capture& c) {
    std::string out = "<Capture '" + c.path + "' " + std::to_string(c.width) + "x" + std::to_string(c.height) + " " + c.source;
    if (!c.frames.empty()) out += " frames " + ReprOf(nb::cast(c.frames));
    else if (nb::len(c.views)) out += " " + ReprOf(c.views);
    if (!c.nodes.empty()) out += " " + std::to_string(c.nodes.size()) + (c.nodes.size() == 1 ? " node" : " nodes");
    return out + ">";
}

std::string HitRepr(const Hit& h) {
    INode* node = HandlerHelpers::FindNodeByHandle(h.node.handle);
    const std::string name = InScene(node) ? "'" + NodeName(node) + "'" : "#" + std::to_string(h.node.handle);
    return "<Hit " + name + " at " + ReprOf(h.point) + ">";
}

}  // namespace

// ── Binding ──────────────────────────────────────────────────────

void BindViewport(nb::module_& m) {
    nb::class_<Capture>(m, "Capture", "A saved image: open(capture.path) or os.fspath(capture); width, height, and what it shows.")
        .def_ro("path", &Capture::path, "The image file: PNG, or JPEG from capture_screen.")
        .def_ro("width", &Capture::width, "Image width in pixels.")
        .def_ro("height", &Capture::height, "Image height in pixels.")
        .def_ro("source", &Capture::source, "What it shows: 'agent', 'active', or the capture_screen target.")
        .def_ro("views", &Capture::views, "The tiles' views in order: names or (x, y, z) directions; [] for one plain view.")
        .def_ro("frames", &Capture::frames, "The frames of a capture_frames strip, else [].")
        .def_ro("grid", &Capture::grid, "(columns, rows) of the tiles.")
        .def_ro("nodes", &Capture::nodes, "capture_isolated: the instances shown, the captured hierarchy's top node first.")
        .def("__fspath__", [](const Capture& c) { return c.path; })
        .def("__repr__", &CaptureRepr);

    nb::class_<Hit>(m, "Hit", "A pick on the evaluated surface (after modifiers, not the editable base): node, point, normal, distance.")
        .def_ro("node", &Hit::node, "The node hit.")
        .def_ro("point", &Hit::point, "World hit point (x, y, z).")
        .def_ro("normal", &Hit::normal, "World surface normal (x, y, z).")
        .def_ro("distance", &Hit::distance, "Distance from the eye along the ray.")
        .def("__repr__", &HitRepr);

    nb::class_<ViewRef>(m, "Viewport", "A viewport, resolved on every call: the user's active view, or the AGENT VIEWPORT.")
        .def("set", &SetView, "view"_a = "perspective", "eye"_a = nb::none(), "target"_a = nb::none(), "frame"_a = nb::none(),
             "fov"_a = 45.0, "padding"_a = 1.15, "shading"_a = "smooth", "edges"_a = false, "grid"_a = false,
             "Set the whole view, defaults included: view (perspective, orthographic, front, back, left, right, top, "
             "bottom), eye/target, frame (nodes to fit), fov, shading (smooth, wireframe, flat), edges, grid: "
             "set('top', frame='Table'), set(eye=(300, -400, 250), target=(0, 0, 50)).")
        .def("capture", [](const ViewRef& v, const std::string& method, int maxWidth, int maxHeight) {
            return v.agent ? CaptureAgent(v, nb::none(), method, maxWidth, maxHeight) : CaptureViewport(v, method, maxWidth, maxHeight);
        }, "method"_a = "auto", "max_width"_a = kMaxWidth, "max_height"_a = 0,
           "Save the view as it is to a PNG; method='window' grabs the window instead of the Nitrous buffer.")
        .def("capture_views", [](const ViewRef& v, nb::handle views, nb::handle subject, const std::optional<std::string>& shading,
                                 std::optional<bool> edges, const std::string& method, int maxWidth, int maxHeight) {
            json args = json::object();
            if (!views.is_none()) args["views"] = FromPython(views);
            return CaptureTiles(v, args, subject, shading, edges, method, maxWidth, maxHeight);
        }, "views"_a = nb::none(), "subject"_a = nb::none(), "shading"_a = nb::none(), "edges"_a = nb::none(),
           "method"_a = "auto", "max_width"_a = kMaxWidth, "max_height"_a = 0,
           "Up to 8 framed views in one grid (default front, right, back, top; also perspective, three_quarter, current, "
           "(x, y, z) eye directions); subject frames one hierarchy, shading smooth/flat/wireframe/clay for this capture; "
           "the view comes back after: capture_views(['front', 'three_quarter'], shading='clay', edges=True).")
        .def("capture_frames", [](const ViewRef& v, nb::handle frames, nb::handle view, nb::handle subject,
                                  const std::optional<std::string>& shading, std::optional<bool> edges, const std::string& method,
                                  int maxWidth, int maxHeight) {
            json args = StripFrames(frames);
            args["views"] = json::array();
            args["views"].push_back(FromPython(view));
            return CaptureTiles(v, args, subject, shading, edges, method, maxWidth, maxHeight);
        }, "frames"_a, "view"_a = "current", "subject"_a = nb::none(), "shading"_a = nb::none(), "edges"_a = nb::none(),
           "method"_a = "auto", "max_width"_a = kMaxWidth, "max_height"_a = 0,
           "One view over time in one strip: a frame count spread over the range or up to 12 frame numbers; a named view "
           "is framed over all of them; the time slider comes back: capture_frames(6), capture_frames([0, 10, 20], 'three_quarter').")
        .def_prop_ro("type", [](const ViewRef& v) { return InfoOf(v).type; },
            "perspective, orthographic, front, back, left, right, top, bottom, camera, light or other.")
        .def_prop_ro("eye", [](const ViewRef& v) { return FromPoint3(InfoOf(v).eye); }, "Eye position (x, y, z).")
        .def_prop_ro("target", [](const ViewRef& v) { return FromPoint3(InfoOf(v).target); },
            "The point the view looks at and orbits: (x, y, z).")
        .def_prop_ro("fov", [](const ViewRef& v) { return InfoOf(v).fov; },
            "Horizontal field of view in degrees; None for orthographic views.")
        .def_prop_ro("size", [](const ViewRef& v) {
            const ViewInfo info = InfoOf(v);
            return std::make_pair(info.width, info.height);
        }, "(width, height) in pixels.")
        .def_prop_ro("shading", [](const ViewRef& v) { return InfoOf(v).shading; },
            "Visual style: smooth, flat, wireframe, clay, realistic, facets, hidden_line, ...")
        .def_prop_ro("edges", [](const ViewRef& v) { return InfoOf(v).edges; }, "Whether edged faces show.")
        .def("__repr__", &ViewRepr)
        .def("__eq__", [](const ViewRef& a, nb::handle b) {
            return nb::isinstance<ViewRef>(b) && a.agent == nb::cast<ViewRef>(b).agent;
        }, nb::arg("other").none())
        .def("__hash__", [](const ViewRef& v) { return (Py_hash_t)(v.agent ? 2 : 1); });

    nb::class_<AgentViewRef, ViewRef>(m, "AgentViewport",
        "The agent's floating AGENT VIEWPORT: navigate, pick and capture without touching the user's views.")
        .def("capture", [](const AgentViewRef& v, nb::handle crop, const std::string& method, int maxWidth, int maxHeight) {
            return CaptureAgent(v, crop, method, maxWidth, maxHeight);
        }, "crop"_a = nb::none(), "method"_a = "auto", "max_width"_a = kMaxWidth, "max_height"_a = 0,
           "Save this view, or the VFB its V-Ray/Corona preview renders into, to an image; crop=(x, y, w, h) in VFB client pixels.")
        .def("frame", [](const AgentViewRef&, nb::handle nodes, double padding) {
            json args = {{"padding", padding}};
            if (!nodes.is_none()) args["frame_names"] = FramingNames(nodes);
            Agent("frame", args);
        }, "nodes"_a = nb::none(), "padding"_a = 1.15,
           "Fit nodes and their descendants (default: visible geometry, or the lights and cameras of a rig-only scene).")
        .def("orbit", [](const AgentViewRef&, double yaw, double pitch) { Agent("orbit", {{"yaw", yaw}, {"pitch", pitch}}); },
             "yaw"_a = 0.0, "pitch"_a = 0.0, "Orbit the target by yaw and pitch degrees: orbit(30, 10).")
        .def("pan", [](const AgentViewRef&, double x, double y) { Agent("pan", {{"x", x}, {"y", y}}); },
             "x"_a = 0.0, "y"_a = 0.0, "Move the view and its target by x, y scene units in the view plane.")
        .def("zoom", [](const AgentViewRef&, double factor) { Agent("zoom", {{"factor", factor}}); },
             "factor"_a, "Move toward the target (factor < 1) or away (> 1); orthographic views scale.")
        .def("pick", [](const AgentViewRef&, double x, double y, const std::optional<Capture>& capture) -> std::optional<Hit> {
            json args = ImagePoint(x, y);
            Expect(args, capture);
            const json hit = Agent("pick", args).value("hit", json());
            if (hit.is_null()) return std::nullopt;
            return Hit{Node{(AnimHandle)hit.at("handle").get<unsigned long long>()}, TupleOf(hit.at("point")),
                       TupleOf(hit.at("normal")), hit.value("distance", 0.0)};
        }, "x"_a, "y"_a, "capture"_a = nb::none(),
           "The nearest surface under image point x, y (0-1 from the top-left) as a Hit, or None; "
           "capture refuses a view that changed since: pick(0.5, 0.4, cap).")
        .def("ray", [](const AgentViewRef&, double x, double y, const std::optional<Capture>& capture) {
            json args = ImagePoint(x, y);
            Expect(args, capture);
            const json reply = Agent("ray", args);
            return nb::make_tuple(TupleOf(reply.at("origin")), TupleOf(reply.at("direction")));
        }, "x"_a, "y"_a, "capture"_a = nb::none(),
           "World ray (origin, direction) through image point x, y (0-1 from the top-left).")
        .def("project", [](const AgentViewRef&, nb::handle points, const std::optional<Capture>& capture) {
            if (!nb::isinstance<nb::list>(points) && !nb::isinstance<nb::tuple>(points))
                throw BadParam("points is a list of (x, y, z) world points");
            json args = {{"points", json::array()}};
            for (nb::handle point : points) args["points"].push_back(JsonOf(ToPoint3(point, "each point")));
            if (args["points"].empty() || args["points"].size() > 2000) throw BadParam("project takes 1 to 2000 points");
            Expect(args, capture);
            const json reply = Agent("project", args);
            const double w = std::max(1, reply.value("width", 1) - 1);
            const double h = std::max(1, reply.value("height", 1) - 1);
            nb::list out;
            for (const json& p : reply.at("projections")) {
                const json& pixel = p.at("pixel");
                if (pixel.is_null()) out.append(nb::none());
                else out.append(nb::make_tuple(pixel.at(0).get<double>() / w, pixel.at(1).get<double>() / h, p.at("depth").get<double>()));
            }
            return out;
        }, "points"_a, "capture"_a = nb::none(),
           "Image positions (x, y, depth) of world points, x and y 0-1 from the top-left as pick takes them; "
           "None behind the eye; no occlusion test: project([(0, 0, 50)], cap).")
        .def("render", [](const AgentViewRef&, const std::string& mode, const std::string& renderer) {
            if (renderer != "activeshade" && renderer != "production")
                throw BadParam("renderer must be 'activeshade' or 'production'");
            if (renderer == "production" && mode != "activeshade")
                throw BadParam("renderer='production' applies to mode='activeshade' only");
            Agent("render", {{"mode", mode}, {"renderer_source", renderer}});
        }, "mode"_a, "renderer"_a = "activeshade",
           "Interactive preview here: activeshade, vray_ipr, vray_vfb, corona_vfb, or shaded to stop; previews are "
           "progressive and block pick/ray/project until shaded: render('activeshade').")
        .def("minimize", [](const AgentViewRef&) { Agent("minimize"); }, "Park the window; restore() it before navigating or capturing.")
        .def("restore", [](const AgentViewRef&) { Agent("restore"); }, "Show the minimized window again and redraw it.")
        .def("release", [](const AgentViewRef&) { Agent("release"); },
             "Stop any preview, hide the window and give the panel back; agent_viewport() opens it again.")
        .def_prop_ro("window_state", [](const AgentViewRef&) {
            const json state = Agent("status");
            return state.value("owned", false) ? state.value("window_state", "unavailable") : std::string("closed");
        }, "visible, minimized, hidden, offscreen, unavailable, or closed once released.")
        .def_prop_ro("render_mode", [](const AgentViewRef&) {
            return AgentState().value("render", json::object()).value("mode", "shaded");
        }, "The preview: shaded, activeshade, vray_ipr, vray_vfb or corona_vfb.");

    m.def("viewport", [](const std::string& source) -> nb::object {
        const std::string which = Lower(source);
        if (which != "auto" && which != "agent" && which != "active")
            throw BadParam("source must be 'auto', 'agent' or 'active', not '" + source + "'");
        RequireMain();
        if (which == "active") return nb::cast(ViewRef{});
        const bool open = AgentOpen();
        if (which == "agent" && !open) throw NotFound("the AGENT VIEWPORT is not open; open it with agent_viewport()");
        return open ? nb::cast(AgentViewRef{}) : nb::cast(ViewRef{});
    }, "source"_a = "auto",
       "The AGENT VIEWPORT while it is open, else the user's active view; source='active' or 'agent' picks one: viewport().capture().");
    m.def("agent_viewport", [](int width, int height, bool minimized) {
        Agent("open", {{"width", width}, {"height", height}, {"start_minimized", minimized}});
        return AgentViewRef{};
    }, "width"_a = 1000, "height"_a = 740, "minimized"_a = false,
       "Open the agent's own floating viewport (or restore it) and return it; the size applies when it first opens: "
       "av = agent_viewport().");
    m.def("capture_screen", &CaptureScreen, "target"_a = "screen", "crop"_a = nb::none(), "max_width"_a = kMaxWidth,
          "max_height"_a = 0,
          "Save the primary monitor, or an open, uncovered frame buffer (vray_vfb, corona_vfb, fstorm_vfb), to a JPEG; "
          "crop=(x, y, w, h) in its pixels: capture_screen('vray_vfb').");
    m.def("capture_isolated", &CaptureIsolated, "nodes"_a = nb::none(),
          "Capture each top-level hierarchy of the selection (or of nodes) alone, framed in the active view; instances "
          "share one image, and visibility and selection come back.");
}

} // namespace MX_ABI_TAG
} // namespace mx
