# ccfc 使用 stepcode claude 启动说明

## 背景

当前安装的 ccfc 是 `@hyposomnia/cc-feishu-connector`。这个版本的配置只识别 `feishu.*`、`defaults.model`、`proxy.url` 等字段，不会读取：

```toml
[claude]
command = "stepcode claude"
```

ccfc 内部默认会在 `dist/cli.js` 里执行：

```js
spawn(this.claudeBin, args, ...)
```

也就是最终走 PATH 中的 `claude`。为了强制让飞书机器人会话从 `stepcode claude` 进入，需要把这个启动点改为：

```js
spawn("/home/shiheping/.local/bin/stepcode", ["claude", ...args], ...)
```

补丁还会打印一行启动日志：

```text
Launching Claude via stepcode claude ...
```

## 一键配置

Linux / WSL：

```bash
bash /home/shiheping/QianLiPrj/TC397Tools/scripts/setup_ccfc_stepcode.sh
```

Windows 原生环境，不需要 WSL：

```bat
TC397Tools\scripts\setup_ccfc_stepcode.bat
```

这个 bat 会调用同目录下的 PowerShell helper：

```text
TC397Tools\scripts\setup_ccfc_stepcode_windows.ps1
```

它会在 Windows 的 npm 全局安装目录中查找 `@hyposomnia/cc-feishu-connector\dist\cli.js`，并把 ccfc 的 Claude 启动点改为通过 Windows 中的 `stepcode` 启动。

## 检查状态

Linux / WSL：

```bash
bash /home/shiheping/QianLiPrj/TC397Tools/scripts/setup_ccfc_stepcode.sh --check
```

Windows 原生：

```bat
TC397Tools\scripts\setup_ccfc_stepcode.bat --check
```

如果显示：

```text
PATCHED: ccfc launches /home/shiheping/.local/bin/stepcode claude
```

说明配置已生效。

## 启动 ccfc

配置完成后仍然正常启动 ccfc：

Linux / WSL：

```bash
ccfc start /home/shiheping/QianLiPrj/TC397Tools/config.toml
```

Windows 原生：

```bat
ccfc start C:\path\to\config.toml
```

然后在飞书中发送 `/start <项目路径>`。真正启动 Claude 会话时，ccfc 终端应出现：

```text
Launching Claude via stepcode claude ...
```

注意：进程列表里后续仍可能看到 `claude`，这是因为 `stepcode claude` 内部可能再启动或 exec Claude 二进制；关键是 ccfc 的入口已经是 `stepcode claude`。

## 回退

脚本会生成备份文件：

```text
.../dist/cli.js.bak-stepcode-YYYYMMDD-HHMMSS
```

回退到最近一个与当前 `cli.js` 内容不同的备份：

Linux / WSL：

```bash
bash /home/shiheping/QianLiPrj/TC397Tools/scripts/setup_ccfc_stepcode.sh --restore
```

Windows 原生：

```bat
TC397Tools\scripts\setup_ccfc_stepcode.bat --restore
```

## 常见情况

如果 `npm update -g @hyposomnia/cc-feishu-connector` 后补丁失效，重新执行一键配置脚本即可。

如果 `stepcode` 或 `ccfc` 不在 PATH，可以显式指定：

```bash
STEPCODE_BIN=/home/shiheping/.local/bin/stepcode \
CCFC_BIN=/home/shiheping/.npm-global/bin/ccfc \
bash /home/shiheping/QianLiPrj/TC397Tools/scripts/setup_ccfc_stepcode.sh
```

如果已知 `cli.js` 绝对路径，也可以直接指定：

```bash
CCFC_CLI_JS=/home/shiheping/.npm-global/lib/node_modules/@hyposomnia/cc-feishu-connector/dist/cli.js \
bash /home/shiheping/QianLiPrj/TC397Tools/scripts/setup_ccfc_stepcode.sh
```

Windows 原生环境中也可以显式指定：

```bat
set STEPCODE_BIN=C:\Users\yourname\AppData\Roaming\npm\stepcode.cmd
set CCFC_CLI_JS=C:\Users\yourname\AppData\Roaming\npm\node_modules\@hyposomnia\cc-feishu-connector\dist\cli.js
TC397Tools\scripts\setup_ccfc_stepcode.bat
```
