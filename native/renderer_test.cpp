#include "rpc.h"
#include <imgui.h>
#include <iostream>

int main() {
    ImGui::CreateContext();
    auto& io = ImGui::GetIO();
    io.DisplaySize = ImVec2(900, 600); io.DeltaTime = 1.f / 60;
    io.IniFilename = nullptr;
    io.Fonts->AddFontDefault();
    unsigned char* pixels; int width, height;
    io.Fonts->GetTexDataAsRGBA32(&pixels, &width, &height);
    Json doc = {{"posts", Json::array({{{"post_id", "1"}, {"blocks", Json::array({
        {{"tag", "p"}, {"children", Json::array({{{"tag", "text"}, {"text", "Law LAW law: wrapped text and another law"}}})}},
        {{"tag", "table"}, {"children", Json::array({{{"tag", "tr"}, {"children", Json::array({
            {{"tag", "td"}, {"children", Json::array({{{"tag", "text"}, {"text", "law"}}})}}
        })}}})}}
    })}}})}};
    auto& blocks = doc["posts"][0]["blocks"];
    for (int i = 0; i < 30; ++i)
        blocks.insert(blocks.end() - 1, Json{{"tag", "text"}, {"text", "Long document, filler paragraph."}});
    DocumentSearch search; search.query = "LAW"; search.active = 4; search.jump = true;
    for (int frame = 0; frame < 3; ++frame) {
        ImGui::NewFrame();
        ImGui::SetNextWindowSize(ImVec2(240, 240));
        ImGui::Begin("test");
        DrawDocument(doc, &search);
        if (frame == 2 && ImGui::GetScrollY() <= 0) return 3;
        ImGui::End(); ImGui::Render();
        if (search.total != 5 || search.jump) return 1;
        if (ImGui::GetDrawData()->TotalVtxCount == 0) return 2;
    }
    search.query = "missing";
    ImGui::NewFrame(); ImGui::Begin("test"); DrawDocument(doc, &search); ImGui::End(); ImGui::Render();
    if (search.total != 0) return 4;
    ImGui::DestroyContext();
    std::cout << "Renderer: PASS (wrapped text, table matches, case folding, active match)\n";
}
