#pragma once
#include "rpc.h"
#include "desktop_ui.h"
#include <map>
#include <regex>
#include <sstream>

namespace chat_ui {
struct Run {std::string text,label;bool bold=false,code=false;};
struct Block {std::string text;bool heading=false,code=false;std::vector<std::vector<std::string>> rows;};
inline std::string Trim(std::string s){auto a=s.find_first_not_of(" \t\r\n");auto b=s.find_last_not_of(" \t\r\n");return a==std::string::npos?"":s.substr(a,b-a+1);}
inline std::vector<std::string> Cells(std::string line){
    line=Trim(line);if(!line.empty() && line.front()=='|')line.erase(0,1);if(!line.empty() && line.back()=='|')line.pop_back();
    std::vector<std::string> out;std::string part;bool escaped=false;
    for(char c:line){if(c=='|' && !escaped){out.push_back(Trim(part));part.clear();}else{if(c!='\\' || escaped)part+=c;}escaped=c=='\\' && !escaped;}
    out.push_back(Trim(part));return out;
}
inline bool Separator(const std::string& line){
    const auto cells=Cells(line);if(cells.size()<2)return false;
    for(const auto& cell:cells)if(cell.find('-')==std::string::npos || cell.find_first_not_of(":- ")!=std::string::npos)return false;
    return true;
}
inline std::vector<Block> Parse(const std::string& text){
    std::vector<std::string> lines;std::istringstream in(text);std::string line;while(std::getline(in,line))lines.push_back(line);
    std::vector<Block> out;bool code=false;
    for(size_t i=0;i<lines.size();++i){
        line=Trim(lines[i]);if(line.rfind("```",0)==0){code=!code;continue;}if(line.empty()){continue;}
        if(!code && i+1<lines.size() && line.find('|')!=std::string::npos && Separator(lines[i+1])){
            Block b;b.rows.push_back(Cells(line));++i;
            while(i+1<lines.size() && lines[i+1].find('|')!=std::string::npos)b.rows.push_back(Cells(lines[++i]));
            out.push_back(std::move(b));continue;
        }
        Block b;b.code=code;
        if(!code && line[0]=='#'){size_t n=line.find_first_not_of('#');if(n!=std::string::npos && line[n]==' '){b.heading=true;line=Trim(line.substr(n));}}
        b.text=line;out.push_back(std::move(b));
    }
    return out;
}
inline std::vector<Run> Inline(const std::string& text,bool bold=false,bool code=false){
    std::vector<Run> out;std::string part;
    auto flush=[&]{if(!part.empty()){out.push_back({part,"",bold,code});part.clear();}};
    for(size_t i=0;i<text.size();){
        if(text[i]=='\\' && i+1<text.size()){part+=text[i+1];i+=2;continue;}
        if(!code && i+1<text.size() && ((text[i]=='*' && text[i+1]=='*') || (text[i]=='_' && text[i+1]=='_'))){flush();bold=!bold;i+=2;continue;}
        if(text[i]=='`'){flush();code=!code;++i;continue;}
        // Italic delimiters are removed; the shared Segoe font remains readable.
        if(!code && (text[i]=='*' || text[i]=='_') && !(i==0 && i+1<text.size() && text[i+1]==' ')){++i;continue;}
        if(!code && text[i]=='['){
            auto end=text.find(']',i+1);
            if(end!=std::string::npos){
                auto caption=text.substr(i+1,end-i-1);auto label=caption;size_t next=end+1;
                if(next<text.size() && text[next]=='('){auto stop=text.find(')',next+1);if(stop!=std::string::npos){auto target=text.substr(next+1,stop-next-1);if(target.size()>1 && target[0]=='S')label=target;next=stop+1;}}
                if(label.size()>1 && label[0]=='S' && label.find_first_not_of("0123456789",1)==std::string::npos){flush();out.push_back({"["+caption+"]",label,bold,false});i=next;continue;}
                if(next>end+1){part+=caption;i=next;continue;} // External URLs never open a browser from chat.
            }
        }
        part+=text[i++];
    }
    flush();return out;
}
inline const Json* Source(const Json& sources,const std::string& label){
    for(const auto& s:sources)if(s.value("label","")==label && s.contains("fingerprint"))return &s;return nullptr;
}
inline float Line(const std::string& text,float width,bool draw,const Json& sources,Json& clicked,bool bold=false,bool code=false){
    const auto start=ImGui::GetCursorScreenPos();float x=0,y=0;const float height=ImGui::GetFontSize()+ui::Px(6);
    auto* d=ImGui::GetWindowDrawList();int part=0;
    for(const auto& run:Inline(text,bold,code)){
        auto* font=run.bold && ui::Bold()?ui::Bold():ImGui::GetFont();
        const Json* source=Source(sources,run.label);
        size_t at=0;
        while(at<run.text.size()){
            auto end=run.text.find_first_of(" \t",at);if(end==std::string::npos)end=run.text.size();if(end==at)++end;
            const char* a=run.text.c_str()+at;const char* stop=run.text.c_str()+end;
            float w=font->CalcTextSizeA(ImGui::GetFontSize(),FLT_MAX,0,a,stop).x;
            if(x>0 && x+w>width){x=0;y+=height;}
            if(w>width){
                stop=font->CalcWordWrapPositionA(ImGui::GetFontSize()/font->FontSize,a,stop,std::max(ui::Px(20),width-x));
                if(stop==a){++stop;while(*stop && (static_cast<unsigned char>(*stop)&0xc0)==0x80)++stop;}
                end=static_cast<size_t>(stop-run.text.c_str());w=font->CalcTextSizeA(ImGui::GetFontSize(),FLT_MAX,0,a,stop).x;
            }
            if(draw){
                const ImVec2 pos(start.x+x,start.y+y);
                if(run.code)d->AddRectFilled(ImVec2(pos.x-ui::Px(2),pos.y),ImVec2(pos.x+w+ui::Px(2),pos.y+height),ui::Color(ImGuiCol_FrameBg),ui::Px(3));
                d->AddText(font,ImGui::GetFontSize(),pos,ui::Color(source?ImGuiCol_CheckMark:ImGuiCol_Text),a,stop);
                if(source){
                    ImGui::SetCursorScreenPos(pos);ImGui::PushID(part++);
                    if(ImGui::InvisibleButton("citation",ImVec2(std::max(1.f,w),height)))clicked=*source;
                    if(ImGui::IsItemHovered())ImGui::SetTooltip("%s\n%s",source->value("title","").c_str(),source->value("heading","").c_str());
                    ImGui::PopID();
                }
            }
            x+=w;at=end;
        }
    }
    ImGui::SetCursorScreenPos(start);return y+height;
}
inline float Markdown(const std::vector<Block>& blocks,float width,bool draw,const Json& sources,Json& clicked){
    const auto start=ImGui::GetCursorScreenPos();float y=0;int blockId=0;
    for(const auto& b:blocks){
        ImGui::SetCursorScreenPos(ImVec2(start.x,start.y+y));ImGui::PushID(blockId++);
        if(b.rows.empty())y+=Line(b.text,width,draw,sources,clicked,b.heading,b.code)+ui::Px(6);
        else {
            size_t columns=0;for(const auto& row:b.rows)columns=std::max(columns,row.size());
            const float colWidth=std::max(ui::Px(120),(width-ui::Px(12))/std::max(size_t(1),columns));
            std::vector<float> heights;float total=0;
            for(size_t r=0;r<b.rows.size();++r){float h=ui::Px(32);for(const auto& cell:b.rows[r])h=std::max(h,Line(cell,colWidth-ui::Px(16),false,sources,clicked,r==0)+ui::Px(16));heights.push_back(h);total+=h;}
            const float boxHeight=std::min(total+ui::Px(12)+(colWidth*columns>width-ui::Px(12)+1?ImGui::GetStyle().ScrollbarSize:0),ui::Px(360));
            if(draw){
                ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(ui::Px(4),ui::Px(4)));
                ImGui::BeginChild("table",ImVec2(width,boxHeight),ImGuiChildFlags_Borders,ImGuiWindowFlags_HorizontalScrollbar);
                const auto origin=ImGui::GetCursorScreenPos();auto* d=ImGui::GetWindowDrawList();float dy=0;
                for(size_t r=0;r<b.rows.size();++r){
                    for(size_t c=0;c<columns;++c){
                        const ImVec2 a(origin.x+c*colWidth,origin.y+dy),z(a.x+colWidth,a.y+heights[r]);
                        d->AddRectFilled(a,z,ui::Color(r?ImGuiCol_TableRowBg:ImGuiCol_TableHeaderBg));d->AddRect(a,z,ui::Color(ImGuiCol_Border));
                        if(c<b.rows[r].size()){ImGui::SetCursorScreenPos(ImVec2(a.x+ui::Px(8),a.y+ui::Px(8)));ImGui::PushID(static_cast<int>(r*columns+c));Line(b.rows[r][c],colWidth-ui::Px(16),true,sources,clicked,r==0);ImGui::PopID();}
                    }dy+=heights[r];
                }
                ImGui::SetCursorScreenPos(origin);ImGui::Dummy(ImVec2(columns*colWidth,total));ImGui::EndChild();ImGui::PopStyleVar();
            }
            y+=boxHeight+ui::Px(8);
        }
        ImGui::PopID();
    }
    ImGui::SetCursorScreenPos(start);return std::max(ImGui::GetFontSize(),y);
}

struct Actions {bool send=false,stop=false,clear=false;Json citation=Json::object();};
class Panel {
    struct Cached {std::string text;std::vector<Block> blocks;float width=0,font=0,height=0;};
    std::map<std::string,Cached> cache;
    bool follow=true,forceBottom=true,focus=false;
    std::string previousTail;
public:
    bool onlyCurrent=false;
    ImVec2 sendCenter{};
    float composerBottom=0;
    void Focus(){focus=true;forceBottom=true;}
    Actions Draw(const Json& messages,char* question,size_t capacity,bool busy,bool stopping,bool ready,const std::string& error){
        Actions action;const float scale=ui::Scale(),width=ImGui::GetContentRegionAvail().x;
        const float top=ImGui::GetCursorPosY();ui::Text("ИИ-помощник",1.18f,true,ImGuiCol_Text,std::max(ui::Px(40),width-ui::Px(48)));
        ImGui::SameLine();ImGui::SetCursorPos(ImVec2(ImGui::GetWindowWidth()-ImGui::GetStyle().WindowPadding.x-ui::Px(32),top));
        if(ui::Button("clear-chat","Очистить диалог",ui::Icon::Trash,ui::Px(32),false,true))ImGui::OpenPopup("clear-chat-confirm");
        if(ImGui::BeginPopup("clear-chat-confirm")){
            ui::Text("Очистить локальную историю?",1.f,true);ui::Text("Сообщения будут удалены из этого чата.",.85f);
            if(ImGui::MenuItem("Очистить диалог"))action.clear=true;ImGui::EndPopup();
        }
        ImGui::SetCursorPosY(top+ui::Px(44));
        ui::Toggle("##current-law",&onlyCurrent);ImGui::SameLine();ui::Text("Только текущий закон",.83f);
        const float inputWidth=std::max(ui::Px(60),width-ui::Px(72));
        const float lines=ImGui::CalcTextSize(question,nullptr,false,inputWidth).y;
        const float composerHeight=std::clamp(lines+ui::Px(24),ui::Px(48),ui::Px(120));
        const float reserve=composerHeight+ui::Px(error.empty()?30:66);
        ImGui::BeginChild("messages",ImVec2(0,-reserve),ImGuiChildFlags_None);
        follow=ImGui::GetScrollY()>=ImGui::GetScrollMaxY()-ui::Px(20);
        if(messages.empty()){
            cache.clear();
            ui::Text(ready?"Задайте вопрос по законам":error.empty()?"Загружаем локальную историю…":"Чат недоступен",1.f,true);
            ui::Text("Например: что такое 51 УК?",.9f,false,ImGuiCol_TextDisabled);
        }
        std::string tail;
        for(const auto& message:messages){
            const auto id=message.value("id",""),text=message.value("content","");const bool user=message.value("role","")=="user";
            auto& item=cache[id];if(item.text!=text){item.text=text;item.blocks=Parse(text);item.width=0;}
            const auto sources=message.value("sources",Json::array());
            const float available=ImGui::GetContentRegionAvail().x,pad=ui::Px(12),bodyWidth=std::max(ui::Px(40),available-(user?ui::Px(32):0)-pad*2);
            if(item.width!=bodyWidth || item.font!=ImGui::GetFontSize()){
                item.width=bodyWidth;item.font=ImGui::GetFontSize();Json ignored;
                item.height=Markdown(item.blocks,bodyWidth,false,sources,ignored)+ui::Px(user?48:70);
            }
            const auto at=ImGui::GetCursorScreenPos();const float left=user?ui::Px(32):0;
            if(ImGui::IsRectVisible(ImVec2(available,item.height))){
                if(user)ImGui::GetWindowDrawList()->AddRectFilled(ImVec2(at.x+left,at.y),ImVec2(at.x+available,at.y+item.height-ui::Px(12)),ui::Color(ImGuiCol_FrameBg),ui::Px(10));
                ImGui::SetCursorScreenPos(ImVec2(at.x+left+pad,at.y+ui::Px(8)));ImGui::PushID(id.c_str());
                ui::Text(user?"Вы":"ИИ-помощник",.8f,true,ImGuiCol_TextDisabled);
                ImGui::SetCursorScreenPos(ImVec2(at.x+left+pad,at.y+ui::Px(8)+ImGui::GetFontSize()*.8f+ImGui::GetStyle().ItemSpacing.y));
                Markdown(item.blocks,bodyWidth,true,sources,action.citation);
                ImGui::SetCursorScreenPos(ImVec2(at.x+left+pad,at.y+item.height-ui::Px(34)));
                const auto status=message.value("status","");
                if(!user && status!="complete")ui::Text(status=="streaming"?(stopping?"Останавливаем…":"Пишет…"):status=="cancelled"?"Ответ остановлен":status=="error"?"Не удалось получить ответ":"",.78f,false,ImGuiCol_TextDisabled);
                ImGui::PopID();
                ImGui::PushID(id.c_str());
                if(ImGui::IsWindowHovered(ImGuiHoveredFlags_ChildWindows) && ImGui::IsMouseHoveringRect(at,ImVec2(at.x+available,at.y+item.height)) && ImGui::IsMouseClicked(ImGuiMouseButton_Right))ImGui::OpenPopup("message-menu");
                if(ImGui::BeginPopup("message-menu")){
                    if(ImGui::MenuItem("Скопировать сообщение"))ImGui::SetClipboardText(text.c_str());
                    ImGui::EndPopup();
                }
                ImGui::PopID();
            }
            ImGui::SetCursorScreenPos(at);ImGui::Dummy(ImVec2(available,item.height));
            tail=id+std::to_string(text.size())+message.value("status","");
        }
        if(forceBottom || (follow && tail!=previousTail))ImGui::SetScrollHereY(1);
        forceBottom=false;previousTail=tail;ImGui::EndChild();
        if(!error.empty())ui::Status("",error);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding,ImVec2(ui::Px(8),ui::Px(6)));
        ImGui::BeginChild("composer",ImVec2(0,composerHeight),ImGuiChildFlags_Borders,ImGuiWindowFlags_NoScrollbar|ImGuiWindowFlags_NoScrollWithMouse);
        const float field=std::max(ui::Px(40),ImGui::GetContentRegionAvail().x-ui::Px(48));
        ImGui::PushStyleVar(ImGuiStyleVar_FrameBorderSize,0);ImGui::PushStyleVar(ImGuiStyleVar_FramePadding,ImVec2(ui::Px(4),ui::Px(4)));
        ImGui::PushStyleColor(ImGuiCol_FrameBg,ImVec4(0,0,0,0));ImGui::BeginDisabled(!ready);
        if(focus && ready){ImGui::SetKeyboardFocusHere();focus=false;}
        const auto inputAt=ImGui::GetCursorScreenPos();
        if(ImGui::InputTextMultiline("##question",question,capacity,ImVec2(field,composerHeight-ui::Px(12)),ImGuiInputTextFlags_EnterReturnsTrue) && !busy)action.send=true;
        if(!question[0]){auto* draw=ImGui::GetWindowDrawList();draw->PushClipRect(inputAt,ImVec2(inputAt.x+field,inputAt.y+composerHeight),true);draw->AddText(ImVec2(inputAt.x+ui::Px(4),inputAt.y+ui::Px(4)),ui::Color(ImGuiCol_TextDisabled),"Спросить о законе…");draw->PopClipRect();}
        ImGui::EndDisabled();ImGui::PopStyleColor();ImGui::PopStyleVar(2);
        ImGui::SameLine();ImGui::SetCursorPosY((composerHeight-ui::Px(34))*.5f);
        const auto pos=ImGui::GetCursorScreenPos();const float diameter=ui::Px(34);
        sendCenter=ImVec2(pos.x+diameter*.5f,pos.y+diameter*.5f);
        ImGui::BeginDisabled(!ready || stopping || (!busy && Trim(question).empty()));
        bool pressed=ImGui::InvisibleButton("send-stop",ImVec2(diameter,diameter));
        auto* d=ImGui::GetWindowDrawList();d->AddCircleFilled(ImVec2(pos.x+diameter*.5f,pos.y+diameter*.5f),diameter*.5f,ui::Color(ImGui::IsItemHovered()?ImGuiCol_ButtonActive:ImGuiCol_Header),32);
        const auto color=ui::Color(ImGuiCol_CheckMark);
        if(busy)d->AddRectFilled(ImVec2(pos.x+diameter*.34f,pos.y+diameter*.34f),ImVec2(pos.x+diameter*.66f,pos.y+diameter*.66f),color,ui::Px(2));
        else {const float x=pos.x+diameter*.5f,y=pos.y+diameter*.32f;d->AddLine(ImVec2(x,pos.y+diameter*.72f),ImVec2(x,y),color,ui::Px(2));d->AddLine(ImVec2(x,y),ImVec2(pos.x+diameter*.3f,pos.y+diameter*.52f),color,ui::Px(2));d->AddLine(ImVec2(x,y),ImVec2(pos.x+diameter*.7f,pos.y+diameter*.52f),color,ui::Px(2));}
        if(pressed){if(busy)action.stop=true;else action.send=true;}
        if(ImGui::IsItemHovered())ImGui::SetTooltip("%s",busy?"Остановить ответ":"Отправить · Ctrl+Enter");
        ImGui::EndDisabled();ImGui::EndChild();ImGui::PopStyleVar();
        composerBottom=ImGui::GetItemRectMax().y;
        ui::Text("Ctrl+Enter — отправить · Enter — новая строка",.72f,false,ImGuiCol_TextDisabled);
        return action;
    }
};
}
