# Network Backup System (网络设备备份系统)

这是一个基于 Python 和 FastAPI 构建的现代化网络设备配置备份与管理平台。它为网络管理员提供了一套强大的工具，用于自动化备份网络设备配置、监控设备状态、管理设备资产以及审计系统操作。

## ✨ 主要功能

- **设备管理**: 支持按平台、分组和标签组织网络设备，支持批量导入导出。
- **自动备份**: 基于 Netmiko 的自动化备份，支持手动触发和定时任务调度。
- **配置对比**: 内置配置差异查看器，直观展示不同版本备份之间的变更内容。
- **仪表盘**: 实时展示系统状态、备份成功率、配置变更统计及设备连通性概览。
- **权限管理 (RBAC)**: 精细的角色访问控制，支持系统管理员、操作员和只读用户，以及基于设备组的权限隔离。
- **审计日志**: 详尽记录用户登录、操作行为及系统事件，保障安全合规。
- **S3 存储集成**: 支持将备份归档至 AWS S3 或兼容的对象存储服务，确保数据安全。
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
    *建议修改 `.env.prod` 中的密钥和数据库密码等敏感信息。*

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

- `APP_NAME`: 系统显示的名称 (默认为 "网络设备备份").
- `DATABASE_URL`: 数据库连接字符串.
- `SECRET_KEY`: 用于加密 Session 和敏感数据的密钥.
- `CELERY_BROKER_URL`: Redis 连接地址.
- `BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD`: 初始化系统时创建的默认管理员账号.
- `TIMEZONE_OFFSET`: 时区偏移量 (默认为 "+08:00").

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进本项目。

## 📄 许可证

[License Name] - 查看 LICENSE 文件了解更多详情。
