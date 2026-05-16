"""应用配置管理，从 .env 文件和环境变量加载。"""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    base_dir: str = field(default_factory=lambda: os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))

    # ---- 路径 ----
    @property
    def storage_dir(self) -> str:
        return os.path.join(self.base_dir, "storage")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self.base_dir, "logs")

    @property
    def data_dir(self) -> str:
        return os.path.join(self.base_dir, "data")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "media_pipeline.db")

    # ---- API Keys ----
    @property
    def deepseek_api_key(self) -> str:
        return os.environ.get("DEEPSEEK_API_KEY", "")

    # 通义听悟 REST API
    @property
    def tingwu_app_key(self) -> str:
        return os.environ.get("TINGWU_APP_KEY", "")

    @property
    def alibaba_access_key_id(self) -> str:
        return os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")

    @property
    def alibaba_access_key_secret(self) -> str:
        return os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")

    # OSS 配置
    @property
    def oss_endpoint(self) -> str:
        return os.environ.get("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")

    @property
    def oss_bucket(self) -> str:
        return os.environ.get("OSS_BUCKET", "")

    # ---- yt-dlp 反爬 ----
    @property
    def cookies_file(self) -> str:
        """Netscape 格式 cookies.txt 文件路径"""
        return os.environ.get("COOKIES_FILE", "")

    @property
    def cookies_from_browser(self) -> str:
        """从浏览器读取 cookies，如 chrome / firefox / safari"""
        return os.environ.get("COOKIES_FROM_BROWSER", "")

    @property
    def http_proxy(self) -> str:
        return os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy", "")

    @property
    def https_proxy(self) -> str:
        return (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or self.http_proxy
        )

    @property
    def xhs_cookie(self) -> str:
        """小红书网页版 Cookie（手动配置，优先级最高）"""
        return os.environ.get("XHS_COOKIE", "")

    @property
    def xhs_cookie_file(self) -> str:
        """小红书 Cookie 文件（可由 scripts/sync-xhs-cookie.sh 从浏览器导出）"""
        path = os.environ.get("XHS_COOKIE_FILE", "")
        if path:
            return path
        return os.path.join(self.data_dir, "xhs_cookie.txt")

    @property
    def xhs_cookie_from_browser(self) -> str:
        """从浏览器读取小红书 Cookie：chrome / safari / edge 等"""
        return os.environ.get("XHS_COOKIE_FROM_BROWSER", "") or self.cookies_from_browser

    # ---- 认证 ----
    @property
    def auth_token(self) -> str:
        """可选的 Bearer Token 认证，为空则不启用"""
        return os.environ.get("AUTH_TOKEN", "")

    # ---- 存储清理 ----
    @property
    def storage_retention_days(self) -> int:
        """已归档任务存储保留天数，0 表示不自动清理"""
        return int(os.environ.get("STORAGE_RETENTION_DAYS", "30"))

    # ---- 重试 ----
    max_retry_count: int = 3

    @property
    def stale_job_minutes(self) -> int:
        """进行中任务超过该分钟数未更新则视为卡住（启动时标记失败）。"""
        return int(os.environ.get("STALE_JOB_MINUTES", "20"))

    @property
    def yutto_timeout_sec(self) -> int:
        return int(os.environ.get("YUTTO_TIMEOUT_SEC", "7200"))

    # ---- 并发 ----
    max_workers: int = 3

    # ---- DeepSeek ----
    deepseek_model: str = "deepseek-chat"
    deepseek_max_tokens: int = 4096

    # ---- 通义听悟 ----
    tingwu_poll_interval: int = 5
    tingwu_poll_timeout: int = 7200

    # ---- Web ----
    host: str = "127.0.0.1"
    port: int = 8080

    def ensure_dirs(self):
        for d in [self.storage_dir, self.logs_dir, self.data_dir]:
            os.makedirs(d, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env")
        load_dotenv(env_path)
        _config = Config()
        _config.ensure_dirs()
    return _config
