# Developer verification / 开发者验证

安装与日常使用参见[中文首页](../README.md)或 [English guide](../README.en.md)。这些测试面向维护者，使用虚构项目和会话标识。普通用户无需运行。测试按系统标识确认是否跨盘；两个盘也可能是同一块物理硬盘的不同分区。

## Run on Windows

Use local NTFS storage, CPython 3.12 or later, Git and Windows PowerShell. If `pwsh.exe` is available, the suite also exercises PowerShell 7; otherwise that individual test is reported as skipped. No GitHub account, network or Codex desktop session is needed.

1. Create **two new empty folders** outside your projects, repositories and skill installation. Give the test account create, modify and rename access. For a genuine cross-volume test, put the folders on different local NTFS volumes. The report records whether the volumes actually differ.
2. From the directory containing this repository's `README.md`, run the command below. Replace both example paths with the absolute paths you have prepared. The runner refuses drive roots, non-empty folders, redirected paths, overlapping paths and paths within a Git checkout. It prints the exact two unique fixture locations before creating them.
3. Check the `unittest` output and the printed `result.json` path. Each scenario uses its own synthetic data. Keep both generated directories together if you want to revisit the receipts. For another run, select two fresh empty folders.

```powershell
python -B tests/run.py --source-scratch 'E:\TestScratch\DemoSource' --target-scratch 'F:\TestScratch\DemoTarget'
```

## Run on macOS

Use a native Mac with CPython 3.12+, Git, `diskutil`, `hdiutil` and permission to mount user-owned disk images. Pass one existing, empty scratch folder outside every project. A sample command (substitute your actual scratch folder):

```sh
python3 -B tests/macos_ci.py --scratch '/Users/example/TestScratch/apfs-ci'
```

The runner creates two uniquely named sparse disk images there, attaches them with ownership enabled, and checks their APFS volume UUIDs before using them. A case-sensitive source and a case-insensitive target exercise filename collisions. Tests verify data and metadata (including extended ACLs, resource forks, flags and internal hard links), real symbolic links, a real detach/remount, and a dirty Git worktree. It detaches its images when finished. Keep the two sparse image files for investigation, or remove them manually after they have been detached.

The [macOS workflow](../.github/workflows/macos-apfs.yml) runs these tests on a native GitHub-hosted Mac with read-only repository permissions. A successful run demonstrates two APFS **test volumes**; it does not establish a test on two separate physical drives. No Agent login or model calls take place in this suite.

## Coverage

| Real filesystem or Git checks | What is verified |
|---|---|
| Environment preflight | Native shells and Python paths, Windows Store alias avoidance, Apple Git dispatcher handling, Git presence and worktree capability, unavailable target locations, APFS detach/remount, and read-only recovery from an existing checkpoint. No installer is run. |
| Project copy | Empty directories, hidden/read-only files, NTFS named streams and hardlink identity. Extra target data and modified or unsupported source data are detected. |
| Manual and automatic cutover | A user-renamed backup, scripted rename, actual directory lock, name collision, wrong junction, replaced target, failed verification and interrupted creation. The NTFS and APFS suites also check that late edits on either side stop cutover, even with the original file size and modification time. |
| Old-directory probe | The packaged script writes and reads in both directions from the synthetic old working directory; an incorrect working directory and a reused request ID are refused. |
| Generic host with limited tools | A synthetic user selects an old session manually. The checkpoint rejects another request, a filesystem-only receipt, and a different claimed old session. It accepts only the preregistered request and matched identity. An unavailable history list remains unverified. |
| Git and restart | Ordinary folder, repository without external worktrees, a linked worktree with staged, unstaged and untracked data, correct common directory, ordered checkpoint recovery and final backup comparison. |
| Batch with shared sources | Three configured sources with a subset selected, one physical copy for a directory shared by two projects in different roles, and an existing primary retained while moving its shared secondary source. Incomplete inventories, incomplete shared-project confirmation, and colliding paths are rejected before writing. |
| Batch interruption and recovery | A failed later copy leaves the source namespaces in place. An already-created first link survives a second conflict. Source-list readbacks confirm each project independently, preserving unselected sources and the primary role. |
| Batch old-chat acceptance | A project moves only one unshared secondary source, keeps its primary and other sources intact, and tests both write directions from an old chat's primary working directory. Wrong project or session, unrelated working directory and requests without host evidence cannot pass. A shared backup becomes eligible only after both projects have real filesystem probes with matching synthetic host attestation. A late file change invalidates its previous backup comparison. |
| Resume inspection | Manual copies and moves, correct or broken old links, an existing single-folder checkpoint, and a partially completed batch produce distinct next steps. Repeated inspection preserves files, links and synthetic project and chat records. |

Tests drive the shipped CLI programs. Desktop source changes and old-chat working directories are represented with synthetic fixtures. No test uses the Codex desktop UI, edits a real project, reads a real chat or sends cross-chat messages. To accept a live migration, verify the chosen existing chat through the skill's documented workflow.

## After testing / 测试后清理

Keep both generated `migration-tests-…` folders to inspect the evidence. The target-side folder contains `result.json`. When finished, you can manually remove the two exact fixture folders printed by the runner. Removing one side breaks the matching **test** junction or worktree on the other side. Leave the parent scratch directories and any real project paths alone.

两个测试根目录各会包含一个 `migration-tests-…` 子目录；目标侧还存有本轮 `result.json`。部分小型 Git 工作树和目录联接会连接这两处测试数据。需要复查时保留两边。测试完成后如决定清理，在资源管理器中按运行前打印的**两个确切测试路径**手动移除这两棵测试目录。清除其中一侧，会使另一侧对应的测试联接或 Git 工作树失去目标。不要通过某个联接进入正式盘目录清理文件。测试程序不自动批量清理目录。
