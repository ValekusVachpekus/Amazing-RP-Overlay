#include "settings_panel.h"
#include <cstring>

void ApplyAppearance(const Appearance& appearance, float fade) {
    // Rebuild from an unscaled baseline, so moving the slider never compounds sizes.
    ImGui::GetStyle() = ImGuiStyle();
    if (appearance.light) ImGui::StyleColorsLight(); else ImGui::StyleColorsDark();
    auto& s = ImGui::GetStyle();
    s.WindowRounding = 10; s.ChildRounding = 8; s.FrameRounding = 6;
    s.GrabRounding = 5; s.ScrollbarRounding = 6;
    s.WindowPadding = ImVec2(14, 12); s.FramePadding = ImVec2(12, 8);
    s.ItemSpacing = ImVec2(10, 8); s.ScrollbarSize = 13;
    auto color = [&](ImGuiCol target, ImVec4 dark, ImVec4 light) { s.Colors[target] = appearance.light ? light : dark; };
    color(ImGuiCol_WindowBg, {.045f,.065f,.095f,1}, {.93f,.95f,.98f,1});
    color(ImGuiCol_ChildBg, {.06f,.085f,.12f,1}, {.98f,.985f,1,1});
    color(ImGuiCol_TitleBg, {.08f,.14f,.21f,1}, {.79f,.86f,.93f,1});
    s.Colors[ImGuiCol_TitleBgActive] = s.Colors[ImGuiCol_TitleBg];
    color(ImGuiCol_Button, {.13f,.22f,.31f,1}, {.80f,.87f,.94f,1});
    color(ImGuiCol_ButtonHovered, {.19f,.34f,.46f,1}, {.66f,.79f,.91f,1});
    color(ImGuiCol_FrameBg, {.10f,.15f,.22f,1}, {.86f,.90f,.95f,1});
    color(ImGuiCol_Border, {.19f,.26f,.34f,.7f}, {.60f,.68f,.76f,.8f});
    color(ImGuiCol_Text, {.88f,.92f,.96f,1}, {.10f,.15f,.21f,1});
    color(ImGuiCol_TextDisabled, {.55f,.62f,.69f,1}, {.35f,.41f,.48f,1});
    color(ImGuiCol_Header, {.14f,.25f,.36f,1}, {.72f,.84f,.94f,1});
    color(ImGuiCol_HeaderHovered, {.12f,.17f,.24f,1}, {.84f,.90f,.96f,1});
    color(ImGuiCol_TableHeaderBg, {.14f,.22f,.31f,1}, {.80f,.87f,.94f,1});
    color(ImGuiCol_TableRowBg, {.09f,.13f,.19f,1}, {.96f,.97f,.99f,1});
    color(ImGuiCol_CheckMark, {.37f,.72f,.90f,1}, {.08f,.36f,.60f,1});
    color(ImGuiCol_PlotHistogram, {.74f,.45f,.10f,1}, {1,.68f,.22f,1});
    color(ImGuiCol_PlotHistogramHovered, {.37f,.31f,.11f,1}, {1,.88f,.55f,1});
    s.ScaleAllSizes(appearance.font / 17.f);
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
    maxTokens = effective.value("max_tokens", 1500);
    key.fill(0);
    openedValues = Values();
}

void SettingsPanel::Cancel() { open = false; draft = saved; key.fill(0); revealKey = false; }

Json SettingsPanel::Values() const {
    Json value = {{"opacity", draft.opacity}, {"font_size", draft.font}, {"theme", draft.light ? "light" : "dark"},
                 {"animations", draft.animations}, {"ai_provider", provider ? "gemini" : "openai"},
                 {"ai_base_url", address.data()}, {"ai_model", model.data()}, {"max_tokens", maxTokens}};
    if (key[0]) value["ai_api_key"] = key.data();
    return value;
}

SettingsPanel::Action SettingsPanel::Draw(bool busy, bool testing, const std::string& message) {
    Action action = Action::None;
    ImGui::BeginChild("settings-body", ImVec2(0, -ImGui::GetFrameHeightWithSpacing() - 12), ImGuiChildFlags_Borders);
    ImGui::TextUnformatted("НАСТРОЙКИ");
    ImGui::TextWrapped("Оформление видно сразу. «Отмена» вернёт прежний вид.");
    ImGui::Separator();
    ImGui::BeginDisabled(busy);
    ImGui::SetNextItemWidth(-1);
    float percent = draft.opacity * 100;
    if (ImGui::SliderFloat("##opacity", &percent, 30, 100, "Прозрачность всего окна: %.0f%%")) draft.opacity = percent / 100;
    ImGui::SetNextItemWidth(-1);
    ImGui::SliderInt("##font", &draft.font, 14, 26, "Размер шрифта везде: %d px");
    ImGui::Checkbox("Светлая тема", &draft.light);
    ImGui::Checkbox("Плавное появление и скрытие (0,2 с)", &draft.animations);
    ImGui::Spacing(); ImGui::Separator(); ImGui::TextUnformatted("ИИ / API");
    ImGui::SetNextItemWidth(-1);
    if (ImGui::Combo("##provider", &provider, "OpenRouter / OpenAI-compatible\0Gemini\0")) {
        const char* base = provider ? "https://generativelanguage.googleapis.com/v1beta" : "https://openrouter.ai/api/v1";
        address.fill(0); std::memcpy(address.data(), base, std::strlen(base));
        const char* defaultModel = provider ? "" : "openrouter/free";
        model.fill(0); std::memcpy(model.data(), defaultModel, std::strlen(defaultModel));
    }
    ImGui::TextUnformatted("Адрес API"); ImGui::SetNextItemWidth(-1);
    ImGui::InputText("##api-address", address.data(), address.size());
    ImGui::TextUnformatted("Модель"); ImGui::SetNextItemWidth(-1);
    ImGui::InputText("##api-model", model.data(), model.size());
    ImGui::TextUnformatted(effective.value("has_api_key", false) ? "API-ключ настроен. Пустое поле сохраняет текущий ключ." : "API-ключ не задан");
    if (effective.value("api_key_from_environment", false))
        ImGui::TextWrapped("Ключ задан переменной окружения AI_API_KEY и имеет приоритет над вводом.");
    ImGui::SetNextItemWidth(-1);
    ImGui::InputText("##api-key", key.data(), key.size(), revealKey ? 0 : ImGuiInputTextFlags_Password);
    ImGui::Checkbox("Показать введённый ключ", &revealKey);
    ImGui::SetNextItemWidth(-1);
    ImGui::SliderInt("##max-tokens", &maxTokens, 128, 16000, "Лимит ответа: %d токенов");
    auto overrides = effective.value("env_overrides", Json::array());
    if (!overrides.empty()) ImGui::TextWrapped("Поля переопределяются окружением/.env: %s", overrides.dump().c_str());
    ImGui::TextWrapped("Ключ хранится отдельно от DLL и настроек оформления. Проверка использует введённые поля без сохранения.");
    if (ImGui::Button(testing ? "Проверяем…" : "Проверить соединение") && !testing) action = Action::Test;
    if (testing) { ImGui::SameLine(); if (ImGui::Button("Остановить проверку")) action = Action::CancelTest; }
    ImGui::EndDisabled();
    if (!message.empty()) ImGui::TextWrapped("%s", message.c_str());
    ImGui::EndChild();
    ImGui::BeginDisabled(busy || testing);
    if (ImGui::Button(busy ? "Сохраняем…" : "Сохранить")) { action = Action::Save; awaitingSave = true; }
    ImGui::EndDisabled();
    ImGui::SameLine();
    ImGui::BeginDisabled(busy);
    if (ImGui::Button("Отмена")) { if (testing) action = Action::CancelTest; Cancel(); }
    ImGui::EndDisabled();
    return action;
}
