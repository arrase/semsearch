"""Media extractor for audio and video files."""

import base64
import io
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


def average_hash(im: Image.Image, hash_size: int = 8) -> int:
    """Calculate 8x8 average perceptual hash for an image."""
    gray = im.convert("L").resize((hash_size, hash_size), Image.Resampling.BILINEAR)
    pixels = np.asarray(gray).flatten()
    avg = float(pixels.mean())
    h = 0
    for p in pixels:
        h = (h << 1) | (1 if p > avg else 0)
    return h


def hamming_distance(h1: int, h2: int) -> int:
    """Calculate bit Hamming distance between two 64-bit integer hashes."""
    return (h1 ^ h2).bit_count()


def extract_audio(
    path: str,
    target_sr: int = 16000,
    window_sec: float = 30.0,
    hop_sec: float = 25.0,
    min_rms: float = 0.003,
) -> list[ExtractedSegment]:
    """Decode audio to 16 kHz mono WAV, slice into windows, and filter silence."""
    cmd = [
        "ffmpeg",
        "-loglevel",
        "quiet",
        "-i",
        path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(target_sr),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    raw_bytes = proc.stdout

    if not raw_bytes:
        return []

    samples = np.frombuffer(raw_bytes, dtype=np.int16)
    n_samples = len(samples)
    if n_samples == 0:
        return []

    float_samples = samples.astype(np.float32) / 32768.0
    window_samples = int(window_sec * target_sr)
    hop_samples = int(hop_sec * target_sr)

    segments: list[ExtractedSegment] = []
    pos = 0

    while pos < n_samples:
        end_pos = min(pos + window_samples, n_samples)
        chunk_float = float_samples[pos:end_pos]
        rms = float(np.sqrt(np.mean(chunk_float**2)))

        if rms > min_rms:
            chunk_int16 = samples[pos:end_pos]
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(target_sr)
                wf.writeframes(chunk_int16.tobytes())

            b64_audio = base64.b64encode(buf.getvalue()).decode("ascii")
            segments.append(
                ExtractedSegment(
                    kind=SegmentKind.AUDIO,
                    modality=Modality.AUDIO,
                    loc=round(pos / target_sr, 2),
                    loc_end=round(end_pos / target_sr, 2),
                    payload={"audio": b64_audio},
                )
            )

        if end_pos >= n_samples:
            break
        pos += hop_samples

    return segments


def extract_video(
    path: str,
    sample_interval_sec: float = 3.0,
    hash_threshold: int = 4,
) -> list[ExtractedSegment]:
    """Sample video frames at intervals, deduplicate shots via aHash, and extract soundtrack."""
    segments: list[ExtractedSegment] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        cmd = [
            "ffmpeg",
            "-loglevel",
            "quiet",
            "-i",
            path,
            "-vf",
            f"fps=1/{sample_interval_sec},scale='min(1024,iw)':-2",
            "-q:v",
            "2",
            str(Path(tmp_dir) / "frame_%05d.jpg"),
        ]
        subprocess.run(cmd, capture_output=True, check=False)

        frame_files = sorted(Path(tmp_dir).glob("frame_*.jpg"))
        prev_hash: int | None = None

        for idx, frame_file in enumerate(frame_files):
            timestamp = round(idx * sample_interval_sec, 2)
            with Image.open(frame_file) as img:
                h = average_hash(img)
                if prev_hash is not None and hamming_distance(h, prev_hash) <= hash_threshold:
                    continue

                prev_hash = h
                b64_frame = base64.b64encode(frame_file.read_bytes()).decode("ascii")
                segments.append(
                    ExtractedSegment(
                        kind=SegmentKind.FRAME,
                        modality=Modality.IMAGE,
                        loc=timestamp,
                        payload={"image": b64_frame},
                    )
                )

    segments.extend(extract_audio(path))
    return segments
