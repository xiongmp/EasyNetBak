# 定时任务运行终止方案

## 背景

当前“定时任务统计”页面存在实时轮询逻辑：只要某个定时任务运行记录仍处于活跃状态，前端就会持续刷新运行列表。  
从现有实现看，页面持续刷新通常不是前端异常死循环，而是后端对应的 `BackupScheduleRun` 一直没有进入终态，导致前端持续认为该任务“仍在处理中”。

结合现有代码，问题大致表现为两类：

- 某些子备份任务长期卡在 `planned` / `queued` / `running`
- 收尾任务 `finalize_schedule_run` 因发现仍有未完成子任务而持续重试

用户感知上，这会表现为：

- 后台页面不断刷新
- 历史记录长期显示“运行中”或“正在处理”
- 运维人员缺少一个可以人工止损的入口

因此，需要补充“终止某次定时任务运行”的能力。

## 目标

- 为单次定时任务运行提供人工终止入口
- 仅终止“尚未开始的子任务”，不强制中断“正在执行中的子任务”
- 终止后保证 `BackupScheduleRun` 与关联 `BackupRecord` 能进入可解释的状态
- 终止后避免新的未运行任务继续被执行
- 保留审计记录，便于后续排障和追踪

## 结论

该功能可以实现，且基于当前架构实现成本可控。

现有代码已经具备关键前提：

- 每个备份子任务的 Celery `task_id` 直接使用 `record_id`
- 每次定时任务运行的收尾任务 `task_id` 固定为 `finalize-{run_id}`
- 每次定时任务运行与其子备份记录之间已有 `BackupScheduleRunItem` 关联表

这意味着可以基于 `run_id` 精准定位并撤销一整次运行所涉及的 Celery 任务，而不需要全局停 worker，也不需要人工登录服务器杀进程。

## 方案范围

本方案只解决“终止某次运行实例”，不直接终止“定时任务定义”。

建议区分两个概念：

- 定时任务定义：`BackupSchedule`
- 定时任务单次运行：`BackupScheduleRun`

用户页面上的“终止”按钮应作用于某一条运行记录，即某一个 `run_id`，而不是关闭整个定时任务计划。

## 现状分析

### 1. 前端刷新机制

当前页面在检测到存在活跃运行记录时，会周期性请求运行记录接口并刷新表格。  
只要后端返回 `has_active_runs = true`，轮询就不会停止。

这一点本身不是 bug，但会放大后端任务长期不收敛的问题。

### 2. 后端运行状态机制

当前 `BackupScheduleRun` 的活跃状态包括：

- `planned`
- `dispatching`
- `running`
- `finalizing`

终态包括：

- `succeeded`
- `partial_failed`
- `failed`

目前缺少“已取消”或“终止中”这类人工干预状态，导致：

- 无法在状态上区分“自然失败”和“人工终止”
- UI 无法给出准确语义
- 运维排障时不容易还原现场

### 3. Celery 任务可定位性

现有实现有两个很重要的优势：

- 子备份任务 `task_id = record_id`
- 收尾任务 `task_id = finalize-{run_id}`

因此，终止某次运行时，可以直接按以下方式撤销：

- 撤销 `finalize-{run_id}`
- 查询该 `run_id` 下所有 `BackupScheduleRunItem`
- 取出关联的 `backup_id`
- 将每个 `backup_id` 作为 Celery `task_id` 执行撤销

这为实现“终止本次运行”提供了非常清晰的技术路径。

## 总体设计

### 设计原则

- 优先终止“单次运行”，不影响后续 cron 调度
- 先补齐后端能力，再补前端按钮
- 先确保状态可收敛，再追求交互细节
- 仅撤销未运行任务，不强制终止已进入 `running` 的任务
- 所有人工终止动作必须有审计日志

### 推荐形态

建议采用“三层实现”：

- 状态层：新增“取消相关状态”
- 服务层：新增“终止单次运行”的编排服务
- 界面层：在运行中记录旁增加“终止”按钮

## 详细方案

### 一、状态模型调整

建议为 `BackupScheduleRun` 和 `BackupRecord` 增加人工终止相关状态。

推荐新增状态如下：

- `BackupScheduleRun`
  - `cancelling`：正在终止
  - `cancelled`：已终止
  - `partial_cancelled`：仅终止了未运行任务，已有运行中任务继续自然收尾
- `BackupRecord`
  - `cancelled`：已终止

建议语义：

- `cancelling`：用户已点击终止，系统正在撤销未运行任务并回写状态
- `cancelled`：本次运行下的子任务均未实际执行或均已被人工撤销
- `partial_cancelled`：本次运行中，未运行任务已被撤销，但已进入 `running` 的任务继续执行并自然完成

为什么建议增加而不是复用 `failed`：

- `failed` 表示任务自然执行失败
- `cancelled` / `partial_cancelled` 表示人为主动干预
- 这些状态对运维分析、告警统计、用户理解都不同

### 二、后端接口设计

建议新增内部 API：

- `POST /api/schedules/runs/{run_id}/terminate`

请求语义：

- 对指定 `run_id` 发起终止操作

鉴权建议：

- 需要 `schedules.update`
- 如当前页面执行手动运行还要求 `devices.backup`，也可以保持一致

返回建议：

- `success`
- `run_id`
- `status`
- `terminated_records`
- `skipped_records`
- `message`

返回示例：

```json
{
  "success": true,
  "run_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "partial_cancelled",
  "terminated_records": 18,
  "skipped_records": 2,
  "message": "未运行任务已终止，运行中的任务将继续完成"
}
```

### 三、服务层编排逻辑

建议新增统一服务方法，例如：

- `schedule_service.terminate_schedule_run(...)`
  或
- `task_orchestration_service.terminate_schedule_run(...)`

推荐处理流程：

1. 根据 `run_id` 读取 `BackupScheduleRun`
2. 如果运行不存在，返回 404
3. 如果运行已经是终态，直接返回“无需终止”
4. 将 `BackupScheduleRun.status` 先置为 `cancelling`
5. 查询该运行下全部 `BackupScheduleRunItem`
6. 收集全部 `backup_id`
7. 查询每个 `BackupRecord` 当前状态，仅筛出未运行任务
8. 撤销收尾任务 `finalize-{run_id}`
9. 仅对未运行任务逐个执行 Celery revoke
10. 将这些未运行的 `BackupRecord` 收敛为 `cancelled`
11. 统计成功数、失败数、取消数、运行中数量
12. 若仍存在 `running` 任务，则将 `BackupScheduleRun` 置为可识别的“部分终止进行中”语义，并在这些任务自然完成后由 finalizer 汇总为 `partial_cancelled`
13. 写审计日志并返回结果

## 任务终止策略

### 1. 对未开始任务

对于尚处于 `planned` / `queued` 的记录：

- 直接执行 Celery revoke
- 数据库状态直接回写为 `cancelled`

这一类风险最低，也是最容易终止干净的部分。

### 2. 对执行中任务

对于已进入 `running` 的记录：

- 不做强制终止
- 不执行 `terminate=True`
- 保持任务继续自然执行

这样做的原因是：

- 避免强制打断 SSH / 网络采集
- 避免产生半截数据或未释放连接
- 将“止损”范围控制在尚未开始的任务上

因此，该方案的真实语义是：

- “终止后续未运行任务”
- “已运行中的任务继续完成”

### 3. 对收尾任务

建议在发起“终止未运行任务”时同步处理 `finalize-{run_id}`，否则会出现两类问题：

- 收尾任务继续轮询，页面状态仍可能被拉回“处理中”
- 已经人工终止的运行被旧的 finalizer 再次汇总覆盖

建议处理方式如下：

- 先撤销当前已入队的 `finalize-{run_id}`
- 再按新的状态结果判断是否需要重新补发一个 finalizer

原因是本方案不会强停 `running` 任务，因此若当前仍有运行中记录，就仍然需要一个 finalizer 在这些任务自然结束后完成最终汇总。

## 数据库状态收敛建议

这是本方案最关键的部分。  
只有 Celery revoke 不够，还必须让数据库状态进入终态，否则前端仍会持续轮询。

建议按以下规则收敛：

- 已成功的 `BackupRecord` 保持 `succeeded`
- 已失败的 `BackupRecord` 保持 `failed`
- 处于 `planned` / `queued` 的 `BackupRecord` 改为 `cancelled`
- 处于 `running` 的 `BackupRecord` 保持不变，等待自然完成
- 如果本次运行下所有未完成任务都被撤销，`BackupScheduleRun` 可直接改为 `cancelled`
- 如果仍存在 `running` 任务，`BackupScheduleRun` 建议使用 `partial_cancelled`

同时建议在 `error_message` 或扩展字段中记录人工终止信息，例如：

- `manual_termination`
- `terminated_by=<user>`
- `terminated_reason=manual_stop`

如果当前不准备扩表，至少可以在现有错误描述中保留可读文本。  
如果允许新增枚举，推荐将 `partial_cancelled` 作为正式状态纳入展示与统计体系。

## 前端交互方案

### 1. 按钮位置

建议在“历史运行记录”中仅对活跃状态的行显示按钮：

- `planned`
- `dispatching`
- `running`
- `finalizing`

按钮文案建议：

- `终止未运行任务`

按钮样式建议：

- 使用危险色或警示色
- 点击前弹出确认框

### 2. 二次确认

建议确认文案：

- “确认终止本次运行中尚未开始的任务吗？已在执行中的任务将继续完成。”

### 3. 终止后的前端行为

调用成功后建议执行：

- 立即刷新当前运行记录
- 若后端已返回终态，则停止轮询
- 给出 toast 提示

提示文案建议：

- 成功：`未运行任务已终止，运行中的任务将继续完成`
- 失败：`终止失败，请稍后重试`

## 审计与可观测性

建议补齐以下记录：

- 审计日志
  - 谁在什么时间终止了哪个 `run_id`
  - 对应的 `schedule_id`
  - 共撤销多少个子任务
- 任务事件日志
  - `schedule_run_termination_requested`
  - `schedule_run_termination_completed`
  - `schedule_run_termination_failed`

这样可以支持后续分析：

- 是谁终止了任务
- 终止发生时任务执行到哪一步
- 是否存在 revoke 失败或状态回写失败

## 风险分析

### 1. 无法立即结束整次运行

由于方案不强制中断 `running` 任务，因此点击终止后：

- 新的未运行任务会停止
- 已运行中的任务仍会继续执行
- 当前这次运行不一定立刻进入终态

因此该方案解决的是“停止继续扩大执行范围”，而不是“瞬时中断整次运行”。

### 2. 状态竞争

如果用户点击终止时，某些子任务恰好刚完成，可能出现状态竞争：

- revoke 发出时任务已结束
- finalizer 已在另一个 worker 上进入收尾逻辑

所以终止逻辑必须具备幂等性：

- 再次终止同一个 `run_id` 不应报错
- 对已终态记录直接跳过
- 对 finalizer 撤销失败应允许继续做数据库状态收敛

### 3. 告警和统计口径变化

新增 `cancelled` / `partial_cancelled` 后，需要考虑：

- 统计页是否把 `cancelled` 算失败
- 统计页是否把 `partial_cancelled` 单独展示
- 告警是否对人工终止继续发通知
- 趋势图是否纳入成功率统计

推荐原则：

- `cancelled` 不算成功
- `cancelled` 不计入成功率分子
- `partial_cancelled` 单独展示，不与自然失败混淆
- `partial_cancelled` 默认不视为“系统故障”，但要保留可见标识
- 是否触发告警可单独配置，默认不发或降级为信息提示

## 推荐实施步骤

建议分三步落地。

### 第一阶段：后端止血能力

目标：

- 可以通过接口终止某次运行
- 可以只撤销未运行任务
- 终止后数据库进入可解释状态

工作项：

- 新增状态枚举
- 新增 terminate API
- 新增服务层终止逻辑
- 撤销 finalizer 和未运行子任务
- 回写 `BackupRecord` 与 `BackupScheduleRun` 状态

验收标准：

- 同一个 `run_id` 可被成功终止
- 未运行任务不会继续被执行
- 若仍有 `running` 任务，状态能正确显示为部分终止或继续收尾
- 运行全部结束后页面轮询正常停止

### 第二阶段：前端交互

目标：

- 用户在页面上可以自主终止任务

工作项：

- 在运行记录表增加“终止”按钮
- 增加二次确认
- 成功后刷新列表并提示结果

验收标准：

- 活跃任务可见终止按钮
- 点击后任务状态在页面中正确切换
- 非活跃任务不显示终止按钮

### 第三阶段：体验和治理增强

目标：

- 提升安全性和可维护性

工作项：

- 补充审计日志和任务事件
- 区分 `cancelled`、`partial_cancelled` 与 `failed` 的展示
- 明确 `partial_cancelled` 的统计与告警口径

验收标准：

- 终止动作可审计
- 状态语义清晰
- 运维排障可追溯

## 是否建议现在就做

建议做，优先级可定为中高。

原因如下：

- 当前问题已经影响用户使用体验
- 现有架构已经具备可实现的任务定位能力
- 该能力属于运维止损手段，价值明确
- 实现范围集中，主要影响调度运行链路，不需要大面积改造业务模块

## 最终建议

推荐采用以下最终策略：

- 终止对象：某一次 `BackupScheduleRun`
- 状态补充：增加 `cancelling` / `cancelled` / `partial_cancelled`
- 后端动作：撤销 finalizer + 仅撤销未运行子任务 + 回写数据库状态
- 前端动作：仅对活跃运行显示“终止”按钮
- 审计要求：必须记录操作者、运行 ID、撤销结果

如果后续需要正式开发，建议先从“后端接口 + 状态收敛”开始，再接前端按钮。  
这样即使前端按钮尚未上线，管理员也可以先通过接口或内部工具完成止损。
