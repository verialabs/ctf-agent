# HuntingBlade 手动导题与中文文档设计

**日期**：2026-04-06  
**项目**：HuntingBlade（CTF Agent Fork）  
**目标**：新增 `ctf-import` 命令，把人工整理的题目信息落盘为现有本地题目目录结构，并让 README、帮助文本、使用示例改为中文说明，但 CLI 参数名保持英文。

---

## 1. 背景

HuntingBlade 当前的自动拉题流程以 CTFd 为中心，但真实比赛里经常会遇到：

- 非 CTFd 平台
- 临时发附件、临时发题面、临时发连接信息
- 平台登录态复杂，不适合立刻做自动接入

这类场景下，操作者更需要一条“把手里的题目材料快速导成标准目录”的路径，而不是强制走平台适配。

同时，本项目面向中文使用者，帮助文本、README 和示例命令需要中文化；但 CLI 作为开发工具，参数名保持英文更符合通用习惯，也更利于脚本兼容。

---

## 2. 设计目标

1. 新增 `ctf-import`，把人工输入的题目名称、类型、描述、连接信息、附件文件、附件目录转成标准本地题目目录。
2. 继续复用现有 `metadata.yml + distfiles/` 协议，不引入第二套目录结构。
3. 支持递归导入目录型附件，并让 prompt 能看到子目录中的文件。
4. 同名题目目录默认覆盖替换，避免旧文件残留。
5. CLI 参数名保留英文，帮助文本、README、示例说明使用中文。

---

## 3. 非目标

1. 不为非 CTFd 平台实现自动登录或自动拉题。
2. `ctf-import` 不负责导入后自动启动求解。
3. 不修改 solver 总体策略。
4. 不把 CLI 参数翻译成中文。

---

## 4. 功能范围

### 4.1 新增命令

- 命令名：`ctf-import`
- 建议参数：
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

### 4.2 输出目录结构

```text
<output-dir>/<slug>/
  metadata.yml
  distfiles/
```

其中：

- `slug` 基于题目名称生成，保持与现有拉题命名风格接近
- `metadata.yml` 可直接被 `ChallengeMeta.from_yaml()` 消费
- `distfiles/` 继续作为 solver 读取附件的唯一约定目录

### 4.3 元数据映射

```yaml
name: 题目名称
category: 题目类型
description: 题目描述
value: 分值
connection_info: 连接信息
tags:
  - 标签1
hints:
  - cost: 0
    content: 提示1
solves: 0
```

### 4.4 覆盖与回滚

- 如果目标题目目录已存在，默认覆盖
- 覆盖时先把旧目录改名为临时备份
- 新目录落盘失败时，自动恢复旧目录
- 成功后删除备份目录

### 4.5 冲突与校验

- `name`、`category`、`description` 不能为空
- `connection_info`、`attachment`、`attachment_dir` 至少提供一项
- `attachment` 必须存在且是文件
- `attachment_dir` 必须存在且是目录
- 如果多个来源映射到同一个 `distfiles/` 相对路径，直接报错
- 大小写仅不同的目标路径也视为冲突

---

## 5. Prompt 侧配合改造

当前 `list_distfiles()` 只列出顶层文件，这不足以支持目录型附件。需要改为：

- 递归列出 `distfiles/` 下所有文件
- 返回相对路径，例如 `src/main.py`
- 图片文件即使位于子目录，也能继续触发视觉分析提示

---

## 6. CLI 与文档原则

最终对外体验遵循两条规则：

1. **参数名保留英文**
   - 例如 `--ctfd-url`、`--challenge`、`--msg-port`
   - `ctf-import` 也使用英文参数
2. **帮助文本与文档中文化**
   - `--help` 中的说明、README 中的操作示例、使用解释全部使用中文

这样既不破坏 CLI 工具的常见使用习惯，也满足中文团队的上手体验。

---

## 7. 测试要求

1. `tests/test_challenge_import.py`
   - 覆盖 metadata 落盘
   - 覆盖递归附件复制
   - 覆盖覆盖替换与失败回滚
   - 覆盖路径冲突、空输入、缺失文件
2. `tests/test_prompts.py`
   - 覆盖递归附件枚举
   - 覆盖子目录图片仍能触发视觉提示
3. CLI 集成测试放在 CLI 统一整理阶段一起补

---

## 8. 验收标准

满足以下条件即可验收：

1. 操作者能用 `ctf-import` 生成合法本地题目目录
2. 导出的目录能被 `ctf-solve --challenge ...` 直接消费
3. 目录型附件会递归进入 `distfiles/`
4. 覆盖替换不会留下旧残留文件
5. 帮助文本和 README 使用中文说明，但参数名保持英文
