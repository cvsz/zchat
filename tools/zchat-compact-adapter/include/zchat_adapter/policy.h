#pragma once

#include <string>
#include <string_view>

namespace zchat::adapter {
struct Size {
    int width;
    int height;
};

inline constexpr Size kCompactDefault{360, 480};
inline constexpr Size kMinimumSize{320, 240};
inline constexpr Size kMaximumSize{4096, 4096};

bool is_managed_window_title(std::wstring_view title);
bool is_valid_size(Size size);
std::wstring json_escape(std::wstring_view value);
} // namespace zchat::adapter
