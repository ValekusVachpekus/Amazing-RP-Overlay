#pragma once
#include "plain_editor_view.h"
#include "rpc.h"
#include "desktop_ui.h"
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
    bool searchVisible=false, restoreState=false, dialogRequested=false, saveFailed=false, layoutTouched=false;
    unsigned listGeneration=0;
    ULONGLONG editedAt=0, searchAt=0, stateAt=0;
    Json current=Json::object(), entries=Json::array(), pendingState=Json::object(), trashItems=Json::array(), recoveryItems=Json::array();
    std::string root, selected, status="Загрузка блокнота…", lastQuery, dialogKind, dialogSource;
    char listQuery[256]{}, findQuery[256]{}, name[1024]{};
    std::set<std::string> expanded;
    float treeWidth=260, scroll=0;
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
                if(ui::Row("note",label,current.value("path","")==path)) {selected=path;Guard([this,path]{Load(path);});}
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
                if(!layoutTouched){treeWidth=pendingState.value("tree_width",260.f);beside=pendingState.value("beside",false);lawFraction=std::clamp(pendingState.value("law_fraction",.43f),.25f,.7f);}
                for (const auto& item:pendingState.value("expanded",Json::array())) expanded.insert(item.get<std::string>());
                auto last=pendingState.value("path","");
                for (const auto& item:entries) if (item.value("path","")==last && !item.value("folder",false)) {Load(last);break;}
            }
            if(!dirty && !saving)status=current.empty()?"Выберите заметку":"Сохранено";
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
    void SetBeside(bool value){layoutTouched=true;beside=value;Persist();}
    bool Editing() const {return open && editor.focused;}
    Json conflict=Json::object();
    bool newRequested=false;
    bool previewFiles=false;
    void PreviewFiles(){previewFiles=true;}
    void Draw(bool interactive, const std::function<void(const std::string&)>& attach, ImFont* font=nullptr) {
        if (!open) return;
        if(font)ImGui::PushFont(font);
        struct FontScope{bool pushed;~FontScope(){if(pushed)ImGui::PopFont();}} fontScope{font!=nullptr};
        const float scale=ui::Scale();
        ImGui::PushStyleColor(ImGuiCol_ChildBg,ImGui::GetStyleColorVec4(ImGuiCol_WindowBg));
        ImGui::BeginChild("notebook",ImVec2(0,-28*scale),ImGuiChildFlags_None);ImGui::PopStyleColor();
        if(newRequested){newRequested=false;Dialog("create",Parent()+"Новая заметка.txt");}
        bool filesRequested=previewFiles;previewFiles=false;
        const float available=ImGui::GetContentRegionAvail().x;
        if(!beside){
            treeWidth=std::clamp(treeWidth,160*scale,std::max(160*scale,available-340*scale));
            ImGui::BeginChild("notes-sidebar",ImVec2(treeWidth,0),ImGuiChildFlags_Borders);
            ui::Text("Мои заметки",1.2f,true);ImGui::Spacing();
            if(ui::Button("new-note","Новая заметка",ui::Icon::Plus,40*scale,true,true))Dialog("create",Parent()+"Новая заметка.txt");
            ImGui::SameLine();if(ui::Button("files","Файлы",ui::Icon::Files,ImGui::GetContentRegionAvail().x))filesRequested=true;
            ui::Divider();ImGui::SetNextItemWidth(-1);
            if(ImGui::InputTextWithHint("##note-query","Имя или текст…",listQuery,sizeof(listQuery)))searchAt=GetTickCount64();
            if(lastQuery!=listQuery && GetTickCount64()-searchAt>=150){lastQuery=listQuery;Refresh();}
            ImGui::BeginChild("notes-tree",ImVec2(0,-100*scale));Tree();ImGui::EndChild();
            if(ui::Button("trash","Корзина",ui::Icon::Trash,ImGui::GetContentRegionAvail().x))Call("notes_trash",Json::object(),[this](const Json& result){trashItems=result;showTrash=true;});
            if(ui::Button("drafts","Черновики",ui::Icon::Note,ImGui::GetContentRegionAvail().x))Recoveries();
            ImGui::EndChild();ImGui::SameLine();ImGui::InvisibleButton("notes-split",ImVec2(5*scale,ImGui::GetContentRegionAvail().y));
            if(interactive && ImGui::IsItemActive()){layoutTouched=true;treeWidth+=ImGui::GetIO().MouseDelta.x;}
            if(ImGui::IsItemHovered())ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
            ImGui::SameLine();
        }
        ImGui::BeginChild("notes-body",ImVec2(0,0),ImGuiChildFlags_Borders);
        const auto path=current.value("path","");auto slash=path.rfind('/');
        const float headingWidth=ImGui::GetContentRegionAvail().x;
        const bool compact=headingWidth<480*scale;const float toggleWidth=compact?40*scale:230*scale;
        const float headingY=ImGui::GetCursorPosY();
        ImGui::BeginChild("note-heading",ImVec2(std::max(40.f,headingWidth-toggleWidth-16*scale),64*scale));
        ui::Text(path.empty()?"Выберите заметку":path.substr(slash==std::string::npos?0:slash+1),1.5f,true);
        ui::Text(path.empty()?"Открой или создай заметку":current.value("encoding","UTF-8"),.8f,false,ImGuiCol_TextDisabled);
        ImGui::EndChild();ImGui::SameLine();
        if(ui::Button("beside",beside?"Развернуть":"Рядом с законом",ui::Icon::Book,toggleWidth,beside,compact))SetBeside(!beside);
        if(beside && ui::Button("files-beside","Файлы",ui::Icon::Files))filesRequested=true;
        ImGui::SetCursorPosY(std::max(ImGui::GetCursorPosY(),headingY+72*scale));
        ui::Divider();
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
        ui::Text(status,.8f,false,ImGuiCol_TextDisabled);
        if(searchVisible){
            ImGui::SetNextItemWidth(-1);
            if(focusFind){ImGui::SetKeyboardFocusHere();focusFind=false;}
            if(ImGui::InputTextWithHint("##note-find","Найти…",findQuery,sizeof(findQuery)))editor.Find(findQuery);
            if(ImGui::Button("<##note-hit"))editor.Step(-1);
            ImGui::SameLine();if(ImGui::Button(">##note-hit"))editor.Step(1);
            ImGui::SameLine();ImGui::Text("%d/%d",editor.active+1,static_cast<int>(editor.matches.size()));
        }
        ImGui::BeginDisabled(loading || current.empty());
        ImGui::BeginChild("note-editor-area",ImVec2(0,-80*scale));
        if(editor.Draw(interactive && !loading && !current.empty(),readonly,&scroll)){
            dirty=true;saveFailed=false;editedAt=GetTickCount64();status="Изменено";
            Json draft=current;draft["text"]=editor.model.Utf8();draft["root"]=root;draft["draft_seq"]=editedAt;
            {std::lock_guard<std::mutex> lock(mutex);pendingDraft=std::move(draft);draftWake.notify_one();}
        }
        ImGui::EndChild();ImGui::EndDisabled();
        ui::Divider();
        if(ui::Button("save-note","Сохранить",ui::Icon::Check))Save();ImGui::SameLine();
        if(ui::Button("attach-note","В чат",ui::Icon::Chat,0,true) && !current.empty()){
            auto text=editor.model.Selected();if(text.empty())text=editor.model.Utf8();
            text="Заметка «"+current.value("path","")+"»:\n"+text;
            if(Decode(text).size()>12000)status="Для чата выделите фрагмент до 12000 символов. Текст не отправлен";else attach(text);
        }
        ImGui::EndChild();
        if(filesRequested)ImGui::OpenPopup("notes-files");
        ImGui::SetNextWindowPos(ImVec2(ImGui::GetIO().DisplaySize.x*.5f,ImGui::GetIO().DisplaySize.y*.5f),ImGuiCond_Appearing,ImVec2(.5f,.5f));
        ImGui::SetNextWindowSizeConstraints(ImVec2(200,120),ImVec2(ImGui::GetIO().DisplaySize.x-40,ImGui::GetIO().DisplaySize.y-40));
        if (ImGui::BeginPopupModal("notes-files",nullptr,ImGuiWindowFlags_AlwaysAutoResize|ImGuiWindowFlags_NoTitleBar)) {
            if(ImGui::IsKeyPressed(ImGuiKey_Escape))ImGui::CloseCurrentPopup();
            ui::Text("Файлы и папки",1.4f,true);ui::Text("TXT-заметки в папке Notes",.85f,false,ImGuiCol_TextDisabled);ui::Divider();
            if (FileMenuItem("Новая заметка", "Ctrl+N")) FileDialog("create",Parent()+"Новая заметка.txt");
            if (FileMenuItem("Новая папка")) FileDialog("folder",Parent()+"Новая папка");
            if (FileMenuItem("Переименовать / переместить",nullptr,false,!selected.empty())) FileDialog("move",selected,selected);
            if (FileMenuItem("Сохранить копию",nullptr,false,!current.empty())) FileDialog("copy",Parent()+"Копия.txt");
            if (FileMenuItem("Перечитать с диска",nullptr,false,!current.empty())) {auto path=current.value("path","");Guard([this,path]{Load(path);});}
            if (FileMenuItem("В корзину",nullptr,false,!selected.empty())) FileDialog("delete",selected,selected);
            if (FileMenuItem("Корзина / восстановление")){ImGui::CloseCurrentPopup();Call("notes_trash",Json::object(),[this](const Json& result){trashItems=result;showTrash=true;});}
            if(FileMenuItem("Черновики / восстановление")){ImGui::CloseCurrentPopup();Recoveries();}
            if (FileMenuItem("Обновить список")) Refresh();
            ui::Divider();if(ui::Button("close-files","Закрыть",ui::Icon::Close))ImGui::CloseCurrentPopup();
            ImGui::EndPopup();
        }

        // Popups are scoped to this panel, independent of the tree/editor child IDs.
        if(dialogRequested){ImGui::OpenPopup("Файл блокнота");dialogRequested=false;}
        ImGui::SetNextWindowPos(ImVec2(ImGui::GetIO().DisplaySize.x*.5f,ImGui::GetIO().DisplaySize.y*.5f),ImGuiCond_Appearing,ImVec2(.5f,.5f));
        ImGui::SetNextWindowSizeConstraints(ImVec2(200,120),ImVec2(ImGui::GetIO().DisplaySize.x-40,ImGui::GetIO().DisplaySize.y-40));
        if(ImGui::BeginPopupModal("Файл блокнота",nullptr,ImGuiWindowFlags_AlwaysAutoResize|ImGuiWindowFlags_NoTitleBar)){
            if(ImGui::IsKeyPressed(ImGuiKey_Escape))ImGui::CloseCurrentPopup();
            ui::Text(dialogKind=="delete"?"Переместить в корзину?":dialogKind=="folder"?"Новая папка":dialogKind=="move"?"Переименовать / переместить":dialogKind=="create"?"Новая заметка":"Файл блокнота",1.4f,true);
            ui::Text("Путь относительно Notes",.85f,false,ImGuiCol_TextDisabled);ImGui::Spacing();
            ImGui::SetNextItemWidth(std::min(450*scale,ImGui::GetIO().DisplaySize.x-120));ImGui::InputText("##note-name",name,sizeof(name));
            ui::Text("Существующие файлы не перезаписываются",.8f,false,ImGuiCol_TextDisabled);ui::Divider();
            if(ui::Button("confirm-file",dialogKind=="delete"?"В корзину":"Подтвердить",ui::Icon::Check,0,true)){
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
            ImGui::SameLine();if(ui::Button("cancel-file","Отмена",ui::Icon::Close))ImGui::CloseCurrentPopup();
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
    bool FileMenuItem(const char* title,const char* shortcut=nullptr,bool selectedItem=false,bool enabled=true){
        (void)shortcut;(void)selectedItem;
        ImGui::BeginDisabled(!enabled);
        const float width=std::min(ui::Px(420),ImGui::GetIO().DisplaySize.x-ui::Px(80));
        bool clicked=ui::Button(title,title,ui::Icon::Files,width);ImGui::EndDisabled();return clicked;
    }
    void FileDialog(std::string kind,std::string initial="",std::string source="") {
        ImGui::CloseCurrentPopup();Dialog(kind,initial,source);
    }
    void Recoveries(){
        const auto expectedRoot=root;
        Queue([expectedRoot]{
            Json result=Json::array();auto dir=Data()/L"notes-recovery";
            if(std::filesystem::exists(dir))for(const auto& entry:std::filesystem::directory_iterator(dir))if(entry.path().extension()==L".json"){
                try{std::ifstream stream(entry.path());Json value;stream>>value;if(value.value("root","")==expectedRoot)result.push_back(value);}catch(...){}
            }return result;
        },[this](const Json& value){recoveryItems=value;showRecovery=true;});
    }
    bool focusFind=false,showTrash=false,showRecovery=false;
    std::string pendingDialog,pendingName,pendingSource,recoveryText;
};
}
