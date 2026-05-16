"""阿里云 OCR 文字识别（ROA 签名 + 二进制图片直接上传）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable

import requests

OCR_HOST = "ocr-api.cn-shanghai.aliyuncs.com"
OCR_VERSION = "2021-07-07"
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif", ".heic"}


def list_image_files(images_dir: str) -> list[str]:
    """递归收集目录下的图片文件路径。"""
    if not os.path.isdir(images_dir):
        return []
    found: list[str] = []
    for root, _dirs, files in os.walk(images_dir):
        for f in sorted(files):
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                found.append(os.path.join(root, f))
    return found


def _roa_sign_binary(
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    """生成 Aliyun ACS3-HMAC-SHA256 签名（binary body 版本）。"""
    signed_headers = {
        "host": headers.get("host", ""),
        "content-type": headers.get("content-type", "application/octet-stream"),
        "x-acs-action": headers.get("x-acs-action", ""),
        "x-acs-version": headers.get("x-acs-version", ""),
        "x-acs-date": headers.get("x-acs-date", ""),
        "x-acs-content-sha256": headers.get("x-acs-content-sha256", ""),
    }

    canonical_uri = path if path.startswith("/") else "/" + path
    canonical_query = ""
    canonical_headers = "\n".join(
        f"{k}:{v}" for k, v in sorted(signed_headers.items())
    ) + "\n"
    signed_header_keys = ";".join(sorted(signed_headers.keys()))

    body_hash = hashlib.sha256(body).hexdigest()

    canonical_request = "\n".join([
        method.upper(),
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_header_keys,
        body_hash,
    ])

    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    str_to_sign = f"ACS3-HMAC-SHA256\n{hashed_canonical}"

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


def _ocr_request(
    image_bytes: bytes,
    action: str,
    access_key_id: str,
    access_key_secret: str,
) -> dict:
    """发送单张图片 OCR 请求，返回解析后的结果。"""
    host = OCR_HOST
    path = "/"
    url = f"https://{host}{path}"

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content_sha256 = hashlib.sha256(image_bytes).hexdigest()

    headers = {
        "host": host,
        "content-type": "application/octet-stream",
        "x-acs-action": action,
        "x-acs-version": OCR_VERSION,
        "x-acs-date": now_utc,
        "x-acs-content-sha256": content_sha256,
    }

    auth = _roa_sign_binary("POST", path, headers, image_bytes,
                            access_key_id, access_key_secret)
    headers["Authorization"] = auth

    resp = requests.post(url, data=image_bytes, headers=headers, timeout=60)
    result = resp.json()

    if resp.status_code >= 400:
        raise RuntimeError(
            f"OCR API 错误 (HTTP {resp.status_code}): "
            f"Code={result.get('Code')} Message={result.get('Message')}"
        )

    # Data 字段是 JSON 字符串
    data_raw = result.get("Data", "{}")
    if isinstance(data_raw, dict):
        parsed = data_raw
    else:
        parsed = json.loads(data_raw)

    text = parsed.get("content") or parsed.get("Content") or ""
    return {"text": text, "raw": parsed, "request_id": result.get("RequestId")}


def ocr_images(
    images_dir: str,
    output_dir: str,
    job_id: str,
    ak_id: str,
    ak_secret: str,
    logger: logging.Logger,
    progress_cb: Callable[[int, str], None],
    action: str = "RecognizeAdvanced",
) -> dict:
    """遍历图片目录，逐张 OCR，返回拼接文本和单张结果。"""
    if not ak_id or not ak_secret:
        raise RuntimeError("未配置 ALIBABA_CLOUD_ACCESS_KEY，请在 .env 中设置")
    if not os.path.isdir(images_dir):
        raise RuntimeError(f"OCR: 图片目录不存在: {images_dir}")

    image_files = list_image_files(images_dir)
    total = len(image_files)
    if total == 0:
        raise RuntimeError(f"OCR: 目录中无图片文件: {images_dir}")

    logger.info("OCR: scanning images | dir=%s total=%d", images_dir, total)
    from app.ui_copy import ProgressMsg

    progress_cb(0, f"{ProgressMsg.OCR_FOUND} {total} 张")

    results: list[dict] = []
    text_parts: list[str] = []
    success = 0
    errors = 0

    for i, img_path in enumerate(image_files):
        filename = os.path.basename(img_path)
        progress_cb(int((i + 1) / total * 90), f"{ProgressMsg.OCR_IMAGE} {i + 1}/{total}")

        try:
            file_size = os.path.getsize(img_path)
            if file_size > MAX_IMAGE_BYTES:
                logger.warning("OCR: image too large (%d bytes), skipping | %s", file_size, filename)
                results.append({"file": filename, "error": f"Image too large ({file_size} bytes)", "text": ""})
                errors += 1
                continue

            with open(img_path, "rb") as f:
                image_bytes = f.read()

            ocr_resp = _ocr_request(image_bytes, action, ak_id, ak_secret)
            text = ocr_resp["text"].strip()

            results.append({
                "file": filename,
                "img_path": img_path,
                "text": text,
                "data": ocr_resp["raw"],
            })

            if text:
                text_parts.append(f"--- 图片 {i + 1}: {filename} ---\n{text}")
                success += 1
            else:
                logger.info("OCR: no text in image | %s", filename)
                errors += 1

            logger.info(
                "OCR: image %d/%d | file=%s text_len=%d request_id=%s",
                i + 1, total, filename, len(text), ocr_resp["request_id"],
            )

        except Exception as e:
            logger.warning("OCR: failed on image | file=%s error=%s", filename, e)
            results.append({"file": filename, "error": str(e), "text": ""})
            errors += 1

        if i < total - 1:
            time.sleep(0.1)

    full_text = "\n\n".join(text_parts)

    # 保存结构化结果
    ocr_path = os.path.join(output_dir, f"{job_id}_ocr_results.json")
    with open(ocr_path, "w", encoding="utf-8") as f:
        json.dump({
            "job_id": job_id,
            "action": action,
            "image_count": total,
            "success_count": success,
            "error_count": errors,
            "full_text": full_text,
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    # 保存纯文本
    txt_path = os.path.join(output_dir, f"{job_id}_ocr_text.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    logger.info("OCR: done | images=%d success=%d errors=%d text_len=%d",
                total, success, errors, len(full_text))

    return {
        "ocr_text": full_text,
        "ocr_path": ocr_path,
        "results": results,
    }
