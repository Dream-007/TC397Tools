#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tasgui {

// Decode raw little-endian bytes into a human readable value string,
// choosing int / uint / float / double based on type_name, signedness and size.
std::string decode_value(const std::vector<uint8_t>& data,
                         const std::string& type_name,
                         int is_signed /* -1 unknown, 0 no, 1 yes */);

// Hex dump of bytes, e.g. "01 a3 ff".
std::string to_hex(const std::vector<uint8_t>& data);

// Encode a user supplied text into exactly byte_count little-endian bytes.
// Accepts decimal/hex ("0x..") integers, floats ("1.5"), or raw hex blobs
// ("0x01a3" / "01 a3"). Throws std::runtime_error on failure.
std::vector<uint8_t> encode_value(const std::string& text,
                                  const std::string& type_name,
                                  int is_signed,
                                  size_t byte_count);

} // namespace tasgui
