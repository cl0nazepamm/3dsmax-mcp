#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/core_bridge.h"
#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/capture_region.h"
#include "mcp_bridge/window_capture.h"
#include "mcp_bridge/job_progress.h"
#include <cwctype>

#include <GraphicsWindow.h>
#include <Graphics/IViewportViewSetting.h>
#include <gdiplus.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <iomanip>
#include <map>
#include <memory>
#include <optional>
#include <sstream>
#include <unordered_set>
#include <utility>

#pragma comment(lib, "gdiplus.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;
namespace fs = std::filesystem;

std::string NativeHandlers::AgentViewportCommand(const std::string& params, MCPBridgeGUP* gup) {
    json p=json::parse(params);
    const std::string action=p.value("action","status");
    p["source"]=p.value("source","agent");
    if(action=="capture") return CaptureViewport(p.dump(),gup);
    if(action=="capture_multi") return CaptureMultiView(p.dump(),gup);
    return gup->GetExecutor().ExecuteSync([params]() {
        json p=json::parse(params);
        if(p.value("action","")=="set" && !McpHost::AgentViewport::IsRequested(p)) return json({{"handled",false}}).dump();
        return McpHost::AgentViewport::Execute(p).dump();
    });
}

// ── Helper: get PNG encoder CLSID ───────────────────────────
static int GetEncoderClsid(const WCHAR* format, CLSID* pClsid) {
    UINT num = 0, size = 0;
    if (Gdiplus::GetImageEncodersSize(&num, &size) != Gdiplus::Ok ||
        num == 0 || size == 0) {
        return -1;
    }

    auto* pImageCodecInfo = (Gdiplus::ImageCodecInfo*)(malloc(size));
    if (!pImageCodecInfo) return -1;

    if (Gdiplus::GetImageEncoders(num, size, pImageCodecInfo) != Gdiplus::Ok) {
        free(pImageCodecInfo);
        return -1;
    }
    for (UINT j = 0; j < num; ++j) {
        if (wcscmp(pImageCodecInfo[j].MimeType, format) == 0) {
            *pClsid = pImageCodecInfo[j].Clsid;
            free(pImageCodecInfo);
            return j;
        }
    }
    free(pImageCodecInfo);
    return -1;
}

// ── Helper: resize bitmap in-place ownership to fit bounds ───
static Gdiplus::Bitmap* ResizeBitmapToMax(Gdiplus::Bitmap* src, int maxWidth, int maxHeight) {
    if (!src) return nullptr;

    int srcW = (int)src->GetWidth();
    int srcH = (int)src->GetHeight();
    int outW = srcW;
    int outH = srcH;
    float scale = 1.0f;

    if (maxWidth > 0 && srcW > maxWidth) {
        float widthScale = (float)maxWidth / (float)srcW;
        if (widthScale < scale) scale = widthScale;
    }
    if (maxHeight > 0 && srcH > maxHeight) {
        float heightScale = (float)maxHeight / (float)srcH;
        if (heightScale < scale) scale = heightScale;
    }

    if (scale >= 1.0f) return src;

    outW = (int)((float)srcW * scale);
    outH = (int)((float)srcH * scale);
    if (outW < 1) outW = 1;
    if (outH < 1) outH = 1;

    Gdiplus::Bitmap* resized = new Gdiplus::Bitmap(outW, outH, PixelFormat24bppRGB);
    Gdiplus::Graphics g(resized);
    g.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);
    g.SetPixelOffsetMode(Gdiplus::PixelOffsetModeHighQuality);
    g.SetSmoothingMode(Gdiplus::SmoothingModeHighQuality);
    g.DrawImage(src, 0, 0, outW, outH);
    delete src;
    return resized;
}

// ── Helper: capture current viewport as GDI+ Bitmap ─────────
static Gdiplus::Bitmap* CaptureViewportDIB(ViewExp* vp) {
    GraphicsWindow* gw = vp->getGW();
    if (!gw) return nullptr;

    int size = 0;
    if (!gw->getDIB(nullptr, &size) || size <= 0)
        return nullptr;

    BITMAPINFO* bmi = (BITMAPINFO*)malloc(size);
    if (!bmi) return nullptr;

    if (!gw->getDIB(bmi, &size)) {
        free(bmi);
        return nullptr;
    }

    int w = bmi->bmiHeader.biWidth;
    int h = abs(bmi->bmiHeader.biHeight);

    // Create GDI+ bitmap from DIB
    Gdiplus::Bitmap* bmp = new Gdiplus::Bitmap(
        (INT)w, (INT)h, PixelFormat24bppRGB);

    // Copy pixel data
    BYTE* srcPixels = (BYTE*)bmi + bmi->bmiHeader.biSize +
                      bmi->bmiHeader.biClrUsed * sizeof(RGBQUAD);
    int srcStride = ((w * bmi->bmiHeader.biBitCount / 8) + 3) & ~3;
    int srcBpp = bmi->bmiHeader.biBitCount / 8;
    bool bottomUp = bmi->bmiHeader.biHeight > 0;

    Gdiplus::BitmapData data;
    Gdiplus::Rect rect(0, 0, w, h);
    bmp->LockBits(&rect, Gdiplus::ImageLockModeWrite, PixelFormat24bppRGB, &data);

    for (int y = 0; y < h; y++) {
        int srcY = bottomUp ? (h - 1 - y) : y;
        BYTE* srcRow = srcPixels + srcY * srcStride;
        BYTE* dstRow = (BYTE*)data.Scan0 + y * data.Stride;
        for (int x = 0; x < w; x++) {
            // BMP is BGR, GDI+ PixelFormat24bppRGB is also BGR
            dstRow[x * 3 + 0] = srcRow[x * srcBpp + 0];
            dstRow[x * 3 + 1] = srcRow[x * srcBpp + 1];
            dstRow[x * 3 + 2] = srcRow[x * srcBpp + 2];
        }
    }
    bmp->UnlockBits(&data);
    free(bmi);
    return bmp;
}

static WindowCapture::Result CaptureViewportImage(ViewExp& view,const std::string& method) {
    if(method!="auto" && method!="window") throw std::runtime_error("capture_method must be auto or window");
    if(method=="auto") {
        std::unique_ptr<Gdiplus::Bitmap> dib(CaptureViewportDIB(&view));
        if(dib && dib->GetLastStatus()==Gdiplus::Ok && dib->GetWidth()>0 && dib->GetHeight()>0)
            return {std::move(dib),"nitrous_dib"};
    }
    auto* gw=view.getGW();
    if(!gw) throw std::runtime_error("Viewport graphics window is unavailable");
    const int width=gw->getWinSizeX(),height=gw->getWinSizeY();
    auto image=WindowCapture::Client(view.GetHWnd());
    if(image.bitmap->GetWidth()!=width || image.bitmap->GetHeight()!=height)
        throw std::runtime_error("Window/viewport pixel dimensions disagree; targeting would be inaccurate");
    return image;
}

// ── Helper: draw label on a GDI+ Graphics context ───────────
static void DrawLabel(Gdiplus::Graphics& g, const wchar_t* text,
                      int x, int y, int quadW, int quadH) {
    Gdiplus::FontFamily fontFamily(L"Arial");
    Gdiplus::Font font(&fontFamily, 28, Gdiplus::FontStyleBold, Gdiplus::UnitPixel);
    Gdiplus::SolidBrush bgBrush(Gdiplus::Color(180, 0, 0, 0));
    Gdiplus::SolidBrush textBrush(Gdiplus::Color(255, 255, 255, 255));

    int barHeight = 40;
    Gdiplus::RectF layoutRect((Gdiplus::REAL)x, (Gdiplus::REAL)y,
                               (Gdiplus::REAL)quadW, (Gdiplus::REAL)barHeight);
    Gdiplus::StringFormat format;
    format.SetAlignment(Gdiplus::StringAlignmentCenter);
    format.SetLineAlignment(Gdiplus::StringAlignmentCenter);

    // Background bar
    g.FillRectangle(&bgBrush, x, y, quadW, barHeight);
    // Text
    g.DrawString(text, -1, &font, layoutRect, &format, &textBrush);
}

namespace {

void CollectNodeAndDescendants(INode* node, std::vector<INode*>& nodes) {
    if (node == nullptr) return;
    nodes.push_back(node);
    for (int i = 0; i < node->NumberOfChildren(); ++i) {
        CollectNodeAndDescendants(node->GetChildNode(i), nodes);
    }
}

bool IsFiniteBox(const Box3& box) {
    if (box.IsEmpty()) return false;
    const Point3 minPoint = box.Min();
    const Point3 maxPoint = box.Max();
    return std::isfinite(minPoint.x) && std::isfinite(minPoint.y) &&
           std::isfinite(minPoint.z) && std::isfinite(maxPoint.x) &&
           std::isfinite(maxPoint.y) && std::isfinite(maxPoint.z);
}

bool IsFramingContent(INode* node, TimeValue time) {
    if (node == nullptr || node->Renderable() == 0 ||
        node->IsNodeHidden(FALSE) != 0) {
        return false;
    }

    const ObjectState objectState = node->EvalWorldState(time);
    Object* object = objectState.obj;
    if (object == nullptr || object->IsRenderable() == 0) {
        return false;
    }

    const SClass_ID superClass = object->SuperClassID();
    return superClass == GEOMOBJECT_CLASS_ID ||
           superClass == SHAPE_CLASS_ID;
}

Box3 RenderableSubtreeWorldBounds(
    const std::vector<INode*>& nodes,
    TimeValue time) {
    Box3 bounds;
    bounds.Init();
    for (INode* node : nodes) {
        if (!IsFramingContent(node, time)) continue;
        const Box3 nodeBounds = SpatialSnapshot::WorldBoundingBox(node, time);
        if (nodeBounds.IsEmpty()) continue;
        bounds += nodeBounds.Min();
        bounds += nodeBounds.Max();
    }
    return bounds;
}

// ── capture_multi_view requests ─────────────────────────────

constexpr int kMaxStripFrames = 12;

std::string LowerAscii(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return text;
}

// World-to-view TM of a camera at `eye` facing away from `back`, the unit
// vector from what it looks at toward the eye. World Z stays up except when
// looking straight down or up, where +Y is up, as in Max's Top view.
Matrix3 LookBack(const Point3& eye, const Point3& back) {
    Point3 up(0, 0, 1);
    if (std::abs(DotProd(back, up)) > 0.999f) up = Point3(0, 1, 0);
    const Point3 right = Normalize(CrossProd(up, back));
    return Inverse(Matrix3(right, CrossProd(back, right), back, eye));
}

// One tile's viewpoint: a named view, a direction from the framed centre
// toward the eye (always perspective), or the view as it already is.
struct TileView {
    enum class Kind { Named, Direction, Current };
    Kind kind = Kind::Named;
    std::string name;     // "front"... or "three_quarter", "direction", "current"
    std::wstring label;
    bool persp = false;   // Named only
    Matrix3 tm;           // Named orthographic views
    Point3 direction;     // Direction, normalized
    json reported;        // as the request named it, for "views" in the result
};

std::map<std::string, TileView> NamedViews() {
    // Camera-to-world bases inverted for SetAffineTM. Passing the camera basis
    // directly flipped elevations upside down.
    const auto named = [](const char* name, const wchar_t* label, bool persp, Matrix3 tm) {
        TileView view;
        view.name = name;
        view.label = label;
        view.persp = persp;
        view.tm = tm;
        view.reported = name;
        return view;
    };
    return {
        {"front", named("front", L"FRONT", false, Inverse(Matrix3(Point3(1,0,0), Point3(0,0,1), Point3(0,-1,0), Point3(0,-100,0))))},
        {"back", named("back", L"BACK", false, Inverse(Matrix3(Point3(-1,0,0), Point3(0,0,1), Point3(0,1,0), Point3(0,100,0))))},
        {"left", named("left", L"LEFT", false, Inverse(Matrix3(Point3(0,-1,0), Point3(0,0,1), Point3(-1,0,0), Point3(-100,0,0))))},
        {"right", named("right", L"RIGHT", false, Inverse(Matrix3(Point3(0,1,0), Point3(0,0,1), Point3(1,0,0), Point3(100,0,0))))},
        {"top", named("top", L"TOP", false, Inverse(Matrix3(Point3(1,0,0), Point3(0,1,0), Point3(0,0,1), Point3(0,0,100))))},
        {"bottom", named("bottom", L"BOTTOM", false, Inverse(Matrix3(Point3(1,0,0), Point3(0,-1,0), Point3(0,0,-1), Point3(0,0,-100))))},
        {"perspective", named("perspective", L"PERSP", true, Matrix3())},
    };
}

TileView DirectionView(const std::string& name, const std::wstring& label, const Point3& direction, json reported) {
    TileView view;
    view.kind = TileView::Kind::Direction;
    view.name = name;
    view.label = label;
    view.direction = Normalize(direction);
    view.reported = std::move(reported);
    return view;
}

TileView ParseTileView(const json& entry, const std::map<std::string, TileView>& named) {
    if (entry.type() == json::value_t::string) {
        const std::string requested = entry.get<std::string>();
        const std::string lower = LowerAscii(requested);
        if (lower == "current") {
            TileView view;
            view.kind = TileView::Kind::Current;
            view.name = "current";
            view.label = L"CURRENT";
            view.reported = "current";
            return view;
        }
        // Front-right, above: the same corner as Max's default perspective.
        if (lower == "three_quarter") return DirectionView("three_quarter", L"3/4", Point3(1.0f, -1.0f, 0.7f), "three_quarter");
        const auto found = named.find(lower);
        if (found != named.end()) return found->second;
        throw std::runtime_error("Unknown view '" + requested + "'. Use front, back, left, right, top, bottom, "
                                 "perspective, three_quarter, current or an [x, y, z] direction");
    }
    if (entry.type() == json::value_t::array && entry.size() == 3 &&
        std::all_of(entry.begin(), entry.end(), [](const json& v) { return v.is_number(); })) {
        const Point3 direction(entry[0].get<float>(), entry[1].get<float>(), entry[2].get<float>());
        if (!std::isfinite(direction.x) || !std::isfinite(direction.y) || !std::isfinite(direction.z) ||
            Length(direction) < 1e-6f)
            throw std::runtime_error("A view direction is three finite numbers, not all zero");
        std::wostringstream label;
        label << std::setprecision(3) << L"(" << direction.x << L", " << direction.y << L", " << direction.z << L")";
        return DirectionView("direction", label.str(), direction, entry);
    }
    throw std::runtime_error("Each view is a view name or an [x, y, z] direction from the subject toward the eye");
}

// A shading override for the capture only; unset fields leave the view alone.
struct ShadingOverride {
    std::optional<MaxSDK::Graphics::VisualStyle> style;
    std::string name;
    std::optional<bool> edges;
    bool Any() const { return style.has_value() || edges.has_value(); }
};

ShadingOverride ParseShading(const json& p) {
    ShadingOverride shading;
    if (p.contains("shading") && !p.at("shading").is_null()) {
        if (p.at("shading").type() != json::value_t::string) throw std::runtime_error("shading must be a string");
        shading.name = LowerAscii(p.at("shading").get<std::string>());
        if (shading.name == "smooth") shading.style = MaxSDK::Graphics::VisualStyleShaded;
        else if (shading.name == "flat") shading.style = MaxSDK::Graphics::VisualStyleConsistentColor;
        else if (shading.name == "wireframe") shading.style = MaxSDK::Graphics::VisualStyleWireframe;
        else if (shading.name == "clay") shading.style = MaxSDK::Graphics::VisualStyleClay;
        else throw std::runtime_error("shading must be smooth, flat, wireframe or clay");
    }
    if (p.contains("edges") && !p.at("edges").is_null()) {
        if (!p.at("edges").is_boolean()) throw std::runtime_error("edges must be true or false");
        shading.edges = p.at("edges").get<bool>();
    }
    return shading;
}

MaxSDK::Graphics::IViewportViewSetting* ViewSettings(ViewExp& view) {
    return static_cast<MaxSDK::Graphics::IViewportViewSetting*>(view.GetInterface(IVIEWPORT_SETTINGS_INTERFACE_ID));
}

void ApplyShading(ViewExp& view, const ShadingOverride& shading) {
    if (!shading.Any()) return;
    auto* settings = ViewSettings(view);
    if (!settings) throw std::runtime_error("This viewport has no Nitrous view settings, so shading cannot be overridden");
    if (shading.style) settings->SetViewportVisualStyle(*shading.style);
    if (shading.edges) settings->SetShowEdgedFaces(*shading.edges);
}

// Frames for a strip: explicit, or frame_count spread over the animation
// range. Ascending and unique, so a simulation steps forward once.
std::vector<int> ParseStripFrames(const json& p, Interface* ip) {
    const bool explicitFrames = p.contains("frames") && !p.at("frames").is_null();
    const bool counted = p.contains("frame_count") && !p.at("frame_count").is_null();
    if (explicitFrames && counted) throw std::runtime_error("Pass frames or frame_count, not both");
    std::vector<int> frames;
    if (explicitFrames) {
        const json& list = p.at("frames");
        if (list.type() != json::value_t::array || list.empty() || list.size() > kMaxStripFrames)
            throw std::runtime_error("frames must list 1 to 12 frame numbers");
        for (const auto& frame : list) {
            if (!frame.is_number_integer()) throw std::runtime_error("frames must be whole frame numbers");
            const long long value = frame.get<long long>();
            if (value < -1000000 || value > 1000000) throw std::runtime_error("frames must lie within +/-1000000");
            frames.push_back(static_cast<int>(value));
        }
    } else if (counted) {
        if (!p.at("frame_count").is_number_integer()) throw std::runtime_error("frame_count must be a whole number");
        const int count = p.at("frame_count").get<int>();
        if (count < 2 || count > kMaxStripFrames) throw std::runtime_error("frame_count must be 2 to 12");
        const Interval range = ip->GetAnimRange();
        const int start = range.Start() / GetTicksPerFrame(), end = range.End() / GetTicksPerFrame();
        for (int i = 0; i < count; ++i)
            frames.push_back(start + static_cast<int>(std::lround(double(end - start) * i / (count - 1))));
    }
    std::sort(frames.begin(), frames.end());
    frames.erase(std::unique(frames.begin(), frames.end()), frames.end());
    return frames;
}

// The agent panel's framing (agent_viewport.cpp Frame), for a box the panel
// cannot measure itself: what a strip's subject covers over its frames.
void FrameView(ViewExp& view, const Box3& box, float padding) {
    Matrix3 tm;
    view.GetAffineTM(tm);
    Matrix3 camera = Inverse(tm);
    const Point3 center = box.Center();
    float halfWidth = 0, halfHeight = 0, distance = 1;
    auto* gw = view.getGW();
    const float aspect = static_cast<float>(gw->getWinSizeX()) / std::max(1, gw->getWinSizeY());
    const float tanH = std::tan(view.GetFOV() * 0.5f);
    for (int i = 0; i < 8; ++i) {
        const Point3 q = box[i] - center;
        const float x = DotProd(q, camera.GetRow(0)) * padding;
        const float y = DotProd(q, camera.GetRow(1)) * padding;
        const float z = DotProd(q, camera.GetRow(2));
        halfWidth = std::max(halfWidth, std::abs(x));
        halfHeight = std::max(halfHeight, std::abs(y));
        distance = std::max(distance, std::max(std::abs(x) / tanH, std::abs(y) * aspect / tanH) + z);
    }
    if (!view.IsPerspView()) distance = std::max(1.f, Length(box.Width()) * 2);
    camera.SetTrans(center + camera.GetRow(2) * distance);
    view.SetAffineTM(Inverse(camera));
    auto* extended = reinterpret_cast<ViewExp10*>(view.Execute(ViewExp::kEXECUTE_GET_VIEWEXP_10));
    view.SetFocalDist(distance);
    if (extended != nullptr) extended->SetFocalDistance(distance);
    if (!view.IsPerspView() && extended != nullptr) {
        const float wanted = std::max(0.001f, 2 * std::max(halfWidth, halfHeight * aspect));
        const float current = view.GetVPWorldWidth(center);
        if (current > 0) extended->Zoom(wanted / current);
        view.SetAffineTM(Inverse(camera));
    }
}

// A tile's camera for agent_views. The panel's full state, with the
// view_token that still matches it, comes once in restored_agent_viewport;
// a tile's own token is stale as soon as the capture restores the view.
// An orthographic view's FOV means nothing, so it is left out.
json TileCamera(const json& state) {
    json camera = json::object();
    const bool perspective = state.value("perspective", true);
    camera["perspective"] = perspective;
    for (const char* key : {"eye", "target"})
        if (state.contains(key)) camera[key] = state.at(key);
    if (perspective && state.contains("fov")) camera["fov"] = state.at("fov");
    return camera;
}

// Puts the time slider back when a strip ends, however it ends.
class TimeGuard {
public:
    explicit TimeGuard(Interface* ip) : ip_(ip), original_(ip->GetTime()) {}
    TimeGuard(const TimeGuard&) = delete;
    TimeGuard& operator=(const TimeGuard&) = delete;
    ~TimeGuard() { Restore(); }
    void Set(TimeValue time) {
        changed_ = true;
        ip_->SetTime(time, FALSE);
    }
    void Restore() noexcept {
        if (!changed_) return;
        changed_ = false;
        try { ip_->SetTime(original_, FALSE); } catch (...) {}
    }
    TimeValue Original() const { return original_; }

private:
    Interface* ip_;
    TimeValue original_;
    bool changed_ = false;
};

class MultiViewStateGuard {
public:
    MultiViewStateGuard(
        Interface* interfacePtr,
        ViewExp& viewport,
        const std::vector<INode*>& sceneNodes)
        : interface_(interfacePtr),
          viewport_(&viewport),
          viewType_(viewport.GetViewType()),
          wasPerspective_(viewport.IsPerspView()),
          viewCamera_(viewport.GetViewCamera()),
          viewSpot_(viewport.GetViewSpot()),
          focalDistance_(viewport.GetFocalDist()),
          fieldOfView_(viewport.GetFOV()),
          orthographicWidth_(viewport.GetVPWorldWidth(Point3::Origin)) {
        viewport.GetAffineTM(affineTransform_);
        viewport10_ = reinterpret_cast<ViewExp10*>(
            viewport.Execute(ViewExp::kEXECUTE_GET_VIEWEXP_10));

        hiddenStates_.reserve(sceneNodes.size());
        for (INode* node : sceneNodes) {
            if (node != nullptr) {
                hiddenStates_.emplace_back(node, node->IsHidden() != 0);
            }
        }

        const int selectionCount = interface_->GetSelNodeCount();
        selectedNodes_.reserve(static_cast<size_t>(selectionCount));
        for (int i = 0; i < selectionCount; ++i) {
            if (INode* node = interface_->GetSelNode(i)) {
                selectedNodes_.push_back(node);
            }
        }
    }

    MultiViewStateGuard(const MultiViewStateGuard&) = delete;
    MultiViewStateGuard& operator=(const MultiViewStateGuard&) = delete;

    ~MultiViewStateGuard() {
        Restore();
    }

    void PrepareFramedCapture(
        const std::unordered_set<INode*>& framedNodes) {
        sceneStateMutated_ = true;
        interface_->ClearNodeSelection(FALSE);
        for (const auto& state : hiddenStates_) {
            if (framedNodes.find(state.first) == framedNodes.end()) {
                state.first->Hide(TRUE);
            }
        }
    }

    // A "current" tile after other tiles: the view as the capture found it,
    // keeping this capture's shading override.
    void ShowOriginalView(const ShadingOverride& shading) {
        RestoreViewport();
        if (shadingSaved_) ApplyShading(*viewport_, shading);
    }

    // The user's visual style and edged faces are restored with the view.
    void OverrideShading(const ShadingOverride& shading) {
        if (!shading.Any()) return;
        auto* settings = ViewSettings(*viewport_);
        if (!settings) throw std::runtime_error("This viewport has no Nitrous view settings, so shading cannot be overridden");
        if (!shadingSaved_) {
            savedStyle_ = settings->GetViewportVisualStyle();
            savedEdges_ = settings->GetShowEdgedFaces();
            shadingSaved_ = true;
        }
        ApplyShading(*viewport_, shading);
    }

    void Restore() noexcept {
        if (restored_) return;
        restored_ = true;

        if (sceneStateMutated_) {
            for (const auto& state : hiddenStates_) {
                try {
                    state.first->Hide(state.second ? TRUE : FALSE);
                } catch (...) {
                }
            }

            try {
                interface_->ClearNodeSelection(FALSE);
                for (INode* node : selectedNodes_) {
                    interface_->SelectNode(node, FALSE);
                }
            } catch (...) {
            }
        }

        try {
            RestoreViewport();
        } catch (...) {
        }

        try {
            interface_->RedrawViews(interface_->GetTime());
        } catch (...) {
        }
    }

private:
    void RestoreViewport() {
        if (viewport_ == nullptr || !viewport_->IsAlive()) return;

        if (shadingSaved_) {
            if (auto* settings = ViewSettings(*viewport_)) {
                settings->SetViewportVisualStyle(savedStyle_);
                settings->SetShowEdgedFaces(savedEdges_);
            }
        }

        if (viewType_ == VIEW_CAMERA && viewCamera_ != nullptr) {
            viewport_->SetViewCamera(viewCamera_);
            return;
        }
        if (viewType_ == VIEW_SPOT && viewSpot_ != nullptr) {
            viewport_->SetViewSpot(viewSpot_);
            return;
        }

        viewport_->SetViewUser(wasPerspective_);
        // Restore the view mode first: changing it can reset zoom and pan.
        const char* namedView = nullptr;
        switch (viewType_) {
            case VIEW_FRONT: namedView = "view_front"; break;
            case VIEW_BACK: namedView = "view_back"; break;
            case VIEW_LEFT: namedView = "view_left"; break;
            case VIEW_RIGHT: namedView = "view_right"; break;
            case VIEW_TOP: namedView = "view_top"; break;
            case VIEW_BOTTOM: namedView = "view_bottom"; break;
            default: break;
        }
        if (namedView != nullptr) {
            RunMAXScript(std::string("viewport.setType #") + namedView);
        }
        viewport_->SetFocalDist(focalDistance_);
        if (viewport10_ != nullptr) {
            viewport10_->SetFocalDistance(focalDistance_);
            if (wasPerspective_) viewport10_->SetFOV(fieldOfView_);
        }
        viewport_->SetAffineTM(affineTransform_);
        if (!wasPerspective_ && viewport10_ != nullptr) {
            // Ortho zoom is independent of focal distance/FOV. SetFOV cannot
            // restore it (its reported pseudo-FOV can even exceed 180 degrees).
            const float currentWidth = viewport_->GetVPWorldWidth(Point3::Origin);
            if (std::isfinite(currentWidth) && currentWidth > 0.0f &&
                std::isfinite(orthographicWidth_) && orthographicWidth_ > 0.0f) {
                viewport10_->Zoom(orthographicWidth_ / currentWidth);
                viewport_->SetAffineTM(affineTransform_);
            }
        }
    }

    Interface* interface_ = nullptr;
    ViewExp* viewport_ = nullptr;
    ViewExp10* viewport10_ = nullptr;
    std::vector<INode*> selectedNodes_;
    std::vector<std::pair<INode*, bool>> hiddenStates_;
    Matrix3 affineTransform_;
    int viewType_ = VIEW_NONE;
    BOOL wasPerspective_ = FALSE;
    INode* viewCamera_ = nullptr;
    INode* viewSpot_ = nullptr;
    float focalDistance_ = 0.0f;
    float fieldOfView_ = 0.0f;
    float orthographicWidth_ = 0.0f;
    bool sceneStateMutated_ = false;
    bool restored_ = false;
    bool shadingSaved_ = false;
    MaxSDK::Graphics::VisualStyle savedStyle_ = MaxSDK::Graphics::VisualStyleShaded;
    bool savedEdges_ = false;
};

fs::path ReserveUniqueMultiViewPath() {
    wchar_t tempPath[MAX_PATH]{};
    const DWORD tempPathLength = GetTempPathW(MAX_PATH, tempPath);
    if (tempPathLength == 0 || tempPathLength >= MAX_PATH) {
        throw std::runtime_error("Failed to resolve the Windows temp directory");
    }

    static std::atomic<unsigned long long> sequence{0};
    for (int attempt = 0; attempt < 64; ++attempt) {
        const unsigned long long suffix = ++sequence;
        const std::wstring filename =
            L"3dsmax_mcp_multiview_" +
            std::to_wstring(GetCurrentProcessId()) + L"_" +
            std::to_wstring(GetTickCount64()) + L"_" +
            std::to_wstring(suffix) + L".png";
        const fs::path candidate = fs::path(tempPath) / filename;

        HANDLE file = CreateFileW(
            candidate.c_str(),
            GENERIC_WRITE,
            FILE_SHARE_READ,
            nullptr,
            CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY,
            nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            CloseHandle(file);
            return candidate;
        }
        if (GetLastError() != ERROR_FILE_EXISTS &&
            GetLastError() != ERROR_ALREADY_EXISTS) {
            throw std::runtime_error(
                "Failed to reserve a unique multi-view output file");
        }
    }
    throw std::runtime_error("Failed to reserve a unique multi-view filename");
}

class OutputFileGuard {
public:
    explicit OutputFileGuard(fs::path path) : path_(std::move(path)) {}
    OutputFileGuard(const OutputFileGuard&) = delete;
    OutputFileGuard& operator=(const OutputFileGuard&) = delete;
    ~OutputFileGuard() {
        if (!keep_) {
            std::error_code ignored;
            fs::remove(path_, ignored);
        }
    }
    const fs::path& Path() const { return path_; }
    void Keep() { keep_ = true; }

private:
    fs::path path_;
    bool keep_ = false;
};

}  // namespace

// ── native:capture_multi_view ───────────────────────────────
std::string NativeHandlers::CaptureMultiViewMainThread(const std::string& params) {
    if (!McpHost::GdiPlusRuntime::EnsureStarted())
        throw std::runtime_error("GDI+ initialization failed");

    json p = json::parse(params, nullptr, false);
    if (p.is_discarded() || !p.is_object()) {
        throw std::runtime_error("Invalid JSON payload");
    }
    const auto captureMethod=p.value("capture_method","auto");
    if(captureMethod!="auto" && captureMethod!="window") throw std::runtime_error("capture_method must be auto or window");
    int maxWidth = p.value("max_width", 1600);
    int maxHeight = p.value("max_height", 0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture dimensions must be between 0 and 8192");

    Interface* ip = GetCOREInterface();
    TimeValue t = ip->GetTime();

    // A frame strip shows one view at several frames; otherwise each tile is
    // a view at the current frame (default: front, right, back, top).
    const std::vector<int> stripFrames = ParseStripFrames(p, ip);
    const bool strip = !stripFrames.empty();
    const bool viewsGiven = p.contains("views") && !p.at("views").is_null();
    json requestedViews = viewsGiven ? p.at("views")
        : strip ? json::array({"current"}) : json::array({"front", "right", "back", "top"});
    if (requestedViews.type() == json::value_t::string) requestedViews = json::array({requestedViews});
    if (requestedViews.type() != json::value_t::array || requestedViews.empty() || requestedViews.size() > 8)
        throw std::runtime_error("Request between 1 and 8 views");
    if (strip && requestedViews.size() != 1)
        throw std::runtime_error("A frame strip uses one view: pass a single entry in views, or none for the current view");
    const auto namedViews = NamedViews();
    std::vector<TileView> views;
    json actualViewNames = json::array();
    for (const auto& entry : requestedViews) {
        views.push_back(ParseTileView(entry, namedViews));
        actualViewNames.push_back(views.back().reported);
    }
    const ShadingOverride shading = ParseShading(p);
    const std::string progressKey = p.value("progress_key", "");
    if (progressKey.size() > 128) throw std::runtime_error("progress_key is limited to 128 characters");

    const bool agent=McpHost::AgentViewport::IsRequested(p);
    ViewExp& vp = agent ? McpHost::AgentViewport::Get() : ip->GetActiveViewExp();
    Matrix3 savedTM;
    vp.GetAffineTM(savedTM);

    std::vector<INode*> sceneNodes;
    CollectNodes(ip->GetRootNode(), sceneNodes);

    INode* framedRoot = nullptr;
    std::string framedRootName;
    Box3 framedBounds;
    framedBounds.Init();
    std::vector<INode*> framedNodes;

    if (p.contains("frame_root")) {
        const auto frameRootIt = p.find("frame_root");
        if (frameRootIt->type() != json::value_t::string) {
            throw std::runtime_error("frame_root must be a string");
        }
        const std::string requestedRoot = frameRootIt->get<std::string>();
        if (requestedRoot.empty()) {
            throw std::runtime_error("frame_root must not be empty");
        }

        const std::vector<INode*> matches =
            CollectNodesByName(requestedRoot);
        if (matches.empty()) {
            throw std::runtime_error(
                "frame_root not found: " + requestedRoot);
        }
        if (matches.size() != 1) {
            throw std::runtime_error(
                "frame_root must resolve to exactly one node: " +
                requestedRoot + " matched " +
                std::to_string(matches.size()));
        }

        framedRoot = matches.front();
        framedRootName = WideToUtf8(framedRoot->GetName());
        CollectNodeAndDescendants(framedRoot, framedNodes);
        framedBounds = RenderableSubtreeWorldBounds(framedNodes, t);
        if (!IsFiniteBox(framedBounds)) {
            throw std::runtime_error(
                "frame_root subtree has no visible renderable geometry "
                "or shape bounds: " +
                requestedRoot);
        }
    }

    // A strip's subject can move, so an aimed strip frames everything it
    // covers over the strip's frames, not just where it is now. Bounds are
    // evaluated at those times without moving the time slider; "current"
    // keeps the view as it is and skips this pass.
    Box3 stripBounds;
    stripBounds.Init();
    const bool stripFramed = strip && views.front().kind != TileView::Kind::Current;
    if (stripFramed) {
        const std::vector<INode*>& subject = framedRoot != nullptr ? framedNodes : sceneNodes;
        for (const int frame : stripFrames) {
            const Box3 part = RenderableSubtreeWorldBounds(subject, frame * GetTicksPerFrame());
            if (part.IsEmpty()) continue;
            stripBounds += part.Min();
            stripBounds += part.Max();
        }
        if (!IsFiniteBox(stripBounds))
            throw std::runtime_error("Nothing visible and renderable to frame over the strip's frames");
    }

    std::unique_ptr<MultiViewStateGuard> stateGuard;
    std::unique_ptr<McpHost::AgentViewport::CameraState> agentState;
    if(agent) agentState=std::make_unique<McpHost::AgentViewport::CameraState>(McpHost::AgentViewport::SaveCamera());
    else stateGuard=std::make_unique<MultiViewStateGuard>(ip,vp,sceneNodes);
    struct RestoreAgent {
        std::unique_ptr<McpHost::AgentViewport::CameraState>& state;
        ~RestoreAgent() { if(state) try { McpHost::AgentViewport::RestoreCamera(*state); } catch(...) {} }
    } restoreAgent{agentState};
    if (framedRoot != nullptr && !agent) {
        const std::unordered_set<INode*> framedNodeSet(
            framedNodes.begin(), framedNodes.end());
        stateGuard->PrepareFramedCapture(framedNodeSet);
    }
    // After the view guards, so it unwinds first and they redraw at the
    // user's frame.
    TimeGuard timeGuard(ip);

    const int tileCount = static_cast<int>(strip ? stripFrames.size() : views.size());
    struct ProgressReport {
        const std::string& key;
        bool finished = false;
        void Set(json state) const {
            if (!key.empty()) JobProgress::Set(key, std::move(state));
        }
        ~ProgressReport() {
            if (!finished) Set({{"phase", "failed"}});
        }
    } progress{progressKey};
    progress.Set({{"phase", "capturing"}, {"done", 0}, {"total", tileCount}});

    // Aims the view for a tile. The agent panel takes the shading after its
    // configure, which resets it; the user's view takes it once, below.
    // "current" is the view as the capture found it, whatever came before.
    const auto aim = [&](const TileView& view) {
        if (agent) {
            if (view.kind == TileView::Kind::Current) {
                McpHost::AgentViewport::RestoreCamera(*agentState);
            } else {
                json config={{"view",view.kind==TileView::Kind::Named ? view.name : std::string("perspective")},
                             {"frame_all",true},{"fov",45}};
                if (view.kind == TileView::Kind::Direction) {
                    // Framing keeps this direction and moves the eye to fit.
                    config["eye"]={view.direction.x*1000.0f, view.direction.y*1000.0f, view.direction.z*1000.0f};
                    config["target"]={0.0f, 0.0f, 0.0f};
                }
                if(framedRoot) config["frame_names"]={framedRootName};
                McpHost::AgentViewport::Configure(config);
                if (stripFramed) FrameView(McpHost::AgentViewport::Get(), stripBounds, 1.15f);
            }
            if (shading.Any() || stripFramed) {
                ApplyShading(McpHost::AgentViewport::Get(), shading);
                McpHost::AgentViewport::Redraw();
            }
            return;
        }
        if (view.kind == TileView::Kind::Current) {
            stateGuard->ShowOriginalView(shading);
            return;
        }
        // Set viewport orientation via SDK (no MAXScript needed)
        if (view.kind == TileView::Kind::Direction) {
            vp.SetViewUser(TRUE);
            vp.SetAffineTM(LookBack(view.direction * 1000.0f, view.direction));
        } else if (!view.persp) {
            vp.SetViewUser(FALSE);       // orthographic
            vp.SetAffineTM(view.tm);
        } else {
            // Restore the original orientation regardless of tile order.
            // Previously this tile could repeat the preceding orthographic view.
            vp.SetViewUser(TRUE);
            vp.SetAffineTM(savedTM);
        }

        if (stripFramed) {
            ip->ZoomToBounds(FALSE, stripBounds);
        } else if (framedRoot != nullptr) {
            ip->ZoomToBounds(FALSE, framedBounds);
        } else {
            ip->ViewportZoomExtents(FALSE, FALSE);
        }
    };

    // Capture each tile
    std::vector<std::unique_ptr<Gdiplus::Bitmap>> captures;
    std::vector<std::wstring> labels;
    json agentViews=json::array();
    json captureMethods=json::array();
    int vpWidth = 0, vpHeight = 0;

    if (stateGuard) stateGuard->OverrideShading(shading);
    // A strip aims once and holds that camera, so motion reads across the tiles.
    if (strip) aim(views.front());
    for (int i = 0; i < tileCount; ++i) {
        const TileView& view = strip ? views.front() : views[static_cast<size_t>(i)];
        if (strip) {
            timeGuard.Set(stripFrames[i] * GetTicksPerFrame());
            if (agent) McpHost::AgentViewport::Redraw();
            else ip->ForceCompleteRedraw(FALSE);
            labels.push_back(L"FRAME " + std::to_wstring(stripFrames[i]));
        } else {
            aim(view);
            if (!agent) ip->ForceCompleteRedraw(FALSE);
            labels.push_back(view.label);
        }

        // Capture
        const json viewState = agent ? McpHost::AgentViewport::Snapshot() : json(nullptr);
        if (agent && (!strip || agentViews.empty())) agentViews.push_back(TileCamera(viewState));
        ViewExp& activeVP = agent ? McpHost::AgentViewport::Get() : ip->GetActiveViewExp();
        auto captured=CaptureViewportImage(activeVP,captureMethod);
        if(agent && McpHost::AgentViewport::Snapshot().at("view_token")!=viewState.at("view_token"))
            throw std::runtime_error("STALE_VIEW: agent view changed during capture");
        captureMethods.push_back(captured.method);
        std::unique_ptr<Gdiplus::Bitmap> bmp(std::move(captured.bitmap));
        if (!bmp || bmp->GetLastStatus() != Gdiplus::Ok ||
            bmp->GetWidth() == 0 || bmp->GetHeight() == 0) {
            throw std::runtime_error("Failed to capture viewport for: " +
                (strip ? "frame " + std::to_string(stripFrames[i]) : view.name));
        }

        if (vpWidth == 0) {
            vpWidth = static_cast<int>(bmp->GetWidth());
            vpHeight = static_cast<int>(bmp->GetHeight());
        }

        captures.push_back(std::move(bmp));
        json state = {{"phase", "capturing"}, {"done", i + 1}, {"total", tileCount}};
        if (strip) state["frame"] = stripFrames[i];
        progress.Set(std::move(state));
    }

    // Keep the state mutation window short. The guards remain armed for
    // every exceptional exit and Restore is idempotent.
    timeGuard.Restore();
    if(stateGuard) stateGuard->Restore();
    if(agentState) { McpHost::AgentViewport::RestoreCamera(*agentState); agentState.reset(); }

    // Determine grid layout
    int cols, rows;
    int n = (int)captures.size();
    if (n <= 1) { cols = 1; rows = 1; }
    else if (n <= 2) { cols = 2; rows = 1; }
    else if (n <= 4) { cols = 2; rows = 2; }
    else if (n <= 6) { cols = 3; rows = 2; }
    else if (n <= 9) { cols = 3; rows = (n + cols - 1) / cols; }
    else { cols = 4; rows = (n + cols - 1) / cols; }

    // Create stitched bitmap
    int stitchW = cols * vpWidth;
    int stitchH = rows * vpHeight;
    std::unique_ptr<Gdiplus::Bitmap> stitched(
        new Gdiplus::Bitmap(stitchW, stitchH, PixelFormat24bppRGB));
    if (stitched->GetLastStatus() != Gdiplus::Ok) {
        throw std::runtime_error("Failed to allocate stitched multi-view bitmap");
    }
    {
        Gdiplus::Graphics g(stitched.get());
        g.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);

        // Fill with black
        Gdiplus::SolidBrush black(Gdiplus::Color(0, 0, 0));
        g.FillRectangle(&black, 0, 0, stitchW, stitchH);

        // Draw each capture into grid
        for (int i = 0; i < n; i++) {
            int col = i % cols;
            int row = i / cols;
            int x = col * vpWidth;
            int y = row * vpHeight;
            g.DrawImage(captures[i].get(), x, y, vpWidth, vpHeight);
            DrawLabel(g, labels[i].c_str(), x, y, vpWidth, vpHeight);
        }
    }

    CLSID pngClsid{};
    if (GetEncoderClsid(L"image/png", &pngClsid) < 0) {
        throw std::runtime_error("PNG encoder is unavailable");
    }
    std::unique_ptr<Gdiplus::Bitmap> outBmp(
        ResizeBitmapToMax(stitched.release(), maxWidth, maxHeight));
    if (!outBmp || outBmp->GetLastStatus() != Gdiplus::Ok ||
        outBmp->GetWidth() == 0 || outBmp->GetHeight() == 0) {
        throw std::runtime_error("Invalid resized multi-view bitmap");
    }
    const int finalW = static_cast<int>(outBmp->GetWidth());
    const int finalH = static_cast<int>(outBmp->GetHeight());

    OutputFileGuard outputFile(ReserveUniqueMultiViewPath());
    std::error_code reservationError;
    if (!fs::remove(outputFile.Path(), reservationError) ||
        reservationError) {
        throw std::runtime_error(
            "Failed to prepare the reserved multi-view output file");
    }
    const Gdiplus::Status saveStatus = outBmp->Save(
        outputFile.Path().c_str(), &pngClsid, nullptr);
    if (saveStatus != Gdiplus::Ok) {
        throw std::runtime_error(
            "Failed to save multi-view PNG (GDI+ status " +
            std::to_string(static_cast<int>(saveStatus)) + ")");
    }

    std::error_code fileError;
    const std::uintmax_t outputSize =
        fs::file_size(outputFile.Path(), fileError);
    if (fileError || outputSize == 0) {
        throw std::runtime_error("Saved multi-view PNG is empty or unreadable");
    }

    Gdiplus::Bitmap validation(outputFile.Path().c_str());
    if (validation.GetLastStatus() != Gdiplus::Ok ||
        validation.GetWidth() != outBmp->GetWidth() ||
        validation.GetHeight() != outBmp->GetHeight()) {
        throw std::runtime_error("Saved multi-view PNG failed validation");
    }

    // Return path
    json result;
    result["file"] = WideToUtf8(outputFile.Path().c_str());
    result["width"] = finalW;
    result["height"] = finalH;
    result["source_width"] = stitchW;
    result["source_height"] = stitchH;
    result["size_bytes"] = outputSize;
    result["views"] = actualViewNames;
    result["source"]=agent ? "agent" : "active";
    result["capture_contract"]="viewport_capture_v2";
    result["occlusion_free"]=true;
    result["capture_methods"]=captureMethods;
    if(agent) {
        result["agent_views"]=agentViews;
        result["restored_agent_viewport"]=McpHost::AgentViewport::Snapshot();
    }
    result["isolation_applied"]=framedRoot!=nullptr && !agent;
    result["framed_root"] =
        framedRoot != nullptr ? json(framedRootName) : json(nullptr);
    if (strip) {
        result["frames"] = stripFrames;
        result["restored_frame"] = timeGuard.Original() / GetTicksPerFrame();
        result["framed_over_frames"] = stripFramed;
    }
    result["shading"] = shading.style ? json(shading.name) : json(nullptr);
    if (shading.edges) result["edges"] = *shading.edges;
    result["grid"] = std::to_string(cols) + "x" + std::to_string(rows);
    result["message"] = "Captured " + std::to_string(n) + (strip ? " frames (" : " views (") +
                       std::to_string(cols) + "x" + std::to_string(rows) + " grid)";
    const std::string serializedResult = result.dump();
    outputFile.Keep();
    progress.finished = true;
    progress.Set({{"phase", "finished"}, {"done", n}, {"total", n}});
    return serializedResult;
}

std::string NativeHandlers::CaptureMultiView(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([params]() { return CaptureMultiViewMainThread(params); });
}

// ── Helper: save GDI+ Bitmap to temp PNG and return path ────
static std::string SaveBitmapToTemp(Gdiplus::Bitmap* bmp, const wchar_t* filename) {
    wchar_t tempPath[MAX_PATH];
    GetTempPathW(MAX_PATH, tempPath);
    std::wstring outPath = std::wstring(tempPath) + filename;

    CLSID pngClsid;
    GetEncoderClsid(L"image/png", &pngClsid);
    bmp->Save(outPath.c_str(), &pngClsid, nullptr);
    return WideToUtf8(outPath.c_str());
}

// ── native:capture_viewport ─────────────────────────────────
std::string NativeHandlers::CaptureViewportMainThread(const std::string& params) {
    if (!McpHost::GdiPlusRuntime::EnsureStarted())
        throw std::runtime_error("GDI+ initialization failed");

    json p = json::parse(params, nullptr, false);
    if(p.is_discarded() || !p.is_object()) throw std::runtime_error("Invalid JSON payload");
    const auto captureMethod=p.value("capture_method","auto");
    if(captureMethod!="auto" && captureMethod!="window") throw std::runtime_error("capture_method must be auto or window");
    int maxWidth = p.value("max_width", 1600);
    int maxHeight = p.value("max_height", 0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture dimensions must be between 0 and 8192");

    Interface* ip = GetCOREInterface();
    const bool agent=McpHost::AgentViewport::IsRequested(p);
    if(agent && p.contains("expected_view")) {
        auto state=McpHost::AgentViewport::Snapshot();
        if(p.at("expected_view")!=state.at("view_token")) throw std::runtime_error("STALE_VIEW: capture again before targeting");
    }
    if(agent) McpHost::AgentViewport::Redraw(); else ip->ForceCompleteRedraw(FALSE);

    ViewExp& vp = agent ? McpHost::AgentViewport::Get() : ip->GetActiveViewExp();
    const auto captureView=agent ? McpHost::AgentViewport::Snapshot().at("view_token") : json(nullptr);
    auto captured=CaptureViewportImage(vp,captureMethod);
    if(agent && McpHost::AgentViewport::Snapshot().at("view_token")!=captureView)
        throw std::runtime_error("STALE_VIEW: agent view changed during capture");
    Gdiplus::Bitmap* bmp = captured.bitmap.release();
    if (!bmp) throw std::runtime_error("Failed to capture viewport DIB");

    int sourceW = (int)bmp->GetWidth();
    int sourceH = (int)bmp->GetHeight();
    std::unique_ptr<Gdiplus::Bitmap> outBmp(ResizeBitmapToMax(bmp, maxWidth, maxHeight));
    if(agent) {
        Gdiplus::Graphics graphics(outBmp.get());
        DrawLabel(graphics,L"AGENT VIEWPORT",0,0,static_cast<int>(outBmp->GetWidth()),static_cast<int>(outBmp->GetHeight()));
        if(p.contains("labels")) {
            const auto& labels=p.at("labels");
            if(labels.type()!=json::value_t::array || labels.size()>100) throw std::runtime_error("At most 100 labels allowed");
            json points=json::array();
            for(const auto& item:labels) points.push_back(item.at("point"));
            auto projected=McpHost::AgentViewport::Project(points);
            Gdiplus::FontFamily family(L"Arial");
            Gdiplus::Font font(&family,16,Gdiplus::FontStyleBold,Gdiplus::UnitPixel);
            Gdiplus::SolidBrush ink(Gdiplus::Color(255,255,225,65));
            Gdiplus::Pen marker(Gdiplus::Color(255,50,220,255),2);
            for(size_t i=0;i<labels.size();++i) {
                if(projected[i].is_null()) continue;
                auto text=Utf8ToWide(labels[i].at("text").get<std::string>());
                if(text.size()>64) throw std::runtime_error("Label text too long");
                float x=projected[i][0].get<float>()*outBmp->GetWidth()/sourceW;
                float y=projected[i][1].get<float>()*outBmp->GetHeight()/sourceH;
                if(x<0 || y<0 || x>=outBmp->GetWidth() || y>=outBmp->GetHeight()) continue;
                graphics.DrawEllipse(&marker,x-4,y-4,8.f,8.f);
                graphics.DrawString(text.c_str(),-1,&font,Gdiplus::PointF(x+6,y-18),&ink);
            }
        }
    }
    OutputFileGuard outputFile(ReserveUniqueMultiViewPath());
    std::error_code fileError;
    if(!fs::remove(outputFile.Path(),fileError) || fileError) throw std::runtime_error("Cannot prepare capture output");
    CLSID encoder{};
    if(GetEncoderClsid(L"image/png",&encoder)<0) throw std::runtime_error("PNG encoder unavailable");
    if(outBmp->Save(outputFile.Path().c_str(),&encoder,nullptr)!=Gdiplus::Ok)
        throw std::runtime_error("Cannot save viewport PNG");
    auto size=fs::file_size(outputFile.Path(),fileError);
    if(fileError || !size) throw std::runtime_error("Viewport PNG is empty");
    std::string path=WideToUtf8(outputFile.Path().c_str());
    int w = (int)outBmp->GetWidth();
    int h = (int)outBmp->GetHeight();

    json result;
    result["file"] = path;
    result["size_bytes"]=size;
    result["width"] = w;
    result["height"] = h;
    result["source_width"] = sourceW;
    result["source_height"] = sourceH;
    result["source"]=agent ? "agent" : "active";
    result["capture_contract"]="viewport_capture_v2";
    result["capture_method"]=captured.method;
    result["occlusion_free"]=true;
    if(agent) result["agent_viewport"]=McpHost::AgentViewport::Snapshot();
    auto serialized=result.dump();
    outputFile.Keep();
    return serialized;
}

std::string NativeHandlers::CaptureViewport(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([params]() { return CaptureViewportMainThread(params); });
}

// Window/desktop capture bypasses Max's scene executor. WindowCapture uses a
// compositor frame off-thread, so a blocked renderer cannot stall cancellation
// waiting for a synchronous PrintWindow call into Max's UI thread.
namespace {
struct CaptureDpiScope {
    using SetDpi=DPI_AWARENESS_CONTEXT (WINAPI*)(DPI_AWARENESS_CONTEXT);
    SetDpi set=reinterpret_cast<SetDpi>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetThreadDpiAwarenessContext"));
    DPI_AWARENESS_CONTEXT previous=set ? set(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) : nullptr;
    CaptureDpiScope() { if(!previous) throw std::runtime_error("Physical-pixel capture DPI context unavailable"); }
    ~CaptureDpiScope() { if(previous) set(previous); }
};
struct VfbSearch { std::string target; std::vector<HWND> windows; };
BOOL CALLBACK FindVfbWindow(HWND window, LPARAM data) {
    DWORD process=0;
    GetWindowThreadProcessId(window,&process);
    if(process!=GetCurrentProcessId() || !IsWindowVisible(window) || IsIconic(window)) return TRUE;
    wchar_t caption[1024]{};
    GetWindowTextW(window,caption,1024);
    wchar_t windowClass[256]{};
    GetClassNameW(window,windowClass,256);
    auto& search=*reinterpret_cast<VfbSearch*>(data);
    if(CaptureRegion::MatchesVfb(search.target,caption,windowClass)) search.windows.push_back(window);
    return TRUE;
}
CaptureRegion::Rect VfbClientRect(HWND window) {
    DWORD process=0; GetWindowThreadProcessId(window,&process);
    if(!IsWindow(window) || process!=GetCurrentProcessId() || !IsWindowVisible(window) || IsIconic(window))
        throw std::runtime_error("Frame buffer became unavailable");
    RECT client{}; POINT origin{};
    if(!GetClientRect(window,&client) || !ClientToScreen(window,&origin))
        throw std::runtime_error("Could not locate frame buffer client area");
    return {origin.x,origin.y,client.right-client.left,client.bottom-client.top};
}
struct ScreenPixels {
    HDC screen=GetDC(nullptr), memory=nullptr;
    HBITMAP bitmap=nullptr;
    HGDIOBJ previous=nullptr;
    ~ScreenPixels() {
        if(previous && memory) SelectObject(memory,previous);
        if(bitmap) DeleteObject(bitmap);
        if(memory) DeleteDC(memory);
        if(screen) ReleaseDC(nullptr,screen);
    }
    void Capture(const CaptureRegion::Rect& r) {
        if(!screen || !(memory=CreateCompatibleDC(screen)) || !(bitmap=CreateCompatibleBitmap(screen,r.width,r.height)))
            throw std::runtime_error("Could not allocate screen capture");
        previous=SelectObject(memory,bitmap);
        if(!previous || previous==HGDI_ERROR || !BitBlt(memory,0,0,r.width,r.height,screen,r.x,r.y,SRCCOPY|CAPTUREBLT))
            throw std::runtime_error("Desktop pixel capture failed");
    }
};
}

std::string NativeHandlers::CaptureScreen(const std::string& params, MCPBridgeGUP* /*gup*/) {
    if(!McpHost::GdiPlusRuntime::EnsureStarted()) throw std::runtime_error("GDI+ initialization failed");
    const json p=json::parse(params);
    if(!p.is_object()) throw std::runtime_error("Expected object payload");
    const std::string target=p.value("target","screen");
    if(target!="screen" && target!="vray_vfb" && target!="corona_vfb" && target!="fstorm_vfb") throw std::runtime_error("target must be screen, vray_vfb, corona_vfb or fstorm_vfb");
    const int maxWidth=p.value("max_width",1600), maxHeight=p.value("max_height",0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture maximum dimensions must be between 0 and 8192");
    CaptureDpiScope dpi;
    HWND window=nullptr;
    CaptureRegion::Rect targetRect{0,0,GetSystemMetrics(SM_CXSCREEN),GetSystemMetrics(SM_CYSCREEN)};
    json windowInfo=nullptr;
    if(target!="screen") {
        VfbSearch search{target,{}};
        EnumWindows(FindVfbWindow,reinterpret_cast<LPARAM>(&search));
        if(search.windows.empty()) throw std::runtime_error("No visible "+target+" in this Max instance; open or restore its VFB first");
        if(search.windows.size()!=1) throw std::runtime_error("AMBIGUOUS: multiple visible "+target+" windows in this Max instance");
        window=search.windows.front(); targetRect=VfbClientRect(window);
        wchar_t caption[1024]{}; GetWindowTextW(window,caption,1024);
        windowInfo={{"handle",std::to_string(reinterpret_cast<uintptr_t>(window))},{"title",WideToUtf8(caption)},
            {"process_id",GetCurrentProcessId()},{"area","client"}};
    }
    const auto region=CaptureRegion::Crop(targetRect,p.value("crop",json(nullptr)));
    RECT desktop{GetSystemMetrics(SM_XVIRTUALSCREEN),GetSystemMetrics(SM_YVIRTUALSCREEN),0,0};
    desktop.right=desktop.left+GetSystemMetrics(SM_CXVIRTUALSCREEN);
    desktop.bottom=desktop.top+GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if(!window && (region.x<desktop.left || region.y<desktop.top || region.x+region.width>desktop.right || region.y+region.height>desktop.bottom))
        throw std::runtime_error("Capture region is partly offscreen; move the VFB fully onto the desktop");
    if(static_cast<uint64_t>(region.width)*region.height>134217728)
        throw std::runtime_error("Capture region is too large");
    std::unique_ptr<ScreenPixels> desktopPixels;
    std::unique_ptr<Gdiplus::Bitmap> bitmap;
    const char* captureMethod="desktop_bitblt";
    if(window) {
        auto captured=WindowCapture::Client(window);
        captureMethod=captured.method;
        bitmap.reset(captured.bitmap->Clone(region.x-targetRect.x,region.y-targetRect.y,
            region.width,region.height,PixelFormat24bppRGB));
    } else {
        desktopPixels=std::make_unique<ScreenPixels>();
        desktopPixels->Capture(region);
        bitmap.reset(Gdiplus::Bitmap::FromHBITMAP(desktopPixels->bitmap,nullptr));
    }
    // Reject a moved/resized/replaced window rather than return a crop of the
    // wrong region when the user changes the layout during capture.
    if(window && CaptureRegion::Json(VfbClientRect(window))!=CaptureRegion::Json(targetRect))
        throw std::runtime_error("Frame buffer moved during capture; retry");
    if(!bitmap || bitmap->GetLastStatus()!=Gdiplus::Ok) throw std::runtime_error("Could not read captured pixels");
    bitmap.reset(ResizeBitmapToMax(bitmap.release(),maxWidth,maxHeight));
    if(!bitmap || bitmap->GetLastStatus()!=Gdiplus::Ok) throw std::runtime_error("Could not resize captured pixels");
    wchar_t temp[MAX_PATH]{};
    if(!GetTempPathW(MAX_PATH,temp)) throw std::runtime_error("Could not locate temporary directory");
    static std::atomic<unsigned long long> sequence{0};
    const std::wstring path=std::wstring(temp)+L"3dsmax_screen_"+std::to_wstring(GetCurrentProcessId())+L"_"+
        std::to_wstring(GetTickCount64())+L"_"+std::to_wstring(++sequence)+L".jpg";
    CLSID encoder;
    if(GetEncoderClsid(L"image/jpeg",&encoder)<0) throw std::runtime_error("JPEG encoder unavailable");
    Gdiplus::EncoderParameters options{};
    ULONG quality=90;
    options.Count=1; options.Parameter[0].Guid=Gdiplus::EncoderQuality;
    options.Parameter[0].Type=Gdiplus::EncoderParameterValueTypeLong;
    options.Parameter[0].NumberOfValues=1; options.Parameter[0].Value=&quality;
    if(bitmap->Save(path.c_str(),&encoder,&options)!=Gdiplus::Ok) throw std::runtime_error("Could not save screen capture");
    return json({{"file",WideToUtf8(path.c_str())},{"width",bitmap->GetWidth()},{"height",bitmap->GetHeight()},
        {"capture_contract",window ? "window_capture_v1" : "desktop_crop_v1"},{"target",target},{"window",windowInfo},
        {"screen_rect",CaptureRegion::Json(region)},{"target_rect",CaptureRegion::Json(targetRect)},
        {"capture_method",captureMethod},{"occlusion_free",window!=nullptr},
        {"visible_pixels_only",window==nullptr},{"occlusion_checked",false}}).dump();
}
