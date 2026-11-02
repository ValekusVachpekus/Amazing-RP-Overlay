#include "rpc.h"
#include "input_policy.h"
#include "display_text.h"
#include "settings_panel.h"
#include "notebook_panel.h"
#include "navigation.h"
#include "desktop_ui.h"
#include <set>
#include <unordered_map>
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
    Json preferences = Json::object(), settings = Json::object();
    unsigned settingsRevision = 0;
    bool settingsPending = false, settingsSaved = false;
    std::string settingsMessage, testJob;
    std::shared_ptr<const Json> document;
    Binding toggle{'L', true, false, false}, input{'F', false, true, false};
    Binding notes{'N',true,false,false};
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
SettingsPanel settingsPanel;
TabNavigation navigation;
constexpr wchar_t NativeInputOwner[]=L"AmazingLawOverlay.NativeInputOwner.v1";
constexpr UINT OpenNotebookMessage=WM_APP+0x416;
std::atomic<bool> openNotebookRequested{false};
std::atomic<int> designPreview{-1}; // Standalone D3D9 host only; never injects.
// Counts and bounds from the actual rendered frame, for the standalone host.
unsigned visibleLawRows=0,selectedLawRows=0,libraryButtonState=0;
float contentTop=0,contentBottom=0,windowBottom=0,hintBottom=0;
notebook::NotebookPanel& Notebook() { static auto* value=new notebook::NotebookPanel();return *value; }
std::atomic<bool> flushNotebook{false};
std::array<ImFont*, 13> uiFonts{};
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
bool Enqueue(std::function<void()> work) {
    auto& state = S();
    std::lock_guard<std::mutex> lock(state.queueMutex);
    if (state.queue.size() < 16) { state.queue.push_back(std::move(work)); state.wake.notify_one(); return true; }
    return false;
}

bool Down(int key) { return (GetAsyncKeyState(key) & 0x8000) != 0; }
bool Pressed(const Binding& binding) {
    return Down(binding.key) && Down(VK_CONTROL) == binding.ctrl && Down(VK_MENU) == binding.alt && Down(VK_SHIFT) == binding.shift && (Down(VK_LWIN) || Down(VK_RWIN)) == binding.win;
}

void SelectTab(MainTab tab, bool programmatic=true) {
    auto& notebook=Notebook();
    if(navigation.active==MainTab::Notebook && tab!=MainTab::Notebook)notebook.Flush();
    navigation.Select(tab,programmatic);
    notebook.open=tab==MainTab::Notebook;
    if(tab==MainTab::Notebook)notebook.Open();
    if(tab==MainTab::Settings && !settingsPanel.open)settingsPanel.Open();
}

void SetMode(Mode next, bool restoreClip = true) {
    Mode old = mode.exchange(next);
    if (old == next) return;
    if (old == Mode::Input && next != Mode::Input) flushNotebook=true;
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
            std::string testJob;
            { std::lock_guard<std::mutex> lock(state.mutex); testJob = state.testJob; }
            if (!testJob.empty()) {
                auto result = Rpc("job_status", {{"id", testJob}});
                if (result.value("state", "error") != "running") {
                    std::lock_guard<std::mutex> lock(state.mutex);
                    if (result.value("state", "error") == "done") {
                        const auto& value = result.at("result");
                        state.settingsMessage = "Соединение работает. Провайдер: " + value.value("provider", "") + " / Модель: " + value.value("model", "");
                    } else state.settingsMessage = result.value("error", "Проверка отменена");
                    state.testJob.clear();
                }
            }
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
            state.testJob.clear(); state.settingsPending = false;
            state.settingsMessage = error.what();
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
            S().settings = settings; ++S().settingsRevision;
            S().toggle = ParseBinding(settings.value("toggle_hotkey", "Ctrl+L"), S().toggle);
            S().input = ParseBinding(settings.value("input_hotkey", "Alt+F"), S().input);
            S().notes = ParseBinding(settings.value("notes_hotkey", "Ctrl+N"), S().notes);
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
    if (msg == OpenNotebookMessage && wParam == 1 && lParam == 0) {
        // A queued external hotkey can arrive just as native ownership changes.
        // Only request the tab here; all ImGui work stays in the render thread.
        openNotebookRequested = true;
        return 0;
    }
    if ((msg == WM_ACTIVATEAPP && !wParam) || (msg == WM_SIZE && wParam == SIZE_MINIMIZED))
        SetMode(Mode::Hidden, false);
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
    auto& notebook=Notebook(); notebook.Tick();
    if(flushNotebook.exchange(false))notebook.Flush();
    static bool showList = true, showChat = false, previousToggle = false, previousInput = false, previousEscape = false;
    static char search[512]{}, question[65536]{};
    static std::string sentSearch;
    static DocumentSearch readerSearch;
    static bool previousFindNext = false;
    static unsigned settingsRevision = 0;
    static Fade visibility;
    static ULONGLONG searchChanged = 0;
    static bool restored = false;
    static bool previousLeft = false, previousRight = false;
    static bool previousUp = false, previousDown = false;
    static ULONGLONG lastScrollRepeat = 0;
    static std::string renderedId;
    static std::set<std::string> favorites;
    static bool favoritesOnly=false, focusSearch=false;
    static std::unordered_map<std::string,float> readingPositions;
    static ULONGLONG layoutSavedAt=0;
    static bool layoutDirty=false;
    auto& state = S();
    Binding toggle, input, previousLaw, nextLaw, scrollUp, scrollDown, notes;
    Json documents, hits, sources;
    std::string status, answer, selectedId, settingsMessage;
    bool settingsPending, testing;
    bool busy, searching;
    std::shared_ptr<const Json> document;
    {
        std::lock_guard<std::mutex> lock(state.mutex);
        toggle = state.toggle; input = state.input;
        notes=state.notes;
        previousLaw = state.previousLaw; nextLaw = state.nextLaw;
        scrollUp = state.scrollUp; scrollDown = state.scrollDown;
        if (settingsRevision != state.settingsRevision) {
            settingsPanel.Receive(state.settings, state.settingsSaved); state.settingsSaved = false; settingsRevision = state.settingsRevision;
            state.toggle = ParseBinding(state.settings.value("toggle_hotkey", "Ctrl+L"), state.toggle);
            state.input = ParseBinding(state.settings.value("input_hotkey", "Alt+F"), state.input);
            state.notes = ParseBinding(state.settings.value("notes_hotkey", "Ctrl+N"), state.notes);
            state.previousLaw = ParseBinding(state.settings.value("prev_law_hotkey", "Alt+Left"), state.previousLaw);
            state.nextLaw = ParseBinding(state.settings.value("next_law_hotkey", "Alt+Right"), state.nextLaw);
            state.scrollUp = ParseBinding(state.settings.value("scroll_up_hotkey", "Alt+Up"), state.scrollUp);
            state.scrollDown = ParseBinding(state.settings.value("scroll_down_hotkey", "Alt+Down"), state.scrollDown);
            toggle=state.toggle; input=state.input; notes=state.notes;
            previousLaw=state.previousLaw; nextLaw=state.nextLaw;
            scrollUp=state.scrollUp; scrollDown=state.scrollDown;
        }
        settingsPending = state.settingsPending; testing = !state.testJob.empty(); settingsMessage = state.settingsMessage;
        if (!settingsPending) settingsPanel.awaitingSave = false;
        selectedId = state.selectedId;
        documents = state.documents; hits = state.hits; sources = state.sources;
        status = state.status; answer = state.answer; busy = state.busy; searching = state.searching; document = state.document;
        if (!restored && state.preferencesLoaded) {
            showList = state.preferences.value("left", true);
            for(const auto& id:state.preferences.value("favorites",Json::array()))if(id.is_string())favorites.insert(id.get<std::string>());
            const auto scrolls=state.preferences.value("reading_positions",Json::object());
            if(scrolls.is_object())for(auto it=scrolls.begin();it!=scrolls.end();++it)if(it.value().is_number())readingPositions[it.key()]=std::max(0.f,it.value().get<float>());
            showChat = state.preferences.value("chat", false);
            restored = true;
        }
    }
    bool focused = GetForegroundWindow() == gameWindow;
    const int preview=designPreview.exchange(-1);
    if(preview>=0){
        SelectTab(preview>=5 && preview<=7 ? MainTab::Settings : preview==3 || preview==4 || preview==8 || preview==9 || preview==12 ? MainTab::Notebook : MainTab::Laws);
        showList=true;showChat=preview==2;notebook.SetBeside(preview==4);
        if(preview==1){strcpy_s(search,"таблица");searchChanged=GetTickCount64()-200;}
        if(preview==10)showList=false;
        if(preview>=5 && preview<=7)settingsPanel.section=preview==5?0:preview==6?2:1;
        if(preview==8)notebook.PreviewFiles();
        if(preview==12)notebook.PreviewFileActions();
        if(preview==13)favoritesOnly=true;
        if(preview==9)notebook.NewNote();
        if(preview==11){SelectTab(MainTab::Settings);settingsPanel.draft.light=true;SelectTab(MainTab::Laws);}
    }
    if(openNotebookRequested.exchange(false)) {
        SelectTab(MainTab::Notebook);
        SetMode(Mode::Input);
    }
    static bool previousNotes=false;
    bool n=focused && Pressed(notes);
    if(n && !previousNotes) {
        if(notebook.Editing() && mode==Mode::Input) notebook.NewNote();
        else {SelectTab(MainTab::Notebook);SetMode(Mode::Input);}
    }
    previousNotes=n;
    bool t = focused && Pressed(toggle), i = focused && Pressed(input), e = focused && Down(VK_ESCAPE);
    if (t && !previousToggle) SetMode(mode == Mode::Hidden ? Mode::View : Mode::Hidden);
    if (i && !previousInput) SetMode(mode == Mode::Input ? Mode::View : Mode::Input);
    if (e && !previousEscape && mode != Mode::Hidden) {
        if(ImGui::IsPopupOpen(nullptr,ImGuiPopupFlags_AnyPopupId)) { /* popup owns Esc */ }
        else if(navigation.active==MainTab::Notebook && notebook.Escape()) { }
        else if(navigation.active==MainTab::Settings && !settingsPending && !testing) {
            settingsPanel.Cancel();SelectTab(navigation.content);
        }
        else {
            SetMode(mode == Mode::Input ? Mode::View : Mode::Hidden);
        }
    }
    previousToggle = t; previousInput = i; previousEscape = e;
    bool left = focused && Pressed(previousLaw), right = focused && Pressed(nextLaw);
    bool up = focused && Pressed(scrollUp), down = focused && Pressed(scrollDown);
    if (mode != Mode::Hidden && navigation.active==MainTab::Laws && ((left && !previousLeft) || (right && !previousRight))) {
        std::vector<std::string> ids;
        for (const auto& item : searching ? hits : documents) ids.push_back(item.value(searching ? "doc_id" : "id", ""));
        auto target = overlay_input::NextDocument(ids, selectedId, right && !previousRight ? 1 : -1);
        if (!target.empty() && target != selectedId) LoadDocument(target);
    }
    float scrollDelta = 0;
    const auto ticks = GetTickCount64();
    if (mode != Mode::Hidden && navigation.active==MainTab::Laws && (up || down) && ((!previousUp && up) || (!previousDown && down) || ticks - lastScrollRepeat >= 100)) {
        scrollDelta = down ? 100.0f : -100.0f;
        lastScrollRepeat = ticks;
    }
    previousLeft = left; previousRight = right; previousUp = up; previousDown = down;
    if (!focused && mode == Mode::Input) SetMode(Mode::Hidden, false);
    if(navigation.active==MainTab::Settings && !settingsPanel.open)settingsPanel.Open();
    const Appearance appearance = settingsPanel.Current();
    const float fade = visibility.Update(mode != Mode::Hidden, appearance.animations, ImGui::GetIO().DeltaTime);
    if (fade <= 0) return;
    ApplyAppearance(appearance, fade);
    const float scale = appearance.font / 17.f;
    ImGui::PushFont(uiFonts[appearance.font - 14]);
    struct FontScope { ~FontScope() { ImGui::PopFont(); } } fontScope;
    if (mode == Mode::Input) ClipCursor(nullptr);
    const auto screen = ImGui::GetIO().DisplaySize;
    const ImVec2 maximum(std::max(320.f, screen.x - 24), std::max(260.f, screen.y - 24));
    ImGui::SetNextWindowSizeConstraints(ImVec2(std::min(maximum.x, (navigation.active==MainTab::Settings ? 750.f : notebook.open ? 850.f : showChat ? (showList ? 1100.f : 900.f) : showList ? 800.f : 660.f) * scale), std::min(maximum.y, 440 * scale)), maximum);
    ImGui::SetNextWindowSize(ImVec2(1100, 720), ImGuiCond_FirstUseEver);
    ImGui::SetNextWindowPos(ImVec2(60, 80), ImGuiCond_FirstUseEver);
    if(preview==10)ImGui::SetNextWindowSize(ImVec2(720,620));
    ImGuiWindowFlags flags = mode == Mode::Input ? 0 : ImGuiWindowFlags_NoInputs | ImGuiWindowFlags_NoMove;
    bool panelOpen=true;
    const bool expanded=ImGui::Begin("AMAZING • Автор: Vladislav_Shetkov###amazing-main", &panelOpen, flags | ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
    if(!panelOpen)SetMode(Mode::Hidden);
    if(!expanded){ImGui::End();return;}
    const auto position = ImGui::GetWindowPos(), size = ImGui::GetWindowSize();
    const ImVec2 bounded(std::clamp(position.x, 0.f, std::max(0.f, screen.x - size.x)), std::clamp(position.y, 0.f, std::max(0.f, screen.y - size.y)));
    if (bounded.x != position.x || bounded.y != position.y) ImGui::SetWindowPos(bounded);
    auto savePanels = [&] {
        const bool listValue=showList,chatValue=showChat;
        const Json pinned=favorites,positions=readingPositions;
        Enqueue([listValue,chatValue,pinned,positions] {
            Json prefs;
            {std::lock_guard<std::mutex> lock(S().mutex);S().preferences["left"]=listValue;S().preferences["chat"]=chatValue;
             S().preferences["favorites"]=pinned;S().preferences["reading_positions"]=positions;prefs=S().preferences;}
            Rpc("state_set",{{"key","native"},{"value",prefs}});
        });
        layoutDirty=false;layoutSavedAt=GetTickCount64();
    };
    if(layoutDirty && GetTickCount64()-layoutSavedAt>1000)savePanels();
    const auto header=ImGui::GetCursorScreenPos();
    const float headerY=ImGui::GetCursorPosY(), headerWidth=ImGui::GetContentRegionAvail().x;
    const bool compactHeader=headerWidth<740*scale;
    const float headerHeight=(compactHeader?104.f:52.f)*scale;
    auto* draw=ImGui::GetWindowDrawList();
    draw->AddRectFilled(ImVec2(position.x+1,position.y+1),ImVec2(position.x+size.x-1,header.y+headerHeight),ui::Color(ImGuiCol_TitleBg),ui::Px(12),ImDrawFlags_RoundCornersTop);
    ui::Text("AMAZING / ЗАКОНЫ",20.f/17,true);
    ui::Text("Автор: Vladislav_Shetkov",14.f/17,false,ImGuiCol_TextDisabled);
    const float navX=compactHeader?16*scale:std::clamp(headerWidth*.26f,230*scale,280*scale);
    ImGui::SetCursorPos(ImVec2(navX,headerY+(compactHeader?52*scale:0)));
    for(auto tab:{MainTab::Laws,MainTab::Notebook,MainTab::Settings}){
        if(tab!=MainTab::Laws)ImGui::SameLine();
        const char* text=tab==MainTab::Laws?"Законы":tab==MainTab::Notebook?"Блокнот":"Настройки";
        auto icon=tab==MainTab::Laws?ui::Icon::Book:tab==MainTab::Notebook?ui::Icon::Note:ui::Icon::Settings;
        if(ui::Button(text,text,icon,tab==MainTab::Settings?142*scale:132*scale,navigation.active==tab))SelectTab(tab,false);
    }
    navigation.requestSelection=false;
    static bool maximized=false;static ImVec2 normalPosition,normalSize;
    ImGui::SetCursorPos(ImVec2(headerWidth-84*scale,headerY));
    if(ui::Button("maximize",maximized?"Обычный размер":"Развернуть",ui::Icon::Expand,36*scale,false,true)){
        if(!maximized){normalPosition=position;normalSize=size;ImGui::SetWindowPos(ImVec2(12,12));ImGui::SetWindowSize(maximum);}
        else{ImGui::SetWindowPos(normalPosition);ImGui::SetWindowSize(normalSize);}maximized=!maximized;
    }
    ImGui::SameLine();if(ui::Button("hide","Скрыть",ui::Icon::Close,36*scale,false,true)){notebook.Flush();savePanels();SetMode(Mode::Hidden);}
    static bool dragging=false;
    const auto mouse=ImGui::GetIO().MousePos;
    if(mode==Mode::Input && !maximized && ImGui::IsWindowHovered() && mouse.y>=position.y && mouse.y<header.y+headerHeight && !ImGui::IsAnyItemHovered() && ImGui::IsMouseClicked(0))dragging=true;
    if(!ImGui::IsMouseDown(0))dragging=false;
    if(dragging && mode==Mode::Input)ImGui::SetWindowPos(ImVec2(position.x+ImGui::GetIO().MouseDelta.x,position.y+ImGui::GetIO().MouseDelta.y));
    ImGui::SetCursorPosY(headerY+headerHeight+10*scale);ImGui::Separator();ImGui::Spacing();
    const auto inputLabel=overlay_input::BindingLabel(input),toggleLabel=overlay_input::BindingLabel(toggle);
    const std::string modeHint=mode==Mode::Input?"Ввод · "+inputLabel+" / Esc — просмотр":"Просмотр · "+inputLabel+" — ввод · "+toggleLabel+" — скрыть";
    // Keep the input hint above every page; a footer previously overflowed the
    // root window and clipped a second line of status at the bottom edge.
    ui::Status(modeHint,document && status==document->value("title","")?"":status);
    hintBottom=ImGui::GetItemRectMax().y;
    ImGui::Spacing();
    visibleLawRows=selectedLawRows=libraryButtonState=0;
    contentTop=ImGui::GetCursorScreenPos().y;
    windowBottom=position.y+size.y;
    if (navigation.active==MainTab::Settings) {
        ImGui::PushStyleColor(ImGuiCol_ChildBg,ImGui::GetStyleColorVec4(ImGuiCol_WindowBg));
        ImGui::BeginChild("settings-page",ImVec2(0,0));ImGui::PopStyleColor();
        const auto action = settingsPanel.Draw(settingsPending, testing, settingsMessage);
        if (action == SettingsPanel::Action::Save || action == SettingsPanel::Action::Test) {
            const Json values = settingsPanel.Values();
            const bool save = action == SettingsPanel::Action::Save;
            { std::lock_guard<std::mutex> lock(state.mutex); state.settingsPending = true; state.settingsMessage = save ? "Сохраняем…" : "Проверяем соединение…"; }
            bool queued = Enqueue([values, save] {
                if (!Rpc("health").value("native_settings", false))
                    throw std::runtime_error("Обновите и перезапустите бэкенд: запустите start_backend.bat из нового комплекта settings-update.");
                auto result = Rpc(save ? "settings_save" : "ai_test", values);
                std::lock_guard<std::mutex> lock(S().mutex);
                S().settingsPending = false;
                if (save) { S().settings = result; S().settingsSaved = true; ++S().settingsRevision; S().settingsMessage = "Настройки сохранены"; }
                else S().testJob = result.at("job_id").get<std::string>();
            });
            if (!queued) { std::lock_guard<std::mutex> lock(state.mutex); state.settingsPending = false; state.settingsMessage = "Очередь занята. Повторите действие."; }
        } else if (action == SettingsPanel::Action::CancelTest) {
            Enqueue([] { std::string job; { std::lock_guard<std::mutex> lock(S().mutex); job = S().testJob; } if (!job.empty()) Rpc("job_cancel", {{"id", job}}); });
        }
        ImGui::EndChild();
        contentBottom=ImGui::GetItemRectMax().y;
        if(!settingsPanel.open)SelectTab(navigation.content);
        ImGui::End();
        return;
    }
    auto attachNote=[&](const std::string& text){
        if(text.size()>=sizeof(question)){Status("Для чата выделите меньший фрагмент");return;}
        if(question[0]){Status("В чате уже есть черновик. Скопируйте или очистите его перед добавлением заметки");return;}
        memcpy(question,text.c_str(),text.size()+1);showChat=true;SelectTab(MainTab::Laws);
    };
    if(notebook.open && !notebook.beside) {
        notebook.Draw(mode==Mode::Input,attachNote,uiFonts[appearance.font-14]);
        contentBottom=ImGui::GetItemRectMax().y;
        ImGui::End();return;
    }
    // Search and reader controls remain local; no browser or parser is opened.
    if(navigation.active==MainTab::Laws && mode==Mode::Input && ImGui::GetIO().KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_F))focusSearch=true;
    const float bodyBottom=0;
    const float toolbar=ImGui::GetCursorPosY(), fullWidth=ImGui::GetContentRegionAvail().x;
    const bool libraryControls=navigation.active==MainTab::Laws;
    if(libraryControls){
        const bool selectLibrary=ui::Button("library","Библиотека",ui::Icon::Book,40*scale,showList && !favoritesOnly,true);
        ImGui::SameLine();const bool selectFavorites=ui::Button("favorites","Избранное",ui::Icon::Star,40*scale,showList && favoritesOnly,true);
        if(selectLibrary || selectFavorites){SelectLibrary(selectFavorites,showList,favoritesOnly);savePanels();}
        libraryButtonState=showList?(favoritesOnly?2u:1u):0u;
        ImGui::SameLine();
    }
    char count[48];std::snprintf(count,sizeof(count),"%d/%d",readerSearch.total?readerSearch.active+1:0,readerSearch.total);
    const float counterWidth=std::max(32*scale,ImGui::CalcTextSize(count).x);
    const float controls=(libraryControls?228:148)*scale+counterWidth+(libraryControls?7:5)*ImGui::GetStyle().ItemSpacing.x;
    ImGui::SetNextItemWidth(std::max(100*scale,fullWidth-controls));
    if(focusSearch){ImGui::SetKeyboardFocusHere();focusSearch=false;}
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(ImGui::GetStyle().FramePadding.x,(40*scale-ImGui::GetFontSize())*.5f));
    if(ImGui::InputTextWithHint("##search","Найти в законах…",search,sizeof(search)))searchChanged=GetTickCount64();
    const bool searchFocused=ImGui::IsItemActive();
    ImGui::PopStyleVar();
    if(readerSearch.query!=search){readerSearch.query=search;readerSearch.active=0;readerSearch.total=0;readerSearch.jump=true;}
    auto stepMatch=[&](int delta){if(readerSearch.total)readerSearch.active=(readerSearch.active+delta+readerSearch.total)%readerSearch.total;readerSearch.jump=true;};
    ImGui::SameLine();if(ui::Button("prev-match","Предыдущее совпадение · Shift+F3",ui::Icon::Left,36*scale,false,true))stepMatch(-1);
    ImGui::SameLine();if(ui::Button("next-match","Следующее совпадение · F3",ui::Icon::Right,36*scale,false,true))stepMatch(1);
    ImGui::SameLine();ImGui::BeginChild("match-count",ImVec2(counterWidth,40*scale),ImGuiChildFlags_None,ImGuiWindowFlags_NoScrollbar|ImGuiWindowFlags_NoScrollWithMouse);
    ImGui::SetCursorPosY((40*scale-ImGui::GetFontSize())*.5f);ImGui::TextDisabled("%s",count);ImGui::EndChild();
    ImGui::SameLine();if(ui::Button("clear-search","Сбросить поиск",ui::Icon::Close,36*scale,false,true)){search[0]=0;searchChanged=GetTickCount64();}
    ImGui::SameLine();if(ui::Button("toggle-chat","ИИ-помощник",ui::Icon::Chat,40*scale,showChat,true)){showChat=!showChat;savePanels();}
    ImGui::SetCursorPosY(toolbar+52*scale);
    bool findNext = navigation.active==MainTab::Laws && focused && (Down(VK_F3) || (searchFocused && Down(VK_RETURN)));
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
    if (showList && !notebook.open) {
        const float listWidth=std::clamp(ImGui::GetContentRegionAvail().x*.23f,200.f*scale,280.f*scale);
        ImGui::BeginChild("library-panel",ImVec2(listWidth,-bodyBottom),ImGuiChildFlags_Borders);
        ui::Text(searching?"Результаты поиска":favoritesOnly?"Избранное":"Библиотека законов",1.12f,true);
        ui::Text(searching?"Найденные положения": "Кодексы и нормативные акты",.78f,false,ImGuiCol_TextDisabled);
        ui::Divider();
        ImGui::BeginChild("laws",ImVec2(0,-52*scale));
        std::vector<std::string> shown;
        auto lawRow=[&](const Json& item,bool pinned){
            const auto id=item.value(searching?"doc_id":"id","");
            if(std::find(shown.begin(),shown.end(),id)!=shown.end() || (favoritesOnly && !favorites.count(id)))return;
            shown.push_back(id);auto title=LawDisplayTitle(item.value("title",""));
            const bool selected=document && document->value("id","")==id;
            ++visibleLawRows;if(selected)++selectedLawRows;
            if(ui::Row(id.c_str(),title,selected,pinned?ui::Icon::Star:ui::Icon::Note))LoadDocument(id);
            if(searching && item.contains("snippet"))ui::Text(item.value("snippet",""),.78f,false,ImGuiCol_TextDisabled);
        };
        const auto& listing=searching?hits:documents;
        if(!searching && !favoritesOnly && !favorites.empty()){
            ui::Text("ЗАКРЕПЛЁННЫЕ",.72f,true,ImGuiCol_CheckMark);
            for(const auto& item:listing)if(favorites.count(item.value("id","")))lawRow(item,true);
            if(shown.size()<listing.size()){ImGui::Spacing();ui::Text("ОСТАЛЬНЫЕ ДОКУМЕНТЫ",.72f,true,ImGuiCol_TextDisabled);}
        }
        for(const auto& item:listing)lawRow(item,false);
        if(shown.empty())ui::Text(favoritesOnly?"Закрепи закон звёздочкой в ридере.":"Ничего не найдено. Попробуй другое слово.",.85f,false,ImGuiCol_TextDisabled);
        ImGui::EndChild();
        if(ui::Button("reload","Перечитать базу",ui::Icon::Refresh,ImGui::GetContentRegionAvail().x))Refresh();
        ImGui::EndChild();ImGui::SameLine();
    }
    const bool besideNotes=notebook.open && notebook.beside;
    const float readerAvailable=ImGui::GetContentRegionAvail().x;
    const float readerHeight=std::max(20.f,ImGui::GetContentRegionAvail().y-bodyBottom);
    float readerWidth = besideNotes ? std::max(180.f*scale,readerAvailable*notebook.lawFraction) : showChat ? std::max(240.0f * scale, ImGui::GetContentRegionAvail().x - 350 * scale) : 0;
    ImGui::BeginChild("reader", ImVec2(readerWidth, -bodyBottom), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
    const auto docId = document ? document->value("id", "") : "";
    if(docId!=renderedId){ImGui::SetScrollY(readingPositions.count(docId)?readingPositions[docId]:0);renderedId=docId;readerSearch.active=0;readerSearch.jump=!readerSearch.query.empty();}
    else if (scrollDelta) ImGui::SetScrollY(std::max(0.0f, ImGui::GetScrollY() + scrollDelta));
    if(document){
        const float headingY=ImGui::GetCursorPosY(), starX=ImGui::GetWindowWidth()-ImGui::GetStyle().WindowPadding.x-44*scale;
        if(!besideNotes){
            ImGui::SetCursorPosX(starX);
            if(ui::Button("pin",favorites.count(docId)?"Открепить закон":"Закрепить закон",ui::Icon::Star,40*scale,favorites.count(docId)!=0,true)){
                if(!favorites.erase(docId))favorites.insert(docId);savePanels();
            }
            ImGui::SetCursorPosY(headingY);
        }
        // Bounded wrapping leaves the star free while keeping long titles readable.
        ui::Text(LawDisplayTitle(document->value("title","Закон")),1.5f,true,ImGuiCol_Text,std::max(40.f,starX-28*scale));
        ui::Text(document->value("structured",true)?"Источник: форум Amazing RP":"Импорт TXT · без исходной структуры",.78f,false,ImGuiCol_TextDisabled);
        ui::Divider();DrawDocument(*document,&readerSearch);
        const float y=ImGui::GetScrollY();
        if(std::abs(readingPositions[docId]-y)>.5f){readingPositions[docId]=y;layoutDirty=true;}
    }else ui::Text(status,.9f,false,ImGuiCol_TextDisabled);
    ImGui::EndChild();
    if(besideNotes){
        ImGui::SameLine();ImGui::InvisibleButton("reader-notes-split",ImVec2(5,readerHeight));
        if(mode==Mode::Input && ImGui::IsItemActive())notebook.lawFraction=std::clamp(notebook.lawFraction+ImGui::GetIO().MouseDelta.x/readerAvailable,.25f,.7f);
        if(ImGui::IsItemHovered())ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
        ImGui::SameLine();notebook.Draw(mode==Mode::Input,attachNote,uiFonts[appearance.font-14]);
    }
    if (showChat && !besideNotes) {
        ImGui::SameLine();
        ImGui::BeginChild("chat", ImVec2(0, -bodyBottom), ImGuiChildFlags_Borders);
        ui::Text("ИИ-помощник",1.25f,true);
        ui::Text("Ответы на основе законов · проверяй источники",.78f,false,ImGuiCol_TextDisabled);
        ui::Divider();
        static bool onlyCurrent=false;
        ui::Toggle("##current-law",&onlyCurrent);ImGui::SameLine();ui::Text("Текущий закон",.83f);
        ImGui::InputTextMultiline("##question",question,sizeof(question),ImVec2(-1,95*scale));
        if (ui::Button("send-ai",busy ? "Ожидание…" : "Отправить",ui::Icon::Chat) && !busy && question[0]) {
            std::string text(question);
            if(notebook::Decode(text).size()>12000){Status("Предел сообщения — 12000 символов; текст не отправлен");}
            else {
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
        }
        ImGui::SameLine();
        if (ui::Button("stop-ai","Стоп",ui::Icon::Close)) Enqueue([] { std::string job; { std::lock_guard<std::mutex> lock(S().mutex); job = S().job; } if (!job.empty()) Rpc("job_cancel", {{"id", job}}); });
        if (ui::Button("copy-ai","Копировать",ui::Icon::Files)) ImGui::SetClipboardText(answer.c_str());
        ui::Divider();ImGui::BeginChild("answer",ImVec2(0,0));
        ui::Text(status,.78f,false,ImGuiCol_TextDisabled);
        ui::Text(answer.empty() ? (busy?"Готовим ответ…":"Задай вопрос по законодательству в поле выше.") : answer,.95f);
        for (const auto& source : sources) {
            auto label = "[" + source.value("label", "") + "] " + source.value("heading", "");
            if (ImGui::Selectable(label.c_str())) LoadDocument(source.value("doc_id", ""));
        }
        ImGui::EndChild();ImGui::EndChild();
    }
    contentBottom=ImGui::GetItemRectMax().y;
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
        static ImVector<ImWchar> glyphs;
        ImFontGlyphRangesBuilder ranges;ranges.AddRanges(io.Fonts->GetGlyphRangesCyrillic());
        static const ImWchar punctuation[]={0x2000,0x206f,0x2116,0x2116,0};ranges.AddRanges(punctuation);ranges.BuildRanges(&glyphs);
        io.Fonts->TexDesiredWidth=2048;
        std::wstring boldPath=std::wstring(windows)+L"\\Fonts\\segoeuib.ttf";
        int boldNeeded=WideCharToMultiByte(CP_UTF8,0,boldPath.c_str(),-1,nullptr,0,nullptr,nullptr);
        std::string boldUtf8(boldNeeded,'\0');WideCharToMultiByte(CP_UTF8,0,boldPath.c_str(),-1,boldUtf8.data(),boldNeeded,nullptr,nullptr);
        for(int size=14;size<=26;++size){
            uiFonts[size-14]=io.Fonts->AddFontFromFileTTF(utf8.c_str(),static_cast<float>(size),nullptr,glyphs.Data);
            ui::boldFonts[size-14]=io.Fonts->AddFontFromFileTTF(boldUtf8.c_str(),static_cast<float>(size),nullptr,glyphs.Data);
        }
        io.FontDefault = uiFonts[3];
        ApplyAppearance(Appearance{}, 1);
        ImGui_ImplWin32_Init(gameWindow);
        ImGui_ImplDX9_Init(device);
        previousProc = reinterpret_cast<WNDPROC>(SetWindowLongPtrW(gameWindow, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(WindowProc)));
        if (!previousProc) {
            ImGui_ImplDX9_Shutdown(); ImGui_ImplWin32_Shutdown(); ImGui::DestroyContext();
            activeDevice = nullptr;
            return originalEnd(device);
        }
        initialized = true;
        if(!SetPropW(gameWindow,NativeInputOwner,reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(GetCurrentProcessId()))))Log("WARNING: could not publish native hotkey ownership");
        SetPropW(gameWindow,L"AmazingLawOverlay.NativeNotebookCommand.v1",reinterpret_cast<HANDLE>(1));
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

extern "C" __declspec(dllexport) unsigned __cdecl AmazingOverlayVisibilityForTest() {
    return static_cast<unsigned>(mode.load());
}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlaySettingsForTest(int font, int light) {
    SelectTab(MainTab::Settings); settingsPanel.draft.font = std::clamp(font, 14, 26);
    settingsPanel.draft.light = light != 0;
}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlayNotebookForTest() {
    SelectTab(MainTab::Notebook);Notebook().beside=false;
}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlayTabForTest(unsigned tab) {
    if(tab<=2)SelectTab(static_cast<MainTab>(tab));
}
extern "C" __declspec(dllexport) void __cdecl AmazingOverlayDesignForTest(unsigned scenario) {
    if(scenario<14)designPreview=static_cast<int>(scenario);
}
extern "C" __declspec(dllexport) unsigned __cdecl AmazingOverlayActiveTabForTest() {return static_cast<unsigned>(navigation.active);}
extern "C" __declspec(dllexport) unsigned __cdecl AmazingOverlayLayoutForTest() {
    return static_cast<unsigned>(navigation.active) | (Notebook().beside?16u:0u) | (static_cast<unsigned>(settingsPanel.section)<<8);
}
extern "C" __declspec(dllexport) unsigned __cdecl AmazingOverlayUiForTest() {
    const bool fits=hintBottom<=contentTop && contentBottom<=windowBottom;
    return (visibleLawRows&255u) | ((selectedLawRows&255u)<<8) | (libraryButtonState<<16) | (fits?1u<<24:0u);
}
extern "C" __declspec(dllexport) float __cdecl AmazingOverlayNoteFontForTest() {return Notebook().editor.fontSize;}

extern "C" __declspec(dllexport) void __cdecl AmazingOverlayDiagnostics(char* buffer, unsigned size) {
    std::lock_guard<std::mutex> lock(S().mutex);
    if (size) strncpy_s(buffer, size, S().status.c_str(), _TRUNCATE);
}
