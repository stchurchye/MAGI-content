"""whisper_transcriber 的行为测试。

通过注入假的 model_factory 在边界处替换 faster-whisper，
因此无需安装 faster-whisper、无需下载模型，纯 Python 即可运行。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.whisper_transcriber import transcribe_whisper


# ---- 假的 faster-whisper 边界 ----

class _FakeSegment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    def __init__(self, duration, language="zh"):
        self.duration = duration
        self.language = language


class _FakeModel:
    def __init__(self, segments, info):
        self._segments = segments
        self._info = info

    def transcribe(self, audio_path, language=None, vad_filter=True):
        return iter(self._segments), self._info


def _factory(segments, info):
    def make(model, device, compute_type):
        return _FakeModel(segments, info)
    return make


def _run(tmp_path, segments, duration=10.0, language="zh"):
    progress = []
    result = transcribe_whisper(
        audio_path="/fake/audio.wav",
        output_dir=str(tmp_path),
        job_id="job1",
        logger=logging.getLogger("test"),
        progress_cb=lambda p, m: progress.append((p, m)),
        model_factory=_factory(segments, _FakeInfo(duration, language)),
    )
    result["_progress"] = progress
    return result


# ---- 行为 1：文本拼接 + 写入 transcript 文件 ----

def test_joins_segment_text_and_writes_transcript_file(tmp_path):
    segs = [_FakeSegment(0.0, 2.0, "你好"), _FakeSegment(2.0, 4.0, "世界")]
    result = _run(tmp_path, segs)

    assert result["transcript_text"] == "你好\n世界"
    with open(result["transcript_path"], encoding="utf-8") as f:
        assert f.read() == "你好\n世界"


# ---- 行为 2：segments 结构对齐 tingwu（begin_time/end_time 为毫秒整数）----

def test_segments_use_millisecond_int_times_like_tingwu(tmp_path):
    segs = [_FakeSegment(0.0, 2.5, "甲"), _FakeSegment(2.5, 4.0, "乙")]
    result = _run(tmp_path, segs)

    assert result["segments"] == [
        {"begin_time": 0, "end_time": 2500, "text": "甲"},
        {"begin_time": 2500, "end_time": 4000, "text": "乙"},
    ]
    # duration 取最后一个 segment 的结束秒数（与 tingwu 一致）
    assert result["duration"] == 4.0
    # language 透传自模型识别结果
    assert result["language"] == "zh"


# ---- 行为 3：空 / 纯空白 segment 被跳过 ----

def test_skips_empty_and_whitespace_segments(tmp_path):
    segs = [
        _FakeSegment(0.0, 1.0, "有内容"),
        _FakeSegment(1.0, 2.0, "   "),
        _FakeSegment(2.0, 3.0, ""),
        _FakeSegment(3.0, 4.0, "继续"),
    ]
    result = _run(tmp_path, segs)

    assert result["transcript_text"] == "有内容\n继续"
    assert [s["text"] for s in result["segments"]] == ["有内容", "继续"]


# ---- 行为 4：空音频（无 segment）不崩，返回空文本 ----

def test_no_segments_returns_empty_without_crashing(tmp_path):
    result = _run(tmp_path, [])

    assert result["transcript_text"] == ""
    assert result["segments"] == []
    assert result["duration"] == 0
    assert os.path.isfile(result["transcript_path"])


# ---- 行为 5：detailed 文件按 [begin - end] text 格式写时间戳（对齐 tingwu）----

def test_writes_detailed_file_with_timestamp_lines(tmp_path):
    segs = [_FakeSegment(0.0, 2.5, "甲"), _FakeSegment(2.5, 4.0, "乙")]
    _run(tmp_path, segs)

    detailed = os.path.join(str(tmp_path), "job1_detailed.txt")
    assert os.path.isfile(detailed)
    with open(detailed, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines == [
        "[00000.0 - 00002.5] 甲",
        "[00002.5 - 00004.0] 乙",
    ]


# ---- 行为 6：进度回调被调用，结尾推进到 95 ----

def test_reports_progress_ending_near_95(tmp_path):
    segs = [_FakeSegment(0.0, 5.0, "前"), _FakeSegment(5.0, 10.0, "后")]
    result = _run(tmp_path, segs, duration=10.0)

    pcts = [p for p, _ in result["_progress"]]
    assert pcts, "progress_cb 应被调用"
    assert pcts[-1] == 95
    assert pcts == sorted(pcts), "进度应单调不减"
