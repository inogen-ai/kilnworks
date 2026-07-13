import shutil
import subprocess
import tempfile
from pathlib import Path

from kilnworks.core.errors import ProviderError

# Video containers whose audio track needs extracting before a Whisper-family
# transcriber can consume it. Anything else handed to `extract_audio` is assumed
# to already be audio and is passed through unchanged.
VIDEO_SUFFIXES = {".mp4", ".mov"}

# Generous but bounded: ffmpeg on a large video should still finish well within a
# typical job timeout; a hard cap keeps a corrupt/hostile file from hanging a worker.
FFMPEG_TIMEOUT_SECONDS = 300

FFMPEG_MISSING_MESSAGE = (
    "ffmpeg is required to ingest video files (.mp4/.mov) but was not found on PATH; "
    "install it (e.g. `apt-get install ffmpeg` on Debian/Ubuntu, `brew install ffmpeg` "
    "on macOS) or ingest an audio file (.mp3/.wav/.m4a) instead"
)


def extract_audio(media_bytes: bytes, suffix: str) -> bytes:
    """Return 16kHz mono WAV bytes suitable for a transcriber.

    For video suffixes (`VIDEO_SUFFIXES`), the audio track is pulled out via a
    subprocess call to the system `ffmpeg` binary (a documented system dependency;
    see docs/limitations.md). For anything else, `media_bytes` is returned
    unchanged — already-audio formats (mp3/wav/m4a) go straight to the transcriber.

    Raises `ProviderError` if `ffmpeg` isn't on PATH, exits non-zero, or times out.
    """
    if suffix.lower() not in VIDEO_SUFFIXES:
        return media_bytes
    if shutil.which("ffmpeg") is None:
        raise ProviderError("ffmpeg", FFMPEG_MISSING_MESSAGE)

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = Path(tmpdir) / f"input{suffix.lower()}"
        dst_path = Path(tmpdir) / "output.wav"
        src_path.write_bytes(media_bytes)
        cmd = [
            "ffmpeg",
            "-y",  # overwrite dst_path without prompting
            "-i", str(src_path),
            "-vn",  # drop the video stream entirely
            "-ac", "1",  # mono
            "-ar", "16000",  # 16kHz, the sample rate Whisper-family models expect
            str(dst_path),
        ]
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=FFMPEG_TIMEOUT_SECONDS,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            raise ProviderError(
                "ffmpeg", f"audio extraction failed (exit {exc.returncode}): {stderr[-500:]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                "ffmpeg", f"audio extraction timed out after {FFMPEG_TIMEOUT_SECONDS}s"
            ) from exc
        if not dst_path.exists():
            raise ProviderError("ffmpeg", "audio extraction produced no output file")
        return dst_path.read_bytes()
