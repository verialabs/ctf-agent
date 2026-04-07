# HuntingBlade 显式策略、单题记忆与跨题知识控制架构设计

**日期**：2026-04-07  
**项目**：HuntingBlade  
**目标**：在保留现有 `poller -> coordinator -> swarm -> solver` 主骨架的前提下，把隐藏在 prompt 和运行时细节中的调度逻辑显式化，同时补上单题短期记忆与跨题知识复用能力，为后续稳定性重构和对外架构叙事提供真实基础。

---

## 1. 背景

当前系统已经不是单一线性执行器，而是具备四层粗粒度结构：

1. 平台同步层：`backend/poller.py`
2. 顶层协调层：`backend/agents/coordinator_loop.py`、`backend/agents/*_coordinator.py`
3. 题目并发层：`backend/agents/swarm.py`
4. 单 solver 执行层：`backend/agents/solver.py`

现状的主要问题不是“没有多层”，而是这些层之间的状态、策略与反馈闭环仍然是隐式的：

1. 调度策略主要藏在 coordinator prompt 中，不是可审计、可替换的策略对象。
2. solver 有 trace、有 loop detector、有 bump，但没有正式的单题短期记忆抽象。
3. 系统有广播和 findings 注入能力，但没有跨题知识沉淀与晋升规则。
4. coordinator 的事件循环当前更像“事件泵 + LLM turn 转发器”，而不是“状态机 + 策略 tick”。
5. `CoordinatorDeps` 逐步承担越来越多运行态字段，长期会演化成难以维护的共享大包。

如果继续直接在现有结构上叠功能，短期仍可运行，但中期会出现三个问题：

1. 解题率提升依赖 prompt 微调，缺少稳定的策略抓手。
2. 稳定性重构会在没有清晰状态边界的情况下被迫大拆。
3. 对外再去讲 `Plan + React + Memory + Tools + Knowledge` 时，容易停留在文案层，而不是代码层。

因此需要先把控制架构升级为“显式状态、显式策略、显式记忆、显式知识”的形态。

---

### 1.1 实现状态（截至 2026-04-07）

这份设计文档以“目标形态”为主叙述，但对应的第一阶段控制面已经落地进代码，并接入共享 `backend/agents/coordinator_loop.py` 事件循环。

已完成（已实现并接线）：

- [x] 显式 `Runtime State`：`backend/control/state.py`（`CompetitionState / ChallengeState / SwarmState` + `build_runtime_state_snapshot`）
- [x] `Working Memory`：`backend/control/working_memory.py`（trace 增量提炼、单题摘要）
- [x] `Knowledge Store`：`backend/control/knowledge_store.py`（晋升、匹配、已应用保护）
- [x] `Policy Engine`：`backend/control/policy_engine.py`（规则优先决策、动作列表生成）
- [x] `Advisor` 接口：`backend/control/advisor.py`（以及 `backend/agents/{azure,claude,codex}_coordinator.py` 的 advisor 适配器）

仍复用既有模块（本阶段不额外拆成新对象）：

- 平台侧事实状态（`Platform State`）：继续由 `poller + platform client` 提供 `known_challenges/known_solved` 等平台事实，再由 runtime snapshot 汇总进入 `CompetitionState`。

待后续（刻意未做）：

- [ ] 更深的稳定性/容错重构（例如进一步状态机化、隔离 I/O 与纯决策、回放/审计工具链完善）
- [ ] 递归式或多层 coordinator 架构（coordinator of coordinators）
- [ ] 更复杂的长期知识库与检索（向量数据库、跨比赛持久化、RAG 等）

## 2. 设计目标

1. 在不推翻现有 solver 与 swarm 主体实现的前提下，引入正式的控制面抽象。
2. 把“什么时候 spawn、什么时候 bump、什么时候 broadcast、什么时候暂缓”从 prompt 语义提升为显式策略逻辑。
3. 为每道题建立结构化 `Working Memory`，减少重复试错和无效 bump。
4. 为整场比赛建立结构化 `Knowledge Store`，支持跨题知识复用，但避免把原始 trace 噪声直接沉淀为知识。
5. 让不同 coordinator provider 只负责推理适配，不再承载业务状态与完整控制逻辑。
6. 为下一阶段的稳定性重构提供清晰边界，使状态机、容错和 provider 兼容性改造有明确落点。

---

## 3. 非目标

1. 第一阶段不引入“递归式 coordinator of coordinators”。
2. 第一阶段不重写 `backend/agents/solver.py` 为多层 planner-executor 模型。
3. 第一阶段不引入向量数据库或复杂 RAG 基础设施。
4. 第一阶段不做跨比赛永久知识库，只关注单场比赛运行期知识。
5. 第一阶段不一次性重构所有 provider runtime，只处理控制面抽象。

---

## 4. 方案比较

### 方案 A：在现有骨架内新增显式控制层

做法：

- 保留现有 `poller`、`swarm`、`solver`、`coordinator_core`
- 新增 `state / actions / policy_engine / working_memory / knowledge_store`
- 让 `coordinator_loop` 从“事件驱动 turn 转发”升级为“状态更新 + 策略 tick + 动作执行”

优点：

- 与现有代码最兼容
- 可以同时推进策略、单题记忆、跨题知识三条线
- 重构风险可控，适合分阶段落地

缺点：

- 第一阶段会出现“旧 coordinator 外壳 + 新控制内核”并存
- 需要花精力明确新旧职责边界

### 方案 B：直接改造成递归式双引擎架构

做法：

- 顶层 coordinator 生成子计划树
- 子计划继续分裂成下级 coordinator 与执行单元
- 记忆与知识同时作为全局上下文注入

优点：

- 架构叙事强
- 从概念上最接近参考案例中的递归式双引擎

缺点：

- 当前阶段复杂度过高
- 容易在没有稳定状态模型前引入过度设计
- 一旦执行链不稳定，调试成本会非常高

### 方案 C：先做记忆系统，再慢慢补策略层

做法：

- 先补 trace 提炼、失败去重、knowledge sink
- 保留 coordinator 基本按 prompt 驱动

优点：

- 落地阻力最小
- 对现有运行链影响最小

缺点：

- 策略层仍然隐式
- 记忆与知识很容易沦为“日志附属品”，无法真正驱动调度

**选型**：采用方案 A。  
原因：用户希望先做解题率提升，再做稳定性，最后做架构叙事。方案 A 能同时托住这三者；方案 B 太早，方案 C 太弱。

---

## 5. 目标架构

目标架构保留现有主干，但把控制面拆成六个职责明确的层：

1. `Platform State`：平台事实与题目状态
2. `Policy Engine`：调度决策
3. `Action Executor`：执行控制动作
4. `Working Memory`：单题短期记忆
5. `Knowledge Store`：跨题知识复用
6. `Solver Runtime`：单模型 ReAct 工具执行

### 5.1 总体数据流

```text
Poller / Platform Events
    -> Runtime State Update
    -> Working Memory Refresh
    -> Knowledge Lookup / Promotion
    -> Policy Engine Tick
    -> Action List
    -> Action Executor
    -> Swarm / Solver Runtime
    -> Trace / Findings / Results
    -> State & Memory Write-back
```

这个数据流意味着：

1. LLM 不再等同于整个 coordinator，而是策略推理链路中的一个部件。
2. coordinator provider 差异会收缩到“如何推理”和“如何调用模型”，而不是“如何管理整场比赛”。
3. 状态、策略、执行、记忆、知识之间的边界会变得可测试、可追踪。

---

## 6. 核心模块设计

### 6.1 `Runtime State`

新增模块建议：`backend/control/state.py`

职责：表达当前系统的事实状态，不夹带策略判断。

建议对象：

#### `CompetitionState`

字段建议：

- `known_challenges`
- `known_solved`
- `active_swarms`
- `results`
- `global_cost_usd`
- `last_poll_at`
- `operator_messages`

职责：

- 表示整场比赛范围内的总状态
- 为策略层提供全局视图

#### `ChallengeState`

字段建议：

- `challenge_name`
- `status`
- `category`
- `value`
- `requires_env_start`
- `unsupported_reason`
- `last_materialized_at`

职责：

- 表示单题在平台与本地材料化链路中的状态

#### `SwarmState`

字段建议：

- `challenge_name`
- `running_models`
- `last_bump_at`
- `bump_count`
- `last_progress_at`
- `last_error`
- `step_count`
- `cost_usd`
- `winner_model`

职责：

- 记录每道题的求解执行态
- 作为 policy 决策的主要输入之一

设计原则：

1. `state` 只回答“现在是什么状态”
2. 不在 `state` 中直接写“应该做什么”
3. 所有派生结论由 policy 层生成，而不是写死在数据对象里

### 6.2 `Policy Engine`

新增模块建议：`backend/control/policy_engine.py`

职责：根据运行态、记忆与知识生成结构化动作。

输入：

- `CompetitionState`
- `ChallengeState`
- `SwarmState`
- `Working Memory` 摘要
- `Knowledge Store` 命中结果
- 并发、成本、冷却和节流规则

输出：

- 一组结构化 `Action`

建议判断内容：

1. 哪些未解题应该优先 spawn
2. 哪些 swarm 已经卡住，需要 bump
3. 哪些 findings 值得广播给同题其他 solver
4. 哪些经验可以晋升为跨题知识
5. 哪些题应该暂缓，而不是无止尽重复尝试

这层的定位不是“全规则系统”或“全 LLM 系统”，而是混合式策略：

- 规则负责硬约束：并发上限、冷却时间、重复 bump 防抖、非法动作阻断
- LLM 负责软判断：优先级、战术提示、下一步可能的技术路线

### 6.3 `Action Model`

新增模块建议：`backend/control/actions.py`

职责：定义 policy 产出的标准动作对象，使决策层与执行层解耦。

第一阶段动作建议：

- `SpawnSwarm(challenge_name, priority, reason)`
- `BumpSolver(challenge_name, model_spec, guidance, reason)`
- `BroadcastKnowledge(challenge_name, message, source)`
- `HoldChallenge(challenge_name, reason, retry_after_seconds)`
- `RetryChallenge(challenge_name, reason)`
- `MarkChallengeSkipped(challenge_name, reason)`

设计要求：

1. `Action` 是结构化对象，不是自由文本
2. `Action` 必须可以被记录、审计和回放
3. `Action` 执行前后要能写回状态，形成闭环

### 6.4 `Working Memory`

新增模块建议：`backend/control/working_memory.py`

职责：保存单题短期记忆，避免同题反复犯同样错误。

作用域：每道题一份，归属 swarm，而不是单个 solver transcript。

建议字段：

- `attempted_actions`
- `failed_hypotheses`
- `open_hypotheses`
- `verified_findings`
- `useful_artifacts`
- `last_guidance`
- `recent_solver_summary`

数据来源：

- solver trace
- `submit_flag` 结果
- `check_findings` 注入结果
- bump 历史
- swarm 收尾结果

更新规则：

1. 不是每一步工具调用都进入 working memory
2. 只有“可概括的失败”“可复用的中间结论”“待验证的分支”才进入 memory
3. working memory 需要维持短而精，不做 transcript 复制品

### 6.5 `Knowledge Store`

新增模块建议：`backend/control/knowledge_store.py`

职责：保存跨题共享知识，支撑整场比赛复用。

作用域：整场比赛运行期全局共享。

建议字段：

- `id`
- `scope`
- `kind`
- `content`
- `evidence`
- `confidence`
- `source_challenge`
- `applicability`

建议知识类别：

- 平台规律
- flag 格式规律
- 题型套路
- 可复用 exploit pattern
- 框架与协议特征

晋升规则：

1. 题目私有细节默认不晋升为全局知识
2. 必须有明确证据，不能只因为模型猜测就入库
3. 被多次验证或多题复用的经验，优先提升置信度

### 6.6 `Action Executor`

第一阶段不单独拆文件也可以，但建议由 `coordinator_core` 承担动作执行库角色。

执行策略：

1. `policy_engine` 产出 action list
2. action executor 读取 action，并调用现有 `do_spawn_swarm`、`do_bump_agent`、`do_broadcast` 等函数
3. 执行结果回写到 `Runtime State` 与 `Working Memory`

这意味着 `backend/agents/coordinator_core.py` 在第一阶段继续保留，但职责被收敛为“执行动作”，而不是“兼做控制决策”。

---

## 7. 现有模块的演进方式

### 7.1 `backend/agents/coordinator_loop.py`

当前职责：

- 拉事件
- 拼字符串消息
- 直接发给 `turn_fn`
- 驱动 coordinator

目标职责：

- 收集平台事件与运行时变化
- 更新 `Runtime State`
- 调用 `Policy Engine` 生成动作
- 执行动作
- 在必要时调用 coordinator provider 进行 LLM 推理

也就是说，这个模块从“事件泵”升级为“控制外壳”。

### 7.2 `backend/agents/*_coordinator.py`

当前职责：

- 持有 provider-specific agent
- 接收事件消息
- 调工具完成整场调度

目标职责：

- 充当 LLM 推理适配器
- 接受结构化策略上下文
- 返回策略建议或战术建议

这样做的目的，是把 provider 差异限制在推理接口上，而不是让每种 provider 都复制一套业务控制逻辑。

### 7.3 `backend/agents/solver.py`

当前职责：

- 启 sandbox
- 构建单 solver agent
- 驱动 ReAct 工具执行
- 记录 tracing 和 loop detection

目标职责：

- 继续作为单 solver 执行器
- 通过 `WorkingMemory` 摘要接收更高质量的 bump
- 将关键结果与失败模式回流给控制面

第一阶段不要求重写 solver 的主循环，只要求把“记忆提炼”从 solver 内部副作用提升为正式接口。

### 7.4 `backend/deps.py`

当前问题：

- `CoordinatorDeps` 持有越来越多共享运行态字段

目标：

- `deps` 继续承担运行注入职责
- 但状态主数据逐步转移到 `Runtime State` 对象中

设计原则：

1. `deps` 是依赖注入容器
2. `state` 是运行事实模型
3. 不再继续把所有状态都塞进 `deps` 顶层字段

---

## 8. 第一阶段最小落地方案

第一阶段只引入最小必要对象，不做大拆。

### 8.1 新增模块

建议新增：

- `backend/control/state.py`
- `backend/control/actions.py`
- `backend/control/policy_engine.py`
- `backend/control/working_memory.py`
- `backend/control/knowledge_store.py`

### 8.2 保持不动或尽量少动的模块

- `backend/poller.py`
- `backend/agents/swarm.py`
- `backend/agents/solver.py`
- `backend/agents/coordinator_core.py`
- `backend/agents/azure_coordinator.py`

### 8.3 第一阶段的最小能力

必须落地的能力只有三类：

1. 用结构化状态替代 coordinator loop 中的部分隐式判断
2. 为每道题建立可更新的 `Working Memory`
3. 建立带晋升规则的 `Knowledge Store`

第一阶段不要求：

- 复杂检索系统
- 向量搜索
- 多层递归计划树
- 永久知识库

---

## 9. 数据流设计

第一阶段建议把一次控制 tick 固定为以下流程：

1. `Poller` 提供平台事件
2. 更新 `CompetitionState / ChallengeState / SwarmState`
3. 从 solver trace 与结果中提炼 `Working Memory`
4. 用 `Knowledge Store` 匹配可复用知识
5. 调用 `Policy Engine` 产出 action list
6. 调用 `Action Executor` 执行动作
7. 将动作结果写回 state / memory / knowledge

这样做的直接收益：

1. 每个动作都有明确前因与后果
2. 任何错误都能定位到是状态问题、策略问题还是执行问题
3. 未来切换 coordinator provider，不需要重写赛事控制逻辑

---

## 10. 错误处理设计

### 10.1 策略层错误

如果 `Policy Engine` 输出非法动作：

- 拒绝执行该动作
- 记录为策略异常
- 回落到安全默认行为，例如不执行或进入 `HoldChallenge`

### 10.2 记忆提炼错误

如果某次 memory 提炼失败：

- 不影响 solver 主执行链
- 保留原 trace
- 在下一 tick 重试提炼

### 10.3 知识晋升错误

如果某条 findings 无法安全晋升为知识：

- 只保留在 `Working Memory`
- 不广播为全局知识

### 10.4 provider 推理异常

如果 coordinator provider 出现模型异常：

- 保留当前状态对象
- 不丢失 memory / knowledge
- 下一 tick 允许重试或回退到规则驱动最小策略

---

## 11. 测试设计

### 11.1 单元测试

新增测试建议覆盖：

- `state` 对象的构建与更新
- `policy_engine` 在不同状态下的动作输出
- `working_memory` 的提炼规则与去重规则
- `knowledge_store` 的晋升、检索与置信度更新
- action executor 对非法动作、重复动作的处理

### 11.2 集成测试

扩展当前 coordinator 平台流测试，覆盖：

1. 新题出现后是否生成正确 `SpawnSwarm`
2. solver 卡住后是否进入 `BumpSolver`
3. 同题重复失败是否被 `Working Memory` 去重
4. 某题发现的高价值经验是否能进入 `Knowledge Store`
5. knowledge 命中后是否影响后续题目的 action 选择

### 11.3 回归要求

以下路径必须保持可用：

- `--coordinator none`
- `--coordinator azure`
- 当前单题模式
- 当前 writeup 与收尾流程

---

## 12. 分阶段实施建议

### 阶段 1：建立显式状态与动作

目标：

- 引入 `state.py` 与 `actions.py`
- 让 coordinator loop 开始使用结构化状态

完成标志：

- 关键控制决策不再只靠字符串消息拼接

### 阶段 2：接入 Working Memory

目标：

- 建立单题记忆对象
- 让 bump 与 findings 注入使用结构化记忆摘要

完成标志：

- 同题重复试错显著减少

### 阶段 3：接入 Knowledge Store 与 Policy Engine

目标：

- 建立全局知识沉淀与复用
- 将跨题复用纳入策略层

完成标志：

- 某题沉淀的通用经验可以影响后续题目的求解动作

### 阶段 4：收缩 provider-specific coordinator 责任

目标：

- 让不同 coordinator provider 只负责推理适配
- 业务控制逻辑统一进入控制内核

完成标志：

- `azure`、`codex`、`claude` 三类 coordinator 共享同一套控制面

---

## 13. 对后续稳定性重构的价值

这份设计不是只服务“解题率优先”的第一阶段，它也为第二阶段稳定性重构提供了明确边界：

1. 有了显式 `State`，后续才能更自然地引入状态机与故障恢复。
2. 有了 `Policy Engine`，后续才能把规则、容错、冷却与预算管理系统化。
3. 有了 `Working Memory` 和 `Knowledge Store`，后续日志、trace、RAG、长期记忆才有统一归属。
4. 有了 `Action` 抽象，后续 provider 兼容层、可观测性和回放系统才有真正载体。

换句话说，这一阶段的控制架构升级不是额外成本，而是后面所有稳定性建设的前置条件。

---

## 14. 最终结论

HuntingBlade 当前并不缺“多层结构”，缺的是把已有多层结构从隐式关系提升为显式控制架构。

本设计建议采用“在现有骨架内新增显式控制层”的路线，通过以下三项同时推进第一阶段目标：

1. `Policy Engine`：让调度策略显式化
2. `Working Memory`：让单题经验结构化
3. `Knowledge Store`：让跨题知识可筛选、可复用

这样既能优先提升解题率，又不会把系统推入过早的递归式复杂架构；同时也为下一阶段的稳定性重构和最终对外架构叙事打下真实、可验证的基础。
