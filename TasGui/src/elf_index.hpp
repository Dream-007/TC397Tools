#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tasgui {

struct VarEntry {
    std::string member_name;
    std::string expression;
    std::string type_name;
    uint64_t address = 0;
    uint32_t byte_size = 0;
    int is_signed = -1; // -1 unknown, 0 no, 1 yes
};

// One resolved reference (from a live expression lookup).
struct VarRef {
    std::string expression;
    std::string type_name;
    uint64_t address = 0;
    uint32_t byte_size = 0;
    int is_signed = -1;
    bool ok = false;
    std::string error;
};

class ElfIndex {
public:
    ElfIndex() = default;
    ~ElfIndex();

    ElfIndex(const ElfIndex&) = delete;
    ElfIndex& operator=(const ElfIndex&) = delete;

    // Generate the member-index JSON (if needed) from the ELF and load it into
    // memory, and open a resolver handle for live expression lookups.
    // Returns false on failure (see last_error()).
    bool load(const std::string& elf_path, const std::string& json_path,
              int max_depth = 8, bool force_regen = false);

    const std::vector<VarEntry>& entries() const { return entries_; }
    bool loaded() const { return loaded_; }
    const std::string& last_error() const { return error_; }
    const std::string& elf_path() const { return elf_path_; }

    // Resolve a full path / leaf member / array index expression live via DWARF.
    VarRef resolve(const std::string& expression);

private:
    bool parse_index_json(const std::string& text);

    std::vector<VarEntry> entries_;
    void* handle_ = nullptr;   // resolver handle
    bool loaded_ = false;
    std::string error_;
    std::string elf_path_;
};

} // namespace tasgui
