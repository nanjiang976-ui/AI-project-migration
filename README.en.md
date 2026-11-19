# Codex Project Migration Assistant

[简体中文](README.md) · [Install](#install) · [First run](#first-run) · [FAQ](#faq)

Move a local Codex project from C: to D: on Windows. New chats can start in the new location, and existing chats that used the main project folder can keep working on the same files. The assistant walks you through the move, shows what it verified, and identifies the backup you can remove to reclaim space.

## When would I use this?

- **C: is running low on space.** Move a complete project, including hidden files, empty folders and uncommitted work, to another local drive.
- **You want to continue an old chat.** An existing chat may still remember the old working directory after you change the project's primary folder in Codex. A Windows **directory junction** lets the old address open the new folder.
- **Your project has uncommitted changes or Git worktrees.** A worktree is a separate working copy of the same repository. The assistant records each worktree's state and checks its connection to the main repository after the move. Ordinary folders are supported too.

### Before you start

Use the Codex desktop app on Windows with **local NTFS** source and destination drives, **CPython 3.12 or later**, and Windows PowerShell 5.1 or PowerShell 7. Git projects also need Git. Allow enough space on the destination drive for the complete project. To see a drive's file system and available space, open its **Properties** in File Explorer.

In PowerShell, `python --version` displays your Python version. For a Git project, `git --version` confirms Git is available. Save open files and pause tasks that may write to the project. Choose an **unused destination folder name**, for example `D:\CodexProjects\Demo`, and let the assistant create it.

<a id="install"></a>

## Install from a ZIP

1. On this repository's GitHub page, choose **Code → Download ZIP**, or use the [direct ZIP download for the main branch](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/archive/refs/heads/main.zip).
2. Right-click the ZIP in File Explorer and select **Extract All**. In the extracted repository, open `skills` and find **`codex-project-migration`**. Open it once to check that it directly contains `SKILL.md`, `agents`, `references` and `scripts`.
3. Copy the entire **`codex-project-migration`** folder. Paste `%USERPROFILE%\.agents\skills` into File Explorer's address bar, press Enter and paste the folder there. If this location does not exist, open `%USERPROFILE%` and create `.agents` and its `skills` subfolder.
4. Check that the following file opens, then restart Codex.

```text
%USERPROFILE%\.agents\skills\codex-project-migration\SKILL.md
```

If your setup already discovers personal skills in `%USERPROFILE%\.codex\skills`, you can install the same folder there instead. Choose a single location. A user-level install keeps the assistant's commands available while Codex is closed and your project folder is being renamed. [Codex skill locations](https://learn.chatgpt.com/docs/build-skills)

<a id="first-run"></a>

## First run

Paste this into a Codex chat:

```text
$codex-project-migration Help me move a local Codex project. Please guide me in English.
```

The assistant starts by asking **“Which Codex project do you want to migrate?”** (in Chinese, **“你要迁移哪个 Codex 项目？”**). Once you choose one, it shows the original path, the proposed destination, all seven steps and who handles each one. Check both paths, especially if multiple projects have the same name.

You can also name a project up front:

```text
$codex-project-migration I want to move my local Demo project to D:\CodexProjects\Demo. First show me the current path, the destination and the full procedure.
```

## What will I need to do?

| Step | What the assistant does | What you do | What success looks like |
|---|---|---|---|
| 1. Identify the project | Records the project identity, paths, chats and worktrees, and checks disk space | Confirm the right project and destination, save files and pause edits | The project and everything to be checked have been identified |
| 2. Copy | Copies the complete project and keeps the original | Usually nothing | A destination copy and a record of this copy exist |
| 3. Verify the copy | Compares SHA-256 file hashes, folders, attributes, permissions, hard links and NTFS alternate data streams | Pause any writes still changing the source | Strict comparison passes after any differences are resolved |
| 4. Update the original project | Changes the primary folder on the same Codex project, reads the settings back and reconnects worktrees as needed | Follow the click-by-click instructions if desktop controls are unavailable | The project keeps its ID, and its primary folder points to the destination |
| 5. Keep the old path working | Supplies a PowerShell command with your exact paths for the option you chose | **Choose A or B before exiting**. Fully quit Codex, including the tray app, and use a separate PowerShell window opened from Start | `LinkType` is `Junction` and `Target` is the new project path |
| 6. Test an existing chat | Checks the junction, then verifies that one eligible old chat can read and write through both paths | **Choose A or B after reopening** Codex | The chat's default directory and the new folder reach the same test file, and the file has been cleaned up |
| 7. Review and reclaim space | Reports chat, worktree and backup checks separately and shows the full paths | Decide when to manually remove the verified physical backup | Any unique backup data has been accounted for, and the old-name junction remains available |

**Step 5 — how to rename the original.** With A, you rename the original folder in File Explorer, then paste the command to create and check the junction. With B, the command handles the rename, junction and checks. In both cases you get the command **before** you quit Codex. Save it in Notepad if helpful. After running it, reopen Codex and say “The junction is in place.” The assistant checks the current state before continuing.

**Step 6 — how to test a chat.** With A, paste a supplied test prompt into a selected old chat that used the main project folder, then return with the result. With B, the assistant runs the test in the current chat if it qualifies. To test another chat, it asks you to select it and explicitly authorize one visible test message. Available desktop integrations determine which UI actions, history checks and messages can be performed automatically. Where an integration is missing, the assistant guides you through the manual step and reports what could be verified.

## Which folder can I delete afterward?

This example uses a fictional project named `Demo`. The junction keeps the old address usable while D: holds the active files.

```text
Before
C:\Projects\Demo              Original project folder

After
D:\CodexProjects\Demo         Active project files                Keep
C:\Projects\Demo              Junction → D:\CodexProjects\Demo    Keep
C:\Projects\Demo_迁移备份     Original files, renamed as backup   Remove manually after verification
```

The suffix `_迁移备份` means “migration backup.” **Deleting files inside `C:\Projects\Demo` now deletes the active files on D:.** To reclaim space, use the exact backup path in your final report and remove only the verified backup folder. The assistant leaves that decision to you.

## How do I know it worked?

Here is a **fictional acceptance report**:

| Check | Example result | What it demonstrates |
|---|---|---|
| Project files | 120 files, all SHA-256 hashes match | The copy passed a per-file content check |
| Chat history | 4 of 4 original IDs returned a historical turn | Those four chats have readable history |
| Old-chat write test | 1 old main-folder chat passed | A random file was created in its default working directory, read and identified on D:, appended to on D:, read back through the old path and cleaned up |
| Git worktrees | 1 of 1 connected to the expected repository | The commit, branch and uncommitted status match the baseline |
| Original-drive backup | 0 backup-only files, 0 unexplained content differences | You can manually remove the backup at the path shown in the report |

The report distinguishes the number of histories read from the number of chats actually tested for writes. If there is no eligible old main-folder chat, the report explains why and records the actual test count. The included [tests](tests/) are for developers. Isolated validation used real Windows file systems, PowerShell and Git. Desktop clicks and cross-chat messaging were simulated. For your migration, rely on the checks recorded for that run.

<a id="faq"></a>

## FAQ

**Windows says the folder is in use. What now?** Save your files and close the application holding the folder open. Codex or a terminal may still be using it as the working directory. Fully quit Codex, including its tray app, then use a separate PowerShell window outside the project to run the command prepared for this migration. If Windows still reports a lock, keep the original and tell the assistant which step failed.

**Why does my old chat still show C:?** The chat retained its earlier working-directory path. Windows follows the junction to D:. The step 6 write-and-read-back test confirms this on your computer.

**What is `.migration-checks`?** It holds local run records, file inventories and checkpoints used to resume a migration. Its default location is under the parent of the destination project folder. Keep the run directory until verification is complete. It may contain local paths and chat IDs, so redact details before sharing a report.

**What if I close Codex halfway through?** Keep the original, the copy and the run records. Reopen Codex and ask to resume the previous migration. The assistant checks the recorded steps against the actual folders and reuses an existing correct junction.

**Why are my Git worktrees still on C:?** Moving the main checkout changes where the shared repository lives. Separate working copies can stay in place with their own uncommitted files. The assistant checks that they connect to the new main repository. Worktree chats continue using their respective working copies.

**Why did the assistant pause on a special file?** Cloud placeholders, encrypted, compressed or sparse files, hard links outside the project, and existing junctions inside the tree need individual handling. The assistant identifies the unsupported case and keeps the original in place. The technical guides for [file operations and recovery](skills/codex-project-migration/references/operations.md) and [chat verification](skills/codex-project-migration/references/chats.md) are available in Chinese.

## Feedback and license

When reporting a problem, include your Windows, Python and PowerShell versions, the step, and a **redacted error description**. Replace real paths with examples such as `C:\Projects\Demo`. Keep chat text, chat IDs, full file inventories and migration reports on your machine. [Open an issue](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/issues)

This project is licensed under the [MIT License](LICENSE).
