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
    def proxy_pool(self) -> list[str]:
        """代理池：从 PROXY_POOL 读逗号分隔的代理列表，供后续代理轮换使用。

        示例：PROXY_POOL=http://127.0.0.1:7890,socks5://127.0.0.1:1080
        返回去空、去首尾空白后的代理 URL 列表；未配置则为空列表。
        """
        raw = os.environ.get("PROXY_POOL", "")
        return [p.strip() for p in raw.split(",") if p.strip()]

    @property
    def rate_limit_aggressive_sec(self) -> float:
        """激进节流间隔（秒），对应 PlatformRule.rate_limit == 'aggressive'（抖音/小红书）。"""
        return self._read_float("RATE_LIMIT_AGGRESSIVE_SEC", 6.0)

    @property
    def rate_limit_moderate_sec(self) -> float:
        """温和节流间隔（秒），对应 PlatformRule.rate_limit == 'moderate'（B站）。"""
        return self._read_float("RATE_LIMIT_MODERATE_SEC", 2.0)

    @property
    def user_agent_override(self) -> str:
        """可选 User-Agent 覆盖：USER_AGENT 非空时覆盖 fingerprint 模块的 UA 池。"""
        return os.environ.get("USER_AGENT", "").strip()

    @staticmethod
    def _read_float(key: str, default: float) -> float:
        """读取浮点环境变量，解析失败时回退到默认值（避免脏配置导致崩溃）。"""
        try:
            return float(os.environ.get(key, str(default)))
        except (TypeError, ValueError):
            return default

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

    # ---- 完成回调 webhook ----
    @property
    def webhook_url(self) -> str:
        """作业完成/失败后 POST 通知的 URL（如 agent 的 /api/magi-content/callback）。为空则不启用。"""
        return os.environ.get("WEBHOOK_URL", "").strip()

    @property
    def webhook_token(self) -> str:
        """webhook 的 Bearer token（与接收端共享）。为空则 POST 不带 Authorization。"""
        return os.environ.get("WEBHOOK_TOKEN", "")

    # ---- 存储清理 ----
    @property
    def storage_retention_days(self) -> int:
        """已归档任务存储保留天数，0 表示不自动清理"""
        return int(os.environ.get("STORAGE_RETENTION_DAYS", "30"))

    @property
    def max_upload_mb(self) -> int:
        """本地文件上传大小上限（MB），防止超大文件耗尽磁盘。"""
        try:
            return int(os.environ.get("MAX_UPLOAD_MB", "2048"))
        except (TypeError, ValueError):
            return 2048

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

    # ---- 摘要引擎（可插拔）----
    @property
    def summary_engine(self) -> str:
        """摘要引擎名：deepseek（默认，保持现状）/ claude / qwen / minimax 等。

        见 app/services/engines。切换只需改 SUMMARY_ENGINE 环境变量，无需改代码。
        """
        return os.environ.get("SUMMARY_ENGINE", "deepseek").strip() or "deepseek"

    @property
    def summary_chunk_chars(self) -> int:
        """长文本 map-reduce 的分块大小（字符）。内容超过引擎容量时按此切块。"""
        try:
            return int(os.environ.get("SUMMARY_CHUNK_CHARS", "80000"))
        except (TypeError, ValueError):
            return 80_000

    # ---- 通义听悟 ----
    tingwu_poll_interval: int = 5
    tingwu_poll_timeout: int = 7200

    # ---- 转录后端 ----
    @property
    def transcribe_backend(self) -> str:
        """转录后端：tingwu（默认，云端）或 whisper（本地 faster-whisper）。"""
        return os.environ.get("TRANSCRIBE_BACKEND", "tingwu")

    @property
    def whisper_model(self) -> str:
        """faster-whisper 模型：tiny / base / small / medium / large-v3。"""
        return os.environ.get("WHISPER_MODEL", "small")

    @property
    def whisper_device(self) -> str:
        """运行设备：cpu / cuda。"""
        return os.environ.get("WHISPER_DEVICE", "cpu")

    @property
    def whisper_compute_type(self) -> str:
        """量化类型：int8（CPU）/ float16（GPU）。"""
        return os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

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
