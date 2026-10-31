#pragma once
#include <nlohmann/json.hpp>
#include <string>

using Json = nlohmann::json;
Json Rpc(const std::string& method, const Json& params = Json::object());
struct DocumentSearch {
    std::string query;
    int active = 0, total = 0, cursor = 0;
    bool jump = false;
};
void DrawDocument(const Json& document, DocumentSearch* search = nullptr);
