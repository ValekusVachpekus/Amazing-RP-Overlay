#include "settings_panel.h"
#include "desktop_ui.h"
#include <cstring>

void ApplyAppearance(const Appearance& appearance, float fade) {
    // Rebuild from an unscaled baseline, so moving the slider never compounds sizes.
    ImGui::GetStyle() = ImGuiStyle();
    if (appearance.light) ImGui::StyleColorsLight(); else ImGui::StyleColorsDark();
    auto& s = ImGui::GetStyle();
    s.WindowRounding = 12; s.ChildRounding = 10; s.FrameRounding = 8;
    s.GrabRounding = 12; s.ScrollbarRounding = 8;
    s.WindowPadding = ImVec2(16, 16); s.FramePadding = ImVec2(14, 10);
    s.ItemSpacing = ImVec2(8, 8); s.ScrollbarSize = 10;
    s.WindowBorderSize=1; s.ChildBorderSize=1; s.FrameBorderSize=1;
    auto color = [&](ImGuiCol target, ImVec4 dark, ImVec4 light) { s.Colors[target] = appearance.light ? light : dark; };
    auto hex=[](unsigned v){return ImVec4(((v>>16)&255)/255.f,((v>>8)&255)/255.f,(v&255)/255.f,1);};
    color(ImGuiCol_WindowBg, hex(0x15161e), hex(0xf0edf5));
    color(ImGuiCol_ChildBg, hex(0x1c1d27), hex(0xfaf8fc));
    color(ImGuiCol_TitleBg, hex(0x181922), hex(0xeae5f0));
    s.Colors[ImGuiCol_TitleBgActive] = s.Colors[ImGuiCol_TitleBg];
    color(ImGuiCol_Button, hex(0x181922), hex(0xf4f0f8));
    color(ImGuiCol_ButtonHovered, hex(0x30283f), hex(0xe9dff5));
    color(ImGuiCol_ButtonActive, hex(0x403052), hex(0xdcc9ef));
    color(ImGuiCol_FrameBg, hex(0x23232f), hex(0xede8f3));
    color(ImGuiCol_FrameBgHovered, hex(0x30283f), hex(0xe5daef));
    color(ImGuiCol_FrameBgActive, hex(0x30283f), hex(0xe5daef));
    color(ImGuiCol_Border, hex(0x343240), hex(0xd8d0e1));
    color(ImGuiCol_Separator, hex(0x343240), hex(0xd8d0e1));
    color(ImGuiCol_Text, hex(0xf3f1f7), hex(0x292230));
    color(ImGuiCol_TextDisabled, hex(0x9995aa), hex(0x746b80));
    color(ImGuiCol_Header, hex(0x403052), hex(0xe4d3f5));
    color(ImGuiCol_HeaderHovered, hex(0x30283f), hex(0xeee3f6));
    color(ImGuiCol_HeaderActive, hex(0x403052), hex(0xe4d3f5));
    color(ImGuiCol_TableHeaderBg, hex(0x30283f), hex(0xe9dff5));
    color(ImGuiCol_TableRowBg, hex(0x23232f), hex(0xf4f0f8));
    color(ImGuiCol_CheckMark, hex(0xbda0ff), hex(0x7951ac));
    s.Colors[ImGuiCol_SliderGrab]=s.Colors[ImGuiCol_CheckMark];
    s.Colors[ImGuiCol_SliderGrabActive]=s.Colors[ImGuiCol_CheckMark];
    s.Colors[ImGuiCol_TextSelectedBg]=s.Colors[ImGuiCol_Header];
    color(ImGuiCol_PopupBg, hex(0x1c1d27), hex(0xfaf8fc));
    s.Colors[ImGuiCol_ModalWindowDimBg]=ImVec4(0,0,0,.65f);
    color(ImGuiCol_PlotHistogram, {.74f,.45f,.10f,1}, {1,.68f,.22f,1});
    color(ImGuiCol_PlotHistogramHovered, {.37f,.31f,.11f,1}, {1,.88f,.55f,1});
    s.ScaleAllSizes(appearance.font / 17.f);
    // ImGui truncates MouseCursorScale when scaling the style. Fonts below 17 px
    // would round the cursor down to zero and make input mode look cursorless.
    s.MouseCursorScale = std::max(1.f, appearance.font / 17.f);
    s.Alpha = appearance.opacity * fade;
}

void SettingsPanel::Receive(const Json& settings, bool savedResult) {
    const bool refreshDraft = open && !awaitingSave && Values() == openedValues;
    effective = settings;
    saved = Appearance::Read(settings);
    if (savedResult) { awaitingSave = false; open = false; key.fill(0); }
    if (!open) draft = saved;
    else if (refreshDraft) Open();
}

void SettingsPanel::Open() {
    draft = saved; open = true; revealKey = false;
    provider = effective.value("ai_provider", "openai") == "gemini" ? 1 : 0;
    auto copy = [](auto& buffer, const std::string& value) {
        buffer.fill(0); const size_t count = std::min(buffer.size() - 1, value.size());
        std::memcpy(buffer.data(), value.data(), count);
    };
    copy(address, effective.value("ai_base_url", "https://openrouter.ai/api/v1"));
    copy(model, effective.value("ai_model", "openrouter/free"));
    copy(notesDirectory,effective.value("notes_dir",""));
    copy(notesHotkey,effective.value("notes_hotkey","Ctrl+N"));
    maxTokens = effective.value("max_tokens", 1500);
    key.fill(0);
    const char* keys[]={"toggle_hotkey","input_hotkey","prev_law_hotkey","next_law_hotkey","scroll_up_hotkey","scroll_down_hotkey"};
    const char* defaults[]={"Ctrl+L","Alt+F","Alt+Left","Alt+Right","Alt+Up","Alt+Down"};
    for(int i=0;i<6;++i)copy(hotkeys[i],effective.value(keys[i],defaults[i]));
    openedValues = Values();
}

void SettingsPanel::Cancel() { open = false; draft = saved; key.fill(0); revealKey = false; }

Json SettingsPanel::Values() const {
    Json value = {{"opacity", draft.opacity}, {"font_size", draft.font}, {"theme", draft.light ? "light" : "dark"},
                 {"animations", draft.animations}, {"ai_provider", provider ? "gemini" : "openai"},
                 {"ai_base_url", address.data()}, {"ai_model", model.data()}, {"max_tokens", maxTokens}};
    value["notes_dir"]=notesDirectory.data();value["notes_hotkey"]=notesHotkey.data();
    const char* keys[]={"toggle_hotkey","input_hotkey","prev_law_hotkey","next_law_hotkey","scroll_up_hotkey","scroll_down_hotkey"};
    for(int i=0;i<6;++i)value[keys[i]]=hotkeys[i].data();
    if (key[0]) value["ai_api_key"] = key.data();
    return value;
}

SettingsPanel::Action SettingsPanel::Draw(bool busy, bool testing, const std::string& message) {
    Action action=Action::None;
    const float scale=ui::Scale(), available=ImGui::GetContentRegionAvail().x;
    const bool narrow=available<780*scale;
    if(!narrow) {
        ImGui::BeginChild("settings-navigation",ImVec2(250*scale,0),ImGuiChildFlags_Borders);
        ui::Text("Настройки",1.25f,true);ImGui::Spacing();
        const char* labels[]={"Оформление","Управление и хоткеи","ИИ и API"};
        for(int i=0;i<3;++i){ImGui::PushID(i);if(ui::Row("section",labels[i],section==i,i==2 ? ui::Icon::Chat : ui::Icon::Settings))section=i;ImGui::PopID();}
        ImGui::EndChild();ImGui::SameLine();
    }
    ImGui::BeginChild("settings-content",ImVec2(0,0),ImGuiChildFlags_Borders);
    if(narrow){
        const char* labels[]={"Оформление","Клавиши","ИИ / API"};
        for(int i=0;i<3;++i){if(i)ImGui::SameLine();ImGui::PushID(i);if(ui::Button("section",labels[i],i==2?ui::Icon::Chat:ui::Icon::Settings,0,section==i))section=i;ImGui::PopID();}
        ui::Divider();
    }
    const float reserve=ui::Px(message.empty()?72:116);
    ImGui::BeginChild("settings-fields",ImVec2(0,-reserve),ImGuiChildFlags_None);
    ui::Text(section==0?"Внешний вид":section==1?"Управление и горячие клавиши":"ИИ-помощник и API",1.55f,true);
    ui::Text(section==0?"Изменения оформления видны сразу. «Отмена» вернёт прежний вид.":section==1?"Сочетания применяются после сохранения. Допустимы F1–F24 без модификаторов.":"Параметры провайдера и подключения. Сохранённый ключ не раскрывается.",.85f,false,ImGuiCol_TextDisabled);
    ui::Divider();
    ImGui::BeginDisabled(busy);
    auto field=[](const char* title,const char* id,char* value,size_t count){
        ui::Text(title,.9f);ImGui::SetNextItemWidth(-1);ImGui::InputText(id,value,count);ImGui::Spacing();
    };
    if(section==0){
        ui::Text("Прозрачность панели",1.f,true);
        ui::Text("Всё окно целиком · от 30 до 100 процентов",.85f,false,ImGuiCol_TextDisabled);
        ImGui::SetNextItemWidth(-1);float percent=draft.opacity*100;
        if(ui::Slider("##opacity",&percent,30,100,"%.0f%%"))draft.opacity=percent/100;
        ui::Divider();ui::Text("Размер текста",1.f,true);
        ui::Text("Единый масштаб интерфейса и блокнота · 14–26 px",.85f,false,ImGuiCol_TextDisabled);
        float font=static_cast<float>(draft.font);if(ui::Slider("##font",&font,14,26,"%.0f px"))draft.font=static_cast<int>(std::round(font));
        ui::Divider();
        ui::Toggle("##animations",&draft.animations);ImGui::SameLine();ui::Text("Плавное появление и скрытие · 0,2 с");
        ImGui::Spacing();ui::Toggle("##theme",&draft.light);ImGui::SameLine();ui::Text("Светлая тема");
    }else if(section==1){
        auto binding=[&](const char* title,const char* id,char* value,size_t count){
            const float available=ImGui::GetContentRegionAvail().x;
            if(available<450*scale){field(title,id,value,count);return;}
            const float y=ImGui::GetCursorPosY(),x=ImGui::GetCursorPosX(),width=190*scale;
            ImGui::SetCursorPosY(y+10*scale);ui::Text(title,.9f,false,ImGuiCol_Text,available-width-16*scale);
            const float end=ImGui::GetCursorPosY();ImGui::SetCursorPos(ImVec2(x+available-width,y));
            ImGui::SetNextItemWidth(width);ImGui::InputText(id,value,count);
            ImGui::SetCursorPos(ImVec2(x,std::max(end,ImGui::GetCursorPosY())));ImGui::Spacing();
        };
        const char* labels[]={"Показать / скрыть оверлей","Режим ввода / поиск","Предыдущий закон","Следующий закон","Прокрутка вверх","Прокрутка вниз"};
        for(int i=0;i<6;++i){ImGui::PushID(i);binding(labels[i],"##binding",hotkeys[i].data(),hotkeys[i].size());ImGui::PopID();}
        binding("Открыть блокнот / новая заметка","##notes-hotkey",notesHotkey.data(),notesHotkey.size());
        ui::Text("Ctrl+F — найти; F3 / Shift+F3 — совпадения; Ctrl+S — сохранить заметку.",.85f,false,ImGuiCol_TextDisabled);
        ui::Divider();field("Папка заметок","##notes-dir",notesDirectory.data(),notesDirectory.size());
        ui::Text("Пусто — Notes рядом с программой. Смена пути не переносит файлы.",.85f,false,ImGuiCol_TextDisabled);
    }else{
        ui::Text("Провайдер",.9f);ImGui::SetNextItemWidth(-1);
        if(ImGui::Combo("##provider",&provider,"OpenRouter / OpenAI-compatible\0Gemini\0")){
            const char* base=provider?"https://generativelanguage.googleapis.com/v1beta":"https://openrouter.ai/api/v1";
            address.fill(0);std::memcpy(address.data(),base,std::strlen(base));
            const char* defaultModel=provider?"":"openrouter/free";model.fill(0);std::memcpy(model.data(),defaultModel,std::strlen(defaultModel));
        }
        ImGui::Spacing();field("Адрес API","##api-address",address.data(),address.size());
        field("Модель","##api-model",model.data(),model.size());
        ui::Text(effective.value("has_api_key",false)?"API-ключ настроен. Пустое поле сохраняет текущий ключ.":"API-ключ не задан",.85f,false,ImGuiCol_TextDisabled);
        ImGui::SetNextItemWidth(-1);ImGui::InputText("##api-key",key.data(),key.size(),revealKey?0:ImGuiInputTextFlags_Password);
        ui::Toggle("##reveal",&revealKey);ImGui::SameLine();ui::Text("Показать введённый ключ",.9f);
        ImGui::Spacing();ui::Text("Лимит ответа",.9f);float tokens=static_cast<float>(maxTokens);if(ui::Slider("##max-tokens",&tokens,128,16000,"%.0f токенов"))maxTokens=static_cast<int>(std::round(tokens));
        if(effective.value("api_key_from_environment",false))ui::Text("AI_API_KEY из окружения имеет приоритет.",.85f,false,ImGuiCol_TextDisabled);
        auto overrides=effective.value("env_overrides",Json::array());
        if(!overrides.empty())ui::Text("Поля переопределены окружением/.env: "+overrides.dump(),.85f,false,ImGuiCol_TextDisabled);
        ImGui::Spacing();if(ui::Button("test",testing?"Проверяем…":"Проверить соединение",ui::Icon::Refresh) && !testing)action=Action::Test;
        if(testing && ui::Button("cancel-test","Остановить проверку",ui::Icon::Close))action=Action::CancelTest;
    }
    ImGui::EndDisabled();ImGui::EndChild();
    if(!message.empty())ui::Text(message,.85f,false,ImGuiCol_TextDisabled);
    ui::Divider();
    ImGui::BeginDisabled(busy || testing);
    if(ui::Button("save",busy?"Сохраняем…":"Сохранить",ui::Icon::Check,0,true)){action=Action::Save;awaitingSave=true;}
    ImGui::EndDisabled();ImGui::SameLine();ImGui::BeginDisabled(busy);
    if(ui::Button("cancel","Отмена",ui::Icon::Close)){if(testing)action=Action::CancelTest;Cancel();}
    ImGui::EndDisabled();ImGui::EndChild();return action;
}
