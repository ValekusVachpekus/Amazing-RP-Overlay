#include "rpc.h"
#include <windows.h>
#include <atomic>
#include <stdexcept>
#include <vector>

namespace {
struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    ~Handle() { if (value != INVALID_HANDLE_VALUE && value != nullptr) CloseHandle(value); }
};

DWORD Transfer(HANDLE pipe, void* buffer, DWORD size, bool writing) {
    Handle event{CreateEventW(nullptr, TRUE, FALSE, nullptr)};
    if (!event.value) throw std::runtime_error("Cannot create IPC event");
    OVERLAPPED operation{};
    operation.hEvent = event.value;
    DWORD count = 0;
    BOOL ok = writing ? WriteFile(pipe, buffer, size, &count, &operation) : ReadFile(pipe, buffer, size, &count, &operation);
    if (!ok && GetLastError() == ERROR_IO_PENDING) {
        if (WaitForSingleObject(event.value, 15000) != WAIT_OBJECT_0) {
            CancelIoEx(pipe, &operation);
            GetOverlappedResult(pipe, &operation, &count, TRUE);
            throw std::runtime_error("Backend IPC timed out");
        }
        ok = GetOverlappedResult(pipe, &operation, &count, FALSE);
    }
    if (!ok || count == 0) throw std::runtime_error("Backend disconnected");
    return count;
}
}

Json Rpc(const std::string& method, const Json& params) {
    static std::atomic<unsigned> next{1};
    wchar_t configured[256]{};
    GetEnvironmentVariableW(L"AMAZING_OVERLAY_PIPE", configured, 256);
    const std::wstring name = L"\\\\.\\pipe\\" + std::wstring(configured[0] ? configured : L"amazing-law-overlay-v2");
    if (!WaitNamedPipeW(name.c_str(), 1000)) throw std::runtime_error("Start run_backend.bat or run_manager.bat");
    Handle pipe{CreateFileW(name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
                           FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, nullptr)};
    if (pipe.value == INVALID_HANDLE_VALUE) throw std::runtime_error("Cannot connect to backend");
    const auto id = std::to_string(GetCurrentProcessId()) + "-" + std::to_string(next++);
    auto payload = Json{{"v", 1}, {"id", id}, {"method", method}, {"params", params}}.dump() + "\n";
    size_t sent = 0;
    while (sent < payload.size()) sent += Transfer(pipe.value, payload.data() + sent, static_cast<DWORD>(payload.size() - sent), true);
    std::string data;
    char buffer[8192];
    while (data.find('\n') == std::string::npos) {
        data.append(buffer, Transfer(pipe.value, buffer, sizeof(buffer), false));
        if (data.size() > 16 * 1024 * 1024) throw std::runtime_error("IPC frame exceeds 16 MB");
    }
    auto result = Json::parse(data.substr(0, data.find('\n')));
    if (result.value("v", 0) != 1 || result.value("id", "") != id) throw std::runtime_error("Incompatible backend protocol");
    if (result.contains("error")) throw std::runtime_error(result["error"].value("message", "Backend error"));
    return result.at("result");
}
