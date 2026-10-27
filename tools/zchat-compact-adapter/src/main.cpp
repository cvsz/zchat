#ifdef _WIN32
#include "zchat_adapter/policy.h"

#include <windows.h>
#include <unknwn.h>
#include <UIAutomation.h>
#include <winver.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#pragma comment(lib, "uiautomationcore.lib")
#pragma comment(lib, "version.lib")

namespace {
using zchat::adapter::Size;
std::wofstream log_file;

void log_event(const wchar_t* event, const std::wstring& detail) {
    SYSTEMTIME now{};
    GetSystemTime(&now);
    if (log_file) {
        log_file << L"{\"timestamp_utc\":\""
                 << now.wYear << L"-" << now.wMonth << L"-" << now.wDay
                 << L"T" << now.wHour << L":" << now.wMinute << L":" << now.wSecond
                 << L"Z\",\"event\":\"" << event << L"\",\"detail\":\""
                 << zchat::adapter::json_escape(detail) << L"\"}\n";
        log_file.flush();
    }
}

std::wstring file_version(const std::wstring& path) {
    DWORD ignored = 0;
    const DWORD bytes = GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (!bytes) return L"unknown";
    std::vector<unsigned char> data(bytes);
    if (!GetFileVersionInfoW(path.c_str(), 0, bytes, data.data())) return L"unknown";
    VS_FIXEDFILEINFO* info = nullptr;
    UINT length = 0;
    if (!VerQueryValueW(data.data(), L"\\", reinterpret_cast<void**>(&info), &length)
        || !info || length < sizeof(VS_FIXEDFILEINFO)) return L"unknown";
    return std::to_wstring(HIWORD(info->dwFileVersionMS)) + L"." +
           std::to_wstring(LOWORD(info->dwFileVersionMS)) + L"." +
           std::to_wstring(HIWORD(info->dwFileVersionLS)) + L"." +
           std::to_wstring(LOWORD(info->dwFileVersionLS));
}

std::wstring process_image_path(HWND hwnd) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (!pid) return {};
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return {};
    std::wstring path(32768, L'\\0');
    DWORD length = static_cast<DWORD>(path.size());
    const BOOL ok = QueryFullProcessImageNameW(process, 0, path.data(), &length);
    CloseHandle(process);
    if (!ok) return {};
    path.resize(length);
    return path;
}

bool canonicalize_existing_image_path(std::wstring& path) {
    if (path.empty() || !std::filesystem::path(path).is_absolute()) return false;
    const DWORD required = GetFullPathNameW(path.c_str(), 0, nullptr, nullptr);
    if (!required || required > 32768) return false;
    std::wstring full(required, L'\0');
    const DWORD length = GetFullPathNameW(path.c_str(), required, full.data(), nullptr);
    if (!length || length >= required) return false;
    full.resize(length);
    const DWORD attributes = GetFileAttributesW(full.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) return false;
    path = full;
    return true;
}

std::wstring window_text(HWND hwnd) {
    const int length = GetWindowTextLengthW(hwnd);
    if (length <= 0 || length > 1024) return {};
    std::wstring text(static_cast<size_t>(length) + 1, L'\0');
    const int copied = GetWindowTextW(hwnd, text.data(), length + 1);
    text.resize(copied > 0 ? static_cast<size_t>(copied) : 0);
    return text;
}

struct Options {
    Size size{360, 480};
    bool apply{false};
    std::wstring log_path{L"zchat-compact-adapter.jsonl"};
    std::vector<std::wstring> allowed_images;
};
bool parse_int(const wchar_t* value, int& out) {
    try {
        size_t used = 0; const int n = std::stoi(value, &used);
        if (value[used] != L'\0') return false;
        out = n; return true;
    } catch (...) { return false; }
}
bool parse_args(int argc, wchar_t** argv, Options& o) {
    for (int i = 1; i < argc; ++i) {
        const std::wstring arg = argv[i];
        if (arg == L"--apply") o.apply = true;
        else if (arg == L"--width" && i + 1 < argc) { if (!parse_int(argv[++i], o.size.width)) return false; }
        else if (arg == L"--height" && i + 1 < argc) { if (!parse_int(argv[++i], o.size.height)) return false; }
        else if (arg == L"--log" && i + 1 < argc) o.log_path = argv[++i];
        else if (arg == L"--allow-image" && i + 1 < argc) {
            std::wstring image_path = argv[++i];
            if (!canonicalize_existing_image_path(image_path)) {
                std::wcerr << L"--allow-image must be an existing absolute executable path.\n";
                return false;
            }
            o.allowed_images.push_back(image_path);
        }
        else if (arg == L"--help") {
            std::wcout << L"Usage: zchat-compact-adapter [--width 360] [--height 480] [--apply --allow-image <absolute-exe-path>] [--log path]\n"
                       << L"Default is dry-run. Apply mode requires one or more exact process-image paths.\n"
                       << L"Title prefixes are an additional filter, not process identity.\n";
            return false;
        } else return false;
    }
    if (!zchat::adapter::is_valid_size(o.size)) return false;
    if (o.apply && o.allowed_images.empty()) {
        std::wcerr << L"--apply requires at least one --allow-image <absolute-exe-path>.\n";
        return false;
    }
    return true;
}

struct ScanContext { IUIAutomation* automation; Options* options; unsigned seen{0}; unsigned changed{0}; };
BOOL CALLBACK scan_window(HWND hwnd, LPARAM param) {
    auto* ctx = reinterpret_cast<ScanContext*>(param);
    if (!IsWindowVisible(hwnd) || IsIconic(hwnd)) return TRUE;
    const auto title = window_text(hwnd);
    if (!zchat::adapter::is_managed_window_title(title)) return TRUE;
    ++ctx->seen;

    // Confirm the HWND can be represented by UI Automation before acting.
    IUIAutomationElement* element = nullptr;
    const HRESULT hr = ctx->automation->ElementFromHandle(hwnd, &element);
    if (FAILED(hr) || !element) {
        log_event(L"uia_unavailable", title);
        if (element) element->Release();
        return TRUE;
    }
    element->Release();

    RECT current{};
    if (!GetWindowRect(hwnd, &current)) {
        log_event(L"window_rect_failed", title);
        return TRUE;
    }
    const auto width = ctx->options->size.width;
    const auto height = ctx->options->size.height;
    auto image_path = process_image_path(hwnd);
    if (!image_path.empty() && !canonicalize_existing_image_path(image_path)) image_path.clear();
    const auto version = image_path.empty() ? std::wstring(L"unknown") : file_version(image_path);
    if (ctx->options->apply &&
        !zchat::adapter::is_allowed_process_image(image_path, ctx->options->allowed_images)) {
        log_event(L"process_image_not_allowed", title + L" [exe_version=" + version + L"]");
        std::wcout << L"SKIPPED (process image not allowlisted) " << title << L"\n";
        return TRUE;
    }
    const std::wstring detail = title + L" [exe_version=" + version + L"] (" + std::to_wstring(current.right-current.left) +
        L"x" + std::to_wstring(current.bottom-current.top) + L" -> " +
        std::to_wstring(width) + L"x" + std::to_wstring(height) + L")";
    if (!ctx->options->apply) {
        log_event(L"dry_run_match", detail);
        std::wcout << L"DRY-RUN " << detail << L"\n";
        return TRUE;
    }
    if (SetWindowPos(hwnd, nullptr, 0, 0, width, height,
                     SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)) {
        ++ctx->changed;
        log_event(L"resize_applied", detail);
        std::wcout << L"RESIZED " << detail << L"\n";
    } else {
        log_event(L"resize_failed", title + L" error=" + std::to_wstring(GetLastError()));
    }
    return TRUE;
}
} // namespace

int wmain(int argc, wchar_t** argv) {
    Options options;
    if (!parse_args(argc, argv, options)) {
        for (int i = 1; i < argc; ++i) {
            if (std::wstring(argv[i]) == L"--help") return 0;
        }
        return 2;
    }
    log_file.open(options.log_path, std::ios::out | std::ios::app);
    log_event(L"startup", L"version=1.0.0 mode=" + std::wstring(options.apply ? L"apply" : L"dry-run"));

    const HRESULT init = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(init)) { log_event(L"com_init_failed", std::to_wstring(init)); return 3; }
    IUIAutomation* automation = nullptr;
    const HRESULT created = CoCreateInstance(CLSID_CUIAutomation, nullptr, CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(&automation));
    if (FAILED(created) || !automation) {
        log_event(L"uia_init_failed", std::to_wstring(created));
        CoUninitialize(); return 4;
    }

    ScanContext context{automation, &options};
    EnumWindows(scan_window, reinterpret_cast<LPARAM>(&context));
    log_event(L"scan_complete", L"matched=" + std::to_wstring(context.seen) +
        L" resized=" + std::to_wstring(context.changed) +
        L" client_version_detection=per-window-process-file-version");
    // Report executable version detection for each matched window's owning process.
    // Window resize remains independent of process version; unknown versions are logged by policy.
    automation->Release();
    CoUninitialize();
    std::wcout << L"Matched " << context.seen << L" ZChat window(s); resized "
               << context.changed << L". " << (options.apply ? L"Apply" : L"Dry-run") << L" mode.\n";
    return 0;
}
#endif
