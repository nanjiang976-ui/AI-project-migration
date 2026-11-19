# AI 项目迁移助手

[English](README.en.md) · [快速安装](#install) · [第一次使用](#first-run) · [兼容性](#compatibility) · [常见问题](#faq)

把本地 AI 项目从这台电脑的一个存储卷搬到另一个卷。助手先复制并核对项目，再在原名位置留下一个链接，让仍使用旧路径的聊天可以访问新文件。它会逐步说明你需要做什么、验证了什么，以及哪份备份可以手动清理。

**例如**，Windows 可以从 `E:\Projects\Demo` 迁到 `F:\Work\Demo`，Mac 可以从 `/Users/example/Projects/Demo` 迁到 `/Volumes/Work/Projects/Demo`。实际路径由你选定。安装文件夹名和 Codex 调用名仍为 `codex-project-migration`，已安装旧版的用户可在同一位置更新。

## 我什么时候需要它？

- **存放项目的卷快满了。** 复制范围包括隐藏文件、空目录、未提交的素材和本地工具目录。把两个目录放在同一个卷内只会改变位置，无法给该卷腾出空间；助手检查卷身份后会请你确认是否仍要继续。
- **以前的聊天还需要修改项目。** Windows 用目录联接，macOS 用目录符号链接，把旧地址连接到新位置。最终会分别检查原会话的恢复、默认工作目录的实际读写以及旧入口本身。
- **项目还有 Git 工作树。** 工作树是同一仓库的独立工作副本。助手记录它们的提交和未提交状态，迁移后再核对连接；普通文件夹也适用。

<a id="compatibility"></a>

## 兼容性和实测范围

下表中，“文件迁移”指本地文件系统与 Git 测试；“旧会话”单独核验原会话身份和双向读写。下面的结果用于选择工具，你自己的项目仍有单独的验收单。

| 执行环境 | 文件迁移 | 旧会话与使用方式 |
|---|---|---|
| Windows · Codex | 本地 NTFS 跨卷已实测 | 原 Codex 主目录旧聊天已有真实读写验收；可读取项目历史，桌面工具缺失时由用户配合 |
| Windows · Claude Code | 使用相同的已测 NTFS 文件流程 | Claude Code 2.1.150 已在虚构项目中恢复同一真实测试会话，并从其默认目录及旧入口完成双向读写；新会话从目标目录启动也已验证 |
| Windows · 其他本地 Agent | 使用相同的已测 NTFS 文件流程 | 手动选路径、从新位置打开项目并在原聊天中测试；结果按宿主实际能力记录 |
| macOS · Codex | 两个 APFS 测试卷已在原生 macOS CI 验证 | macOS 上的真实 Codex 旧聊天需在宿主核验 |
| macOS · Claude Code | 两个 APFS 测试卷已在原生 macOS CI 验证 | 提供 Claude Code 安装和恢复流程；macOS 上的真实 Claude 旧会话尚未实测 |
| macOS · 其他本地 Agent | 两个 APFS 测试卷已在原生 macOS CI 验证 | 通用引导；需要能够在本机运行命令，旧聊天手动核验 |

Windows 跨卷测试使用同一块 SSD 的不同 NTFS 分区。macOS 原生 CI 使用两个独立挂载的 APFS 镜像卷；镜像测试覆盖文件、元数据、工作树、符号链接和读写。[查看原生验收记录](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/actions/runs/44454723501)。两块独立物理硬盘、Linux、网络共享、跨电脑及换系统迁移均未纳入这份实测记录。[开发测试](tests/) · [macOS 原生测试工作流](.github/workflows/macos-apfs.yml)

**谁来搬、谁保存着聊天？** 例如，Claude Code 可以帮助复制一个含 Codex 聊天的项目，但那些聊天仍须在 Codex 中验证。没有批量历史接口时会标明“历史未能批量核验”；一份文件复制成功报告只证明文件方面的结果。[各宿主的操作与验收](skills/codex-project-migration/references/hosts.md)

### 开始前准备

- **Windows**：源卷和目标卷均为已连接的本地 NTFS，安装 CPython 3.12+、Windows PowerShell 5.1 或 PowerShell 7。可在 PowerShell 输入 `python --version` 查看 Python 版本。
- **macOS**：选择本机已挂载、可写且启用了所有权的 APFS 源卷和目标卷，准备 CPython 3.12+ 和终端。用 `python3 --version` 查看版本，外接卷在迁移及后续使用项目时都要保持可用。
- Git 项目另需 Git（`git --version`）。保存文件、暂停会改写项目的任务，为完整副本预留空间。给出尚未占用的目标项目路径，让助手创建它。

<a id="install"></a>

## 快速安装

1. 点 **Code → Download ZIP** 或[下载 main 分支 ZIP](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/archive/refs/heads/main.zip)。Windows 右键选择“全部解压缩”；macOS 可在访达中双击解压。
2. 找到解压后的 `skills/codex-project-migration`。复制这个完整文件夹，打开它应该直接看到 `SKILL.md`、`agents`、`references` 和 `scripts`。
3. 按使用的 Agent 选择个人安装目录。若目录不存在，可以先创建。安装位置应在待迁移的项目之外。

| 使用者 | Windows 目标文件夹 | macOS 目标文件夹 |
|---|---|---|
| Codex | `%USERPROFILE%\.agents\skills\codex-project-migration` | `~/.agents/skills/codex-project-migration` |
| Claude Code 本地 CLI | `%USERPROFILE%\.claude\skills\codex-project-migration` | `~/.claude/skills/codex-project-migration` |
| 其他可执行本地命令的 Agent | 使用宿主支持的个人 Skill 目录，或放在固定工具目录 | 同左；向助手提供 `SKILL.md` 的完整路径 |

4. 确认目标文件夹中直接有 `SKILL.md`，然后重开所用的本地 Agent。例如 Codex 在 Windows 的入口是 `%USERPROFILE%\.agents\skills\codex-project-migration\SKILL.md`。已经使用 `.codex/skills` 并能识别它的 Codex 环境可沿用该位置，同一宿主只选一处。[Codex Skill 目录](https://learn.chatgpt.com/docs/build-skills) · [Claude Code Skills](https://code.claude.com/docs/en/skills)

<a id="first-run"></a>

## 第一次使用

**Codex** 中粘贴：

```text
$codex-project-migration 帮我迁移一个本地项目。
```

**Claude Code 本地 CLI** 中输入：

```text
/codex-project-migration 帮我迁移一个本地项目。
```

如果使用其他 Agent，让它读取安装好的 `SKILL.md` 并按照七步流程引导你。没有指定项目时，助手会先问 **“你要迁移哪个本地项目？”**。也可以直接说：“我想把 Demo 迁到另一块盘，旧聊天在 Claude Code。请先让我核对两个完整路径和七步分工。” 请核对同名项目和卷名后再开始。

## 迁移时，我要做什么？

| 步骤 | 助手做什么 | 你做什么 | 成功标志 |
|---|---|---|---|
| 1. 核对项目 | 确认宿主、两端路径与卷身份，记录聊天和工作树基准 | 选项目与目标，保存文件，暂停写入 | 路径、容量、基准与可核验范围明确 |
| 2. 复制 | 完整复制并保留源实体 | 正常无需操作 | 有目标副本和本次复制记录 |
| 3. 核验副本 | 逐文件 SHA-256，核对目录、权限、硬链接及平台元数据 | 源还在变化时暂停写入 | 严格比较通过 |
| 4. 从新位置打开 | Codex 在原项目换源；CLI 或通用 Agent 核对新会话的默认目录；按需重连工作树 | 按所属宿主打开或确认 | 目标实体确实用于新工作，原项目/工作树对应关系可核对 |
| 5. 保留旧路径 | 根据选项给出一段填好真实路径的命令 | **退出前选 A/B**，退出占用程序，从项目外打开独立 PowerShell 或 Mac 终端操作 | 原名为正确链接，备份仍是原实体 |
| 6. 验证旧聊天 | 核对链接、原会话身份和实际双向读写 | **重开且链接通过后选 A/B**，使用迁移前已有的一条旧聊天 | 目标文件与旧入口访问同一对象，临时文件已清理 |
| 7. 最终复核 | 分别列出聊天、工作树、源盘备份及三条准确路径 | 验收后自行决定是否手动清理备份 | 所有差异已解释，旧入口继续保留 |

**第 5 步的两个选项**：A 是你在资源管理器或访达中手动改名，再粘贴命令建立链接；B 是命令完成改名、建立链接和检查。退出前将所选命令存到文本编辑器。Windows 结果会显示 `Junction`，macOS 显示 `SymbolicLink`，`Target` 应为准确的目标项目路径。重开后告诉助手“联接已建立”。

**第 6 步的两个选项**：A 是你把提示词贴进原项目的旧聊天并回报结果；B 是由助手验证当前符合条件的旧聊天。跨聊天代发仅在所属宿主具备该能力、你选定旧聊天并明确授权后使用；否则由你手动转交。Claude Code 先尝试 `claude --resume <原会话ID>`。Windows 实测中，2.1.150 换根后仅凭 ID 没有找到原记录；用 `claude --resume <原会话.jsonl的绝对路径>` 恢复后，宿主返回同一 ID、记得原会话专有的测试标记，并从默认目标目录通过探针验证了旧入口和目标实体。这个测试也单独确认了 `/codex-project-migration` 能在隔离项目启动并询问项目名。无需上传或改写聊天记录。[宿主操作与证据](skills/codex-project-migration/references/hosts.md) ·[Claude Code 会话恢复](https://code.claude.com/docs/en/cli-reference)

## 迁完以后，哪个文件夹可以删？

> [!CAUTION]
> **⚠️ 请勿删除原位置下的原名文件夹。** Windows 的这个入口是目录联接，macOS 上是目录符号链接。旧聊天依靠它找到新地址。删除入口会使旧路径失效；**进入其中删除文件，会直接影响目标盘上的项目文件。**

以下为虚构的 `Demo` 示例。最终报告会把对应的三条完整路径写给你。

```text
Windows
E:\Projects\Demo                    原名目录联接 → F:\Work\Demo           必须保留
F:\Work\Demo                         目标项目实体                          必须保留
E:\Projects\Demo_迁移备份             源盘实体备份                          验收通过后可手动清理

macOS
/Users/example/Projects/Demo         原名符号链接 → /Volumes/Work/Projects/Demo   必须保留
/Volumes/Work/Projects/Demo          目标项目实体                                必须保留
/Users/example/Projects/Demo_迁移备份  源盘实体备份                                验收通过后可手动清理
```

**⛔ 暂勿清理备份**：有源盘独有文件、内容差异未解释，或有关核验尚未完成时，保留备份。助手不会替你批量删除文件夹。

## 怎样知道真的成功？

一份**虚构验收单**可以是：120 个文件的 SHA-256 均匹配；有接口可读的 4/4 条旧聊天成功打开；其中 1 条按原 ID 恢复，并从默认目录和新位置对同一文件完成双向读写；1/1 个 Git 工作树连接和改动清单匹配；备份独有文件和未解释的差异均为 0。报告将历史可读数量与真正写过测试文件的旧聊天数量分开列出。macOS 或已重绑的宿主可能显示解析后的目标路径；原会话身份、实际默认目录和旧链接仍分别检查。

<a id="faq"></a>

## 常见问题

**原目录提示“正在使用”？** 保存文件、退出相关 Agent（Windows 桌面应用也要退出托盘后台），关闭以项目为当前目录的终端，然后从项目外运行已准备的命令。如果仍被占用，保留现场并告诉助手具体报错。

**外接卷未挂载或目标卷变化了？** 重新挂载并核对卷身份与目标位置。不要在消失的挂载点目录里创建一个临时替代项目；助手会暂停切换。

**中断后怎样继续？`.migration-checks` 是什么？** 保留两端目录和本机审计目录，重新打开后说“继续上次迁移”。检查点、文件清单和本次证据用于核实实际状态。审计目录可能含本地路径、会话 ID；反馈问题时先脱敏。

**为什么工作树仍在旧卷？** 独立工作副本可以保留原位置和各自未提交的内容。助手会核对它们指向新主仓库；工作树聊天继续编辑对应的独立副本。

**有些文件为什么会暂停？** 源卷和目标卷均须使用支持的本地文件系统。无法核验的权限、云占位、依赖树外对象的链接等需要另行处理。macOS 还要核对 ACL、扩展属性（含资源分支）、文件标志和名称碰撞。助手保留原件并说明具体原因。更多细节见[复制与接续](skills/codex-project-migration/references/operations.md)和[聊天验收](skills/codex-project-migration/references/chats.md)。

## 反馈与许可

提 Issue 时附操作系统、Agent、Python 版本、失败步骤与脱敏后的错误描述。使用虚构的路径或项目名，聊天正文、真实会话 ID、完整文件清单和迁移报告请留在本机。[提交 Issue](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/issues)

本项目使用 [MIT 许可证](LICENSE)。
