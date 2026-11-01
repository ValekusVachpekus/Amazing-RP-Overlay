#pragma once
#include <string>
#include <vector>
#include <utility>

namespace reader_search {
struct Letter { unsigned code; size_t start, end; };
inline std::vector<Letter> Decode(const std::string& text) {
    std::vector<Letter> result;
    for (size_t i = 0; i < text.size();) {
        const size_t start = i;
        unsigned c = static_cast<unsigned char>(text[i++]);
        int tail = c < 128 ? 0 : (c & 0xe0) == 0xc0 ? 1 : (c & 0xf0) == 0xe0 ? 2 : 3;
        c &= tail == 0 ? 0x7f : tail == 1 ? 0x1f : tail == 2 ? 0x0f : 0x07;
        while (tail-- && i < text.size()) c = (c << 6) | (static_cast<unsigned char>(text[i++]) & 0x3f);
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c >= 0x410 && c <= 0x42f) c += 32;
        if (c == 0x401) c = 0x451;
        result.push_back({c, start, i});
    }
    return result;
}
inline std::vector<std::pair<size_t, size_t>> Matches(const std::string& text, const std::string& query) {
    if (query.empty()) return {};
    const auto source = Decode(text), needle = Decode(query);
    std::vector<std::pair<size_t, size_t>> result;
    if (needle.empty()) return result;
    for (size_t i = 0; i + needle.size() <= source.size();) {
        size_t j = 0;
        while (j < needle.size() && source[i + j].code == needle[j].code) ++j;
        if (j == needle.size()) {
            result.emplace_back(source[i].start, source[i + j - 1].end);
            i += j;
        } else ++i;
    }
    return result;
}
}
