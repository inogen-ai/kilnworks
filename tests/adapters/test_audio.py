import subprocess

import pytest

from kilnworks.adapters.media import audio as audio_module
from kilnworks.adapters.media.audio import extract_audio
from kilnworks.core.errors import ProviderError


def test_audio_suffixes_pass_through_unchanged():
    raw = b"already-audio-bytes"
    assert extract_audio(raw, ".wav") == raw
    assert extract_audio(raw, ".mp3") == raw
    assert extract_audio(raw, ".m4a") == raw


def test_video_runs_ffmpeg_with_expected_args(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    captured = {}

    def fake_run(cmd, capture_output, timeout, check):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        dst_path = cmd[-1]
        with open(dst_path, "wb") as handle:
            handle.write(b"RIFF-fake-wav-bytes")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(audio_module.subprocess, "run", fake_run)

    result = extract_audio(b"fake mp4 bytes", ".mp4")

    assert result == b"RIFF-fake-wav-bytes"
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-vn" in cmd  # drop video stream
    assert cmd[-1].endswith(".wav")


def test_mov_suffix_also_runs_ffmpeg(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, capture_output, timeout, check):
        with open(cmd[-1], "wb") as handle:
            handle.write(b"wav-out")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(audio_module.subprocess, "run", fake_run)
    assert extract_audio(b"fake mov bytes", ".mov") == b"wav-out"


def test_ffmpeg_absent_raises_clear_provider_error(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda name: None)
    with pytest.raises(ProviderError, match="ffmpeg"):
        extract_audio(b"fake mp4 bytes", ".mp4")


def test_ffmpeg_nonzero_exit_raises_provider_error(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, capture_output, timeout, check):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"invalid data found")

    monkeypatch.setattr(audio_module.subprocess, "run", fake_run)
    with pytest.raises(ProviderError, match="ffmpeg"):
        extract_audio(b"corrupt", ".mp4")


def test_ffmpeg_timeout_raises_provider_error(monkeypatch):
    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, capture_output, timeout, check):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(audio_module.subprocess, "run", fake_run)
    with pytest.raises(ProviderError, match="timed out"):
        extract_audio(b"slow video", ".mp4")
