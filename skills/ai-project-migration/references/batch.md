# Codex 多源与共享目录批次

本入口适用于同机 Codex 本地项目的新迁移，包含选中次要源、多个实体目录，或选中被多个项目引用的目录的情况。即使只选中一个独享次要源，也使用批次验收。项目 ID 与各自的主要、次要角色决定怎样换源。一个目录实体只复制、改名、建联接一次。选中的共享源要同步所有已确认引用。

## 1. 核对范围和建立批次

从「编辑项目」读取每个待核项目的完整来源和主要角色。项目简表只给出主要目录时，用可用的桌面入口或用户读回补齐次要源。发现覆盖不完整，先补证再标记 `complete`。配置预览列出每个目录的旧地址、目标、引用项目及角色、未选来源和验收范围。同一实体的不同路径先解析。重叠目录、多个目标指向同一地址或同一 Git 工作树被当作独立项目源，先处理冲突。

按[迁移环境预检](preflight.md)核对本机环境和每个已确认的源、目标。全部适用项通过后，将两份输入文件写到项目树外的本机私有审计目录。示例中的宿主、项目和路径要替换为已经回读的数据。

```json
{
  "version": 1,
  "host_id": "local",
  "complete": true,
  "projects": [
    {
      "id": "demo",
      "name": "Demo",
      "sources": [
        {"path": "E:/Example/Demo", "role": "primary"},
        {"path": "E:/Example/Assets", "role": "secondary"}
      ]
    },
    {
      "id": "library",
      "name": "Library",
      "sources": [
        {"path": "E:/Example/Assets", "role": "primary"}
      ]
    }
  ]
}
```

```json
{
  "sources": [
    {"source": "E:/Example/Demo", "target": "F:/Example/Demo", "project_ids": ["demo"]},
    {"source": "E:/Example/Assets", "target": "F:/Example/Assets", "project_ids": ["demo", "library"]}
  ],
  "allow_same_volume": false
}
```

只迁一个独享次要源时，项目快照仍包含完整来源清单。选择文件的 `sources` 只填该次要源和它的目标，`project_ids` 只填它所属的项目。主要来源和其他未选来源留在原配置中。

源和目标以最终实际实体为准。同盘移动须像单目录流程一样明确告知用户空间影响，经确认才设置 `allow_same_volume`。如果目标已存在于旧的单目录迁移，沿旧检查点完成这一项，剩余明确选中的目录另建批次。保留两边和已复制的候选文件，避免重复搬运或借用另一个运行的回执。发现未支持的重解析点时提示具体路径，先由用户决定如何保全，禁止静默跳过、展开或清理。

所有命令以绝对脚本路径执行。下例用 `BATCH` 表示新建批次目录，用 `PLAN` 表示其 `batch.json` 文件，用 `SNAPSHOT` 表示当前刚回读的完整源配置。涉及写入前核对用户批准的确切范围。

```text
project_batch.py preview --snapshot PROJECTS.json --selection SELECTION.json
project_batch.py create --snapshot PROJECTS.json --selection SELECTION.json --batch BATCH
project_batch.py stage --batch BATCH
project_batch.py status --batch BATCH
```

先运行只读预览，给用户列出源、目标和各项目角色。建立批次后先保存所有原聊天的基准，再开始复制。可在确认范围内逐份复制和独立校验；某份停止时保留全部旧实体及已验候选。从状态读回后接续，所有副本通过之前暂不换源。

## 2. 接好原名入口，再逐项目换源

全部副本核验后确认各受影响项目已暂停写入。在原名仍为实体时，处理并核验必要的 Git 工作树关联。校验两边文件及 Git 回执。选 A 手动改名或 B 让命令改名，只运行所选模式的 `prepare`。

```text
project_batch.py prepare --batch BATCH --mode manual
project_batch.py prepare --batch BATCH --mode auto
```

退出前按[手动终端指引](terminal.md#offline-cutover)交付 `prepare` 生成的那一段批次离线命令及对应系统的图示。选 A 时，列出需要手动改名的每个原件和计划中对应的备份完整路径。选 B 时，交付直接运行命令的步骤。说明成功时每个旧入口都应指向各自的目标，备份仍是原实体。

离线链接以批次计划和每个原/目标实体的身份为准。重启后读取 `status`。发生占用、中断或阻塞，按实际 ready/renamed/linked 状态续做，保留已完成入口和对应实体备份。

所有原名入口通过后，对每个项目先读回原 ID、来源和角色，调用 `confirm` 看实际配置是否需要编辑。若需要，在 Codex 的「编辑项目」换掉已选源，保留其他来源及其主要、次要角色，再读回实际保存结果。工具不可用时向用户提供该项目要操作的确切条目。已符合预期的项目直接跳过。偏离基准或计划的配置由用户确认差异；切换过程与项目配置之间不存在一个原子提交。

```text
project_batch.py confirm --batch BATCH --snapshot SNAPSHOT --project-id ID
```

## 3. 分开验证历史、代表旧聊天与新聊天

在复制前分页收集每个所属项目的原会话 ID、旧工作目录、是否归档以及历史可读性。工作树和子目录保留实际相对位置。共享文件夹下没有明确项目归属的会话单列，避免按同一个 cwd 武断分组。换源后按相同 ID 验证旧历史及工作目录。写入审计的只需元数据和覆盖范围。

```text
batch_evidence.py history --batch PLAN --phase before --input HISTORY_BEFORE.json
batch_evidence.py history --batch PLAN --phase after --input HISTORY_AFTER.json
```

历史输入以 `host_id` 标识同一个 Codex 宿主，`projects` 每项记录 `id`、`complete` 与 `sessions`。每条 session 记录 `id`、`cwd`、`history_readable` 和 `archived`。历史不完整时标记实际覆盖，保持备份。

针对每个「引用项目 × 已迁来源」选代表旧聊天。其默认工作目录就是所选来源时，直接验证该目录。若此来源为次要且没有从它启动的旧聊天，选本项目从主要或其他已登记来源启动的一条原聊天，从它的实际默认目录启动探针。探针通过该次要源的旧绝对路径完成双向读写，记录为 `attached_source`。保持会话默认目录不变，既不切换目录，也不替探针设置工作目录。记录旧链接、目标及同文件身份；清理的范围仅限探针临时文件。

```text
batch_evidence.py register --batch PLAN --project-id ID --source-id SOURCE --session-id ORIGINAL --request-id UUID
probe.py --batch PLAN --request-id UUID
batch_evidence.py attest --batch PLAN --request-id UUID --input SESSION_PROOF.json
batch_evidence.py new-chat --batch PLAN --input NEW_CHAT.json
```

探针只能在选定的真实旧聊天执行。代发时须有相应接口，并在本次操作中让用户点名并明确授权；也可让用户手动贴提示词。登记后使用同一请求 UUID，工具超时先找回执，避免重复发送。CLI 只能验证实际读写，由宿主确认收到回执的仍是同一个原会话。会话证明包含 `host_id`、`project_id`、`session_id` 和 `request_id`。新本地聊天由用户打开或明确授权创建，核对其主要默认目录以及实际可读的附加来源。新聊天证明包含 `host_id`、`project_id`、`session_id`、`cwd` 和实测可读的 `readable_sources`。只读、未执行、原身份或工作目录不明，分别列为待验证。

## 4. 按来源收尾

对每份实体备份重新核验备份、目标和 Git 状态，使用本次所做的真实产物。随后用最新实际项目配置查询批次收尾状态。

```text
batch_evidence.py status --batch PLAN --snapshot SNAPSHOT
```

逐来源报告所有引用项目的实际配置、旧历史核对数、代表性实际读写和新聊天验证覆盖。共享来源的所有引用项目都通过才提示可以手动清理其备份。单目录子回执不能单独放行。收尾和再次查看清理提示时，按来源列出三条完整路径，先展示 `status` 返回的 `warning`。已确认入口直达目标时，保留回执中的实际链接类型、空间说明和删除风险。未完成显示 **⛔ 暂勿清理备份**，同一条消息按[阶段汇报与接续](../SKILL.md#阶段汇报与接续)指出该来源还缺哪一项核验、由谁补证，以及何时复核批次结果。
