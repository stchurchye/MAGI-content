# MAGI-CONTENT

全平台内容搬运工具：粘贴链接 → 下载 → 转文字 → AI 摘要，一站式保存。

支持视频 / 图文笔记 / 公众号文章 / 本地文件，摘要引擎可插拔，并提供跨任务语义检索。

## 核心能力

- **多平台采集**：12 个平台规则 + 通用兜底，失败自动按平台降级到备用下载器。
- **多种转写**：本地 faster-whisper（免费、默认 `small`）或云端通义听悟；命中平台官方字幕时直接复用，免重转写。
- **可插拔摘要引擎**：DeepSeek / Claude / Qwen-VL / MiniMax，改一个环境变量即可切换。
- **多模态 + 长文本**：视觉引擎可直接读图（免 OCR）；超长内容走 map-reduce 分块归并，不再硬截断。
- **结构化摘要**：章节时间轴 / 实体 / 行动项 / 标签，渲染为小节与标签 chips。
- **语义检索**：可选向量库（sqlite-vec），跨任务按语义搜索（`/api/search/semantic`）。
- **一键导出**：每个任务产出 `magi_export.zip`（摘要 + 转写 + 图片），供下游 agent 取用；可配完成回调 webhook。

## 支持平台

| 平台 | 下载器 | 类型 | 备注 |
|------|--------|------|------|
| YouTube | yt-dlp | 视频 | 大陆可能需代理；地区/年龄限制视频需 Cookie |
| B站 | yutto（备用 yt-dlp） | 视频、AI字幕 | 高清/大会员/付费内容需 `BILIBILI_SESSDATA` |
| 小红书 | XHS-Downloader | 图文 / 视频 | 需 Cookie，见下方配置 |
| 抖音 | yt-dlp | 视频 | 反爬严格、节流较激进，可能需 Cookie |
| 微博 | yt-dlp（降级 gallery-dl） | 视频 / 图文 | 图文/九宫格走 gallery-dl，支持 `t.cn` 短链 |
| 快手 | yt-dlp（降级 gallery-dl） | 视频 | 稳定下载可能需 Cookie |
| Instagram | yt-dlp（降级 gallery-dl） | 视频 / 图文 | 多数内容需登录 Cookie，大陆需代理 |
| TikTok | yt-dlp | 视频 | 大陆需代理 |
| X (Twitter) | yt-dlp（降级 gallery-dl） | 视频 / 图文 | 纯图集走 gallery-dl，受限内容需 Cookie |
| 微信公众号 | trafilatura | 图文文章 | 抓取正文文本直接进摘要，不下载/不转写 |
| 本地文件 | — | 视频 / 音频 / 图片 | 上传或本地路径，跳过下载直接处理 |
| N站 | yt-dlp | 视频 | 部分视频需日本 IP + 登录 |
| 其他 | yt-dlp | 通用 | 兜底，尝试通用提取 |

## 快速开始

```bash
# 1. 安装 ffmpeg
brew install ffmpeg

# 2. 配置（详见 .env.example，含全部可选项的中文说明）
cp .env.example .env
# 最简免费方案：本地转写 + 一个摘要引擎 key
#   TRANSCRIBE_BACKEND=whisper
#   DEEPSEEK_API_KEY=sk-...        # 默认摘要引擎

# 3. 启动（首次会自动建 .venv 并装依赖）
chmod +x run.sh
./run.sh
```

打开 http://127.0.0.1:8080 ，粘贴链接或上传本地文件即可。

> 需要 Python 3.10+（推荐 3.12）。

### Docker

```bash
docker compose up -d   # 映射 127.0.0.1:8080；.env 自动注入
```

## 摘要引擎与 Key

摘要引擎可插拔，用 `SUMMARY_ENGINE` 选择（默认 `deepseek`），按所选引擎填对应 Key：

| 引擎 | `SUMMARY_ENGINE` | Key | 视觉 |
|------|------------------|-----|------|
| DeepSeek（默认） | `deepseek` | `DEEPSEEK_API_KEY` | 否 |
| Claude | `claude` | `ANTHROPIC_API_KEY` | 是 |
| 通义千问 VL | `qwen` / `qwen-vl` | `DASHSCOPE_API_KEY` | 是 |
| MiniMax | `minimax` | `MINIMAX_API_KEY` | 可选 |

- 切到**视觉引擎**后，图文笔记可直接读图，免 OCR。
- **转写后端**：`TRANSCRIBE_BACKEND=whisper`（本地、免费）或 `tingwu`（云端通义听悟，需阿里云 AccessKey + OSS）。
- **语义检索**：配置 `EMBEDDING_BACKEND` + `EMBEDDING_API_KEY` 后启用 `/api/search/semantic`。

### Key 获取

- **DeepSeek**：https://platform.deepseek.com
- **Claude**：https://console.anthropic.com
- **通义千问 / 通义听悟 / 嵌入**：https://dashscope.console.aliyun.com

## 目录结构

每个任务的产物保存在独立目录：

```
storage/{job_id}/
├── job_info.json          # 任务元信息（标题、平台、下载器、产物路径）
├── {job_id}.mp4           # 下载的视频（yt-dlp 路径；B站/yutto 按标题命名）
├── {job_id}.wav           # 提取的音频
├── {job_id}.txt           # 纯文本转录（或公众号正文 / 复用的字幕文本）
├── {job_id}_detailed.txt  # 带时间戳的转录
├── {job_id}.md            # 结构化 AI 摘要
├── images/                # 图文笔记的图片（小红书等）
├── magi_export.zip        # 一键导出包（摘要 + 转写 + 图片）
└── job.log                # 全链路日志
```

## 小红书 Cookie

隐私默认：未配置时**绝不**自动读取本机浏览器登录态。三选一：

```bash
# 方式 1（推荐）：本机执行一次，导出到 data/xhs_cookie.txt
./scripts/sync-xhs-cookie.sh

# 方式 2：显式开启浏览器自动读取
XHS_COOKIE_FROM_BROWSER=chrome   # 或 safari

# 方式 3：手动粘贴
XHS_COOKIE=...
```
