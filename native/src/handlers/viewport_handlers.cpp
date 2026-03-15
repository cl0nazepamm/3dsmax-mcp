#include "mcp_bridge/native_handlers.h"
#include "mcp_bridge/handler_helpers.h"
#include "mcp_bridge/bridge_gup.h"

#include <GraphicsWindow.h>
#include <gdiplus.h>

#pragma comment(lib, "gdiplus.lib")

using json = nlohmann::json;
using namespace HandlerHelpers;

// ── GDI+ RAII initializer ───────────────────────────────────
static class GdiPlusInit {
    ULONG_PTR token = 0;
public:
    GdiPlusInit() {
        Gdiplus::GdiplusStartupInput input;
        Gdiplus::GdiplusStartup(&token, &input, nullptr);
    }
    ~GdiPlusInit() {
        if (token) Gdiplus::GdiplusShutdown(token);
    }
} g_gdipInit;

// ── Helper: get PNG encoder CLSID ───────────────────────────
static int GetEncoderClsid(const WCHAR* format, CLSID* pClsid) {
    UINT num = 0, size = 0;
    Gdiplus::GetImageEncodersSize(&num, &size);
    if (size == 0) return -1;

    auto* pImageCodecInfo = (Gdiplus::ImageCodecInfo*)(malloc(size));
    if (!pImageCodecInfo) return -1;

    Gdiplus::GetImageEncoders(num, size, pImageCodecInfo);
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
    Gdiplus::Font font(&fontFamily, 14, Gdiplus::FontStyleBold, Gdiplus::UnitPixel);
    Gdiplus::SolidBrush bgBrush(Gdiplus::Color(160, 0, 0, 0));
    Gdiplus::SolidBrush textBrush(Gdiplus::Color(255, 255, 255, 255));

    Gdiplus::RectF layoutRect((Gdiplus::REAL)x, (Gdiplus::REAL)y,
                               (Gdiplus::REAL)quadW, 24.0f);
    Gdiplus::StringFormat format;
    format.SetAlignment(Gdiplus::StringAlignmentCenter);

    // Background bar
    g.FillRectangle(&bgBrush, x, y, quadW, 22);
    // Text
    g.DrawString(text, -1, &font, layoutRect, &format, &textBrush);
}

// ── native:capture_multi_view ───────────────────────────────
std::string NativeHandlers::CaptureMultiView(const std::string& params, MCPBridgeGUP* gup) {
    return gup->GetExecutor().ExecuteSync([&params]() -> std::string {
        json p = json::parse(params, nullptr, false);

        // Optional: custom views (default: front, right, back, top)
        auto viewNames = p.value("views", std::vector<std::string>{
            "front", "right", "back", "top"
        });

        Interface* ip = GetCOREInterface();
        TimeValue t = ip->GetTime();

        // Save current viewport state
        ViewExp& vp = ip->GetActiveViewExp();
        Matrix3 savedTM;
        vp.GetAffineTM(savedTM);

        // Map view names to MAXScript viewport types
        struct ViewDef {
            std::string name;
            std::string msType;
            std::wstring label;
        };
        std::map<std::string, ViewDef> viewMap = {
            {"front",       {"front",       "#view_front",         L"FRONT"}},
            {"back",        {"back",        "#view_back",          L"BACK"}},
            {"left",        {"left",        "#view_left",          L"LEFT"}},
            {"right",       {"right",       "#view_right",         L"RIGHT"}},
            {"top",         {"top",         "#view_top",           L"TOP"}},
            {"bottom",      {"bottom",      "#view_bottom",        L"BOTTOM"}},
            {"perspective", {"perspective", "#view_persp_user",    L"PERSP"}},
        };

        // Collect views to capture
        std::vector<ViewDef> views;
        for (const auto& vn : viewNames) {
            std::string lower = vn;
            std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
            auto it = viewMap.find(lower);
            if (it != viewMap.end()) {
                views.push_back(it->second);
            }
        }
        if (views.empty()) {
            throw std::runtime_error("No valid views specified");
        }

        // Capture each view
        std::vector<Gdiplus::Bitmap*> captures;
        int vpWidth = 0, vpHeight = 0;

        for (auto& view : views) {
            // Set viewport type
            if (view.msType != "#view_persp_user") {
                RunMAXScript("viewport.setType " + view.msType);
            }

            // Zoom extents
            ip->ViewportZoomExtents(FALSE, FALSE);

            // Force complete redraw
            ip->ForceCompleteRedraw(FALSE);

            // Capture
            ViewExp& activeVP = ip->GetActiveViewExp();
            Gdiplus::Bitmap* bmp = CaptureViewportDIB(&activeVP);
            if (!bmp) {
                // Cleanup and throw
                for (auto* b : captures) delete b;
                throw std::runtime_error("Failed to capture viewport for: " + view.name);
            }

            if (vpWidth == 0) {
                vpWidth = bmp->GetWidth();
                vpHeight = bmp->GetHeight();
            }

            captures.push_back(bmp);
        }

        // Restore original viewport state
        vp.SetAffineTM(savedTM);
        ip->RedrawViews(t);

        // Determine grid layout
        int cols, rows;
        int n = (int)captures.size();
        if (n <= 1) { cols = 1; rows = 1; }
        else if (n <= 2) { cols = 2; rows = 1; }
        else if (n <= 4) { cols = 2; rows = 2; }
        else if (n <= 6) { cols = 3; rows = 2; }
        else { cols = 3; rows = 3; }

        // Create stitched bitmap
        int stitchW = cols * vpWidth;
        int stitchH = rows * vpHeight;
        Gdiplus::Bitmap stitched(stitchW, stitchH, PixelFormat24bppRGB);
        Gdiplus::Graphics g(&stitched);
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
            g.DrawImage(captures[i], x, y, vpWidth, vpHeight);
            DrawLabel(g, views[i].label.c_str(), x, y, vpWidth, vpHeight);
        }

        // Save to temp file as PNG
        wchar_t tempPath[MAX_PATH];
        GetTempPathW(MAX_PATH, tempPath);
        std::wstring outPath = std::wstring(tempPath) + L"3dsmax_multiview.png";

        CLSID pngClsid;
        GetEncoderClsid(L"image/png", &pngClsid);
        stitched.Save(outPath.c_str(), &pngClsid, nullptr);

        // Cleanup
        for (auto* b : captures) delete b;

        // Return path
        json result;
        result["file"] = WideToUtf8(outPath.c_str());
        result["width"] = stitchW;
        result["height"] = stitchH;
        result["views"] = viewNames;
        result["grid"] = std::to_string(cols) + "x" + std::to_string(rows);
        result["message"] = "Captured " + std::to_string(n) + " views (" +
                           std::to_string(cols) + "x" + std::to_string(rows) + " grid)";
        return result.dump();
    });
}
