from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from urllib.parse import quote_plus

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_env_file = str(Path(__file__).resolve().parents[2] / ".env")


class CelerySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    # Celery 消息代理地址 (如 redis://localhost:6379/0)
    broker_url: str = Field("", alias="CELERY_BROKER_URL")
    # Celery 结果存储地址
    result_backend: str = Field("", alias="CELERY_RESULT_BACKEND")
    
    # Redis 配置 (用于自动构建 broker_url/result_backend)
    redis_host: str = Field("localhost", alias="REDIS_HOST")
    redis_port: int = Field(6379, alias="REDIS_PORT")
    redis_password: str = Field("", alias="REDIS_PASSWORD")
    redis_db: int = Field(0, alias="REDIS_DB")

    def model_post_init(self, __context) -> None:
        if not self.broker_url and self.redis_host:
            auth = ""
            if self.redis_password:
                pwd = quote_plus(self.redis_password)
                auth = f":{pwd}@"
            self.broker_url = f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"
            
        if not self.result_backend and self.broker_url:
            self.result_backend = self.broker_url

    # 备份任务失败时的最大重试次数
    backup_max_retries: int = Field(1, alias="CELERY_BACKUP_MAX_RETRIES")
    # 备份任务重试的指数退避基数(秒)
    backup_retry_backoff_seconds: int = Field(10, alias="CELERY_BACKUP_RETRY_BACKOFF_SECONDS")
    # 任务软超时时间(秒)，超时会抛出 SoftTimeLimitExceeded 异常，任务可以在清理后退出
    task_soft_time_limit_seconds: int = Field(0, alias="CELERY_TASK_SOFT_TIME_LIMIT_SECONDS")
    # 任务硬超时时间(秒)，超时会强制终止任务
    task_time_limit_seconds: int = Field(300, alias="CELERY_TASK_TIME_LIMIT_SECONDS")
    # 批量定时任务完成状态检查的轮询间隔(秒)
    schedule_finalize_poll_seconds: int = Field(5, alias="CELERY_SCHEDULE_FINALIZE_POLL_SECONDS")
    # 批量定时任务完成状态检查的最大轮询次数 (默认 720 次 * 5秒 = 1小时)
    schedule_finalize_max_polls: int = Field(720, alias="CELERY_SCHEDULE_FINALIZE_MAX_POLLS")
    # When false, Redis semaphore failures block backup execution and trigger task retry.
    redis_semaphore_fail_open: bool = Field(False, alias="CELERY_REDIS_SEMAPHORE_FAIL_OPEN")


class CsrfSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    secret_key: str = Field("dev-secret-key-change-me", alias="SECRET_KEY")
    cookie_samesite: str = Field("lax", alias="CSRF_COOKIE_SAMESITE")
    cookie_secure: bool = Field(False, alias="CSRF_COOKIE_SECURE")
    cookie_key: str = Field("fastapi-csrf-token", alias="CSRF_COOKIE_KEY")
    cookie_path: str = Field("/", alias="CSRF_COOKIE_PATH")
    token_location: str = Field("body", alias="CSRF_TOKEN_LOCATION")
    token_key: str = Field("csrf_token", alias="CSRF_TOKEN_KEY")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8", 
        case_sensitive=True, 
        extra="ignore"
    )

    # 应用名称/品牌名，固定在代码中，不通过 .env 覆盖。
    app_name: ClassVar[str] = "EasyNetBak"
    # 应用版本
    app_version: str = Field("v1.5.1", alias="APP_VERSION")
    # 数据库连接字符串
    database_url: str = Field("", alias="DATABASE_URL")
    db_scheme: str = Field("postgresql", alias="DB_SCHEME")
    db_user: str = Field("", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")
    db_host: str = Field("", alias="DB_HOST")
    db_port: str = Field("5432", alias="DB_PORT")
    db_name: str = Field("", alias="DB_NAME")
    # 应用密钥，用于加密会话和敏感数据
    secret_key: str = Field("dev-secret-key-change-me", alias="SECRET_KEY")
    # 默认时区偏移量
    timezone_offset: str = Field("+08:00", alias="TIMEZONE_OFFSET")
    default_locale: str = Field("zh-CN", alias="DEFAULT_LOCALE")
    supported_locales: str = Field("zh-CN,en-US", alias="SUPPORTED_LOCALES")
    i18n_strict: bool = Field(False, alias="I18N_STRICT")
    
    # 认证 Cookie 名称
    auth_cookie_name: str = Field("nb_session", alias="AUTH_COOKIE_NAME")
    auth_cookie_secure: bool = Field(False, alias="AUTH_COOKIE_SECURE")
    auth_cookie_samesite: str = Field("lax", alias="AUTH_COOKIE_SAMESITE")
    auth_cookie_persistent: bool = Field(False, alias="AUTH_COOKIE_PERSISTENT")
    # 会话有效期(秒)，默认2小时
    session_ttl_seconds: int = Field(7200, alias="SESSION_TTL_SECONDS")
    # 初始管理员用户名 (仅在初始化时使用)
    bootstrap_admin_username: str = Field("admin", alias="BOOTSTRAP_ADMIN_USERNAME")
    # 初始管理员密码 (仅在初始化时使用)
    bootstrap_admin_password: str = Field("admin", alias="BOOTSTRAP_ADMIN_PASSWORD")
    
    # 是否启用内置调度器 (多实例部署时建议仅在一个实例开启)
    enable_scheduler: bool = Field(True, alias="ENABLE_SCHEDULER")

    # Nested configurations
    # Celery 异步任务配置
    celery: CelerySettings = Field(default_factory=CelerySettings)
    # CSRF 安全防护配置
    csrf: CsrfSettings = Field(default_factory=CsrfSettings)

    def model_post_init(self, __context) -> None:
        if self.database_url:
            return
        if self.db_host and self.db_name:
            auth = ""
            if self.db_user:
                pwd = quote_plus(self.db_password or "")
                auth = f"{self.db_user}:{pwd}@"
            port = f":{self.db_port}" if self.db_port else ""
            self.database_url = f"{self.db_scheme}://{auth}{self.db_host}{port}/{self.db_name}"
            return
        self.database_url = "sqlite:///./dev.db"


settings = Settings()
