# HuntingBlade CLI 英文参数与中文说明收口设计

**日期**：2026-04-06  
**项目**：HuntingBlade（CTF Agent Fork）  
**目标**：在保留凌虚赛事 CTF 接入、`ctf-import` 手动导题和递归附件支持的前提下，把 CLI 收口为“参数名保持英文，帮助文本/README/示例说明使用中文”的最终体验，并同步修正文档口径。

---

## 1. 背景

前两个任务已经完成两项关键能力：

1. 接入凌虚赛事 CTF 平台
2. 新增 `ctf-import` 手动导题能力，并支持目录型附件递归导入

但在其中一个实现线程里，对“CLI 中文化”的理解出现了偏差：

- 正确需求是：**帮助文本、README、示例说明中文化**
- 错误实现是：**把长参数名也翻译成中文**

这会带来两个直接问题：

1. 破坏 CLI 工具的通用使用习惯，降低脚本可移植性
2. 让 README、计划文档和实际代码之间出现相互矛盾的口径

因此，需要新增一次“收口型修正”，统一最终对外规则，并把相关文档一并纠偏。

---

## 2. 设计目标

本次收口的设计目标如下：

1. `ctf-solve`、`ctf-msg`、`ctf-import` 的参数名全部保持英文
2. `--help`、命令 docstring、README、示例命令解读全部使用中文说明
3. 保留已完成的功能能力，不回退凌虚平台接入、手动导题和递归附件支持
4. 将历史 plan / spec 中错误的“中文参数”描述修正为统一口径
5. 为第三次任务新增独立的中文 spec 和 plan，保证三次任务都有对应文档沉淀

---

## 3. 非目标

本次修正明确不做以下事项：

1. 不更改命令名，仍使用 `ctf-solve`、`ctf-msg`、`ctf-import`
2. 不新增中英文双写参数别名
3. 不修改内部配置字段命名，例如 `Settings.platform_url`
4. 不改变凌虚平台、手动导题、递归附件的核心行为
5. 不处理与本次任务无关的既有 lint 问题

---

## 4. 最终 CLI 原则

### 4.1 参数命名原则

所有命令统一遵循：

- 参数名使用英文
- 选项说明使用中文
- 命令输出摘要可以使用中文

例如：

- 正确：`--ctfd-url`、`--platform-url`、`--msg-port`
- 错误：`--ctfd地址`、`--平台地址`、`--消息端口`

### 4.2 `ctf-solve`

`ctf-solve` 保持英文参数，重点包括：

- `--platform`
- `--platform-url`
- `--lingxu-event-id`
- `--lingxu-cookie`
- `--lingxu-cookie-file`
- `--ctfd-url`
- `--ctfd-token`
- `--challenge`
- `--challenges-dir`
- `--no-submit`
- `--coordinator`
- `--coordinator-model`
- `--max-challenges`
- `--msg-port`
- `-v/--verbose`

其帮助文本、命令说明、README 示例解释均使用中文。

### 4.3 `ctf-msg`

`ctf-msg` 保持：

- `MESSAGE`
- `--port`
- `--host`

帮助文本中文化，例如“向运行中的协调器发送消息”。

### 4.4 `ctf-import`

`ctf-import` 保持：

- `--name`
- `--category`
- `--description`
- `--connection-info`
- `--attachment`
- `--attachment-dir`
- `--output-dir`
- `--value`
- `--tag`
- `--hint`

导入成功后的命令输出采用中文摘要，便于本地操作人员快速确认结果。

---

## 5. 文档同步原则

### 5.1 README

README 需要满足以下要求：

1. 用中文解释项目结构、运行流程和使用方法
2. 所有命令示例中的参数名保持英文
3. 补充 `ctf-import` 用法
4. 补充凌虚赛事 CTF 的使用方式、限制范围和 Cookie 说明
5. 明确说明“参数名英文，帮助与说明中文”的规则

### 5.2 历史文档纠偏

已有文档中，如果仍写着“中文参数”“中文 Click 选项”“through Chinese CLI”等表述，需要统一修正。

本次至少要修正：

- `docs/superpowers/plans/2026-04-06-lingxu-event-ctf-integration.md`

以下文档若已经明确“参数名保留英文”，则不需要改动：

- `docs/superpowers/specs/2026-04-06-manual-challenge-import-design.md`
- `docs/superpowers/plans/2026-04-06-manual-challenge-import-cli.md`
- `docs/superpowers/specs/2026-04-06-lingxu-event-ctf-design.md`

---

## 6. 兼容性决策

本次修正后的最终决策是：

1. **英文参数是唯一正式接口**
2. **中文帮助文本是唯一需要中文化的 CLI 层**
3. **不保留中文长参数别名**

原因如下：

- CLI 工具默认应适合脚本、文档和团队协作复用
- 双写别名会增加长期维护成本
- 本次错误实现尚未作为最终 `main` 分支规范对外固化，及时纠偏成本最低

---

## 7. 测试要求

新增或更新 `tests/test_cli.py`，至少覆盖以下内容：

1. `ctf-solve --help` 中帮助文本为中文，但选项名为英文
2. `ctf-msg --help` 中帮助文本为中文，但选项名为英文
3. `ctf-import --help` 中帮助文本为中文，但选项名为英文
4. `pyproject.toml` 暴露 `ctf-import` 脚本入口
5. `ctf-import` 能使用英文参数创建本地题目目录
6. 凌虚平台参数通过英文选项进入 `Settings`
7. `_run_single()` 使用平台工厂，而不是写死 `CTFdClient`

此外需要执行 CLI smoke：

- `uv run ctf-solve --help`
- `uv run ctf-msg --help`
- `uv run ctf-import --help`

---

## 8. 验收标准

满足以下条件即可验收：

1. `ctf-solve`、`ctf-msg`、`ctf-import` 的所有参数名均为英文
2. 三个命令的帮助文本和 README 说明均为中文
3. README 已补充凌虚赛事 CTF 与 `ctf-import` 的最终用法
4. 历史文档中不再残留“中文参数化”的错误指引
5. 第三次任务拥有独立的中文 spec 与 plan 文档
6. 相关测试、lint 和 help smoke 均通过
