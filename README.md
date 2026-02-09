# Network Backup System (网络设备备份系统)

这是一个基于 Python 和 FastAPI 构建的现代化网络设备配置备份与管理平台。它为网络管理员提供了一套强大的工具，用于自动化备份网络设备配置、监控设备状态、管理设备资产以及审计系统操作。

## ✨ 主要功能

- **设备资产管理**: 
  - 支持按平台、分组和标签组织网络设备。
  - 支持 Excel/CSV 批量导入导出。
  - **连通性检测**: 基于 Celery 的异步批量连通性检测，支持大规模设备快速巡检。

- **自动化备份**: 
  - 基于 Netmiko 的多厂商支持（Cisco, Huawei, H3C, Juniper 等）。
  - 支持手动触发和 Crontab 表达式定时任务调度。
  - 异步任务执行，支持自动重试机制。

- **配置管理与对比**: 
  - **版本对比**: 内置 Diff 查看器，直观展示不同版本备份之间的配置差异。
  - **变更时间轴**: 提供设备配置变更的时间轴视图，快速定位变更点。
  - **变更统计**: 热力图展示配置变更频率。

- **可视化仪表盘**: 
  - 实时展示系统状态、备份成功率、最近失败任务。
  - 配置变更统计图表及设备连通性概览。

- **安全与权限 (RBAC)**: 
  - **精细化权限**: 
    - **管理员 (Admin)**: 拥有系统所有权限，包括用户管理和系统设置。
    - **操作员 (Operator)**: 可管理设备和备份任务，查看日志，但无法修改系统设置。
    - **只读用户 (Read-only)**: 仅具有查看权限，无法进行任何变更操作。
  - **凭据加密**: 敏感凭据（密码/Secret）在数据库中加密存储。

- **审计与日志**: 
  - **操作审计**: 详尽记录用户对系统的所有增删改操作。
  - **登录日志**: 记录用户登录成功、失败及登出行为，支持异常登录分析。

- **存储与集成**: 
  - **S3 归档**: 支持将备份文件自动同步至 AWS S3 或兼容的对象存储（MinIO, Aliyun OSS 等）。
  - **消息通知**: 支持备份失败或系统异常时的即时通知。

## 🛠 技术栈

- **后端**: Python 3.10+, FastAPI, SQLModel (SQLAlchemy), Celery (异步任务), Redis (消息队列/缓存).
- **前端**: Bootstrap 5, Jinja2 模板引擎, ECharts (数据可视化).
- **数据库**: PostgreSQL (生产环境推荐), SQLite (开发环境默认).
- **容器化**: Docker, Docker Compose.

## 🚀 快速开始

### 前置要求

- Docker & Docker Compose

### 部署步骤 (Docker)

1.  **克隆仓库**:
    ```bash
    git clone <repository-url>
    cd network-backup
    ```

2.  **配置环境**:
    复制示例环境变量文件：
    ```bash
    cp .env.prod.example .env.prod
    ```
    *建议修改 `.env.prod` 中的密钥、数据库密码及 S3 配置等敏感信息。*

3.  **启动服务**:
    ```bash
    docker-compose up -d
    ```

4.  **访问系统**:
    打开浏览器访问 `http://localhost:8000`。

    **默认管理员账号**:
    - 用户名: `admin`
    - 密码: `admin`
    *(请首次登录后立即修改密码)*

### 开发环境搭建 (手动)

1.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **配置环境变量**:
    参考 `app/core/settings.py` 配置必要的环境变量，或直接使用默认值（开发模式使用 SQLite）。

3.  **初始化数据库**:
    ```bash
    alembic upgrade head
    ```

4.  **启动 Worker (处理备份任务)**:
    ```bash
    celery -A app.celery_app.celery_app worker --loglevel=info
    ```

5.  **启动 Web 服务**:
    ```bash
    uvicorn app.main:app --reload
    ```

## ⚙️ 关键配置

主要通过环境变量进行配置 (可在 `.env.prod` 或 `docker-compose.yml` 中设置):

- **基础配置**:
  - `APP_NAME`: 系统显示的名称.
  - `SECRET_KEY`: 用于加密 Session 和敏感数据的密钥 (务必修改).
  - `TIMEZONE_OFFSET`: 时区偏移量 (默认为 "+08:00").

- **数据库与中间件**:
  - `DATABASE_URL`: 数据库连接字符串.
  - `CELERY_BROKER_URL`: Redis 连接地址.

- **功能配置**:
  - `S3_ENABLED`: 是否启用 S3 归档 (1/0).
  - `S3_ENDPOINT` / `S3_BUCKET` ...: S3 相关配置.

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进本项目。


