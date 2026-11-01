#pragma once
#include <string>
#include <vector>
#include <deque>
#include <algorithm>
#include <utility>

namespace notebook {
inline std::u32string Decode(const std::string& value) {
    std::u32string out;
    for (size_t i = 0; i < value.size();) {
        unsigned code = static_cast<unsigned char>(value[i++]);
        int count = code < 128 ? 0 : (code & 0xe0) == 0xc0 ? 1 : (code & 0xf0) == 0xe0 ? 2 : 3;
        code &= count == 0 ? 127 : count == 1 ? 31 : count == 2 ? 15 : 7;
        while (count-- && i < value.size()) code = (code << 6) | (static_cast<unsigned char>(value[i++]) & 63);
        if (code != '\r') out.push_back(static_cast<char32_t>(code));
    }
    return out;
}
inline std::string Encode(const std::u32string& value) {
    std::string out;
    for (unsigned c : value) {
        if (c < 128) out += static_cast<char>(c);
        else if (c < 2048) { out += static_cast<char>(192 | (c >> 6)); out += static_cast<char>(128 | (c & 63)); }
        else if (c < 65536) { out += static_cast<char>(224 | (c >> 12)); out += static_cast<char>(128 | ((c >> 6) & 63)); out += static_cast<char>(128 | (c & 63)); }
        else { out += static_cast<char>(240 | (c >> 18)); out += static_cast<char>(128 | ((c >> 12) & 63)); out += static_cast<char>(128 | ((c >> 6) & 63)); out += static_cast<char>(128 | (c & 63)); }
    }
    return out;
}
inline char32_t Fold(char32_t c) {
    if (c >= U'A' && c <= U'Z') return c + 32;
    if (c >= U'А' && c <= U'Я') return c + 32;
    return c == U'Ё' ? U'ё' : c;
}
struct Snapshot { std::u32string text; size_t cursor, anchor; };
class PlainEditor {
public:
    std::u32string text;
    size_t cursor = 0, anchor = 0;
    unsigned revision = 0;
    std::deque<Snapshot> undo, redo;
    void Load(const std::string& value) { text = Decode(value); cursor = anchor = 0; undo.clear(); redo.clear(); ++revision; }
    std::string Utf8() const { return Encode(text); }
    std::pair<size_t, size_t> Selection() const { return {std::min(cursor, anchor), std::max(cursor, anchor)}; }
    std::string Selected() const { auto [a,b] = Selection(); return Encode(text.substr(a,b-a)); }
    void Remember() {
        undo.push_back({text,cursor,anchor}); redo.clear();
        size_t count = 0;
        for (const auto& entry : undo) count += entry.text.size() * 4;
        while (undo.size() > 1 && (undo.size() > 100 || count > 8 * 1024 * 1024)) { count -= undo.front().text.size() * 4; undo.pop_front(); }
    }
    void Insert(const std::u32string& value) {
        auto [a,b] = Selection();
        auto proposed = text; proposed.replace(a,b-a,value);
        if (Encode(proposed).size() > 1024 * 1024) return;
        Remember(); text = std::move(proposed); cursor = anchor = a + value.size(); ++revision;
    }
    void Erase(bool backwards, bool word = false) {
        if (cursor == anchor) {
            if (backwards && cursor) anchor = word ? Word(cursor,-1) : cursor-1;
            if (!backwards && cursor < text.size()) anchor = word ? Word(cursor,1) : cursor+1;
        }
        if (cursor != anchor) Insert(U"");
    }
    void Undo(bool forward = false) {
        auto& from = forward ? redo : undo; auto& to = forward ? undo : redo;
        if (from.empty()) return;
        to.push_back({text,cursor,anchor}); auto value = std::move(from.back()); from.pop_back();
        text = std::move(value.text); cursor = value.cursor; anchor = value.anchor; ++revision;
    }
    size_t Word(size_t at, int direction) const {
        auto space = [](char32_t c) { return c == U' ' || c == U'\n' || c == U'\t'; };
        if (direction < 0) { while (at && space(text[at-1])) --at; while (at && !space(text[at-1])) --at; }
        else { while (at < text.size() && !space(text[at])) ++at; while (at < text.size() && space(text[at])) ++at; }
        return at;
    }
    void Move(size_t at, bool select) { cursor = std::min(at,text.size()); if (!select) anchor = cursor; }
    std::vector<std::pair<size_t,size_t>> Matches(const std::string& value) const {
        auto query = Decode(value); std::vector<std::pair<size_t,size_t>> out;
        if (query.empty()) return out;
        for (auto& c : query) c = Fold(c);
        std::vector<size_t> prefix(query.size());
        for(size_t i=1,j=0;i<query.size();++i){
            while(j && query[i]!=query[j])j=prefix[j-1];
            if(query[i]==query[j])++j;prefix[i]=j;
        }
        for(size_t i=0,j=0;i<text.size();++i){
            auto c=Fold(text[i]);while(j && c!=query[j])j=prefix[j-1];
            if(c==query[j])++j;
            if(j==query.size()){out.emplace_back(i+1-j,i+1);j=0;}
        }
        return out;
    }
};
}
