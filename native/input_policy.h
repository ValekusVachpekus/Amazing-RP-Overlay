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

inline std::string BindingLabel(const Binding& binding) {
    std::string value;
    if (binding.ctrl) value += "Ctrl+";
    if (binding.alt) value += "Alt+";
    if (binding.shift) value += "Shift+";
    if (binding.win) value += "Win+";
    switch (binding.key) {
    case 0x1b: return value + "Esc";
    case 0x20: return value + "Space";
    case 0x25: return value + "Left";
    case 0x26: return value + "Up";
    case 0x27: return value + "Right";
    case 0x28: return value + "Down";
    }
    if (binding.key >= 0x70 && binding.key <= 0x87)
        return value + "F" + std::to_string(binding.key - 0x70 + 1);
    return value + static_cast<char>(binding.key);
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
