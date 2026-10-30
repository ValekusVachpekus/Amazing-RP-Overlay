#include "rpc.h"
#include <windows.h>
#include <d3d9.h>
#include <dinput.h>
#include <shellapi.h>
#include <MinHook.h>
#include <imgui.h>
#include <imgui_impl_dx9.h>
#include <imgui_impl_win32.h>
#include <atomic>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace {
enum class Mode { Hidden, View, Input };
struct Binding { int key; bool ctrl, alt, shift, win = false; };
struct State {
    std::mutex mutex, queueMutex;
    std::condition_variable wake;
    std::deque<std::function<void()>> queue;
    std::string status = "Подключение к бэкенду…", answer, job;
    Json documents = Json::array(), hits = Json::array(), sources = Json::array(), history = Json::array();
    Json preferences = Json::object();
    std::shared_ptr<const Json> document;
    Binding toggle{'L', true, false, false}, input{'F', false, true, false};
    bool searching = false, busy = false;
    bool preferencesLoaded = false;
    unsigned searchGeneration = 0;
};
// Process-lifetime object: DLL is pinned, no unsafe unload of active callbacks.
State& S() { static auto* state = new State(); return *state; }
std::atomic<Mode> mode{Mode::Hidden};
std::atomic<ULONGLONG> suppressUntil{0};
HWND gameWindow = nullptr;
WNDPROC previousProc = nullptr;
IDirect3DDevice9* activeDevice = nullptr;
bool initialized = false;
RECT previousClip{};
using EndSceneFn = HRESULT(WINAPI*)(IDirect3DDevice9*);
using ResetFn = HRESULT(WINAPI*)(IDirect3DDevice9*, D3DPRESENT_PARAMETERS*);
using DeviceStateFn = HRESULT(WINAPI*)(IDirectInputDevice8W*, DWORD, LPVOID);
using DeviceDataFn = HRESULT(WINAPI*)(IDirectInputDevice8W*, DWORD, LPDIDEVICEOBJECTDATA, LPDWORD, DWORD);
EndSceneFn originalEnd = nullptr;
ResetFn originalReset = nullptr;
DeviceStateFn originalState = nullptr;
DeviceDataFn originalData = nullptr;
DeviceStateFn originalMouseState = nullptr;
DeviceDataFn originalMouseData = nullptr;
std::vector<void*> ownedHooks;

void Status(const std::string& text) { std::lock_guard<std::mutex> lock(S().mutex); S().status = text; }
void Enqueue(std::function<void()> work) {
    auto& state = S();
    std::lock_guard<std::mutex> lock(state.queueMutex);
    if (state.queue.size() < 16) { state.queue.push_back(std::move(work)); state.wake.notify_one(); }
}

Binding ParseBinding(std::string value, Binding fallback) {
    value.erase(std::remove(value.begin(), value.end(), ' '), value.end());
    for (char& c : value) if (c >= 'a' && c <= 'z') c -= 32;
    auto at = value.rfind('+');
    auto key = at == std::string::npos ? value : value.substr(at + 1);
    int code = 0;
    if (key.size() == 1) code = key[0];
    else if (key.size() > 1 && key[0] == 'F') {
        try { int n = std::stoi(key.substr(1)); if (n >= 1 && n <= 24) code = VK_F1 + n - 1; } catch (...) {}
    }
    if (!code) return fallback;
    return {code, value.find("CTRL+") != std::string::npos, value.find("ALT+") != std::string::npos, value.find("SHIFT+") != std::string::npos, value.find("WIN+") != std::string::npos};
}

bool Down(int key) { return (GetAsyncKeyState(key) & 0x8000) != 0; }
bool Pressed(const Binding& binding) {
    return Down(binding.key) && Down(VK_CONTROL) == binding.ctrl && Down(VK_MENU) == binding.alt && Down(VK_SHIFT) == binding.shift && (Down(VK_LWIN) || Down(VK_RWIN)) == binding.win;
}

void SetMode(Mode next, bool restoreClip = true) {
    Mode old = mode.exchange(next);
    if (old == next) return;
    if (next == Mode::Input) { GetClipCursor(&previousClip); ClipCursor(nullptr); }
    else if (old == Mode::Input && restoreClip && GetForegroundWindow() == gameWindow) ClipCursor(&previousClip);
    // Drain releases before sending game input again.
    suppressUntil = GetTickCount64() + 180;
    if (initialized) {
        auto& io = ImGui::GetIO();
        io.ClearInputKeys();
        io.ClearInputMouse();
        io.MouseDrawCursor = next == Mode::Input;
    }
}

void LoadDocument(std::string id) {
    Enqueue([id] {
        auto result = Rpc("document", {{"id", id}});
        auto document = std::make_shared<const Json>(result.at("document"));
        Json preferences;
        {
            std::lock_guard<std::mutex> lock(S().mutex);
            S().document = document;
            S().status = document->value("title", "");
            S().preferences["document"] = id;
            preferences = S().preferences;
        }
        Rpc("state_set", {{"key", "native"}, {"value", preferences}});
    });
}

void IoThread() {
    auto& state = S();
    for (;;) {
        try {
            std::function<void()> work;
            {
                std::unique_lock<std::mutex> lock(state.queueMutex);
                state.wake.wait_for(lock, std::chrono::milliseconds(350), [&] { return !state.queue.empty(); });
                if (!state.queue.empty()) { work = std::move(state.queue.front()); state.queue.pop_front(); }
            }
            if (work) work();
            std::string job;
            { std::lock_guard<std::mutex> lock(state.mutex); job = state.job; }
            if (!job.empty()) {
                auto result = Rpc("job_status", {{"id", job}});
                std::lock_guard<std::mutex> lock(state.mutex);
                auto progress = result.value("progress", Json::object());
                if (progress.contains("answer")) state.answer = progress["answer"].get<std::string>();
                if (progress.contains("sources")) state.sources = progress["sources"];
                if (progress.contains("message")) state.status = progress["message"].get<std::string>();
                auto status = result.value("state", "error");
                if (status != "running") {
                    if (status == "done") {
                        auto answer = result.at("result");
                        state.answer = answer.value("answer", "");
                        state.sources = answer.value("sources", Json::array());
                        state.history.push_back({{"role", "assistant"}, {"content", state.answer}});
                        state.status = "Ответ получен";
                    } else state.status = result.value("error", "Запрос отменён");
                    state.busy = false;
                    state.job.clear();
                }
            }
        } catch (const std::exception& error) {
            Status(error.what());
            std::lock_guard<std::mutex> lock(state.mutex);
            state.busy = false;
            state.job.clear();
        }
    }
}

void Refresh() {
    Enqueue([] {
        auto documents = Rpc("documents");
        auto settings = Rpc("settings");
        auto preferences = Rpc("state_get", {{"key", "native"}, {"default", Json::object()}});
        std::string first;
        {
            std::lock_guard<std::mutex> lock(S().mutex);
            S().documents = documents;
            S().preferences = preferences;
            S().preferencesLoaded = true;
            S().toggle = ParseBinding(settings.value("toggle_hotkey", "Ctrl+L"), S().toggle);
            S().input = ParseBinding(settings.value("input_hotkey", "Alt+F"), S().input);
            S().status = "Бэкенд подключён";
            if (!S().document && !documents.empty()) {
                first = documents[0].value("id", "");
                auto saved = preferences.value("document", "");
                for (const auto& item : documents) if (item.value("id", "") == saved) first = saved;
            }
        }
        if (!first.empty()) LoadDocument(first);
    });
}

LRESULT CALLBACK WindowProc(HWND window, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == WM_ACTIVATEAPP && !wParam) SetMode(Mode::View, false);
    if (initialized && mode == Mode::Input) {
        ImGui_ImplWin32_WndProcHandler(window, msg, wParam, lParam);
        if ((msg >= WM_KEYFIRST && msg <= WM_KEYLAST) ||
            (msg >= WM_MOUSEFIRST && msg <= WM_MOUSELAST) || msg == WM_INPUT || msg == WM_SETCURSOR) {
            if (msg == WM_INPUT) return DefWindowProcW(window, msg, wParam, lParam);
            return msg == WM_SETCURSOR ? TRUE : 0;
        }
    }
    return CallWindowProcW(previousProc, window, msg, wParam, lParam);
}

HRESULT WINAPI GetState(IDirectInputDevice8W* device, DWORD size, LPVOID buffer) {
    HRESULT result = originalState(device, size, buffer);
    if (SUCCEEDED(result) && buffer && (mode == Mode::Input || GetTickCount64() < suppressUntil.load())) ZeroMemory(buffer, size);
    return result;
}

HRESULT WINAPI GetData(IDirectInputDevice8W* device, DWORD size, LPDIDEVICEOBJECTDATA buffer, LPDWORD count, DWORD flags) {
    HRESULT result = originalData(device, size, buffer, count, flags);
    if (SUCCEEDED(result) && count && (mode == Mode::Input || GetTickCount64() < suppressUntil.load())) *count = 0;
    return result;
}

HRESULT WINAPI GetMouseState(IDirectInputDevice8W* device, DWORD size, LPVOID buffer) {
    HRESULT result = originalMouseState(device, size, buffer);
    if (SUCCEEDED(result) && buffer && (mode == Mode::Input || GetTickCount64() < suppressUntil.load())) ZeroMemory(buffer, size);
    return result;
}

HRESULT WINAPI GetMouseData(IDirectInputDevice8W* device, DWORD size, LPDIDEVICEOBJECTDATA buffer, LPDWORD count, DWORD flags) {
    HRESULT result = originalMouseData(device, size, buffer, count, flags);
    if (SUCCEEDED(result) && count && (mode == Mode::Input || GetTickCount64() < suppressUntil.load())) *count = 0;
    return result;
}

void Draw() {
    static bool showList = false, showChat = false, previousToggle = false, previousInput = false, previousEscape = false;
    static char search[512]{}, question[8192]{};
    static std::string sentSearch;
    static ULONGLONG searchChanged = 0;
    static bool restored = false;
    auto& state = S();
    Binding toggle, input;
    Json documents, hits, sources;
    std::string status, answer;
    bool busy, searching;
    std::shared_ptr<const Json> document;
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        toggle = state.toggle; input = state.input;
        documents = state.documents; hits = state.hits; sources = state.sources;
        status = state.status; answer = state.answer; busy = state.busy; searching = state.searching; document = state.document;
        if (!restored && state.preferencesLoaded) {
            showList = state.preferences.value("left", false);
            showChat = state.preferences.value("chat", false);
            restored = true;
        }
    }
    bool focused = GetForegroundWindow() == gameWindow;
    bool t = focused && Pressed(toggle), i = focused && Pressed(input), e = focused && Down(VK_ESCAPE);
    if (t && !previousToggle) SetMode(mode == Mode::Hidden ? Mode::View : Mode::Hidden);
    if (i && !previousInput) SetMode(mode == Mode::Input ? Mode::View : Mode::Input);
    if (e && !previousEscape && mode != Mode::Hidden) SetMode(mode == Mode::Input ? Mode::View : Mode::Hidden);
    previousToggle = t; previousInput = i; previousEscape = e;
    if (!focused && mode == Mode::Input) SetMode(Mode::View, false);
    if (mode == Mode::Hidden) return;
    if (mode == Mode::Input) ClipCursor(nullptr);
    ImGui::SetNextWindowSize(ImVec2(650, 520), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowPos(ImVec2(60, 80), ImGuiCond_FirstUseEver);
    ImGuiWindowFlags flags = mode == Mode::Input ? 0 : ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoMove;
    ImGui::Begin("AMAZING / ЗАКОНЫ", nullptr, flags);
    auto savePanels = [&] {
        const bool listValue = showList, chatValue = showChat;
        Enqueue([listValue, chatValue] {
            Json prefs;
            { std::lock_guard<std::mutex> lock(S().mutex); S().preferences["left"] = listValue; S().preferences["chat"] = chatValue; prefs = S().preferences; }
            Rpc("state_set", {{"key", "native"}, {"value", prefs}});
        });
    };
    if (ImGui::Button("Список")) { showList = !showList; savePanels(); }
    ImGui::SameLine(); if (ImGui::Button("Чат")) { showChat = !showChat; savePanels(); }
    ImGui::SameLine(); if (ImGui::Button("Обновить")) Refresh();
    ImGui::SameLine(); if (ImGui::Button("Скрыть")) SetMode(Mode::Hidden);
    ImGui::TextDisabled("%s", mode == Mode::Input ? "Ввод / Esc: просмотр" : "Просмотр / Alt+F: ввод");
    if (ImGui::InputTextWithHint("##search", "Поиск по всей базе…", search, sizeof(search))) searchChanged = GetTickCount64();
    if (sentSearch != search && GetTickCount64() - searchChanged >= 150) {
        std::string query(search);
        sentSearch = query;
        unsigned generation;
        { std::lock_guard<std::mutex> lock(state.mutex); generation = ++state.searchGeneration; }
        Enqueue([query, generation] {
            auto result = query.empty() ? Json::array() : Rpc("search", {{"query", query}});
            std::lock_guard<std::mutex> lock(S().mutex);
            if (generation == S().searchGeneration) { S().hits = result; S().searching = !query.empty(); }
        });
        if (!query.empty()) showList = true;
    }
    if (showList) {
        ImGui::BeginChild("laws", ImVec2(240, -26), ImGuiChildFlags_Borders);
        const auto& listing = searching ? hits : documents;
        for (size_t index = 0; index < listing.size(); ++index) {
            const auto& item = listing[index];
            auto id = item.value(searching ? "doc_id" : "id", "");
            auto label = item.value("title", "") + (searching ? "\n" + item.value("heading", "") : "");
            ImGui::PushID(static_cast<int>(index));
            if (ImGui::Selectable(label.c_str(), document && document->value("id", "") == id)) LoadDocument(id);
            ImGui::PopID();
        }
        ImGui::EndChild();
        ImGui::SameLine();
    }
    float readerWidth = showChat ? std::max(240.0f, ImGui::GetContentRegionAvail().x - 350) : 0;
    ImGui::BeginChild("reader", ImVec2(readerWidth, -26), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
    if (document) DrawDocument(*document); else ImGui::TextWrapped("%s", status.c_str());
    ImGui::EndChild();
    if (showChat) {
        ImGui::SameLine();
        ImGui::BeginChild("chat", ImVec2(0, -26), ImGuiChildFlags_Borders);
        static bool onlyCurrent = false;
        ImGui::Checkbox("Только текущий закон", &onlyCurrent);
        ImGui::InputTextMultiline("##question", question, sizeof(question), ImVec2(-1, 95));
        if (ImGui::Button(busy ? "Ожидание…" : "Отправить") && !busy && question[0]) {
            std::string text(question);
            question[0] = 0;
            Json scope = onlyCurrent && document ? Json::array({document->value("id", "")}) : Json();
            { std::lock_guard<std::mutex> lock(state.mutex); state.busy = true; state.answer.clear(); state.sources = Json::array(); }
            Enqueue([text, scope] {
                Json history;
                { std::lock_guard<std::mutex> lock(S().mutex); history = S().history; }
                auto result = Rpc("ai", {{"question", text}, {"doc_ids", scope}, {"history", history}});
                std::lock_guard<std::mutex> lock(S().mutex);
                S().job = result.at("job_id").get<std::string>();
                S().history.push_back({{"role", "user"}, {"content", text}});
                while (S().history.size() > 20) S().history.erase(S().history.begin());
            });
        }
        ImGui::SameLine();
        if (ImGui::Button("Стоп")) Enqueue([] { std::string job; { std::lock_guard<std::mutex> lock(S().mutex); job = S().job; } if (!job.empty()) Rpc("job_cancel", {{"id", job}}); });
        if (ImGui::Button("Копировать")) ImGui::SetClipboardText(answer.c_str());
        ImGui::Separator();
        ImGui::TextWrapped("%s", answer.c_str());
        for (const auto& source : sources) {
            auto label = "[" + source.value("label", "") + "] " + source.value("heading", "");
            if (ImGui::Selectable(label.c_str())) LoadDocument(source.value("doc_id", ""));
        }
        ImGui::EndChild();
    }
    ImGui::TextDisabled("%s", status.c_str());
    ImGui::End();
}

HRESULT WINAPI EndScene(IDirect3DDevice9* device) {
    if (!initialized) {
        D3DDEVICE_CREATION_PARAMETERS parameters{};
        if (FAILED(device->GetCreationParameters(&parameters)) || !parameters.hFocusWindow) return originalEnd(device);
        gameWindow = parameters.hFocusWindow;
        activeDevice = device;
        ImGui::CreateContext();
        auto& io = ImGui::GetIO();
        wchar_t localData[MAX_PATH]{};
        GetEnvironmentVariableW(L"AMAZING_OVERLAY_DATA", localData, MAX_PATH);
        std::wstring storage;
        if (localData[0]) storage = localData;
        else {
            GetEnvironmentVariableW(L"LOCALAPPDATA", localData, MAX_PATH);
            storage = std::wstring(localData) + L"\\AmazingLawOverlay";
            CreateDirectoryW(storage.c_str(), nullptr);
            storage += L"\\v2";
        }
        CreateDirectoryW(storage.c_str(), nullptr);
        storage += L"\\native-layout.ini";
        int iniSize = WideCharToMultiByte(CP_UTF8, 0, storage.c_str(), -1, nullptr, 0, nullptr, nullptr);
        auto* iniPath = new std::string(iniSize, '\0');
        WideCharToMultiByte(CP_UTF8, 0, storage.c_str(), -1, iniPath->data(), iniSize, nullptr, nullptr);
        io.IniFilename = iniPath->c_str();
        io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
        wchar_t windows[MAX_PATH]; GetWindowsDirectoryW(windows, MAX_PATH);
        std::wstring fontPath = std::wstring(windows) + L"\\Fonts\\segoeui.ttf";
        int needed = WideCharToMultiByte(CP_UTF8, 0, fontPath.c_str(), -1, nullptr, 0, nullptr, nullptr);
        std::string utf8(needed, '\0');
        WideCharToMultiByte(CP_UTF8, 0, fontPath.c_str(), -1, utf8.data(), needed, nullptr, nullptr);
        io.Fonts->AddFontFromFileTTF(utf8.c_str(), 17, nullptr, io.Fonts->GetGlyphRangesCyrillic());
        ImGui::StyleColorsDark();
        ImGui::GetStyle().WindowRounding = 9;
        ImGui_ImplWin32_Init(gameWindow);
        ImGui_ImplDX9_Init(device);
        previousProc = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(gameWindow, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(WindowProc)));
        if (!previousProc) {
            ImGui_ImplDX9_Shutdown(); ImGui_ImplWin32_Shutdown(); ImGui::DestroyContext();
            activeDevice = nullptr;
            return originalEnd(device);
        }
        initialized = true;
    }
    if (device == activeDevice) {
        try {
            ImGui_ImplDX9_NewFrame(); ImGui_ImplWin32_NewFrame(); ImGui::NewFrame();
            Draw();
            ImGui::EndFrame(); ImGui::Render();
            ImGui_ImplDX9_RenderDrawData(ImGui::GetDrawData());
        } catch (const std::exception& error) { Status(error.what()); SetMode(Mode::Hidden); }
    }
    return originalEnd(device);
}

HRESULT WINAPI Reset(IDirect3DDevice9* device, D3DPRESENT_PARAMETERS* parameters) {
    if (initialized && device == activeDevice) ImGui_ImplDX9_InvalidateDeviceObjects();
    HRESULT result = originalReset(device, parameters);
    if (SUCCEEDED(result) && initialized && device == activeDevice) ImGui_ImplDX9_CreateDeviceObjects();
    return result;
}

bool Hook(void* target, void* replacement, void** original) {
    auto result = MH_CreateHook(target, replacement, original);
    if (result != MH_OK) { Status(std::string("MH_CreateHook: ") + MH_StatusToString(result)); return false; }
    ownedHooks.push_back(target);
    return true;
}

DWORD WINAPI Bootstrap(void*) {
    // Pin before installing callbacks: unloading a hooked module is unsafe.
    HMODULE pinned = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                       reinterpret_cast<LPCWSTR>(&Bootstrap), &pinned);
    HINSTANCE module = GetModuleHandleW(nullptr);
    WNDCLASSW klass{}; klass.lpfnWndProc = DefWindowProcW; klass.hInstance = module; klass.lpszClassName = L"AmazingOverlayProbe";
    RegisterClassW(&klass);
    HWND probe = CreateWindowW(klass.lpszClassName, L"", WS_OVERLAPPED, 0, 0, 16, 16, nullptr, nullptr, module, nullptr);
    auto* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    IDirect3DDevice9* device = nullptr;
    D3DPRESENT_PARAMETERS parameters{}; parameters.Windowed = TRUE; parameters.SwapEffect = D3DSWAPEFFECT_DISCARD; parameters.hDeviceWindow = probe;
    parameters.BackBufferWidth = 64;
    parameters.BackBufferHeight = 64;
    parameters.BackBufferFormat = D3DFMT_X8R8G8B8;
    bool success = false;
    Status("Creating D3D9 probe device");
    HRESULT creation = probe && d3d ? d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, probe, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &parameters, &device) : E_FAIL;
    if (SUCCEEDED(creation)) {
        auto** table = *reinterpret_cast<void***>(device);
        auto hookInit = MH_Initialize();
        if (hookInit == MH_OK) {
            Status("Hooking EndScene / Reset");
            success = Hook(table[42], reinterpret_cast<void*>(EndScene), reinterpret_cast<void**>(&originalEnd)) &&
                      Hook(table[16], reinterpret_cast<void*>(Reset), reinterpret_cast<void**>(&originalReset));
            IDirectInput8W* input = nullptr;
            IDirectInputDevice8W* keyboard = nullptr;
            IDirectInputDevice8W* mouse = nullptr;
            if (success) Status("Creating DirectInput probe keyboard");
            if (success && SUCCEEDED(DirectInput8Create(module, DIRECTINPUT_VERSION, IID_IDirectInput8W, reinterpret_cast<void**>(&input), nullptr)) &&
                SUCCEEDED(input->CreateDevice(GUID_SysKeyboard, &keyboard, nullptr))) {
                Status("Hooking DirectInput state / buffered data");
                auto** keys = *reinterpret_cast<void***>(keyboard);
                success = Hook(keys[9], reinterpret_cast<void*>(GetState), reinterpret_cast<void**>(&originalState)) &&
                          Hook(keys[10], reinterpret_cast<void*>(GetData), reinterpret_cast<void**>(&originalData));
                if (success && SUCCEEDED(input->CreateDevice(GUID_SysMouse, &mouse, nullptr))) {
                    auto** mouseTable = *reinterpret_cast<void***>(mouse);
                    if (mouseTable[9] != keys[9]) success = Hook(mouseTable[9], reinterpret_cast<void*>(GetMouseState), reinterpret_cast<void**>(&originalMouseState));
                    if (success && mouseTable[10] != keys[10]) success = Hook(mouseTable[10], reinterpret_cast<void*>(GetMouseData), reinterpret_cast<void**>(&originalMouseData));
                } else success = false;
            } else success = false;
            if (keyboard) keyboard->Release();
            if (mouse) mouse->Release();
            if (input) input->Release();
        } else Status(std::string("MH_Initialize: ") + MH_StatusToString(hookInit));
    } else Status("D3D9 probe creation HRESULT=" + std::to_string(static_cast<unsigned long>(creation)));
    if (device) device->Release();
    if (d3d) d3d->Release();
    if (probe) DestroyWindow(probe);
    UnregisterClassW(klass.lpszClassName, module);
    if (success) {
        Status("Enabling owned hooks");
        for (void* target : ownedHooks) if (MH_QueueEnableHook(target) != MH_OK) success = false;
        if (success) success = MH_ApplyQueued() == MH_OK;
    }
    if (!success) {
        for (void* target : ownedHooks) { MH_DisableHook(target); MH_RemoveHook(target); }
        OutputDebugStringW(L"AmazingLawClient: initialization failed; owned hooks removed.\n");
        return 1;
    }
    std::thread(IoThread).detach();
    Refresh();
    return 0;
}
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread = CreateThread(nullptr, 0, Bootstrap, nullptr, 0, nullptr);
        if (thread) CloseHandle(thread);
        else return FALSE;
    }
    return TRUE;
}

// Diagnostics for the standalone test host, not a game loader or injector.
extern "C" __declspec(dllexport) unsigned __cdecl AmazingOverlayProbe() {
    std::lock_guard<std::mutex> lock(S().mutex);
    return (initialized ? 1u : 0u) | (!S().documents.empty() ? 2u : 0u);
}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlayShowForTest() {
    SetMode(Mode::View);
}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlayDiagnostics(char* buffer, unsigned size) {
    std::lock_guard<std::mutex> lock(S().mutex);
    if (size) strncpy_s(buffer, size, S().status.c_str(), _TRUNCATE);
}
