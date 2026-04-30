# 任务编排收敛改造计划

## 背景

当前任务编排逻辑分散在以下几个层次中：

- `app/scheduler.py`：计划定时任务运行、创建 `BackupScheduleRun` 与 `BackupRecord`
- `app/services/task_queue_service.py`：封装 Celery 分发参数
- `app/celery_tasks.py`：处理入队失败、批量入队、定时任务收尾轮询
- `app/services/task_execution_service.py`：执行单个备份并推进 `BackupRecord` 状态

这种拆分方式的主要问题：

- 状态推进点分散，排查 `run` / `record` 最终状态时需要跨多个文件跳转
- 入队失败、部分入队成功、最终收尾等边界逻辑容易出现遗漏
- 后续增加幂等、补偿、失败恢复时，容易出现重复逻辑和责任不清
- 调度入口、手动入口、批量入口之间复用不足

## 改造目标

- [x] 新增统一的“任务编排服务”作为状态推进唯一入口
- [x] 将“计划运行 + 创建记录 + 提交事务 + 入队 + 入队失败回写”收敛到统一服务
- [x] 将“定时任务最终汇总/轮询完成”收敛到统一服务
- [x] 让 `scheduler.py` 只负责调度触发，不再持有编排细节
- [x] 让 `celery_tasks.py` 只负责任务执行与任务入口，不再持有复杂编排状态逻辑
- [x] 更新手动备份、批量备份、手动触发计划任务三类入口
- [x] 补充针对新编排层的测试，覆盖关键边界

## 设计原则

- 单条备份记录状态推进，仍由执行层负责
- 批量/计划任务的“计划、入队、收尾”由统一编排层负责
- 所有跨进程可见性边界统一在编排层显式 `commit`
- 所有入队失败回写统一在编排层处理，避免调用方重复兜底
- 调度器与路由层只发起编排，不再直接处理编排异常分支

## 计划步骤

### 1. 新增统一编排服务

- [x] 新建 `app/services/task_orchestration_service.py`
- [x] 统一封装单设备备份计划
- [x] 统一封装批量备份计划
- [x] 统一封装定时任务运行计划
- [x] 统一封装入队失败回写
- [x] 统一封装定时任务最终收尾

### 2. 调整调用关系

- [x] `app/services/backup_service.py` 改为调用统一编排服务
- [x] `app/routers/internal_api/schedules.py` 改为调用统一编排服务
- [x] `app/scheduler.py` 改为调用统一编排服务
- [x] `app/celery_tasks.py` 中的定时任务收尾逻辑改为调用统一编排服务

### 3. 测试与验证

- [x] 新增统一编排服务测试
- [x] 覆盖“先下发 finalizer 再下发备份任务”的关键边界
- [x] 覆盖“finalizer 轮询未完成 -> 重试”分支
- [x] 覆盖“全部完成后汇总 schedule run”分支

## 本次已完成

- [x] 新增统一编排服务 `app/services/task_orchestration_service.py`
- [x] 将单设备备份计划与入队收敛到统一编排服务
- [x] 将批量备份计划与入队收敛到统一编排服务
- [x] 将定时任务运行计划与最终汇总收敛到统一编排服务
- [x] 调整 `app/services/backup_service.py` 接入新编排层
- [x] 调整 `app/scheduler.py` 接入新编排层
- [x] 调整 `app/routers/internal_api/schedules.py` 接入新编排层
- [x] 调整 `app/celery_tasks.py` 接入新编排层
- [x] 新增编排层关键测试用例

## 第二阶段目标

- [x] 确认 `app/celery_tasks.py` 中兼容包装入口已无真实调用方
- [x] 删除 `enqueue_backup_record()` 兼容包装
- [x] 删除 `enqueue_schedule_run()` 兼容包装
- [x] 清理第二阶段产生的无用 import / 死代码
- [x] 完成第二阶段相关验证

## 第二阶段已完成

- [x] 移除 `app/celery_tasks.py` 中已无调用方的兼容包装入口
- [x] 保留 `Celery Task` 执行职责，继续由统一编排层负责入队与收尾状态推进
- [x] 使用项目 `.venv` 重新执行相关测试并通过

## 第三阶段目标

- [x] 将 `task_queue_service.py` 拆分为“运行时配置”和“任务投递”两个清晰职责
- [x] 新增显式任务状态模型，覆盖 `BackupRecord` 与 `BackupScheduleRun`
- [x] 将编排、执行、监控、统计、列表查询切换为基于显式状态的判断
- [x] 为显式状态字段补充数据库迁移脚本
- [x] 更新相关测试用例，适配新状态模型与新分层结构
- [x] 完成第三阶段相关验证

## 第三阶段已完成

- [x] 新增 `app/services/task_runtime_config_service.py`，承接运行时配置读取与降级状态判断
- [x] 新增 `app/services/task_dispatcher_service.py`，承接 Celery 任务投递
- [x] 保留 `app/services/task_queue_service.py` 作为兼容薄包装层，避免外部引用立即断裂
- [x] 新增 `app/services/task_state_service.py`，集中管理备份记录与计划运行状态常量
- [x] 为 `BackupRecord` 增加 `status` 字段
- [x] 为 `BackupScheduleRun` 增加 `status` 字段
- [x] 新增迁移脚本 `migrations/versions/w3x4y5z6a7b8_add_task_status_columns.py`
- [x] 修复迁移脚本的 PostgreSQL 布尔兼容问题并完成 `alembic upgrade head` 验证
- [x] 调整 `task_orchestration_service.py`，统一推进 `planned -> queued -> running/finalizing -> terminal`
- [x] 调整 `task_execution_service.py`，在执行入口显式切换 `BackupRecord` 到 `running`
- [x] 调整 `task_observability_service.py`、`backup_service.py`、`schedule_service.py`、`stats_service.py`、`crud.py`，改为优先基于 `status` 做统计和查询
- [x] 使用项目 `.venv` 重新执行相关测试并通过

## 第四阶段目标

- [x] 删除 `task_queue_service.py` 兼容薄层
- [x] 确认全项目已无 `task_queue_service.py` 引用
- [x] 为备份任务接口补充状态标签与状态色调字段
- [x] 为任务面板、备份历史页、设备详情页、计划统计页显示显式状态枚举
- [x] 完成第四阶段相关验证

## 第四阶段已完成

- [x] 删除 `app/services/task_queue_service.py`
- [x] 新增统一状态展示辅助方法到 `app/services/task_state_service.py`
- [x] 调整 `backup_service.py` 返回 `status_label`、`status_tone`
- [x] 调整 `schedule_service.py` 返回 `run_status_labels`、`run_status_tones`
- [x] 调整 `app/templates/base.html` 任务面板，显示中文状态名 + 原始枚举
- [x] 调整 `app/templates/backups.html`，显示中文状态名 + 原始枚举
- [x] 调整 `app/templates/device_detail.html`，显示中文状态名 + 原始枚举
- [x] 调整 `app/templates/schedule_stats.html`，显示中文状态名 + 原始枚举

## 第五阶段目标

- [x] 将模板中的状态枚举展示抽取到可复用宏
- [x] 将前端页面中的状态渲染逻辑抽取到公共 JS 入口
- [x] 替换现有页面中的重复状态映射代码
- [x] 完成第五阶段相关验证

## 第五阶段已完成

- [x] 在 `app/templates/macros.html` 新增通用状态标签宏
- [x] 新增 `render_backup_record_status(...)` 模板宏
- [x] 新增 `render_schedule_run_status(...)` 模板宏
- [x] 调整 `app/templates/device_detail.html` 改为调用状态宏
- [x] 调整 `app/templates/schedule_stats.html` 改为调用状态宏
- [x] 调整 `app/templates/base.html`，新增通用前端入口 `taskStatusMeta(...)` / `renderTaskStatusBadge(...)`
- [x] 调整 `app/templates/backups.html`、`app/templates/device_detail.html` 的 JS 改为调用统一前端入口
- [x] 修复 `app/templates/schedule_stats.html` 状态信息需手动刷新页面的问题，改为轮询内部 API 实时刷新运行记录

## 当前验证状态

- [x] 已完成静态诊断检查，最近修改文件无编辑器诊断错误
- [x] 已使用项目 `.venv` 完成相关 pytest 验证

已验证命令：

- `.venv\Scripts\python.exe -m pytest tests/test_identity_and_queue_services.py tests/test_settings_and_observability_services.py tests/test_refactor_smoke.py`

验证结果：

- `30 passed`

## 备注

- 本文档用于记录已完成改造项，后续继续改造时请先更新本文件，避免重复工作。
