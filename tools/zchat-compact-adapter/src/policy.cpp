#include "zchat_adapter/policy.h"

namespace {
std::wstring normalize_image_path(std::wstring_view path) {
    std::wstring normalized;
    normalized.reserve(path.size());
    for (wchar_t ch : path) {
        if (ch == L'/') ch = L'\\';
        if (ch >= L'A' && ch <= L'Z') ch = static_cast<wchar_t>(ch - L'A' + L'a');
        normalized.push_back(ch);
    }
    while (normalized.size() > 3 && normalized.back() == L'\\') normalized.pop_back();
    return normalized;
}
} // namespace

namespace zchat::adapter {
bool is_managed_window_title(std::wstring_view title) {
    // Intentionally restrict changes to windows explicitly branded as ZChat.
    constexpr std::wstring_view prefixes[] = {
        L"ZChat ", L"ZChat-", L"ZChat:"
    };
    for (const auto prefix : prefixes) {
        if (title.size() >= prefix.size() &&
            title.substr(0, prefix.size()) == prefix) {
            return true;
        }
    }
    return false;
}

bool is_allowed_process_image(std::wstring_view image_path,
                              const std::vector<std::wstring>& allowed_images) {
    if (image_path.empty()) return false;
    const auto candidate = normalize_image_path(image_path);
    if (candidate.empty()) return false;
    for (const auto& allowed : allowed_images) {
        if (!allowed.empty() && normalize_image_path(allowed) == candidate) return true;
    }
    return false;
}

bool is_valid_size(Size size) {
    return size.width >= kMinimumSize.width &&
           size.height >= kMinimumSize.height &&
           size.width <= kMaximumSize.width &&
           size.height <= kMaximumSize.height;
}

std::wstring json_escape(std::wstring_view value) {
    std::wstring out;
    out.reserve(value.size() + 8);
    for (const wchar_t ch : value) {
        switch (ch) {
        case L'"': out += L"\\\""; break;
        case L'\\': out += L"\\\\"; break;
        case L'\n': out += L"\\n"; break;
        case L'\r': out += L"\\r"; break;
        case L'\t': out += L"\\t"; break;
        default:
            if (ch >= 0x20) out += ch;
            else out += L'?';
        }
    }
    return out;
}
} // namespace zchat::adapter
