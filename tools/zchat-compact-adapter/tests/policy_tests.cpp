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
    check(is_valid_size(kCompactDefault), "accept compact default");
    check(is_valid_size({320, 240}), "accept lower bound");
    check(!is_valid_size({319, 480}), "reject too narrow");
    check(!is_valid_size({360, 239}), "reject too short");
    check(!is_valid_size({4097, 480}), "reject excessive width");
    check(json_escape(L"quote\" slash\\ newline\n") == L"quote\\\" slash\\\\ newline\\n",
          "escape JSON strings");
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
