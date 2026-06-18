cmake_minimum_required(VERSION 3.16)
project(TasGui CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Repo root (parent of TasGui) holds the unchanged ELF resolver + ELFIO.
get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(DAS_HOME "/opt/Tools/DAS/8.3.0" CACHE PATH "Infineon DAS/TAS install dir")

# ---- Dear ImGui (fetched) ------------------------------------------------
include(FetchContent)
FetchContent_Declare(
  imgui
  GIT_REPOSITORY https://github.com/ocornut/imgui.git
  GIT_TAG        v1.91.5
  GIT_SHALLOW    TRUE
)
FetchContent_MakeAvailable(imgui)

find_package(glfw3 REQUIRED)
find_package(OpenGL REQUIRED)
find_package(Threads REQUIRED)

add_library(imgui STATIC
  ${imgui_SOURCE_DIR}/imgui.cpp
  ${imgui_SOURCE_DIR}/imgui_draw.cpp
  ${imgui_SOURCE_DIR}/imgui_tables.cpp
  ${imgui_SOURCE_DIR}/imgui_widgets.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_glfw.cpp
  ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp
)
target_include_directories(imgui PUBLIC
  ${imgui_SOURCE_DIR}
  ${imgui_SOURCE_DIR}/backends
)
target_link_libraries(imgui PUBLIC glfw OpenGL::GL)

# ---- TC397 ELF/DWARF resolver (compiled from existing, unmodified source) -
add_library(tc397resolver STATIC ${REPO_ROOT}/cpp/tc397_elfio_resolver.cpp)
target_include_directories(tc397resolver PRIVATE ${REPO_ROOT}/ELFIO)
set_target_properties(tc397resolver PROPERTIES POSITION_INDEPENDENT_CODE ON)

# ---- DAS/TAS imported library --------------------------------------------
add_library(das_api SHARED IMPORTED)
set_target_properties(das_api PROPERTIES
  IMPORTED_LOCATION "${DAS_HOME}/lib/libdas_api.so"
  INTERFACE_INCLUDE_DIRECTORIES "${DAS_HOME}/include"
)

# ---- Application ----------------------------------------------------------
add_executable(tasgui
  src/main.cpp
  src/das_client.cpp
  src/elf_index.cpp
  src/value_codec.cpp
)
target_link_libraries(tasgui PRIVATE
  imgui
  tc397resolver
  das_api
  Threads::Threads
  ${CMAKE_DL_LIBS}
)
set_target_properties(tasgui PROPERTIES
  BUILD_RPATH "${DAS_HOME}/lib"
  INSTALL_RPATH "${DAS_HOME}/lib"
)
