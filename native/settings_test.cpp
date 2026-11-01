#include "settings_panel.h"
#include "navigation.h"
#include <iostream>

int main() {
    TabNavigation tabs;
    for(int cycle=0;cycle<100;++cycle){
        tabs.Select(MainTab::Notebook);if(tabs.active!=MainTab::Notebook)return 20;
        tabs.Select(MainTab::Settings);if(tabs.active!=MainTab::Settings || tabs.content!=MainTab::Notebook)return 21;
        tabs.Select(MainTab::Notebook);if(tabs.active!=MainTab::Notebook)return 22;
        tabs.Select(MainTab::Laws);if(tabs.active!=MainTab::Laws)return 23;
    }
    Fade fade;
    if (std::abs(fade.Update(true, true, .1f) - .5f) > .001f) return 1;
    const float before = fade.position;
    fade.Update(false, true, .02f);
    if (!(fade.position < before && fade.position > 0)) return 2;
    if (fade.Update(false, false, .01f) != 0 || fade.Update(true, false, 0) != 1) return 3;
    for (int cycle = 0; cycle < 100; ++cycle) {
        if (fade.Update(false, true, .2f) != 0 || fade.Update(true, true, .2f) != 1) return 4;
    }
    SettingsPanel panel;
    panel.Receive({{"font_size", 18}, {"opacity", .7}, {"theme", "light"}, {"animations", false}, {"ai_model", "saved-model"}});
    panel.Open(); panel.draft.font = 26; panel.draft.opacity = .3f; panel.key[0] = 'x';
    const auto draft = panel.Values();
    if (draft["font_size"] != 26 || draft["ai_api_key"] != "x") return 5;
    panel.Cancel();
    if (panel.Current().font != 18 || panel.key[0] || !panel.Current().light) return 6;
    panel.Open(); panel.awaitingSave = true;
    panel.Receive({{"font_size", 20}}, false);
    if (!panel.open) return 7; // A background refresh must not pretend a save succeeded.
    panel.Receive({{"font_size", 22}, {"animations", true}}, true);
    if (panel.open || panel.Current().font != 22) return 8;
    ImGui::CreateContext();
    auto& io = ImGui::GetIO(); io.IniFilename = nullptr;
    io.DisplaySize = ImVec2(1400, 1000); io.DeltaTime = 1.f / 60;
    io.Fonts->AddFontDefault(); unsigned char* pixels; int width, height;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &width, &height);
    for (int size = 14; size <= 26; ++size) {
        Appearance look; look.font = size;
        ApplyAppearance(look, 1);
        if (ImGui::GetStyle().MouseCursorScale < 1.f) return 13;
        io.MouseDrawCursor = true;
        io.AddMousePosEvent(120, 120);
        ImGui::NewFrame(); ImGui::Render();
        if (ImGui::GetDrawData()->TotalVtxCount == 0) return 14;
    }
    io.MouseDrawCursor = false;
    for (bool light : {false, true}) {
        Appearance look; look.light = light; look.font = 26; look.opacity = .3f;
        ApplyAppearance(look, .5f);
        if (std::abs(ImGui::GetStyle().Alpha - .15f) > .001f) return 9;
        if (std::abs(ImGui::GetStyle().WindowPadding.x - std::floor(14 * 26.f / 17)) > .01f) return 12;
        if (light != (ImGui::GetStyleColorVec4(ImGuiCol_Text).x < .5f)) return 10;
        ImGui::NewFrame(); ImGui::SetNextWindowSize(ImVec2(1100, 900)); ImGui::Begin("test");
        panel.Open(); panel.Draw(false, false, "Connection test result");
        ImGui::End(); ImGui::Render();
        if (!ImGui::GetDrawData()->TotalVtxCount) return 11;
    }
    ImGui::DestroyContext();
    std::cout << "Settings: PASS (save/cancel, key clearing, themes, alpha, scale, reversible 200ms fade)\n";
}
