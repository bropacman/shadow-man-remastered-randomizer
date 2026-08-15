#include "hook_d3d11.h"
#include "overlay.h"

#include <d3d11.h>
#include <dxgi.h>
#include <windows.h>
#include <MinHook.h>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")

namespace {

using Present_t = HRESULT(__stdcall*)(IDXGISwapChain*, UINT, UINT);
using ResizeBuffers_t = HRESULT(__stdcall*)(IDXGISwapChain*, UINT, UINT, UINT, DXGI_FORMAT, UINT);

Present_t oPresent = nullptr;
ResizeBuffers_t oResizeBuffers = nullptr;

ID3D11Device* g_device = nullptr;
ID3D11DeviceContext* g_context = nullptr;
ID3D11RenderTargetView* g_rtv = nullptr;
bool g_overlayInited = false;

void ReleaseRtv() {
    if (g_rtv) { g_rtv->Release(); g_rtv = nullptr; }
}

HRESULT __stdcall hkPresent(IDXGISwapChain* pSwapChain, UINT SyncInterval, UINT Flags) {
    if (!g_overlayInited) {
        if (SUCCEEDED(pSwapChain->GetDevice(__uuidof(ID3D11Device), (void**)&g_device))) {
            g_device->GetImmediateContext(&g_context);
            DXGI_SWAP_CHAIN_DESC desc{};
            pSwapChain->GetDesc(&desc);
            g_overlayInited = Overlay::Init(g_device, g_context, desc.OutputWindow);
        }
    }

    if (g_overlayInited) {
        if (!g_rtv) {
            ID3D11Texture2D* backBuffer = nullptr;
            if (SUCCEEDED(pSwapChain->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&backBuffer))) {
                g_device->CreateRenderTargetView(backBuffer, nullptr, &g_rtv);
                backBuffer->Release();
            }
        }
        if (g_rtv) {
            g_context->OMSetRenderTargets(1, &g_rtv, nullptr);
            Overlay::Render();
        }
    }

    return oPresent(pSwapChain, SyncInterval, Flags);
}

HRESULT __stdcall hkResizeBuffers(IDXGISwapChain* pSwapChain, UINT BufferCount,
                                   UINT Width, UINT Height, DXGI_FORMAT NewFormat,
                                   UINT SwapChainFlags) {
    ReleaseRtv();
    Overlay::OnPreResize();
    return oResizeBuffers(pSwapChain, BufferCount, Width, Height, NewFormat, SwapChainFlags);
}

// Creates a throwaway hidden window + D3D11 device/swapchain purely to read
// real vtable function pointers off it. Standard technique for hooking a
// COM interface you don't otherwise have an instance of yet.
bool GetRealVtablePointers(void** outPresent, void** outResizeBuffers) {
    WNDCLASSEXA wc{};
    wc.cbSize = sizeof(wc);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = DefWindowProcA;
    wc.hInstance = GetModuleHandleA(nullptr);
    wc.lpszClassName = "ShadowManOverlayDummyWndClass";
    RegisterClassExA(&wc);

    HWND hwnd = CreateWindowExA(0, wc.lpszClassName, "dummy", WS_OVERLAPPEDWINDOW,
                                 0, 0, 100, 100, nullptr, nullptr, wc.hInstance, nullptr);
    if (!hwnd) {
        UnregisterClassA(wc.lpszClassName, wc.hInstance);
        return false;
    }

    DXGI_SWAP_CHAIN_DESC scd{};
    scd.BufferCount = 1;
    scd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    scd.BufferDesc.Width = 100;
    scd.BufferDesc.Height = 100;
    scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow = hwnd;
    scd.SampleDesc.Count = 1;
    scd.Windowed = TRUE;
    scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL featureLevel;
    IDXGISwapChain* swapChain = nullptr;
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;

    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, &scd, &swapChain,
        &device, &featureLevel, &context);

    if (FAILED(hr)) {
        DestroyWindow(hwnd);
        UnregisterClassA(wc.lpszClassName, wc.hInstance);
        return false;
    }

    // swapChain is a COM object: first 8 bytes (x64) point to its vtable,
    // an array of function pointers in declared interface order.
    void** vtable = *reinterpret_cast<void***>(swapChain);
    *outPresent = vtable[8];        // IDXGISwapChain::Present
    *outResizeBuffers = vtable[13]; // IDXGISwapChain::ResizeBuffers

    swapChain->Release();
    context->Release();
    device->Release();
    DestroyWindow(hwnd);
    UnregisterClassA(wc.lpszClassName, wc.hInstance);
    return true;
}

} // namespace

namespace HookD3D11 {

bool Install() {
    void* presentAddr = nullptr;
    void* resizeAddr = nullptr;
    if (!GetRealVtablePointers(&presentAddr, &resizeAddr)) return false;

    if (MH_Initialize() != MH_OK) return false;

    if (MH_CreateHook(presentAddr, &hkPresent, reinterpret_cast<void**>(&oPresent)) != MH_OK)
        return false;
    if (MH_CreateHook(resizeAddr, &hkResizeBuffers, reinterpret_cast<void**>(&oResizeBuffers)) != MH_OK)
        return false;

    if (MH_EnableHook(MH_ALL_HOOKS) != MH_OK) return false;

    return true;
}

void Uninstall() {
    MH_DisableHook(MH_ALL_HOOKS);
    ReleaseRtv();
    Overlay::Shutdown();
    if (g_context) { g_context->Release(); g_context = nullptr; }
    if (g_device) { g_device->Release(); g_device = nullptr; }
    MH_Uninitialize();
}

} // namespace HookD3D11
