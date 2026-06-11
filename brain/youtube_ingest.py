#!/usr/bin/env python3
"""
YouTube video ingest pipeline:
1. Download audio+video with yt-dlp
2. Transcribe audio with Whisper (medium)
3. Extract frames at 1 per 30 seconds (scene-representative sampling)
4. OCR frames with Tesseract
5. Merge into 60-second windows, chunk, upsert to ChromaDB
"""
import sys
import os
import re
import json
import subprocess
import shutil
import time
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))

from brain_config import BRAIN_DATA_DIR, WHISPER_MODEL
from brain_embed import upsert_doc

YTDLP = "/opt/homebrew/bin/yt-dlp"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
TESSERACT = "/opt/homebrew/bin/tesseract"


def get_video_id(youtube_url: str) -> str:
    """Extract video ID from YouTube URL."""
    patterns = [
        r'[?&]v=([^&]+)',
        r'youtu\.be/([^?]+)',
        r'/embed/([^?]+)',
        r'/shorts/([^?]+)',
    ]
    for p in patterns:
        m = re.search(p, youtube_url)
        if m:
            return m.group(1)
    return youtube_url.split("/")[-1].split("?")[0]


def download_video(youtube_url: str, work_dir: Path) -> tuple[Path | None, dict]:
    """Download video+audio. Returns (video_path, metadata_dict)."""
    output_template = str(work_dir / "video.%(ext)s")
    info_path = work_dir / "info.json"
    
    # Download with metadata
    cmd = [
        YTDLP,
        "--write-info-json",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", output_template,
        "--merge-output-format", "mp4",
        youtube_url
    ]
    print(f"  Downloading video...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  yt-dlp error: {result.stderr[:500]}")
        return None, {}
    
    # Find the downloaded video file
    video_path = None
    for ext in ["mp4", "mkv", "webm", "mov"]:
        p = work_dir / f"video.{ext}"
        if p.exists():
            video_path = p
            break
    
    # Load info
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
                "description": (info.get("description") or "")[:500],
                "duration": str(info.get("duration", 0)),
            }
        except Exception as e:
            print(f"  Warning: could not parse info.json: {e}")
    
    return video_path, metadata


def transcribe_audio(video_path: Path) -> list:
    """Transcribe audio with Whisper. Returns list of {start, end, text} segments."""
    print(f"  Transcribing with Whisper ({WHISPER_MODEL})...")
    import whisper
    model = whisper.load_model(WHISPER_MODEL)
    result = model.transcribe(str(video_path), verbose=False)
    return result.get("segments", [])


FRAME_INTERVAL_SECONDS = 30  # 1 frame every N seconds

def extract_frames(video_path: Path, frames_dir: Path) -> int:
    """Extract frames at 1 per FRAME_INTERVAL_SECONDS. Returns number of frames."""
    frames_dir.mkdir(exist_ok=True)
    cmd = [
        FFMPEG, "-i", str(video_path),
        "-vf", f"fps=1/{FRAME_INTERVAL_SECONDS}",
        "-q:v", "5",
        str(frames_dir / "%05d.jpg"),
        "-y", "-loglevel", "error"
    ]
    print(f"  Extracting frames at 1/{FRAME_INTERVAL_SECONDS}fps...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"  ffmpeg error: {result.stderr[:300]}")
        return 0
    frames = list(frames_dir.glob("*.jpg"))
    print(f"  Extracted {len(frames)} frames")
    return len(frames)


def ocr_frame(frame_path: Path) -> str:
    """Run Tesseract OCR on a frame."""
    try:
        result = subprocess.run(
            [TESSERACT, str(frame_path), "stdout", "--psm", "6"],
            capture_output=True, text=True, timeout=10
        )
        text = result.stdout.strip()
        # Clean up noise
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""



def process_video(youtube_url: str, video_id: str = None, title: str = "", channel: str = "") -> dict:
    """Full pipeline: download → transcribe → frames → OCR → vision → chunk → upsert."""
    if not video_id:
        video_id = get_video_id(youtube_url)
    
    work_dir = Path(BRAIN_DATA_DIR) / "videos" / video_id
    work_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n--- Processing video {video_id} ---")
    print(f"URL: {youtube_url}")
    
    # 1. Download
    video_path, dl_metadata = download_video(youtube_url, work_dir)
    if not video_path:
        return {"error": "download failed", "video_id": video_id}
    
    if not title and dl_metadata.get("title"):
        title = dl_metadata["title"]
    if not channel and dl_metadata.get("channel"):
        channel = dl_metadata["channel"]
    
    print(f"  Title: {title}")
    print(f"  Channel: {channel}")
    
    # 2. Transcribe
    segments = transcribe_audio(video_path)
    print(f"  Got {len(segments)} transcript segments")
    
    # 3. Extract frames
    frames_dir = work_dir / "frames"
    n_frames = extract_frames(video_path, frames_dir)
    
    # 4. OCR all frames
    print(f"  Running OCR on {n_frames} frames...")
    frame_data = {}  # second -> {ocr}
    frame_files = sorted(frames_dir.glob("*.jpg"))

    for i, frame_path in enumerate(frame_files):
        second = i * FRAME_INTERVAL_SECONDS  # frame N is at N*interval seconds
        ocr_text = ocr_frame(frame_path)
        frame_data[second] = {"ocr": ocr_text}

    # 5. Merge into 60-second windows
    print(f"  Merging into 60-second windows...")
    
    # Build transcript lookup: second -> text
    transcript_by_second = {}
    for seg in segments:
        start = int(seg.get("start", 0))
        end = int(seg.get("end", start + 1))
        text = seg.get("text", "").strip()
        for s in range(start, end + 1):
            transcript_by_second[s] = transcript_by_second.get(s, "") + " " + text
    
    # Create 60-second windows
    max_second = max(
        max(transcript_by_second.keys()) if transcript_by_second else 0,
        max(frame_data.keys()) if frame_data else 0
    )
    
    chunks_added = 0
    for window_start in range(0, max_second + 60, 60):
        window_end = window_start + 60
        timestamp = f"{window_start//60:02d}:{window_start%60:02d}"
        
        # Collect transcript for this window
        transcript_parts = []
        for s in range(window_start, window_end):
            t = transcript_by_second.get(s, "").strip()
            if t:
                transcript_parts.append(t)
        transcript_text = " ".join(transcript_parts).strip()
        
        # Collect OCR for this window (deduplicated)
        ocr_parts = set()
        for s in range(window_start, window_end):
            ocr = frame_data.get(s, {}).get("ocr", "").strip()
            if ocr and len(ocr) > 5:
                ocr_parts.add(ocr[:200])
        ocr_text = " | ".join(sorted(ocr_parts)[:5])
        
        # Build combined chunk text
        parts = []
        parts.append(f"Video: {title}")
        parts.append(f"Channel: {channel}")
        parts.append(f"Timestamp: {timestamp}")
        if transcript_text:
            parts.append(f"Transcript: {transcript_text}")
        if ocr_text:
            parts.append(f"Screen text: {ocr_text}")

        chunk_text = "\n".join(parts)

        # Skip empty windows
        if not transcript_text and not ocr_text:
            continue
        
        doc_id = f"youtube_{video_id}_{window_start}"
        metadata = {
            "source": "youtube",
            "video_id": video_id,
            "url": youtube_url,
            "title": title[:200] if title else "",
            "channel": channel[:100] if channel else "",
            "timestamp": timestamp,
            "window_start": str(window_start),
            "upload_date": dl_metadata.get("upload_date", ""),
        }
        
        if upsert_doc(doc_id, chunk_text, metadata):
            chunks_added += 1
    
    print(f"  Added {chunks_added} chunks to ChromaDB")
    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "transcript_segments": len(segments),
        "frames": n_frames,
        "chunks_added": chunks_added,
    }


def ingest_url_list(urls: list):
    """Ingest a list of YouTube URLs."""
    results = []
    for url in urls:
        try:
            result = process_video(url)
            results.append(result)
        except Exception as e:
            print(f"Error processing {url}: {e}")
            results.append({"url": url, "error": str(e)})
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: youtube_ingest.py <youtube_url> [url2 ...]")
        sys.exit(1)
    
    urls = sys.argv[1:]
    results = ingest_url_list(urls)
    print("\n=== YouTube Ingest Summary ===")
    for r in results:
        if "error" in r:
            print(f"  ERROR: {r}")
        else:
            print(f"  {r['title'][:60]}: {r['chunks_added']} chunks")
