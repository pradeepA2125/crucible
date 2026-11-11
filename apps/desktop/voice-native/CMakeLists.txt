cmake_minimum_required(VERSION 3.20)
project(CrucibleWhisper LANGUAGES C CXX ASM)

include(FetchContent)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_METAL_EMBED_LIBRARY ON CACHE BOOL "" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_IS_DEV OFF CACHE BOOL "" FORCE)
# Windows and Linux x64: one binary with an explicit AVX2 baseline (`--check-cpu` reports a
# processor without it). Apple Silicon builds are unaffected.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")
    set(GGML_AVX ON CACHE BOOL "" FORCE)
    set(GGML_AVX2 ON CACHE BOOL "" FORCE)
    set(GGML_FMA ON CACHE BOOL "" FORCE)
    set(GGML_F16C ON CACHE BOOL "" FORCE)
endif()
# No Visual C++ redistributable needed on Windows: the static CRT, and ggml's own thread pool
# instead of OpenMP (MSVC's OpenMP needs vcomp140.dll; GCC's needs libgomp on Linux).
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded" CACHE STRING "" FORCE)
if(NOT APPLE)
    set(GGML_OPENMP OFF CACHE BOOL "" FORCE)
endif()

FetchContent_Declare(whisper
    URL https://codeload.github.com/ggml-org/whisper.cpp/legacy.tar.gz/refs/tags/v1.9.5
    URL_HASH SHA256=469e96407d4a5f5a72288160f3b5bf0ec69f218b09d68cee2d552a27b525a469
)
FetchContent_MakeAvailable(whisper)

add_executable(crucible-whisper whisper.cpp)
target_compile_features(crucible-whisper PRIVATE cxx_std_17)
target_link_libraries(crucible-whisper PRIVATE whisper)
# Linux: run on distributions older than the build machine (only glibc stays dynamic).
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_link_options(crucible-whisper PRIVATE -static-libstdc++ -static-libgcc)
endif()
install(TARGETS crucible-whisper RUNTIME DESTINATION . COMPONENT VoiceWorker)
install(FILES "${whisper_SOURCE_DIR}/LICENSE" DESTINATION . RENAME LICENSE.whisper.cpp COMPONENT VoiceWorker)
