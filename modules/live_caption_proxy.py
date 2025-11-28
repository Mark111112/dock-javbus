import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException

from modules.translation.translator import get_translator

LOGGER = logging.getLogger("CaptionProxy")


def _load_configs() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load transcription/translation sections from config file."""
    try:
        with open("config/config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        return {}, {}
    return cfg.get("transcription", {}) or {}, cfg.get("translation", {}) or {}


def _build_fwhisper_ws_url(transcription_cfg: Dict[str, Any]) -> str:
    """Build downstream faster-whisper WS URL from config."""
    explicit = str(transcription_cfg.get("ws_url") or transcription_cfg.get("websocket_url") or "").strip()
    if explicit:
        return explicit

    base = str(
        transcription_cfg.get("api_base_url") or transcription_cfg.get("base_url") or transcription_cfg.get("api_url") or ""
    ).strip()
    if not base:
        return "ws://localhost:8001/ws/realtime"

    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc
    if not netloc:
        return "ws://localhost:8001/ws/realtime"

    return f"{scheme}://{netloc}/ws/realtime"


def handle_caption_proxy_ws(ws) -> None:
    """
    WebSocket proxy: accept PCM from front-end, forward to faster-whisper WS,
    then translate transcripts and send back {text, translated_text}.
    """
    transcription_cfg, translation_cfg = _load_configs()
    fwh_ws_url = _build_fwhisper_ws_url(transcription_cfg)
    translator = get_translator()

    send_lock = threading.Lock()
    downstream = {"conn": None}
    stop_event = threading.Event()
    translate_enabled = False

    def send_json(payload: Dict[str, Any]) -> None:
        try:
            with send_lock:
                ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            LOGGER.exception("Failed to send to client")

    def close_downstream() -> None:
        conn = downstream.get("conn")
        downstream["conn"] = None
        if conn:
            try:
                conn.send(json.dumps({"type": "stop"}))
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def recv_downstream_loop(conn) -> None:
        while not stop_event.is_set():
            try:
                msg = conn.recv()
            except WebSocketException as exc:
                LOGGER.warning("Downstream WS error: %s", exc)
                break
            except Exception as exc:
                LOGGER.warning("Downstream WS recv failed: %s", exc)
                break

            if msg is None:
                break

            if isinstance(msg, (bytes, bytearray)):
                continue

            try:
                data = json.loads(msg)
            except Exception:
                continue

            msg_type = data.get("type")
            if msg_type == "transcript":
                text = data.get("text") or ""
                translated = ""
                if translate_enabled and text:
                    try:
                        translated = translator.translate_sync(text) or ""
                    except Exception as exc:
                        LOGGER.warning("Translate failed: %s", exc)
                payload = {
                    "type": "transcript",
                    "text": text,
                    "is_final": bool(data.get("is_final", False)),
                }
                if translated:
                    payload["translated_text"] = translated
                send_json(payload)
                continue

            # Forward other downstream messages as-is (ack/error)
            send_json(data)

    try:
        recv_thread: Optional[threading.Thread] = None

        while True:
            message = ws.receive()
            if message is None:
                break

            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except Exception:
                    continue

                msg_type = data.get("type")

                if msg_type == "start":
                    translate_enabled = bool(data.get("translate", False))

                    if downstream.get("conn"):
                        close_downstream()

                    try:
                        conn = connect(fwh_ws_url, open_timeout=5)
                    except Exception as exc:
                        LOGGER.exception("Failed to connect downstream WS: %s", exc)
                        send_json({"type": "error", "message": f"connect downstream failed: {exc}"})
                        continue

                    downstream["conn"] = conn
                    stop_event.clear()
                    payload = dict(data)
                    payload.pop("translate", None)
                    payload.pop("targetLang", None)
                    try:
                        conn.send(json.dumps(payload))
                    except Exception as exc:
                        LOGGER.exception("Failed to send start downstream: %s", exc)
                        send_json({"type": "error", "message": f"downstream start failed: {exc}"})
                        close_downstream()
                        continue

                    recv_thread = threading.Thread(target=recv_downstream_loop, args=(conn,), daemon=True)
                    recv_thread.start()
                    send_json({"type": "started"})
                    continue

                if msg_type == "stop":
                    break

                continue

            if isinstance(message, (bytes, bytearray)):
                conn = downstream.get("conn")
                if not conn:
                    continue
                try:
                    conn.send(message)
                except Exception as exc:
                    LOGGER.warning("Failed to forward audio: %s", exc)
                    continue

    finally:
        stop_event.set()
        try:
            close_downstream()
        finally:
            try:
                if recv_thread:
                    recv_thread.join(timeout=1.0)
            except Exception:
                pass
