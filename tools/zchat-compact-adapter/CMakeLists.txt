cmake_minimum_required(VERSION 3.20)
project(zchat_compact_adapter VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(zchat_adapter_policy src/policy.cpp)
target_include_directories(zchat_adapter_policy PUBLIC include)

if(WIN32)
  add_executable(zchat-compact-adapter src/main.cpp)
  target_link_libraries(zchat-compact-adapter PRIVATE zchat_adapter_policy
    ole32 oleaut32 uiautomationcore version)
  target_compile_definitions(zchat-compact-adapter PRIVATE UNICODE _UNICODE
    WIN32_LEAN_AND_MEAN NOMINMAX)
endif()

include(CTest)
if(BUILD_TESTING)
  add_executable(zchat-adapter-policy-tests tests/policy_tests.cpp)
  target_link_libraries(zchat-adapter-policy-tests PRIVATE zchat_adapter_policy)
  add_test(NAME zchat-adapter-policy-tests COMMAND zchat-adapter-policy-tests)
endif()
