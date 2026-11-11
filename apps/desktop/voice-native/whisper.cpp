#include <algorithm>
#include <cstdio>
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

// Silero VAD in streaming mode: a voice probability for every 32 ms of microphone audio, so
// the desktop can tell speech from noise. One process, separate from transcription, so it
// never waits behind a decode.
constexpr int vad_window = 512; // samples per probability at 16 kHz (Silero v5 and v6)
constexpr std::size_t vad_max_line = 16'000 * 2 / 3 * 4 + 200; // one second of PCM16, base64

int run_vad(const char *model) {
    auto context_params = whisper_vad_default_context_params();
    context_params.n_threads = 1;
    context_params.use_gpu = false;
    const std::unique_ptr<whisper_vad_context, decltype(&whisper_vad_free)> vad(
        whisper_vad_init_from_file_with_params(model, context_params), whisper_vad_free);
    if (!vad) {
        std::cerr << "Could not load the VAD model.\n";
        return 1;
    }
    // The window comes from the model file; check it instead of trusting the constant.
    std::vector<float> probe(vad_window * 2, 0.0f);
    if (!whisper_vad_detect_speech(vad.get(), probe.data(), static_cast<int>(probe.size())) ||
        whisper_vad_n_probs(vad.get()) != 2) {
        std::cerr << "Unexpected VAD model window.\n";
        return 1;
    }
    whisper_vad_reset_state(vad.get());

    std::cout << "ready" << std::endl;
    std::vector<float> pending;
    std::vector<float> samples;
    std::string line;
    while (std::getline(std::cin, line)) {
        const auto separator = line.find('\t');
        const std::string_view id = std::string_view(line).substr(0, separator);
        if (!voice::valid_id(id)) continue;
        if (separator == std::string::npos || line.size() > vad_max_line) {
            reply(id, "error", "Expected up to one second of 16 kHz mono PCM audio.");
            continue;
        }
        const std::string_view payload = std::string_view(line).substr(separator + 1);
        if (payload == "reset") {
            whisper_vad_reset_state(vad.get());
            pending.clear();
            reply(id, "result", "");
            continue;
        }
        if (!voice::decode_pcm(payload, samples)) {
            reply(id, "error", "Expected up to one second of 16 kHz mono PCM audio.");
            continue;
        }
        pending.insert(pending.end(), samples.begin(), samples.end());
        const int whole = static_cast<int>(pending.size() / vad_window) * vad_window;
        std::string probs;
        if (whole > 0) {
            if (!whisper_vad_detect_speech_no_reset(vad.get(), pending.data(), whole)) {
                reply(id, "error", "Could not detect speech.");
                continue;
            }
            const float *values = whisper_vad_probs(vad.get());
            const int count = whisper_vad_n_probs(vad.get());
            char number[16];
            for (int i = 0; i < count; ++i) {
                std::snprintf(number, sizeof number, "%s%.3f", i ? "," : "", values[i]);
                probs += number;
            }
            pending.erase(pending.begin(), pending.begin() + whole);
        }
        reply(id, "result", probs);
    }
    return 0;
}

} // namespace

int main(int argc, char **argv) {
    if (argc == 3 && std::string_view(argv[1]) == "--vad" && argv[2][0] == '/') {
        std::ios::sync_with_stdio(false);
        return run_vad(argv[2]);
    }
    // After the model: --cpu, and --prompt with the project's own names (file and symbol
    // names the host read from the index), added to the coding glossary below.
    bool use_gpu = true;
    std::string project_words;
    bool usage = argc < 3 || std::string_view(argv[1]) != "--model" || argv[2][0] != '/';
    for (int i = 3; !usage && i < argc; ++i) {
        const std::string_view arg(argv[i]);
        if (arg == "--cpu" && use_gpu) {
            use_gpu = false;
        } else if (arg == "--prompt" && i + 1 < argc && project_words.empty()) {
            project_words = argv[++i];
        } else {
            usage = true;
        }
    }
    if (usage) {
        std::cerr << "Usage: crucible-whisper --model /absolute/model.bin [--cpu] [--prompt words]\n"
                     "       crucible-whisper --vad /absolute/silero-model.bin\n";
        return 2;
    }

    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    auto context_params = whisper_context_default_params();
    context_params.use_gpu = use_gpu;
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
    // Coding words Whisper otherwise hears as everyday ones ("repo" came back as "report",
    // "ripple" and "rapport" in live tests). A short glossary, not a sentence, so it biases
    // spelling without inviting Whisper to repeat it on near-silence.
    // Whisper conditions on at most half its text context of prompt tokens and keeps the
    // LAST ones, so an over-long prompt would lose the fixed glossary. Project words go
    // first (most referenced first, as the host sends them), the glossary last, and the
    // least-referenced names are dropped until the whole prompt fits.
    const std::string fixed_glossary = "Glossary: repo, repository, commit, branch, pull request, PR, merge, "
                                       "npm, TypeScript, Python, API, Crucible.";
    std::replace_if(project_words.begin(), project_words.end(),
                    [](unsigned char c) { return c < 0x20; }, ' ');
    std::vector<std::string> names;
    for (std::size_t start = 0; start < project_words.size() && names.size() < 400;) {
        const auto comma = project_words.find(',', start);
        const auto end = comma == std::string::npos ? project_words.size() : comma;
        auto name = project_words.substr(start, end - start);
        name.erase(0, name.find_first_not_of(' '));
        name.erase(name.find_last_not_of(' ') + 1);
        if (!name.empty()) names.push_back(name);
        start = end + 1;
    }
    const auto prompt_with = [&](std::size_t kept) {
        std::string text;
        for (std::size_t i = 0; i < kept; ++i) text += (i ? ", " : "") + names[i];
        return kept ? "Project: " + text + ". " + fixed_glossary : fixed_glossary;
    };
    const int prompt_budget = whisper_n_text_ctx(context.get()) / 2 - 2;
    std::vector<whisper_token> scratch(1024);
    std::size_t kept = names.size();
    std::string glossary = prompt_with(kept);
    // Negative: more tokens than the scratch buffer holds, so over budget too.
    const auto over_budget = [&](const std::string &text) {
        const int n = whisper_tokenize(context.get(), text.c_str(), scratch.data(),
                                       static_cast<int>(scratch.size()));
        return n < 0 || n > prompt_budget;
    };
    while (kept > 0 && over_budget(glossary)) glossary = prompt_with(--kept);
    params.initial_prompt = glossary.c_str();
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
