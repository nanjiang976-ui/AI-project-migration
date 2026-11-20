# AI 项目迁移助手 · AI-project-migration

[English](README.en.md) · [安装](#install) · [第一次使用](#first-run) · [如何更新](#update) · [常见问题](#faq) · [完整指南](docs/guide.zh-CN.md)

项目盘快满了，旧聊天还要接着改文件？这个 Skill 帮你把本地项目搬到同一台电脑的另一块盘。它会核对副本，在原位置留下通往新目录的链接，并实际检查旧聊天能否读写新位置的文件。

支持 Windows 本地 NTFS 盘、macOS 本地 APFS 盘；适配 Codex 和 Claude Code，其他能执行本地命令的 Agent 可用通用流程。迁移需要 CPython 3.12+；Git 项目还需 Git。[适用环境与实测范围](docs/guide.zh-CN.md#compatibility)

<a id="install"></a>

## 安装

选一种方式，安装在个人技能目录中，以便项目改名后仍能运行助手。

**① 把这句话发给 Agent（推荐新手）**

```text
请从 https://github.com/nanjiang976-ui/AI-project-migration 安装 skills/ai-project-migration 到当前 Agent 支持的个人技能目录。先检查是否已安装或有本地修改，再安装完整目录，核对 SKILL.md 和随附脚本，告诉我安装位置和调用方式。本次只安装，安装完成后停下。
```

Agent 需要能够访问 GitHub 并写入本机文件。仅支持聊天的网页助手请改用 ZIP 方式。

**② 用终端安装**

需要 Node.js 22.20.0 或更高版本、npm 和 Git。按你的 Agent 运行一条命令。首次使用时，npx 可能先询问是否下载 `skills` 工具。`-g` 选择个人目录，`-y` 确认安装指定的 Skill。

```sh
# Codex
npx skills add nanjiang976-ui/AI-project-migration --skill ai-project-migration -g -a codex --copy -y

# Claude Code
npx skills add nanjiang976-ui/AI-project-migration --skill ai-project-migration -g -a claude-code --copy -y
```

**③ 手动下载 ZIP**

[下载 ZIP](https://github.com/nanjiang976-ui/AI-project-migration/archive/refs/heads/main.zip)，解压后把 `skills/ai-project-migration` 整个文件夹复制到个人技能目录。确认 `ai-project-migration/SKILL.md` 能直接打开，然后重开 Agent。[各宿主安装位置与目录示例](docs/guide.zh-CN.md#zip)

<a id="first-run"></a>

## 第一次使用

在 **Codex** 中发送 `$ai-project-migration 帮我迁移一个本地项目。`
在 **Claude Code** 中输入 `/ai-project-migration 帮我迁移一个本地项目。`

没有指定项目时，第一句应是 **“你要迁移哪个本地项目？”** 确认项目后，助手会先给出源路径、目标路径和七步分工。[迁移七步](docs/guide.zh-CN.md#steps)

手动搬过或迁移做到一半，可以说 **“先检查我上次迁移做到哪一步。”** 助手会只读核对原件、目标、项目设置和聊天证据，再询问剩余来源与目标。[中途接续检查](skills/ai-project-migration/references/resume.md)

<a id="update"></a>

## 如何更新

**正在迁移项目时，请先完成当前迁移，再更新 Skill。** 离线命令可能仍在使用已安装的脚本。按当初的安装方式更新即可：

- **CLI 安装**：重新运行[上面对应 Agent 的安装命令](#install)，保留 `--skill ai-project-migration` 和原来的 `-a` 参数。这样只更新这个宿主里的这一项技能。若你手动修改过技能文件，先备份改动。
- **Agent 安装**：把这句发给 Agent：“请从 https://github.com/nanjiang976-ui/AI-project-migration 更新我已安装的 ai-project-migration。先核对安装位置、安装方式和本地修改，更新同一份技能，验证调用入口后停下。”
- **ZIP 安装**：下载新版 ZIP，先将已安装的文件夹暂存到技能目录之外，再把新版完整复制到原安装位置。重开 Agent 并按上面的方式检查入口；如曾自行修改，先比较并保留改动。

> [!CAUTION]
> **⚠️ 请勿删除原位置下的原名文件夹。** 它是旧聊天访问新项目的入口。进入其中删除文件会直接影响目标盘上的项目。只有最终验收通过后，才可手动清理报告指明的 `_迁移备份` 实体目录。

<a id="faq"></a>

## 常见问题（FAQ）

### Codex 项目有多个源文件夹，需要先移除其他来源吗？迁移共享目录时，其他项目会一起更新吗？

保留原有的源列表，先选本次要搬的目录。只迁本项目独有的主目录时，共享来源可以留在原位。假设 `Demo` 把 `SharedAssets` 设为次要源，`AssetLibrary` 把同一目录设为主要源。若确认迁移这份共享目录，只复制和校验一次，然后分别将两个项目的对应条目指向同一个新路径，各自保留原来的主次角色。Codex 按项目保存源列表，编辑其中一个项目时，其他项目仍需分别同步。

Skill 会在复制前列出来源和共享范围。你可以在同一次迁移中选择多个实体目录，或选择一个共享目录并同步它的引用项目。全部副本校验通过后，批次先接好旧路径，再逐项目换源和回读。已有单目录迁移从原检查点接续。[Codex 多源批次手册](skills/ai-project-migration/references/batch.md)

### 怎样腾出原盘空间，同时让相关旧聊天继续访问迁移后的文件？

验收通过后，手动清理原盘的实体备份。保留原名入口，也就是 Windows 的目录联接或 macOS 的目录符号链接，旧聊天仍可通过原地址访问目标盘。逐条核对相关旧聊天的原 ID、历史和工作目录，再从每个受影响项目中选择代表性旧聊天，对其涉及的已迁目录实际读写。报告分开记录逐条核对的数量和实际读写的范围。只要目录或聊天仍待核验，就保留对应备份。

[完整指南](docs/guide.zh-CN.md) · [兼容性与实测记录](docs/guide.zh-CN.md#compatibility) · [更多常见问题](docs/guide.zh-CN.md#faq) · [反馈问题](https://github.com/nanjiang976-ui/AI-project-migration/issues) · [MIT 许可证](LICENSE)
