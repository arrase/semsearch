"""Unit tests for media extractor (audio windowing and video perceptual hash deduplication)."""

import base64
import io
from pathlib import Path
import subprocess
import wave
import numpy as np
from PIL import Image
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors.media import (
    AudioWindow,
    VideoFrame,
    average_hash,
    extract_audio,
    extract_audio_segments,
    extract_video,
    extract_video_segments,
    hamming_distance,
)


def test_average_hash_and_hamming_distance() -> None:
    img1 = Image.new("RGB", (64, 64), color=(255, 255, 255))
    img2 = Image.new("RGB", (64, 64), color=(255, 255, 255))
    img3 = Image.new("L", (64, 64))
    # Fill left half black, right half white
    for x in range(32, 64):
        for y in range(64):
            img3.putpixel((x, y), 255)

    h1 = average_hash(img1)
    h2 = average_hash(img2)
    h3 = average_hash(img3)

    assert hamming_distance(h1, h2) == 0
    assert hamming_distance(h1, h3) > 0


def test_extract_audio_windows(tmp_path: Path) -> None:
    wav_path = tmp_path / "tone.wav"
    sr = 16000
    duration_sec = 35
    total_samples = sr * duration_sec

    # 440 Hz sine wave
    t = np.linspace(0, duration_sec, total_samples, endpoint=False)
    waveform = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(waveform.tobytes())

    windows = extract_audio(str(wav_path))
    assert len(windows) == 2
    assert windows[0].loc == 0.0
    assert windows[0].loc_end == 30.0
    assert windows[1].loc == 25.0
    assert windows[1].loc_end == 35.0

    raw_wav = base64.b64decode(windows[0].data_base64)
    with wave.open(io.BytesIO(raw_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000


def test_extract_audio_silence_rejection(tmp_path: Path) -> None:
    wav_path = tmp_path / "silence.wav"
    sr = 16000
    duration_sec = 10
    total_samples = sr * duration_sec
    silent_waveform = np.zeros(total_samples, dtype=np.int16)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(silent_waveform.tobytes())

    windows = extract_audio(str(wav_path))
    assert len(windows) == 0


def test_extract_video_deduplication(tmp_path: Path) -> None:
    vid_path = tmp_path / "test_video.mp4"
    # Create 7-second video: 3s smptebars, 4s testsrc, with audio tone
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "smptebars=duration=3:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=7",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(vid_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0

    frames, audio = extract_video(str(vid_path), sample_interval_sec=3.0, hash_threshold=4)

    # At 3s intervals:
    # frame 0 (t=0.0): smptebars
    # frame 1 (t=3.0): testsrc (different scene -> kept)
    # frame 2 (t=6.0): testsrc (same scene -> dropped as duplicate)
    assert len(frames) == 2
    assert frames[0].loc == 0.0
    assert frames[1].loc == 3.0
    assert len(audio) >= 1


def test_media_to_segment() -> None:
    audio_win = AudioWindow(data_base64="dGVzdA==", loc=0.0, loc_end=30.0)
    seg_audio = audio_win.to_segment()
    assert seg_audio.kind == SegmentKind.AUDIO
    assert seg_audio.modality == Modality.AUDIO
    assert seg_audio.payload == {"audio": "dGVzdA=="}

    video_frame = VideoFrame(data_base64="aW1hZ2U=", loc=3.0, hash=12345)
    seg_frame = video_frame.to_segment()
    assert seg_frame.kind == SegmentKind.FRAME
    assert seg_frame.modality == Modality.IMAGE
    assert seg_frame.payload == {"image": "aW1hZ2U="}


def test_extract_audio_and_video_segments(tmp_path: Path) -> None:
    # 1. Test audio segments
    wav_path = tmp_path / "tone.wav"
    sr = 16000
    duration_sec = 35
    total_samples = sr * duration_sec
    t = np.linspace(0, duration_sec, total_samples, endpoint=False)
    waveform = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)

    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(waveform.tobytes())

    audio_segs = extract_audio_segments(str(wav_path))
    assert len(audio_segs) == 2
    for seg in audio_segs:
        assert seg.kind == SegmentKind.AUDIO
        assert seg.modality == Modality.AUDIO
        assert "audio" in seg.payload

    # 2. Test video segments
    vid_path = tmp_path / "test_video.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "smptebars=duration=3:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=7",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(vid_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0

    video_segs = extract_video_segments(str(vid_path), sample_interval_sec=3.0, hash_threshold=4)
    assert len(video_segs) >= 3
    frame_segs = [s for s in video_segs if s.kind == SegmentKind.FRAME]
    audio_win_segs = [s for s in video_segs if s.kind == SegmentKind.AUDIO]
    assert len(frame_segs) == 2
    assert len(audio_win_segs) >= 1
    assert all(s.modality == Modality.IMAGE for s in frame_segs)
    assert all(s.modality == Modality.AUDIO for s in audio_win_segs)
