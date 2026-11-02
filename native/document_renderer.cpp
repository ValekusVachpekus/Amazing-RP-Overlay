#include "rpc.h"
#include "text_search.h"
#include "desktop_ui.h"
#include <imgui.h>
#include <algorithm>
#include <functional>
#include <string>
#include <sstream>
#include <vector>
#include <unordered_map>

namespace {
DocumentSearch* currentSearch = nullptr;
std::unordered_map<std::string, std::vector<std::pair<size_t, size_t>>> matchCache;
const std::vector<std::pair<size_t, size_t>>& CachedMatches(const std::string& text) {
    const auto found = matchCache.find(text);
    if (found != matchCache.end()) return found->second;
    return matchCache.emplace(text, reader_search::Matches(text, currentSearch ? currentSearch->query : "")).first->second;
}

// Draw each wrapped line once, with match rectangles underneath the glyphs.
// Byte offsets remain those of the original UTF-8 text, including Cyrillic.
float RichText(const std::string& text, ImVec2 origin, float width, ImU32 color) {
    const auto& matches = CachedMatches(text);
    const int first = currentSearch ? currentSearch->cursor : 0;
    if (currentSearch) currentSearch->cursor += static_cast<int>(matches.size());
    auto* font = ImGui::GetFont();
    auto* draw = ImGui::GetWindowDrawList();
    const float size = ImGui::GetFontSize(), height = ImGui::GetTextLineHeight() + ui::Px(5);
    const char* begin = text.c_str();
    const char* end = begin + text.size();
    const char* line = begin;
    float y = origin.y;
    while (line < end) {
        const char* newline = std::find(line, end, '\n');
        const char* stop = font->CalcWordWrapPositionA(size / font->FontSize, line, newline, std::max(20.0f, width));
        if (stop == line && stop < newline) { ++stop; while (stop < newline && (static_cast<unsigned char>(*stop) & 0xc0) == 0x80) ++stop; }
        for (size_t i = 0; i < matches.size(); ++i) {
            const auto from = std::max(matches[i].first, static_cast<size_t>(line - begin));
            const auto to = std::min(matches[i].second, static_cast<size_t>(stop - begin));
            if (from >= to) continue;
            float x1 = ImGui::CalcTextSize(line, begin + from).x;
            float x2 = ImGui::CalcTextSize(line, begin + to).x;
            bool active = currentSearch && first + static_cast<int>(i) == currentSearch->active;
            draw->AddRectFilled(ImVec2(origin.x + x1, y), ImVec2(origin.x + x2, y + size + 2),
                ImGui::GetColorU32(active ? ImGuiCol_PlotHistogram : ImGuiCol_PlotHistogramHovered), 2);
            if (active && currentSearch->jump && from == matches[i].first) ImGui::SetScrollFromPosY(y - ImGui::GetWindowPos().y, .35f);
        }
        draw->AddText(ImVec2(origin.x, y), color, line, stop);
        y += height;
        line = stop;
        if (line == newline && line < end) ++line;
        else while (line < newline && (*line == ' ' || *line == '\t')) ++line;
    }
    return std::max(height, y - origin.y);
}

const Json& Children(const Json& node) {
    static const Json empty = Json::array();
    const auto at = node.find("children");
    return at != node.end() && at->is_array() ? *at : empty;
}

std::string CompactDisplayText(std::string value) {
    // Forum authors often separate clauses with four or more <br> elements.
    // Keep content lines and indentation; the renderer supplies paragraph spacing.
    auto replace = [&](const std::string& from, const std::string& to) {
        size_t at = 0;
        while ((at = value.find(from, at)) != std::string::npos) {
            value.replace(at, from.size(), to);
            at += to.size();
        }
    };
    replace("\r\n", "\n");
    replace("\r", "\n");
    replace("\xC2\xA0", " ");   // non-breaking space
    replace("\xE2\xA0\x80", " "); // braille blank used as indentation
    replace("\xE2\x80\x8B", "");  // zero-width space
    std::istringstream lines(value);
    std::string line, result;
    while (std::getline(lines, line)) {
        const auto end = line.find_last_not_of(" \t");
        if (end == std::string::npos) continue;
        if (!result.empty()) result += '\n';
        result.append(line, 0, end + 1);
    }
    return result;
}

std::string Text(const Json& node) {
    const auto tag = node.value("tag", "");
    if (tag == "text") return node.value("text", "");
    if (tag == "br") return "\n";
    if (tag == "img") return "[" + node.value("alt", "Изображение") + "]";
    std::string result;
    for (const auto& child : Children(node)) result += Text(child);
    if (tag == "p" || tag == "div" || tag == "li") result += "\n";
    return result;
}

void Table(const Json& table) {
    std::vector<const Json*> rows;
    std::function<void(const Json&)> collect = [&](const Json& node) {
        const auto tag = node.value("tag", "");
        if (tag == "tr") rows.push_back(&node);
        else if (tag != "table" || &node == &table)
            for (const auto& child : Children(node)) collect(child);
    };
    collect(table);
    if (rows.empty()) return;
    struct Cell { int row, col, rowspan, colspan; bool heading; std::string text; };
    std::vector<Cell> cells;
    // Bounded by parser limits, with a defensive renderer cap.
    std::vector<std::vector<bool>> occupied(rows.size() + 100, std::vector<bool>(200, false));
    int columns = 0;
    for (int r = 0; r < static_cast<int>(rows.size()); ++r) {
        int c = 0;
        for (const auto& cell : Children(*rows[r])) {
            auto tag = cell.value("tag", "");
            if (tag != "td" && tag != "th") continue;
            while (c < 200 && occupied[r][c]) ++c;
            if (c >= 200) break;
            int rs = std::clamp(cell.value("rowspan", 1), 1, 100);
            int cs = std::clamp(cell.value("colspan", 1), 1, 200 - c);
            rs = std::min(rs, static_cast<int>(rows.size()) - r);
            cells.push_back({r, c, rs, cs, tag == "th", CompactDisplayText(Text(cell))});
            for (int dr = 0; dr < rs; ++dr) for (int dc = 0; dc < cs; ++dc) occupied[r + dr][c + dc] = true;
            c += cs;
            columns = std::max(columns, c);
        }
    }
    if (!columns) return;
    const float width = std::max(170.0f, std::min(260.0f, (ImGui::GetContentRegionAvail().x - ImGui::GetStyle().WindowPadding.x * 2 - 4) / columns));
    std::vector<float> heights(rows.size(), ImGui::GetTextLineHeight() + 16);
    for (const auto& cell : cells) {
        float textHeight = ImGui::CalcTextSize(cell.text.c_str(), nullptr, false, width * cell.colspan - 16).y;
        float need = textHeight * (ImGui::GetTextLineHeight() + 3) / ImGui::GetTextLineHeight() + 16;
        for (int r = cell.row; r < cell.row + cell.rowspan; ++r) heights[r] = std::max(heights[r], need / cell.rowspan);
    }
    std::vector<float> offsets{0};
    for (float height : heights) offsets.push_back(offsets.back() + height);
    float childHeight = std::min(500.0f, offsets.back() + ImGui::GetStyle().WindowPadding.y * 2 + ImGui::GetStyle().ScrollbarSize + 4);
    if (currentSearch && currentSearch->jump) {
        int count = 0;
        for (const auto& cell : cells) count += static_cast<int>(CachedMatches(cell.text).size());
        if (currentSearch->active >= currentSearch->cursor && currentSearch->active < currentSearch->cursor + count)
            ImGui::SetScrollFromPosY(ImGui::GetCursorPosY(), .25f);
    }
    ImGui::PushID(&table);
    ImGui::BeginChild("table", ImVec2(0, childHeight), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    auto* draw = ImGui::GetWindowDrawList();
    for (const auto& cell : cells) {
        ImVec2 from(origin.x + cell.col * width, origin.y + offsets[cell.row]);
        ImVec2 to(from.x + cell.colspan * width, origin.y + offsets[cell.row + cell.rowspan]);
        draw->AddRectFilled(from, to, ImGui::GetColorU32(cell.heading ? ImGuiCol_TableHeaderBg : ImGuiCol_TableRowBg));
        draw->AddRect(from, to, ImGui::GetColorU32(ImGuiCol_Border));
        draw->PushClipRect(from, to, true);
        RichText(cell.text, ImVec2(from.x + 8, from.y + 8), width * cell.colspan - 16, ImGui::GetColorU32(ImGuiCol_Text));
        draw->PopClipRect();
    }
    ImGui::Dummy(ImVec2(width * columns, offsets.back()));
    ImGui::EndChild();
    ImGui::PopID();
}

void Node(const Json& node) {
    auto tag = node.value("tag", "");
    if (tag == "table") { Table(node); return; }
    bool blockChildren = false;
    for (const auto& child : Children(node)) {
        auto childTag = child.value("tag", "");
        if (childTag == "table" || childTag == "div" || childTag == "p" || childTag == "ul" || childTag == "ol" || childTag == "h3") blockChildren = true;
    }
    if (blockChildren) {
        for (const auto& child : Children(node)) Node(child);
        return;
    }
    auto text = CompactDisplayText(Text(node));
    if (text.find_first_not_of(" \r\n\t") == std::string::npos) return;
    bool heading = tag.size() == 2 && tag[0] == 'h';
    const bool chapter=heading && (text.rfind("ГЛАВА",0)==0 || text.rfind("Глава",0)==0 || text.rfind("РАЗДЕЛ",0)==0);
    const bool boldFont=heading && ui::Bold();
    if(boldFont)ImGui::PushFont(ui::Bold());
    if(heading)ImGui::Dummy(ImVec2(0,ui::Px(chapter?12:8)));
    if (tag == "li") ImGui::Bullet();
    const auto origin = ImGui::GetCursorScreenPos();
    const float width = ImGui::GetContentRegionAvail().x;
    const float padding=heading?ui::Px(12):0;
    if(heading){
        const float height=ImGui::CalcTextSize(text.c_str(),nullptr,false,std::max(20.f,width-padding*2)).y*1.4f+padding*2;
        auto* draw=ImGui::GetWindowDrawList();
        if(chapter)draw->AddRectFilled(origin,ImVec2(origin.x+ui::Px(4),origin.y+height),ui::Color(ImGuiCol_CheckMark));
        else draw->AddRectFilled(origin,ImVec2(origin.x+width,origin.y+height),ui::Color(ImGuiCol_FrameBg),ui::Px(8));
    }
    float height = RichText(text, ImVec2(origin.x+padding,origin.y+padding), std::max(20.f,width-padding*2), ImGui::GetColorU32(chapter?ImGuiCol_CheckMark:ImGuiCol_Text));
    ImGui::Dummy(ImVec2(width, height+padding*2));
    if(boldFont)ImGui::PopFont();
}
}

void DrawDocument(const Json& document, DocumentSearch* search) {
    static const Json* previousDocument = nullptr;
    static std::string previousQuery;
    const std::string query = search ? search->query : "";
    if (previousDocument != &document || previousQuery != query) {
        matchCache.clear(); previousDocument = &document; previousQuery = query;
    }
    currentSearch = search;
    if (search) search->cursor = 0;
    if (!document.contains("posts")) return;
    for (const auto& post : document.at("posts")) {
        ImGui::PushID(post.value("post_id", "").c_str());
        for (const auto& node : post.at("blocks")) Node(node);
        ImGui::PopID();
    }
    if (search) { search->total = search->cursor; search->jump = false; }
    currentSearch = nullptr;
}
