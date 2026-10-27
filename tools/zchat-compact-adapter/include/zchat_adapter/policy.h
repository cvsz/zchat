#pragma once

#include <string>
#include <string_view>
#include <vector>

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
bool is_allowed_process_image(std::wstring_view image_path,
                              const std::vector<std::wstring>& allowed_images);
std::wstring json_escape(std::wstring_view value);
} // namespace zchat::adapter
