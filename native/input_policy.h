#pragma once
#include <algorithm>
#include <string>
#include <vector>

namespace overlay_input {
struct Binding { int key; bool ctrl, alt, shift, win = false; };

inline Binding ParseBinding(std::string value, Binding fallback) {
    value.erase(std::remove(value.begin(), value.end(), ' '), value.end());
    for (char& c : value) if (c >= 'a' && c <= 'z') c -= 32;
    auto at = value.rfind('+');
    auto key = at == std::string::npos ? value : value.substr(at + 1);
    int code = 0;
    if (key.size() == 1) code = key[0];
    else if (key == "LEFT") code = 0x25;
    else if (key == "RIGHT") code = 0x27;
    else if (key == "UP") code = 0x26;
    else if (key == "DOWN") code = 0x28;
    else if (key == "SPACE") code = 0x20;
    else if (key.size() > 1 && key[0] == 'F') {
        try { int n = std::stoi(key.substr(1)); if (n >= 1 && n <= 24) code = 0x70 + n - 1; } catch (...) {}
    }
    if (!code) return fallback;
    return {code, value.find("CTRL+") != std::string::npos, value.find("ALT+") != std::string::npos,
            value.find("SHIFT+") != std::string::npos, value.find("WIN+") != std::string::npos};
}

inline bool SuppressCursor(bool input, bool foreground) {
    return input && foreground;
}

inline bool SuppressClip(bool input, bool foreground, bool hasRectangle) {
    return SuppressCursor(input, foreground) && hasRectangle;
}

inline std::string NextDocument(const std::vector<std::string>& visible, const std::string& current, int direction) {
    std::vector<std::string> unique;
    for (const auto& id : visible)
        if (!id.empty() && std::find(unique.begin(), unique.end(), id) == unique.end()) unique.push_back(id);
    if (unique.empty()) return {};
    auto at = std::find(unique.begin(), unique.end(), current);
    if (at == unique.end()) return unique.front();
    auto index = static_cast<int>(at - unique.begin());
    index = std::clamp(index + direction, 0, static_cast<int>(unique.size()) - 1);
    return unique[index];
}
}
