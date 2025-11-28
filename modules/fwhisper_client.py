from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass
from typing import Optional, Dict, Any

import logging
import requests


@dataclass
class FasterWhisperConfig:
    base_url: str = os.getenv("FWHISPER_BASE_URL", "http://localhost:8001")
    timeout: float = float(os.getenv("FWHISPER_TIMEOUT", "15"))
    model: Optional[str] = os.getenv("FWHISPER_MODEL")
    enabled: bool = True


DEFAULT_FWHISPER_CONFIG = FasterWhisperConfig()


def configure_fwhisper_from_dict(config_section: Optional[Dict[str, Any]]) -> None:
    global DEFAULT_FWHISPER_CONFIG

    cfg = FasterWhisperConfig()
    if not isinstance(config_section, dict):
        DEFAULT_FWHISPER_CONFIG = cfg
        return

    base_url = str(config_section.get("api_base_url") or config_section.get("base_url") or "").strip()
    if base_url:
        cfg.base_url = base_url

    model = str(config_section.get("model") or "").strip()
    if model:
        cfg.model = model

    timeout_value = config_section.get("timeout")
    if timeout_value is not None:
        try:
            cfg.timeout = float(timeout_value)
        except Exception:
            pass

    enabled = config_section.get("enabled")
    if isinstance(enabled, bool):
        cfg.enabled = enabled

    DEFAULT_FWHISPER_CONFIG = cfg


class FasterWhisperClient:
    """HTTP client for faster-whisper-service."""

    def __init__(self, config: Optional[FasterWhisperConfig] = None) -> None:
        self.config = config or DEFAULT_FWHISPER_CONFIG
        self.logger = logging.getLogger("Transcription.FWhisper")

    def transcribe_pcm(self, pcm_data: bytes, sample_rate: int, language: Optional[str] = None) -> str:
        if not pcm_data or not self.config.enabled:
            return ""
        wav_bytes = self._pcm_to_wav(pcm_data, sample_rate)
        return self.transcribe_wav(wav_bytes, language)

    def transcribe_wav(self, wav_bytes: bytes, language: Optional[str] = None) -> str:
        if not wav_bytes or not self.config.enabled:
            return ""

        urls = self._build_urls_with_fallback()
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data: Dict[str, Any] = {}
        if self.config.model:
            data["model"] = self.config.model
        if language:
            data["language"] = language

        resp = None
        for url in urls:
            try:
                resp = requests.post(url, files=files, data=data, timeout=self.config.timeout)
                resp.raise_for_status()
                break
            except requests.HTTPError as exc:
                # Try fallback when 404 (common when base_url already includes /v1)
                status = exc.response.status_code if exc.response is not None else None
                self.logger.error("FWhisper HTTP error (%s): %s", url, exc, exc_info=True)
                if status == 404:
                    continue
                return ""
            except Exception as exc:
                self.logger.error("FWhisper HTTP error (%s): %s", url, exc, exc_info=True)
                return ""

        if resp is None:
            return ""

        try:
            payload = resp.json()
        except Exception as exc:
            self.logger.error("FWhisper parse error: %s", exc, exc_info=True)
            return ""

        if isinstance(payload, dict):
            text = payload.get("text")
            if isinstance(text, str):
                return text
        self.logger.warning("FWhisper response missing text field: %r", payload)
        return ""

    def _build_urls_with_fallback(self) -> list:
        """
        Build primary + fallback URLs to tolerate config with/without /v1 suffix.
        Returns list ordered by preference.
        """
        base = (self.config.base_url or "").rstrip("/")
        urls = []

        # If base explicitly ends with /v1, strip it once for canonical form
        if base.endswith("/v1"):
            base_without_v1 = base[:-3].rstrip("/")
            urls.append(f"{base_without_v1}/v1/audio/transcriptions")
            urls.append(f"{base_without_v1}/audio/transcriptions")  # fallback without /v1
        else:
            urls.append(f"{base}/v1/audio/transcriptions")
            urls.append(f"{base}/audio/transcriptions")  # fallback when service not namespaced

        # Deduplicate while preserving order
        seen = set()
        unique_urls = []
        for u in urls:
            if u not in seen:
                unique_urls.append(u)
                seen.add(u)
        return unique_urls

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        return buffer.getvalue()
