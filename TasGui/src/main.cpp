// TasGui — minimal UDE-like upper-computer for TC397: browse ELF variables,
// expand struct members, read/write their values over Infineon DAS/TAS.

#include "das_client.hpp"
#include "elf_index.hpp"
#include "value_codec.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <future>
#include <memory>
#include <string>
#include <vector>

#define GLFW_INCLUDE_NONE
#include <GLFW/glfw3.h>
#include <GL/gl.h>

#include "imgui.h"
#include "imgui_impl_glfw.h"
#include "imgui_impl_opengl3.h"

using namespace tasgui;

namespace {

struct WatchItem {
    std::string expression;
    uint64_t address = 0;
    uint32_t byte_size = 0;
    std::string type_name;
    int is_signed = -1;
    std::string value;   // decoded value
    std::string hex;     // raw bytes
    std::string error;
    char edit_buf[160] = {0};
};

struct App {
    ElfIndex index;
    DasClient client;
    DasConfig cfg;

    char elf_path[512];
    char json_path[512];
    char manual_expr[256] = {0};
    char filter[128] = {0};

    std::vector<size_t> filtered;     // indices into index.entries()
    std::string last_filter = "\x01"; // force initial rebuild

    std::vector<WatchItem> watch;

    bool connected = false;
    std::string device_name;

    bool polling = false;
    float poll_hz = 5.0f;
    double last_poll = 0.0;

    // async load/connect
    std::future<std::string> task;
    bool busy = false;
    std::string busy_label;

    std::vector<std::string> log;
    std::vector<DapDevice> dap_devices;
    int dap_choice = -1; // -1 = use port_sel / none

    void add_log(const std::string& s) {
        log.push_back(s);
        if (log.size() > 500) log.erase(log.begin(), log.begin() + 100);
    }
};

std::string ci_lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

void rebuild_filter(App& app) {
    app.filtered.clear();
    std::string f = ci_lower(app.filter);
    const auto& entries = app.index.entries();
    app.filtered.reserve(entries.size());
    for (size_t i = 0; i < entries.size(); ++i) {
        if (f.empty()) {
            app.filtered.push_back(i);
            continue;
        }
        // match against expression or member name
        if (ci_lower(entries[i].expression).find(f) != std::string::npos ||
            ci_lower(entries[i].member_name).find(f) != std::string::npos) {
            app.filtered.push_back(i);
        }
    }
    app.last_filter = app.filter;
}

void add_watch_from_entry(App& app, const VarEntry& e) {
    WatchItem it;
    it.expression = e.expression;
    it.address = e.address;
    it.byte_size = e.byte_size;
    it.type_name = e.type_name;
    it.is_signed = e.is_signed;
    app.watch.push_back(std::move(it));
}

void add_watch_expr(App& app, const std::string& expr) {
    if (expr.empty() || app.busy) return;
    // 1) exact match in the index (full expression or unique leaf member).
    const auto& entries = app.index.entries();
    std::vector<const VarEntry*> exact;
    for (const auto& e : entries) {
        if (e.expression == expr || e.member_name == expr) exact.push_back(&e);
    }
    if (exact.size() == 1) {
        add_watch_from_entry(app, *exact[0]);
        app.add_log("Added " + exact[0]->expression);
        return;
    }
    if (exact.size() > 1) {
        // prefer an exact full-expression match if present
        for (auto* e : exact) {
            if (e->expression == expr) {
                add_watch_from_entry(app, *e);
                app.add_log("Added " + e->expression);
                return;
            }
        }
        app.add_log("Ambiguous leaf '" + expr + "' (" + std::to_string(exact.size()) +
                    " matches) — use a full path");
        return;
    }
    // 2) live resolve (arrays, arbitrary nested paths).
    VarRef ref = app.index.resolve(expr);
    if (!ref.ok) {
        app.add_log("Resolve failed for '" + expr + "': " + ref.error);
        return;
    }
    WatchItem it;
    it.expression = ref.expression;
    it.address = ref.address;
    it.byte_size = ref.byte_size;
    it.type_name = ref.type_name;
    it.is_signed = ref.is_signed;
    app.watch.push_back(std::move(it));
    app.add_log("Resolved & added " + ref.expression);
}

void poll_reads(App& app) {
    if (!app.connected) return;
    for (auto& it : app.watch) {
        if (it.byte_size == 0) { it.error = "unknown size"; continue; }
        try {
            auto data = app.client.read((uint32_t)it.address, it.byte_size);
            it.hex = to_hex(data);
            it.value = decode_value(data, it.type_name, it.is_signed);
            it.error.clear();
        } catch (const std::exception& exc) {
            it.error = exc.what();
        }
    }
}

void write_item(App& app, WatchItem& it) {
    if (app.busy) return;
    if (!app.connected) { it.error = "not connected"; return; }
    try {
        auto bytes = encode_value(it.edit_buf, it.type_name, it.is_signed, it.byte_size);
        app.client.write((uint32_t)it.address, bytes);
        app.add_log("Wrote " + it.expression + " = " + it.edit_buf);
        // read back immediately
        auto data = app.client.read((uint32_t)it.address, it.byte_size);
        it.hex = to_hex(data);
        it.value = decode_value(data, it.type_name, it.is_signed);
        it.error.clear();
    } catch (const std::exception& exc) {
        it.error = exc.what();
        app.add_log("Write failed " + it.expression + ": " + exc.what());
    }
}

// ---- UI panels ----------------------------------------------------------

void ui_connection(App& app) {
    ImGui::Begin("Connection");

    {
        static char das_home_buf[512];
        if (das_home_buf[0] == 0)
            std::strncpy(das_home_buf, app.cfg.das_home.c_str(), sizeof(das_home_buf) - 1);
        if (ImGui::InputText("DAS_HOME path", das_home_buf, sizeof(das_home_buf)))
            app.cfg.das_home = das_home_buf;
    }

    ImGui::InputInt("server index", &app.cfg.server_index);
    ImGui::InputInt("port sel", &app.cfg.port_sel);

    if (ImGui::Button("Refresh DAP list")) {
        app.dap_devices = list_dap_devices();
        app.add_log("Found " + std::to_string(app.dap_devices.size()) + " DAP device(s)");
    }
    ImGui::SameLine();
    {
        std::string preview = app.dap_choice >= 0 && app.dap_choice < (int)app.dap_devices.size()
                                  ? app.dap_devices[app.dap_choice].serial
                                  : "(use port sel)";
        if (ImGui::BeginCombo("DAP wiggler", preview.c_str())) {
            if (ImGui::Selectable("(use port sel)", app.dap_choice < 0)) {
                app.dap_choice = -1;
                app.cfg.dap_serial.clear();
            }
            for (int i = 0; i < (int)app.dap_devices.size(); ++i) {
                const auto& d = app.dap_devices[i];
                std::string label = d.serial + "  [bus " + std::to_string(d.busnum) +
                                    " dev " + std::to_string(d.devnum) + "]  " + d.product;
                if (ImGui::Selectable(label.c_str(), app.dap_choice == i)) {
                    app.dap_choice = i;
                    app.cfg.dap_serial = d.serial;
                }
            }
            ImGui::EndCombo();
        }
    }

    ImGui::BeginDisabled(app.busy);
    if (!app.connected) {
        if (ImGui::Button("Connect")) {
            app.busy = true;
            app.busy_label = "Connecting...";
            DasConfig cfg = app.cfg;
            app.task = std::async(std::launch::async, [&app, cfg]() -> std::string {
                try {
                    app.client.connect(cfg);
                    return "";
                } catch (const std::exception& e) {
                    return std::string("connect error: ") + e.what();
                }
            });
        }
    } else {
        if (ImGui::Button("Disconnect")) {
            app.client.disconnect();
            app.connected = false;
            app.polling = false;
            app.device_name.clear();
            app.add_log("Disconnected");
        }
    }
    ImGui::EndDisabled();

    ImGui::SameLine();
    if (app.connected)
        ImGui::TextColored(ImVec4(0.3f, 1.0f, 0.3f, 1.0f), "CONNECTED  %s",
                           app.device_name.c_str());
    else
        ImGui::TextColored(ImVec4(1.0f, 0.6f, 0.3f, 1.0f), "disconnected");

    ImGui::End();
}

void ui_elf(App& app) {
    ImGui::Begin("ELF / Index");
    ImGui::InputText("ELF path", app.elf_path, sizeof(app.elf_path));
    ImGui::InputText("Index JSON", app.json_path, sizeof(app.json_path));

    ImGui::BeginDisabled(app.busy);
    if (ImGui::Button("Load / Index")) {
        app.busy = true;
        app.busy_label = "Loading ELF index...";
        std::string elf = app.elf_path, json = app.json_path;
        app.task = std::async(std::launch::async, [&app, elf, json]() -> std::string {
            if (app.index.load(elf, json, 8, false)) return "";
            return app.index.last_error();
        });
    }
    ImGui::SameLine();
    if (ImGui::Button("Force regen")) {
        app.busy = true;
        app.busy_label = "Regenerating index...";
        std::string elf = app.elf_path, json = app.json_path;
        app.task = std::async(std::launch::async, [&app, elf, json]() -> std::string {
            if (app.index.load(elf, json, 8, true)) return "";
            return app.index.last_error();
        });
    }
    ImGui::EndDisabled();

    if (app.index.loaded())
        ImGui::Text("Loaded %zu variables", app.index.entries().size());
    else
        ImGui::TextDisabled("no index loaded");
    ImGui::End();
}

void ui_variables(App& app) {
    ImGui::Begin("Variables");
    if (app.busy) {
        ImGui::TextDisabled("%s", app.busy_label.c_str());
        ImGui::End();
        return;
    }
    if (!app.index.loaded()) {
        ImGui::TextDisabled("Load an ELF index first.");
        ImGui::End();
        return;
    }

    if (ImGui::InputText("filter", app.filter, sizeof(app.filter))) {
        // rebuilt below when changed
    }
    if (app.last_filter != app.filter) rebuild_filter(app);

    ImGui::Text("%zu / %zu match", app.filtered.size(), app.index.entries().size());

    const auto& entries = app.index.entries();
    if (ImGui::BeginTable("vars", 5,
                          ImGuiTableFlags_Resizable | ImGuiTableFlags_Borders |
                          ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY)) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("", ImGuiTableColumnFlags_WidthFixed, 28.0f);
        ImGui::TableSetupColumn("Expression");
        ImGui::TableSetupColumn("Address", ImGuiTableColumnFlags_WidthFixed, 90.0f);
        ImGui::TableSetupColumn("Type", ImGuiTableColumnFlags_WidthFixed, 130.0f);
        ImGui::TableSetupColumn("Sz", ImGuiTableColumnFlags_WidthFixed, 34.0f);
        ImGui::TableHeadersRow();

        ImGuiListClipper clipper;
        clipper.Begin((int)app.filtered.size());
        while (clipper.Step()) {
            for (int row = clipper.DisplayStart; row < clipper.DisplayEnd; ++row) {
                const VarEntry& e = entries[app.filtered[row]];
                ImGui::TableNextRow();
                ImGui::TableSetColumnIndex(0);
                ImGui::PushID(row);
                if (ImGui::SmallButton("+")) add_watch_from_entry(app, e);
                ImGui::PopID();
                ImGui::TableSetColumnIndex(1);
                ImGui::TextUnformatted(e.expression.c_str());
                if (ImGui::IsItemHovered() && ImGui::IsMouseDoubleClicked(0))
                    add_watch_from_entry(app, e);
                ImGui::TableSetColumnIndex(2);
                ImGui::Text("0x%08llX", (unsigned long long)e.address);
                ImGui::TableSetColumnIndex(3);
                ImGui::TextUnformatted(e.type_name.c_str());
                ImGui::TableSetColumnIndex(4);
                ImGui::Text("%u", e.byte_size);
            }
        }
        clipper.End();
        ImGui::EndTable();
    }
    ImGui::End();
}

void ui_watch(App& app) {
    ImGui::Begin("Watch");

    ImGui::SetNextItemWidth(260);
    ImGui::InputText("##manual", app.manual_expr, sizeof(app.manual_expr));
    ImGui::SameLine();
    if (ImGui::Button("Add expr")) {
        add_watch_expr(app, app.manual_expr);
        app.manual_expr[0] = 0;
    }
    ImGui::SameLine();
    ImGui::Checkbox("Poll", &app.polling);
    ImGui::SameLine();
    ImGui::SetNextItemWidth(120);
    ImGui::SliderFloat("Hz", &app.poll_hz, 0.5f, 50.0f, "%.1f");
    ImGui::SameLine();
    if (ImGui::Button("Read once")) poll_reads(app);
    ImGui::SameLine();
    if (ImGui::Button("Clear")) app.watch.clear();

    if (ImGui::BeginTable("watch", 7,
                          ImGuiTableFlags_Resizable | ImGuiTableFlags_Borders |
                          ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY)) {
        ImGui::TableSetupColumn("Expression");
        ImGui::TableSetupColumn("Address", ImGuiTableColumnFlags_WidthFixed, 90.0f);
        ImGui::TableSetupColumn("Type", ImGuiTableColumnFlags_WidthFixed, 110.0f);
        ImGui::TableSetupColumn("Value", ImGuiTableColumnFlags_WidthFixed, 120.0f);
        ImGui::TableSetupColumn("Hex", ImGuiTableColumnFlags_WidthFixed, 130.0f);
        ImGui::TableSetupColumn("Write", ImGuiTableColumnFlags_WidthFixed, 200.0f);
        ImGui::TableSetupColumn("", ImGuiTableColumnFlags_WidthFixed, 28.0f);
        ImGui::TableHeadersRow();

        int remove_idx = -1;
        for (int i = 0; i < (int)app.watch.size(); ++i) {
            WatchItem& it = app.watch[i];
            ImGui::TableNextRow();
            ImGui::PushID(i);
            ImGui::TableSetColumnIndex(0);
            ImGui::TextUnformatted(it.expression.c_str());
            ImGui::TableSetColumnIndex(1);
            ImGui::Text("0x%08llX", (unsigned long long)it.address);
            ImGui::TableSetColumnIndex(2);
            ImGui::TextUnformatted(it.type_name.c_str());
            ImGui::TableSetColumnIndex(3);
            if (!it.error.empty())
                ImGui::TextColored(ImVec4(1, 0.4f, 0.4f, 1), "ERR");
            else
                ImGui::TextUnformatted(it.value.c_str());
            if (!it.error.empty() && ImGui::IsItemHovered())
                ImGui::SetTooltip("%s", it.error.c_str());
            ImGui::TableSetColumnIndex(4);
            ImGui::TextUnformatted(it.hex.c_str());
            ImGui::TableSetColumnIndex(5);
            ImGui::SetNextItemWidth(120);
            bool enter = ImGui::InputText("##v", it.edit_buf, sizeof(it.edit_buf),
                                          ImGuiInputTextFlags_EnterReturnsTrue);
            ImGui::SameLine();
            if (ImGui::SmallButton("W") || enter) write_item(app, it);
            ImGui::TableSetColumnIndex(6);
            if (ImGui::SmallButton("x")) remove_idx = i;
            ImGui::PopID();
        }
        if (remove_idx >= 0) app.watch.erase(app.watch.begin() + remove_idx);
        ImGui::EndTable();
    }
    ImGui::End();
}

void ui_log(App& app) {
    ImGui::Begin("Log");
    if (ImGui::Button("Clear log")) app.log.clear();
    ImGui::Separator();
    ImGui::BeginChild("logscroll");
    for (const auto& line : app.log) ImGui::TextUnformatted(line.c_str());
    if (!app.log.empty()) ImGui::SetScrollHereY(1.0f);
    ImGui::EndChild();
    ImGui::End();
}

void pump_async(App& app) {
    if (!app.busy || !app.task.valid()) return;
    if (app.task.wait_for(std::chrono::seconds(0)) != std::future_status::ready) return;
    std::string err = app.task.get();
    app.busy = false;
    if (err.empty()) {
        app.add_log(app.busy_label + " done");
        // refresh derived state
        if (app.client.connected()) {
            app.connected = true;
            app.device_name = app.client.device_name();
        }
        if (app.index.loaded() && app.last_filter != std::string(app.filter)) {
            // force filter rebuild next frame
        }
        rebuild_filter(app);
    } else {
        app.add_log("ERROR: " + err);
    }
}

void glfw_error(int e, const char* d) { std::fprintf(stderr, "GLFW %d: %s\n", e, d); }

} // namespace

int main(int argc, char** argv) {
    App app;
    std::strncpy(app.elf_path,
                 "/home/shiheping/QianLiPrj/TC397Tools/Downloads/MCU_A.elf",
                 sizeof(app.elf_path) - 1);
    std::strncpy(app.json_path,
                 "/home/shiheping/QianLiPrj/TC397Tools/Downloads/MCU_A.json",
                 sizeof(app.json_path) - 1);
    if (argc > 1) std::strncpy(app.elf_path, argv[1], sizeof(app.elf_path) - 1);
    app.dap_devices = list_dap_devices();

    glfwSetErrorCallback(glfw_error);
    if (!glfwInit()) {
        std::fprintf(stderr, "glfwInit failed\n");
        return 1;
    }
    const char* glsl_version = "#version 130";
    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 0);

    GLFWwindow* window = glfwCreateWindow(1280, 800, "TasGui - TC397 TAS", nullptr, nullptr);
    if (!window) {
        std::fprintf(stderr, "window creation failed\n");
        glfwTerminate();
        return 1;
    }
    glfwMakeContextCurrent(window);
    glfwSwapInterval(1);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui::StyleColorsDark();
    ImGui_ImplGlfw_InitForOpenGL(window, true);
    ImGui_ImplOpenGL3_Init(glsl_version);

    while (!glfwWindowShouldClose(window)) {
        glfwPollEvents();
        pump_async(app);

        // polling reads
        if (app.polling && app.connected && !app.busy) {
            double now = glfwGetTime();
            double interval = 1.0 / (app.poll_hz > 0.1f ? app.poll_hz : 0.1f);
            if (now - app.last_poll >= interval) {
                poll_reads(app);
                app.last_poll = now;
            }
        }

        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplGlfw_NewFrame();
        ImGui::NewFrame();

        ui_connection(app);
        ui_elf(app);
        ui_variables(app);
        ui_watch(app);
        ui_log(app);

        if (app.busy) {
            ImGui::Begin("##busy", nullptr,
                         ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_AlwaysAutoResize);
            ImGui::Text("%s", app.busy_label.c_str());
            ImGui::End();
        }

        ImGui::Render();
        int w, h;
        glfwGetFramebufferSize(window, &w, &h);
        glViewport(0, 0, w, h);
        glClearColor(0.1f, 0.1f, 0.12f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        glfwSwapBuffers(window);
    }

    if (app.client.connected()) app.client.disconnect();
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplGlfw_Shutdown();
    ImGui::DestroyContext();
    glfwDestroyWindow(window);
    glfwTerminate();
    return 0;
}
