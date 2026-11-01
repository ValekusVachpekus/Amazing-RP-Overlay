#include "input_policy.h"
#include "text_search.h"
#include "display_text.h"
#include <iostream>

int main() {
    if (LawDisplayTitle("ЗАКРЕПЛЕНО\n\xC2\xA0\nТрудовой кодекс") != "Трудовой кодекс") return 12;
    if (LawDisplayTitle("\xEF\xBB\xBF ИНФОРМАЦИЯ\n\xE2\x80\x8B\nЗакон\tоб оружии ") != "Закон об оружии") return 13;
    if (LawDisplayTitle(" \n\xC2\xA0 ") != "Без названия") return 14;
    using reader_search::Matches;
    if (Matches("Статья СТАТЬЯ статья", "статья").size() != 3) return 8;
    if (Matches("Ёж ёж ЕЖ", "ёж").size() != 2) return 9;
    const auto match = Matches("xx Закон yy", "закон");
    if (match.size() != 1 || match[0].first != 3 || match[0].second != 13) return 10;
    if (!Matches("text", "").empty() || Matches("aaa", "aa").size() != 1) return 11;
    using namespace overlay_input;
    // Capture only while interacting with the game; Alt+Tab and view restore OS behavior.
    if (!SuppressCursor(true, true) || SuppressCursor(true, false) || SuppressCursor(false, true)) return 1;
    // Always allow ClipCursor(nullptr), which releases the cursor.
    if (SuppressClip(true, true, false) || !SuppressClip(true, true, true) || SuppressClip(false, true, true)) return 2;
    auto left = ParseBinding("alt+left", {'L', true, false, false});
    auto right = ParseBinding(" Alt + Right ", left);
    auto up = ParseBinding("Alt+Up", left);
    if (left.key != 0x25 || !left.alt || left.ctrl || right.key != 0x27 || up.key != 0x26) return 3;
    if (ParseBinding("Ctrl+F8", left).key != 0x77 || ParseBinding("invalid", left).key != left.key) return 4;
    if (BindingLabel(ParseBinding("F11", left)) != "F11" || BindingLabel(ParseBinding("Shift+F9", left)) != "Shift+F9") return 15;
    if (BindingLabel(ParseBinding("Ctrl+N", left)) != "Ctrl+N" || BindingLabel(right) != "Alt+Right") return 16;
    if (BindingLabel(ParseBinding("Ctrl+Alt+Shift+Win+Space", left)) != "Ctrl+Alt+Shift+Win+Space") return 17;
    // Multiple search hits for one law must not require several presses to reach the next law.
    std::vector<std::string> laws{"a", "a", "b", "c", "b"};
    if (NextDocument(laws, "a", 1) != "b" || NextDocument(laws, "b", -1) != "a") return 5;
    if (NextDocument(laws, "c", 1) != "c" || NextDocument(laws, "a", -1) != "a") return 6;
    if (!NextDocument({}, "a", 1).empty() || NextDocument(laws, "missing", 1) != "a") return 7;
    std::cout << "Input policy: PASS (cursor scope, release, named keys, filtered navigation)\n";
    return 0;
}
