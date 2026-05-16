# MAGI-CONTENT

全平台内容搬运工具：粘贴链接 → 下载 → 转文字 → AI 摘要，一站式保存。

## 支持平台

| 平台 | 下载器 | 能力 | 备注 |
|------|--------|------|------|
| YouTube | yt-dlp | 视频下载、1080p | 需要 Cookie 过反爬 |
| B站 | yt-dlp / yutto | 视频、弹幕、AI字幕、合集 | |
| 小红书 | XHS-Downloader | 图文笔记、视频 | 自动使用 curl-cffi 过 TLS 检测 |
| 抖音 | yt-dlp | 视频 | |
| N站 | yt-dlp | 视频 | |
| 其他 | yt-dlp | 通用兜底 | |

## 快速开始

```bash
# 1. 安装 ffmpeg
brew install ffmpeg

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY 和 DASHSCOPE_API_KEY

# 3. 启动
chmod +x run.sh
./run.sh
```

打开 http://127.0.0.1:8080 ，粘贴链接即可。

## API Key 获取

- **Claude API**: https://console.anthropic.com → 创建 API Key
- **通义听悟**: https://dashscope.console.aliyun.com → 开通语音转写服务 → 获取 API Key

## 目录结构

```
storage/{job_id}/
├── job_info.json        # 任务元信息
├── video.mp4            # 下载的视频
├── audio.wav            # 提取的音频
├── transcript.txt       # 纯文本转录
├── transcript_detailed.txt  # 带时间戳
├── summary.md           # AI 摘要
├── danmaku.xml          # 弹幕（B站）
├── images/              # 图片（小红书）
└── job.log              # 全链路日志
```
