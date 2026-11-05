#pragma once
#include "rpc.h"
#include <imgui.h>
#include <algorithm>
#include <array>
#include <cmath>

struct Appearance {
    float opacity = .96f;
    int font = 17;
    bool light = false, animations = true;
    static Appearance Read(const Json& value) {
        return {std::clamp(value.value("opacity", .96f), .3f, 1.f),
                std::clamp(value.value("font_size", 17), 14, 26),
                value.value("theme", "dark") == "light", value.value("animations", true)};
    }
};

struct Fade {
    float position = 0;
    float Update(bool visible, bool enabled, float delta) {
        position = enabled ? std::clamp(position + (visible ? 1.f : -1.f) * std::max(0.f, delta) / .2f, 0.f, 1.f) : (visible ? 1.f : 0.f);
        return position * position * (3 - 2 * position);
    }
};

// Explicit draw-list colors must obey the same alpha as standard ImGui widgets.
inline ImU32 UiColor(ImU32 color) { return ImGui::GetColorU32(ImGui::ColorConvertU32ToFloat4(color)); }
void ApplyAppearance(const Appearance& appearance, float fade);

struct SettingsPanel {
    enum class Action { None, Save, Test, CancelTest };
    bool open = false, awaitingSave = false, revealKey = false;
    Appearance saved, draft;
    Json effective = Json::object(), openedValues = Json::object();
    int provider = 0, maxTokens = 1500;
    std::array<char, 2048> address{}, model{}, key{};
    std::array<char,8192> notesDirectory{};
    std::array<char,80> notesHotkey{};
    int section=0;
    std::array<std::array<char,80>,6> hotkeys{};
    void Receive(const Json& settings, bool savedResult = false);
    void Open();
    void Cancel();
    Json Values() const;
    Action Draw(bool busy, bool testing, const std::string& message);
    Appearance Current() const { return open ? draft : saved; }
};
