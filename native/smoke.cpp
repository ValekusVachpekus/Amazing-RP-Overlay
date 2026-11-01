// Standalone D3D9 test host. Never injects into the game.
#include <windows.h>
#include <d3d9.h>
#include <fstream>
#include <vector>

void Capture(IDirect3DDevice9* device) {
    wchar_t path[32768]{};
    if (!GetEnvironmentVariableW(L"AMAZING_OVERLAY_CAPTURE", path, 32768)) return;
    IDirect3DSurface9 *back = nullptr, *copy = nullptr;
    if (FAILED(device->GetRenderTarget(0, &back))) return;
    D3DSURFACE_DESC desc{}; back->GetDesc(&desc);
    if (SUCCEEDED(device->CreateOffscreenPlainSurface(desc.Width, desc.Height, desc.Format, D3DPOOL_SYSTEMMEM, &copy, nullptr)) &&
        SUCCEEDED(device->GetRenderTargetData(back, copy))) {
        D3DLOCKED_RECT pixels{};
        if (SUCCEEDED(copy->LockRect(&pixels, nullptr, D3DLOCK_READONLY))) {
            BITMAPFILEHEADER file{}; BITMAPINFOHEADER info{};
            file.bfType = 0x4d42; file.bfOffBits = sizeof(file) + sizeof(info);
            file.bfSize = file.bfOffBits + desc.Width * desc.Height * 4;
            info.biSize = sizeof(info); info.biWidth = desc.Width; info.biHeight = -static_cast<LONG>(desc.Height);
            info.biPlanes = 1; info.biBitCount = 32; info.biCompression = BI_RGB;
            std::ofstream output(path, std::ios::binary);
            output.write(reinterpret_cast<char*>(&file), sizeof(file)); output.write(reinterpret_cast<char*>(&info), sizeof(info));
            for (UINT row = 0; row < desc.Height; ++row)
                output.write(static_cast<char*>(pixels.pBits) + row * pixels.Pitch, desc.Width * 4);
            copy->UnlockRect();
        }
    }
    if (copy) copy->Release();
    back->Release();
}

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
    auto visibility = reinterpret_cast<Probe>(GetProcAddress(library, "AmazingOverlayVisibilityForTest"));
    using SettingsPreview = void(__cdecl*)(int, int);
    auto settingsPreview = reinterpret_cast<SettingsPreview>(GetProcAddress(library, "AmazingOverlaySettingsForTest"));
    auto notebookPreview = reinterpret_cast<Show>(GetProcAddress(library, "AmazingOverlayNotebookForTest"));
    bool previewedNotebook=false;
    bool previewedSettings = false;
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
        if (selfTest && frames >= 249 && probe() == 3u) Capture(device);
        device->Present(nullptr, nullptr, nullptr, nullptr);
        if (selfTest) {
            if (probe() & 1u) show();
            if(!previewedNotebook && notebookPreview && probe()==3u && wcsstr(command,L"--notebook")){
                notebookPreview();previewedNotebook=true;
            }
            if (!previewedSettings && settingsPreview && probe() == 3u && wcsstr(command, L"--settings")) {
                settingsPreview(wcsstr(command, L"--large") ? 26 : 17, wcsstr(command, L"--light") ? 1 : 0);
                previewedSettings = true;
            }
            if (++frames >= 250 && probe() == 3u) {
                if (!visibility) return 6;
                for (int cycle = 0; cycle < 100; ++cycle) {
                    // Loss/restoration of focus must never turn Hidden into View.
                    SendMessageW(window, WM_ACTIVATEAPP, FALSE, 0);
                    SendMessageW(window, WM_ACTIVATEAPP, TRUE, 0);
                    if (visibility() != 0) return 7;
                    show();
                    SendMessageW(window, WM_SIZE, SIZE_MINIMIZED, 0);
                    SendMessageW(window, WM_SIZE, SIZE_RESTORED, MAKELPARAM(1280, 800));
                    if (visibility() != 0) return 8;
                }
                device->Release(); d3d->Release(); return 0;
            }
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
