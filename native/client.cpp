#include "rpc.h"
#include "input_policy.h"
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
#include <sstream>
#include <iomanip>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND, UINT, WPARAM, LPARAM);

namespace {
enum class Mode { Hidden, View, Input };
using overlay_input::Binding;
using overlay_input::ParseBinding;
struct State {
    std::mutex mutex, queueMutex;
    std::condition_variable wake;
    std::deque<std::function<void()>> queue;
    std::string status = "Подключение к бэкенду…", answer, job;
    Json documents = Json::array(), hits = Json::array(), sources = Json::array(), history = Json::array();
    Json preferences = Json::object();
    std::shared_ptr<const Json> document;
    Binding toggle{'L', true, false, false}, input{'F', false, true, false};
    Binding previousLaw{VK_LEFT, false, true, false}, nextLaw{VK_RIGHT, false, true, false};
    Binding scrollUp{VK_UP, false, true, false}, scrollDown{VK_DOWN, false, true, false};
    std::string selectedId;
    unsigned documentGeneration = 0;
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
using SetCursorPosFn = BOOL(WINAPI*)(int, int);
using ClipCursorFn = BOOL(WINAPI*)(const RECT*);
SetCursorPosFn originalSetCursorPos = nullptr;
ClipCursorFn originalClipCursor = nullptr;
EndSceneFn originalEnd = nullptr;
ResetFn originalReset = nullptr;
DeviceStateFn originalState = nullptr;
DeviceDataFn originalData = nullptr;
DeviceStateFn originalMouseState = nullptr;
DeviceDataFn originalMouseData = nullptr;
std::vector<void*> ownedHooks;

std::wstring DataDirectory() {
    wchar_t value[32768]{};
    DWORD length = GetEnvironmentVariableW(L"AMAZING_OVERLAY_DATA", value, 32768);
    if (length && length < 32768) return value;
    length = GetEnvironmentVariableW(L"LOCALAPPDATA", value, 32768);
    if (!length || length >= 32768) return L"";
    std::wstring path = std::wstring(value) + L"\\AmazingLawOverlay";
    CreateDirectoryW(path.c_str(), nullptr);
    path += L"\\v2";
    CreateDirectoryW(path.c_str(), nullptr);
    return path;
}

void Log(const std::string& text) {
    static auto* mutex = new std::mutex();
    std::lock_guard<std::mutex> lock(*mutex);
    auto directory = DataDirectory();
    if (directory.empty()) return;
    auto path = directory + L"\\native-client-" + std::to_wstring(GetCurrentProcessId()) + L".log";
    HANDLE file = CreateFileW(path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME time{}; GetLocalTime(&time);
    char stamp[64]{};
    sprintf_s(stamp, "%04u-%02u-%02u %02u:%02u:%02u.%03u ", time.wYear, time.wMonth, time.wDay,
              time.wHour, time.wMinute, time.wSecond, time.wMilliseconds);
    std::string line = std::string(stamp) + text + "\r\n";
    DWORD written = 0;
    WriteFile(file, line.data(), static_cast<DWORD>(line.size()), &written, nullptr);
    CloseHandle(file);
}

std::string HResult(HRESULT result) {
    std::ostringstream out;
    out << "0x" << std::hex << std::uppercase << std::setw(8) << std::setfill('0') << static_cast<unsigned long>(result);
    return out.str();
}

void Status(const std::string& text) {
    { std::lock_guard<std::mutex> lock(S().mutex); S().status = text; }
    Log(text);
}
void Enqueue(std::function<void()> work) {
    auto& state = S();
    std::lock_guard<std::mutex> lock(state.queueMutex);
    if (state.queue.size() < 16) { state.queue.push_back(std::move(work)); state.wake.notify_one(); }
}

bool Down(int key) { return (GetAsyncKeyState(key) & 0x8000) != 0; }
bool Pressed(const Binding& binding) {
    return Down(binding.key) && Down(VK_CONTROL) == binding.ctrl && Down(VK_MENU) == binding.alt && Down(VK_SHIFT) == binding.shift && (Down(VK_LWIN) || Down(VK_RWIN)) == binding.win;
}

void SetMode(Mode next, bool restoreClip = true) {
    Mode old = mode.exchange(next);
    if (old == next) return;
    Log(std::string("Mode: ") + (next == Mode::Input ? "input" : next == Mode::View ? "view" : "hidden"));
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
    unsigned generation;
    {
        std::lock_guard<std::mutex> lock(S().mutex);
        S().selectedId = id;
        generation = ++S().documentGeneration;
    }
    Enqueue([id, generation] {
        auto result = Rpc("document", {{"id", id}});
        auto document = std::make_shared<const Json>(result.at("document"));
        Json preferences;
        {
            std::lock_guard<std::mutex> lock(S().mutex);
            if (generation != S().documentGeneration) return;
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
            S().previousLaw = ParseBinding(settings.value("prev_law_hotkey", "Alt+Left"), S().previousLaw);
            S().nextLaw = ParseBinding(settings.value("next_law_hotkey", "Alt+Right"), S().nextLaw);
            S().scrollUp = ParseBinding(settings.value("scroll_up_hotkey", "Alt+Up"), S().scrollUp);
            S().scrollDown = ParseBinding(settings.value("scroll_down_hotkey", "Alt+Down"), S().scrollDown);
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

BOOL WINAPI CursorPosition(int x, int y) {
    if (overlay_input::SuppressCursor(mode == Mode::Input, GetForegroundWindow() == gameWindow)) return TRUE;
    return originalSetCursorPos(x, y);
}

BOOL WINAPI CursorClip(const RECT* rectangle) {
    if (overlay_input::SuppressClip(mode == Mode::Input, GetForegroundWindow() == gameWindow, rectangle != nullptr)) return TRUE;
    return originalClipCursor(rectangle);
}

void Draw() {
    static bool showList = false, showChat = false, previousToggle = false, previousInput = false, previousEscape = false;
    static char search[512]{}, question[8192]{};
    static std::string sentSearch;
    static DocumentSearch readerSearch;
    static bool previousFindNext = false;
    static ULONGLONG searchChanged = 0;
    static bool restored = false;
    static bool previousLeft = false, previousRight = false;
    static bool previousUp = false, previousDown = false;
    static ULONGLONG lastScrollRepeat = 0;
    static std::string renderedId;
    auto& state = S();
    Binding toggle, input, previousLaw, nextLaw, scrollUp, scrollDown;
    Json documents, hits, sources;
    std::string status, answer, selectedId;
    bool busy, searching;
    std::shared_ptr<const Json> document;
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        toggle = state.toggle; input = state.input;
        previousLaw = state.previousLaw; nextLaw = state.nextLaw;
        scrollUp = state.scrollUp; scrollDown = state.scrollDown;
        selectedId = state.selectedId;
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
    bool left = focused && Pressed(previousLaw), right = focused && Pressed(nextLaw);
    bool up = focused && Pressed(scrollUp), down = focused && Pressed(scrollDown);
    if (mode != Mode::Hidden && ((left && !previousLeft) || (right && !previousRight))) {
        std::vector<std::string> ids;
        for (const auto& item : searching ? hits : documents) ids.push_back(item.value(searching ? "doc_id" : "id", ""));
        auto target = overlay_input::NextDocument(ids, selectedId, right && !previousRight ? 1 : -1);
        if (!target.empty() && target != selectedId) LoadDocument(target);
    }
    float scrollDelta = 0;
    const auto ticks = GetTickCount64();
    if (mode != Mode::Hidden && (up || down) && ((!previousUp && up) || (!previousDown && down) || ticks - lastScrollRepeat >= 100)) {
        scrollDelta = down ? 100.0f : -100.0f;
        lastScrollRepeat = ticks;
    }
    previousLeft = left; previousRight = right; previousUp = up; previousDown = down;
    if (!focused && mode == Mode::Input) SetMode(Mode::View, false);
    if (mode == Mode::Hidden) return;
    if (mode == Mode::Input) ClipCursor(nullptr);
    ImGui::SetNextWindowSizeConstraints(ImVec2(620, 400), ImVec2(3000, 2000));
    ImGui::SetNextWindowSize(ImVec2(1100, 720), ImGuiCond_FirstUseEver);
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
    if (ImGui::Button(showList ? "Законы: открыты" : "Законы", ImVec2(160, 36))) { showList = !showList; savePanels(); }
    ImGui::SameLine(); if (ImGui::Button(showChat ? "ИИ: открыт" : "ИИ-помощник", ImVec2(145, 36))) { showChat = !showChat; savePanels(); }
    ImGui::SameLine(); if (ImGui::Button("Обновить", ImVec2(110, 36))) Refresh();
    ImGui::SameLine(); if (ImGui::Button("Скрыть", ImVec2(95, 36))) SetMode(Mode::Hidden);
    ImGui::TextDisabled("%s", mode == Mode::Input ? "Ввод / Esc: просмотр" : "Просмотр / Alt+F: ввод");
    ImGui::Spacing();
    ImGui::SetNextItemWidth(std::max(150.0f, ImGui::GetContentRegionAvail().x - 285));
    if (ImGui::InputTextWithHint("##search", "Найти в законах…", search, sizeof(search))) searchChanged = GetTickCount64();
    const bool searchFocused = ImGui::IsItemActive();
    if (readerSearch.query != search) {
        readerSearch.query = search; readerSearch.active = 0; readerSearch.total = 0; readerSearch.jump = true;
    }
    auto stepMatch = [&](int delta) {
        if (readerSearch.total) readerSearch.active = (readerSearch.active + delta + readerSearch.total) % readerSearch.total;
        readerSearch.jump = true;
    };
    ImGui::SameLine();
    if (ImGui::Button("<##match", ImVec2(38, 0))) stepMatch(-1);
    ImGui::SameLine();
    if (ImGui::Button(">##match", ImVec2(38, 0))) stepMatch(1);
    ImGui::SameLine();
    ImGui::TextDisabled("%d / %d", readerSearch.total ? readerSearch.active + 1 : 0, readerSearch.total);
    if (ImGui::IsItemHovered()) ImGui::SetTooltip("Совпадения в открытом законе. F3 / Shift+F3; Enter в поиске.");
    ImGui::SameLine();
    if (ImGui::Button("Сброс")) { search[0] = 0; searchChanged = GetTickCount64(); }
    bool findNext = focused && (Down(VK_F3) || (searchFocused && Down(VK_RETURN)));
    bool findBack = findNext && Down(VK_SHIFT);
    if (findNext && !previousFindNext) stepMatch(findBack ? -1 : 1);
    previousFindNext = findNext;
    ImGui::Spacing();
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
        const float listWidth = std::clamp(ImGui::GetContentRegionAvail().x * .27f, 220.0f, 340.0f);
        ImGui::BeginChild("laws", ImVec2(listWidth, -30), ImGuiChildFlags_Borders);
        ImGui::TextDisabled(searching ? "РЕЗУЛЬТАТЫ ПОИСКА" : "БИБЛИОТЕКА ЗАКОНОВ");
        ImGui::Separator();
        const auto& listing = searching ? hits : documents;
        std::vector<std::string> shown;
        for (size_t index = 0; index < listing.size(); ++index) {
            const auto& item = listing[index];
            auto id = item.value(searching ? "doc_id" : "id", "");
            if (std::find(shown.begin(), shown.end(), id) != shown.end()) continue;
            shown.push_back(id);
            auto label = item.value("title", "");
            for (const std::string prefix : {"ЗАКРЕПЛЕНО", "ИНФОРМАЦИЯ"}) {
                if (label.rfind(prefix, 0) == 0) label.erase(0, prefix.size());
            }
            const auto first = label.find_first_not_of(" \r\n\t");
            if (first != std::string::npos) label.erase(0, first);
            if (label.empty()) label = "Без названия";
            ImGui::PushID(id.c_str());
            const float width = ImGui::GetContentRegionAvail().x;
            const float height = std::max(58.0f, ImGui::CalcTextSize(label.c_str(), nullptr, false, width - 26).y + 24);
            const auto pos = ImGui::GetCursorScreenPos();
            const bool selected = document && document->value("id", "") == id;
            if (ImGui::InvisibleButton("law", ImVec2(width, height))) LoadDocument(id);
            const bool hovered = ImGui::IsItemHovered();
            auto* draw = ImGui::GetWindowDrawList();
            draw->AddRectFilled(pos, ImVec2(pos.x + width, pos.y + height),
                selected ? IM_COL32(35, 64, 91, 255) : hovered ? IM_COL32(31, 44, 61, 255) : IM_COL32(23, 32, 45, 255), 7);
            if (selected) draw->AddRectFilled(pos, ImVec2(pos.x + 3, pos.y + height), IM_COL32(95, 184, 229, 255), 2);
            draw->AddText(nullptr, 0, ImVec2(pos.x + 13, pos.y + 12), IM_COL32(228, 235, 244, 255), label.c_str(), nullptr, width - 26);
            if (hovered) ImGui::SetTooltip("%s", label.c_str());
            ImGui::PopID();
        }
        if (shown.empty()) ImGui::TextWrapped("Ничего не найдено. Попробуйте другое слово.");
        ImGui::EndChild();
        ImGui::SameLine();
    }
    float readerWidth = showChat ? std::max(240.0f, ImGui::GetContentRegionAvail().x - 350) : 0;
    ImGui::BeginChild("reader", ImVec2(readerWidth, -26), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
    const auto docId = document ? document->value("id", "") : "";
    if (docId != renderedId) { ImGui::SetScrollY(0); renderedId = docId; readerSearch.active = 0; readerSearch.jump = true; }
    else if (scrollDelta) ImGui::SetScrollY(std::max(0.0f, ImGui::GetScrollY() + scrollDelta));
    if (document) {
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(.64f, .82f, .94f, 1));
        auto title = document->value("title", "Закон");
        ImGui::TextWrapped("%s", title.c_str());
        ImGui::PopStyleColor();
        ImGui::Separator(); ImGui::Spacing();
        DrawDocument(*document, &readerSearch);
    } else ImGui::TextWrapped("%s", status.c_str());
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
    static std::atomic<bool> first{true};
    if (first.exchange(false)) Log("First EndScene callback received");
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
        auto& style = ImGui::GetStyle();
        style.WindowRounding = 10; style.ChildRounding = 8; style.FrameRounding = 6;
        style.GrabRounding = 5; style.ScrollbarRounding = 6;
        style.WindowPadding = ImVec2(14, 12); style.FramePadding = ImVec2(12, 8);
        style.ItemSpacing = ImVec2(10, 8); style.ScrollbarSize = 13;
        style.Colors[ImGuiCol_WindowBg] = ImVec4(.045f, .065f, .095f, .97f);
        style.Colors[ImGuiCol_ChildBg] = ImVec4(.06f, .085f, .12f, 1);
        style.Colors[ImGuiCol_TitleBg] = style.Colors[ImGuiCol_TitleBgActive] = ImVec4(.08f, .14f, .21f, 1);
        style.Colors[ImGuiCol_Button] = ImVec4(.13f, .22f, .31f, 1);
        style.Colors[ImGuiCol_ButtonHovered] = ImVec4(.19f, .34f, .46f, 1);
        style.Colors[ImGuiCol_FrameBg] = ImVec4(.10f, .15f, .22f, 1);
        style.Colors[ImGuiCol_Border] = ImVec4(.19f, .26f, .34f, .7f);
        style.Colors[ImGuiCol_Text] = ImVec4(.88f, .92f, .96f, 1);
        ImGui_ImplWin32_Init(gameWindow);
        ImGui_ImplDX9_Init(device);
        previousProc = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(gameWindow, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(WindowProc)));
        if (!previousProc) {
            ImGui_ImplDX9_Shutdown(); ImGui_ImplWin32_Shutdown(); ImGui::DestroyContext();
            activeDevice = nullptr;
            return originalEnd(device);
        }
        initialized = true;
        Log("Renderer initialized; Ctrl+L shows panel; Alt+F enables input");
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
    Log("Bootstrap entered; build " __DATE__ " " __TIME__ "; x86 D3D9 client");
    // Pin before installing callbacks: unloading a hooked module is unsafe.
    HMODULE pinned = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                            reinterpret_cast<LPCWSTR>(&Bootstrap), &pinned)) {
        Log("Module is not registered with the Windows loader (Manual Map); caller must keep it mapped while callbacks are installed");
    }
    HINSTANCE module = GetModuleHandleW(nullptr);
    WNDCLASSW klass{}; klass.lpfnWndProc = DefWindowProcW; klass.hInstance = module; klass.lpszClassName = L"AmazingOverlayProbe";
    RegisterClassW(&klass);
    HWND probe = CreateWindowW(klass.lpszClassName, L"", WS_OVERLAPPED, 0, 0, 64, 64, nullptr, nullptr, module, nullptr);
    if (!probe) Log("ERROR: CreateWindow probe failed, Win32=" + std::to_string(GetLastError()));
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
            if (success) {
                Status("Hooking game cursor recenter / clip");
                auto user32 = GetModuleHandleW(L"user32.dll");
                success = user32 && Hook(reinterpret_cast<void*>(GetProcAddress(user32, "SetCursorPos")), reinterpret_cast<void*>(CursorPosition), reinterpret_cast<void**>(&originalSetCursorPos)) &&
                          Hook(reinterpret_cast<void*>(GetProcAddress(user32, "ClipCursor")), reinterpret_cast<void*>(CursorClip), reinterpret_cast<void**>(&originalClipCursor));
            }
            IDirectInput8W* input = nullptr;
            IDirectInputDevice8W* keyboard = nullptr;
            IDirectInputDevice8W* mouse = nullptr;
            if (success) Status("Creating DirectInput probe keyboard");
            HRESULT inputResult = success ? DirectInput8Create(module, DIRECTINPUT_VERSION, IID_IDirectInput8W, reinterpret_cast<void**>(&input), nullptr) : E_FAIL;
            HRESULT keyboardResult = SUCCEEDED(inputResult) ? input->CreateDevice(GUID_SysKeyboard, &keyboard, nullptr) : inputResult;
            if (success && SUCCEEDED(keyboardResult)) {
                Status("Hooking DirectInput state / buffered data");
                auto** keys = *reinterpret_cast<void***>(keyboard);
                success = Hook(keys[9], reinterpret_cast<void*>(GetState), reinterpret_cast<void**>(&originalState)) &&
                          Hook(keys[10], reinterpret_cast<void*>(GetData), reinterpret_cast<void**>(&originalData));
                HRESULT mouseResult = success ? input->CreateDevice(GUID_SysMouse, &mouse, nullptr) : E_FAIL;
                if (success && SUCCEEDED(mouseResult)) {
                    auto** mouseTable = *reinterpret_cast<void***>(mouse);
                    if (mouseTable[9] != keys[9]) success = Hook(mouseTable[9], reinterpret_cast<void*>(GetMouseState), reinterpret_cast<void**>(&originalMouseState));
                    if (success && mouseTable[10] != keys[10]) success = Hook(mouseTable[10], reinterpret_cast<void*>(GetMouseData), reinterpret_cast<void**>(&originalMouseData));
                } else { if (success) Log("ERROR: CreateDevice mouse " + HResult(mouseResult)); success = false; }
            } else { if (success) Log("ERROR: CreateDevice keyboard " + HResult(keyboardResult)); success = false; }
            if (keyboard) keyboard->Release();
            if (mouse) mouse->Release();
            if (input) input->Release();
        } else Status(std::string("MH_Initialize: ") + MH_StatusToString(hookInit));
    } else Status("ERROR: D3D9 probe creation " + HResult(creation) + "; windowed probe can be unavailable while a game owns exclusive fullscreen");
    if (device) device->Release();
    if (d3d) d3d->Release();
    if (probe) DestroyWindow(probe);
    UnregisterClassW(klass.lpszClassName, module);
    if (success) {
        Status("Enabling owned hooks");
        for (void* target : ownedHooks) {
            auto queued = MH_QueueEnableHook(target);
            if (queued != MH_OK) { Log(std::string("ERROR: MH_QueueEnableHook ") + MH_StatusToString(queued)); success = false; }
        }
        if (success) {
            auto applied = MH_ApplyQueued();
            success = applied == MH_OK;
            if (!success) Log(std::string("ERROR: MH_ApplyQueued ") + MH_StatusToString(applied));
        }
    }
    if (!success) {
        for (void* target : ownedHooks) { MH_DisableHook(target); MH_RemoveHook(target); }
        OutputDebugStringW(L"AmazingLawClient: initialization failed; owned hooks removed.\n");
        Log("Initialization FAILED; only this client's hooks removed");
        return 1;
    }
    std::thread(IoThread).detach();
    Refresh();
    Log("Initialization complete; waiting for game render callback and backend");
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
