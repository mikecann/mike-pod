#!/usr/bin/env python3
"""Publish a built Mike Pod bundle to Cloudflare R2, with the feed last."""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = BASE_DIR / "dist" / "podcast"
DEFAULT_BUCKET = "mike-pod-public"
DEFAULT_PUBLIC_BASE_URL = "https://podcast.mikecann.app"


class PublishError(RuntimeError):
    """A concise publishing failure."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_type(path: Path) -> str:
    overrides = {
        ".xml": "application/rss+xml; charset=utf-8",
        ".mp3": "audio/mpeg",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    return overrides.get(
        path.suffix.lower(),
        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def cache_control(key: str) -> str:
    if key == "feed.xml":
        return "public, max-age=300, must-revalidate"
    if key.startswith("episodes/"):
        return "public, max-age=31536000, immutable"
    return "public, max-age=86400"


def wrangler(arguments: list[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["npx", "--yes", "wrangler@latest", *arguments],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result


def remote_copy(bucket: str, key: str, destination: Path) -> bool:
    result = wrangler(
        [
            "r2",
            "object",
            "get",
            f"{bucket}/{key}",
            "--file",
            str(destination),
            "--remote",
        ]
    )
    if result.returncode == 0:
        return True
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "not found" in output or "does not exist" in output or "404" in output:
        return False
    raise PublishError(
        f"Could not inspect existing R2 object {key}: "
        f"{(result.stderr or result.stdout).strip()[-1200:]}"
    )


def public_copy(url: str, destination: Path) -> bool:
    request = Request(url, headers={"User-Agent": "MikePod/2.0"})
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
        return True
    except HTTPError as exc:
        if exc.code == 404:
            destination.unlink(missing_ok=True)
            return False
        raise PublishError(f"Public verification returned HTTP {exc.code}: {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PublishError(f"Could not verify public object {url}: {exc}") from exc


def upload(bucket: str, key: str, source: Path) -> None:
    result = wrangler(
        [
            "r2",
            "object",
            "put",
            f"{bucket}/{key}",
            "--file",
            str(source),
            "--content-type",
            content_type(source),
            "--cache-control",
            cache_control(key),
            "--storage-class",
            "Standard",
            "--remote",
            "--force",
        ]
    )
    if result.returncode != 0:
        raise PublishError(
            f"Upload failed for {key}: "
            f"{(result.stderr or result.stdout).strip()[-1200:]}"
        )


def upload_and_verify(
    bucket: str,
    key: str,
    source: Path,
    temporary_dir: Path,
    public_base_url: str,
) -> None:
    existing = temporary_dir / ("existing-" + source.name)
    public_url = f"{public_base_url}/{key}"
    if key.startswith("episodes/") and public_copy(public_url, existing):
        if sha256(existing) != sha256(source):
            raise PublishError(
                f"Refusing to replace immutable episode object {key} with "
                "different bytes. Use a new filename and GUID."
            )
        print(f"Already present and identical: {key}")
        return

    upload(bucket, key, source)
    verified = temporary_dir / ("verified-" + source.name)
    if key.startswith("episodes/"):
        exists = public_copy(public_url, verified)
    else:
        exists = remote_copy(bucket, key, verified)
    if not exists:
        raise PublishError(f"R2 did not return newly uploaded object {key}")
    if sha256(verified) != sha256(source):
        raise PublishError(f"R2 verification checksum failed for {key}")
    print(f"Uploaded and verified: {key} ({source.stat().st_size} bytes)")


def public_head(url: str) -> tuple[int, dict[str, str]]:
    request = Request(url, method="HEAD", headers={"User-Agent": "MikePod/2.0"})
    with urlopen(request, timeout=30) as response:
        return response.status, {key.lower(): value for key, value in response.headers.items()}


def verify_public(source_dir: Path, public_base_url: str, attempts: int = 12) -> None:
    expected = {
        path.relative_to(source_dir).as_posix(): path.stat().st_size
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    last_error = "no attempt made"
    for attempt in range(1, attempts + 1):
        try:
            for key, expected_size in expected.items():
                status, headers = public_head(f"{public_base_url}/{key}")
                if status != 200:
                    raise PublishError(f"{key} returned HTTP {status}")
                actual_size = int(headers.get("content-length", "-1"))
                if actual_size != expected_size:
                    raise PublishError(
                        f"{key} size mismatch: public={actual_size}, local={expected_size}"
                    )

            audio_key = next(key for key in expected if key.endswith(".mp3"))
            request = Request(
                f"{public_base_url}/{audio_key}",
                headers={"Range": "bytes=0-1023", "User-Agent": "MikePod/2.0"},
            )
            with urlopen(request, timeout=30) as response:
                if response.status != 206:
                    raise PublishError(
                        f"Audio byte-range request returned HTTP {response.status}"
                    )
                if len(response.read()) != 1024:
                    raise PublishError("Audio byte-range response had the wrong length")
                if not response.headers.get("Content-Range", "").startswith("bytes 0-1023/"):
                    raise PublishError("Audio response did not include a valid Content-Range")
            print(f"Public origin verified: {public_base_url}")
            return
        except (PublishError, HTTPError, URLError, TimeoutError, ValueError) as exc:
            last_error = str(exc)
            if attempt < attempts:
                time.sleep(10)
    raise PublishError(
        f"Public verification did not pass after {attempts} attempts: {last_error}"
    )


def publish(
    source_dir: Path,
    bucket: str,
    public_base_url: str,
    *,
    verify: bool,
) -> None:
    feed = source_dir / "feed.xml"
    if not feed.exists():
        raise PublishError(f"Built feed does not exist: {feed}")
    assets = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path != feed
    )
    if not assets:
        raise PublishError(f"No public assets found under {source_dir}")

    with tempfile.TemporaryDirectory(prefix="mike-pod-publish-") as temporary:
        temporary_dir = Path(temporary)
        # The feed is deliberately last. A podcast client can never discover an
        # enclosure until the exact media and artwork bytes are confirmed in R2.
        for path in assets:
            key = path.relative_to(source_dir).as_posix()
            upload_and_verify(
                bucket,
                key,
                path,
                temporary_dir,
                public_base_url,
            )
        upload_and_verify(
            bucket,
            "feed.xml",
            feed,
            temporary_dir,
            public_base_url,
        )

    if verify:
        verify_public(source_dir, public_base_url)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--skip-public-verification", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        publish(
            args.source_dir.resolve(),
            args.bucket,
            args.public_base_url.rstrip("/"),
            verify=not args.skip_public_verification,
        )
        return 0
    except PublishError as exc:
        print(f"Publishing failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
