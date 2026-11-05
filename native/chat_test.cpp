#include "chat_panel.h"
#include <iostream>
#include <cstring>

int main(){
    chat_ui::ContextSelection selection;selection.mode="selected";selection.selected={"uk","tk"};
    chat_ui::ContextSelection restored;restored.Load(selection.Save());
    if(restored.Save()!=selection.Save() || restored.Request("other")["doc_ids"].size()!=2)return 9;
    restored.mode="current";if(restored.Request("uk")["doc_ids"]!=Json::array({"uk"}) || !restored.Request("")["doc_ids"].empty())return 10;
    restored.mode="none";const auto plain=restored.Request("");
    if(!plain["doc_ids"].is_null() || plain["context_mode"]!="none" || !chat_ui::MatchesTitle("Трудовой КОДЕКС","кодекс"))return 11;
    const auto history=chat_ui::ContextHistory(Json::array({
        {{"role","assistant"},{"content","law text"},{"status","complete"},{"context",selection.Request("")}},
        {{"role","user"},{"content","hello"},{"status","complete"},{"context",plain}},
        {{"role","assistant"},{"content","partial"},{"status","streaming"},{"context",plain}},
        {{"role","assistant"},{"content","old laws"},{"status","complete"}}}),plain);
    if(history.size()!=1 || history[0]["content"]!="hello")return 12;
    const auto blocks=chat_ui::Parse("**Answer** [S1]\n\n| A | B |\n|---|---|\n| 1 | value |\n");
    if(blocks.size()!=2 || blocks[1].rows.size()!=2)return 1;
    bool bold=false,citation=false;
    for(const auto& r:chat_ui::Inline("**Bold** and *source [S1]*")){bold|=r.bold;citation|=r.label=="S1";}
    if(!bold || !citation || chat_ui::Source(Json::array(),"S99"))return 2;
    ImGui::CreateContext();auto& io=ImGui::GetIO();io.IniFilename=nullptr;io.DisplaySize=ImVec2(1400,1000);io.DeltaTime=1.f/60;
    ImFontConfig font;font.SizePixels=17;io.Fonts->AddFontDefault(&font);unsigned char* pixels;int w,h;io.Fonts->GetTexDataAsRGBA32(&pixels,&w,&h);
    for(float dpi:{1.f,1.25f,1.5f}){
        io.FontGlobalScale=dpi;chat_ui::Panel panel;char question[1024]="Question";bool busy=false,stopping=false;chat_ui::Actions action;
        Json messages=Json::array({{{"id","u"},{"role","user"},{"content","What is article 51?"},{"status","complete"}},
                                  {{"id","a"},{"role","assistant"},{"content","**Answer** [S1]\n\n| Article | Meaning |\n|---|---|\n| 51 | Official powers |"},{"status","complete"},
                                   {"sources",Json::array({{{"label","S1"},{"fingerprint","hash"},{"heading","Article 51"},{"title","Code"}}})}}});
        auto frame=[&]{
            ImGui::NewFrame();ImGui::SetNextWindowPos(ImVec2(10,10));ImGui::SetNextWindowSize(ImVec2(440*dpi,630*dpi));
            ImGui::Begin("chat",nullptr,ImGuiWindowFlags_NoTitleBar|ImGuiWindowFlags_NoMove);
            action=panel.Draw(messages,question,sizeof(question),busy,stopping,true,"",Json::array({
                {{"id","uk"},{"title","Уголовный кодекс"}},{{"id","tk"},{"title","Трудовой кодекс"}}}));
            if(panel.composerBottom>ImGui::GetWindowPos().y+ImGui::GetWindowSize().y)return false;
            ImGui::End();ImGui::Render();return true;
        };
        if(!frame() || !frame())return 3;
        auto click=[&]{io.AddMousePosEvent(panel.sendCenter.x,panel.sendCenter.y);frame();io.AddMouseButtonEvent(0,true);frame();io.AddMouseButtonEvent(0,false);return frame();};
        if(!click() || !action.send || action.stop)return 4;
        busy=true;if(!frame() || !click() || !action.stop || action.send)return 5;
        stopping=true;frame();click();if(action.stop || action.send)return 6;
        busy=stopping=false;panel.Focus();frame();frame();
        io.AddKeyEvent(ImGuiKey_Enter,true);frame();io.AddKeyEvent(ImGuiKey_Enter,false);frame();
        if(action.send || !std::strchr(question,'\n'))return 7;
        io.AddKeyEvent(ImGuiMod_Ctrl,true);io.AddKeyEvent(ImGuiKey_Enter,true);frame();bool sent=action.send;
        io.AddKeyEvent(ImGuiKey_Enter,false);io.AddKeyEvent(ImGuiMod_Ctrl,false);frame();
        if(!sent)return 8;
        panel.context.mode="current";panel.context.selected={"uk"};const auto saved=panel.context.Save();
        panel.PreviewSelection();frame();frame();
        if(!ImGui::IsPopupOpen(nullptr,ImGuiPopupFlags_AnyPopupId|ImGuiPopupFlags_AnyPopupLevel))return 13;
        io.AddKeyEvent(ImGuiKey_Escape,true);frame();io.AddKeyEvent(ImGuiKey_Escape,false);frame();
        if(action.contextChanged || panel.context.Save()!=saved || ImGui::IsPopupOpen(nullptr,ImGuiPopupFlags_AnyPopupId|ImGuiPopupFlags_AnyPopupLevel))return 14;
    }
    ImGui::DestroyContext();std::cout<<"Chat: PASS (Markdown/table/citations, bottom composer, send/stop, Enter and Ctrl+Enter at 100/125/150%)\n";
}
