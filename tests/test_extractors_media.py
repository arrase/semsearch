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
    average_hash,
    extract_audio,
    extract_video,
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
    assert windows[0].kind == SegmentKind.AUDIO
    assert windows[0].modality == Modality.AUDIO

    assert isinstance(windows[0].payload, dict)
    raw_wav = base64.b64decode(windows[0].payload["audio"])
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

    video_segs = extract_video(str(vid_path), sample_interval_sec=3.0, hash_threshold=4)

    frames = [s for s in video_segs if s.kind == SegmentKind.FRAME]
    audio = [s for s in video_segs if s.kind == SegmentKind.AUDIO]

    # At 3s intervals:
    # frame 0 (t=0.0): smptebars
    # frame 1 (t=3.0): testsrc (different scene -> kept)
    # frame 2 (t=6.0): testsrc (same scene -> dropped as duplicate)
    assert len(frames) == 2
    assert frames[0].loc == 0.0
    assert frames[1].loc == 3.0
    assert len(audio) >= 1
    assert all(s.modality == Modality.IMAGE for s in frames)
    assert all(s.modality == Modality.AUDIO for s in audio)
