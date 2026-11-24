# 迁移环境预检

每次新迁移在第 1 步执行，中途接续按下一步所需检查。可以先只读识别项目、宿主、路径和已有记录。普通环境检查须通过，Windows 新迁移还需满足下文的外部硬链接检查和授权条件，才为迁移写入记录、创建副本、改名、建链接或更改宿主配置。

## 用本机命令确定系统、终端和 Python

1. 从宿主的本机执行环境核对系统。Windows 和 macOS 都须直接操作这台电脑的本地盘。仅能运行 Linux、WSL、远程环境或不能访问本机时，报告阻塞并说明需要切换到哪个本机执行环境。
2. Windows 用已找到的 Windows PowerShell 5.1 或 PowerShell 7，以 `-NoProfile -NonInteractive -File` 启动 `scripts/preflight.ps1`。如果搜索路径里找不到，还可只读核对系统自带的 PowerShell 位置和已安装的 PowerShell 7。两者都不可用或现有策略阻止运行脚本时，由宿主报告具体原因。macOS 用 `/bin/sh` 执行 `scripts/preflight.sh`，并依据结果核对本机 Terminal 和所需系统工具。脚本均使用本次加载的 Skill 目录下的绝对路径，从项目树外执行。
3. 读取脚本标准输出的 JSON 和退出状态。接受实际可启动的 CPython 3.12+，使用输出的 Python 可执行文件绝对路径执行后续脚本。解释器和 Skill 须位于本次源、目标与备份目录之外。在 Windows 避免启动可能触发 Store 或自动装运行时的 Python 别名。Mac 上 Apple 的开发工具占位命令也可能触发安装对话框，由预检排除未安装开发工具支撑的占位命令。

## 核对选中的源和目标

使用通过检查的解释器，保持后续脚本执行环境与预检一致。下方仅展示脚本参数。实际调用时在前面加解释器的绝对路径与 `-B`，将脚本名和参数换成完整路径，并逐个传参。

```text
preflight.py --source OLD --target NEW
preflight.py --source OLD --target NEW --inspect-hardlinks
preflight.py --resume --source BACKUP --target NEW
preflight.py --resume --target NEW --git-root NEW
```

- macOS 新迁移用第一行，两端须为本地已挂载、可写、已启用所有权的 APFS。Windows 新迁移用第二行，两端须为本地 NTFS。目标项目目录尚未创建时，检查其已存在的父目录和所属卷。盘未挂载时让用户重新连接并确认身份，或更换目标，再复检。
- Git 检查仅适用于识别为 Git 的来源。脚本核验的是当前进程能调用、后续基线脚本也会使用的 Git。普通文件夹跳过。报告要求调整项目范围或核对 Git 结构时，回到原流程处理仓库子目录、工作树或其他特殊结构。
- 批次中按已选物理目录去重，对每个实体及其目标执行对应系统的新迁移命令。相同的环境安装缺项可合并报告。遇到已存在未知目标、源已改名或成了链接时，先只读核对，再按接续方式检查已确认的实体。
- 接续时使用 `--resume`，只传下一步实际使用且身份已核实的实体路径。旧位置已是联接或符号链接时，从记录和现场确认目标及适用的实体备份。需要 Git 的步骤才传 `--git-root`。只读取记录时可以先做环境检查并确认现场，不需要为缺失的旧路径或已移除的备份运行新迁移检查。
- 检查器仅在标准输出中返回观察结果。读取 `checks` 中各项状态和原因，区分通过、尚待检查、缺失、不兼容、阻塞和不适用。普通环境检查通过或不适用后，按下面的专项结果决定能否继续。路径未明确的项目盘仍待核对。后续保留卷身份检查、容量估算、文件核验和 Git 基线。

## Windows 新迁移的外部硬链接

`--inspect-hardlinks` 在建立迁移记录前检查已选物理来源。它读取文件身份、链接计数和用于复查稳定性的元数据，不读取文件内容或跟随目录联接。按来源报告 `external_groups`（外部组数）、`external_entries`（涉及的项目内路径数）和少量相对路径示例。授权范围要求一组的每个项目内成员都位于 `node_modules` 目录下。

| `assessment` | 结果与下一步 |
|---|---|
| `none` | 没有外部组。其他适用检查通过后，可以建立迁移记录。 |
| `authorization_required` | 待授权，退出码为 1。若其他适用检查通过，先说明该来源的组数、路径数和复制独立副本的影响，取得用户明确授权后，单目录用 `run_record.py start --materialize-dependency-hardlinks` 记录，批次只在获准来源的 `selection` 中设 `materialize_dependency_hardlinks: true`，再建立记录。软件检查结果仍保留为待授权，无需重跑预检或修改它的输出。 |
| `outside_scope` | 存在超出授权范围的组。报告对应来源和示例，暂停迁移。 |
| `incomplete` | 路径未核实、无法读取元数据或扫描期间发现变化。按报告核对原因，复查该来源后再决定。 |

正式复制和验收仍核对当时的现场和已有授权。接续按原流程检查，不增加这次新迁移预检。

## 用户同意后才安装，安装后复检

遇到缺失或不兼容的软件，汇总已确认的适用缺项、能核实的实际版本及待检查项。先确认包管理器已经可用，再说明确切软件包、来源、拟执行命令、用户级或系统级范围，以及可能的依赖、权限提示或 PATH 影响。让用户明确同意整份清单、指定条目，或选择自行安装。用户只同意指定条目时，仅执行这些命令。仅同意迁移、回答含糊、拒绝或尚未回答时，保留现状。工具阻塞、安装范围改变或要更换渠道时，先说明变化并让用户确认更新后的方案。

| 系统和缺项 | 已有对应包管理器时可提出的命令 | 需要用户手动安装时 |
|---|---|---|
| Windows · CPython | `winget install --id Python.Python.3.14 --exact --source winget --scope user` | [Python 官方下载](https://www.python.org/downloads/windows/)，选择本机可用的稳定 CPython 3.12+ |
| Windows · Git | `winget install --id Git.Git --exact --source winget` | [Git for Windows 安装程序](https://git-scm.com/install/win) |
| Windows · PowerShell | `winget install --id Microsoft.PowerShell --exact --source winget` | [Microsoft 的 PowerShell 7 安装程序](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows) |
| macOS · CPython | `brew install python@3.14`，复检 Homebrew 前缀下的 `bin/python3.14` | [Python 官方 macOS 安装包](https://www.python.org/downloads/macos/)，选择本机可用的稳定 CPython 3.12+ |
| macOS · Git | `brew install git` | [Apple 命令行工具](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools)，由用户运行 `xcode-select --install` 并完成系统对话框 |

自动安装仅使用已可用的 WinGet 或 Homebrew，逐项运行获准的命令。WinGet 命令未指定 `--scope` 时，根据本机匹配的安装器说明安装范围和是否可能触发 UAC。用进程环境设置 `HOMEBREW_NO_AUTO_UPDATE=1` 和 `HOMEBREW_NO_INSTALL_CLEANUP=1`，避免触发例行更新和清理。由用户本人处理许可、UAC 和管理员提示。避免自动接受许可条款、静默提权、全量更新或永久改动环境设置。没有包管理器、需要宿主无法完成的交互或系统自带终端不可用时，给出官方手动入口或恢复本机执行能力的步骤。系统不支持、卷格式不符、盘只读或权限不足时，报告实际原因并说明如何恢复或更换位置。

安装后在新的本机进程中复检，需要时让用户重开终端或 Agent 来获得更新后的 PATH。如果解释器位于待迁目录，退出该项目的虚拟环境，再从项目外运行原生预检。采用测试通过的 Python 绝对路径，确认适用的 Git 可在后续命令的环境中运行。安装失败或复检仍不通过时，报告实际错误、保留现场并停在当前步骤。全部适用项通过后，再按单目录、批次或接续流程继续。
