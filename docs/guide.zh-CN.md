# AI 项目迁移助手使用指南

[返回首页](../README.md) · [English](guide.en.md) · [兼容性](#compatibility) · [ZIP 安装位置](#zip) · [七步流程](#steps) · [常见问题](#faq)

<a id="compatibility"></a>

## 适用环境与实测范围

这个 Skill 处理**同一台电脑、同一操作系统**内的本地项目。适合项目盘空间不足、旧聊天还要编辑项目，或必须保留 Git 工作树及未提交文件的情况。Windows 的示例可以是 `E:\Projects\Demo` 搬到 `F:\Work\Demo`；Mac 可以从 `/Users/example/Projects/Demo` 搬到 `/Volumes/Work/Projects/Demo`。你可以选择自己的完整路径。

| 系统与 Agent | 项目文件 | 旧聊天 |
|---|---|---|
| Windows · Codex | 本地 NTFS 跨盘复制和 Git 已实测 | 原先使用项目主目录的真实聊天已验证双向读写；连接器不可用时由用户核对 |
| Windows · Claude Code | 使用相同的已测 NTFS 流程 | 2.1.150 已在虚构项目中恢复同一测试会话，完成双向读写，并验证新会话从目标目录启动 |
| Windows · 其他本地 Agent | 可调用同一组文件和 Git 助手 | 由用户指定路径，在原聊天中测试；报告列出实际验证范围 |
| macOS · Codex | 原生 macOS CI 已验证两个 APFS 测试盘 | macOS 上的真实 Codex 旧聊天尚待宿主实测 |
| macOS · Claude Code | 使用相同的已测 APFS 流程 | 提供安装和会话恢复指引；macOS 上的真实 Claude 旧会话尚未实测 |
| macOS · 其他本地 Agent | 可调用同一组文件和 Git 助手 | 手动打开目标项目并用原聊天测试；未取得的证据会标明 |

Windows 跨盘测试使用同一块 SSD 上的两个独立 NTFS 分区。macOS 在原生 runner 上挂载两个独立 APFS 磁盘镜像，实际检查文件、元数据、链接、工作树和读写。Linux、不同电脑间的迁移、网络共享以及两块独立物理硬盘均未纳入这份实测记录。[macOS 原生验收](https://github.com/nanjiang976-ui/AI-project-migration/actions/runs/44457680917) · [开发测试说明](../tests/README.md)

如果由 Claude Code 引导迁移一个 Codex 项目，旧聊天仍应在 Codex 中验证。没有历史列表接口时，助手会说明“历史未能批量核验”，单列已实测的那条聊天。[各宿主的能力与验收](../skills/ai-project-migration/references/hosts.md)

### 迁移前的准备

- **Windows**：准备两个本地 NTFS 位置、CPython 3.12 或更新版本，以及 Windows PowerShell 5.1 或 PowerShell 7。可用 `python --version` 检查 Python。
- **macOS**：源盘和目标盘应为本地已挂载、可写且已启用所有权的 APFS 盘。准备 Terminal 和 CPython 3.12+（`python3 --version`）。使用迁移后的项目时，外接目标盘需要保持连接。
- **Git 项目**：用 `git --version` 检查 Git。保存打开的文件，暂停会继续写入项目的任务。项目需要一个尚未占用的目标文件夹名，并为完整副本预留空间。在同一个盘内移动不能给该盘腾出空间，助手会请你确认是否继续。

<a id="zip"></a>

## 手动安装时应该放在哪里？

[下载 ZIP](https://github.com/nanjiang976-ui/AI-project-migration/archive/refs/heads/main.zip)，在 Windows 右键“全部解压缩”，或在 macOS 用访达双击解压。进入解压后的仓库，找到 `skills/ai-project-migration`。复制**整个**文件夹，放到一个稳定的个人技能目录，避免放在准备搬走的项目里。

| Agent | Windows 安装完成后的文件夹 | macOS 安装完成后的文件夹 |
|---|---|---|
| Codex | `%USERPROFILE%\.agents\skills\ai-project-migration` | `~/.agents/skills/ai-project-migration` |
| Claude Code 本地 CLI | `%USERPROFILE%\.claude\skills\ai-project-migration` | `~/.claude/skills/ai-project-migration` |
| 其他可执行本地命令的 Agent | 宿主支持的个人技能目录，或一个固定工具目录 | 同左；需要时让 Agent 读取 `SKILL.md` 的完整路径 |

Windows 可以把父目录粘贴到资源管理器地址栏；macOS 在访达按 `⌘⇧G` 打开个人技能目录。父目录不存在时先创建。核对路径为 `ai-project-migration/SKILL.md`，同级应有 `agents/`、`assets/`、`references/` 和 `scripts/`。同一个 Agent 只安装一份。若当前 Codex 已使用并能发现 `.codex/skills`，手动安装时可沿用该位置。重开 Agent 后按[首页的首次使用示例](../README.md#first-run)检查入口。[Codex 官方目录说明](https://learn.chatgpt.com/docs/build-skills) · [Claude Code Skills](https://code.claude.com/docs/en/skills)

### 为什么 CLI 更新沿用原安装命令？

`-g` 表示个人范围，`--skill ai-project-migration` 选定技能，`-a codex` 或 `-a claude-code` 选定宿主。在隔离测试里，对旧版重跑相同宿主的 `add` 安装命令会更新到新版，并保持原安装范围。通用安装器的 `update` 操作只按技能名筛选时，还可能在其他已识别宿主或共享目录写入副本。因此若只想更新原来的那一份，按[首页的更新方法](../README.md#update)操作。

<a id="steps"></a>

## 单目录迁移的七个步骤

Codex 需要搬多个已选来源，或同一目录被多个项目共享时，可以使用[多源批次流程](../skills/ai-project-migration/references/batch.md)。先确认完整引用和目标。共享实体只复制一次，原名入口接好后逐项目换源。正在进行的单目录迁移仍从原检查点接续。

| 步骤 | 助手做什么 | 你做什么 | 成功标志 |
|---|---|---|---|
| 1. 确认项目 | 识别旧聊天所属的 Agent，核对源路径、目标路径和空间，记录聊天与 Git 基准 | 选定项目和目标盘，保存并暂停编辑 | 双方可以准确指出要迁移的项目和要验收的对象 |
| 2. 复制完整项目 | 复制目录、隐藏文件和未提交素材，保留原件 | 正常情况下无需操作 | 目标副本和本次复制记录可读 |
| 3. 独立核验 | 逐文件核对 SHA-256、空目录、硬链接、权限和系统元数据 | 若还有新写入，保存后暂停 | 严格比较通过 |
| 4. 改用新目录 | 在原 Codex 项目换源，或按其他宿主方式从目标处启动新会话；检查 Git 工作树连接 | 接口不足时按提示操作 | 新工作使用目标位置，项目和工作树身份已核实 |
| 5. 保留旧地址 | 只给出你选定方式的一段命令，预填完整路径 | **退出前选 A/B**，退出占用程序，再从项目外打开终端执行 | 旧入口指向正确目标，备份仍是原件 |
| 6. 测一条原聊天 | 检查链接、原会话身份和默认目录双向读写 | **重开且链接正确后选 A/B** | 原聊天通过默认目录读写到目标文件，临时文件已清理 |
| 7. 收尾 | 分别报告历史读取、实际写入、Git 工作树和备份的检查结果 | 验收后决定何时手动清理备份 | 旧入口可用，备份独有或不同的内容已保全 |

**第 5 步**。退出前，助手会交付你选定方式的一段命令和对应系统的终端图示。先保存命令，再退出占用项目的程序。Windows 桌面应用也要退出托盘后台。选 A 时，先在资源管理器或访达里按指定名称改名，再到项目外的独立终端运行命令建链接。选 B 时，直接到独立终端运行命令，由它完成改名、建链接和检查。重开后说“联接已建立”，助手会核对实际状态并接续。[Windows 图示](../skills/ai-project-migration/assets/open-terminal-windows.png) · [macOS 图示](../skills/ai-project-migration/assets/open-terminal-macos.png)

**第 6 步**：A 是你把专用提示词贴到原项目的一条旧聊天，再告诉助手结果；B 是让助手自动验证。若需代发到另一条聊天，要先选定并明确授权。测试从该聊天默认目录创建随机文件，核对原名入口、目标文件身份和双向读写，再清理临时文件。恢复后的目录有时会显示新地址，原会话身份与实际写入结果分别核验。

## 迁完后怎么认出该留的文件夹？

> [!CAUTION]
> **⚠️ 请勿删除原位置下的原名文件夹。** 请放心，保留这个入口几乎不占用硬盘空间。删除这个入口会使旧地址失效；进入其中删除文件会影响目标盘上的实际项目。

```text
Windows 示例
E:\Projects\Demo                      原名目录联接 → F:\Work\Demo        保留
F:\Work\Demo                          正在使用的项目实体                保留
E:\Projects\Demo_迁移备份              迁移前的原件                      验收后可手动清理

macOS 示例
/Users/example/Projects/Demo           原名符号链接 → /Volumes/Work/Projects/Demo   保留
/Volumes/Work/Projects/Demo            正在使用的项目实体                            保留
/Users/example/Projects/Demo_迁移备份    迁移前的原件                                  验收后可手动清理
```

**虚构验收单示例**：120 个文件的 SHA-256 一致；4 条可查询历史全部能读到原回合；其中 1 条原会话完成了实际读写；1 个工作树的连接、分支和未提交状态都匹配；原盘备份独有文件和未解释差异均为 0。报告会分别给出这些数字和三条实际路径。如果出现 **⛔ 暂勿清理备份**，保留备份，先处理列出的差异。

<a id="faq"></a>

## 常见问题

**改名时显示“正在使用”怎么办？** 保存文件，退出占用该项目的 Agent 或编辑器；关闭当前目录在项目里的终端。把报错交给助手，先核对改名或建链接做到哪一步。确认需要重试时，再从项目外打开独立 PowerShell 或 Mac 终端，执行本次准备的命令。

**机器重启了，或只完成了改名怎么办？** 保留源、目标和 `.migration-checks`。重开后说“继续上次迁移”。助手会依据检查点核对目录是否已改名、链接是否已建立，以及下一步由谁操作。

**原聊天还显示旧地址，或者 Claude Code 找不到会话怎么办？** 原地址可通过链接读写新文件。先核对旧会话的真实身份，再进行读写测试。Claude Code 可先用 `claude --resume <原会话ID>`；Windows 上测试的 2.1.150 在换根后需要用 `claude --resume <该原会话.jsonl的绝对路径>` 恢复。请把聊天记录留在本机。[宿主操作手册](../skills/ai-project-migration/references/hosts.md) · [Claude Code 恢复说明](https://code.claude.com/docs/en/cli-reference)

**外接盘断开了怎么办？** 重新连接、挂载同一个盘，核对硬盘身份和项目路径后再继续。不要在原挂载点新建同名项目充当目标。

**为什么 Git 工作树还留在源盘？** 本次搬的是主仓库。独立工作副本可以继续放在原位置，助手会检查它们的连接及未提交改动。工作树聊天仍编辑各自的工作副本。

**为什么碰到云占位文件或特殊属性就会暂停？** 助手需要准确保留权限、元数据、内部硬链接和文件内容。无法读取的数据、指向项目外的链接或目标盘不能表示的属性要单独核对，不能直接跳过。

**`.migration-checks` 能发到 GitHub 帮忙排错吗？** 里面可能有真实文件路径、会话 ID、哈希清单和恢复证据。保留在本机，提 Issue 时只附系统、Agent、版本、失败步骤和脱敏后的错误描述。[提交 Issue](https://github.com/nanjiang976-ui/AI-project-migration/issues)

[返回安装和更新](../README.md#install) · [复制与接续手册](../skills/ai-project-migration/references/operations.md) · [聊天验收手册](../skills/ai-project-migration/references/chats.md) · [MIT 许可证](../LICENSE)
