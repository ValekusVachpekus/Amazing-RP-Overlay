#pragma once

enum class MainTab { Laws, Notebook, Settings };
struct TabNavigation {
    MainTab active=MainTab::Laws, content=MainTab::Laws;
    bool requestSelection=true;
    bool Select(MainTab tab, bool programmatic=true) {
        bool changed=active!=tab;
        active=tab;
        if(tab!=MainTab::Settings)content=tab;
        requestSelection=programmatic;
        return changed;
    }
};

// A visible sidebar has exactly one active mode. Clicking that mode again
// collapses it; selecting the other mode always opens it.
inline void SelectLibrary(bool requestedFavorites, bool& visible, bool& favoritesOnly) {
    visible = !visible || favoritesOnly != requestedFavorites;
    favoritesOnly = requestedFavorites;
}
