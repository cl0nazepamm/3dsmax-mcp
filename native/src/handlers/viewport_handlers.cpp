#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/gdiplus_runtime.h"
#include "mcp_bridge/spatial_snapshot.h"

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
          fieldOfView_(viewport.GetFOV()) {
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
        viewport_->SetAffineTM(affineTransform_);
        viewport_->SetFocalDist(focalDistance_);
        if (viewport10_ != nullptr) {
            viewport10_->SetFocalDistance(focalDistance_);
            viewport10_->SetFOV(fieldOfView_);
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
std::string NativeHandlers::CaptureMultiView(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        if (!GdiPlusRuntime::EnsureStarted())
            throw std::runtime_error("GDI+ initialization failed");

        json p = json::parse(params, nullptr, false);
        if (p.is_discarded() || !p.is_object()) {
            throw std::runtime_error("Invalid JSON payload");
        }
        int maxWidth = p.value("max_width", 1600);
        int maxHeight = p.value("max_height", 0);

        // Optional: custom views (default: front, right, back, top)
        auto viewNames = p.value("views", std::vector<std::string>{
            "front", "right", "back", "top"
        });

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        ViewExp& vp = ip->GetActiveViewExp();
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

        // Orthographic view transforms (affine TM = inverse camera)
        // Front: camera looks down -Y → rows: X=right, Y=up(Z), Z=forward(Y)
        Matrix3 frontTM(Point3(1,0,0), Point3(0,0,1), Point3(0,-1,0), Point3(0,100,0));
        // Back: camera looks down +Y
        Matrix3 backTM(Point3(-1,0,0), Point3(0,0,1), Point3(0,1,0), Point3(0,-100,0));
        // Left: camera looks down +X
        Matrix3 leftTM(Point3(0,1,0), Point3(0,0,1), Point3(1,0,0), Point3(-100,0,0));
        // Right: camera looks down -X
        Matrix3 rightTM(Point3(0,-1,0), Point3(0,0,1), Point3(-1,0,0), Point3(100,0,0));
        // Top: camera looks down -Z
        Matrix3 topTM(Point3(1,0,0), Point3(0,1,0), Point3(0,0,1), Point3(0,0,100));
        // Bottom: camera looks down +Z
        Matrix3 bottomTM(Point3(1,0,0), Point3(0,-1,0), Point3(0,0,-1), Point3(0,0,-100));

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

        MultiViewStateGuard stateGuard(ip, vp, sceneNodes);
        if (framedRoot != nullptr) {
            const std::unordered_set<INode*> framedNodeSet(
                framedNodes.begin(), framedNodes.end());
            stateGuard.PrepareFramedCapture(framedNodeSet);
        }

        // Capture each view
        std::vector<std::unique_ptr<Gdiplus::Bitmap>> captures;
        int vpWidth = 0, vpHeight = 0;

        for (auto& view : views) {
            // Set viewport orientation via SDK (no MAXScript needed)
            if (!view.persp) {
                vp.SetViewUser(FALSE);       // orthographic
                vp.SetAffineTM(view.tm);
            } else if (framedRoot != nullptr) {
                // In framed mode, "perspective" is an actual perspective
                // projection based on the caller's original view transform.
                vp.SetViewUser(TRUE);
                vp.SetAffineTM(savedTM);
            }
            // Without frame_root, perspective retains the legacy behavior.

            if (framedRoot != nullptr) {
                ip->ZoomToBounds(FALSE, framedBounds);
            } else {
                ip->ViewportZoomExtents(FALSE, FALSE);
            }

            // Force complete redraw
            ip->ForceCompleteRedraw(FALSE);

            // Capture
            ViewExp& activeVP = ip->GetActiveViewExp();
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
        stateGuard.Restore();

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
        result["framed_root"] =
            framedRoot != nullptr ? json(framedRootName) : json(nullptr);
        result["grid"] = std::to_string(cols) + "x" + std::to_string(rows);
        result["message"] = "Captured " + std::to_string(n) + " views (" +
                           std::to_string(cols) + "x" + std::to_string(rows) + " grid)";
        const std::string serializedResult = result.dump();
        outputFile.Keep();
        return serializedResult;
    });
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
std::string NativeHandlers::CaptureViewport(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        if (!GdiPlusRuntime::EnsureStarted())
            throw std::runtime_error("GDI+ initialization failed");

        json p = json::parse(params, nullptr, false);
        int maxWidth = p.value("max_width", 1600);
        int maxHeight = p.value("max_height", 0);

        Interface* ip = GetCOREInterface();
        ip->ForceCompleteRedraw(FALSE);

        ViewExp& vp = ip->GetActiveViewExp();
        Gdiplus::Bitmap* bmp = CaptureViewportDIB(&vp);
        if (!bmp) throw std::runtime_error("Failed to capture viewport DIB");

        int sourceW = (int)bmp->GetWidth();
        int sourceH = (int)bmp->GetHeight();
        Gdiplus::Bitmap* outBmp = ResizeBitmapToMax(bmp, maxWidth, maxHeight);
        std::string path = SaveBitmapToTemp(outBmp, L"3dsmax_viewport.png");
        int w = (int)outBmp->GetWidth();
        int h = (int)outBmp->GetHeight();
        delete outBmp;

        json result;
        result["file"] = path;
        result["width"] = w;
        result["height"] = h;
        result["source_width"] = sourceW;
        result["source_height"] = sourceH;
        return result.dump();
    });
}

// ── native:capture_screen ───────────────────────────────────
std::string NativeHandlers::CaptureScreen(const std::string& params, MCPBridgeGUP* gup) {
    // Screen capture doesn't need main thread — pure Win32/GDI+
    if (!GdiPlusRuntime::EnsureStarted())
        throw std::runtime_error("GDI+ initialization failed");

    json p = json::parse(params, nullptr, false);
    int maxWidth = p.value("max_width", 1600);
    int maxHeight = p.value("max_height", 0);

    // Get screen dimensions
    int screenW = GetSystemMetrics(SM_CXSCREEN);
    int screenH = GetSystemMetrics(SM_CYSCREEN);

    // Capture screen via GDI
    HDC hScreen = GetDC(NULL);
    HDC hMemDC = CreateCompatibleDC(hScreen);
    HBITMAP hBitmap = CreateCompatibleBitmap(hScreen, screenW, screenH);
    SelectObject(hMemDC, hBitmap);
    BitBlt(hMemDC, 0, 0, screenW, screenH, hScreen, 0, 0, SRCCOPY);
    ReleaseDC(NULL, hScreen);

    // Create GDI+ bitmap from HBITMAP
    Gdiplus::Bitmap* srcBmp = Gdiplus::Bitmap::FromHBITMAP(hBitmap, NULL);
    DeleteObject(hBitmap);
    DeleteDC(hMemDC);

    if (!srcBmp) throw std::runtime_error("Failed to capture screen");

    // Resize if needed
    int outW = screenW;
    int outH = screenH;
    if (maxWidth > 0 && outW > maxWidth) {
        float scale = (float)maxWidth / outW;
        outW = maxWidth;
        outH = (int)(screenH * scale);
    }
    if (maxHeight > 0 && outH > maxHeight) {
        float scale = (float)maxHeight / outH;
        outH = maxHeight;
        outW = (int)(outW * scale);
    }

    Gdiplus::Bitmap* outBmp;
    if (outW != screenW || outH != screenH) {
        outBmp = new Gdiplus::Bitmap(outW, outH, PixelFormat24bppRGB);
        Gdiplus::Graphics g(outBmp);
        g.SetInterpolationMode(Gdiplus::InterpolationModeHighQualityBicubic);
        g.DrawImage(srcBmp, 0, 0, outW, outH);
        delete srcBmp;
    } else {
        outBmp = srcBmp;
    }

    // Save as JPEG for smaller file size
    wchar_t tempPath[MAX_PATH];
    GetTempPathW(MAX_PATH, tempPath);
    std::wstring outPath = std::wstring(tempPath) + L"3dsmax_screen.jpg";

    CLSID jpgClsid;
    GetEncoderClsid(L"image/jpeg", &jpgClsid);
    Gdiplus::EncoderParameters encoderParams;
    ULONG quality = 85;
    encoderParams.Count = 1;
    encoderParams.Parameter[0].Guid = Gdiplus::EncoderQuality;
    encoderParams.Parameter[0].Type = Gdiplus::EncoderParameterValueTypeLong;
    encoderParams.Parameter[0].NumberOfValues = 1;
    encoderParams.Parameter[0].Value = &quality;
    outBmp->Save(outPath.c_str(), &jpgClsid, &encoderParams);

    int finalW = outBmp->GetWidth();
    int finalH = outBmp->GetHeight();
    delete outBmp;

    json result;
    result["file"] = WideToUtf8(outPath.c_str());
    result["width"] = finalW;
    result["height"] = finalH;
    return result.dump();
}
