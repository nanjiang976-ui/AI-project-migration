# AI-project-migration

[简体中文](README.md) · [Install](#install) · [First use](#first-run) · [How to update](#update) · [Full guide](docs/guide.en.md)

Running out of space on your project drive while old chats still need the files? Move a local project to another drive on the same computer. The skill verifies the copy, leaves a link at the old location and checks whether an existing chat can actually read and write the files in their new location.

Supports local NTFS drives on Windows and local APFS drives on macOS. It guides Codex, Claude Code and other agents that can run local commands. Migration requires CPython 3.12+ and, for Git projects, Git. [Requirements and tested configurations](docs/guide.en.md#compatibility)

<a id="install"></a>

## Install

Choose one method. Install in your personal skills directory so the assistant remains available when the project folder is renamed.

**① Ask your agent (recommended for beginners)**

```text
Install skills/ai-project-migration from https://github.com/nanjiang976-ui/AI-project-migration in this agent's personal skills directory. First check for an existing installation and local edits. Install the complete folder, verify SKILL.md and its helper scripts, then tell me the installation path and invocation. This is an installation request only. Stop after verifying it.
```

Your agent needs access to GitHub and permission to write local files. If you're using a chat-only web interface, use the ZIP instructions instead.

**② Use a terminal**

Requires Node.js 22.20.0 or later, npm and Git. Run the command for your agent. On first use, npx may ask to download the `skills` tool. `-g` selects a personal installation, and `-y` confirms installation of the specified skill.

```sh
# Codex
npx skills add nanjiang976-ui/AI-project-migration --skill ai-project-migration -g -a codex --copy -y

# Claude Code
npx skills add nanjiang976-ui/AI-project-migration --skill ai-project-migration -g -a claude-code --copy -y
```

**③ Download a ZIP**

[Download the ZIP](https://github.com/nanjiang976-ui/AI-project-migration/archive/refs/heads/main.zip), extract it, and copy the entire `skills/ai-project-migration` folder to your agent's personal skills directory. Check that you can open `ai-project-migration/SKILL.md` directly, then restart the agent. [Installation paths by host](docs/guide.en.md#zip)

<a id="first-run"></a>

## First use

In **Codex**, send `$ai-project-migration Help me move a local project. Please guide me in English.`
In **Claude Code**, enter `/ai-project-migration Help me move a local project. Please guide me in English.`

If you haven't named a project, the assistant first asks **“Which local project do you want to migrate?”** Once you choose, it shows the source and destination paths and who handles each of the seven steps. [Migration walkthrough](docs/guide.en.md#steps)

<a id="update"></a>

## How to update

**Finish any migration already in progress before updating.** An offline command may still use the installed scripts. Update using the original installation method:

- **CLI installation:** Rerun [the installation command for your original agent](#install), keeping `--skill ai-project-migration` and the same `-a` option. This updates this skill for the selected host. Back up any edits you made inside the installed folder first.
- **Agent installation:** Send: “Update my installed ai-project-migration from https://github.com/nanjiang976-ui/AI-project-migration. Check its actual location, original installation method and local edits first. Update the same installation, verify the invocation and stop.”
- **ZIP installation:** Download the new ZIP, keep the previous installation outside the skills directory, and copy the complete new folder into its original location. Restart your agent and check the invocation above. If you customized any files, compare and preserve your edits first.

> [!CAUTION]
> **⚠️ Keep the folder entry at the original project path.** Old chats use it to reach the new files. Opening that folder and deleting its contents affects the active project on the destination drive. After final verification, only the physical `_迁移备份` backup identified in the report is eligible for manual removal.

[Full guide](docs/guide.en.md) · [Compatibility and test evidence](docs/guide.en.md#compatibility) · [Troubleshooting](docs/guide.en.md#faq) · [Report an issue](https://github.com/nanjiang976-ui/AI-project-migration/issues) · [MIT license](LICENSE)
