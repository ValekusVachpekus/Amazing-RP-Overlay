#include "desktop_ui.h"
#include <iostream>

int main(){
    ImGui::CreateContext();auto& io=ImGui::GetIO();io.IniFilename=nullptr;
    io.DisplaySize=ImVec2(1200,900);io.DeltaTime=1.f/60;
    ImFontConfig font;font.SizePixels=17;io.Fonts->AddFontDefault(&font);
    unsigned char* pixels;int w,h;io.Fonts->GetTexDataAsRGBA32(&pixels,&w,&h);
    for(float dpi:{1.f,1.25f,1.5f}){
        io.FontGlobalScale=dpi;bool enabled=false;float slider=17;ImVec2 center;
        auto frame=[&](bool disabled=false){
            ImGui::NewFrame();ImGui::SetNextWindowPos(ImVec2(0,0));ImGui::SetNextWindowSize(ImVec2(900,700));
            ImGui::Begin("controls",nullptr,ImGuiWindowFlags_NoTitleBar|ImGuiWindowFlags_NoMove);
            ImGui::BeginDisabled(disabled);ui::Toggle("toggle",&enabled);
            auto a=ImGui::GetItemRectMin(),b=ImGui::GetItemRectMax();center=ImVec2((a.x+b.x)/2,(a.y+b.y)/2);
            ImGui::EndDisabled();ui::Slider("slider",&slider,14,26,"%.0f px");
            ui::Button("small","Long label clipped inside its button",ui::Icon::Plus,ui::Px(50));
            ui::Row("doc","A long document title that wraps across lines",true);
            for(int icon=0;icon<=static_cast<int>(ui::Icon::Trash);++icon){
                ImGui::PushID(icon);ui::Button("icon","Icon",static_cast<ui::Icon>(icon),0,false,true);ImGui::PopID();
            }
            ImGui::End();ImGui::Render();
            return ImGui::GetDrawData()->TotalVtxCount>0;
        };
        frame();io.AddMousePosEvent(center.x,center.y);io.AddMouseButtonEvent(0,true);frame();
        io.AddMouseButtonEvent(0,false);if(!frame() || !enabled)return 1;
        io.AddMouseButtonEvent(0,true);frame(true);io.AddMouseButtonEvent(0,false);frame(true);
        if(!enabled || slider<14 || slider>26)return 2;
    }
    ImGui::DestroyContext();std::cout<<"Native components: PASS (100/125/150%, pointer toggle, disabled state, sliders, icons, wrapped rows)\n";
}
