// hook_d3d11.h — detours IDXGISwapChain::Present (and ResizeBuffers, so we
// don't hold a stale render-target-view across a window resize) to draw
// the overlay every frame.
//
// NOTE: this assumes the game renders via Direct3D 11 (IDXGISwapChain).
// If tools/detect_renderer.py reports a different API (D3D9, OpenGL,
// Vulkan), this file is the ONLY thing that needs replacing — ipc_server,
// overlay, and json_mini are all backend-agnostic.
#pragma once

namespace HookD3D11 {

// Spins up a throwaway device/swapchain purely to read the real Present /
// ResizeBuffers function pointers off its vtable, installs MinHook detours
// on those addresses (shared code, so the detour applies to every
// swapchain in the process — including the game's real one), then tears
// the dummy objects down. Returns false if any step fails.
bool Install();

void Uninstall();

} // namespace HookD3D11
