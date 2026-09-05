#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/gdiplus_runtime.h"
#include "mcp_bridge/spatial_snapshot.h"
#include "mcp_bridge/agent_viewport.h"
#include "mcp_bridge/capture_region.h"
#include <cwctype>

#include <GraphicsWindow.h>
#include <gdiplus.h>

#include <atomic>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <memory>
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
        if(p.value("action","")=="set" && !AgentViewport::IsRequested(p)) return json({{"handled",false}}).dump();
        return AgentViewport::Execute(p).dump();
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
    if (!GdiPlusRuntime::EnsureStarted())
        throw std::runtime_error("GDI+ initialization failed");

    json p = json::parse(params, nullptr, false);
    if (p.is_discarded() || !p.is_object()) {
        throw std::runtime_error("Invalid JSON payload");
    }
    int maxWidth = p.value("max_width", 1600);
    int maxHeight = p.value("max_height", 0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture dimensions must be between 0 and 8192");

    // Optional: custom views (default: front, right, back, top)
    auto viewNames = p.value("views", std::vector<std::string>{
        "front", "right", "back", "top"
    });
    if(viewNames.empty() || viewNames.size()>8) throw std::runtime_error("Request between 1 and 8 views");

    Interface* ip = GetCOREInterface();
    TimeValue t = ip->GetTime();

    const bool agent=AgentViewport::IsRequested(p);
    ViewExp& vp = agent ? AgentViewport::Get() : ip->GetActiveViewExp();
    Matrix3 savedTM;
    vp.GetAffineTM(savedTM);

    // View definitions with affine transforms (inverse camera matrix)
    // These set the viewport to look at the origin from each direction
    struct ViewDef {
        std::string name;
        std::wstring label;
        bool persp;      // true = perspective, false = orthographic
        Matrix3 tm;      // affine TM for ViewExp::SetAffineTM
    };

    // Define camera-to-world bases, then invert them for SetAffineTM.
    // Passing the camera basis directly flipped elevations upside down.
    Matrix3 frontTM = Inverse(Matrix3(Point3(1,0,0), Point3(0,0,1), Point3(0,-1,0), Point3(0,-100,0)));
    Matrix3 backTM = Inverse(Matrix3(Point3(-1,0,0), Point3(0,0,1), Point3(0,1,0), Point3(0,100,0)));
    Matrix3 leftTM = Inverse(Matrix3(Point3(0,-1,0), Point3(0,0,1), Point3(-1,0,0), Point3(-100,0,0)));
    Matrix3 rightTM = Inverse(Matrix3(Point3(0,1,0), Point3(0,0,1), Point3(1,0,0), Point3(100,0,0)));
    Matrix3 topTM = Inverse(Matrix3(Point3(1,0,0), Point3(0,1,0), Point3(0,0,1), Point3(0,0,100)));
    Matrix3 bottomTM = Inverse(Matrix3(Point3(1,0,0), Point3(0,-1,0), Point3(0,0,-1), Point3(0,0,-100)));

    std::map<std::string, ViewDef> viewMap = {
        {"front",       {"front",       L"FRONT",   false, frontTM}},
        {"back",        {"back",        L"BACK",    false, backTM}},
        {"left",        {"left",        L"LEFT",    false, leftTM}},
        {"right",       {"right",       L"RIGHT",   false, rightTM}},
        {"top",         {"top",         L"TOP",     false, topTM}},
        {"bottom",      {"bottom",      L"BOTTOM",  false, bottomTM}},
        {"perspective", {"perspective", L"PERSP",   true,  Matrix3()}},
    };

    // Collect views to capture
    std::vector<ViewDef> views;
    std::vector<std::string> actualViewNames;
    for (const auto& vn : viewNames) {
        std::string lower = vn;
        std::transform(
            lower.begin(),
            lower.end(),
            lower.begin(),
            [](unsigned char ch) {
                return static_cast<char>(std::tolower(ch));
            });
        auto it = viewMap.find(lower);
        if (it != viewMap.end()) {
            views.push_back(it->second);
            actualViewNames.push_back(it->second.name);
        }
    }
    if (views.empty()) {
        throw std::runtime_error("No valid views specified");
    }

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
            CollectNodesByExactName(requestedRoot);
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

    std::unique_ptr<MultiViewStateGuard> stateGuard;
    std::unique_ptr<AgentViewport::CameraState> agentState;
    if(agent) agentState=std::make_unique<AgentViewport::CameraState>(AgentViewport::SaveCamera());
    else stateGuard=std::make_unique<MultiViewStateGuard>(ip,vp,sceneNodes);
    struct RestoreAgent {
        std::unique_ptr<AgentViewport::CameraState>& state;
        ~RestoreAgent() { if(state) try { AgentViewport::RestoreCamera(*state); } catch(...) {} }
    } restoreAgent{agentState};
    if (framedRoot != nullptr && !agent) {
        const std::unordered_set<INode*> framedNodeSet(
            framedNodes.begin(), framedNodes.end());
        stateGuard->PrepareFramedCapture(framedNodeSet);
    }

    // Capture each view
    std::vector<std::unique_ptr<Gdiplus::Bitmap>> captures;
    json agentViews=json::array();
    int vpWidth = 0, vpHeight = 0;

    for (auto& view : views) {
        if(agent) {
            json config={{"view",view.name},{"frame_all",true},{"fov",45}};
            if(framedRoot) config["frame_names"]={framedRootName};
            AgentViewport::Configure(config);
            agentViews.push_back(AgentViewport::Snapshot());
        } else {
        // Set viewport orientation via SDK (no MAXScript needed)
        if (!view.persp) {
            vp.SetViewUser(FALSE);       // orthographic
            vp.SetAffineTM(view.tm);
        } else {
            // Restore the original orientation regardless of tile order.
            // Previously this tile could repeat the preceding orthographic view.
            vp.SetViewUser(TRUE);
            vp.SetAffineTM(savedTM);
        }

        if (framedRoot != nullptr) {
            ip->ZoomToBounds(FALSE, framedBounds);
        } else {
            ip->ViewportZoomExtents(FALSE, FALSE);
        }

        // Force complete redraw
        ip->ForceCompleteRedraw(FALSE);
        }

        // Capture
        ViewExp& activeVP = agent ? AgentViewport::Get() : ip->GetActiveViewExp();
        std::unique_ptr<Gdiplus::Bitmap> bmp(
            CaptureViewportDIB(&activeVP));
        if (!bmp || bmp->GetLastStatus() != Gdiplus::Ok ||
            bmp->GetWidth() == 0 || bmp->GetHeight() == 0) {
            throw std::runtime_error("Failed to capture viewport for: " + view.name);
        }

        if (vpWidth == 0) {
            vpWidth = static_cast<int>(bmp->GetWidth());
            vpHeight = static_cast<int>(bmp->GetHeight());
        }

        captures.push_back(std::move(bmp));
    }

    // Keep the state mutation window short. The guard remains armed for
    // every exceptional exit and Restore is idempotent.
    if(stateGuard) stateGuard->Restore();
    if(agentState) { AgentViewport::RestoreCamera(*agentState); agentState.reset(); }

    // Determine grid layout
    int cols, rows;
    int n = (int)captures.size();
    if (n <= 1) { cols = 1; rows = 1; }
    else if (n <= 2) { cols = 2; rows = 1; }
    else if (n <= 4) { cols = 2; rows = 2; }
    else if (n <= 6) { cols = 3; rows = 2; }
    else { cols = 3; rows = (n + cols - 1) / cols; }

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
            DrawLabel(g, views[i].label.c_str(), x, y, vpWidth, vpHeight);
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
    if(agent) {
        result["agent_views"]=agentViews;
        result["restored_agent_viewport"]=AgentViewport::Snapshot();
    }
    result["isolation_applied"]=framedRoot!=nullptr && !agent;
    result["framed_root"] =
        framedRoot != nullptr ? json(framedRootName) : json(nullptr);
    result["grid"] = std::to_string(cols) + "x" + std::to_string(rows);
    result["message"] = "Captured " + std::to_string(n) + " views (" +
                       std::to_string(cols) + "x" + std::to_string(rows) + " grid)";
    const std::string serializedResult = result.dump();
    outputFile.Keep();
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
    if (!GdiPlusRuntime::EnsureStarted())
        throw std::runtime_error("GDI+ initialization failed");

    json p = json::parse(params, nullptr, false);
    if(p.is_discarded() || !p.is_object()) throw std::runtime_error("Invalid JSON payload");
    int maxWidth = p.value("max_width", 1600);
    int maxHeight = p.value("max_height", 0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture dimensions must be between 0 and 8192");

    Interface* ip = GetCOREInterface();
    const bool agent=AgentViewport::IsRequested(p);
    if(agent && p.contains("expected_view")) {
        auto state=AgentViewport::Snapshot();
        if(p.at("expected_view")!=state.at("view_token")) throw std::runtime_error("STALE_VIEW: capture again before targeting");
    }
    if(agent) AgentViewport::Redraw(); else ip->ForceCompleteRedraw(FALSE);

    ViewExp& vp = agent ? AgentViewport::Get() : ip->GetActiveViewExp();
    Gdiplus::Bitmap* bmp = CaptureViewportDIB(&vp);
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
            auto projected=AgentViewport::Project(points);
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
    if(agent) result["agent_viewport"]=AgentViewport::Snapshot();
    auto serialized=result.dump();
    outputFile.Keep();
    return serialized;
}

std::string NativeHandlers::CaptureViewport(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([params]() { return CaptureViewportMainThread(params); });
}

// Screen pixels deliberately bypass Max's scene/main-thread executor. A VFB
// crop is not an offscreen render-buffer export: occluding windows remain visible.
namespace {
struct CaptureDpiScope {
    using SetDpi=DPI_AWARENESS_CONTEXT (WINAPI*)(DPI_AWARENESS_CONTEXT);
    SetDpi set=reinterpret_cast<SetDpi>(GetProcAddress(GetModuleHandleW(L"user32.dll"),"SetThreadDpiAwarenessContext"));
    DPI_AWARENESS_CONTEXT previous=set ? set(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) : nullptr;
    CaptureDpiScope() { if(!previous) throw std::runtime_error("Physical-pixel capture DPI context unavailable"); }
    ~CaptureDpiScope() { if(previous) set(previous); }
};
BOOL CALLBACK FindVfbWindow(HWND window, LPARAM data) {
    DWORD process=0;
    GetWindowThreadProcessId(window,&process);
    if(process!=GetCurrentProcessId() || !IsWindowVisible(window) || IsIconic(window)) return TRUE;
    wchar_t caption[1024]{};
    GetWindowTextW(window,caption,1024);
    std::wstring title(caption);
    std::transform(title.begin(),title.end(),title.begin(),[](wchar_t c) { return std::towlower(c); });
    if(title.find(L"v-ray frame buffer")!=std::wstring::npos || title.find(L"v-ray virtual frame buffer")!=std::wstring::npos)
        reinterpret_cast<std::vector<HWND>*>(data)->push_back(window);
    return TRUE;
}
CaptureRegion::Rect VfbClientRect(HWND window) {
    DWORD process=0; GetWindowThreadProcessId(window,&process);
    if(!IsWindow(window) || process!=GetCurrentProcessId() || !IsWindowVisible(window) || IsIconic(window))
        throw std::runtime_error("V-Ray frame buffer became unavailable");
    RECT client{}; POINT origin{};
    if(!GetClientRect(window,&client) || !ClientToScreen(window,&origin))
        throw std::runtime_error("Could not locate V-Ray frame buffer client area");
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
    if(!GdiPlusRuntime::EnsureStarted()) throw std::runtime_error("GDI+ initialization failed");
    const json p=json::parse(params);
    if(!p.is_object()) throw std::runtime_error("Expected object payload");
    const std::string target=p.value("target","screen");
    if(target!="screen" && target!="vray_vfb") throw std::runtime_error("target must be screen or vray_vfb");
    const int maxWidth=p.value("max_width",1600), maxHeight=p.value("max_height",0);
    if(maxWidth<0 || maxHeight<0 || maxWidth>8192 || maxHeight>8192)
        throw std::runtime_error("Capture maximum dimensions must be between 0 and 8192");
    CaptureDpiScope dpi;
    HWND window=nullptr;
    CaptureRegion::Rect targetRect{0,0,GetSystemMetrics(SM_CXSCREEN),GetSystemMetrics(SM_CYSCREEN)};
    json windowInfo=nullptr;
    if(target=="vray_vfb") {
        std::vector<HWND> windows;
        EnumWindows(FindVfbWindow,reinterpret_cast<LPARAM>(&windows));
        if(windows.empty()) throw std::runtime_error("No visible V-Ray frame buffer in this Max instance; open or restore its VFB first");
        if(windows.size()!=1) throw std::runtime_error("AMBIGUOUS: multiple visible V-Ray frame buffers in this Max instance");
        window=windows.front(); targetRect=VfbClientRect(window);
        wchar_t caption[1024]{}; GetWindowTextW(window,caption,1024);
        windowInfo={{"handle",std::to_string(reinterpret_cast<uintptr_t>(window))},{"title",WideToUtf8(caption)},
            {"process_id",GetCurrentProcessId()},{"area","client"}};
    }
    const auto region=CaptureRegion::Crop(targetRect,p.value("crop",json(nullptr)));
    RECT desktop{GetSystemMetrics(SM_XVIRTUALSCREEN),GetSystemMetrics(SM_YVIRTUALSCREEN),0,0};
    desktop.right=desktop.left+GetSystemMetrics(SM_CXVIRTUALSCREEN);
    desktop.bottom=desktop.top+GetSystemMetrics(SM_CYVIRTUALSCREEN);
    if(region.x<desktop.left || region.y<desktop.top || region.x+region.width>desktop.right || region.y+region.height>desktop.bottom)
        throw std::runtime_error("Capture region is partly offscreen; move the VFB fully onto the desktop");
    if(static_cast<uint64_t>(region.width)*region.height>134217728)
        throw std::runtime_error("Capture region is too large");
    ScreenPixels pixels; pixels.Capture(region);
    // Reject a moved/resized/replaced window rather than return a crop of the
    // wrong region when the user changes the layout during capture.
    if(window && CaptureRegion::Json(VfbClientRect(window))!=CaptureRegion::Json(targetRect))
        throw std::runtime_error("V-Ray frame buffer moved during capture; retry");
    auto bitmap=std::unique_ptr<Gdiplus::Bitmap>(Gdiplus::Bitmap::FromHBITMAP(pixels.bitmap,nullptr));
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
        {"capture_contract","desktop_crop_v1"},{"target",target},{"window",windowInfo},
        {"screen_rect",CaptureRegion::Json(region)},{"target_rect",CaptureRegion::Json(targetRect)},
        {"visible_pixels_only",true},{"occlusion_checked",false}}).dump();
}
