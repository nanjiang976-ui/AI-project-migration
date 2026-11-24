# AI-project-migration

[简体中文](README.md) · [What can it do?](#what-it-does) · [Install](#install) · [How to use](#usage) · [How to update](#update) · [FAQ](#faq) · [Full guide](docs/guide.en.md)

Running out of space on your project drive while old chats still need the files? Move a local project to another drive on the same computer. The skill verifies the copy, leaves a link at the old location and checks whether an existing chat can actually read and write the files in their new location.

Supports local NTFS drives on Windows and local APFS drives on macOS. It guides Codex, Claude Code and other agents that can run local commands. Migration requires CPython 3.12+ and, for Git projects, Git. [Requirements and tested configurations](docs/guide.en.md#compatibility)

<a id="what-it-does"></a>

## What can it do?

**Move a local project to another drive on the same computer.** New chats start at the new location, and old chats can still reach the project files through the original path. Use it when the project drive is running low on space, a project uses several folders, or you have already moved some files by hand and need to pick up where you left off.

- **Copy the whole project and verify it.** The copy includes hidden files, empty directories, uncommitted code, and assets. Internal pnpm directory junctions on Windows are mapped to the new location and verified individually. When dependency files share hard links outside the project, the assistant explains the independent copies required on another volume and obtains approval while preserving hardlink groups within the project. The skill checks files individually and identifies discrepancies, files still being written, and items it cannot copy faithfully.
- **Move multiple source folders and handle shared folders.** In Codex, review the primary and secondary sources and known sharing relationships before choosing what to move. Copy a shared folder once, then update each project that references it to use the same destination while keeping its original role.
- **Resume a manual or interrupted move.** A read-only check reviews the original files, the copy, project settings, links, and chat evidence. It reports where each source stopped and asks you to confirm the remaining folders and their destinations.
- **Check old chats and Git worktrees.** Check whether each relevant old chat is readable and where its working directory points. Then test reads and writes from selected original chats to confirm changes reach the new drive. For linked Git worktrees, also check their connection to the new repository and their uncommitted changes.
- **See which folder you can clean up.** The verification report identifies the live project on the new drive, the original path old chats still use, and the backup you can remove manually. If more verification is needed, the report identifies the outstanding checks.

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

Open the [latest GitHub release](https://github.com/nanjiang976-ui/AI-project-migration/releases/latest) and select **Source code (zip)** under **Assets**. Extract the ZIP and copy the entire `skills/ai-project-migration` folder to your agent's personal skills directory. Check that you can open `ai-project-migration/SKILL.md` directly, then restart the agent. [Installation paths by host](docs/guide.en.md#zip)

<a id="first-run"></a>
<a id="usage"></a>

## How to use

After installing, send a prompt to your agent. The examples below use **Codex**. In **Claude Code**, replace `$ai-project-migration` at the start with `/ai-project-migration`. For other local agents, ask them to read `SKILL.md` in the installed folder first.

### 1. First time, and you have not picked a project

```text
$ai-project-migration Guide me through moving a local project. Help me confirm the project, source folder, and destination, then wait for my confirmation before copying.
```

The assistant first asks **“Which local project do you want to migrate?”** Once you confirm, it shows the full paths, the steps and the actions you need to take. It checks the local environment and both volumes before moving the project. It checks Git only for Git projects. If a tool is missing or incompatible, it explains how it would install it, waits for your explicit approval, then repeats the checks before resuming. [Migration walkthrough](docs/guide.en.md#steps)

### 2. You know the project and destination

```text
$ai-project-migration Move Demo from E:\Projects\Demo to F:\Work\Demo. Check the project settings, source folder, and destination first, then tell me what I need to confirm.
```

Replace the example project name and paths with your own. If the destination folder already exists, the assistant checks whether it belongs to an earlier move before deciding how to proceed.

### 3. A Codex project has several source folders or shares one with other projects

```text
$ai-project-migration Demo has several source folders. List the full path of each one, whether it is primary or secondary, and which folders other projects share. Let me choose what to move and where, then ask me to confirm which projects to update together.
```

To move one secondary source, add “Keep the existing primary folder.” The batch workflow applies even if only this project uses the secondary folder. To verify the move, use an original chat started from the primary folder. The probe keeps that chat's default working directory and tests two-way reads and writes to the migrated files through the secondary folder's old path. If you select a shared folder, the assistant lists the affected projects and waits for you to confirm which ones to update.

### 4. You moved some files by hand or paused a migration

```text
$ai-project-migration I have moved some files by hand and the migration is unfinished. Start with a read-only check of the originals, destination, copy, original path, project settings, and chat status. Tell me where each folder stopped and what comes next. I will confirm the remaining folders and destinations.
```

If a checkpoint exists, the assistant compares it with the current state and resumes from a verified step. If the originals or verification records are missing, it explains which findings need more evidence. [Resume inspection](skills/ai-project-migration/references/resume.md)

### 5. The offline command has finished and you want to continue verification

Wait for the normal `PS …>` prompt in the separate PowerShell window, or the normal shell prompt in macOS Terminal. Take a screenshot or copy the last few lines before closing that window. Reopen Codex or your agent and return to **the original migration chat**. If you're unsure whether the link was created, attach the screenshot or paste the output.

```text
$ai-project-migration The offline command has finished. Check the actual link status and continue verification from the current step.
```

Test actual reads and writes in chats that existed before the migration. For shared folders, check whether old chats from each affected project can access the corresponding source. The report distinguishes readable chat history, reachable paths, and actual reads and writes.

### 6. The move is finished and you want to check whether you can remove the backup

```text
$ai-project-migration Run the final verification and report the results for project files, old chats, and Git worktrees. List the full paths of the original entry to keep, the live project on the new drive, and any backup I can remove manually.
```

**⚠️ Keep the folder entry at the original location.** Old chats use it to reach the new drive. Once verification passes, you can manually remove the physical `_迁移备份` backup named in the report.

If new chats were opened solely for verification, the final report lists any temporary verification chats you can manually delete once verification is complete and the evidence has been saved. Keep the original migration chat and any existing chats used for testing.

<a id="update"></a>

## How to update

**Finish any migration already in progress before updating.** An offline command may still use the installed scripts. Update using the original installation method:

- **CLI installation:** Rerun [the installation command for your original agent](#install), keeping `--skill ai-project-migration` and the same `-a` option. This updates this skill for the selected host. Back up any edits you made inside the installed folder first.
- **Agent installation:** Send: “Update my installed ai-project-migration from https://github.com/nanjiang976-ui/AI-project-migration. Check its actual location, original installation method and local edits first. Update the same installation, verify the invocation and stop.”
- **ZIP installation:** Download the new ZIP, keep the previous installation outside the skills directory, and copy the complete new folder into its original location. Restart your agent and check the invocation above. If you customized any files, compare and preserve your edits first.

> [!CAUTION]
> **⚠️ Keep the folder entry at the original project path.** Keeping this link takes up almost no disk space. Old chats use it to reach the new files. Opening that folder and deleting its contents affects the active project on the destination drive. After final verification, only the physical `_迁移备份` backup identified in the report is eligible for manual removal.

<a id="faq"></a>

## FAQ

### My Codex project has several source folders. Should I remove the others first? Will moving a shared folder update the other projects?

Keep the existing source list and choose which folder to move. If only your project uses the primary folder and you move just that folder, shared sources can stay in place. For example, `Demo` might use `SharedAssets` as a secondary source while `AssetLibrary` uses the same folder as its primary source. If you confirm the move, copy and verify the folder once, then update the corresponding entry in each project to the same destination, keeping its primary or secondary role. Codex saves source lists per project, so update each affected project separately.

The skill lists sources and shared references before copying. Use the batch workflow to move one secondary source, several physical folders, or one shared folder and update the projects that reference it. After every copy passes verification, the batch restores the old paths, then switches and checks each project. Resume an existing single-folder move from its original checkpoint. [Codex batch workflow](skills/ai-project-migration/references/batch.md)

### How can I reclaim space on the original drive and keep old chats working with the migrated files?

Once verification passes, manually remove the physical backup from the original drive. Keep the link at the original path, a directory junction on Windows or a directory symlink on macOS, so old chats can still reach the destination. Check each relevant chat's original ID, history, and working directory. Then use representative old chats from each affected project to test actual reads and writes for its migrated sources. Report the number of chats checked separately from what the read and write tests covered. Keep the corresponding backup while any folder or chat still needs verification.

### Why does an old chat still show its original working directory after migration?

For example, Codex may resume an old chat with its original working directory on the C drive after the project's primary folder moves to the D drive. The verified link at the old path (a directory junction on Windows or a directory symlink on macOS) leads to the files in the destination folder. The verification report records which old chats passed read and write tests.

[Full guide](docs/guide.en.md) · [Compatibility and test evidence](docs/guide.en.md#compatibility) · [Troubleshooting](docs/guide.en.md#faq) · [Report an issue](https://github.com/nanjiang976-ui/AI-project-migration/issues) · [MIT license](LICENSE)
