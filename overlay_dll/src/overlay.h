// overlay.h — ImGui-based toast popup renderer.
//
// Deliberately non-interactive: toasts never capture mouse/keyboard input
// (ImGuiWindowFlags_NoInputs), so we never need to hook the game's WndProc
// or fight it for focus. This matches the OOT/MM/DS3-style "just a corner
// popup" UX the feature is modeled on.
#pragma once
#include <d3d11.h>
#include <windows.h>

namespace Overlay {

// Call once, from inside the hooked Present the first time a valid
// device/context/window are available.
bool Init(ID3D11Device* device, ID3D11DeviceContext* context, HWND hwnd);

// Call every frame from the hooked Present, after Init() has succeeded and
// the render target view for the current back buffer is bound.
void Render();

// Call from the hooked ResizeBuffers, BEFORE the real ResizeBuffers call,
// to release any back-buffer-derived resources so we don't hold a stale
// pointer across the resize.
void OnPreResize();

void Shutdown();

} // namespace Overlay
