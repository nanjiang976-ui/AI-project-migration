# AI Project Migration Assistant

[简体中文](README.md) · [Install](#install) · [First run](#first-run) · [Compatibility](#compatibility) · [FAQ](#faq)

Move a local AI project between storage volumes on the same computer. The assistant copies and verifies the project, then leaves a link at its original name so existing chats that still use the old address can reach the new files. Each step explains what you need to do, what has been verified and when you can remove the backup.

**For example**, a Windows project might move from `E:\Projects\Demo` to `F:\Work\Demo`, or a Mac project from `/Users/example/Projects/Demo` to `/Volumes/Work/Projects/Demo`. You choose the actual paths. The installation folder and Codex invocation name remain `codex-project-migration`, so existing users can update in place.

## When would I use this?

- **Your project volume is running low on space.** The copy includes hidden files, empty folders, uncommitted materials and local tools. A move within the same volume only changes a folder's location: it won't reclaim space on that volume. The assistant checks the volume identities and asks whether you still want to proceed.
- **You want existing chats to keep editing the project.** A directory junction on Windows or a directory symbolic link on macOS connects the original address to the destination. Acceptance checks distinguish recovery of the original session, actual reads and writes from its default working directory, and access through the old link.
- **The project has Git worktrees.** A worktree is a separate working copy of the same repository. The assistant records its commit and uncommitted state and checks its connection afterward. Ordinary non-Git folders can also be moved.

<a id="compatibility"></a>

## Compatibility and verification

“File migration” refers to local filesystem and Git checks. “Existing session” is separately verified by matching the original session identity and reading and writing from its default directory. Use the table to choose a workflow; every real migration gets its own acceptance report.

| Environment | File migration | Existing sessions and workflow |
|---|---|---|
| Windows · Codex | Tested across local NTFS volumes | A real existing Codex chat using the main project folder has passed a read/write check. History can be inspected where the desktop integration is available; the user assists otherwise. |
| Windows · Claude Code | Uses the same tested NTFS file workflow | Claude Code 2.1.150 restored the same real test session in a fictional project and verified bidirectional reads and writes through its default directory and old link. A new session also started in the destination. |
| Windows · Other local agents | Uses the same tested NTFS file workflow | Choose paths, open the destination and test the original chat manually; coverage depends on the host. |
| macOS · Codex | Two APFS test volumes verified in native macOS CI | A real existing Codex chat still requires host-side verification on macOS. |
| macOS · Claude Code | Two APFS test volumes verified in native macOS CI | Installation and resume instructions are provided. A real existing Claude session has not been verified on macOS. |
| macOS · Other local agents | Two APFS test volumes verified in native macOS CI | Guided manual workflow for agents that can execute local commands. Test an existing chat manually. |

The Windows cross-volume test used different NTFS partitions on the same SSD. Native macOS CI used two separately mounted APFS disk images to verify file contents and metadata, Git worktrees, symbolic links, and reads and writes. [Native acceptance run](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/actions/runs/44454723501). Two separate physical drives, Linux, network shares, moves to another computer and OS changes are outside this test record. [Developer tests](tests/) · [Native macOS workflow](.github/workflows/macos-apfs.yml)

**Which agent performs the move, and which one owns the chat?** For instance, Claude Code can help copy a project used by Codex, while recovery of its Codex chats must be verified in Codex. If the host cannot list histories, the report marks bulk history verification as unavailable. A successful file copy proves only the file portion of the move. [Host-specific operations and evidence](skills/codex-project-migration/references/hosts.md)

### Before you start

- **Windows:** Both volumes must be local NTFS. You need CPython 3.12+ and Windows PowerShell 5.1 or PowerShell 7. Check Python with `python --version`.
- **macOS:** Use mounted, writable local APFS volumes with ownership enabled, CPython 3.12+ and Terminal. Check with `python3 --version`. An external destination must also be available whenever you use the migrated project.
- Git projects additionally require Git (`git --version`). Save open files, pause tasks that may write and allow enough space for a full copy. Choose an unused destination project path so the assistant can create it.

<a id="install"></a>

## Install from a ZIP

1. Select **Code → Download ZIP**, or use the [main branch ZIP](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/archive/refs/heads/main.zip). In Windows, right-click the archive and select **Extract All**. In Finder, double-click it to extract.
2. Open `skills/codex-project-migration` inside the extracted repository. Copy that entire folder. Its direct contents include `SKILL.md`, `agents`, `references` and `scripts`.
3. Choose a personal installation location for the agent you will use. Create the parent directory if needed. Install outside the project being moved.

| Host | Windows destination folder | macOS destination folder |
|---|---|---|
| Codex | `%USERPROFILE%\.agents\skills\codex-project-migration` | `~/.agents/skills/codex-project-migration` |
| Claude Code local CLI | `%USERPROFILE%\.claude\skills\codex-project-migration` | `~/.claude/skills/codex-project-migration` |
| Other agents with local command access | The host's personal skills directory, or a stable local tools directory | Use the same approach and give the agent the full path to `SKILL.md` |

4. Check that the installed folder directly contains `SKILL.md`, then restart your local agent. For example, the Codex entry point on Windows is `%USERPROFILE%\.agents\skills\codex-project-migration\SKILL.md`. Codex environments that already discover personal skills in `.codex/skills` can keep using that location. Choose one installation location per host. [Codex skill locations](https://learn.chatgpt.com/docs/build-skills) · [Claude Code Skills](https://code.claude.com/docs/en/skills)

<a id="first-run"></a>

## First run

**In Codex**, paste:

```text
$codex-project-migration Help me move a local project. Please guide me in English.
```

**In the Claude Code local CLI**, enter:

```text
/codex-project-migration Help me move a local project. Please guide me in English.
```

In another local agent, ask it to read the installed `SKILL.md` and guide you through the seven steps. If you haven't named the project, the first question is **“Which local project do you want to migrate?”** You can also start with “Move my Demo project to another drive. Its existing chats are in Claude Code. First show me the two full paths and who handles each step.” Check both paths and volume names before proceeding.

## What will I need to do?

| Step | What the assistant does | What you do | What success looks like |
|---|---|---|---|
| 1. Identify | Checks the host, paths and volume identities, and records a baseline for chats and worktrees | Choose the project and destination; save files and pause writes | Paths, available space, baseline and evidence coverage are clear |
| 2. Copy | Copies the project and keeps the source intact | Usually nothing | A destination copy and a record of the operation exist |
| 3. Verify | Hashes file contents with SHA-256 and checks folders, permissions, hard links and platform-specific metadata | Pause any remaining writes | The strict comparison passes |
| 4. Open the new location | Updates the existing Codex source folder, or checks a fresh CLI/generic session starts in the new project; reconnects Git worktrees as needed | Open or confirm the project in the owning host | New work uses the destination and project/worktree mappings are accounted for |
| 5. Keep the old path working | Prepares one command with your actual paths for the option you chose | **Choose A or B before quitting**. Close the application holding the folder and use an independent PowerShell window or Mac terminal outside the project | The original name links to the exact destination; the backup is the original physical folder |
| 6. Test an existing chat | Checks the link, original session identity and actual reads and writes in both directions | **Choose A or B after restart and link verification**; use a chat that existed before migration | Both paths reach the same test file and cleanup succeeds |
| 7. Review | Reports history, worktrees and backup results along with the three full paths | Decide when to manually remove the verified physical backup | All differences are accounted for and the old-name link remains in place |

**Step 5: choose how to rename the source.** With A, rename it yourself in File Explorer or Finder, then paste the command to create and verify the link. With B, the command renames it, creates the link and checks it. Save your chosen command before quitting the agent. The output should show `Junction` on Windows or `SymbolicLink` on macOS and a `Target` matching the full destination path. Reopen the agent and say “The link is in place.”

**Step 6: choose how to test the old session.** With A, paste a supplied prompt into the selected old chat and return with its result. With B, the assistant can test in the current chat if it qualifies. Sending a visible prompt to another chat requires a suitable host integration, a specific chat selection and your explicit permission. Otherwise, paste it manually. In Claude Code, first try `claude --resume <original-session-id>`. In the Windows test, version 2.1.150 could not find the old record by ID alone after the project root changed. Using `claude --resume <absolute-path-to-original-session.jsonl>` restored the same ID and its original test marker. From the resumed session's default destination directory, the probe verified reads and writes through the old link and the same target file. The native `/codex-project-migration` command also prompted for the project in an isolated fixture. No transcript upload or editing is needed. [Host guide](skills/codex-project-migration/references/hosts.md) · [Claude Code session options](https://code.claude.com/docs/en/cli-reference)

## Which folder can I delete afterward?

> [!CAUTION]
> **⚠️ Do not remove the folder entry at the original project path.** It is a directory junction on Windows or a directory symbolic link on macOS. Existing chats use that entry to find the new files. Removing the link breaks the old address. **Opening that folder and deleting files inside it can delete the active files on the destination volume.**

These are fictional `Demo` paths. The final report supplies the three exact paths for your move.

```text
Windows
E:\Projects\Demo                      Original-name junction → F:\Work\Demo            Keep
F:\Work\Demo                           Active project folder                           Keep
E:\Projects\Demo_迁移备份               Original folder, renamed as backup              Remove manually after acceptance

macOS
/Users/example/Projects/Demo           Original-name symlink → /Volumes/Work/Projects/Demo   Keep
/Volumes/Work/Projects/Demo            Active project folder                                  Keep
/Users/example/Projects/Demo_迁移备份    Original folder, renamed as backup                     Remove manually after acceptance
```

**⛔ Keep the backup** if it contains unique files, unexplained differences remain, or relevant checks are still pending. The suffix `_迁移备份` means “migration backup”. The assistant leaves folder removal to you.

## How do I know it worked?

A **fictional acceptance report** might show: 120 file hashes matched; all four histories covered by the available API were readable; one original main-folder chat was resumed by ID and wrote and read the same temporary file through its default directory and the destination; one worktree matched its original registration and changes; the backup contained zero unique files or unexplained differences. Readable histories and actual write-tested chats are counted separately. On macOS or after a host has rebound a session, the default directory may display the physical destination. The original session identity, actual default directory and old link are still checked separately.

<a id="faq"></a>

## FAQ

**The folder is in use. What should I do?** Save your files, quit the agent holding the project (including the tray app on Windows), and close terminals still using the project directory. Run the prepared cutover command from outside the folder. If the lock persists, keep the original and report the error.

**What if an external volume is missing or its identity changed?** Remount the intended volume and verify its identity and location. Don't create a replacement project under a missing mount point. The assistant pauses cutover.

**How do I resume an interrupted move? What is `.migration-checks`?** Keep both folders and the local audit directory, reopen the assistant and ask to resume the last migration. It compares the checkpoints and inventories with the actual folders. Audit files can include local paths and session IDs; redact them before sharing any report.

**Why are my Git worktrees still on the old volume?** Separate working copies can stay in place with their uncommitted changes. The assistant checks their connection to the new main repository. A worktree chat continues to edit its corresponding working copy.

**Why does the assistant stop on some files?** Both volumes must use the supported local filesystem. Permissions it cannot verify, cloud placeholders and links to data outside the tree need individual review. On macOS, checks also cover ACLs, extended attributes (including resource forks), file flags and name collisions. The assistant keeps the original and explains the specific issue. See the [operations and recovery guide](skills/codex-project-migration/references/operations.md) and [chat verification guide](skills/codex-project-migration/references/chats.md) in Chinese.

## Feedback and license

When opening an issue, include your operating system, agent, Python version, failing step and a **redacted error description**. Keep real local paths, chat text, session IDs, full file inventories and migration reports private. [Open an issue](https://github.com/nanjiang976-ui/Codex-Project-Migration-Assistant/issues)

This project is licensed under the [MIT License](LICENSE).
