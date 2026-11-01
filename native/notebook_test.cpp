#include "plain_editor_view.h"
#include <cassert>

int main() {
    using namespace notebook;
    PlainEditor editor;
    editor.Load("Русский\nEnglish Ёж 😀");
    assert(editor.Utf8()=="Русский\nEnglish Ёж 😀");
    editor.Move(editor.text.size(),false);editor.Insert(U" конец");
    auto modified=editor.Utf8();editor.Undo();assert(editor.Utf8()=="Русский\nEnglish Ёж 😀");
    editor.Undo(true);assert(editor.Utf8()==modified);
    assert(editor.Matches("РУССКИЙ").size()==1);
    assert(editor.Matches("ёЖ").size()==1);
    editor.anchor=0;editor.cursor=7;assert(editor.Selected()=="Русский");
    editor.Insert(U"Закон");assert(editor.Utf8().rfind("Закон\n",0)==0);
    editor.Load(std::string(1024*1024,'a'));editor.Move(editor.text.size(),false);editor.Insert(U"b");assert(editor.text.size()==1024*1024);
    auto* context=ImGui::CreateContext();
    auto& io=ImGui::GetIO();io.IniFilename=nullptr;io.DisplaySize=ImVec2(600,400);io.DeltaTime=1.f/60;
    io.Fonts->AddFontDefault();unsigned char* pixels;int w,h;io.Fonts->GetTexDataAsRGBA32(&pixels,&w,&h);
    EditorView view;view.model.Load("Long line with many words to wrap across the editor.\n\nSecond paragraph.\n");
    for(int font=14;font<=26;++font){
        ImGui::NewFrame();ImGui::SetNextWindowSize(ImVec2(500,350));ImGui::Begin("Test");
        ImGui::SetWindowFontScale(font/13.f);view.Layout(100);
        assert(std::abs(view.fontSize-font)<.01f);
        assert(std::abs(view.Advance(U'a')-ImGui::GetFont()->GetCharAdvance('a')*font/13.f)<.01f);
        assert(view.rows.size()>4);
        for(size_t r=0;r<view.rows.size();++r){
            auto row=view.rows[r];assert(row.end>=row.start);
            assert(view.Hit(r,0)==row.start);assert(view.Hit(r,10000)==row.end);
        }
        view.Find("line");assert(view.matches.size()==1);view.Step(1);assert(view.model.Selected()=="line");
        view.Draw(false,false);ImGui::End();ImGui::Render();
        assert(ImGui::GetDrawData()->TotalVtxCount>0);
    }
    ImGui::DestroyContext(context);
}
