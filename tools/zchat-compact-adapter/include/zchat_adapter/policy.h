#pragma once

#include <functional>
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

enum class MutationResult {
    identity_changed,
    resized,
    failed
};

enum class ResizeOutcome {
    title_not_managed,
    dry_run,
    process_image_not_allowed,
    identity_changed,
    resized,
    resize_failed
};

ResizeOutcome execute_resize_if_authorized(
    bool apply,
    bool title_managed,
    std::wstring_view image_path,
    const std::vector<std::wstring>& allowed_images,
    const std::function<MutationResult()>& mutate_if_identity_current);

std::wstring json_escape(std::wstring_view value);
} // namespace zchat::adapter
