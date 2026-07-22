"""SG-ERM 全局配置。

使用 pydantic-settings 从环境变量加载配置。
所有路径都基于 data_dir 解析，便于在容器中以 PVC 挂载。

环境变量命名规则: SG_ERM_<大写字段名>
例如: SG_ERM_LISTEN_PORT=18070
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SG-ERM 应用配置。"""

    model_config = SettingsConfigDict(
        env_prefix="SG_ERM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === 基础路径 ===
    # 数据根目录（PVC 挂载点）。SQLite 数据库与扩展包仓库都在此目录下。
    data_dir: Path = Path("/data")

    # === 网络 ===
    listen_host: str = "0.0.0.0"
    listen_port: int = 18070

    # === 数据库 ===
    # SQLite 文件名（相对 data_dir）
    db_filename: str = "sg-erm.db"

    # === 仓库 ===
    # 扩展包存储子目录（相对 data_dir）
    repo_dirname: str = "repo"
    # 上游官方仓库 URL
    upstream_repo_url: str = "https://extensions.stackgres.io/postgres/repository"

    # === 同步引擎 ===
    # 并发下载数
    sync_concurrency: int = 8
    # 单包下载超时（秒）
    sync_download_timeout: int = 120
    # 同步重试次数
    sync_max_retries: int = 3

    # === 缓存 / 代理 ===
    # 代理模式：hybrid / strict / proxy_only
    # hybrid: 预同步白名单 + 代理兜底（默认）
    # strict: 仅返回本地已缓存的包
    # proxy_only: 不预同步，按需代理
    proxy_mode: str = "hybrid"
    # 磁盘使用率阈值（%），超过触发 LRU 淘汰
    cache_max_disk_usage: int = 80
    # 淘汰后回落到的使用率（%）
    cache_target_disk_usage: int = 70
    # TTL（天），超过未访问的包在下次同步时删除
    cache_ttl_days: int = 7
    # 每个扩展保留的版本数
    cache_keep_versions: int = 3

    # === 安全 ===
    # JWT 签名密钥（生产环境必须通过环境变量覆盖）
    secret_key: str = "change-me-in-production"
    # JWT 算法
    jwt_algorithm: str = "HS256"
    # Access token 过期时间（分钟）
    access_token_expire_minutes: int = 1440

    # === 调度 ===
    # APScheduler 是否启用
    scheduler_enabled: bool = True

    # === 健康检查 ===
    # 仓库源健康检查间隔（秒）
    health_check_interval: int = 60

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


# 单例配置对象
settings = Settings()
