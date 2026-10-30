// Standalone D3D9 test host. Never injects into the game.
#include <windows.h>
#include <d3d9.h>
#include <fstream>

LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, wParam, lParam);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR command, int) {
    const bool selfTest = command && wcsstr(command, L"--self-test");
    WNDCLASSW klass{}; klass.lpfnWndProc = WindowProc; klass.hInstance = instance; klass.lpszClassName = L"OverlaySmoke";
    RegisterClassW(&klass);
    HWND window = CreateWindowW(klass.lpszClassName, L"Amazing DLL test host — Ctrl+L, Alt+F", WS_OVERLAPPEDWINDOW,
                                80, 80, 1280, 800, nullptr, nullptr, instance, nullptr);
    auto* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) return 1;
    D3DPRESENT_PARAMETERS parameters{}; parameters.Windowed = TRUE; parameters.SwapEffect = D3DSWAPEFFECT_DISCARD; parameters.hDeviceWindow = window;
    IDirect3DDevice9* device = nullptr;
    if (FAILED(d3d->CreateDevice(0, D3DDEVTYPE_HAL, window, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &parameters, &device))) return 2;
    HMODULE library = LoadLibraryW(L"AmazingLawClient.dll");
    if (!library) return 3;
    using Probe = unsigned(__cdecl*)();
    using Show = void(__cdecl*)();
    auto probe = reinterpret_cast<Probe>(GetProcAddress(library, "AmazingOverlayProbe"));
    auto show = reinterpret_cast<Show>(GetProcAddress(library, "AmazingOverlayShowForTest"));
    using Diagnostics = void(__cdecl*)(char*, unsigned);
    auto diagnostics = reinterpret_cast<Diagnostics>(GetProcAddress(library, "AmazingOverlayDiagnostics"));
    if (!probe || !show) return 4;
    ShowWindow(window, selfTest ? SW_HIDE : SW_SHOW);
    ULONGLONG started = GetTickCount64();
    unsigned frames = 0;
    MSG message{};
    while (message.message != WM_QUIT) {
        if (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); continue; }
        device->Clear(0, nullptr, D3DCLEAR_TARGET, D3DCOLOR_XRGB(15, 23, 42), 1, 0);
        if (SUCCEEDED(device->BeginScene())) device->EndScene();
        device->Present(nullptr, nullptr, nullptr, nullptr);
        if (selfTest) {
            if (probe() & 1u) show();
            if (++frames >= 250 && probe() == 3u) { device->Release(); d3d->Release(); return 0; }
            if (GetTickCount64() - started > 15000) {
                char detail[2048]{};
                if (diagnostics) diagnostics(detail, sizeof(detail));
                std::ofstream("native-smoke.log") << "probe=" << probe() << " frames=" << frames << " status=" << detail;
                return 5;
            }
        }
        Sleep(10);
    }
    device->Release(); d3d->Release();
    return 0;
}
