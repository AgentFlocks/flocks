# 打包说明

本目录包含 **Windows 安装包（Inno Setup）** 相关脚本与配置。产物为 **`FlocksSetup.exe`**（安装向导），不是 PyInstaller 等单文件可执行程序。

## 目录结构

| 路径 | 说明 |
|------|------|
| `windows/build-staging.ps1` | 生成分发用的 **staging 目录**：下载并解压 uv、Node.js、Chrome for Testing，并 `robocopy` 复制仓库（不含 `.git`、`.venv`、`node_modules`）。**不包含预建 `.venv`**，安装后由 `scripts/install.ps1` 等完成引导。 |
| `windows/build-installer.ps1` | 一键：先跑 staging，再用 Inno Setup 编译安装包。 |
| `windows/flocks-setup.iss` | Inno Setup 6 工程文件；编译器为 `ISCC.exe`。 |
| `windows/bootstrap-windows.ps1` | 将已复制到目标机的 staging（含 `tools\`、`flocks\`）与用户环境衔接（PATH、`FLOCKS_*` 等），供安装后或手动场景使用。 |
| `windows/uninstall-flocks-user-state.ps1` | 由 Inno **`[UninstallRun]`** 在删除安装目录**之前**调用：优先 **`flocks stop`**，再 `taskkill` 兜底；从**用户** PATH 去掉**任意**位于 `{app}` 下的路径段（含 `bin`、`tools\uv`、`tools\node` 等）；删除指向本安装的 `%USERPROFILE%\.local\bin\flocks*`；按精确值清理用户级 `FLOCKS_*`；仅当 `AGENT_BROWSER_EXECUTABLE_PATH` 指向安装目录内文件时清除；删除桌面/开始菜单快捷方式；按需移除 `~/.flocks/browser/bundled` 联接。**不删除** `~/.flocks` 下用户数据（日志、workspace 等）。 |
| `windows/versions.manifest.json` | 锁定的 **uv / Node / Chrome for Testing** 版本，打 reproducible 包时在此升级。 |
| `windows/staging-layout.json` | staging 目录约定说明（机器可读摘要）。 |
| `windows/DOWNLOAD-HOSTING.txt` | 构建产物在 CI Artifact 与 GitHub Release 上的存放与保留策略说明。 |

## 本地打包前置条件

1. **Windows**，PowerShell 5+（脚本按 Windows PowerShell 编写）。
2. **网络**：staging 需从 GitHub、nodejs.org、Google 存储等下载工具链压缩包。
3. **Inno Setup 6** 已安装，且默认路径存在编译器：  
   `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`  
   若安装路径不同，调用 `build-installer.ps1` 时使用 `-InnoSetupCompilerPath` 指向你的 `ISCC.exe`。

## 推荐命令（仓库根目录）

**一键生成安装包**（staging 默认输出到仓库**上一级**目录下的 `agentflocks`，安装包输出到 `packaging/windows/Output/`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\windows\build-installer.ps1
```

常用可选参数：

| 参数 | 含义 |
|------|------|
| `-OutputDir` | staging 输出目录；不设则默认为 `{仓库父目录}\agentflocks`。 |
| `-RepoRoot` | 仓库根路径；默认即为当前仓库根。 |
| `-AppVersion` | 写入安装包的版本字符串（发版时与 tag 对齐）。 |
| `-CacheRoot` | 下载缓存根目录；不设则按 `build-staging.ps1` 内 `Resolve-CacheRoot` 规则解析（如环境变量 `FLOCKS_CACHE_ROOT`、父目录下已有 `flocks_deps`、`%LOCALAPPDATA%\flocks\cache` 等）。 |
| `-InnoSetupCompilerPath` | `ISCC.exe` 的完整路径。 |

**仅生成 staging、不编安装包**：

```powershell
.\packaging\windows\build-staging.ps1 -OutputDir C:\path\to\staging -RepoRoot $PWD
```

## 产物位置

- **安装包**：`packaging\windows\Output\FlocksSetup.exe`
- **Staging 根目录**：由 `-OutputDir` 或上述默认值决定，其下包含 `tools\`（uv、node、chrome）与 `flocks\`（仓库副本）等，详见 `staging-layout.json`。

## 版本与缓存

- 升级捆绑的 **uv / Node / Chrome for Testing**：编辑 `windows/versions.manifest.json` 中对应字段后重新打包。
- 重复打包时，已下载的 zip 会在 **CacheRoot** 下复用，可减少下载时间。

## CI

- **PR / 手动触发**：`.github/workflows/windows-packaging.yml` — 在 `windows-latest` 上安装 Inno Setup（Chocolatey），执行 `build-installer.ps1`，上传 **`FlocksSetup.exe`** 为 Artifact（保留天数见 workflow）。
- **打 tag 发版**：`.github/workflows/windows-packaging-release.yml` — 推送 `v*` 标签时构建安装包并作为 **GitHub Release** 资源上传。

更长期的下载与 Artifact 过期策略见 `windows/DOWNLOAD-HOSTING.txt`。

## 安装后说明

安装程序会向用户环境写入 `FLOCKS_INSTALL_ROOT` 等变量；**安装完成后需新开终端**，再执行 `flocks start` 等命令，以便新进程继承 PATH 与相关环境变量（Inno 向导结束页亦有英文/中文提示）。

## 卸载说明

通过系统「应用和功能」或 Inno 卸载程序卸载时，会执行 `uninstall-flocks-user-state.ps1`，**先**在安装目录仍存在时运行 **`flocks stop`**，再对仍存活的 PID 做强制结束；并与 `flocks-setup.iss` 中 **`[Registry]`** 的 `uninsdeletevalue`（`FLOCKS_INSTALL_ROOT` / `FLOCKS_REPO_ROOT` / `FLOCKS_NODE_HOME`）一起，清理安装时写入的用户级环境。**用户 PATH** 中凡是以 `{app}\` 为前缀的目录（含 `bootstrap-windows.ps1` 写入的 `tools\uv`、`tools\node`，以及可能出现的 `{app}\bin` 等）均由卸载脚本**整段移除**；`Path` 本身无法靠 `uninsdeletevalue` 自动还原，必须脚本处理。

**不会**删除 **`%USERPROFILE%\.flocks`** 目录（用户数据）；仅删除安装期创建的 **`browser\bundled`** 目录联接（若存在）。

**不会**删除整个 `%USERPROFILE%\.local\bin` 目录或从 PATH 中整体移除该目录（避免影响用户在同一目录下的其他工具）；仅当 `flocks.cmd` / `flocks.exe` 内容包含当前安装根路径时，才删除这些包装文件。

卸载完成后请**新开终端**，以便进程看到更新后的 PATH 与环境变量。

卸载时会删除**桌面**上的 `Flocks.lnk`（若安装时勾选了桌面快捷方式）以及「开始」菜单程序组 `Flocks` 下的快捷方式：`[UninstallDelete]` 与 `uninstall-flocks-user-state.ps1` 中的 `Remove-FlocksShellShortcuts` 互为补充（含 OneDrive 重定向后的桌面路径）。
