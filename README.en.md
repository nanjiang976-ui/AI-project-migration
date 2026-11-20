# AI-project-migration

[简体中文](README.md) · [Install](#install) · [First use](#first-run) · [How to update](#update) · [FAQ](#faq) · [Full guide](docs/guide.en.md)

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

<a id="faq"></a>

## FAQ

### My Codex project has several source folders. Should I remove the others first? Will moving a shared folder update the other projects?

Keep the existing source list and choose which folder to move. If only your project uses the primary folder and you move just that folder, shared sources can stay in place. For example, `Demo` might use `SharedAssets` as a secondary source while `AssetLibrary` uses the same folder as its primary source. If you confirm the move, copy and verify the folder once, then update the corresponding entry in each project to the same destination, keeping its primary or secondary role. Codex saves source lists per project, so update each affected project separately.

The skill prompts you to confirm source folders and shared references before copying. The current scripts handle one physical folder at a time. Coordinating settings for multiple sources or projects requires separate checks for each entry.

### How can I reclaim space on the original drive and keep old chats working with the migrated files?

Once verification passes, manually remove the physical backup from the original drive. Keep the link at the original path, a directory junction on Windows or a directory symlink on macOS, so old chats can still reach the destination. Check each relevant chat's original ID, history, and working directory. Then use representative old chats from each affected project to test actual reads and writes for its migrated sources. Report the number of chats checked separately from what the read and write tests covered. Keep the corresponding backup while any folder or chat still needs verification.

[Full guide](docs/guide.en.md) · [Compatibility and test evidence](docs/guide.en.md#compatibility) · [Troubleshooting](docs/guide.en.md#faq) · [Report an issue](https://github.com/nanjiang976-ui/AI-project-migration/issues) · [MIT license](LICENSE)
