# -*- coding: utf-8 -*-
"""SG-ERM 全局配置。

使用 pydantic-settings 从环境变量加载配置。
所有路径都基于 data_dir 解析，便于在容器中以 PVC 挂载。
所有硬编码参数集中在此管理，不再分散在业务代码中。

环境变量命名规则: SG_ERM_<大写字段名>
例如: SG_ERM_LISTEN_PORT=18070
"""
import logging
import secrets
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """SG-ERM 应用配置。所有可调参数集中在此。"""

    model_config = SettingsConfigDict(
        env_prefix="SG_ERM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ═══════════════════════════════════════════════════════════════
    # 1. 基础路径
    # ═══════════════════════════════════════════════════════════════
    # 数据根目录（PVC 挂载点）。SQLite 数据库与扩展包仓库都在此目录下。
    data_dir: Path = Path("/data")
    # 应用代码根目录（自动推导，无需环境变量）
    app_dir: Path = Path(__file__).resolve().parent
    # 静态文件子目录（相对 app_dir）
    static_dirname: str = "static"
    # 模板子目录（相对 app_dir）
    template_dirname: str = "templates"
    # 日志子目录（相对 data_dir）
    logs_dirname: str = "logs"

    # ═══════════════════════════════════════════════════════════════
    # 2. 网络
    # ═══════════════════════════════════════════════════════════════
    listen_host: str = "0.0.0.0"
    listen_port: int = 18070

    # ═══════════════════════════════════════════════════════════════
    # 3. 数据库
    # ═══════════════════════════════════════════════════════════════
    # SQLite 文件名（相对 data_dir）
    db_filename: str = "sg-erm.db"
    # SQLite busy_timeout 毫秒数（避免 SQLITE_BUSY）
    db_busy_timeout_ms: int = 5000
    # 是否启用 WAL 日志模式（并发读性能）
    db_enable_wal: bool = True
    # 外键约束
    db_enable_foreign_keys: bool = True
    # 同步模式: FULL / NORMAL / OFF
    db_synchronous: str = "NORMAL"

    # ═══════════════════════════════════════════════════════════════
    # 4. 仓库
    # ═══════════════════════════════════════════════════════════════
    # 扩展包存储子目录（相对 data_dir）
    repo_dirname: str = "repo"
    # 上游官方仓库 URL
    upstream_repo_url: str = "https://extensions.stackgres.io/postgres/repository"
    # index.json 相对路径
    index_path: str = "v2/index.json"

    # ═══════════════════════════════════════════════════════════════
    # 5. 同步引擎
    # ═══════════════════════════════════════════════════════════════
    # 并发下载数
    sync_concurrency: int = 8
    # 单包下载超时（秒）
    sync_download_timeout: int = 120
    # 同步重试次数
    sync_max_retries: int = 3

    # ═══════════════════════════════════════════════════════════════
    # 6. 缓存 / 代理
    # ═══════════════════════════════════════════════════════════════
    # 代理模式：hybrid / strict / proxy_only
    proxy_mode: str = "hybrid"
    # 磁盘使用率阈值（%），超过触发 LRU 淘汰
    cache_max_disk_usage: int = 80
    # 淘汰后回落到的使用率（%）
    cache_target_disk_usage: int = 70
    # TTL（天），超过未访问的包在下次同步时删除
    cache_ttl_days: int = 7
    # 每个扩展保留的版本数
    cache_keep_versions: int = 3

    # ═══════════════════════════════════════════════════════════════
    # 7. 安全 / JWT
    # ═══════════════════════════════════════════════════════════════
    # JWT 签名密钥（必须通过环境变量 SG_ERM_SECRET_KEY 设置）
    secret_key: str = ""
    # JWT 算法
    jwt_algorithm: str = "HS256"
    # Access token 过期时间（分钟）
    access_token_expire_minutes: int = 1440
    # Refresh token 过期时间（天）
    refresh_token_expire_days: int = 7

    # ═══════════════════════════════════════════════════════════════
    # 8. API Token 认证
    # ═══════════════════════════════════════════════════════════════
    # API Token 前缀（明文，用于索引）
    api_token_prefix: str = "sgerm_"
    # Token 前缀索引长度（字符）
    token_prefix_len: int = 8
    # API Token 随机部分长度（字节，token_urlsafe 的参数）
    api_token_random_bytes: int = 32

    # ═══════════════════════════════════════════════════════════════
    # 9. 默认管理员（首次启动创建，生产环境请立即修改密码）
    # ═══════════════════════════════════════════════════════════════
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"
    default_admin_email: str = "admin@sg-erm.local"

    # ═══════════════════════════════════════════════════════════════
    # 10. 加密服务
    # ═══════════════════════════════════════════════════════════════
    # RSA 密钥长度（位）
    rsa_key_size: int = 2048
    # PBKDF2 盐长度（字节）
    kdf_salt_len: int = 16
    # AES-GCM Nonce 长度（字节）
    aes_nonce_len: int = 12
    # PBKDF2 迭代次数
    pbkdf2_iterations: int = 100_000
    # 文件哈希/签名时分块读取大小（字节）
    file_hash_chunk_size: int = 65536

    # ═══════════════════════════════════════════════════════════════
    # 11. 日志
    # ═══════════════════════════════════════════════════════════════
    # 应用主日志单文件大小（字节，默认 10MB）
    app_log_max_bytes: int = 10 * 1024 * 1024
    # 应用主日志保留备份数
    app_log_backup_count: int = 5
    # 任务日志单文件大小（字节，默认 10MB）
    task_log_max_bytes: int = 10 * 1024 * 1024
    # 任务日志保留备份数
    task_log_backup_count: int = 10
    # 控制台日志级别
    console_log_level: str = "INFO"
    # 文件日志级别
    file_log_level: str = "DEBUG"

    # ═══════════════════════════════════════════════════════════════
    # 12. 调度
    # ═══════════════════════════════════════════════════════════════
    # APScheduler 是否启用
    scheduler_enabled: bool = True

    # ═══════════════════════════════════════════════════════════════
    # 13. 健康检查
    # ═══════════════════════════════════════════════════════════════
    # 仓库源健康检查间隔（秒）
    health_check_interval: int = 60
    # 单次健康检查请求超时（秒）
    health_check_timeout: float = 10.0
    # 延迟阈值（秒），超过标记为 degraded
    health_degraded_latency_sec: float = 5.0
    # 首次启动延迟（秒），等待系统初始化
    health_initial_delay_sec: int = 10
    # 连续失败次数阈值，达到后标记为 down
    health_consecutive_failure_threshold: int = 3

    # ═══════════════════════════════════════════════════════════════
    # 14. 默认值（命名、架构、OS）
    # ═══════════════════════════════════════════════════════════════
    # 默认架构
    default_arch: str = "x86_64"
    # 默认操作系统
    default_os: str = "linux"
    # 默认发布者
    default_publisher: str = "com.ongres"

    # ═══════════════════════════════════════════════════════════════
    # 15. IO / 网络传输
    # ═══════════════════════════════════════════════════════════════
    # 异步下载/上传分块大小（字节）
    io_chunk_size: int = 8192
    # 临时文件后缀
    temp_file_suffix: str = ".tmp"
    # 扩展包后缀（发布上传）
    tgz_suffix: str = ".tgz"
    # 仓库包后缀
    tar_suffix: str = ".tar"
    # 签名文件后缀
    sha256_suffix: str = ".sha256"
    # 上传临时目录前缀
    upload_tmp_prefix: str = "sg-erm-upload-"

    # ═══════════════════════════════════════════════════════════════
    # 16. API 默认值
    # ═══════════════════════════════════════════════════════════════
    # 列表默认分页大小
    api_default_limit: int = 20
    # 列表最大分页大小
    api_max_limit: int = 100

    # ═══════════════════════════════════════════════════════════════
    # 17. HTTP 缓存头
    # ═══════════════════════════════════════════════════════════════
    # index.json 缓存时间（秒）
    http_index_cache_max_age: int = 300
    # 扩展包文件缓存时间（秒）
    http_package_cache_max_age: int = 86400

    # ─── 校验器 ──────────────────────────────────────────────────

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """校验 secret_key 强度。"""
        if not v:
            raise ValueError(
                "SG_ERM_SECRET_KEY 必须设置。"
                "请使用 `python -c \"import secrets; print(secrets.token_hex(32))\"` 生成安全密钥"
            )
        if len(v) < 32:
            logger.warning(
                f"SECRET_KEY 长度 {len(v)} 过短（建议至少 32 字符），"
                "使用短密钥可能导致安全风险"
            )
        return v

    @field_validator("proxy_mode")
    @classmethod
    def validate_proxy_mode(cls, v: str) -> str:
        allowed = {"hybrid", "strict", "proxy_only"}
        if v not in allowed:
            raise ValueError(f"proxy_mode 必须是 {allowed} 之一")
        return v

    @field_validator("db_synchronous")
    @classmethod
    def validate_synchronous(cls, v: str) -> str:
        allowed = {"FULL", "NORMAL", "OFF", "0", "1", "2"}
        if v.upper() not in allowed:
            raise ValueError(f"db_synchronous 必须是 {allowed} 之一")
        return v.upper()

    # ─── 派生路径属性 ─────────────────────────────────────────────

    @property
    def db_path(self) -> Path:
        """SQLite 数据库完整路径。"""
        return self.data_dir / self.db_filename

    @property
    def db_url(self) -> str:
        """SQLAlchemy 异步 SQLite URL。"""
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def repo_dir(self) -> Path:
        """扩展包仓库根目录。"""
        return self.data_dir / self.repo_dirname

    @property
    def logs_dir(self) -> Path:
        """日志目录完整路径。"""
        return self.data_dir / self.logs_dirname

    @property
    def static_dir(self) -> Path:
        """静态文件目录完整路径。"""
        return self.app_dir / self.static_dirname

    @property
    def template_dir(self) -> Path:
        """模板目录完整路径。"""
        return self.app_dir / self.template_dirname

    @property
    def index_file_path(self) -> Path:
        """本地 index.json 完整路径。"""
        return self.repo_dir / self.index_path


# 单例配置对象
settings = Settings()


def generate_secret_key() -> str:
    """生成安全的 256 位随机密钥。"""
    return secrets.token_hex(32)
