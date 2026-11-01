#pragma once
#include "plain_editor_view.h"
#include "rpc.h"
#include <windows.h>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <fstream>
#include <functional>
#include <mutex>
#include <set>
#include <thread>

namespace notebook {
// Process-lifetime panel, like the renderer. Its worker is independent of AI polling.
class NotebookPanel {
    struct Job { std::function<Json()> work; std::function<void(const Json&)> done; };
    struct Reply { Json value; std::string error; std::function<void(const Json&)> done; };
    std::mutex mutex;
    std::condition_variable wake;
    std::deque<Job> jobs;
    std::deque<Reply> replies;
    std::condition_variable draftWake;
    Json pendingDraft=Json::object();
    bool started=false, initialized=false, loading=false, saving=false, dirty=false, readonly=false;
    bool searchVisible=false, restoreState=false, dialogRequested=false, saveFailed=false;
    unsigned listGeneration=0;
    ULONGLONG editedAt=0, searchAt=0, stateAt=0;
    Json current=Json::object(), entries=Json::array(), pendingState=Json::object(), trashItems=Json::array(), recoveryItems=Json::array();
    std::string root, selected, status="Загрузка блокнота…", lastQuery, dialogKind, dialogSource;
    char listQuery[256]{}, findQuery[256]{}, name[1024]{};
    std::set<std::string> expanded;
    float treeWidth=200, scroll=0;
    std::function<void()> afterSave;

    static std::filesystem::path Data() {
        wchar_t value[32768]{};
        DWORD length=GetEnvironmentVariableW(L"AMAZING_OVERLAY_DATA",value,32768);
        if (length && length<32768) return value;
        GetEnvironmentVariableW(L"LOCALAPPDATA",value,32768);
        return std::filesystem::path(value)/L"AmazingLawOverlay"/L"v2";
    }
    static std::filesystem::path Recovery(const Json& value) {
        const auto id=std::hash<std::string>{}(value.value("root","")+"/"+value.value("path",""));
        return Data()/L"notes-recovery"/("native-"+std::to_string(GetCurrentProcessId())+"-"+std::to_string(id)+".json");
    }
    static void Draft(const Json& value) {
        static std::mutex diskMutex;
        std::lock_guard<std::mutex> lock(diskMutex);
        auto path=Recovery(value); std::filesystem::create_directories(path.parent_path());
        if(std::filesystem::exists(path)){
            try{std::ifstream old(path);Json previous;old>>previous;if(previous.value("draft_seq",0ull)>value.value("draft_seq",0ull))return;}catch(...){}
        }
        auto temp=path; temp+=L".tmp";
        { std::ofstream stream(temp,std::ios::binary|std::ios::trunc); stream<<value.dump(); stream.flush(); if (!stream) throw std::runtime_error("Не удалось записать восстановительный черновик"); }
        if (!MoveFileExW(temp.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) throw std::runtime_error("Не удалось сохранить восстановительный черновик");
    }
    bool Queue(std::function<Json()> work, std::function<void(const Json&)> done={}) {
        Start();
        std::lock_guard<std::mutex> lock(mutex);
        if (jobs.size()>=32) { status="Очередь блокнота занята; текст оставлен в редакторе"; return false; }
        jobs.push_back({std::move(work),std::move(done)}); wake.notify_one(); return true;
    }
    bool Call(std::string method, Json params, std::function<void(const Json&)> done={}) {
        if(method.rfind("notes_",0)==0 && !root.empty())params["root"]=root;
        return Queue([method,params]{return Rpc(method,params);},std::move(done));
    }
    void Start() {
        if (started) return; started=true;
        std::thread([this]{
            for (;;) {
                Job job;
                { std::unique_lock<std::mutex> lock(mutex); wake.wait(lock,[&]{return !jobs.empty();}); job=std::move(jobs.front()); jobs.pop_front(); }
                Reply reply; reply.done=std::move(job.done);
                try { reply.value=job.work(); } catch (const std::exception& error) { reply.error=error.what(); }
                { std::lock_guard<std::mutex> lock(mutex); replies.push_back(std::move(reply)); }
            }
        }).detach();
        std::thread([this]{
            for(;;){
                Json value;
                {std::unique_lock<std::mutex> lock(mutex);draftWake.wait(lock,[&]{return !pendingDraft.empty();});value=std::move(pendingDraft);pendingDraft=Json::object();}
                try{Draft(value);}catch(const std::exception&){std::lock_guard<std::mutex> lock(mutex);replies.push_back({Json(),"Не удалось записать восстановительный черновик",{}});}
            }
        }).detach();
    }
    void Guard(std::function<void()> action) {
        if (loading) return;
        if (dirty || saving) { afterSave=std::move(action); Save(); } else action();
    }
    void Load(std::string path) {
        loading=true; Persist();
        Call("notes_read",{{"path",path}},[this](const Json& value){
            current=value; selected=value.at("path").get<std::string>();
            editor.model.Load(value.at("text").get<std::string>()); editor.reveal=false;editor.focused=true;
            readonly=value.value("readonly",false); dirty=loading=false; saveFailed=false;
            status=readonly ? "Только чтение: файл больше 1 МиБ" : "Сохранено";
            if (pendingState.value("path","")==selected) {
                editor.model.Move(pendingState.value("cursor",size_t(0)),false);
                editor.restoreScroll=pendingState.value("scroll",0.f);
            } else editor.restoreScroll=0;
            pendingState=Json::object();
        });
    }
    void Dialog(std::string kind, std::string initial="", std::string source="") {
        dialogKind=kind; dialogSource=source;
        std::fill(std::begin(name),std::end(name),0);
        memcpy(name,initial.data(),std::min(initial.size(),sizeof(name)-1));
        dialogRequested=true;
    }
    std::string Parent() const {
        for (const auto& item:entries) if (item.value("path","")==selected && item.value("folder",false)) return selected+"/";
        auto at=selected.rfind('/'); return at==std::string::npos ? "" : selected.substr(0,at+1);
    }
    void Tree(const std::string& parent="") {
        for (const auto& item:entries) {
            auto path=item.value("path",""); auto at=path.rfind('/');
            auto directory=at==std::string::npos ? "" : path.substr(0,at);
            if (directory!=parent && !listQuery[0]) continue;
            ImGui::PushID(path.c_str());
            auto label=listQuery[0] ? path : path.substr(at==std::string::npos ? 0 : at+1);
            if (item.value("folder",false)) {
                ImGui::SetNextItemOpen(expanded.count(path)!=0,ImGuiCond_Once);
                bool open=ImGui::TreeNodeEx("folder",ImGuiTreeNodeFlags_OpenOnArrow|ImGuiTreeNodeFlags_SpanAvailWidth,"%s",label.c_str());
                if (ImGui::IsItemClicked()) selected=path;
                if (open) { expanded.insert(path); Tree(path); ImGui::TreePop(); } else expanded.erase(path);
            } else {
                if (ImGui::Selectable(label.c_str(),current.value("path","")==path)) { selected=path; Guard([this,path]{Load(path);}); }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("%s",path.c_str());
            }
            ImGui::PopID();
        }
    }
public:
    EditorView editor;
    bool open=false, beside=false;
    float lawFraction=.43f;
    void Refresh(bool first=false) {
        unsigned generation=++listGeneration; std::string query=listQuery;
        Queue([query,first]{
            if (!Rpc("health").value("notes",false)) throw std::runtime_error("Обновите и перезапустите бэкенд для работы блокнота");
            auto value=Rpc("notes_list",{{"query",query}});
            if (first) value["state"]=Rpc("state_get",{{"key","notebook-native"},{"default",Json::object()}});
            return value;
        },[this,generation,first](const Json& value){
            if (generation!=listGeneration) return;
            if (!root.empty() && root!=value.value("root","") && (dirty || saving)) {status="Папка изменилась. Сохраните свой текст отдельной копией";return;}
            if (!root.empty() && root!=value.value("root","")) {current=Json::object();editor.model.Load("");}
            root=value.value("root",""); entries=value.at("entries");
            if (first) {
                pendingState=value.value("state",Json::object());
                treeWidth=pendingState.value("tree_width",200.f); beside=pendingState.value("beside",false);lawFraction=std::clamp(pendingState.value("law_fraction",.43f),.25f,.7f);
                for (const auto& item:pendingState.value("expanded",Json::array())) expanded.insert(item.get<std::string>());
                auto last=pendingState.value("path","");
                for (const auto& item:entries) if (item.value("path","")==last && !item.value("folder",false)) {Load(last);break;}
            }
            if (!dirty && !saving) status="Папка: "+root;
        });
    }
    void Tick() {
        std::deque<Reply> ready;
        {std::lock_guard<std::mutex> lock(mutex);ready.swap(replies);}
        for (auto& reply:ready) {
            if (!reply.error.empty()) {saving=loading=false; saveFailed=true; afterSave={}; status="Ошибка: "+reply.error+". Текст оставлен в редакторе; доступен пункт «Черновики»";}
            else if (reply.done) {try{reply.done(reply.value);}catch(const std::exception&){status="Несовместимый ответ блокнота; обновите бэкенд";saving=loading=false;}}
        }
        if (dirty && !saving && !saveFailed && conflict.empty() && GetTickCount64()-editedAt>=1000) Save();
        if (open && GetTickCount64()-stateAt>2500) {Persist();stateAt=GetTickCount64();}
    }
    void Open() {open=true;editor.focused=true;if (!initialized) {initialized=true;Refresh(true);} }
    void Persist() {
        if (current.empty()) return;
        Json value={{"path",current.value("path","")},{"cursor",editor.model.cursor},{"scroll",scroll},{"tree_width",treeWidth},{"expanded",expanded},{"beside",beside},{"law_fraction",lawFraction}};
        Call("state_set",{{"key","notebook-native"},{"value",value}});
    }
    void Save() {
        if (!dirty || saving || loading || current.empty() || !conflict.empty()) return;
        Json value=current; value["text"]=editor.model.Utf8(); value["root"]=root;value["draft_seq"]=editedAt;
        saving=true; saveFailed=false; status="Сохраняется…";
        bool queued=Queue([value]{
            Draft(value);
            auto result=Rpc("notes_save",{{"path",value.at("path")},{"text",value.at("text")},{"revision",value.at("revision")},{"root",value.at("root")}});
            if (!result.value("conflict",false)) {std::error_code ignored;std::filesystem::remove(Recovery(value),ignored);}
            return result;
        },[this,value](const Json& result){
            saving=false;
            if (result.value("conflict",false)) { status="Конфликт: файл изменён извне. Выберите загрузку внешней версии или сохранение копии"; conflict=result.at("current"); afterSave={}; return; }
            current["revision"]=result.at("revision");
            dirty=editor.model.Utf8()!=value.at("text").get<std::string>();
            status=dirty ? "Изменено" : "Сохранено";
            if (!dirty && afterSave) {auto action=std::move(afterSave);afterSave={};action();}
        });
        if (!queued) {saving=false;saveFailed=true;}
    }
    void Flush() {Save();Persist();}
    bool Escape() {
        if (searchVisible) {searchVisible=false;findQuery[0]=0;editor.Find("");return true;}
        return false;
    }
    void NewNote() {newRequested=true;}
    bool Editing() const {return open && editor.focused;}
    Json conflict=Json::object();
    bool newRequested=false;
    void Draw(bool interactive, const std::function<void(const std::string&)>& attach) {
        if (!open) return;
        ImGui::BeginChild("notebook",ImVec2(0,-26),ImGuiChildFlags_Borders);
        if (newRequested) {newRequested=false;Dialog("create",Parent()+"Новая заметка.txt");}
        if (ImGui::Button("Файлы")) ImGui::OpenPopup("notes-files");
        if (ImGui::BeginPopup("notes-files")) {
            if (ImGui::MenuItem("Новая заметка", "Ctrl+N")) Dialog("create",Parent()+"Новая заметка.txt");
            if (ImGui::MenuItem("Новая папка")) Dialog("folder",Parent()+"Новая папка");
            if (ImGui::MenuItem("Переименовать / переместить",nullptr,false,!selected.empty())) Dialog("move",selected,selected);
            if (ImGui::MenuItem("Сохранить копию",nullptr,false,!current.empty())) Dialog("copy",Parent()+"Копия.txt");
            if (ImGui::MenuItem("Перечитать с диска",nullptr,false,!current.empty())) {auto path=current.value("path","");Guard([this,path]{Load(path);});}
            if (ImGui::MenuItem("В корзину",nullptr,false,!selected.empty())) Dialog("delete",selected,selected);
            if (ImGui::MenuItem("Корзина / восстановление")) Call("notes_trash",Json::object(),[this](const Json& result){trashItems=result;showTrash=true;});
            if (ImGui::MenuItem("Черновики / восстановление")) {
                const auto expectedRoot=root;
                Queue([expectedRoot]{
                    Json result=Json::array(); auto dir=Data()/L"notes-recovery";
                    if (std::filesystem::exists(dir)) for (const auto& entry:std::filesystem::directory_iterator(dir)) if (entry.path().extension()==L".json") {
                        try{std::ifstream stream(entry.path());Json value;stream>>value;if(value.value("root","")==expectedRoot) result.push_back(value);}catch(...){}
                    }
                    return result;
                },[this](const Json& value){recoveryItems=value;showRecovery=true;});
            }
            if (ImGui::MenuItem("Обновить список")) Refresh();
            ImGui::EndPopup();
        }
        ImGui::SameLine();if (ImGui::Button("Сохранить")) Save();
        ImGui::SameLine();if (ImGui::Button("В чат") && !current.empty()) {
            auto text=editor.model.Selected();if(text.empty())text=editor.model.Utf8();
            text="Заметка «"+current.value("path","")+"»:\n"+text;
            if (Decode(text).size()>12000) status="Для чата выделите фрагмент до 12000 символов. Текст не отправлен"; else attach(text);
        }
        if (ImGui::Checkbox("Рядом с законом",&beside)) Persist();
        if (interactive && editor.focused && ImGui::GetIO().KeyCtrl) {
            if (ImGui::IsKeyPressed(ImGuiKey_S)) Save();
            if (ImGui::IsKeyPressed(ImGuiKey_F)) {searchVisible=true;focusFind=true;editor.focused=false;}
        }
        if (interactive && (searchVisible || editor.focused) && ImGui::IsKeyPressed(ImGuiKey_F3)) editor.Step(ImGui::GetIO().KeyShift ? -1 : 1);
        if (!conflict.empty()) {
            ImGui::TextWrapped("Файл изменён другим редактором. Ваш текст остаётся в черновике.");
            if(ImGui::Button("Загрузить внешнюю")){dirty=false;Load(current.value("path",""));conflict=Json::object();}
            ImGui::SameLine();if(ImGui::Button("Сохранить свою копию"))Dialog("copy",Parent()+"Копия.txt");
        }
        ImGui::TextWrapped("%s",status.c_str());
        const float available=ImGui::GetContentRegionAvail().x;
        treeWidth=std::clamp(treeWidth,110.f,std::max(110.f,available-200.f));
        ImGui::BeginChild("notes-tree",ImVec2(treeWidth,0),ImGuiChildFlags_Borders);
        ImGui::SetNextItemWidth(-1);
        if(ImGui::InputTextWithHint("##note-query","Имя или текст…",listQuery,sizeof(listQuery)))searchAt=GetTickCount64();
        if(lastQuery!=listQuery && GetTickCount64()-searchAt>=150){lastQuery=listQuery;Refresh();}
        Tree();ImGui::EndChild();
        ImGui::SameLine();ImGui::InvisibleButton("notes-split",ImVec2(5,ImGui::GetContentRegionAvail().y));
        if(interactive && ImGui::IsItemActive())treeWidth+=ImGui::GetIO().MouseDelta.x;
        if(ImGui::IsItemHovered())ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
        ImGui::SameLine();ImGui::BeginChild("notes-body",ImVec2(0,0));
        ImGui::TextWrapped("%s",current.value("path","Выберите заметку").c_str());
        if(searchVisible){
            ImGui::SetNextItemWidth(-1);
            if(focusFind){ImGui::SetKeyboardFocusHere();focusFind=false;}
            if(ImGui::InputTextWithHint("##note-find","Найти…",findQuery,sizeof(findQuery)))editor.Find(findQuery);
            if(ImGui::Button("<##note-hit"))editor.Step(-1);
            ImGui::SameLine();if(ImGui::Button(">##note-hit"))editor.Step(1);
            ImGui::SameLine();ImGui::Text("%d/%d",editor.active+1,static_cast<int>(editor.matches.size()));
        }
        ImGui::BeginDisabled(loading || current.empty());
        if(editor.Draw(interactive && !loading && !current.empty(),readonly,&scroll)){
            dirty=true;saveFailed=false;editedAt=GetTickCount64();status="Изменено";
            Json draft=current;draft["text"]=editor.model.Utf8();draft["root"]=root;draft["draft_seq"]=editedAt;
            {std::lock_guard<std::mutex> lock(mutex);pendingDraft=std::move(draft);draftWake.notify_one();}
        }
        ImGui::EndDisabled();ImGui::EndChild();
        // Popups are scoped to this panel, independent of the tree/editor child IDs.
        if(dialogRequested){ImGui::OpenPopup("Файл блокнота");dialogRequested=false;}
        if(ImGui::BeginPopupModal("Файл блокнота",nullptr,ImGuiWindowFlags_AlwaysAutoResize)){
            if(ImGui::IsKeyPressed(ImGuiKey_Escape))ImGui::CloseCurrentPopup();
            ImGui::TextUnformatted(dialogKind=="delete" ? "Переместить в корзину?" : "Путь относительно Notes (с расширением .txt):");
            ImGui::SetNextItemWidth(450);ImGui::InputText("##note-name",name,sizeof(name));
            if(ImGui::Button("ОК")){
                std::string destination=name, kind=dialogKind, source=dialogSource;
                auto text=kind=="copy" ? editor.model.Utf8() : kind=="recover" ? recoveryText : "";
                auto action=[this,destination,kind,source,text]{
                    std::string method=kind=="move" ? "notes_move" : kind=="delete" ? "notes_delete" : kind=="restore" ? "notes_restore" : "notes_create";
                    Json params=kind=="move" ? Json{{"path",source},{"destination",destination}} : kind=="delete" ? Json{{"path",source}} : kind=="restore" ? Json{{"id",source},{"destination",destination}} : Json{{"path",destination},{"folder",kind=="folder"},{"text",text}};
                    loading=true;
                    if(!Call(method,params,[this,kind,source,destination](const Json&){
                        loading=false;
                        auto old=current.value("path","");
                        bool affected=old==source || (!source.empty() && old.rfind(source+"/",0)==0);
                        if(kind=="delete" && affected){current=Json::object();editor.model.Load("");dirty=false;}
                        else if(kind=="move" && affected){current["path"]=destination+old.substr(source.size());selected=current.value("path","");}
                        else if(kind=="create" || kind=="copy" || kind=="recover"){dirty=false;conflict=Json::object();Load(destination);}
                        Refresh();
                    }))loading=false;
                };
                if(kind=="copy") action(); else Guard(action);
                ImGui::CloseCurrentPopup();
            }
            ImGui::SameLine();if(ImGui::Button("Отмена"))ImGui::CloseCurrentPopup();
            ImGui::EndPopup();
        }
        if(showTrash){ImGui::OpenPopup("Корзина заметок");showTrash=false;}
        if(ImGui::BeginPopup("Корзина заметок")){
            if(trashItems.empty())ImGui::TextUnformatted("Корзина пуста");
            for(const auto& item:trashItems){
                auto label=item.value("path","")+"##"+item.value("id","");
                if(ImGui::Selectable(label.c_str())){pendingDialog="restore";pendingName=item.value("path","");pendingSource=item.value("id","");}
            }ImGui::EndPopup();
        }
        if(showRecovery){ImGui::OpenPopup("Черновики заметок");showRecovery=false;}
        if(ImGui::BeginPopup("Черновики заметок")){
            if(recoveryItems.empty())ImGui::TextUnformatted("Нет восстановительных черновиков");
            int index=0;for(const auto& item:recoveryItems){
                auto label=item.value("path","")+"##"+std::to_string(index++);
                if(ImGui::Selectable(label.c_str())){recoveryText=item.value("text","");pendingDialog="recover";pendingName="Восстановлено.txt";}
            }ImGui::EndPopup();
        }
        if(!pendingDialog.empty()){Dialog(pendingDialog,pendingName,pendingSource);pendingDialog.clear();}
        ImGui::EndChild();
    }
private:
    bool focusFind=false,showTrash=false,showRecovery=false;
    std::string pendingDialog,pendingName,pendingSource,recoveryText;
};
}
