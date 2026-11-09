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

FetchContent_Declare(whisper
    URL https://codeload.github.com/ggml-org/whisper.cpp/legacy.tar.gz/refs/tags/v1.9.5
    URL_HASH SHA256=469e96407d4a5f5a72288160f3b5bf0ec69f218b09d68cee2d552a27b525a469
)
FetchContent_MakeAvailable(whisper)

add_executable(crucible-whisper whisper.cpp)
target_compile_features(crucible-whisper PRIVATE cxx_std_17)
target_link_libraries(crucible-whisper PRIVATE whisper)
install(TARGETS crucible-whisper RUNTIME DESTINATION . COMPONENT VoiceWorker)
install(FILES "${whisper_SOURCE_DIR}/LICENSE" DESTINATION . RENAME LICENSE.whisper.cpp COMPONENT VoiceWorker)
