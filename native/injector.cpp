// Fixed-payload launcher for AmazingHelper. No arbitrary DLL paths or drivers.
#include <BlackBone/Process/Process.h>
#include <windows.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <bcrypt.h>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <vector>
#include "helper_version.h"

using Json = nlohmann::json;
struct Handle {
    HANDLE h = nullptr;
    ~Handle() { if(h && h != INVALID_HANDLE_VALUE) CloseHandle(h); }
};
std::wstring Path(HANDLE process) {
    wchar_t value[32768]{}; DWORD size=32768;
    if(!QueryFullProcessImageNameW(process,0,value,&size))throw std::runtime_error("Нет доступа к пути игры");
    return value;
}
std::string Created(HANDLE process) {
    FILETIME c{},e{},k{},u{};
    if(!GetProcessTimes(process,&c,&e,&k,&u))throw std::runtime_error("Нет доступа к идентичности игры");
    return std::to_string((static_cast<unsigned long long>(c.dwHighDateTime)<<32)|c.dwLowDateTime);
}
std::vector<unsigned char> User(HANDLE process) {
    Handle token;
    if(!OpenProcessToken(process,TOKEN_QUERY,&token.h))throw std::runtime_error("Нет доступа к владельцу процесса");
    DWORD size=0; GetTokenInformation(token.h,TokenUser,nullptr,0,&size);
    std::vector<unsigned char> result(size);
    if(!GetTokenInformation(token.h,TokenUser,result.data(),size,&size))throw std::runtime_error("Нет доступа к владельцу процесса");
    return result;
}
std::string Hash(const std::vector<unsigned char>& data) {
    BCRYPT_ALG_HANDLE provider=nullptr; BCRYPT_HASH_HANDLE hash=nullptr;
    if(BCryptOpenAlgorithmProvider(&provider,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0)throw std::runtime_error("SHA256 unavailable");
    DWORD size=0,count=0; BCryptGetProperty(provider,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&size),sizeof(size),&count,0);
    std::vector<unsigned char> object(size); unsigned char digest[32]{};
    auto status=BCryptCreateHash(provider,&hash,object.data(),size,nullptr,0,0);
    if(status>=0)status=BCryptHashData(hash,const_cast<PUCHAR>(data.data()),static_cast<ULONG>(data.size()),0);
    if(status>=0)status=BCryptFinishHash(hash,digest,32,0);
    if(hash)BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(provider,0);
    if(status<0)throw std::runtime_error("SHA256 failed");
    std::ostringstream out; for(auto byte:digest)out<<std::hex<<std::setw(2)<<std::setfill('0')<<static_cast<int>(byte);
    return out.str();
}
BOOL CALLBACK OldOwner(HWND window,LPARAM param) {
    DWORD pid=0; GetWindowThreadProcessId(window,&pid);
    auto* pair=reinterpret_cast<std::pair<DWORD,bool>*>(param);
    if(pid==pair->first && GetPropW(window,L"AmazingLawOverlay.NativeInputOwner.v1"))pair->second=true;
    return TRUE;
}
int Output(Json value,int code) {
    auto data=value.dump()+"\n"; DWORD written=0;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),data.data(),static_cast<DWORD>(data.size()),&written,nullptr);
    return code;
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int) {
    std::string stage="preflight";
    try {
        int argc=0; auto argv=CommandLineToArgvW(GetCommandLineW(),&argc);
        if(!argv)throw std::runtime_error("Некорректные аргументы");
        std::vector<std::wstring> args(argv,argv+argc); LocalFree(argv);
        if(args.size()==2 && args[1]==L"--version")return Output({{"version",AMAZING_HELPER_VERSION}},0);
        if(args.size()!=9 || args[1]!=L"--pid" || args[3]!=L"--created" || args[5]!=L"--image" || args[7]!=L"--sha256")throw std::runtime_error("Некорректные аргументы загрузчика");
        size_t used=0; auto number=std::stoull(args[2],&used);
        if(used!=args[2].size() || !number || number>0xffffffff)throw std::runtime_error("Некорректный PID");
        DWORD pid=static_cast<DWORD>(number);
        Handle target{OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,pid)};
        if(!target.h)throw std::runtime_error("Нет доступа к игре. Проверьте одинаковые права запуска.");
        auto image=Path(target.h);
        auto name=std::filesystem::path(image).filename().wstring();
        bool allowed=_wcsicmp(name.c_str(),L"amazing.exe")==0 || _wcsicmp(name.c_str(),L"gta_sa.exe")==0;
#ifdef AMAZING_HELPER_TEST_HOST
        allowed=_wcsicmp(name.c_str(),L"overlay_native_smoke.exe")==0 ||
                _wcsicmp(name.c_str(),L"overlay_native_smoke_dep_off.exe")==0;
#endif
        if(!allowed || _wcsicmp(image.c_str(),args[6].c_str()) || Created(target.h)!=std::string(args[4].begin(),args[4].end()) || WaitForSingleObject(target.h,0)!=WAIT_TIMEOUT)throw std::runtime_error("Игровой процесс изменился");
        BOOL wow=FALSE;
        if(!IsWow64Process(target.h,&wow) || !wow)throw std::runtime_error("Требуется игра x86 на Windows x64");
        auto own=User(GetCurrentProcess()), remote=User(target.h);
        if(!EqualSid(reinterpret_cast<TOKEN_USER*>(own.data())->User.Sid,reinterpret_cast<TOKEN_USER*>(remote.data())->User.Sid))throw std::runtime_error("Игра принадлежит другому пользователю");
        auto mutexName=L"Local\\AmazingHelper.Native."+std::to_wstring(pid);
        Handle existing{OpenMutexW(SYNCHRONIZE,FALSE,mutexName.c_str())};
        std::pair<DWORD,bool> owner{pid,false}; EnumWindows(OldOwner,reinterpret_cast<LPARAM>(&owner));
        Handle snapshot{CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid)};
        if(snapshot.h==INVALID_HANDLE_VALUE)throw std::runtime_error("Нет доступа к списку модулей игры");
        MODULEENTRY32W module{};module.dwSize=sizeof(module);
        for(BOOL next=Module32FirstW(snapshot.h,&module);next;next=Module32NextW(snapshot.h,&module))
            if(!_wcsicmp(module.szModule,L"AmazingLawClient.dll") || !_wcsicmp(module.szModule,L"AmazingLawClient.asi"))owner.second=true;
        if(existing.h || owner.second)throw std::runtime_error("DLL/ASI уже загружена. При необходимости перезапустите игру.");
        wchar_t exe[32768]{}; if(!GetModuleFileNameW(nullptr,exe,32768))throw std::runtime_error("Не найден каталог загрузчика");
        auto path=std::filesystem::path(exe).parent_path()/L"AmazingLawClient.dll";
        std::ifstream input(path,std::ios::binary); if(!input)throw std::runtime_error("Не найдена собственная DLL");
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)),{});
        if(bytes.size()<sizeof(IMAGE_DOS_HEADER) || bytes.size()>32*1024*1024 || Hash(bytes)!=std::string(args[8].begin(),args[8].end()))throw std::runtime_error("Контрольная сумма DLL не совпадает");
        auto dos=reinterpret_cast<const IMAGE_DOS_HEADER*>(bytes.data());
        if(dos->e_magic!=IMAGE_DOS_SIGNATURE || dos->e_lfanew<0 || static_cast<size_t>(dos->e_lfanew)+sizeof(IMAGE_NT_HEADERS32)>bytes.size())throw std::runtime_error("Некорректная DLL");
        auto nt=reinterpret_cast<const IMAGE_NT_HEADERS32*>(bytes.data()+dos->e_lfanew);
        if(nt->Signature!=IMAGE_NT_SIGNATURE || nt->FileHeader.Machine!=IMAGE_FILE_MACHINE_I386)throw std::runtime_error("Требуется DLL x86");
        blackbone::Process process;
        auto status=process.Attach(pid,PROCESS_QUERY_INFORMATION|PROCESS_VM_READ|PROCESS_VM_WRITE|PROCESS_VM_OPERATION|PROCESS_CREATE_THREAD|PROCESS_DUP_HANDLE|SYNCHRONIZE);
        if(!NT_SUCCESS(status))throw std::runtime_error("Windows или защита игры отклонила доступ к процессу");
        stage="mapping";
        // Preserve the PE section protections even for legacy DEP-off targets.
        // Blackbone's default useDep casting strips PAGE_EXECUTE there, making
        // MinHook reject our own detour. This does not change the target's DEP
        // policy or the protection of any existing game/third-party code.
        process.memory().protectionCasting(blackbone::MemProtectionCasting::none);
        // Map the verified in-memory bytes, avoiding a file replacement race.
        auto result=process.mmap().MapImage(bytes.size(),bytes.data(),false,blackbone::NoFlags);
        if(!result)throw std::runtime_error("Manual Map завершился ошибкой. Перезапустите игру. NTSTATUS="+std::to_string(static_cast<unsigned long>(result.status)));
        return Output({{"ok",true},{"stage","mapped"},{"version",AMAZING_HELPER_VERSION},
                       {"dep_enabled",process.core().DEP()}},0);
    } catch(const std::exception& error) {
        return Output({{"ok",false},{"stage",stage},{"error",error.what()}},1);
    }
}
