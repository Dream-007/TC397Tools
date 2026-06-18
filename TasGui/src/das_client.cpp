#include "das_client.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>

#include <dirent.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

extern "C" {
#include <das_api.h>
}

extern char** environ;

namespace tasgui {

namespace {

constexpr uint32_t kMaxTransfer = DAS_MAX_TRANSFER_SIZE; // 1024
constexpr const char* kInfineonVendor = "058b";

std::string describe_error(unsigned err) {
    if (err == 0) return "0x0";
    struct Bit { unsigned bit; const char* name; };
    static const Bit bits[] = {
        {DAS_ERROR_DEVICE_RESET, "DEVICE_RESET"},
        {DAS_ERROR_DEVICE_LOCKED, "DEVICE_LOCKED"},
        {DAS_ERROR_DEVICE_ACCESS, "DEVICE_ACCESS"},
        {DAS_ERROR_DEVICE_DATA, "DEVICE_DATA"},
        {DAS_ERROR_PORT_ACCESS, "PORT_ACCESS"},
        {DAS_ERROR_SERVER_LOCKED, "SERVER_LOCKED"},
        {DAS_ERROR_TIMEOUT, "TIMEOUT"},
        {DAS_ERROR_COMMAND_FAILED, "COMMAND_FAILED"},
        {DAS_ERROR_PARAMETER, "PARAMETER"},
        {DAS_ERROR_CONNECTION, "CONNECTION"},
        {DAS_ERROR_NO_SERVER, "NO_SERVER"},
        {DAS_ERROR_FATAL, "FATAL"},
    };
    char head[16];
    std::snprintf(head, sizeof(head), "0x%08x", err);
    std::string out = head;
    std::string names;
    for (const auto& b : bits) {
        if (err & b.bit) {
            if (!names.empty()) names += "|";
            names += b.name;
        }
    }
    if (!names.empty()) out += " (" + names + ")";
    return out;
}

std::string read_text_file(const std::string& path) {
    std::ifstream f(path);
    if (!f) return "";
    std::string s;
    std::getline(f, s);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' '))
        s.pop_back();
    return s;
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

bool file_exists(const std::string& path) {
    struct stat st;
    return ::stat(path.c_str(), &st) == 0;
}

} // namespace

std::vector<DapDevice> list_dap_devices() {
    std::vector<DapDevice> devices;
    const char* base = "/sys/bus/usb/devices";
    DIR* dir = ::opendir(base);
    if (!dir) return devices;
    struct dirent* ent;
    while ((ent = ::readdir(dir)) != nullptr) {
        std::string name = ent->d_name;
        if (name == "." || name == "..") continue;
        std::string dpath = std::string(base) + "/" + name;
        if (lower(read_text_file(dpath + "/idVendor")) != kInfineonVendor) continue;
        std::string serial = read_text_file(dpath + "/serial");
        if (serial.empty()) continue;
        DapDevice d;
        d.serial = serial;
        d.product = read_text_file(dpath + "/product");
        d.busnum = std::atoi(read_text_file(dpath + "/busnum").c_str());
        d.devnum = std::atoi(read_text_file(dpath + "/devnum").c_str());
        devices.push_back(std::move(d));
    }
    ::closedir(dir);
    std::sort(devices.begin(), devices.end(), [](const DapDevice& a, const DapDevice& b) {
        if (a.busnum != b.busnum) return a.busnum < b.busnum;
        if (a.devnum != b.devnum) return a.devnum < b.devnum;
        return a.serial < b.serial;
    });
    return devices;
}

DasClient::~DasClient() {
    disconnect();
}

void* DasClient::load_api(const std::string& das_home) {
    // libdas_api.so is linked at build time; das_api_load returns the vtable.
    unsigned err = 0;
    das_api_t* api = das_api_load(DAS_API_VERSION_MAJOR, &err);
    if (!api || err) {
        throw std::runtime_error("das_api_load failed: " + describe_error(err));
    }
    das_client_info_t info;
    std::memset(&info, 0, sizeof(info));
    std::strncpy(info.name, "TasGui", sizeof(info.name) - 1);
    std::strncpy(info.manufacturer_name, "Local", sizeof(info.manufacturer_name) - 1);
    info.version_major = 0;
    info.version_minor = 1;
    info.das_api_v_major = DAS_API_VERSION_MAJOR;
    info.das_api_v_minor = DAS_API_VERSION_MINOR;
    std::strncpy(info.date, __DATE__, sizeof(info.date) - 1);
    err = 0;
    api->init(&info, &err);
    if (err) {
        throw std::runtime_error("das init failed: " + describe_error(err));
    }
    return api;
}

void DasClient::ensure_server(const DasConfig& cfg) {
    das_api_t* api = static_cast<das_api_t*>(api_);
    das_servers_on_host_list_t servers;
    unsigned err = 0;
    api->get_das_servers(cfg.host.c_str(), &servers, &err);
    if (!err && servers.n_das_servers > 0) return;

    std::string bin = cfg.das_home + "/bin/tas_server";
    const char* argv0 = file_exists(bin) ? bin.c_str() : "tas_server";
    char* argv[] = {const_cast<char*>(argv0), nullptr};
    pid_t pid = 0;
    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID);
    int rc = posix_spawnp(&pid, argv0, nullptr, &attr, argv, environ);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        throw std::runtime_error("failed to spawn tas_server");
    }
    for (int i = 0; i < 25; ++i) {
        ::usleep(200 * 1000);
        err = 0;
        api->get_das_servers(cfg.host.c_str(), &servers, &err);
        if (!err && servers.n_das_servers > 0) return;
    }
    throw std::runtime_error("tas_server did not become ready");
}

void DasClient::connect(const DasConfig& cfg_in) {
    disconnect();
    DasConfig cfg = cfg_in;

    if (!api_) api_ = load_api(cfg.das_home);
    das_api_t* api = static_cast<das_api_t*>(api_);

    ensure_server(cfg);

    das_servers_on_host_list_t servers;
    unsigned err = 0;
    api->get_das_servers(cfg.host.c_str(), &servers, &err);
    if (err) throw std::runtime_error("get_das_servers failed: " + describe_error(err));
    if (cfg.server_index < 0 || (unsigned)cfg.server_index >= servers.n_das_servers) {
        throw std::runtime_error("server index out of range (found " +
                                 std::to_string(servers.n_das_servers) + " server(s))");
    }
    das_server_info_t* server = &servers.si[cfg.server_index];

    // Map DAP wiggler serial to a port select index.
    if (!cfg.dap_serial.empty()) {
        auto devs = list_dap_devices();
        int sel = -1;
        for (size_t i = 0; i < devs.size(); ++i) {
            if (lower(devs[i].serial) == lower(cfg.dap_serial)) { sel = (int)i; break; }
        }
        if (sel < 0) throw std::runtime_error("DAP serial '" + cfg.dap_serial +
                                              "' not found on USB");
        cfg.port_sel = sel;
    }
    if ((unsigned)cfg.port_sel >= server->ports[cfg.port_type]) {
        throw std::runtime_error("port select " + std::to_string(cfg.port_sel) +
                                 " out of range (have " +
                                 std::to_string(server->ports[cfg.port_type]) + ")");
    }

    das_key_t key = das_default_key;
    err = 0;
    das_port_t port = api->open_port(DAS_OPO_DEFAULT, server, &key, &err);
    if (!port || err) throw std::runtime_error("open_port failed: " + describe_error(err));
    port_ = port;

    err = 0;
    api->map_port(port, DAS_MPO_DEFAULT,
                  static_cast<das_server_port_type_t>(cfg.port_type),
                  cfg.port_sel, &err);
    if (err) { disconnect(); throw std::runtime_error("map_port failed: " + describe_error(err)); }

    das_device_info_t device;
    std::memset(&device, 0, sizeof(device));
    err = 0;
    api->connect_to_device(port, (uint8_t)cfg.device_sel, &device, &err);
    if (err) { disconnect(); throw std::runtime_error("connect_to_device failed: " + describe_error(err)); }

    err = 0;
    api->init_device(port, nullptr, DAS_DIO_HOT_ATTACH, &err);
    if (err) { disconnect(); throw std::runtime_error("init_device failed: " + describe_error(err)); }

    device.name[sizeof(device.name) - 1] = '\0';
    device_name_ = device.name;
    cfg_addr_map_ = cfg.addr_map;
}

void DasClient::disconnect() {
    if (port_ && api_) {
        das_api_t* api = static_cast<das_api_t*>(api_);
        unsigned err = 0;
        api->close_port(static_cast<das_port_t>(port_), &err);
    }
    port_ = nullptr;
    device_name_.clear();
}

// Run a single transaction (one chunk).
static void run_transaction(das_api_t* api, das_port_t port, uint8_t action,
                            uint8_t addr_map, uint32_t address, void* buffer,
                            uint16_t size, const char* verb) {
    das_transaction_t tx;
    std::memset(&tx, 0, sizeof(tx));
    tx.action = action;
    tx.addr_map = addr_map;
    tx.n_bytes = size;
    tx.address = address;
    tx.data = buffer;

    das_list_t list;
    std::memset(&list, 0, sizeof(list));
    list.control = DAS_LC_DEFAULT;
    list.status = DAS_LS_OK;
    list.n_items = 1;
    list.transaction = &tx;

    unsigned err = 0;
    api->send_list(port, &list, &err);
    if (err) throw std::runtime_error(std::string(verb) + " send_list failed: " + describe_error(err));

    err = 0;
    api->wait_list(port, DAS_DEFAULT_TIMEOUT, &list, &err);
    if (err || list.status != DAS_LS_OK || tx.status != DAS_TS_OK) {
        char buf[160];
        std::snprintf(buf, sizeof(buf),
                      "%s failed: api=%s list=0x%x tx=0x%x", verb,
                      describe_error(err).c_str(), list.status, tx.status);
        throw std::runtime_error(buf);
    }
}

std::vector<uint8_t> DasClient::read(uint32_t address, size_t byte_count) {
    if (!port_) throw std::runtime_error("TAS is not connected");
    das_api_t* api = static_cast<das_api_t*>(api_);
    std::vector<uint8_t> out(byte_count);
    size_t offset = 0;
    while (offset < byte_count) {
        uint16_t size = (uint16_t)std::min<size_t>(kMaxTransfer, byte_count - offset);
        run_transaction(api, static_cast<das_port_t>(port_),
                        DAS_TRA_R | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
                        (uint8_t)cfg_addr_map_, address + offset,
                        out.data() + offset, size, "read");
        offset += size;
    }
    return out;
}

void DasClient::write(uint32_t address, const std::vector<uint8_t>& data) {
    if (!port_) throw std::runtime_error("TAS is not connected");
    das_api_t* api = static_cast<das_api_t*>(api_);
    size_t offset = 0;
    while (offset < data.size()) {
        uint16_t size = (uint16_t)std::min<size_t>(kMaxTransfer, data.size() - offset);
        std::vector<uint8_t> chunk(data.begin() + offset, data.begin() + offset + size);
        run_transaction(api, static_cast<das_port_t>(port_),
                        DAS_TRA_W | DAS_TRA_BYTE | DAS_TRA_RW_TRANSACTION,
                        (uint8_t)cfg_addr_map_, address + offset,
                        chunk.data(), size, "write");
        offset += size;
    }
}

} // namespace tasgui
