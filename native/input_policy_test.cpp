#include "input_policy.h"
#include <iostream>

int main() {
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
    // Multiple search hits for one law must not require several presses to reach the next law.
    std::vector<std::string> laws{"a", "a", "b", "c", "b"};
    if (NextDocument(laws, "a", 1) != "b" || NextDocument(laws, "b", -1) != "a") return 5;
    if (NextDocument(laws, "c", 1) != "c" || NextDocument(laws, "a", -1) != "a") return 6;
    if (!NextDocument({}, "a", 1).empty() || NextDocument(laws, "missing", 1) != "a") return 7;
    std::cout << "Input policy: PASS (cursor scope, release, named keys, filtered navigation)\n";
    return 0;
}
