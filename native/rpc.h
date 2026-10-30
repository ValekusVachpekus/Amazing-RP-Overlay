#pragma once
#include <nlohmann/json.hpp>
#include <string>

using Json = nlohmann::json;
Json Rpc(const std::string& method, const Json& params = Json::object());
void DrawDocument(const Json& document);
