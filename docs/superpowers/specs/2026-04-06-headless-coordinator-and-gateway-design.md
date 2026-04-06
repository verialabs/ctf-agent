# HuntingBlade 无总控整场模式与网关切换设计

**日期**：2026-04-06  
**项目**：HuntingBlade  
**目标**：新增 `--coordinator none` 的“无总控整场模式”，同时把项目默认 OpenAI 兼容网关切换到新的稳定地址，并补齐 README 中针对凌虚赛事 CTF 的最新详细使用说明。

---

## 1. 背景

当前项目的整场模式只有两种入口：

1. `--coordinator claude`
2. `--coordinator codex`

这两种模式都依赖一个顶层 LLM 协调器持续观察赛事状态、决定何时拉题、何时起 swarm、何时查看 trace、何时广播策略。

但在实际使用里已经暴露出两个问题：

1. 某些模型网关对 `responses` 或 `chat/completions` 的支持不稳定，导致顶层协调器和 solver 都可能在第 0 步失败。
2. 当用户只希望“自动拉题 + 自动起 swarm + 自动监控已解题状态”，并不一定需要再额外消耗一个顶层大模型作为总控。

同时，这个项目已经接入凌虚赛事 CTF，README 需要把此前实际验证过的使用方式写清楚，包括：

- Cookie 只带 `sessionid` 也可以工作
- 题目会自动拉取到本地
- 容器题环境会在需要时自动预热
- 模型网关如何配置
- 如何根据并发上限选择整场模式

---

## 2. 设计目标

1. 保留现有 `claude` / `codex` 协调器模式，不破坏已有工作流。
2. 新增 `--coordinator none`，让项目在没有顶层 LLM 总控时也能跑完整场自动调度。
3. `none` 模式下仍然复用现有 poller、platform client、swarm、自动拉题和自动起环境能力，避免分叉出第二套编排逻辑。
4. 项目默认配置切换到新的稳定网关 `https://api.masterjie.eu.cc/v1`。
5. README 明确区分三种整场模式的适用场景，并给出凌虚赛事 CTF 的最新推荐命令。

---

## 3. 非目标

1. 不移除现有 `claude` / `codex` 协调器实现。
2. 不改 solver 的核心求解逻辑。
3. 不为 `none` 模式引入复杂的“伪智能”调度规则；它只负责稳定地自动编排，不负责策略推理。
4. 不把 CLI 参数改成中文。

---

## 4. 方案选择

### 方案 A：继续强依赖 `claude/codex` 顶层协调器

优点：

- 代码改动最少
- 保持现有架构单一路径

缺点：

- 顶层协调器继续成为单点故障
- 多占 1 个模型并发
- 网关不稳时整场直接不可用

### 方案 B：新增 `--coordinator none`，复用共享事件循环做“无总控整场模式”

优点：

- 不再依赖顶层协调器模型
- 减少并发占用
- 失败面更小，尤其适合只想稳定跑整场的场景

缺点：

- 会失去总控 LLM 的高级策略能力
- 需要在共享事件循环里补一个“无总控 turn”分支

### 方案 C：单独做一套 headless 编排器

优点：

- 逻辑最纯粹，语义最清晰

缺点：

- 和现有 `coordinator_loop`、`coordinator_core` 重复
- 后续维护成本更高

**选型**：采用方案 B。  
原因：它能最大化复用当前共享事件循环和自动起 swarm 逻辑，改动集中、风险可控，同时解决“没有总控也要能跑整场”的核心问题。

---

## 5. 核心设计

### 5.1 CLI 行为

`backend/cli.py` 中把 `--coordinator` 的可选值从：

- `claude`
- `codex`

扩展为：

- `claude`
- `codex`
- `none`

帮助文本要明确说明：

- `claude` / `codex`：启用顶层 LLM 协调器
- `none`：无总控整场模式，只做自动拉题、自动起 swarm、自动监控状态

### 5.2 无总控模式的执行方式

无总控模式不应该复制一套新的事件循环，而是继续使用 `backend/agents/coordinator_loop.py` 的共享 `run_event_loop()`。

差异只在于传入的 `turn_fn`：

- `claude/codex`：`turn_fn` 会把消息发给顶层 LLM 协调器
- `none`：`turn_fn` 不调用任何模型，只把事件记录到日志

这样仍然保留以下行为：

1. 启动前校验平台访问
2. 启动 poller 获取题目与已解状态
3. 初始时自动为所有未解题尝试起 swarm
4. 新题出现时自动拉题并起 swarm
5. 某题被解出时自动停掉对应 swarm
6. 暴露 `ctf-msg` 可用的 operator message HTTP 端口

其中第 6 点即使在 `none` 模式下也保留，但不会再把消息喂给 LLM，只记录为操作员事件，便于后续排错和统一体验。

### 5.3 `none` 模式下的用户感知

控制台启动提示要改成可区分的文案，例如：

- `Starting coordinator (claude, Ctrl+C to stop)...`
- `Starting coordinator (codex, Ctrl+C to stop)...`
- `Starting coordinator (none/headless, Ctrl+C to stop)...`

最终结果输出保持一致，避免用户需要记两套收尾方式。

### 5.4 网关切换

新的稳定网关已经验证支持：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`

因此项目级配置需要统一切到：

- `OPENAI_BASE_URL=https://api.masterjie.eu.cc/v1`
- `AZURE_OPENAI_ENDPOINT=https://api.masterjie.eu.cc/v1`

README 和 `.env.example` 也同步更新，但不写入真实密钥。

用户自己的全局 Codex 配置如果已经切换，本次实现不重复覆盖；README 只说明推荐配置方式。

---

## 6. README 要补充的重点

README 需要把下面这些此前对话里已验证的信息系统化写进去：

1. **凌虚 Cookie 规则**
   - `sessionid` 必需
   - `csrftoken` 可选，不是硬要求

2. **整场自动行为**
   - 自动读取赛事题单
   - 自动拉取题面和附件
   - 容器题需要环境时会自动尝试预热
   - `--no-submit` 只是不交 flag，不会阻止拉题和起 swarm

3. **三种协调模式**
   - `claude`：需要本机可用 Claude 相关能力
   - `codex`：需要本机 `codex` 可用且网关支持 `responses`
   - `none`：不需要顶层总控模型，适合网关并发紧张或稳定性优先的场景

4. **并发建议**
   - 近似并发消耗 = `max_challenges * 模型数 + 顶层协调器(claude/codex 时额外 1)`
   - 当上游网关并发有限时，优先降低 `--max-challenges` 或减少 `--models`
   - 若只想稳定跑整场，优先使用 `--coordinator none`

5. **推荐命令**
   - 凌虚赛事 CTF 的 `none` 模式推荐命令
   - 单题模式推荐命令
   - `ctf-msg` 仅对带消息端口的整场模式有效

---

## 7. 测试要求

### 7.1 CLI 测试

在 `tests/test_cli.py` 中新增或调整测试，覆盖：

1. `--help` 中出现 `none`
2. `main()` 接受 `--coordinator none`
3. `_run_coordinator()` 在 `none` 模式下走新的 headless 分支

### 7.2 共享协调器测试

在 `tests/test_coordinator_platform_flow.py` 中覆盖：

1. `none` 模式仍会跑 `validate_access()`
2. 初始未解题仍会自动起 swarm
3. 不依赖任何 LLM turn，即可完成 headless 事件循环的一轮启动

### 7.3 文档与配置验证

1. `.env.example` 不包含真实密钥
2. README 中存在 `--coordinator none` 的使用说明
3. README 中示例网关切换为新的稳定地址

---

## 8. 验收标准

满足以下条件即可验收：

1. `ctf-solve --coordinator none ...` 可以进入整场自动编排流程。
2. `none` 模式下，题目仍会自动拉取、本地落盘、自动起 swarm。
3. `claude` / `codex` 旧模式不回归。
4. `.env.example` 与 README 都切换到新的稳定网关写法。
5. README 能让首次接触项目的中文使用者读懂凌虚赛事 CTF 的完整使用路径。
