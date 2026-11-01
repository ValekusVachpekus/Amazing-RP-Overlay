#pragma once
#include "text_search.h"

inline std::string LawDisplayTitle(const std::string& title) {
    std::string result;
    bool space = false;
    for (const auto& letter : reader_search::Decode(title)) {
        const unsigned c = letter.code;
        if (c == 0x200b || c == 0xfeff) continue;
        if (c <= 0x20 || c == 0xa0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200a) ||
            c == 0x2028 || c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x2800 || c == 0x3000) {
            space = !result.empty();
            continue;
        }
        if (space) result += ' ';
        space = false;
        result.append(title, letter.start, letter.end - letter.start);
    }
    for (const std::string prefix : {"ЗАКРЕПЛЕНО ", "ИНФОРМАЦИЯ "}) {
        if (result.rfind(prefix, 0) == 0) result.erase(0, prefix.size());
    }
    return result.empty() ? "Без названия" : result;
}
