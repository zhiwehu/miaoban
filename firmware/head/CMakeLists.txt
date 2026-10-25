# For more information about build system see
# https://docs.espressif.com/projects/esp-idf/en/latest/api-guides/build-system.html
# The following five lines of boilerplate have to be in your project's
# CMakeLists in this exact order for cmake to work correctly
cmake_minimum_required(VERSION 3.16)

set(PROJECT_VER "2.0.5")

# Add this line to disable the specific warning
# 这个 fork 里有多处 ESP_LOG 的格式串类型不严格（%x 配 long / %d 配 __u32），
# 在新版 GCC(14.2) + -Werror=all 下会直接编不过。这些只是日志格式化，
# 不影响功能，所以把 format 这一类降级为普通警告。
add_compile_options(-Wno-missing-field-initializers -Wno-error=format -Wno-error=format-security)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(xiaozhi)
if(NOT CONFIG_BOARD_TYPE_ECHOEAR)
    message(FATAL_ERROR "This Miaoban source release supports EchoEar only")
endif()

