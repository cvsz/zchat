#include "zchat_adapter/policy.h"
#include <cstdlib>
#include <iostream>

int main() {
    using namespace zchat::adapter;
    int failures = 0;
    const auto check = [&](bool condition, const char* name) {
        if (!condition) { std::cerr << "FAIL: " << name << "\n"; ++failures; }
    };
    check(is_managed_window_title(L"ZChat Status Changer"), "accept branded status GUI");
    check(is_managed_window_title(L"ZChat-Settings"), "accept branded hyphen title");
    check(!is_managed_window_title(L"Camfrog Video Chat"), "reject Camfrog client");
    check(!is_managed_window_title(L"LINE Status Changer"), "reject unrelated app");
    check(!is_managed_window_title(L"Not ZChat Status Changer"), "reject embedded prefix");
    const std::vector<std::wstring> allowed_images{L"C:\\Program Files\\ZChat\\zchat.exe"};
    check(is_allowed_process_image(L"c:/program files/zchat/ZChat.exe", allowed_images),
          "accept case-insensitive exact process image path");
    check(!is_allowed_process_image(L"C:\\Other\\zchat.exe", allowed_images),
          "reject a different process with the same executable name");
    check(!is_allowed_process_image(L"", allowed_images), "reject unreadable process image");
    check(!is_allowed_process_image(L"C:\\Program Files\\ZChat\\zchat.exe", {}),
          "reject when process allowlist is empty");
    check(is_managed_window_title(L"ZChat Spoofed") &&
          !is_allowed_process_image(L"C:\\Other\\spoof.exe", allowed_images),
          "title prefix alone does not authorize a process");

    int resize_calls = 0;
    const auto successful_resize = [&]() {
        ++resize_calls;
        return true;
    };
    check(execute_resize_if_authorized(false, true,
              L"C:\\Program Files\\ZChat\\zchat.exe", allowed_images,
              successful_resize) == ResizeOutcome::dry_run,
          "dry-run returns without applying resize");
    check(resize_calls == 0, "dry-run never calls resize callback");
    check(execute_resize_if_authorized(true, true, L"C:\\Other\\zchat.exe",
              allowed_images, successful_resize) == ResizeOutcome::process_image_not_allowed,
          "unallowlisted process is denied at mutation boundary");
    check(resize_calls == 0, "unallowlisted process never calls resize callback");
    check(execute_resize_if_authorized(true, false,
              L"C:\\Program Files\\ZChat\\zchat.exe", allowed_images,
              successful_resize) == ResizeOutcome::title_not_managed,
          "unmanaged title is denied at mutation boundary");
    check(resize_calls == 0, "unmanaged title never calls resize callback");
    check(execute_resize_if_authorized(true, true,
              L"C:\\Program Files\\ZChat\\zchat.exe", allowed_images,
              successful_resize) == ResizeOutcome::resized,
          "allowlisted apply mode invokes resize callback");
    check(resize_calls == 1, "authorized apply calls resize exactly once");
    check(execute_resize_if_authorized(true, true,
              L"C:\\Program Files\\ZChat\\zchat.exe", allowed_images,
              []() { return false; }) == ResizeOutcome::resize_failed,
          "failed Win32 resize is reported as failure");
    check(is_valid_size(kCompactDefault), "accept compact default");
    check(is_valid_size({320, 240}), "accept lower bound");
    check(!is_valid_size({319, 480}), "reject too narrow");
    check(!is_valid_size({360, 239}), "reject too short");
    check(!is_valid_size({4097, 480}), "reject excessive width");
    check(json_escape(L"quote\" slash\\ newline\n") == L"quote\\\" slash\\\\ newline\\n",
          "escape JSON strings");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
