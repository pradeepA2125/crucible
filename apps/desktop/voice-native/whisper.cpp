#include <algorithm>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "pcm-protocol.hpp"
#include "whisper.h"

namespace {

static_assert(voice::sample_rate == WHISPER_SAMPLE_RATE);

std::string transcript(whisper_context *context) {
    std::string result;
    for (int i = 0; i < whisper_full_n_segments(context); ++i) {
        if (const char *segment = whisper_full_get_segment_text(context, i)) result += segment;
    }
    for (auto &c : result) {
        const auto byte = static_cast<unsigned char>(c);
        if (byte < 0x20 || byte == 0x7f) c = ' ';
    }
    const auto start = result.find_first_not_of(' ');
    if (start == std::string::npos) return {};
    return result.substr(start, result.find_last_not_of(' ') - start + 1);
}

void reply(std::string_view id, std::string_view status, std::string_view value) {
    std::cout << id << '\t' << status << '\t' << value << std::endl;
}

} // namespace

int main(int argc, char **argv) {
    if (argc < 3 || argc > 4 || std::string_view(argv[1]) != "--model" ||
        argv[2][0] != '/' || (argc == 4 && std::string_view(argv[3]) != "--cpu")) {
        std::cerr << "Usage: crucible-whisper --model /absolute/model.bin [--cpu]\n";
        return 2;
    }

    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    auto context_params = whisper_context_default_params();
    context_params.use_gpu = argc != 4;
    const std::unique_ptr<whisper_context, decltype(&whisper_free)> context(
        whisper_init_from_file_with_params(argv[2], context_params), whisper_free);
    if (!context) {
        std::cerr << "Could not load the Whisper model.\n";
        return 1;
    }

    auto params = whisper_full_default_params(WHISPER_SAMPLING_BEAM_SEARCH);
    params.n_threads = static_cast<int>(std::clamp(std::thread::hardware_concurrency(), 1U, 4U));
    params.language = "en";
    params.no_context = true;
    params.no_timestamps = true;
    params.print_special = false;
    params.print_progress = false;
    params.print_realtime = false;
    params.print_timestamps = false;

    // Warm the encoder and decoder while the UI is still showing startup.
    std::vector<float> samples(WHISPER_SAMPLE_RATE, 0.0f);
    if (whisper_full(context.get(), params, samples.data(), static_cast<int>(samples.size())) != 0) {
        std::cerr << "Could not warm the Whisper model.\n";
        return 1;
    }

    std::cout << "ready" << std::endl;
    voice::read_pcm_requests([&](std::string_view id, const std::vector<float> &pcm, voice::PcmError error) {
        if (error == voice::PcmError::oversized) {
            reply(id, "error", "Audio request is too large.");
        } else if (error == voice::PcmError::invalid) {
            reply(id, "error", "Expected up to 30 seconds of 16 kHz mono PCM audio.");
        } else if (whisper_full(context.get(), params, pcm.data(), static_cast<int>(pcm.size())) != 0) {
            reply(id, "error", "Could not transcribe audio.");
        } else {
            reply(id, "result", transcript(context.get()));
        }
    });
    return 0;
}
