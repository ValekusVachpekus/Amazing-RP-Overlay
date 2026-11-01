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
