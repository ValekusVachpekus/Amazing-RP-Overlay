#pragma once
#include <imgui.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <string>
#include <cfloat>
#include <cstdio>

// Native design tokens sampled from the approved Obsidian PDF. All sizes are
// logical pixels; the existing shared font setting scales the entire interface.
namespace ui {
inline std::array<ImFont*,13> boldFonts{};
inline float Scale() { return ImGui::GetFontSize()/17.f; }
inline float Px(float value) { return value*Scale(); }
inline ImU32 Color(ImGuiCol col) { return ImGui::GetColorU32(col); }
inline ImU32 Alpha(ImU32 col) { return ImGui::GetColorU32(ImGui::ColorConvertU32ToFloat4(col)); }
inline ImFont* Bold(float pixels=0) { return boldFonts[std::clamp(static_cast<int>(std::round(pixels>0?pixels:ImGui::GetFontSize()))-14,0,12)]; }
inline void Text(const std::string& text, float factor=1.f, bool bold=false, ImGuiCol color=ImGuiCol_Text,float limit=0) {
    const float size=ImGui::GetFontSize()*factor, width=std::max(20.f,limit>0?limit:ImGui::GetContentRegionAvail().x);
    auto* font=bold && Bold(size) ? Bold(size) : ImGui::GetFont();
    const auto extent=font->CalcTextSizeA(size,FLT_MAX,width,text.c_str());
    ImGui::GetWindowDrawList()->AddText(font,size,ImGui::GetCursorScreenPos(),Color(color),text.c_str(),nullptr,width);
    ImGui::Dummy(ImVec2(std::min(width,extent.x),std::max(size,extent.y)));
}
enum class Icon { Book, Note, Settings, Star, Search, Left, Right, Close, Refresh, Plus, Chat, Expand, Files, Check, Trash, More, Folder };
inline void DrawIcon(Icon icon, ImVec2 origin, float size, ImU32 color) {
    auto* d=ImGui::GetWindowDrawList(); const float t=std::max(1.f,size/13.f);
    auto p=[&](float x,float y){return ImVec2(origin.x+x*size,origin.y+y*size);};
    auto line=[&](float x,float y,float a,float b){d->AddLine(p(x,y),p(a,b),color,t);};
    switch(icon) {
    case Icon::Book:case Icon::Note:case Icon::Files:
        d->AddRect(p(.18f,.1f),p(.82f,.9f),color,1,0,t);
        for(float y:{.34f,.5f,.66f})line(.32f,y,.69f,y);
        if(icon==Icon::Book)line(.25f,.1f,.25f,.9f);break;
    case Icon::Star: {
        ImVec2 vertices[10];for(int i=0;i<10;++i){float a=-1.5707963f+i*.6283185f,r=i%2 ? .2f : .46f;vertices[i]=p(.5f+cosf(a)*r,.5f+sinf(a)*r);}
        d->AddPolyline(vertices,10,color,ImDrawFlags_Closed,t);break;}
    case Icon::Search:d->AddCircle(p(.42f,.42f),size*.28f,color,16,t);line(.63f,.63f,.9f,.9f);break;
    case Icon::Close:line(.28f,.28f,.72f,.72f);line(.72f,.28f,.28f,.72f);break;
    case Icon::Left:case Icon::Right: {float sign=icon==Icon::Left ? -1.f : 1.f;line(.5f-sign*.3f,.5f,.5f+sign*.3f,.5f);line(.5f+sign*.3f,.5f,.5f,.2f);line(.5f+sign*.3f,.5f,.5f,.8f);break;}
    case Icon::Plus:line(.5f,.2f,.5f,.8f);line(.2f,.5f,.8f,.5f);break;
    case Icon::Check:line(.15f,.5f,.4f,.75f);line(.4f,.75f,.88f,.2f);break;
    case Icon::Expand:line(.2f,.4f,.2f,.2f);line(.2f,.2f,.4f,.2f);line(.6f,.2f,.8f,.2f);line(.8f,.2f,.8f,.4f);line(.8f,.6f,.8f,.8f);line(.8f,.8f,.6f,.8f);line(.4f,.8f,.2f,.8f);line(.2f,.8f,.2f,.6f);break;
    case Icon::Refresh:d->PathArcTo(p(.5f,.5f),size*.33f,-1.2f,4.2f,20);d->PathStroke(color,0,t);line(.6f,.08f,.75f,.18f);line(.75f,.18f,.59f,.28f);break;
    // The sparkle is concave. A convex fill folds its inner corners over each
    // other and produces the asymmetric blob visible in the old AI buttons.
    case Icon::Chat: {ImVec2 points[]={p(.5f,.05f),p(.63f,.37f),p(.95f,.5f),p(.63f,.63f),p(.5f,.95f),p(.37f,.63f),p(.05f,.5f),p(.37f,.37f)};d->AddConcavePolyFilled(points,8,color);break;}
    case Icon::Settings:d->AddCircle(p(.5f,.5f),size*.29f,color,16,t);d->AddCircle(p(.5f,.5f),size*.1f,color,12,t);for(int i=0;i<8;++i){float a=i*.785398f;line(.5f+cosf(a)*.3f,.5f+sinf(a)*.3f,.5f+cosf(a)*.44f,.5f+sinf(a)*.44f);}break;
    case Icon::Trash:d->AddRect(p(.27f,.3f),p(.73f,.9f),color,1,0,t);line(.17f,.23f,.83f,.23f);line(.4f,.1f,.6f,.1f);break;
    case Icon::More:for(float x:{.2f,.5f,.8f})d->AddCircleFilled(p(x,.5f),size*.07f,color,12);break;
    case Icon::Folder:line(.1f,.24f,.4f,.24f);line(.4f,.24f,.5f,.36f);line(.5f,.36f,.9f,.36f);line(.9f,.36f,.9f,.83f);line(.9f,.83f,.1f,.83f);line(.1f,.83f,.1f,.24f);break;
    }
}
inline bool Button(const char* id, const char* text, Icon icon, float width=0, bool selected=false, bool iconOnly=false) {
    const float scale=Scale(),height=40*scale;
    if(!width)width=iconOnly ? height : ImGui::CalcTextSize(text).x+54*scale;
    auto at=ImGui::GetCursorScreenPos();
    ImGui::PushID(id);const bool pressed=ImGui::InvisibleButton("button",ImVec2(width,height));
    const bool hover=ImGui::IsItemHovered(),active=ImGui::IsItemActive();
    auto* draw=ImGui::GetWindowDrawList();
    draw->AddRectFilled(at,ImVec2(at.x+width,at.y+height),Color(selected ? ImGuiCol_Header : active ? ImGuiCol_ButtonActive : hover ? ImGuiCol_ButtonHovered : ImGuiCol_Button),Px(9));
    draw->AddRect(at,ImVec2(at.x+width,at.y+height),Color(selected ? ImGuiCol_CheckMark : ImGuiCol_Border),Px(9));
    const float iconSize=16*scale, x=iconOnly ? (width-iconSize)*.5f : 14*scale;
    DrawIcon(icon,ImVec2(at.x+x,at.y+(height-iconSize)*.5f),iconSize,Color(selected ? ImGuiCol_CheckMark : ImGuiCol_Text));
    draw->PushClipRect(at,ImVec2(at.x+width,at.y+height),true);
    if(!iconOnly)draw->AddText(ImVec2(at.x+38*scale,at.y+(height-ImGui::GetFontSize())*.5f),Color(ImGuiCol_Text),text);
    draw->PopClipRect();
    if(hover && iconOnly)ImGui::SetTooltip("%s",text);
    ImGui::PopID();return pressed;
}
inline bool Slider(const char* id,float* value,float low,float high,const char* format) {
    const float scale=Scale(),width=std::max(Px(40),ImGui::GetContentRegionAvail().x-Px(100));
    const auto at=ImGui::GetCursorScreenPos();
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(0,Px(8)));
    for(auto color:{ImGuiCol_FrameBg,ImGuiCol_FrameBgHovered,ImGuiCol_FrameBgActive,ImGuiCol_SliderGrab,ImGuiCol_SliderGrabActive})ImGui::PushStyleColor(color,ImVec4(0,0,0,0));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);ImGui::SetNextItemWidth(width);
    const bool changed=ImGui::SliderFloat(id,value,low,high,"");
    ImGui::PopStyleVar(2);ImGui::PopStyleColor(5);
    auto* d=ImGui::GetWindowDrawList();const float y=at.y+ImGui::GetItemRectSize().y*.5f;
    const float x=at.x+Px(9)+std::clamp((*value-low)/(high-low),0.f,1.f)*(width-Px(18));
    d->AddLine(ImVec2(at.x+Px(9),y),ImVec2(at.x+width-Px(9),y),Color(ImGuiCol_Border),Px(4));
    d->AddLine(ImVec2(at.x+Px(9),y),ImVec2(x,y),Color(ImGuiCol_CheckMark),Px(4));
    d->AddCircleFilled(ImVec2(x,y),9*scale,Color(ImGuiCol_CheckMark),24);
    char number[64];std::snprintf(number,sizeof(number),format,*value);
    ImGui::SameLine();ImGui::TextColored(ImGui::GetStyleColorVec4(ImGuiCol_CheckMark),"%s",number);return changed;
}
inline bool Toggle(const char* label, bool* value) {
    const float h=28*Scale(),w=52*Scale();auto at=ImGui::GetCursorScreenPos();
    bool clicked=ImGui::InvisibleButton(label,ImVec2(w,h));if(clicked)*value=!*value;
    auto* draw=ImGui::GetWindowDrawList();
    draw->AddRectFilled(at,ImVec2(at.x+w,at.y+h),Color(*value ? ImGuiCol_Header : ImGuiCol_FrameBg),h*.5f);
    draw->AddCircleFilled(ImVec2(at.x+(*value ? w-h*.5f : h*.5f),at.y+h*.5f),h*.36f,Color(*value ? ImGuiCol_CheckMark : ImGuiCol_TextDisabled),24);
    return clicked;
}
inline void Divider() {ImGui::Spacing();ImGui::Separator();ImGui::Spacing();}
inline void Status(const std::string& mode,const std::string& status) {
    std::string text=mode+(status.empty()?"":" · "+status);
    // Forum titles can contain line breaks. A one-line status must not paint
    // those extra lines over the search bar or outside its allocated bounds.
    for(char& ch:text)if(ch=='\r' || ch=='\n' || ch=='\t')ch=' ';
    const float width=ImGui::GetContentRegionAvail().x, size=ImGui::GetFontSize()*.9f;
    auto* font=ImGui::GetFont();const char* end=nullptr;
    font->CalcTextSizeA(size,std::max(1.f,width-20),0,text.c_str(),nullptr,&end);
    const bool shortened=end && *end;
    if(shortened)text=std::string(text.c_str(),end)+"…";
    ImGui::GetWindowDrawList()->AddText(font,size,ImGui::GetCursorScreenPos(),Color(ImGuiCol_TextDisabled),text.c_str());
    ImGui::Dummy(ImVec2(width,ImGui::GetFontSize()));
    if(ImGui::IsItemHovered())ImGui::SetTooltip("%s\n%s",mode.c_str(),status.c_str());
}
inline bool Row(const char* id,const std::string& label, bool selected, Icon icon=Icon::Note,float width=0) {
    auto at=ImGui::GetCursorScreenPos();float scale=Scale();
    if(width<=0)width=ImGui::GetContentRegionAvail().x;
    float h=std::max(Px(42),ImGui::CalcTextSize(label.c_str(),nullptr,false,std::max(20.f,width-Px(48))).y+Px(20));
    ImGui::PushID(id);bool pressed=ImGui::InvisibleButton("row",ImVec2(width,h));auto* draw=ImGui::GetWindowDrawList();
    draw->AddRectFilled(at,ImVec2(at.x+width,at.y+h),Color(selected ? ImGuiCol_Header : ImGui::IsItemHovered() ? ImGuiCol_HeaderHovered : ImGuiCol_FrameBg),Px(8));
    if(selected)draw->AddRect(at,ImVec2(at.x+width,at.y+h),Color(ImGuiCol_CheckMark),Px(8));
    DrawIcon(icon,ImVec2(at.x+12*scale,at.y+13*scale),14*scale,Color(selected ? ImGuiCol_CheckMark : ImGuiCol_TextDisabled));
    draw->AddText(nullptr,0,ImVec2(at.x+36*scale,at.y+10*scale),Color(ImGuiCol_Text),label.c_str(),nullptr,std::max(20.f,width-46*scale));
    if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",label.c_str());ImGui::PopID();return pressed;
}
inline bool Dropdown(const char* id,ImVec2 anchor) {
    const auto screen=ImGui::GetIO().DisplaySize;
    // Anchor to the invoking button, including keyboard/test activation. Do not
    // depend on a stale mouse position elsewhere on screen.
    anchor.x=std::clamp(anchor.x,Px(4),std::max(Px(4),screen.x-Px(320)));
    anchor.y=std::clamp(anchor.y,Px(4),std::max(Px(4),screen.y-Px(250)));
    ImGui::SetNextWindowPos(anchor,ImGuiCond_Appearing);
    ImGui::SetNextWindowSizeConstraints(ImVec2(0,0),ImVec2(std::max(1.f,screen.x-Px(8)),std::max(1.f,screen.y-Px(8))));
    ImGui::PushStyleVar(ImGuiStyleVar_PopupRounding,Px(8));
    bool open=ImGui::BeginPopup(id);ImGui::PopStyleVar();return open;
}
}
