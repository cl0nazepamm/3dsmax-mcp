#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"
#include "mcp_bridge/gdiplus_runtime.h"
#include "mcp_bridge/agent_viewport.h"

#include <GraphicsWindow.h>
#include <gdiplus.h>

#include <algorithm>
#include <cmath>
#include <cwctype>
#include <filesystem>
#include <memory>
#include <set>
#include <stdexcept>
#include <system_error>
#include <utility>
#include <vector>

#pragma comment(lib, "gdiplus.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;

namespace {

namespace fs = std::filesystem;

bool GetPngEncoderClsid(CLSID& clsid) {
    UINT count = 0;
    UINT bytes = 0;
    if (Gdiplus::GetImageEncodersSize(&count, &bytes) != Gdiplus::Ok ||
        count == 0 || bytes == 0) {
        return false;
    }

    std::vector<BYTE> storage(bytes);
    auto* encoders = reinterpret_cast<Gdiplus::ImageCodecInfo*>(storage.data());
    if (Gdiplus::GetImageEncoders(count, bytes, encoders) != Gdiplus::Ok) {
        return false;
    }

    for (UINT i = 0; i < count; ++i) {
        if (encoders[i].MimeType != nullptr &&
            wcscmp(encoders[i].MimeType, L"image/png") == 0) {
            clsid = encoders[i].Clsid;
            return true;
        }
    }
    return false;
}

std::unique_ptr<Gdiplus::Bitmap> CaptureViewportBitmap(ViewExp& viewport) {
    GraphicsWindow* gw = viewport.getGW();
    if (gw == nullptr) {
        throw std::runtime_error("Active viewport has no GraphicsWindow");
    }

    int dibBytes = 0;
    if (!gw->getDIB(nullptr, &dibBytes) || dibBytes <= 0) {
        throw std::runtime_error("Failed to query viewport DIB size");
    }

    std::vector<BYTE> dib(static_cast<size_t>(dibBytes));
    auto* info = reinterpret_cast<BITMAPINFO*>(dib.data());
    if (!gw->getDIB(info, &dibBytes)) {
        throw std::runtime_error("Failed to capture viewport DIB");
    }

    const int width = info->bmiHeader.biWidth;
    const int height = std::abs(info->bmiHeader.biHeight);
    const int sourceBitsPerPixel = info->bmiHeader.biBitCount;
    if (width <= 0 || height <= 0 ||
        (sourceBitsPerPixel != 24 && sourceBitsPerPixel != 32)) {
        throw std::runtime_error(
            "Viewport DIB must be 24-bit or 32-bit RGB");
    }

    const size_t colorTableBytes =
        static_cast<size_t>(info->bmiHeader.biClrUsed) * sizeof(RGBQUAD);
    size_t pixelOffset = info->bmiHeader.biSize + colorTableBytes;
    if (info->bmiHeader.biCompression == BI_BITFIELDS &&
        info->bmiHeader.biSize == sizeof(BITMAPINFOHEADER)) {
        pixelOffset += 3 * sizeof(DWORD);
    }

    const int sourceBytesPerPixel = sourceBitsPerPixel / 8;
    const int sourceStride =
        ((width * sourceBytesPerPixel) + 3) & ~3;
    const size_t requiredBytes =
        pixelOffset +
        static_cast<size_t>(sourceStride) * static_cast<size_t>(height);
    if (requiredBytes > dib.size()) {
        throw std::runtime_error("Viewport DIB pixel data is truncated");
    }

    BYTE* sourcePixels = dib.data() + pixelOffset;
    const bool isBottomUp = info->bmiHeader.biHeight > 0;

    auto bitmap = std::make_unique<Gdiplus::Bitmap>(
        width, height, PixelFormat24bppRGB);
    if (bitmap->GetLastStatus() != Gdiplus::Ok) {
        throw std::runtime_error("Failed to allocate viewport bitmap");
    }

    Gdiplus::BitmapData data{};
    Gdiplus::Rect rect(0, 0, width, height);
    if (bitmap->LockBits(
            &rect,
            Gdiplus::ImageLockModeWrite,
            PixelFormat24bppRGB,
            &data) != Gdiplus::Ok) {
        throw std::runtime_error("Failed to lock viewport bitmap");
    }

    for (int y = 0; y < height; ++y) {
        const int sourceY = isBottomUp ? (height - 1 - y) : y;
        const BYTE* sourceRow =
            sourcePixels + static_cast<size_t>(sourceY) * sourceStride;
        BYTE* destinationRow = nullptr;
        if (data.Stride >= 0) {
            destinationRow =
                static_cast<BYTE*>(data.Scan0) +
                static_cast<ptrdiff_t>(y) * data.Stride;
        } else {
            destinationRow =
                static_cast<BYTE*>(data.Scan0) +
                static_cast<ptrdiff_t>(height - 1 - y) *
                    -static_cast<ptrdiff_t>(data.Stride);
        }

        for (int x = 0; x < width; ++x) {
            destinationRow[x * 3 + 0] =
                sourceRow[x * sourceBytesPerPixel + 0];
            destinationRow[x * 3 + 1] =
                sourceRow[x * sourceBytesPerPixel + 1];
            destinationRow[x * 3 + 2] =
                sourceRow[x * sourceBytesPerPixel + 2];
        }
    }
    bitmap->UnlockBits(&data);
    return bitmap;
}

std::wstring SanitizeFilename(const MCHAR* name) {
    std::wstring safe = name != nullptr ? name : L"";
    for (wchar_t& ch : safe) {
        if (ch < 32 ||
            ch == L'<' || ch == L'>' || ch == L':' || ch == L'"' ||
            ch == L'/' || ch == L'\\' || ch == L'|' || ch == L'?' ||
            ch == L'*') {
            ch = L'_';
        }
    }

    while (!safe.empty() && (safe.back() == L' ' || safe.back() == L'.')) {
        safe.back() = L'_';
    }
    if (safe.empty()) {
        safe = L"_";
    }
    return safe;
}

void SavePng(Gdiplus::Bitmap& bitmap, const fs::path& path) {
    CLSID pngEncoder{};
    if (!GetPngEncoderClsid(pngEncoder)) {
        throw std::runtime_error("PNG encoder is unavailable");
    }

    std::error_code ignored;
    fs::remove(path, ignored);
    if (bitmap.Save(path.c_str(), &pngEncoder, nullptr) != Gdiplus::Ok) {
        throw std::runtime_error(
            "Failed to save viewport PNG: " +
            WideToUtf8(path.c_str()));
    }
}

void CollectNodeAndDescendants(INode* node, std::vector<INode*>& nodes) {
    if (node == nullptr) {
        return;
    }

    nodes.push_back(node);
    for (int i = 0; i < node->NumberOfChildren(); ++i) {
        CollectNodeAndDescendants(node->GetChildNode(i), nodes);
    }
}

INode* GetTopLevelParent(INode* node, INode* sceneRoot) {
    if (node == nullptr) {
        return nullptr;
    }

    INode* current = node;
    while (current->GetParentNode() != nullptr &&
           current->GetParentNode() != sceneRoot) {
        current = current->GetParentNode();
    }
    return current;
}

Object* GetTrueBaseObject(INode* node) {
    Object* object = node != nullptr ? node->GetObjectRef() : nullptr;
    while (object != nullptr &&
           object->SuperClassID() == GEN_DERIVOB_CLASS_ID) {
        object = static_cast<IDerivedObject*>(object)->GetObjRef();
    }
    return object;
}

struct InstanceGroup {
    Object* baseObject = nullptr;
    std::vector<INode*> roots;
};

class IdentifyStateGuard {
public:
    IdentifyStateGuard(
        Interface* ip,
        ViewExp& viewport,
        const std::vector<INode*>& sceneNodes)
        : interface_(ip),
          viewport_(&viewport),
          viewType_(viewport.GetViewType()),
          wasPerspective_(viewport.IsPerspView()),
          viewCamera_(viewport.GetViewCamera()),
          viewSpot_(viewport.GetViewSpot()),
          focalDistance_(viewport.GetFocalDist()),
          fieldOfView_(viewport.GetFOV()),
          referenceScreenPoint_(
              viewport.MapViewToScreen(Point3::Origin)),
          referenceWorldWidth_(
              viewport.GetVPWorldWidth(Point3::Origin)) {
        viewport.GetAffineTM(affineTransform_);
        viewport10_ = reinterpret_cast<ViewExp10*>(
            viewport.Execute(ViewExp::kEXECUTE_GET_VIEWEXP_10));

        const int selectionCount = interface_->GetSelNodeCount();
        selectedNodes_.reserve(static_cast<size_t>(selectionCount));
        for (int i = 0; i < selectionCount; ++i) {
            INode* node = interface_->GetSelNode(i);
            if (node != nullptr) {
                selectedNodes_.push_back(node);
            }
        }

        hiddenStates_.reserve(sceneNodes.size());
        for (INode* node : sceneNodes) {
            if (node != nullptr) {
                hiddenStates_.emplace_back(node, node->IsHidden() != 0);
            }
        }
    }

    IdentifyStateGuard(const IdentifyStateGuard&) = delete;
    IdentifyStateGuard& operator=(const IdentifyStateGuard&) = delete;

    ~IdentifyStateGuard() {
        Restore();
    }

    void PrepareCaptureView() {
        if (viewport_ == nullptr || !viewport_->IsAlive()) {
            return;
        }

        // Zoom-extents in an object-based camera/light viewport can affect the
        // scene object driving that view. Work in an equivalent user view and
        // let RestoreViewport rebind the original camera or spotlight.
        if (viewType_ == VIEW_CAMERA || viewType_ == VIEW_SPOT) {
            viewport_->SetViewUser(wasPerspective_);
            viewport_->SetAffineTM(affineTransform_);
            viewport_->SetFocalDist(focalDistance_);
            if (viewport10_ != nullptr && wasPerspective_) {
                viewport10_->SetFocalDistance(focalDistance_);
                viewport10_->SetFOV(fieldOfView_);
            }
        }
    }

    void Restore() noexcept {
        if (restored_) {
            return;
        }
        restored_ = true;

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
        if (viewport_ == nullptr || !viewport_->IsAlive()) {
            return;
        }

        if (viewType_ == VIEW_CAMERA && viewCamera_ != nullptr) {
            viewport_->SetViewCamera(viewCamera_);
        } else if (viewType_ == VIEW_SPOT && viewSpot_ != nullptr) {
            viewport_->SetViewSpot(viewSpot_);
        }

        if (viewType_ == VIEW_ISO_USER || viewType_ == VIEW_PERSP_USER) {
            viewport_->SetViewUser(wasPerspective_);
            viewport_->SetAffineTM(affineTransform_);
            viewport_->SetFocalDist(focalDistance_);
            if (viewport10_ != nullptr) {
                viewport10_->SetFocalDistance(focalDistance_);
                viewport10_->SetFOV(fieldOfView_);
            }
            return;
        }

        // Fixed orthographic views reject SetAffineTM. Restore their zoom and
        // pan through the public ViewExp10 navigation surface while retaining
        // the original fixed view type.
        if (viewport10_ != nullptr &&
            referenceWorldWidth_ > 0.0f &&
            std::isfinite(referenceWorldWidth_)) {
            const float currentWorldWidth =
                viewport_->GetVPWorldWidth(Point3::Origin);
            if (currentWorldWidth > 0.0f &&
                std::isfinite(currentWorldWidth)) {
                viewport10_->Zoom(
                    referenceWorldWidth_ / currentWorldWidth);
            }

            const Point2 currentScreenPoint =
                viewport_->MapViewToScreen(Point3::Origin);
            viewport10_->Pan(
                referenceScreenPoint_ - currentScreenPoint);
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
    Point2 referenceScreenPoint_;
    float referenceWorldWidth_ = 0.0f;
    bool restored_ = false;
};

fs::path ResolveCaptureDirectory(const json& params) {
    const std::string requested = params.value("capture_dir", "");
    if (!requested.empty()) {
        return fs::path(Utf8ToWide(requested));
    }

    wchar_t tempPath[MAX_PATH]{};
    const DWORD length = GetTempPathW(MAX_PATH, tempPath);
    if (length == 0 || length >= MAX_PATH) {
        throw std::runtime_error("Failed to resolve the Windows temp directory");
    }
    return fs::path(tempPath) / L"3dsmax-mcp" / L"identify";
}

std::string PathForJson(const fs::path& path) {
    std::string result = WideToUtf8(path.c_str());
    std::replace(result.begin(), result.end(), '\\', '/');
    return result;
}

std::wstring CaseInsensitiveFilenameKey(const fs::path& path) {
    std::wstring key = path.filename().native();
    std::transform(
        key.begin(),
        key.end(),
        key.begin(),
        [](wchar_t ch) { return static_cast<wchar_t>(std::towlower(ch)); });
    return key;
}

fs::path UniqueCapturePath(
    const fs::path& directory,
    INode* representative,
    std::set<std::wstring>& usedNames) {
    const std::wstring base = SanitizeFilename(representative->GetName());
    fs::path candidate = directory / (base + L".png");
    if (usedNames.insert(CaseInsensitiveFilenameKey(candidate)).second) {
        return candidate;
    }

    const std::wstring handle =
        std::to_wstring(NodeHandle(representative));
    candidate = directory / (base + L"_" + handle + L".png");
    int suffix = 2;
    while (!usedNames.insert(CaseInsensitiveFilenameKey(candidate)).second) {
        candidate =
            directory /
            (base + L"_" + handle + L"_" +
             std::to_wstring(suffix++) + L".png");
    }
    return candidate;
}

}  // namespace

namespace NativeHandlers {

// ── native:isolate_and_capture_selected ────────────────────────
std::string IsolateAndCaptureSelected(
    const std::string& params,
    MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        if(AgentViewport::IsOwned()) throw std::runtime_error(
            "Scene-wide isolation is disabled while AGENT VIEWPORT is owned; use agent_viewport frame and capture_viewport");
        const json payload = json::parse(params, nullptr, false);
        if (payload.is_discarded()) {
            throw std::runtime_error("Invalid JSON payload");
        }
        if (!GdiPlusRuntime::EnsureStarted()) {
            throw std::runtime_error("GDI+ initialization failed");
        }

        Interface* ip = GetCOREInterface();
        INode* sceneRoot = ip->GetRootNode();

        const int selectionCount = ip->GetSelNodeCount();
        if (selectionCount == 0) {
            return json::array().dump();
        }

        std::vector<INode*> sceneNodes;
        CollectNodes(sceneRoot, sceneNodes);

        ViewExp& viewport = ip->GetActiveViewExp();
        IdentifyStateGuard stateGuard(ip, viewport, sceneNodes);
        stateGuard.PrepareCaptureView();

        std::vector<INode*> roots;
        roots.reserve(static_cast<size_t>(selectionCount));
        for (int i = 0; i < selectionCount; ++i) {
            INode* root =
                GetTopLevelParent(ip->GetSelNode(i), sceneRoot);
            if (root != nullptr &&
                std::find(roots.begin(), roots.end(), root) == roots.end()) {
                roots.push_back(root);
            }
        }

        std::vector<InstanceGroup> groups;
        for (INode* root : roots) {
            Object* baseObject = GetTrueBaseObject(root);
            auto existing = groups.end();
            if (baseObject != nullptr) {
                existing = std::find_if(
                    groups.begin(),
                    groups.end(),
                    [baseObject](const InstanceGroup& group) {
                        return group.baseObject == baseObject;
                    });
            }
            if (existing != groups.end()) {
                existing->roots.push_back(root);
            } else {
                groups.push_back(InstanceGroup{baseObject, {root}});
            }
        }

        const fs::path captureDirectory = ResolveCaptureDirectory(payload);
        std::error_code directoryError;
        fs::create_directories(captureDirectory, directoryError);
        if (directoryError || !fs::is_directory(captureDirectory)) {
            throw std::runtime_error(
                "Failed to create identify capture directory: " +
                PathForJson(captureDirectory));
        }

        json result = json::array();
        std::set<std::wstring> usedCaptureNames;
        for (const InstanceGroup& group : groups) {
            INode* representative = group.roots.front();
            std::vector<INode*> descendants;
            CollectNodeAndDescendants(representative, descendants);

            for (INode* node : sceneNodes) {
                node->Hide(TRUE);
            }
            for (INode* node : descendants) {
                node->Hide(FALSE);
            }

            ip->ClearNodeSelection(FALSE);
            for (INode* node : descendants) {
                ip->SelectNode(node, FALSE);
            }

            ip->ViewportZoomExtents(FALSE, FALSE);
            ip->ForceCompleteRedraw(FALSE);

            std::unique_ptr<Gdiplus::Bitmap> bitmap =
                CaptureViewportBitmap(ip->GetActiveViewExp());
            const fs::path imagePath = UniqueCapturePath(
                captureDirectory,
                representative,
                usedCaptureNames);
            SavePng(*bitmap, imagePath);

            json instances = json::array();
            for (INode* root : group.roots) {
                instances.push_back(WideToUtf8(root->GetName()));
            }

            result.push_back({
                {"name", WideToUtf8(representative->GetName())},
                {"image_path", PathForJson(imagePath)},
                {"instances", std::move(instances)},
            });
        }

        return result.dump();
    });
}

}  // namespace NativeHandlers
