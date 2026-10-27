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
    check(is_valid_size(kCompactDefault), "accept compact default");
    check(is_valid_size({320, 240}), "accept lower bound");
    check(!is_valid_size({319, 480}), "reject too narrow");
    check(!is_valid_size({360, 239}), "reject too short");
    check(!is_valid_size({4097, 480}), "reject excessive width");
    check(json_escape(L"quote\" slash\\ newline\n") == L"quote\\\" slash\\\\ newline\\n",
          "escape JSON strings");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
