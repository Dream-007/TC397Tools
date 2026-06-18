#include "value_codec.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <cstdio>
#include <stdexcept>

namespace tasgui {

namespace {

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

bool is_float_type(const std::string& type_name, size_t size, bool& is_double) {
    std::string t = lower(type_name);
    if (t == "double" && size == 8) { is_double = true; return true; }
    if (t == "float" && size == 4) { is_double = false; return true; }
    return false;
}

uint64_t bytes_to_uint(const std::vector<uint8_t>& data) {
    uint64_t v = 0;
    for (size_t i = 0; i < data.size() && i < 8; ++i) {
        v |= uint64_t(data[i]) << (8 * i);
    }
    return v;
}

std::string trim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}

} // namespace

std::string to_hex(const std::vector<uint8_t>& data) {
    std::string out;
    out.reserve(data.size() * 3);
    char buf[4];
    for (size_t i = 0; i < data.size(); ++i) {
        std::snprintf(buf, sizeof(buf), "%02x", data[i]);
        if (i) out += ' ';
        out += buf;
    }
    return out;
}

std::string decode_value(const std::vector<uint8_t>& data,
                         const std::string& type_name,
                         int is_signed) {
    if (data.empty()) return "";
    bool is_double = false;
    if (is_float_type(type_name, data.size(), is_double)) {
        char buf[64];
        if (is_double) {
            double d;
            std::memcpy(&d, data.data(), 8);
            std::snprintf(buf, sizeof(buf), "%.10g", d);
        } else {
            float f;
            std::memcpy(&f, data.data(), 4);
            std::snprintf(buf, sizeof(buf), "%.7g", static_cast<double>(f));
        }
        return buf;
    }

    uint64_t u = bytes_to_uint(data);
    char buf[64];
    if (is_signed == 1 && data.size() <= 8) {
        // sign extend
        int64_t s = static_cast<int64_t>(u);
        size_t bits = data.size() * 8;
        if (bits < 64) {
            uint64_t sign_bit = uint64_t(1) << (bits - 1);
            if (u & sign_bit) {
                s = static_cast<int64_t>(u | (~uint64_t(0) << bits));
            }
        }
        std::snprintf(buf, sizeof(buf), "%lld", static_cast<long long>(s));
    } else {
        std::snprintf(buf, sizeof(buf), "%llu", static_cast<unsigned long long>(u));
    }
    return buf;
}

static std::vector<uint8_t> parse_hex_blob(const std::string& in, size_t byte_count) {
    std::string raw;
    for (char c : in) {
        if (c == ' ' || c == '_') continue;
        raw += c;
    }
    if (raw.size() >= 2 && raw[0] == '0' && (raw[1] == 'x' || raw[1] == 'X')) {
        raw = raw.substr(2);
    }
    if (raw.size() % 2) raw = "0" + raw;
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i + 1 < raw.size(); i += 2) {
        auto hexval = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        int hi = hexval(raw[i]), lo = hexval(raw[i + 1]);
        if (hi < 0 || lo < 0) throw std::runtime_error("invalid hex byte");
        bytes.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    if (bytes.size() != byte_count) {
        throw std::runtime_error("hex blob has " + std::to_string(bytes.size()) +
                                 " byte(s), expected " + std::to_string(byte_count));
    }
    return bytes;
}

std::vector<uint8_t> encode_value(const std::string& text_in,
                                  const std::string& type_name,
                                  int is_signed,
                                  size_t byte_count) {
    std::string text = trim(text_in);
    if (text.empty()) throw std::runtime_error("empty value");
    if (byte_count == 0 || byte_count > 8) {
        // fall back to raw hex blob handling for unusual sizes
        return parse_hex_blob(text, byte_count);
    }

    bool is_double = false;
    if (is_float_type(type_name, byte_count, is_double)) {
        std::vector<uint8_t> out(byte_count);
        if (is_double) {
            double d = std::stod(text);
            std::memcpy(out.data(), &d, 8);
        } else {
            float f = std::stof(text);
            std::memcpy(out.data(), &f, 4);
        }
        return out;
    }

    // Integer path. Detect raw multi-byte hex blob ("0x0102..") longer than the
    // scalar so it is treated as a byte sequence, otherwise parse as a number.
    bool looks_like_blob = false;
    {
        std::string r = text;
        if (r.rfind("0x", 0) == 0 || r.rfind("0X", 0) == 0) r = r.substr(2);
        r.erase(std::remove_if(r.begin(), r.end(),
                               [](char c) { return c == ' ' || c == '_'; }),
                r.end());
        if (r.size() > byte_count * 2) looks_like_blob = true;
    }
    if (looks_like_blob) return parse_hex_blob(text, byte_count);

    bool negative = !text.empty() && text[0] == '-';
    uint64_t magnitude = 0;
    int base = 10;
    std::string body = negative ? text.substr(1) : text;
    if (body.rfind("0x", 0) == 0 || body.rfind("0X", 0) == 0) {
        base = 16;
        body = body.substr(2);
    }
    magnitude = std::stoull(body, nullptr, base);
    uint64_t value = negative ? static_cast<uint64_t>(-static_cast<int64_t>(magnitude)) : magnitude;

    std::vector<uint8_t> out(byte_count);
    for (size_t i = 0; i < byte_count; ++i) {
        out[i] = static_cast<uint8_t>((value >> (8 * i)) & 0xFF);
    }
    return out;
}

} // namespace tasgui
