#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace tasgui {

struct DapDevice {
    std::string serial;
    std::string product;
    int busnum = 0;
    int devnum = 0;
};

// Enumerate Infineon (vendor 058b) DAP wigglers visible on USB, ordered the same
// way base_tas.py does, so list index == DAS port select.
std::vector<DapDevice> list_dap_devices();

// Connection parameters chosen in the UI.
struct DasConfig {
    std::string das_home = "/opt/Tools/DAS/8.3.0";
    std::string host = "127.0.0.1";
    int server_index = 0;
    int port_type = 3;   // DAS_PT_JTAG
    int port_sel = 0;
    int device_sel = 0;
    int addr_map = 0;    // DAS_AMAP_DEVICE_MIN
    std::string dap_serial; // empty => use port_sel directly
};

class DasClient {
public:
    DasClient() = default;
    ~DasClient();

    DasClient(const DasClient&) = delete;
    DasClient& operator=(const DasClient&) = delete;

    // Start tas_server if no server is running yet, then open/map/connect/init.
    // Throws std::runtime_error with a descriptive message on failure.
    void connect(const DasConfig& cfg);
    void disconnect();
    bool connected() const { return port_ != nullptr; }

    std::string device_name() const { return device_name_; }

    std::vector<uint8_t> read(uint32_t address, size_t byte_count);
    void write(uint32_t address, const std::vector<uint8_t>& data);

private:
    void ensure_server(const DasConfig& cfg);
    void* load_api(const std::string& das_home);

    void* api_ = nullptr;       // das_api_t*
    void* port_ = nullptr;      // das_port_t
    int cfg_addr_map_ = 0;
    std::string device_name_;
};

} // namespace tasgui
