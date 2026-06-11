#!/usr/bin/env python3
"""
Run a YouTube video through the pipeline and produce a human-readable quality report.
Shows every 60 seconds: timestamp | Whisper transcript | Tesseract OCR | moondream vision

Usage: experiment.py <youtube_url>
"""
import sys
import os
import re
import json
import subprocess
import time
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))

from brain_config import BRAIN_DATA_DIR, WHISPER_MODEL, OLLAMA_VISION_MODEL

import ollama

YTDLP = "/opt/homebrew/bin/yt-dlp"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
TESSERACT = "/opt/homebrew/bin/tesseract"


def get_video_id(youtube_url: str) -> str:
    patterns = [r'[?&]v=([^&]+)', r'youtu\.be/([^?]+)', r'/shorts/([^?]+)']
    for p in patterns:
        m = re.search(p, youtube_url)
        if m:
            return m.group(1)
    return youtube_url.split("/")[-1].split("?")[0]


def download_video(youtube_url: str, work_dir: Path) -> tuple:
    output_template = str(work_dir / "video.%(ext)s")
    cmd = [
        YTDLP,
        "--write-info-json",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", output_template,
        "--merge-output-format", "mp4",
        youtube_url
    ]
    print("  Downloading video...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  yt-dlp error: {result.stderr[:500]}")
        return None, {}
    
    video_path = None
    for ext in ["mp4", "mkv", "webm"]:
        p = work_dir / f"video.{ext}"
        if p.exists():
            video_path = p
            break
    
    metadata = {}
    info_files = list(work_dir.glob("*.info.json"))
    if info_files:
        try:
            with open(info_files[0]) as f:
                info = json.load(f)
            metadata = {
                "title": info.get("title", ""),
                "channel": info.get("channel", info.get("uploader", "")),
                "upload_date": info.get("upload_date", ""),
                "duration": int(info.get("duration", 0)),
            }
        except Exception:
            pass
    
    return video_path, metadata


def transcribe(video_path: Path) -> list:
    print(f"  Transcribing with Whisper ({WHISPER_MODEL})...", flush=True)
    import whisper
    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(str(video_path), verbose=False)
    return result.get("segments", [])


def extract_frames(video_path: Path, frames_dir: Path) -> int:
    frames_dir.mkdir(exist_ok=True)
    cmd = [
        FFMPEG, "-i", str(video_path),
        "-vf", "fps=1",
        "-q:v", "5",
        str(frames_dir / "%05d.jpg"),
        "-y", "-loglevel", "error"
    ]
    print("  Extracting frames...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    frames = list(frames_dir.glob("*.jpg"))
    return len(frames)


def ocr_frame(frame_path: Path) -> str:
    try:
        result = subprocess.run(
            [TESSERACT, str(frame_path), "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=10
        )
        text = result.stdout.strip()
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""


def vision_describe(frame_path: Path) -> str:
    try:
        with open(frame_path, "rb") as f:
            image_data = f.read()
        resp = ollama.chat(
            model=OLLAMA_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": "Briefly describe what is shown in this screen. Focus on key text, UI elements, or visual content. Be concise (1-2 sentences).",
                "images": [image_data]
            }]
        )
        return resp["message"]["content"].strip()
    except Exception as e:
        return f"[vision error: {e}]"


def run_experiment(youtube_url: str):
    video_id = get_video_id(youtube_url)
    work_dir = Path(BRAIN_DATA_DIR) / "videos" / video_id
    work_dir.mkdir(parents=True, exist_ok=True)
    
    exp_dir = Path(BRAIN_DATA_DIR) / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    report_path = exp_dir / f"{video_id}_report.txt"
    
    print(f"\n=== EXPERIMENT: {video_id} ===")
    print(f"URL: {youtube_url}")
    
    # Download
    video_path, meta = download_video(youtube_url, work_dir)
    if not video_path:
        print("ERROR: Download failed")
        return
    
    title = meta.get("title", "Unknown")
    channel = meta.get("channel", "Unknown")
    duration = meta.get("duration", 0)
    
    print(f"  Title: {title}")
    print(f"  Channel: {channel}")
    print(f"  Duration: {duration}s ({duration//60}m {duration%60}s)")
    
    # Transcribe
    segments = transcribe(video_path)
    print(f"  Transcript: {len(segments)} segments")
    
    # Frames
    frames_dir = work_dir / "frames"
    n_frames = extract_frames(video_path, frames_dir)
    print(f"  Frames: {n_frames}")
    
    # Build transcript lookup
    transcript_by_second = {}
    for seg in segments:
        start = int(seg.get("start", 0))
        end = int(seg.get("end", start + 1))
        text = seg.get("text", "").strip()
        for s in range(start, end + 1):
            transcript_by_second[s] = transcript_by_second.get(s, "") + " " + text
    
    # Generate report
    lines = []
    lines.append(f"EXPERIMENT REPORT: {video_id}")
    lines.append(f"URL: {youtube_url}")
    lines.append(f"Title: {title}")
    lines.append(f"Channel: {channel}")
    lines.append(f"Duration: {duration}s ({duration//60}m {duration%60}s)")
    lines.append(f"Transcript segments: {len(segments)}")
    lines.append(f"Frames extracted: {n_frames}")
    lines.append("=" * 80)
    lines.append("")
    
    max_second = max(duration, n_frames)
    windows_processed = 0
    
    for window_start in range(0, max_second + 60, 60):
        window_end = window_start + 60
        timestamp = f"{window_start//60:02d}:{window_start%60:02d} - {window_end//60:02d}:{window_end%60:02d}"
        
        # Transcript
        transcript_parts = []
        for s in range(window_start, window_end):
            t = transcript_by_second.get(s, "").strip()
            if t and t not in transcript_parts:
                transcript_parts.append(t)
        transcript_text = " ".join(transcript_parts).strip()
        
        if not transcript_text:
            continue
        
        # OCR middle frame of window
        mid_second = window_start + 30
        ocr_text = ""
        frame_path = frames_dir / f"{mid_second+1:05d}.jpg"
        if frame_path.exists():
            ocr_text = ocr_frame(frame_path)
        
        # Vision for middle frame
        vision_text = ""
        if frame_path.exists():
            vision_text = vision_describe(frame_path)
        
        lines.append(f"[{timestamp}]")
        lines.append(f"WHISPER:   {transcript_text[:300]}")
        lines.append(f"OCR:       {ocr_text[:200] if ocr_text else '(none)'}")
        lines.append(f"VISION:    {vision_text[:300] if vision_text else '(none)'}")
        lines.append("")
        
        windows_processed += 1
        print(f"  Window {timestamp}: {len(transcript_text)} chars transcript, {len(ocr_text)} chars OCR", flush=True)
    
    lines.append(f"=== Total windows: {windows_processed} ===")
    
    report = "\n".join(lines)
    
    # Save report
    with open(report_path, "w") as f:
        f.write(report)
    
    print(f"\n  Report saved to: {report_path}")
    print("\n" + "=" * 80)
    print(report[:3000])  # Print first 3000 chars to stdout
    if len(report) > 3000:
        print(f"... (truncated, full report in {report_path})")
    
    return report_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: experiment.py <youtube_url>")
        sys.exit(1)
    
    url = sys.argv[1]
    run_experiment(url)
