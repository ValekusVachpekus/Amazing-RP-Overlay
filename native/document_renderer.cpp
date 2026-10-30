#include "rpc.h"
#include <imgui.h>
#include <algorithm>
#include <functional>
#include <string>
#include <vector>

namespace {
const Json& Children(const Json& node) {
    static const Json empty = Json::array();
    const auto at = node.find("children");
    return at != node.end() && at->is_array() ? *at : empty;
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
            cells.push_back({r, c, rs, cs, tag == "th", Text(cell)});
            for (int dr = 0; dr < rs; ++dr) for (int dc = 0; dc < cs; ++dc) occupied[r + dr][c + dc] = true;
            c += cs;
            columns = std::max(columns, c);
        }
    }
    if (!columns) return;
    const float width = std::max(170.0f, std::min(260.0f, ImGui::GetContentRegionAvail().x / columns));
    std::vector<float> heights(rows.size(), ImGui::GetTextLineHeight() + 16);
    for (const auto& cell : cells) {
        float need = ImGui::CalcTextSize(cell.text.c_str(), nullptr, false, width * cell.colspan - 16).y + 16;
        for (int r = cell.row; r < cell.row + cell.rowspan; ++r) heights[r] = std::max(heights[r], need / cell.rowspan);
    }
    std::vector<float> offsets{0};
    for (float height : heights) offsets.push_back(offsets.back() + height);
    float childHeight = std::min(500.0f, offsets.back() + 26);
    ImGui::PushID(&table);
    ImGui::BeginChild("table", ImVec2(0, childHeight), ImGuiChildFlags_Borders, ImGuiWindowFlags_HorizontalScrollbar);
    const ImVec2 origin = ImGui::GetCursorScreenPos();
    auto* draw = ImGui::GetWindowDrawList();
    for (const auto& cell : cells) {
        ImVec2 from(origin.x + cell.col * width, origin.y + offsets[cell.row]);
        ImVec2 to(from.x + cell.colspan * width, origin.y + offsets[cell.row + cell.rowspan]);
        draw->AddRectFilled(from, to, cell.heading ? IM_COL32(36, 55, 78, 255) : IM_COL32(22, 32, 47, 255));
        draw->AddRect(from, to, IM_COL32(86, 108, 137, 255));
        draw->PushClipRect(from, to, true);
        draw->AddText(nullptr, 0, ImVec2(from.x + 8, from.y + 8), IM_COL32(226, 232, 240, 255), cell.text.c_str(), nullptr, width * cell.colspan - 16);
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
    auto text = Text(node);
    if (text.find_first_not_of(" \r\n\t") == std::string::npos) return;
    bool heading = tag.size() == 2 && tag[0] == 'h';
    if (heading) ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(.58f, .78f, 1, 1));
    if (tag == "li") ImGui::Bullet();
    ImGui::TextWrapped("%s", text.c_str());
    if (heading) { ImGui::PopStyleColor(); ImGui::Spacing(); }
}
}

void DrawDocument(const Json& document) {
    if (!document.contains("posts")) return;
    for (const auto& post : document.at("posts")) {
        ImGui::PushID(post.value("post_id", "").c_str());
        for (const auto& node : post.at("blocks")) Node(node);
        ImGui::PopID();
    }
}
