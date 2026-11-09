#pragma once

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string_view>
#include <vector>

namespace voice {

constexpr int sample_rate = 16'000;
enum class PcmError { none, invalid, oversized };

constexpr std::size_t max_audio_bytes = 30 * sample_rate * sizeof(int16_t);
constexpr std::size_t max_line_bytes = (max_audio_bytes + 2) / 3 * 4 + 81;

inline bool valid_id(std::string_view id) {
    return !id.empty() && id.size() <= 80 && std::all_of(id.begin(), id.end(), [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
               (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == ':';
    });
}

inline int base64_value(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

inline bool decode_pcm(std::string_view encoded, std::vector<float> &samples) {
    if (encoded.empty() || encoded.size() % 4 != 0) return false;

    samples.clear();
    samples.reserve(encoded.size() * 3 / 8);
    int low_byte = -1;
    auto append_byte = [&](int byte) {
        if (low_byte < 0) {
            low_byte = byte;
        } else {
            const int bits = low_byte | (byte << 8);
            const int signed_value = bits >= 32768 ? bits - 65536 : bits;
            samples.push_back(signed_value / 32768.0f);
            low_byte = -1;
        }
    };

    for (std::size_t i = 0; i < encoded.size(); i += 4) {
        const int a = base64_value(encoded[i]);
        const int b = base64_value(encoded[i + 1]);
        const int c = base64_value(encoded[i + 2]);
        const int d = base64_value(encoded[i + 3]);
        if (a < 0 || b < 0) return false;
        append_byte((a << 2) | (b >> 4));

        const bool last = i + 4 == encoded.size();
        if (c < 0) {
            if (!last || encoded.substr(i + 2, 2) != "==" || (b & 15) != 0) return false;
            break;
        }
        append_byte(((b & 15) << 4) | (c >> 2));
        if (d < 0) {
            if (!last || encoded[i + 3] != '=' || (c & 3) != 0) return false;
            break;
        }
        append_byte(((c & 3) << 6) | d);
    }
    return low_byte < 0 && !samples.empty() && samples.size() * sizeof(int16_t) <= max_audio_bytes;
}

template <class Handler>
void read_pcm_requests(Handler handle) {
    std::vector<char> buffer(max_line_bytes + 1);
    std::vector<float> samples;
    while (std::cin.getline(buffer.data(), static_cast<std::streamsize>(buffer.size())) || !std::cin.eof()) {
        const bool oversized = std::cin.fail();
        const auto count = static_cast<std::size_t>(std::cin.gcount());
        const auto length = oversized || std::cin.eof() ? count : count - 1;
        const std::string_view line(buffer.data(), length);
        const auto separator = line.find('\t');
        const auto id = line.substr(0, separator);
        if (oversized) {
            if (std::cin.bad()) break;
            std::cin.clear();
            std::cin.ignore(std::numeric_limits<std::streamsize>::max(), '\n');
            if (valid_id(id)) handle(id, samples, PcmError::oversized);
            continue;
        }
        if (!valid_id(id)) continue;
        if (separator == std::string_view::npos || !decode_pcm(line.substr(separator + 1), samples)) {
            handle(id, samples, PcmError::invalid);
            continue;
        }
        handle(id, samples, PcmError::none);
    }
}

} // namespace voice
