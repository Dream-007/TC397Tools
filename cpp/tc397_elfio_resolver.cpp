#include <elfio/elfio.hpp>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fstream>
#include <iomanip>
#include <memory>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

using namespace ELFIO;

namespace {

constexpr uint64_t DW_TAG_array_type = 0x01;
constexpr uint64_t DW_TAG_class_type = 0x02;
constexpr uint64_t DW_TAG_compile_unit = 0x11;
constexpr uint64_t DW_TAG_member = 0x0d;
constexpr uint64_t DW_TAG_pointer_type = 0x0f;
constexpr uint64_t DW_TAG_structure_type = 0x13;
constexpr uint64_t DW_TAG_typedef = 0x16;
constexpr uint64_t DW_TAG_union_type = 0x17;
constexpr uint64_t DW_TAG_base_type = 0x24;
constexpr uint64_t DW_TAG_const_type = 0x26;
constexpr uint64_t DW_TAG_variable = 0x34;
constexpr uint64_t DW_TAG_volatile_type = 0x35;
constexpr uint64_t DW_TAG_restrict_type = 0x37;

constexpr uint64_t DW_AT_location = 0x02;
constexpr uint64_t DW_AT_name = 0x03;
constexpr uint64_t DW_AT_byte_size = 0x0b;
constexpr uint64_t DW_AT_data_member_location = 0x38;
constexpr uint64_t DW_AT_encoding = 0x3e;
constexpr uint64_t DW_AT_type = 0x49;

constexpr uint64_t DW_FORM_addr = 0x01;
constexpr uint64_t DW_FORM_block2 = 0x03;
constexpr uint64_t DW_FORM_block4 = 0x04;
constexpr uint64_t DW_FORM_data2 = 0x05;
constexpr uint64_t DW_FORM_data4 = 0x06;
constexpr uint64_t DW_FORM_data8 = 0x07;
constexpr uint64_t DW_FORM_string = 0x08;
constexpr uint64_t DW_FORM_block = 0x09;
constexpr uint64_t DW_FORM_block1 = 0x0a;
constexpr uint64_t DW_FORM_data1 = 0x0b;
constexpr uint64_t DW_FORM_flag = 0x0c;
constexpr uint64_t DW_FORM_sdata = 0x0d;
constexpr uint64_t DW_FORM_strp = 0x0e;
constexpr uint64_t DW_FORM_udata = 0x0f;
constexpr uint64_t DW_FORM_ref_addr = 0x10;
constexpr uint64_t DW_FORM_ref1 = 0x11;
constexpr uint64_t DW_FORM_ref2 = 0x12;
constexpr uint64_t DW_FORM_ref4 = 0x13;
constexpr uint64_t DW_FORM_ref8 = 0x14;
constexpr uint64_t DW_FORM_ref_udata = 0x15;
constexpr uint64_t DW_FORM_indirect = 0x16;
constexpr uint64_t DW_FORM_sec_offset = 0x17;
constexpr uint64_t DW_FORM_exprloc = 0x18;
constexpr uint64_t DW_FORM_flag_present = 0x19;
constexpr uint64_t DW_FORM_ref_sig8 = 0x20;

struct VariableRef {
    std::string expression;
    std::string base_name;
    uint64_t address = 0;
    uint64_t byte_size = 0;
    uint64_t variable_size = 0;
    bool indexed = false;
    bool signed_known = false;
    bool is_signed = false;
    std::string source;
    std::string symbol_type;
    std::string binding;
    std::string section_name;
    std::string type_name;
};

struct MemberPath {
    std::string member_name;
    std::string expression;
    std::string base_name;
    uint64_t address = 0;
    uint64_t byte_offset = 0;
    uint64_t byte_size = 0;
    bool signed_known = false;
    bool is_signed = false;
    std::string type_name;
};

struct ParsedExpression {
    std::string base_expression;
    std::string root_name;
    std::vector<std::string> fields;
    std::optional<uint64_t> index;
};

struct AttrSpec {
    uint64_t name = 0;
    uint64_t form = 0;
};

struct Abbrev {
    uint64_t tag = 0;
    bool has_children = false;
    std::vector<AttrSpec> attrs;
};

struct AttrValue {
    uint64_t form = 0;
    uint64_t u = 0;
    int64_t s = 0;
    std::string str;
    std::vector<uint8_t> block;
};

struct Die {
    uint64_t offset = 0;
    uint64_t cu_start = 0;
    uint64_t tag = 0;
    bool has_children = false;
    std::unordered_map<uint64_t, AttrValue> attrs;
    std::vector<uint64_t> children;
};

struct CuHeader {
    uint64_t start = 0;
    uint64_t end = 0;
    uint64_t abbrev_offset = 0;
    uint8_t address_size = 4;
    bool parsed = false;
};

uint16_t read_u16(const std::vector<uint8_t>& data, size_t off) {
    if (off + 2 > data.size()) {
        throw std::runtime_error("unexpected end of section");
    }
    return uint16_t(data[off]) | (uint16_t(data[off + 1]) << 8);
}

uint32_t read_u32(const std::vector<uint8_t>& data, size_t off) {
    if (off + 4 > data.size()) {
        throw std::runtime_error("unexpected end of section");
    }
    return uint32_t(data[off]) | (uint32_t(data[off + 1]) << 8) |
           (uint32_t(data[off + 2]) << 16) | (uint32_t(data[off + 3]) << 24);
}

uint64_t read_u64(const std::vector<uint8_t>& data, size_t off) {
    if (off + 8 > data.size()) {
        throw std::runtime_error("unexpected end of section");
    }
    uint64_t value = 0;
    for (int i = 7; i >= 0; --i) {
        value = (value << 8) | data[off + i];
    }
    return value;
}

uint64_t read_uint(const std::vector<uint8_t>& data, size_t off, size_t size) {
    if (off + size > data.size()) {
        throw std::runtime_error("unexpected end of section");
    }
    uint64_t value = 0;
    for (size_t i = 0; i < size; ++i) {
        value |= uint64_t(data[off + i]) << (8 * i);
    }
    return value;
}

uint64_t read_uleb(const std::vector<uint8_t>& data, size_t& off) {
    uint64_t result = 0;
    unsigned shift = 0;
    while (off < data.size()) {
        uint8_t byte = data[off++];
        result |= uint64_t(byte & 0x7f) << shift;
        if ((byte & 0x80) == 0) {
            return result;
        }
        shift += 7;
    }
    throw std::runtime_error("unterminated ULEB128");
}

int64_t read_sleb(const std::vector<uint8_t>& data, size_t& off) {
    int64_t result = 0;
    unsigned shift = 0;
    uint8_t byte = 0;
    do {
        if (off >= data.size()) {
            throw std::runtime_error("unterminated SLEB128");
        }
        byte = data[off++];
        result |= int64_t(byte & 0x7f) << shift;
        shift += 7;
    } while (byte & 0x80);
    if ((shift < 64) && (byte & 0x40)) {
        result |= -int64_t(1ULL << shift);
    }
    return result;
}

std::string read_cstr(const std::vector<uint8_t>& data, size_t& off) {
    size_t start = off;
    while (off < data.size() && data[off] != 0) {
        ++off;
    }
    if (off >= data.size()) {
        throw std::runtime_error("unterminated DW_FORM_string");
    }
    std::string value(reinterpret_cast<const char*>(data.data() + start), off - start);
    ++off;
    return value;
}

std::vector<uint8_t> read_block_bytes(const std::vector<uint8_t>& data, size_t& off, size_t len) {
    if (off + len > data.size()) {
        throw std::runtime_error("unexpected end of DW_FORM_block");
    }
    std::vector<uint8_t> block(data.begin() + off, data.begin() + off + len);
    off += len;
    return block;
}

std::vector<uint8_t> section_data(const elfio& reader, const std::string& name) {
    const section* sec = reader.sections[name];
    if (sec == nullptr || sec->get_data() == nullptr) {
        return {};
    }
    const auto* begin = reinterpret_cast<const uint8_t*>(sec->get_data());
    return std::vector<uint8_t>(begin, begin + sec->get_size());
}

std::string json_escape(const std::string& text) {
    std::ostringstream out;
    for (char c : text) {
        switch (c) {
        case '\\': out << "\\\\"; break;
        case '"': out << "\\\""; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (static_cast<unsigned char>(c) < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << int(static_cast<unsigned char>(c)) << std::dec;
            } else {
                out << c;
            }
        }
    }
    return out.str();
}

std::string to_json(const VariableRef& ref) {
    std::ostringstream out;
    out << "{";
    out << "\"expression\":\"" << json_escape(ref.expression) << "\",";
    out << "\"base_name\":\"" << json_escape(ref.base_name) << "\",";
    out << "\"address\":" << ref.address << ",";
    out << "\"byte_size\":" << ref.byte_size << ",";
    out << "\"variable_size\":" << ref.variable_size << ",";
    out << "\"indexed\":" << (ref.indexed ? "true" : "false") << ",";
    out << "\"signed\":";
    if (ref.signed_known) {
        out << (ref.is_signed ? "true" : "false");
    } else {
        out << "null";
    }
    out << ",";
    out << "\"source\":\"" << json_escape(ref.source) << "\",";
    out << "\"symbol_type\":\"" << json_escape(ref.symbol_type) << "\",";
    out << "\"binding\":\"" << json_escape(ref.binding) << "\",";
    out << "\"section_name\":\"" << json_escape(ref.section_name) << "\",";
    out << "\"type_name\":\"" << json_escape(ref.type_name) << "\"";
    out << "}";
    return out.str();
}

std::string to_json(const MemberPath& item) {
    std::ostringstream out;
    out << "{";
    out << "\"member_name\":\"" << json_escape(item.member_name) << "\",";
    out << "\"expression\":\"" << json_escape(item.expression) << "\",";
    out << "\"base_name\":\"" << json_escape(item.base_name) << "\",";
    out << "\"address\":" << item.address << ",";
    out << "\"byte_offset\":" << item.byte_offset << ",";
    out << "\"byte_size\":" << item.byte_size << ",";
    out << "\"signed\":";
    if (item.signed_known) {
        out << (item.is_signed ? "true" : "false");
    } else {
        out << "null";
    }
    out << ",";
    out << "\"type_name\":\"" << json_escape(item.type_name) << "\"";
    out << "}";
    return out.str();
}

std::string to_member_index_json(
    const std::string& elf_path,
    const std::vector<MemberPath>& items,
    int max_depth) {
    std::map<std::string, std::vector<const MemberPath*>> by_member;
    for (const auto& item : items) {
        by_member[item.member_name].push_back(&item);
    }

    std::ostringstream out;
    out << "{";
    out << "\"elf_path\":\"" << json_escape(elf_path) << "\",";
    out << "\"max_depth\":" << max_depth << ",";
    out << "\"entry_count\":" << items.size() << ",";
    out << "\"member_count\":" << by_member.size() << ",";
    out << "\"entries_by_member\":{";
    bool first_member = true;
    for (const auto& pair : by_member) {
        if (!first_member) {
            out << ",";
        }
        first_member = false;
        out << "\"" << json_escape(pair.first) << "\":[";
        bool first_item = true;
        for (const auto* item : pair.second) {
            if (!first_item) {
                out << ",";
            }
            first_item = false;
            out << to_json(*item);
        }
        out << "]";
    }
    out << "}}";
    return out.str();
}

std::string symbol_type_name(unsigned char type) {
    switch (type) {
    case STT_NOTYPE: return "STT_NOTYPE";
    case STT_OBJECT: return "STT_OBJECT";
    case STT_FUNC: return "STT_FUNC";
    case STT_SECTION: return "STT_SECTION";
    case STT_FILE: return "STT_FILE";
    case STT_COMMON: return "STT_COMMON";
    case STT_TLS: return "STT_TLS";
    default: return "STT_UNKNOWN";
    }
}

std::string binding_name(unsigned char bind) {
    switch (bind) {
    case STB_LOCAL: return "STB_LOCAL";
    case STB_GLOBAL: return "STB_GLOBAL";
    case STB_WEAK: return "STB_WEAK";
    default: return "STB_UNKNOWN";
    }
}

uint64_t parse_uint(const std::string& text) {
    size_t pos = 0;
    int base = 10;
    if (text.size() > 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) {
        base = 16;
    }
    uint64_t value = std::stoull(text, &pos, base);
    if (pos != text.size()) {
        throw std::runtime_error("invalid integer: " + text);
    }
    return value;
}

ParsedExpression parse_expression(const std::string& expression) {
    ParsedExpression parsed;
    parsed.base_expression = expression;
    auto lb = expression.rfind('[');
    if (lb != std::string::npos) {
        if (expression.empty() || expression.back() != ']') {
            throw std::runtime_error("invalid variable reference: " + expression);
        }
        parsed.index = parse_uint(expression.substr(lb + 1, expression.size() - lb - 2));
        parsed.base_expression = expression.substr(0, lb);
    }
    if (parsed.base_expression.empty()) {
        throw std::runtime_error("invalid variable reference: " + expression);
    }
    size_t start = 0;
    while (true) {
        size_t dot = parsed.base_expression.find('.', start);
        std::string part = parsed.base_expression.substr(
            start, dot == std::string::npos ? std::string::npos : dot - start);
        if (part.empty()) {
            throw std::runtime_error("invalid variable reference: " + expression);
        }
        if (parsed.root_name.empty()) {
            parsed.root_name = part;
        } else {
            parsed.fields.push_back(part);
        }
        if (dot == std::string::npos) {
            break;
        }
        start = dot + 1;
    }
    return parsed;
}

class DwarfResolver {
public:
    explicit DwarfResolver(const elfio& reader)
        : info(section_data(reader, ".debug_info")),
          abbrev(section_data(reader, ".debug_abbrev")) {
        if (!info.empty()) {
            scan_cus();
        }
    }

    bool available() const { return !info.empty() && !abbrev.empty(); }

    std::optional<VariableRef> resolve(const ParsedExpression& expr) {
        if (!available() || expr.fields.empty()) {
            return std::nullopt;
        }
        const Die* root = find_variable(expr.root_name);
        if (root == nullptr) {
            return std::nullopt;
        }
        auto root_addr = address_from_location(*root);
        if (!root_addr) {
            return std::nullopt;
        }
        const Die* root_type = resolve_type(ref_attr(*root, DW_AT_type));
        if (root_type == nullptr) {
            return std::nullopt;
        }

        uint64_t root_size = byte_size(*root_type).value_or(0);
        uint64_t byte_offset = 0;
        uint64_t final_size = root_size;
        std::optional<bool> final_signed = signedness(*root_type);
        std::string final_type_name = name_of(*root_type);
        const Die* current_type = root_type;
        std::string traversed = expr.root_name;

        for (const auto& field : expr.fields) {
            current_type = resolve_type(current_type);
            if (current_type == nullptr) {
                return std::nullopt;
            }
            const Die* member = nullptr;
            for (uint64_t child_off : current_type->children) {
                const Die* child = get_die(child_off);
                if (child != nullptr && child->tag == DW_TAG_member && name_of(*child) == field) {
                    member = child;
                    break;
                }
            }
            if (member == nullptr) {
                throw std::runtime_error("DWARF member not found: " + traversed + "." + field);
            }
            byte_offset += member_offset(*member);
            traversed += "." + field;

            const Die* member_type = resolve_type(ref_attr(*member, DW_AT_type));
            final_size = byte_size(*member).value_or(member_type ? byte_size(*member_type).value_or(0) : 0);
            final_signed = member_type ? signedness(*member_type) : std::nullopt;
            final_type_name = member_type ? name_of(*member_type) : "";
            current_type = member_type;
        }

        if (expr.index && final_size != 0 && *expr.index >= final_size) {
            throw std::runtime_error("byte index is outside " + expr.base_expression + " size");
        }

        VariableRef ref;
        ref.expression = expr.base_expression + (expr.index ? "[" + std::to_string(*expr.index) + "]" : "");
        ref.base_name = expr.root_name;
        ref.address = *root_addr + byte_offset + expr.index.value_or(0);
        ref.byte_size = expr.index ? 1 : final_size;
        ref.variable_size = root_size;
        ref.indexed = expr.index.has_value();
        ref.signed_known = final_signed.has_value();
        ref.is_signed = final_signed.value_or(false);
        ref.source = "ELFIO.DWARF.field";
        ref.symbol_type = "DW_TAG_variable";
        ref.type_name = final_type_name;
        return ref;
    }

    std::vector<MemberPath> build_member_index(size_t max_depth) {
        std::vector<MemberPath> items;
        if (!available() || max_depth == 0) {
            return items;
        }

        parse_all_cus();

        std::vector<const Die*> variables;
        variables.reserve(dies.size());
        for (const auto& pair : dies) {
            const Die& die = pair.second;
            if (die.tag == DW_TAG_variable && !name_of(die).empty()) {
                variables.push_back(&die);
            }
        }
        std::sort(variables.begin(), variables.end(), [](const Die* lhs, const Die* rhs) {
            return lhs->offset < rhs->offset;
        });

        std::set<std::string> seen_expressions;
        for (const Die* variable : variables) {
            auto root_addr = address_from_location(*variable);
            if (!root_addr) {
                continue;
            }
            const Die* root_type = resolve_type(ref_attr(*variable, DW_AT_type));
            if (root_type == nullptr || !is_aggregate_type(root_type)) {
                continue;
            }

            std::unordered_set<uint64_t> type_stack;
            std::string root_name = name_of(*variable);
            collect_member_paths(
                root_name,
                root_name,
                *root_addr,
                0,
                root_type,
                max_depth,
                type_stack,
                seen_expressions,
                items);
        }

        std::sort(items.begin(), items.end(), [](const MemberPath& lhs, const MemberPath& rhs) {
            return std::tie(lhs.member_name, lhs.expression, lhs.address) <
                   std::tie(rhs.member_name, rhs.expression, rhs.address);
        });
        return items;
    }

private:
    std::vector<uint8_t> info;
    std::vector<uint8_t> abbrev;
    std::vector<CuHeader> cus;
    std::unordered_map<uint64_t, std::map<uint64_t, Abbrev>> abbrev_cache;
    std::unordered_map<uint64_t, Die> dies;
    std::unordered_map<std::string, std::optional<uint64_t>> variable_cache;

    void parse_all_cus() {
        for (auto& cu : cus) {
            parse_cu(cu);
        }
    }

    void scan_cus() {
        size_t off = 0;
        while (off + 11 <= info.size()) {
            size_t start = off;
            uint32_t length = read_u32(info, off);
            off += 4;
            if (length == 0 || length == 0xffffffff) {
                break;
            }
            uint64_t end = start + 4 + length;
            if (end > info.size()) {
                break;
            }
            (void)read_u16(info, off);
            off += 2;
            uint64_t abbrev_offset = read_u32(info, off);
            off += 4;
            uint8_t address_size = info.at(off++);
            cus.push_back({start, end, abbrev_offset, address_size, false});
            off = end;
        }
    }

    CuHeader* cu_containing(uint64_t offset) {
        for (auto& cu : cus) {
            if (cu.start <= offset && offset < cu.end) {
                return &cu;
            }
        }
        return nullptr;
    }

    const std::map<uint64_t, Abbrev>& abbrevs_for(uint64_t abbrev_offset) {
        auto found = abbrev_cache.find(abbrev_offset);
        if (found != abbrev_cache.end()) {
            return found->second;
        }

        std::map<uint64_t, Abbrev> table;
        size_t off = abbrev_offset;
        while (off < abbrev.size()) {
            uint64_t code = read_uleb(abbrev, off);
            if (code == 0) {
                break;
            }
            Abbrev item;
            item.tag = read_uleb(abbrev, off);
            item.has_children = abbrev.at(off++) != 0;
            while (true) {
                uint64_t name = read_uleb(abbrev, off);
                uint64_t form = read_uleb(abbrev, off);
                if (name == 0 && form == 0) {
                    break;
                }
                item.attrs.push_back({name, form});
            }
            table[code] = item;
        }
        auto inserted = abbrev_cache.emplace(abbrev_offset, std::move(table));
        return inserted.first->second;
    }

    AttrValue read_attr(uint64_t form, size_t& off, const CuHeader& cu) {
        if (form == DW_FORM_indirect) {
            form = read_uleb(info, off);
        }
        AttrValue value;
        value.form = form;
        switch (form) {
        case DW_FORM_addr:
            value.u = read_uint(info, off, cu.address_size);
            off += cu.address_size;
            break;
        case DW_FORM_block1: {
            size_t len = info.at(off++);
            value.block = read_block_bytes(info, off, len);
            break;
        }
        case DW_FORM_block2: {
            size_t len = read_u16(info, off);
            off += 2;
            value.block = read_block_bytes(info, off, len);
            break;
        }
        case DW_FORM_block4: {
            size_t len = read_u32(info, off);
            off += 4;
            value.block = read_block_bytes(info, off, len);
            break;
        }
        case DW_FORM_block:
        case DW_FORM_exprloc: {
            size_t len = read_uleb(info, off);
            value.block = read_block_bytes(info, off, len);
            break;
        }
        case DW_FORM_data1:
        case DW_FORM_ref1:
        case DW_FORM_flag:
            value.u = read_uint(info, off, 1);
            off += 1;
            break;
        case DW_FORM_data2:
        case DW_FORM_ref2:
            value.u = read_uint(info, off, 2);
            off += 2;
            break;
        case DW_FORM_data4:
        case DW_FORM_ref4:
        case DW_FORM_sec_offset:
        case DW_FORM_strp:
            value.u = read_uint(info, off, 4);
            off += 4;
            break;
        case DW_FORM_data8:
        case DW_FORM_ref8:
        case DW_FORM_ref_sig8:
            value.u = read_uint(info, off, 8);
            off += 8;
            break;
        case DW_FORM_string:
            value.str = read_cstr(info, off);
            break;
        case DW_FORM_udata:
        case DW_FORM_ref_udata:
            value.u = read_uleb(info, off);
            break;
        case DW_FORM_sdata:
            value.s = read_sleb(info, off);
            value.u = static_cast<uint64_t>(value.s);
            break;
        case DW_FORM_ref_addr:
            value.u = read_uint(info, off, 4);
            off += 4;
            break;
        case DW_FORM_flag_present:
            value.u = 1;
            break;
        default:
            throw std::runtime_error("unsupported DWARF form: " + std::to_string(form));
        }
        return value;
    }

    void parse_cu(CuHeader& cu) {
        if (cu.parsed) {
            return;
        }
        const auto& abbrev_table = abbrevs_for(cu.abbrev_offset);
        size_t off = cu.start + 4 + 2 + 4 + 1;
        std::vector<uint64_t> stack;
        while (off < cu.end) {
            uint64_t die_offset = off;
            uint64_t code = read_uleb(info, off);
            if (code == 0) {
                if (!stack.empty()) {
                    stack.pop_back();
                }
                continue;
            }
            auto abbrev_it = abbrev_table.find(code);
            if (abbrev_it == abbrev_table.end()) {
                throw std::runtime_error("missing DWARF abbrev code");
            }
            const Abbrev& abbr = abbrev_it->second;
            Die die;
            die.offset = die_offset;
            die.cu_start = cu.start;
            die.tag = abbr.tag;
            die.has_children = abbr.has_children;
            for (const auto& spec : abbr.attrs) {
                die.attrs[spec.name] = read_attr(spec.form, off, cu);
            }
            if (!stack.empty()) {
                dies[stack.back()].children.push_back(die.offset);
            }
            dies[die.offset] = std::move(die);
            if (abbr.has_children) {
                stack.push_back(die_offset);
            }
        }
        cu.parsed = true;
    }

    const Die* get_die(uint64_t offset) {
        auto found = dies.find(offset);
        if (found != dies.end()) {
            return &found->second;
        }
        CuHeader* cu = cu_containing(offset);
        if (cu == nullptr) {
            return nullptr;
        }
        parse_cu(*cu);
        found = dies.find(offset);
        return found == dies.end() ? nullptr : &found->second;
    }

    const Die* find_variable(const std::string& name) {
        auto cached = variable_cache.find(name);
        if (cached != variable_cache.end()) {
            return cached->second ? get_die(*cached->second) : nullptr;
        }

        std::vector<size_t> offsets;
        const auto* needle = reinterpret_cast<const uint8_t*>(name.data());
        auto it = info.begin();
        while (true) {
            it = std::search(it, info.end(), needle, needle + name.size());
            if (it == info.end()) {
                break;
            }
            size_t offset = std::distance(info.begin(), it);
            size_t after = offset + name.size();
            if (after >= info.size() || info[after] == 0) {
                offsets.push_back(offset);
            }
            ++it;
        }

        for (size_t str_offset : offsets) {
            CuHeader* cu = cu_containing(str_offset);
            if (cu == nullptr) {
                continue;
            }
            parse_cu(*cu);
            for (const auto& pair : dies) {
                const Die& die = pair.second;
                if (die.cu_start == cu->start && die.tag == DW_TAG_variable &&
                    name_of(die) == name) {
                    variable_cache[name] = die.offset;
                    return &die;
                }
            }
        }
        variable_cache[name] = std::nullopt;
        return nullptr;
    }

    std::string name_of(const Die& die) const {
        auto it = die.attrs.find(DW_AT_name);
        return it == die.attrs.end() ? "" : it->second.str;
    }

    bool is_aggregate_type(const Die* die) {
        const Die* resolved = resolve_type(die);
        if (resolved == nullptr) {
            return false;
        }
        return resolved->tag == DW_TAG_structure_type || resolved->tag == DW_TAG_class_type ||
               resolved->tag == DW_TAG_union_type;
    }

    std::optional<uint64_t> byte_size(const Die& die) const {
        auto it = die.attrs.find(DW_AT_byte_size);
        if (it == die.attrs.end()) {
            return std::nullopt;
        }
        return it->second.u;
    }

    std::optional<bool> signedness(const Die& die) const {
        auto it = die.attrs.find(DW_AT_encoding);
        if (it == die.attrs.end()) {
            return std::nullopt;
        }
        return it->second.u == 0x05 || it->second.u == 0x06;
    }

    const AttrValue* attr(const Die& die, uint64_t attr_name) const {
        auto it = die.attrs.find(attr_name);
        return it == die.attrs.end() ? nullptr : &it->second;
    }

    std::optional<uint64_t> ref_attr(const Die& die, uint64_t attr_name) {
        const AttrValue* value = attr(die, attr_name);
        if (value == nullptr) {
            return std::nullopt;
        }
        switch (value->form) {
        case DW_FORM_ref_addr:
            return value->u;
        case DW_FORM_ref1:
        case DW_FORM_ref2:
        case DW_FORM_ref4:
        case DW_FORM_ref8:
        case DW_FORM_ref_udata:
            return die.cu_start + value->u;
        default:
            return std::nullopt;
        }
    }

    const Die* resolve_type(std::optional<uint64_t> offset) {
        if (!offset) {
            return nullptr;
        }
        return resolve_type(get_die(*offset));
    }

    const Die* resolve_type(const Die* die) {
        std::vector<uint64_t> seen;
        const Die* current = die;
        while (current != nullptr) {
            if (std::find(seen.begin(), seen.end(), current->offset) != seen.end()) {
                return current;
            }
            seen.push_back(current->offset);
            if (current->tag != DW_TAG_typedef && current->tag != DW_TAG_const_type &&
                current->tag != DW_TAG_volatile_type && current->tag != DW_TAG_restrict_type) {
                return current;
            }
            current = resolve_type(ref_attr(*current, DW_AT_type));
        }
        return current;
    }

    std::optional<uint64_t> address_from_location(const Die& die) const {
        const AttrValue* value = attr(die, DW_AT_location);
        if (value == nullptr || value->block.empty() || value->block[0] != 0x03) {
            return std::nullopt;
        }
        CuHeader const* cu = nullptr;
        for (const auto& item : cus) {
            if (item.start == die.cu_start) {
                cu = &item;
                break;
            }
        }
        size_t addr_size = cu == nullptr ? 4 : cu->address_size;
        if (value->block.size() < 1 + addr_size) {
            return std::nullopt;
        }
        uint64_t result = 0;
        for (size_t i = 0; i < addr_size; ++i) {
            result |= uint64_t(value->block[1 + i]) << (8 * i);
        }
        return result;
    }

    uint64_t member_offset(const Die& die) const {
        const AttrValue* value = attr(die, DW_AT_data_member_location);
        if (value == nullptr) {
            return 0;
        }
        if (value->form == DW_FORM_data1 || value->form == DW_FORM_data2 ||
            value->form == DW_FORM_data4 || value->form == DW_FORM_data8 ||
            value->form == DW_FORM_udata) {
            return value->u;
        }
        if (value->block.empty()) {
            return 0;
        }
        if (value->block[0] == 0x23) {
            size_t off = 1;
            return read_uleb(value->block, off);
        }
        if (value->block[0] >= 0x30 && value->block[0] <= 0x4f) {
            return value->block[0] - 0x30;
        }
        throw std::runtime_error("unsupported DWARF member location expression");
    }

    void collect_member_paths(
        const std::string& base_name,
        const std::string& parent_expression,
        uint64_t root_address,
        uint64_t parent_offset,
        const Die* type_die,
        size_t depth_remaining,
        std::unordered_set<uint64_t>& type_stack,
        std::set<std::string>& seen_expressions,
        std::vector<MemberPath>& items) {
        const Die* current_type = resolve_type(type_die);
        if (current_type == nullptr || !is_aggregate_type(current_type) || depth_remaining == 0) {
            return;
        }
        if (type_stack.find(current_type->offset) != type_stack.end()) {
            return;
        }

        type_stack.insert(current_type->offset);
        for (uint64_t child_off : current_type->children) {
            const Die* child = get_die(child_off);
            if (child == nullptr || child->tag != DW_TAG_member) {
                continue;
            }

            std::string member_name = name_of(*child);
            if (member_name.empty()) {
                continue;
            }

            uint64_t byte_offset = parent_offset + member_offset(*child);
            const Die* member_type = resolve_type(ref_attr(*child, DW_AT_type));
            uint64_t member_size =
                byte_size(*child).value_or(member_type ? byte_size(*member_type).value_or(0) : 0);
            std::optional<bool> member_signed = member_type ? signedness(*member_type) : std::nullopt;
            std::string member_type_name = member_type ? name_of(*member_type) : "";
            std::string expression = parent_expression + "." + member_name;

            if (seen_expressions.insert(expression).second) {
                MemberPath item;
                item.member_name = member_name;
                item.expression = expression;
                item.base_name = base_name;
                item.address = root_address + byte_offset;
                item.byte_offset = byte_offset;
                item.byte_size = member_size;
                item.signed_known = member_signed.has_value();
                item.is_signed = member_signed.value_or(false);
                item.type_name = member_type_name;
                items.push_back(std::move(item));
            }

            if (member_type != nullptr && is_aggregate_type(member_type)) {
                collect_member_paths(
                    base_name,
                    expression,
                    root_address,
                    byte_offset,
                    member_type,
                    depth_remaining - 1,
                    type_stack,
                    seen_expressions,
                    items);
            }
        }
        type_stack.erase(current_type->offset);
    }
};

std::optional<VariableRef> resolve_symbol(
    const elfio& reader,
    const ParsedExpression& expr,
    bool include_zero_size,
    bool include_notype) {
    if (!expr.fields.empty()) {
        return std::nullopt;
    }

    std::vector<VariableRef> matches;
    for (const auto& sec_ptr : reader.sections) {
        const section* sec = sec_ptr.get();
        if (sec->get_type() != SHT_SYMTAB && sec->get_type() != SHT_DYNSYM) {
            continue;
        }
        const_symbol_section_accessor symbols(reader, sec);
        for (Elf_Xword i = 0; i < symbols.get_symbols_num(); ++i) {
            std::string name;
            Elf64_Addr value = 0;
            Elf_Xword size = 0;
            unsigned char bind = 0;
            unsigned char type = 0;
            Elf_Half section_index = 0;
            unsigned char other = 0;
            if (!symbols.get_symbol(i, name, value, size, bind, type, section_index, other)) {
                continue;
            }
            if (name != expr.root_name || section_index == SHN_UNDEF) {
                continue;
            }
            bool variable_type = type == STT_OBJECT || type == STT_COMMON || type == STT_TLS ||
                                 (include_notype && type == STT_NOTYPE);
            if (!variable_type || (size == 0 && !include_zero_size)) {
                continue;
            }
            if (expr.index && size != 0 && *expr.index >= size) {
                throw std::runtime_error("byte index is outside " + expr.root_name + " size");
            }
            VariableRef ref;
            ref.expression = expr.base_expression + (expr.index ? "[" + std::to_string(*expr.index) + "]" : "");
            ref.base_name = name;
            ref.address = value + expr.index.value_or(0);
            ref.byte_size = expr.index ? 1 : size;
            ref.variable_size = size;
            ref.indexed = expr.index.has_value();
            ref.source = sec->get_name();
            ref.symbol_type = symbol_type_name(type);
            ref.binding = binding_name(bind);
            if (section_index < reader.sections.size() && reader.sections[section_index] != nullptr) {
                ref.section_name = reader.sections[section_index]->get_name();
            }
            matches.push_back(ref);
        }
    }

    if (matches.empty()) {
        return std::nullopt;
    }
    std::sort(matches.begin(), matches.end(), [](const VariableRef& lhs, const VariableRef& rhs) {
        return std::tuple(lhs.variable_size == 0, lhs.address == 0, lhs.binding == "STB_LOCAL") <
               std::tuple(rhs.variable_size == 0, rhs.address == 0, rhs.binding == "STB_LOCAL");
    });
    return matches.front();
}

VariableRef resolve_reference(
    const std::string& elf_path,
    const std::string& expression,
    bool include_zero_size,
    bool include_notype) {
    ParsedExpression parsed = parse_expression(expression);
    elfio reader;
    if (!reader.load(elf_path)) {
        throw std::runtime_error("failed to load ELF: " + elf_path);
    }

    DwarfResolver dwarf(reader);
    if (auto ref = dwarf.resolve(parsed)) {
        ref->expression = expression;
        return *ref;
    }
    if (auto ref = resolve_symbol(reader, parsed, include_zero_size, include_notype)) {
        ref->expression = expression;
        return *ref;
    }
    throw std::runtime_error("ELF variable not found: " + expression);
}

void write_member_index_json(
    const std::string& elf_path,
    const std::string& json_path,
    int max_depth) {
    if (max_depth <= 0) {
        throw std::runtime_error("max_depth must be greater than zero");
    }

    elfio reader;
    if (!reader.load(elf_path)) {
        throw std::runtime_error("failed to load ELF: " + elf_path);
    }

    DwarfResolver dwarf(reader);
    if (!dwarf.available()) {
        throw std::runtime_error("ELF has no usable DWARF .debug_info/.debug_abbrev");
    }

    std::vector<MemberPath> items = dwarf.build_member_index(static_cast<size_t>(max_depth));
    std::ofstream out(json_path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("failed to open JSON output: " + json_path);
    }
    out << to_member_index_json(elf_path, items, max_depth);
    if (!out) {
        throw std::runtime_error("failed to write JSON output: " + json_path);
    }
}

class ResolverHandle {
public:
    explicit ResolverHandle(const std::string& path) {
        if (!reader.load(path)) {
            throw std::runtime_error("failed to load ELF: " + path);
        }
        dwarf = std::make_unique<DwarfResolver>(reader);
    }

    VariableRef resolve(
        const std::string& expression,
        bool include_zero_size,
        bool include_notype) {
        ParsedExpression parsed = parse_expression(expression);
        if (auto ref = dwarf->resolve(parsed)) {
            ref->expression = expression;
            return *ref;
        }
        if (auto ref = resolve_symbol(reader, parsed, include_zero_size, include_notype)) {
            ref->expression = expression;
            return *ref;
        }
        throw std::runtime_error("ELF variable not found: " + expression);
    }

private:
    elfio reader;
    std::unique_ptr<DwarfResolver> dwarf;
};

void copy_string(const std::string& text, char* out, size_t out_size) {
    if (out == nullptr || out_size == 0) {
        return;
    }
    size_t n = std::min(out_size - 1, text.size());
    std::memcpy(out, text.data(), n);
    out[n] = '\0';
}

} // namespace

extern "C" int tc397_elf_resolve(
    const char* elf_path,
    const char* expression,
    int include_zero_size,
    int include_notype,
    char* out_json,
    size_t out_json_size,
    char* err,
    size_t err_size) {
    try {
        if (elf_path == nullptr || expression == nullptr) {
            throw std::runtime_error("elf_path and expression are required");
        }
        VariableRef ref = resolve_reference(
            elf_path, expression, include_zero_size != 0, include_notype != 0);
        copy_string(to_json(ref), out_json, out_json_size);
        copy_string("", err, err_size);
        return 0;
    } catch (const std::exception& exc) {
        copy_string(exc.what(), err, err_size);
        if (out_json != nullptr && out_json_size != 0) {
            out_json[0] = '\0';
        }
        return 1;
    }
}

extern "C" void* tc397_elf_open(const char* elf_path, char* err, size_t err_size) {
    try {
        if (elf_path == nullptr) {
            throw std::runtime_error("elf_path is required");
        }
        auto* handle = new ResolverHandle(elf_path);
        copy_string("", err, err_size);
        return handle;
    } catch (const std::exception& exc) {
        copy_string(exc.what(), err, err_size);
        return nullptr;
    }
}

extern "C" void tc397_elf_close(void* handle) {
    delete static_cast<ResolverHandle*>(handle);
}

extern "C" int tc397_elf_write_member_index(
    const char* elf_path,
    const char* json_path,
    int max_depth,
    char* err,
    size_t err_size) {
    try {
        if (elf_path == nullptr || json_path == nullptr) {
            throw std::runtime_error("elf_path and json_path are required");
        }
        write_member_index_json(elf_path, json_path, max_depth);
        copy_string("", err, err_size);
        return 0;
    } catch (const std::exception& exc) {
        copy_string(exc.what(), err, err_size);
        return 1;
    }
}

extern "C" int tc397_elf_resolve_handle(
    void* handle,
    const char* expression,
    int include_zero_size,
    int include_notype,
    char* out_json,
    size_t out_json_size,
    char* err,
    size_t err_size) {
    try {
        if (handle == nullptr || expression == nullptr) {
            throw std::runtime_error("handle and expression are required");
        }
        auto* resolver = static_cast<ResolverHandle*>(handle);
        VariableRef ref = resolver->resolve(
            expression, include_zero_size != 0, include_notype != 0);
        copy_string(to_json(ref), out_json, out_json_size);
        copy_string("", err, err_size);
        return 0;
    } catch (const std::exception& exc) {
        copy_string(exc.what(), err, err_size);
        if (out_json != nullptr && out_json_size != 0) {
            out_json[0] = '\0';
        }
        return 1;
    }
}
