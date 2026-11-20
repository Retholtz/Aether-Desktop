"""
Aether Desktop - Speech-to-Text (STT) Service & Dispatcher
Prevents non-model endpoints (e.g. WebSocket or fake transcribe IDs) from causing 404s,
and routes one-shot audio transcription to a valid multimodal Flash model.
"""

import os
import asyncio
import collections
import logging
from typing import Optional, Any, Callable, Deque, List
from google import genai
from google.genai import types
from core.config_manager import load_config, unprotect_secret

logger = logging.getLogger("Aether.STTService")

_active_engine: Optional[Any] = None


def get_active_engine() -> Optional[Any]:
    """Returns the globally active Aether engine instance if available."""
    global _active_engine
    if _active_engine is not None:
        return _active_engine
    try:
        import main
        if getattr(main, "active_engine", None) is not None:
            return main.active_engine
    except Exception:
        pass
    return None


def set_active_engine(engine: Any) -> None:
    """Sets the globally active Aether engine instance."""
    global _active_engine
    _active_engine = engine


def get_genai_client(api_key: Optional[str] = None) -> genai.Client:
    """Returns an authenticated Google GenAI client instance."""
    engine = get_active_engine()
    if engine is not None:
        client = getattr(engine, "genai_client", None) or getattr(engine, "client", None)
        if client is not None:
            return client

    cfg = load_config()
    key = api_key
    if not key:
        api_cfg = cfg.get("api", {})
        enc_key = api_cfg.get("api_key_encrypted", "")
        if enc_key:
            try:
                key = unprotect_secret(enc_key)
            except Exception:
                pass
        if not key:
            key = api_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY", "")

    return genai.Client(api_key=key)


def resolve_stt_model_endpoint(stt_endpoint: Optional[str] = None, fallback: Optional[str] = None) -> str:
    """
    Sanitizes STT endpoint and resolves fake / WebSocket IDs down to a valid
    multimodal Gemini Flash model suitable for client.models.generate_content.
    """
    cfg = load_config()
    endpoint = stt_endpoint or cfg.get("stt_endpoint") or cfg.get("stt_model_endpoint") or "primary_flash"

    from core.model_discovery import resolve_model_profile
    if endpoint in (
        "gemini_live_audio",
        "gemini_live",
        "gemini-3.5-transcribe-live",
        "gemini-3.8-transcribe",
        "gemini-3.8-transcribe-live",
        "primary_flash_stt",
        "primary_flash",
    ):
        if fallback:
            return resolve_model_profile(fallback, default_model="gemini-3.5-transcribe")[0]
        raw_ep = cfg.get("primary_model_endpoint") or cfg.get("tier1_fast_model") or "gemini-3.5-transcribe"
        base_ep = resolve_model_profile(raw_ep, default_model="gemini-3.5-transcribe")[0]
        if any(k in base_ep.lower() for k in ("hydrogen", "neon", "argon", "carbon")):
            return "gemini-3.5-transcribe"
        return base_ep

    return resolve_model_profile(endpoint or fallback or "gemini-3.5-transcribe", default_model="gemini-3.5-transcribe")[0]


def transcribe_audio_buffer(audio_bytes: bytes, mime_type: str = "audio/wav", stt_endpoint: Optional[str] = None) -> str:
    """
    Transcribes raw audio bytes into text.
    Routes WebSocket streams to active live sessions, or resolves to a valid
    multimodal Gemini Flash / Transcribe model to avoid 404 NOT_FOUND errors.
    """
    from core.model_discovery import resolve_model_profile
    cfg = load_config()
    endpoint = stt_endpoint or cfg.get("stt_endpoint", "primary_flash")

    # 1. If configured for Live WebSocket streaming, route to the live connection
    if endpoint in ("gemini_live_audio", "gemini_live"):
        # Live session already handles audio natively over WebSocket;
        # if called standalone, delegate to the active live session or fall back to fast STT
        engine = get_active_engine()
        if engine and hasattr(engine, "send_live_audio_buffer"):
            res = engine.send_live_audio_buffer(audio_bytes)
            if res:
                return res
        endpoint = resolve_stt_model_endpoint(endpoint)

    # 2. Sanitize model endpoint: ensure we don't pass fake or non-existent endpoints
    if endpoint in (
        "gemini-3.5-transcribe-live",
        "gemini-3.8-transcribe",
        "gemini-3.8-transcribe-live",
        "gemini_live_audio",
        "primary_flash_stt",
        "primary_flash",
    ):
        endpoint = resolve_stt_model_endpoint(endpoint)

    endpoint = resolve_model_profile(endpoint, default_model="gemini-3.5-transcribe")[0]

    # 3. Call generate_content with audio data
    client = get_genai_client()
    try:
        gen_kwargs = {"temperature": 0.0}
        if "transcribe" not in endpoint.lower():
            gen_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        response = client.models.generate_content(
            model=endpoint,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": audio_bytes}},
                        {"text": "Transcribe the spoken audio verbatim. Output only the transcribed text with no extra commentary."}
                    ]
                }
            ],
            config=types.GenerateContentConfig(**gen_kwargs)
        )
        return response.text.strip() if response and response.text else ""
    except Exception as e:
        print(f"[ERROR] [STT] Transcription failed on model '{endpoint}': {e}")
        logger.error(f"[ERROR] [STT] Transcription failed on model '{endpoint}': {e}")
        raise


class _LiveSTTTurnBuffer:
    """Isolated per-turn transcript & completion state so Turn 2 audio never races Turn 1 completion."""

    def __init__(self) -> None:
        self.final_parts: List[str] = []
        self.interim_text: str = ""
        self.has_streamed_audio: bool = False
        self.done_event = asyncio.Event()

    def get_text(self) -> Optional[str]:
        text = " ".join(self.final_parts).strip() or self.interim_text.strip()
        return text or None


class ModularLiveSTTClient:
    """
    Persistent bidirectional WebSocket STT streamer for Modular Pipeline using
    `gemini-3.5-transcribe-live`. Streams 16kHz PCM frames in real time as the
    user speaks so transcription completes in ~230ms after speech end instead
    of waiting for a full WAV upload over REST.
    """

    def __init__(self, client: genai.Client, model: str = "gemini-3.5-transcribe-live"):
        self.client = client
        self.model = model
        self._ctx: Optional[Any] = None
        self._session: Optional[Any] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()

        self._active_turn = _LiveSTTTurnBuffer()
        self._pending_recv_turns: Deque[_LiveSTTTurnBuffer] = collections.deque()
        self._pending_finish_turns: Deque[_LiveSTTTurnBuffer] = collections.deque()
        self._end_sent_event = asyncio.Event()
        self._closed: bool = False

    @property
    def _final_parts(self) -> List[str]:
        return self._active_turn.final_parts

    @property
    def _interim_text(self) -> str:
        return self._active_turn.interim_text

    @_interim_text.setter
    def _interim_text(self, value: str) -> None:
        self._active_turn.interim_text = value

    @property
    def _has_streamed_audio(self) -> bool:
        return self._active_turn.has_streamed_audio

    @_has_streamed_audio.setter
    def _has_streamed_audio(self, value: bool) -> None:
        self._active_turn.has_streamed_audio = value

    @property
    def _turn_done_event(self) -> asyncio.Event:
        return self._active_turn.done_event

    @property
    def is_connected(self) -> bool:
        return self._session is not None and not self._closed

    def _reset_turn_buffers(self) -> None:
        self._active_turn = _LiveSTTTurnBuffer()
        self._pending_recv_turns.clear()
        self._pending_finish_turns.clear()
        self._end_sent_event.clear()

    def _signal_all_pending_done(self) -> None:
        self._active_turn.done_event.set()
        for t in list(self._pending_recv_turns):
            t.done_event.set()
        for t in list(self._pending_finish_turns):
            t.done_event.set()

    async def ensure_connected(self) -> bool:
        """Connects (or reuses) the persistent gemini-3.5-transcribe-live WebSocket."""
        if self._closed:
            return False
        if self._session is not None:
            return True
        async with self._connect_lock:
            if self._session is not None:
                return True
            if self._closed:
                return False
            try:
                ctx = self.client.aio.live.connect(model=self.model)
                session = await ctx.__aenter__()
                self._ctx = ctx
                self._session = session
                self._reset_turn_buffers()
                self._recv_task = asyncio.create_task(self._receive_loop(session, ctx))
                logger.info(f"[STT LIVE] Connected persistent WebSocket session ({self.model})")
                return True
            except Exception as err:
                logger.warning(f"[STT LIVE] Connect failed ({self.model}): {err}")
                await self._drop_connection()
                return False

    async def _drop_connection(self) -> None:
        recv = self._recv_task
        self._recv_task = None
        if recv and not recv.done():
            recv.cancel()
        ctx = self._ctx
        self._ctx = None
        self._session = None
        self._signal_all_pending_done()
        self._end_sent_event.set()
        if ctx is not None:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        await self._drop_connection()

    async def _receive_loop(self, session: Any, ctx: Any = None) -> None:
        try:
            async for resp in session.receive():
                sc = getattr(resp, "server_content", None)
                if not sc:
                    continue
                target_turn = self._pending_recv_turns[0] if self._pending_recv_turns else self._active_turn
                interim = getattr(sc, "interim_input_transcription", None)
                if interim and getattr(interim, "text", None):
                    target_turn.interim_text = str(interim.text).strip()
                final = getattr(sc, "input_transcription", None)
                if final and getattr(final, "text", None):
                    part_txt = str(final.text).strip()
                    if part_txt:
                        target_turn.final_parts.append(part_txt)
                if getattr(sc, "generation_complete", False):
                    if self._pending_recv_turns:
                        completed = self._pending_recv_turns.popleft()
                        completed.done_event.set()
                    else:
                        target_turn.done_event.set()
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.debug(f"[STT LIVE] Receive loop ended: {err}")
        finally:
            ctx_to_close = None
            if self._session is session:
                self._session = None
                if self._ctx is not None:
                    ctx_to_close = self._ctx
                    self._ctx = None
                self._signal_all_pending_done()
                self._end_sent_event.set()
            elif ctx is not None and self._ctx is ctx:
                ctx_to_close = ctx
                self._ctx = None
            if ctx_to_close is not None:
                try:
                    await ctx_to_close.__aexit__(None, None, None)
                except Exception:
                    pass

    async def send_pcm(self, pcm_bytes: bytes) -> bool:
        """Streams a raw 16kHz 16-bit PCM frame into the active live STT session."""
        if not pcm_bytes or self._closed:
            return False
        if not await self.ensure_connected():
            return False
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
            )
            self._active_turn.has_streamed_audio = True
            self._end_sent_event.clear()
            return True
        except Exception as err:
            logger.debug(f"[STT LIVE] send_realtime_input error: {err}")
            await self._drop_connection()
            return False

    async def signal_end_of_turn(self, enqueue_for_finish: bool = True) -> Optional[_LiveSTTTurnBuffer]:
        closed_turn: Optional[_LiveSTTTurnBuffer] = None
        if self._session and self._active_turn.has_streamed_audio:
            closed_turn = self._active_turn
            self._active_turn = _LiveSTTTurnBuffer()
            self._pending_recv_turns.append(closed_turn)
            if enqueue_for_finish:
                self._pending_finish_turns.append(closed_turn)
            try:
                await self._session.send_realtime_input(audio_stream_end=True)
            except Exception as err:
                logger.debug(f"[STT LIVE] audio_stream_end error: {err}")
                await self._drop_connection()
        self._end_sent_event.set()
        return closed_turn

    async def discard_turn(self) -> None:
        """Clears buffered audio from a rejected/short VAD snippet without disturbing subsequent turns."""
        if self._session and self._active_turn.has_streamed_audio:
            discarded = await self.signal_end_of_turn(enqueue_for_finish=False)
            if discarded is not None:
                try:
                    await asyncio.wait_for(discarded.done_event.wait(), timeout=0.4)
                except Exception:
                    await self._drop_connection()
        else:
            self._active_turn = _LiveSTTTurnBuffer()
        self._end_sent_event.set()

    async def finish_turn(self, timeout: float = 2.5) -> Optional[str]:
        """
        Waits for the live STT transcript corresponding to the streamed turn.
        Returns None if no live audio was streamed (signaling REST fallback).
        """
        if not self._pending_finish_turns:
            if not self._active_turn.has_streamed_audio or self._session is None:
                return None
            if not self._end_sent_event.is_set():
                try:
                    await asyncio.wait_for(self._end_sent_event.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    await self.signal_end_of_turn()
            if not self._pending_finish_turns and self._active_turn.has_streamed_audio:
                await self.signal_end_of_turn()

        if not self._pending_finish_turns:
            return None

        turn = self._pending_finish_turns.popleft()
        try:
            await asyncio.wait_for(turn.done_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[STT LIVE] Timed out waiting for generation_complete; using available hypothesis or REST fallback.")
        return turn.get_text()

    async def run_input_pump(
        self,
        input_queue: asyncio.Queue,
        is_running_fn: Callable[[], bool],
        should_skip_audio_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Continuously forwards live PCM audio chunks from AudioPipeline.input_queue."""
        while is_running_fn() and not self._closed:
            try:
                item = await asyncio.wait_for(input_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            if item == b"__END_OF_TURN__":
                await self.signal_end_of_turn()
            elif item == b"__DISCARD_TURN__":
                await self.discard_turn()
            elif item:
                if should_skip_audio_fn and should_skip_audio_fn():
                    continue
                await self.send_pcm(item)
