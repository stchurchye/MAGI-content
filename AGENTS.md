# AGENTS.md — MAGI-CONTENT

## 概要
全平台内容采集 + 转写 + AI 摘要系统。粘贴链接 → 下载 → 转文字 → AI 摘要，支持 YouTube / B站 / 小红书 / 抖音 / 微博 / TikTok 等 12+ 平台，本地 faster-whisper 或云端通义听悟转录，摘要引擎可插拔。

**技术栈**：FastAPI + yt-dlp / yutto / gallery-dl + Whisper/TingWu + SQLite + ThreadPoolExecutor 流水线。

---

## 架构 & 关键目录

| 模块 | 职责 | 文件 |
|-----|------|------|
| **Web 服务** | FastAPI 应用入口、静态资源挂载、lifespan 管理 | `app/main.py` |
| **API 路由** | `/api/jobs` 提交 / `/api/search/semantic` 向量检索 / SSE 实时日志、文件下载 | `app/routes/api.py` |
| **流水线编排** | 下载 → 提音 → 转写 → OCR → 摘要，ThreadPoolExecutor 并行、取消、重试、自动清理过期任务 | `app/services/pipeline.py` |
| **平台检测** | URL → 平台类型 + 下载器选择（yt-dlp / yutto / gallery-dl / 微信公众号等） | `app/services/platform_detector.py` |
| **下载策略** | 调度 yt-dlp / yutto / gallery-dl，支持 Cookie / 代理 / 反爬配置 | `app/services/downloader.py` |
| **转写后端** | 本地 faster-whisper 或云端通义听悟（含 OSS 临时文件处理） | `app/services/transcriber.py` / `whisper_transcriber.py` |
| **摘要引擎** | 可插拔：DeepSeek / Claude（视觉支持）/ Qwen-VL / MiniMax | `app/services/engines/` |
| **数据模型** | JobStatus（pending/downloading/…/completed/failed）、Stage、Job 对象、SQLite schema | `app/models/job.py` / `app/database.py` |
| **配置管理** | 从 .env 加载各平台 key、转写、代理、存储路径配置 | `app/config.py` |

**关键数据流**：
1. POST `/api/jobs/submit` → `pipeline.submit(job_id, url, downloader)` → ThreadPoolExecutor 入队
2. 平台检测（`platform_detector.detect_platform`） → 选择下载器（`downloader.download`）
3. 提音（`extract_audio`） → 转写（whisper 或 tingwu）+ OCR（图文）
4. AI 摘要（`summarizer.summarize`，按引擎调度）→ 生成结构化 JSON/Markdown
5. 导出 zip（`magi_exporter.export`）→webhook 回调 / SSE 推送完成事件
6. SQLite 持久化 + 向量库（可选 sqlite-vec）+ 日志流文件

---

## 快速上手

### 本地开发
```bash
# 1. 装 ffmpeg（macOS）
brew install ffmpeg

# 2. 复制配置，填入最少必需 key
cp .env.example .env
# 编辑 .env：至少需要 DEEPSEEK_API_KEY 或其他摘要引擎 key
#   SUMMARY_ENGINE=deepseek            # 默认
#   DEEPSEEK_API_KEY=sk-xxxx
#   TRANSCRIBE_BACKEND=whisper         # 本地转写（默认），免费、无需云 key

# 3. 启动（自动建 .venv、装依赖）
chmod +x run.sh
./run.sh
# 访问 http://127.0.0.1:8080
```

### Docker
```bash
# 开发环境（自动重载）
docker compose up -d
# 生产加固（需 AUTH_TOKEN + webhook 配置）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### 常用测试命令
```bash
# 提交任务（粘贴链接 / 上传本地文件）
curl -X POST http://127.0.0.1:8080/api/jobs/submit \
  -F "url=https://www.youtube.com/watch?v=..."

# 列出任务
curl http://127.0.0.1:8080/api/jobs

# 语义搜索（需先配置 EMBEDDING_BACKEND）
curl "http://127.0.0.1:8080/api/search/semantic?q=关键词&k=5"

# 导出任务产出（magi_export.zip）
curl -o export.zip http://127.0.0.1:8080/api/jobs/{job_id}/export
```

---

## ⚠️ 常见坑与不变式

### 1. **Cookie 隐私默认**
- **小红书**: 未配置任何 XHS_COOKIE* 时，**绝不**自动读本机浏览器登录态（不会泄漏账号）
  - 三选一：`./scripts/sync-xhs-cookie.sh`（推荐，导出到 `data/xhs_cookie.txt`） / `XHS_COOKIE_FROM_BROWSER=chrome` / 手动粘贴 `XHS_COOKIE=`
- **B站**: 高清/大会员/付费内容需 `BILIBILI_SESSDATA`（yutto 主下载器仅认此字段）

### 2. **转写后端差异**
- `TRANSCRIBE_BACKEND=whisper`（默认）：本地 faster-whisper，免费、无云 key 需求，但 CPU 密集、首次下载模型慢
  - 建议 `WHISPER_MODEL=small`（性价比好）；GPU 加速改 `WHISPER_DEVICE=cuda`
- `TRANSCRIBE_BACKEND=tingwu`：云端通义听悟，需阿里云 AccessKey + OSS 桶；更快但有成本
- **字幕复用** (`SUBTITLE_REUSE=1`)：YouTube 官方 / B站 AI 字幕存在则直接复用，跳过本地转写（节省计算）

### 3. **摘要引擎切换**
- 改 `SUMMARY_ENGINE` 后需确保对应 key 已填 `.env`（缺 key 则启动失败）
- 支持视觉的引擎（Claude / Qwen-VL）可直接读图文笔记，免 OCR；否则先 OCR 再文本摘要
- 超长内容自动走 map-reduce 分块，避免硬截断

### 4. **存储结构**
- 每个任务独立目录 `storage/{job_id}/`，含元信息 `job_info.json` + 视频 + 音频 + 转写 + 摘要 + 图片等
- **一键导出** `magi_export.zip` 供下游 agent 取用（含完整成果）
- 自动清理过期任务（`STORAGE_RETENTION_DAYS`；默认不清，设 >0 启用）

### 5. **平台降级链**
- 下载失败时自动降级（e.g., 抖音 yt-dlp → 备用策略）；某些平台需 Cookie 才能下高清（B站、小红书、TikTok）
- 微博/微信内容无视频则只抓文本 + 图片，不转写

### 6. **代理与反爬**
- 外网（YouTube / TikTok）可能需代理：`HTTP_PROXY=http://127.0.0.1:7890` / Docker 默认 `host.docker.internal:7890`
- yt-dlp User-Agent 与 referer 会自动轮换，避免触发反爬；抖音/TikTok 节流激进，可能仍需 Cookie
- 若代理不通，启动期会警告（避免静默失败到转写阶段）

### 7. **生产 fail-closed**
- `ENVIRONMENT=production` 时必须设 `AUTH_TOKEN`（缺失拒绝启动），杜绝"默认无认证 + 公网可达"高危态
- `docker-compose.prod.yml` 移除了源码 bind-mount、禁用 --reload、配置内存限制（下载+转写+ffmpeg 较重）

---

## ⚠️ 安全与数据丢失防护

### 数据卷管理（Docker）
- **绝不** `docker compose down -v`（会永久删除所有任务产出）
  - 正确做法：只 `docker compose down`，卷保留；重启时重新 `docker compose up`
- `storage/` / `data/` / `logs/` 是 VOLUME，改动应 commit 文件或使用主机卷挂载

### 密钥隐私
- `.env` **绝不进 git**（`.gitignore` 已配置），含所有 API key / Cookie
- `data/xhs_cookie.txt`（小红书 Cookie）也在 `.gitignore` 里，仅本地文件，不上传
- 若不慎暴露 key，立即轮换对应平台 API 凭证

### 下载风险
- YouTube / TikTok 等受版权保护内容，下载需符合当地法律
- 小红书、微博等平台 ToS 可能限制自动化采集，仅供个人存档使用
- 此工具不负责用户行为合规性

### 反爬告急
- 某些平台（抖音、B站高清）对频繁下载有速率限制，过于激进可能被封 IP/账号
- 建议任务间适当延迟、使用代理轮换 IP、或配 Cookie 作可信凭证

---

## 文件修改禁忌

- `app/database.py` schema：若修改字段或索引，需手动迁移既有 SQLite 数据库（无自动迁移工具）
- `app/models/job.py` JobStatus / Stage：改动影响持久化 JSON 和状态机逻辑，需测试覆盖
- `xhs-downloader/` 内置捆绑：改代码后需重新构建 Docker 镜像、或本地 reinstall

---

## CI / 测试

```bash
# 单元测试（webhook、startup checks、url guard、transcriber 等）
pytest tests/

# 基准测试（e2e 下载、转写、摘要链路）
# 仅供本地开发，对 API key 有真实成本，CI 不跑
bash scripts/bench/run.mjs
```

---

## 联系与反馈

- 问题 / 功能建议：GitHub Issues
- 小红书 Cookie 同步失败：见 `scripts/sync-xhs-cookie.sh` 中文注释
- 生产部署问卷：使用 `docker-compose.prod.yml` + `.env` 中文说明