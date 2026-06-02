# TC397Tools

TC397Tools 用于在 Linux 环境下自动化读写 TC397 MCU 地址值。当前核心流程是：C++ 解析 ELF/DWARF 生成变量索引 JSON，Python 加载 JSON 后根据完整变量路径或末级成员名查找地址，再通过 Infineon DAS/TAS 连接目标板并执行读写。

## 功能概览

1. 使用 `cpp/tc397_elfio_resolver.cpp` 解析 ELF/DWARF。
2. 生成 `build/MCU_A.json` 变量索引文件。
3. JSON 顶层写入 ELF 的 `elf_md5` 等信息，用于判断索引是否过期。
4. Python 支持通过完整变量路径或末级成员名查找变量。
5. Python 通过 TAS/DAS 连接目标芯片。
6. 根据查到的地址和字节大小读取或写入 MCU 内存。

如果 ELF 文件不存在但 `MCU_A.json` 存在且结构可用，Python 仍可直接加载 JSON 查询变量地址。

## 编译 C++ 解析器

Ubuntu 20.04 / `g++ 9.4.0` 可直接执行：

```bash
bash scripts/build_tc397_elfio_resolver.sh
```

等价手动命令：

```bash
g++ -std=c++17 -O3 -fPIC -shared \
  -I/home/shiheping/QianLiPrj/TC397Tools/ELFIO \
  /home/shiheping/QianLiPrj/TC397Tools/cpp/tc397_elfio_resolver.cpp \
  -o /home/shiheping/QianLiPrj/TC397Tools/scripts/libtc397_elfio_resolver.so
```

## 默认文件

主要 Python 文件：

```text
scripts/base_tas.py
```

默认路径：

```text
ELF:  /home/shiheping/QianLiPrj/TC397Tools/Downloads/MCU_A.elf
JSON: /home/shiheping/QianLiPrj/TC397Tools/build/MCU_A.json
SO:   /home/shiheping/QianLiPrj/TC397Tools/scripts/libtc397_elfio_resolver.so
```

## JSON 索引结构

`MCU_A.json` 主要包含：

- `entries_by_member`：以末级成员名为 key 的变量列表。
- `elf_path`：ELF 绝对路径。
- `elf_size`：ELF 文件大小。
- `elf_mtime_ns`：ELF 纳秒级修改时间。
- `elf_md5`：ELF MD5。
- `max_depth`：结构体成员最大递归深度。

变量条目示例：

```json
{
  "member_name": "VehModMngtGlbSafe1UsgModSts",
  "expression": "kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts",
  "base_name": "kAdapterReadSignalDoc",
  "address": 1610758516,
  "byte_offset": 448,
  "byte_size": 1,
  "signed": false,
  "type_name": "unsigned char"
}
```

## Python 读写示例

通过末级成员名查找并读取：

```python
from scripts.base_tas import BaseTas

tas = BaseTas()
info = tas.resolve_variable("VehModMngtGlbSafe1UsgModSts")
print(info.name, hex(info.address), info.byte_size)

try:
    tas.connect()
    data = tas.read_variable_info(info)
    value = tas.read_variable_info_value(info)
finally:
    tas.disconnect()
```

通过完整路径写入：

```python
from scripts.base_tas import BaseTas

name = "kAdapterReadSignalDoc.flexray.VehModMngtGlbSafe1UsgModSts"
tas = BaseTas()
info = tas.resolve_variable(name)

try:
    tas.connect()
    tas.write_variable_info(info, 1)
    read_back = tas.read_variable_info(info)
finally:
    tas.disconnect()
```

也可以直接使用变量名接口：

```python
tas.read_variable("VehModMngtGlbSafe1UsgModSts")
tas.read_variable_value("VehModMngtGlbSafe1UsgModSts")
tas.write_variable("VehModMngtGlbSafe1UsgModSts", 1)
tas.write_variable_bytes("VehModMngtGlbSafe1UsgModSts", "01")
```

如果一个末级成员名匹配多个完整路径，`resolve_variable()` 会抛出歧义错误，此时应改用完整变量路径。

## 直接运行

```bash
python3 scripts/base_tas.py
```

当前 `base_tas.py` 底部调试入口会读取 `VehModMngtGlbSafe1UsgModSts`，写入递增值后再次读取。该操作会真实写 MCU 内存，运行前请确认目标变量允许被修改。

## DAS/TAS 环境

默认 DAS 路径：

```text
/opt/Tools/DAS/8.3.0
```

如需覆盖：

```bash
export DAS_HOME=/opt/Tools/DAS/8.3.0
```

`BaseTas.connect()` 会启动或连接 `tas_server`，打开 DAP/JTAG 端口，连接目标芯片，并初始化地址读写访问。

## 关键文件

- `scripts/base_tas.py`：最小 Python API，负责 JSON 生成/校验、变量查询、TAS 连接/断开、读写。
- `cpp/tc397_elfio_resolver.cpp`：C++ ELF/DWARF 解析器和 JSON 索引生成器。
- `scripts/build_tc397_elfio_resolver.sh`：C++ 解析器编译脚本。
- `docs/TC397_ELF_DAP_PATENT_DRAFT.md`：对应技术方案的发明专利交底草稿。
