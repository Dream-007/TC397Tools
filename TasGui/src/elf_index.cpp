#include "elf_index.hpp"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>

extern "C" {
int tc397_elf_write_member_index(const char* elf_path, const char* json_path,
                                 int max_depth, const char* mcu_version,
                                 char* err, size_t err_size);
void* tc397_elf_open(const char* elf_path, char* err, size_t err_size);
void tc397_elf_close(void* handle);
int tc397_elf_resolve_handle(void* handle, const char* expression,
                             int include_zero_size, int include_notype,
                             char* out_json, size_t out_json_size,
                             char* err, size_t err_size);
}

namespace tasgui {

namespace {

bool file_exists(const std::string& p) {
    struct stat st;
    return ::stat(p.c_str(), &st) == 0;
}

time_t file_mtime(const std::string& p) {
    struct stat st;
    if (::stat(p.c_str(), &st) != 0) return 0;
    return st.st_mtime;
}

// Minimal JSON scanner sufficient for the resolver's generated output.
class JsonScan {
public:
    JsonScan(const char* begin, const char* end) : p_(begin), end_(end) {}

    void ws() {
        while (p_ < end_ && (*p_ == ' ' || *p_ == '\t' || *p_ == '\n' || *p_ == '\r')) ++p_;
    }
    bool eof() { ws(); return p_ >= end_; }
    char peek() { ws(); return p_ < end_ ? *p_ : '\0'; }
    void expect(char c) {
        ws();
        if (p_ >= end_ || *p_ != c) throw std::runtime_error("JSON: expected token");
        ++p_;
    }

    std::string str() {
        ws();
        if (p_ >= end_ || *p_ != '"') throw std::runtime_error("JSON: expected string");
        ++p_;
        std::string out;
        while (p_ < end_ && *p_ != '"') {
            char c = *p_++;
            if (c == '\\' && p_ < end_) {
                char e = *p_++;
                switch (e) {
                    case 'n': out += '\n'; break;
                    case 'r': out += '\r'; break;
                    case 't': out += '\t'; break;
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    case 'u': {
                        if (end_ - p_ >= 4) {
                            int v = 0;
                            for (int i = 0; i < 4; ++i) {
                                char h = *p_++;
                                v <<= 4;
                                if (h >= '0' && h <= '9') v |= h - '0';
                                else if (h >= 'a' && h <= 'f') v |= h - 'a' + 10;
                                else if (h >= 'A' && h <= 'F') v |= h - 'A' + 10;
                            }
                            if (v < 0x80) out += (char)v;
                        }
                        break;
                    }
                    default: out += e; break;
                }
            } else {
                out += c;
            }
        }
        if (p_ < end_) ++p_; // closing quote
        return out;
    }

    // Parse a number token, returning the unsigned integer value (index data has
    // no fractional values). Handles a leading '-' by returning 0 magnitude sign-less.
    uint64_t uint_() {
        ws();
        bool neg = false;
        if (p_ < end_ && *p_ == '-') { neg = true; ++p_; }
        uint64_t v = 0;
        while (p_ < end_ && *p_ >= '0' && *p_ <= '9') {
            v = v * 10 + (*p_ - '0');
            ++p_;
        }
        // tolerate fraction/exponent we don't need
        while (p_ < end_ && (*p_ == '.' || *p_ == 'e' || *p_ == 'E' || *p_ == '+' ||
                             *p_ == '-' || (*p_ >= '0' && *p_ <= '9'))) ++p_;
        (void)neg;
        return v;
    }

    // signed field: true|false|null -> 1/0/-1
    int signed_field() {
        ws();
        if (match("true")) return 1;
        if (match("false")) return 0;
        if (match("null")) return -1;
        skip_value();
        return -1;
    }

    void skip_value() {
        ws();
        if (p_ >= end_) return;
        char c = *p_;
        if (c == '"') { str(); return; }
        if (c == '{') { skip_container('{', '}'); return; }
        if (c == '[') { skip_container('[', ']'); return; }
        if (match("true") || match("false") || match("null")) return;
        // number
        while (p_ < end_ && (*p_ == '-' || *p_ == '+' || *p_ == '.' || *p_ == 'e' ||
                             *p_ == 'E' || (*p_ >= '0' && *p_ <= '9'))) ++p_;
    }

    const char* pos() const { return p_; }
    const char* end() const { return end_; }

private:
    bool match(const char* lit) {
        size_t n = std::strlen(lit);
        if ((size_t)(end_ - p_) < n) return false;
        if (std::strncmp(p_, lit, n) != 0) return false;
        p_ += n;
        return true;
    }
    void skip_container(char open, char close) {
        expect(open);
        if (peek() == close) { ++p_; return; }
        while (true) {
            skip_value();
            char c = peek();
            if (c == ',') { ++p_; continue; }
            if (c == close) { ++p_; break; }
            throw std::runtime_error("JSON: malformed container");
        }
    }

    const char* p_;
    const char* end_;
};

void parse_entry(JsonScan& js, std::vector<VarEntry>& out) {
    js.expect('{');
    VarEntry e;
    if (js.peek() != '}') {
        while (true) {
            std::string key = js.str();
            js.expect(':');
            if (key == "member_name") e.member_name = js.str();
            else if (key == "expression") e.expression = js.str();
            else if (key == "type_name") e.type_name = js.str();
            else if (key == "address") e.address = js.uint_();
            else if (key == "byte_size") e.byte_size = (uint32_t)js.uint_();
            else if (key == "signed") e.is_signed = js.signed_field();
            else js.skip_value();
            char c = js.peek();
            if (c == ',') { js.expect(','); continue; }
            break;
        }
    }
    js.expect('}');
    out.push_back(std::move(e));
}

} // namespace

ElfIndex::~ElfIndex() {
    if (handle_) tc397_elf_close(handle_);
}

bool ElfIndex::parse_index_json(const std::string& text) {
    entries_.clear();
    try {
        JsonScan js(text.data(), text.data() + text.size());
        js.expect('{');
        if (js.peek() == '}') { js.expect('}'); return true; }
        while (true) {
            std::string key = js.str();
            js.expect(':');
            if (key == "entries_by_member") {
                js.expect('{');
                if (js.peek() != '}') {
                    while (true) {
                        js.str();          // member key (redundant with member_name)
                        js.expect(':');
                        js.expect('[');
                        if (js.peek() != ']') {
                            while (true) {
                                parse_entry(js, entries_);
                                char c = js.peek();
                                if (c == ',') { js.expect(','); continue; }
                                break;
                            }
                        }
                        js.expect(']');
                        char c = js.peek();
                        if (c == ',') { js.expect(','); continue; }
                        break;
                    }
                }
                js.expect('}');
            } else {
                js.skip_value();
            }
            char c = js.peek();
            if (c == ',') { js.expect(','); continue; }
            break;
        }
        return true;
    } catch (const std::exception& exc) {
        error_ = std::string("failed to parse index JSON: ") + exc.what();
        return false;
    }
}

bool ElfIndex::load(const std::string& elf_path, const std::string& json_path,
                    int max_depth, bool force_regen) {
    error_.clear();
    loaded_ = false;
    elf_path_ = elf_path;

    bool need_regen = force_regen || !file_exists(json_path);
    if (!need_regen && file_exists(elf_path)) {
        if (file_mtime(json_path) < file_mtime(elf_path)) need_regen = true;
    }

    if (need_regen) {
        if (!file_exists(elf_path)) {
            error_ = "ELF file not found: " + elf_path;
            return false;
        }
        char err[4096] = {0};
        int rc = tc397_elf_write_member_index(elf_path.c_str(), json_path.c_str(),
                                              max_depth, "", err, sizeof(err));
        if (rc != 0) {
            error_ = std::string("index generation failed: ") + err;
            return false;
        }
    }

    std::ifstream f(json_path, std::ios::binary);
    if (!f) {
        error_ = "cannot open index JSON: " + json_path;
        return false;
    }
    std::stringstream ss;
    ss << f.rdbuf();
    std::string text = ss.str();
    if (!parse_index_json(text)) return false;

    if (file_exists(elf_path)) {
        if (handle_) { tc397_elf_close(handle_); handle_ = nullptr; }
        char err[4096] = {0};
        handle_ = tc397_elf_open(elf_path.c_str(), err, sizeof(err));
        // handle is optional; live resolve simply degrades if unavailable.
    }

    loaded_ = true;
    return true;
}

VarRef ElfIndex::resolve(const std::string& expression) {
    VarRef ref;
    ref.expression = expression;
    if (!handle_) {
        ref.error = "no resolver handle (ELF not loaded)";
        return ref;
    }
    std::vector<char> out(8192);
    char err[4096] = {0};
    int rc = tc397_elf_resolve_handle(handle_, expression.c_str(), 0, 0,
                                      out.data(), out.size(), err, sizeof(err));
    if (rc != 0) {
        ref.error = err[0] ? err : "resolve failed";
        return ref;
    }
    try {
        std::string text(out.data());
        JsonScan js(text.data(), text.data() + text.size());
        js.expect('{');
        if (js.peek() != '}') {
            while (true) {
                std::string key = js.str();
                js.expect(':');
                if (key == "expression") ref.expression = js.str();
                else if (key == "type_name") ref.type_name = js.str();
                else if (key == "address") ref.address = js.uint_();
                else if (key == "byte_size") ref.byte_size = (uint32_t)js.uint_();
                else if (key == "signed") ref.is_signed = js.signed_field();
                else js.skip_value();
                char c = js.peek();
                if (c == ',') { js.expect(','); continue; }
                break;
            }
        }
        js.expect('}');
        ref.ok = true;
    } catch (const std::exception& exc) {
        ref.error = std::string("parse resolve result: ") + exc.what();
    }
    return ref;
}

} // namespace tasgui
