# AI-project-migration user guide

[Home](../README.en.md) · [简体中文](guide.zh-CN.md) · [Compatibility](#compatibility) · [ZIP installation paths](#zip) · [Seven steps](#steps) · [Troubleshooting](#faq)

<a id="compatibility"></a>

## Requirements and verification coverage

The skill moves a local project **within the same computer and operating system**. Use it if the project drive is nearly full, you need to continue using old chats, or you have Git worktrees and uncommitted files to preserve. A Windows move might go from `E:\Projects\Demo` to `F:\Work\Demo`; a Mac move might go from `/Users/example/Projects/Demo` to `/Volumes/Work/Projects/Demo`. You choose both paths.

| OS and agent | Project files | Existing chats |
|---|---|---|
| Windows · Codex | Local NTFS cross-drive copy and Git tested | A real chat previously working in the main project directory passed the two-way read/write check. The user can assist where desktop integrations are unavailable. |
| Windows · Claude Code | Uses the same tested NTFS workflow | On 2.1.150, the same real test session was resumed in a synthetic project and verified two-way I/O. A new session also started in the destination. |
| Windows · Other local agents | Can call the same filesystem and Git helpers | Choose paths and test in the original chat manually. Coverage depends on what the host can verify. |
| macOS · Codex | Two APFS test disks verified in native macOS CI | A real existing Codex chat on macOS still requires host-side testing. |
| macOS · Claude Code | Uses the same tested APFS workflow | Installation and session recovery are documented. A real existing Claude session on macOS has not been tested. |
| macOS · Other local agents | Can call the same filesystem and Git helpers | Open the destination manually and test the original chat. Missing evidence is marked explicitly. |

The Windows cross-drive tests used two independent NTFS partitions on the same SSD. Native macOS runners mounted two separate APFS disk images to verify data, metadata, links, worktrees and read/write behavior. Linux, cross-computer migration, network shares and two separate physical drives are outside this test record. [Native macOS acceptance](https://github.com/nanjiang976-ui/AI-project-migration/actions/runs/44457680917) · [Developer tests](../tests/README.md)

If Claude Code guides the move of a project whose chats belong to Codex, those chats must still be verified in Codex. If a host cannot list histories, the assistant reports that bulk history was unavailable and lists any original chat that was actually tested. [Host-specific capabilities and checks](../skills/ai-project-migration/references/hosts.md)

### Before moving a project

- **Windows:** Use local NTFS source and destination drives, CPython 3.12+ and Windows PowerShell 5.1 or PowerShell 7. Check Python with `python --version`.
- **macOS:** Both APFS drives must be locally mounted and writable, with ownership enabled. Have Terminal and CPython 3.12+ available (`python3 --version`). Keep an external destination connected when using the migrated project.
- **Git projects:** Check `git --version`. Save open files and pause tasks that may write to the project. Choose an unused destination project folder name and allow enough free space for a complete copy. Moving within one drive won't free space on that drive, so the assistant asks whether to proceed.

<a id="zip"></a>

## Where to put a manual installation

Open the [latest GitHub release](https://github.com/nanjiang976-ui/AI-project-migration/releases/latest) and select **Source code (zip)** under **Assets**. On Windows, right-click the ZIP and choose **Extract All**. On macOS, double-click it in Finder. Inside the extracted repository locate `skills/ai-project-migration`. Copy the **whole** folder into a stable personal skills directory outside the project you intend to move.

| Agent | Installed folder on Windows | Installed folder on macOS |
|---|---|---|
| Codex | `%USERPROFILE%\.agents\skills\ai-project-migration` | `~/.agents/skills/ai-project-migration` |
| Claude Code local CLI | `%USERPROFILE%\.claude\skills\ai-project-migration` | `~/.claude/skills/ai-project-migration` |
| Other agents with local command access | A personal skills directory supported by the host or a stable tools directory | Same approach. Provide the full path to `SKILL.md` when needed. |

Use the File Explorer address bar on Windows or `⌘⇧G` in Finder on macOS to open the personal skills directory. Create the parent directory if needed. Check that `ai-project-migration/SKILL.md` exists and sits alongside `agents/`, `assets/`, `references/` and `scripts/`. Choose one installation per host. Codex environments that already discover personal skills in `.codex/skills` can keep using that location for a manual installation. Restart the agent and try the [first-use prompt](../README.en.md#first-run). [Codex skill locations](https://learn.chatgpt.com/docs/build-skills) · [Claude Code skills](https://code.claude.com/docs/en/skills)

### Why rerun the installation command to update?

`-g` selects a personal installation, `--skill ai-project-migration` selects the skill, and `-a codex` or `-a claude-code` selects the host. In an isolated upgrade test, rerunning the same host-specific `add` command updated the previous version in its original scope. The installer's general `update` operation, when filtered only by skill name, can also write a copy for another detected agent or a shared skills directory. Follow [the update instructions](../README.en.md#update) to keep the existing host selection.

<a id="steps"></a>

## The seven steps for a single folder

Use the [Codex batch workflow](../skills/ai-project-migration/references/batch.md) to move several selected source folders, or one folder shared by multiple projects. Confirm every reference and destination first. Copy each shared physical folder once, restore the old paths, then update each project. Resume a single-folder move already in progress from its original checkpoint.

| Step | What the assistant does | What you do | What success looks like |
|---|---|---|---|
| 1. Identify | Checks the chat-owning host, both paths and available space; records chat and Git baselines | Choose the project and destination, save open files, pause writes | Both parties can identify the project and what needs verifying |
| 2. Copy | Copies the full project, including hidden and uncommitted files, and keeps the original | Usually nothing | A destination copy and an independent copy receipt exist |
| 3. Verify | Checks file SHA-256 hashes, empty directories, hard links, permissions and system metadata | Stop further writes if the source is still changing | The strict comparison passes |
| 4. Use the destination | Updates the original Codex project or checks that a fresh session starts at the new location; verifies Git worktree links | Follow the host-specific steps when the assistant cannot perform them directly | New work uses the destination and the project/worktrees match their baseline |
| 5. Preserve old paths | Supplies one command with the actual paths for your chosen method | **Choose A/B before quitting**; close programs using the project and run the command in a terminal outside it | The old entry links to the right destination and the backup is the original directory |
| 6. Test an old chat | Checks the link, original session identity and two-way reads/writes from its default directory | **Choose A/B after restart and link verification**; use an existing chat from before the move | The chat reaches the same file at the destination and the temporary probe is removed |
| 7. Review | Reports history access, actual writes, Git worktrees and the source-drive backup separately | After acceptance, decide whether to manually remove the backup | Any unique or different data has been accounted for |

**Step 5.** The assistant gives you one command for your chosen method, with a guide to opening the terminal on your system. Save the command before quitting programs using the project. On Windows, fully quit desktop apps, including the tray app. With A, rename the original in File Explorer or Finder as instructed, then run the command in a separate terminal outside the project to create the link. With B, run the command from that terminal to rename the folder, create the link and check it. After restarting the agent, say “The link is in place.” The assistant verifies the current state and resumes from there. [Windows illustration](../skills/ai-project-migration/assets/open-terminal-windows.png) · [macOS illustration](../skills/ai-project-migration/assets/open-terminal-macos.png)

**Step 6.** With A, paste the supplied prompt into a selected original chat and report back. With B, let the assistant test an eligible existing chat. Sending to another chat requires a supported host integration, a specific chat selection and your explicit permission. From the chat's default directory the probe creates a random file, verifies the old path and the destination access the same file, writes back in the other direction and removes the probe. A resumed chat may show the resolved destination path. Its identity and real write result are checked separately.

## Which folder should I keep?

> [!CAUTION]
> **⚠️ Keep the folder entry at the original project path.** Keeping this link takes up almost no disk space. Removing it breaks the old address. Deleting files *inside* that folder can delete active files on the destination drive.

```text
Windows example
E:\Projects\Demo                       Original junction → F:\Work\Demo                    Keep
F:\Work\Demo                           Active project                                      Keep
E:\Projects\Demo_迁移备份                Original physical folder, now the backup            Remove manually after acceptance

macOS example
/Users/example/Projects/Demo            Original symlink → /Volumes/Work/Projects/Demo     Keep
/Volumes/Work/Projects/Demo             Active project                                     Keep
/Users/example/Projects/Demo_迁移备份     Original physical folder, now the backup           Remove manually after acceptance
```

**Example acceptance report (fictional):** 120 file hashes matched. All four histories visible to the host contained their original turns. One original session completed a two-way write check. One Git worktree matched its registration, branch and uncommitted state. The backup contained zero unique files or unexplained differences. The final report gives those counts separately and shows the three exact paths. **⛔ Keep the backup** while any required check is pending. The `_迁移备份` suffix means “migration backup.”

<a id="faq"></a>

## Troubleshooting

**The folder is in use. What should I do?** Save your changes and quit the agent or editor holding the project. Close terminals whose current directory is inside it. Share the error with the assistant so it can check whether the rename or link creation already completed. If the command needs to be retried, run it in a separate PowerShell or Mac terminal outside the project.

**My computer restarted or only the rename completed. How do I continue?** Keep both locations and the local `.migration-checks` folder, then say “Resume the last migration.” The assistant checks the current files and recorded checkpoints before continuing.

**The chat still displays the old address, or Claude Code cannot find my session.** The old address reaches the new files through the link. Verify the session's original identity and run the write check. For Claude Code, try `claude --resume <original-session-id>`. The tested Windows version, 2.1.150, needed `claude --resume <absolute-path-to-original-session.jsonl>` after the project root changed. Keep the transcript on your machine. [Host guide](../skills/ai-project-migration/references/hosts.md) · [Claude Code resume options](https://code.claude.com/docs/en/cli-reference)

**An external drive is disconnected.** Reconnect and mount the intended drive, then verify its identity and the project location before resuming. Avoid creating a replacement project at a missing mount point.

**Why are the Git worktrees still on the source drive?** This migration moves the main repository. Separate working copies may remain in their original locations with their uncommitted changes. The assistant verifies their connection to the new main repository.

**Why did the assistant pause on a cloud placeholder or special attribute?** Complete verification requires readable data, permissions, system metadata and internal hard links. Data outside the tree and attributes that the destination cannot represent need individual review.

**Can I attach `.migration-checks` when reporting a problem?** Keep it local. It may contain actual paths, chat IDs, checksums and recovery evidence. Submit the OS, agent, versions, failed step and a redacted description instead. [Report an issue](https://github.com/nanjiang976-ui/AI-project-migration/issues)

[Back to installation and updates](../README.en.md#install) · [File operations and recovery](../skills/ai-project-migration/references/operations.md) · [Chat verification](../skills/ai-project-migration/references/chats.md) · [MIT license](../LICENSE)
