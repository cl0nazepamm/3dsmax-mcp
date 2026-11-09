#include "mcp_bridge/window_capture.h"
#include <d3d11.h>
#include <dwmapi.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <chrono>
#include <stdexcept>
#include <thread>

#pragma comment(lib,"d3d11.lib")
#pragma comment(lib,"dwmapi.lib")
#pragma comment(lib,"windowsapp.lib")
#pragma comment(lib,"gdiplus.lib")

namespace WindowCapture {
namespace {
struct Apartment {
    HRESULT status=CoInitializeEx(nullptr,COINIT_MULTITHREADED);
    Apartment() { if(FAILED(status) && status!=RPC_E_CHANGED_MODE) winrt::check_hresult(status); }
    ~Apartment() { if(SUCCEEDED(status)) CoUninitialize(); }
};
RECT ClientBounds(HWND window) {
    DWORD pid=0;
    GetWindowThreadProcessId(window,&pid);
    if(!IsWindow(window) || pid!=GetCurrentProcessId() || !IsWindowVisible(window) ||
       IsIconic(GetAncestor(window,GA_ROOT)))
        throw std::runtime_error("Window capture requires a visible, non-minimized window in this Max instance");
    RECT rect{}; POINT point{};
    if(!GetClientRect(window,&rect) || !ClientToScreen(window,&point) || rect.right<=0 || rect.bottom<=0 ||
       static_cast<unsigned long long>(rect.right)*rect.bottom>134217728)
        throw std::runtime_error("Window capture has invalid client bounds");
    return {point.x,point.y,point.x+rect.right,point.y+rect.bottom};
}
bool Equal(const RECT& a,const RECT& b) {
    return a.left==b.left && a.top==b.top && a.right==b.right && a.bottom==b.bottom;
}

// PrintWindow is synchronous. Only use it on the HWND's own responsive thread;
// a pipe worker must not wait indefinitely for a renderer blocking Max's UI.
Result PrintClient(HWND window,const RECT& rect) {
    const int width=rect.right-rect.left,height=rect.bottom-rect.top;
    auto bitmap=std::make_unique<Gdiplus::Bitmap>(width,height,PixelFormat32bppRGB);
    Gdiplus::Graphics graphics(bitmap.get());
    HDC dc=graphics.GetHDC();
    if(!dc) throw std::runtime_error("Could not allocate window capture DC");
    const BOOL printed=PrintWindow(window,dc,PW_CLIENTONLY|2 /* PW_RENDERFULLCONTENT */);
    graphics.ReleaseHDC(dc);
    if(!printed || bitmap->GetLastStatus()!=Gdiplus::Ok)
        throw std::runtime_error("PrintWindow could not capture this window; desktop pixels were not used");
    return {std::move(bitmap),"print_window"};
}

Result GraphicsClient(HWND window,const RECT& client) {
    using namespace winrt::Windows::Graphics;
    using namespace Capture;
    Apartment apartment;
    if(!GraphicsCaptureSession::IsSupported())
        throw std::runtime_error("Windows Graphics Capture is unavailable; desktop pixels were not used");
    HWND root=GetAncestor(window,GA_ROOT);
    RECT bounds{};
    winrt::check_hresult(DwmGetWindowAttribute(root,DWMWA_EXTENDED_FRAME_BOUNDS,&bounds,sizeof(bounds)));
    auto interop=winrt::get_activation_factory<GraphicsCaptureItem,IGraphicsCaptureItemInterop>();
    GraphicsCaptureItem item{nullptr};
    winrt::check_hresult(interop->CreateForWindow(root,winrt::guid_of<GraphicsCaptureItem>(),winrt::put_abi(item)));
    auto size=item.Size();
    if(size.Width<=0 || size.Height<=0 || static_cast<unsigned long long>(size.Width)*size.Height>134217728 ||
       size.Width!=bounds.right-bounds.left || size.Height!=bounds.bottom-bounds.top ||
       client.left<bounds.left || client.top<bounds.top || client.right>bounds.right || client.bottom>bounds.bottom)
        throw std::runtime_error("Window capture frame/client bounds disagree; retry after resizing finishes");
    winrt::com_ptr<ID3D11Device> device;
    winrt::com_ptr<ID3D11DeviceContext> context;
    winrt::check_hresult(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr,0,D3D11_SDK_VERSION,device.put(),nullptr,context.put()));
    auto dxgi=device.as<IDXGIDevice>();
    winrt::com_ptr<IInspectable> inspectable;
    winrt::check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi.get(),inspectable.put()));
    auto rtDevice=inspectable.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();
    auto pool=Direct3D11CaptureFramePool::CreateFreeThreaded(rtDevice,winrt::Windows::Graphics::DirectX::DirectXPixelFormat::B8G8R8A8UIntNormalized,1,size);
    auto session=pool.CreateCaptureSession(item);
    struct Close {
        GraphicsCaptureSession session;
        Direct3D11CaptureFramePool pool;
        ~Close() { try { session.Close(); } catch(...) {} try { pool.Close(); } catch(...) {} }
    } close{session,pool};
    session.IsCursorCaptureEnabled(false);
    session.StartCapture();
    Direct3D11CaptureFrame frame{nullptr};
    const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(2);
    do {
        frame=pool.TryGetNextFrame();
        if(frame) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    } while(std::chrono::steady_clock::now()<deadline);
    if(!frame) throw std::runtime_error("WINDOW_CAPTURE_TIMEOUT: no window frame within 2 seconds; desktop pixels were not used");
    const auto actual=frame.ContentSize();
    RECT after{};
    winrt::check_hresult(DwmGetWindowAttribute(root,DWMWA_EXTENDED_FRAME_BOUNDS,&after,sizeof(after)));
    if(actual.Width!=size.Width || actual.Height!=size.Height || !Equal(bounds,after))
        throw std::runtime_error("Window moved or resized during capture; retry");
    auto access=frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
    winrt::com_ptr<ID3D11Texture2D> texture;
    winrt::check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D),texture.put_void()));
    D3D11_TEXTURE2D_DESC desc{}; texture->GetDesc(&desc);
    if(desc.Width<static_cast<UINT>(size.Width) || desc.Height<static_cast<UINT>(size.Height) ||
       desc.Format!=DXGI_FORMAT_B8G8R8A8_UNORM)
        throw std::runtime_error("Window frame texture size or format changed; retry");
    desc.Usage=D3D11_USAGE_STAGING; desc.BindFlags=0; desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ; desc.MiscFlags=0;
    winrt::com_ptr<ID3D11Texture2D> staging;
    winrt::check_hresult(device->CreateTexture2D(&desc,nullptr,staging.put()));
    context->CopyResource(staging.get(),texture.get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    winrt::check_hresult(context->Map(staging.get(),0,D3D11_MAP_READ,0,&mapped));
    struct Unmap { ID3D11DeviceContext* context; ID3D11Texture2D* texture; ~Unmap() { context->Unmap(texture,0); } } unmap{context.get(),staging.get()};
    Gdiplus::Bitmap full(size.Width,size.Height,static_cast<INT>(mapped.RowPitch),PixelFormat32bppRGB,static_cast<BYTE*>(mapped.pData));
    Gdiplus::Rect crop(static_cast<INT>(client.left-bounds.left),static_cast<INT>(client.top-bounds.top),
        static_cast<INT>(client.right-client.left),static_cast<INT>(client.bottom-client.top));
    std::unique_ptr<Gdiplus::Bitmap> bitmap(full.Clone(crop,PixelFormat24bppRGB));
    if(!bitmap || bitmap->GetLastStatus()!=Gdiplus::Ok) throw std::runtime_error("Could not copy window frame pixels");
    return {std::move(bitmap),"windows_graphics_capture"};
}
}
Result Client(HWND window) {
    auto before=ClientBounds(window);
    Result result;
    try {
        result=GetWindowThreadProcessId(window,nullptr)==GetCurrentThreadId()
            ? PrintClient(window,before) : GraphicsClient(window,before);
    } catch(const winrt::hresult_error& error) {
        throw std::runtime_error("Window capture failed: "+winrt::to_string(error.message())+"; desktop pixels were not used");
    }
    if(!Equal(before,ClientBounds(window))) throw std::runtime_error("Window changed during capture; retry");
    return result;
}
void ReleaseFactories() noexcept { winrt::clear_factory_cache(); }
}
