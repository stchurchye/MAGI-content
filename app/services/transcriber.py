"""通义听悟 REST API 语音转文字（AppKey + OSS 上传）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote, urlencode, urlparse

import requests


# ---- Aliyun ROA (REST Open API) 签名 ----

def _roa_sign(
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    body: str,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    """生成 Aliyun ROA 风格的 Authorization header。"""
    # 标准化 headers
    signed_headers = {
        "x-acs-action": headers.get("x-acs-action", ""),
        "x-acs-version": headers.get("x-acs-version", ""),
        "x-acs-date": headers.get("x-acs-date", ""),
        "x-acs-content-sha256": headers.get("x-acs-content-sha256", ""),
        "host": headers.get("host", ""),
        "content-type": headers.get("content-type", "application/json"),
    }

    # Canonical request
    canonical_uri = path if path.startswith("/") else "/" + path
    if query:
        canonical_query = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}"
            for k, v in sorted(query.items())
        )
    else:
        canonical_query = ""

    canonical_headers = "\n".join(
        f"{k}:{v}" for k, v in sorted(signed_headers.items())
    ) + "\n"

    signed_header_keys = ";".join(sorted(signed_headers.keys()))

    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_header_keys,
        body_hash,
    ])

    # String to sign
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

    # Aliyun uses x-acs-date for the signing timestamp
    acs_date = signed_headers["x-acs-date"]
    str_to_sign = f"ACS3-HMAC-SHA256\n{hashed_canonical}"

    # Sign
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        str_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return (
        f"ACS3-HMAC-SHA256 "
        f"Credential={access_key_id},"
        f"SignedHeaders={signed_header_keys},"
        f"Signature={signature}"
    )


def _tingwu_request(
    method: str,
    path: str,
    query: dict[str, str] | None = None,
    body: dict | None = None,
    access_key_id: str = "",
    access_key_secret: str = "",
) -> dict:
    """发送带 ROA 签名的通义听悟 API 请求。"""
    host = "tingwu.cn-beijing.aliyuncs.com"
    url = f"https://{host}{path}"
    query = query or {}
    body_dict = body or {}
    body_str = json.dumps(body_dict) if body_dict else ""

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_sha256 = hashlib.sha256(body_str.encode("utf-8")).hexdigest()

    headers = {
        "host": host,
        "content-type": "application/json",
        "x-acs-action": path.split("/")[-1] if "/tasks" not in path else (
            "CreateTask" if "type=offline" in urlencode(query) else "GetTaskInfo"
        ),
        "x-acs-version": "2023-09-30",
        "x-acs-date": now_utc,
        "x-acs-content-sha256": content_sha256,
    }

    auth = _roa_sign(method, path, query, headers, body_str,
                     access_key_id, access_key_secret)
    headers["Authorization"] = auth

    if query:
        url += "?" + urlencode(query)

    resp = requests.request(method, url, headers=headers, data=body_str or None, timeout=60)
    result = resp.json()

    if resp.status_code >= 400:
        raise RuntimeError(
            f"通义听悟 API 错误 (HTTP {resp.status_code}): "
            f"Code={result.get('Code')} Message={result.get('Message')}"
        )

    return result


# ---- OSS 上传 ----

def _oss_upload(
    local_path: str,
    object_key: str,
    endpoint: str,
    bucket: str,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    """上传文件到 OSS 并返回公开 URL（使用自定义签名，避免引入 oss2 SDK）。"""
    host = f"{bucket}.{endpoint}"
    url = f"https://{host}/{object_key}"

    with open(local_path, "rb") as f:
        file_content = f.read()

    content_type = "audio/wav"
    now_utc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    content_md5 = hashlib.md5(file_content).digest()
    content_md5_b64 = __import__("base64").b64encode(content_md5).decode()

    # OSS signature v2
    string_to_sign = f"PUT\n{content_md5_b64}\n{content_type}\n{now_utc}\nx-oss-date:{now_utc}\n/{bucket}/{object_key}"
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    sig_b64 = __import__("base64").b64encode(signature).decode()

    headers = {
        "Content-Type": content_type,
        "Content-MD5": content_md5_b64,
        "Date": now_utc,
        "x-oss-date": now_utc,
        "Authorization": f"OSS {access_key_id}:{sig_b64}",
    }

    resp = requests.put(url, data=file_content, headers=headers, timeout=120)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"OSS 上传失败 (HTTP {resp.status_code}): {resp.text[:200]}")

    # 生成预签名 URL（6 小时有效），这样私有 bucket 也能被通义听悟访问
    expires = int(time.time()) + 21600  # 6 hours
    string_to_sign = f"GET\n\n\n{expires}\n/{bucket}/{object_key}"
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    sig_b64 = __import__("base64").b64encode(signature).decode()
    signed_url = (
        f"https://{host}/{quote(object_key)}"
        f"?OSSAccessKeyId={quote(access_key_id)}"
        f"&Expires={expires}"
        f"&Signature={quote(sig_b64, safe='')}"
    )
    return signed_url


# ---- 主流程 ----

def transcribe_tingwu(
    audio_path: str,
    output_dir: str,
    job_id: str,
    api_key: str,           # 不再使用 DashScope key，保留参数兼容
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    poll_interval: int = 5,
    poll_timeout: int = 7200,
    # 新增参数
    app_key: str = "",
    ak_id: str = "",
    ak_secret: str = "",
    oss_endpoint: str = "",
    oss_bucket: str = "",
) -> dict:
    """
    使用通义听悟 REST API + OSS 上传转录音频。
    """
    if not app_key:
        raise RuntimeError("未配置 TINGWU_APP_KEY，请在 .env 中设置")
    if not ak_id or not ak_secret:
        raise RuntimeError("未配置 ALIBABA_CLOUD_ACCESS_KEY，请在 .env 中设置")
    if not oss_bucket:
        raise RuntimeError("未配置 OSS_BUCKET，请在 .env 中设置")

    from app.ui_copy import ProgressMsg

    progress_cb(0, ProgressMsg.UPLOAD_OSS)
    logger.info("Tingwu: uploading to OSS | audio=%s", audio_path)

    # 1. 上传音频到 OSS
    object_key = f"tingwu-uploads/{job_id}/{os.path.basename(audio_path)}"
    file_url = _oss_upload(audio_path, object_key, oss_endpoint, oss_bucket, ak_id, ak_secret)
    logger.info("Tingwu: OSS upload done | url=%s", file_url)
    progress_cb(5, ProgressMsg.OSS_DONE)

    # 2. 创建转写任务
    progress_cb(8, ProgressMsg.TRANSCRIBE_TASK)
    create_resp = _tingwu_request(
        method="PUT",
        path="/openapi/tingwu/v2/tasks",
        query={"type": "offline"},
        body={
            "AppKey": app_key,
            "Input": {
                "FileUrl": file_url,
                "SourceLanguage": "cn",
                "LanguageHints": ["zh", "en"],
            },
            "Parameters": {
                "Transcription": {
                    "DiarizationEnabled": False,
                },
            },
        },
        access_key_id=ak_id,
        access_key_secret=ak_secret,
    )

    data = create_resp.get("Data", {})
    task_id = data.get("TaskId")
    if not task_id:
        raise RuntimeError(f"创建任务失败: {create_resp}")

    logger.info("Tingwu: task created | task_id=%s", task_id)
    progress_cb(10, f"转写任务已创建 ({task_id[:12]}…)")

    # 3. 轮询结果
    elapsed = 0
    last_pct = 10
    result_url = None

    while elapsed < poll_timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval

        # 查询任务状态
        try:
            status_resp = _tingwu_request(
                method="GET",
                path=f"/openapi/tingwu/v2/tasks/{task_id}",
                access_key_id=ak_id,
                access_key_secret=ak_secret,
            )
        except Exception as e:
            logger.warning("Tingwu poll error: %s", e)
            continue

        status_data = status_resp.get("Data", {})
        task_status = status_data.get("TaskStatus", "")
        logger.info("Tingwu: poll | status=%s elapsed=%ds", task_status, elapsed)

        if task_status == "COMPLETED":
            result = status_data.get("Result", {})
            result_url = result.get("Transcription")
            if result_url:
                break
            raise RuntimeError("转写完成但无结果 URL")

        if task_status == "FAILED":
            raise RuntimeError(
                f"转写任务失败: {status_data.get('ErrorMessage', 'unknown')}"
            )

        # 进度估算
        pct = min(10 + int(elapsed / poll_timeout * 85), 90)
        if pct > last_pct:
            last_pct = pct
        progress_cb(pct, f"{ProgressMsg.TRANSCRIBE_POLL} ({elapsed} 秒)")

    if elapsed >= poll_timeout:
        raise TimeoutError(f"通义听悟转写超时 ({poll_timeout}s)")

    # 4. 下载转写结果
    progress_cb(90, ProgressMsg.TRANSCRIBE_FETCH)
    logger.info("Tingwu: downloading result | url=%s", result_url)
    dl_resp = requests.get(result_url, timeout=60)
    dl_resp.raise_for_status()
    result_json = dl_resp.json()
    transcript_data = result_json if isinstance(result_json, dict) else {}

    # 保存原始响应用于调试
    raw_path = os.path.join(output_dir, f"{job_id}_tingwu_raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)

    # 5. 解析结果（尝试多种可能的结构）
    full_text_parts = []
    segments = []

    # 通义听悟离线转写结果可能有多种包装结构
    transcription = (
        transcript_data.get("Transcription") or
        transcript_data.get("Result", {}).get("Transcription") or
        transcript_data
    )
    paragraphs = transcription.get("Paragraphs", []) if isinstance(transcription, dict) else []

    # 也尝试直接从顶层获取
    if not paragraphs:
        paragraphs = transcript_data.get("Paragraphs", [])
    if not paragraphs and "Data" in transcript_data:
        data = transcript_data["Data"]
        if isinstance(data, dict):
            paragraphs = data.get("Paragraphs", [])
            if not paragraphs:
                inner_trans = data.get("Transcription") or data.get("Result", {}).get("Transcription") or {}
                paragraphs = inner_trans.get("Paragraphs", []) if isinstance(inner_trans, dict) else []

    for para in paragraphs:
        # 通义听悟可能返回 Sentences 或 Words（逐词），兼容两种格式
        para_sentences = para.get("Sentences", [])
        if para_sentences:
            for sent in para_sentences:
                text = sent.get("Text", "").strip()
                if text:
                    full_text_parts.append(text)
                    segments.append({
                        "begin_time": sent.get("BeginTime", 0),
                        "end_time": sent.get("EndTime", 0),
                        "text": text,
                    })
        else:
            # Words 格式：按 SentenceId 分组合并
            words = para.get("Words", [])
            sentence_map: dict[int, list[dict]] = {}
            for w in words:
                sid = w.get("SentenceId", 0)
                sentence_map.setdefault(sid, []).append(w)
            for sid in sorted(sentence_map.keys()):
                ws = sentence_map[sid]
                sentence_text = "".join(w.get("Text", "") for w in ws)
                begin = ws[0].get("Start", 0)
                end = ws[-1].get("End", 0)
                full_text_parts.append(sentence_text)
                segments.append({
                    "begin_time": begin,
                    "end_time": end,
                    "text": sentence_text,
                })

    full_text = "\n".join(full_text_parts)

    if not full_text:
        logger.warning(
            "Tingwu: empty result. Top-level keys: %s, transcription keys: %s, paragraphs count: %d",
            list(transcript_data.keys()) if isinstance(transcript_data, dict) else "not-dict",
            list(transcription.keys()) if isinstance(transcription, dict) else "not-dict",
            len(paragraphs),
        )

    # 6. 保存文件
    transcript_path = os.path.join(output_dir, f"{job_id}.txt")
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    detailed_path = os.path.join(output_dir, f"{job_id}_detailed.txt")
    with open(detailed_path, "w", encoding="utf-8") as f:
        for seg in segments:
            begin = seg["begin_time"] / 1000
            end = seg["end_time"] / 1000
            f.write(f"[{begin:07.1f} - {end:07.1f}] {seg['text']}\n")

    # 7. 清理 OSS 文件（可选，失败不影响流程）
    try:
        _oss_delete(object_key, oss_endpoint, oss_bucket, ak_id, ak_secret)
        logger.info("Tingwu: OSS file deleted | key=%s", object_key)
    except Exception as e:
        logger.warning("Tingwu: failed to delete OSS file: %s", e)

    duration = segments[-1]["end_time"] / 1000 if segments else 0
    logger.info("Tingwu: done | text_len=%d segments=%d duration=%.1f",
                 len(full_text), len(segments), duration)
    progress_cb(95, f"转写完成 ({len(full_text)} 字)")

    return {
        "transcript_path": transcript_path,
        "transcript_text": full_text,
        "language": "zh",
        "duration": duration,
        "segments": segments,
    }


def _oss_delete(
    object_key: str,
    endpoint: str,
    bucket: str,
    access_key_id: str,
    access_key_secret: str,
) -> None:
    """删除 OSS 对象。"""
    host = f"{bucket}.{endpoint}"
    url = f"https://{host}/{object_key}"
    now_utc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    string_to_sign = f"DELETE\n\n\n{now_utc}\nx-oss-date:{now_utc}\n/{bucket}/{object_key}"
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    sig_b64 = __import__("base64").b64encode(signature).decode()

    headers = {
        "Date": now_utc,
        "x-oss-date": now_utc,
        "Authorization": f"OSS {access_key_id}:{sig_b64}",
    }
    requests.delete(url, headers=headers, timeout=30)
