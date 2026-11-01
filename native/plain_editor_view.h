#pragma once
#include "plain_editor.h"
#include <imgui.h>
#include <cmath>

namespace notebook {
struct EditorRow { size_t start, end; std::string utf8; };
struct EditorView {
    PlainEditor model;
    std::vector<EditorRow> rows;
    std::vector<float> positions;
    std::vector<std::pair<size_t,size_t>> matches;
    std::string query;
    int active = -1;
    unsigned layoutRevision = ~0u, matchRevision = ~0u;
    float width = 0, fontSize = 0;
    ImFont* cachedFont=nullptr;
    bool focused = false, reveal = false;
    float restoreScroll = -1;
    float Advance(char32_t c) const {
        auto* font=ImGui::GetFont();
        const float ratio=ImGui::GetFontSize()/font->FontSize;
        return (c==U'\t' ? font->GetCharAdvance(' ')*4 : font->GetCharAdvance(static_cast<ImWchar>(c<=65535 ? c : 0xfffd)))*ratio;
    }
    void Layout(float available) {
        if (layoutRevision == model.revision && width == available && fontSize == ImGui::GetFontSize() && cachedFont==ImGui::GetFont()) return;
        cachedFont=ImGui::GetFont();
        width = available; fontSize = ImGui::GetFontSize(); layoutRevision = model.revision;
        rows.clear(); positions.resize(model.text.size()+1);
        size_t start=0;
        float x=0;
        auto append = [&](size_t end) { rows.push_back({start,end,Encode(model.text.substr(start,end-start))}); };
        for (size_t i=0; i < model.text.size(); ++i) {
            char32_t c = model.text[i];
            float advance = Advance(c);
            if (c != U'\n' && x + advance > available && i > start) { append(i); start=i; x=0; }
            positions[i] = x;
            if (c == U'\n') { append(i); start=i+1; x=0; } else x += advance;
        }
        positions[model.text.size()] = x; append(model.text.size());
    }
    size_t RowAt(size_t cursor) const {
        for (size_t i=0; i+1<rows.size(); ++i) if (cursor < rows[i+1].start) return i;
        return rows.empty() ? 0 : rows.size()-1;
    }
    float X(size_t at, size_t row) const {
        if (at >= rows[row].end) {
            if (rows[row].end == rows[row].start) return 0;
            auto end = rows[row].end;
            char32_t c = model.text[end-1];
            return positions[end-1]+Advance(c);
        }
        return positions[at];
    }
    size_t Hit(size_t row, float x) const {
        row = std::min(row,rows.size()-1);
        for (size_t at=rows[row].start; at<rows[row].end; ++at) if (x < (X(at,row)+X(at+1,row))*.5f) return at;
        return rows[row].end;
    }
    void Find(const std::string& value) {
        if (query == value && matchRevision == model.revision) return;
        query=value; matchRevision=model.revision; matches=model.Matches(query);
        active=matches.empty() ? -1 : std::clamp(active,0,static_cast<int>(matches.size())-1);
    }
    void Step(int direction) {
        if (matches.empty()) return;
        active=(active+direction+static_cast<int>(matches.size()))%static_cast<int>(matches.size());
        model.anchor=matches[active].first; model.cursor=matches[active].second; reveal=true;
    }
    bool Draw(bool interactive, bool readonly, float* scroll = nullptr) {
        const auto before=model.revision;
        ImGui::BeginChild("plain-editor", ImVec2(0,0), ImGuiChildFlags_Borders);
        if (restoreScroll >= 0) { ImGui::SetScrollY(restoreScroll); restoreScroll=-1; }
        const float line=ImGui::GetTextLineHeightWithSpacing(), margin=6;
        Layout(std::max(20.f,ImGui::GetWindowWidth()-ImGui::GetStyle().ScrollbarSize-2*margin-2*ImGui::GetStyle().WindowPadding.x));
        auto origin=ImGui::GetCursorScreenPos();
        ImGui::InvisibleButton("text-surface", ImVec2(width,std::max(ImGui::GetContentRegionAvail().y,rows.size()*line+margin*2)));
        bool hover=ImGui::IsItemHovered();
        auto& io=ImGui::GetIO();
        if(ImGui::IsAnyItemActive() && !ImGui::IsItemActive())focused=false;
        if (interactive && ImGui::IsMouseClicked(0)) {
            focused=hover;
            if (hover) {
                size_t row=static_cast<size_t>(std::max(0.f,(io.MousePos.y-origin.y)/line));
                model.Move(Hit(row,io.MousePos.x-origin.x),io.KeyShift);
                if (ImGui::IsMouseDoubleClicked(0)) { model.anchor=model.Word(model.cursor,-1); model.cursor=model.Word(model.anchor,1); }
            }
        }
        if (interactive && focused && ImGui::IsMouseDragging(0) && ImGui::IsItemActive()) {
            model.Move(Hit(static_cast<size_t>(std::max(0.f,(io.MousePos.y-origin.y)/line)),io.MousePos.x-origin.x),true);
            if (io.MousePos.y > ImGui::GetWindowPos().y+ImGui::GetWindowHeight()-20) ImGui::SetScrollY(ImGui::GetScrollY()+line);
            if (io.MousePos.y < ImGui::GetWindowPos().y+20) ImGui::SetScrollY(std::max(0.f,ImGui::GetScrollY()-line));
        }
        if (interactive && focused && !ImGui::IsPopupOpen(nullptr,ImGuiPopupFlags_AnyPopupId)) {
            io.WantTextInput=true;
            const bool ctrl=io.KeyCtrl, shift=io.KeyShift;
            auto key=[](ImGuiKey k) { return ImGui::IsKeyPressed(k); };
            if (ctrl && key(ImGuiKey_A)) { model.anchor=0; model.cursor=model.text.size(); }
            if (ctrl && (key(ImGuiKey_C) || key(ImGuiKey_X))) { auto selected=model.Selected(); if (!selected.empty()) ImGui::SetClipboardText(selected.c_str()); }
            if (!readonly) {
                if (ctrl && key(ImGuiKey_X)) model.Insert(U"");
                if (ctrl && key(ImGuiKey_V)) model.Insert(Decode(ImGui::GetClipboardText()));
                if (ctrl && key(ImGuiKey_Z)) model.Undo(shift);
                if (ctrl && key(ImGuiKey_Y)) model.Undo(true);
                if (key(ImGuiKey_Backspace)) model.Erase(true,ctrl);
                if (key(ImGuiKey_Delete)) model.Erase(false,ctrl);
                if (key(ImGuiKey_Enter)) model.Insert(U"\n");
                if (key(ImGuiKey_Tab)) model.Insert(U"    ");
                if (!ctrl && !io.KeyAlt) {
                    std::u32string typed;
                    for (int index=0; index<io.InputQueueCharacters.Size; ++index) {
                        unsigned c=io.InputQueueCharacters[index];
                        if (c>=32 && c!=127) {
                            if (c>=0xd800 && c<=0xdbff && index+1<io.InputQueueCharacters.Size) {
                                unsigned low=io.InputQueueCharacters[++index]; c=0x10000+((c-0xd800)<<10)+(low-0xdc00);
                            }
                            typed.push_back(static_cast<char32_t>(c));
                        }
                    }
                    if (!typed.empty()) model.Insert(typed);
                }
            }
            // Navigation uses the exact same wrapped rows as drawing and mouse hit tests.
            Layout(width);
            auto [a,b]=model.Selection();
            if (key(ImGuiKey_LeftArrow)) model.Move(!shift && a!=b ? a : ctrl ? model.Word(model.cursor,-1) : model.cursor ? model.cursor-1 : 0,shift);
            if (key(ImGuiKey_RightArrow)) model.Move(!shift && a!=b ? b : ctrl ? model.Word(model.cursor,1) : model.cursor+1,shift);
            auto row=RowAt(model.cursor); auto x=X(model.cursor,row);
            if (key(ImGuiKey_Home)) model.Move(ctrl ? 0 : rows[row].start,shift);
            if (key(ImGuiKey_End)) model.Move(ctrl ? model.text.size() : rows[row].end,shift);
            if (key(ImGuiKey_UpArrow)) model.Move(Hit(row ? row-1 : 0,x),shift);
            if (key(ImGuiKey_DownArrow)) model.Move(Hit(std::min(row+1,rows.size()-1),x),shift);
            const size_t page=std::max(size_t(1),static_cast<size_t>(ImGui::GetWindowHeight()/line)-1);
            if (key(ImGuiKey_PageUp)) model.Move(Hit(row>page ? row-page : 0,x),shift);
            if (key(ImGuiKey_PageDown)) model.Move(Hit(std::min(row+page,rows.size()-1),x),shift);
            if (before!=model.revision || key(ImGuiKey_LeftArrow) || key(ImGuiKey_RightArrow) || key(ImGuiKey_UpArrow) || key(ImGuiKey_DownArrow) || key(ImGuiKey_Home) || key(ImGuiKey_End) || key(ImGuiKey_PageUp) || key(ImGuiKey_PageDown)) reveal=true;
        }
        Layout(width); Find(query);
        if (reveal) {
            float y=RowAt(model.cursor)*line;
            if (y<ImGui::GetScrollY()) ImGui::SetScrollY(y);
            else if (y>ImGui::GetScrollY()+ImGui::GetWindowHeight()-2*line) ImGui::SetScrollY(y-ImGui::GetWindowHeight()+2*line);
            reveal=false;
        }
        auto* draw=ImGui::GetWindowDrawList();
        auto [selectionStart,selectionEnd]=model.Selection();
        size_t first=static_cast<size_t>(std::max(0.f,ImGui::GetScrollY()/line-1));
        size_t last=std::min(rows.size(), first+static_cast<size_t>(ImGui::GetWindowHeight()/line)+4);
        const auto cursorRow=RowAt(model.cursor);
        for (size_t row=first; row<last; ++row) {
            float y=origin.y+row*line;
            auto mark=[&](size_t a,size_t b,ImU32 color) {
                a=std::max(a,rows[row].start); b=std::min(b,rows[row].end);
                if (b>a) draw->AddRectFilled(ImVec2(origin.x+X(a,row),y),ImVec2(origin.x+X(b,row),y+line),color);
            };
            // Search ranges are sorted; don't scan a large note's entire result set each frame.
            auto it=std::lower_bound(matches.begin(),matches.end(),rows[row].start,[](auto range,size_t value){return range.second<=value;});
            for (;it!=matches.end() && it->first<rows[row].end;++it) {
                const bool current=static_cast<int>(it-matches.begin())==active;
                mark(it->first,it->second,ImGui::GetColorU32(current ? ImGuiCol_PlotHistogram : ImGuiCol_PlotHistogramHovered));
            }
            mark(selectionStart,selectionEnd,ImGui::GetColorU32(ImGuiCol_TextSelectedBg));
            // Tabs are expanded for display using the same advances as hit tests.
            auto text=rows[row].utf8; size_t at=0;
            while ((at=text.find('\t',at))!=std::string::npos) { text.replace(at,1,"    "); at+=4; }
            draw->AddText(ImGui::GetFont(),ImGui::GetFontSize(),ImVec2(origin.x,y),ImGui::GetColorU32(ImGuiCol_Text),text.c_str());
            if (interactive && focused && cursorRow==row && std::fmod(ImGui::GetTime(),1.0)<.6)
                draw->AddLine(ImVec2(origin.x+X(model.cursor,row),y),ImVec2(origin.x+X(model.cursor,row),y+line),ImGui::GetColorU32(ImGuiCol_Text));
        }
        if (scroll) *scroll=ImGui::GetScrollY();
        ImGui::EndChild();
        return before!=model.revision;
    }
};
}
