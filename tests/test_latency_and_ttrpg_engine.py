import asyncio
import types as pytypes
import unittest
from unittest.mock import MagicMock

from google.genai import types

from core.config_manager import sync_config_schema
from core.model_discovery import get_ttrpg_model_options
from core.stt_service import ModularLiveSTTClient
from core.audio_stream import StreamingWSOLAScaler
from core.engine import AetherEngine


class _FakeLiveSTTSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.sent_inputs = []

    async def send_realtime_input(self, **kwargs):
        self.sent_inputs.append(kwargs)

    async def receive(self):
        for r in self._responses:
            yield r
            await asyncio.sleep(0.01)


class _FakeConnectCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestLatencyAndTTRPGEngine(unittest.TestCase):
    def test_ttrpg_model_config_sync_and_options(self):
        cfg = sync_config_schema({})
        self.assertEqual(cfg["primary_model_endpoint"], "gemini-3.8-live")
        self.assertEqual(cfg["tier2_moderate_model"], "gemini-4-hydrogen:medium")
        self.assertEqual(cfg["tier3_heavy_model"], "gemini-4-argon:medium")
        self.assertEqual(cfg["tier2_heavy_model"], "gemini-4-argon:medium")
        self.assertEqual(cfg["ttrpg_model_endpoint"], "gemini-4-hydrogen:medium")
        self.assertEqual(cfg["api"]["ttrpg_model_id"], "gemini-4-hydrogen:medium")

        mod_ids = [o["id"] for o in cfg["models"]["tier2_moderate_options"]]
        self.assertEqual(
            mod_ids,
            [
                "gemini-4-hydrogen:low",
                "gemini-4-hydrogen:medium",
                "gemini-4-hydrogen:high",
                "gemini-4-hydrogen:xhigh",
            ],
        )
        self.assertNotIn("gemini-4-hydrogen:instant", mod_ids)

        opts = get_ttrpg_model_options()
        ids = [o["id"] for o in opts]
        self.assertIn("gemini-4-hydrogen:medium", ids)
        self.assertIn("gemini-4-hydrogen:high", ids)
        self.assertIn("gemini-4-argon:high", ids)

    def test_engine_active_cortex_profile_switches_for_ttrpg(self):
        eng = object.__new__(AetherEngine)
        eng._ttrpg_session_active = False
        eng.active_workspace_type = "general"
        eng.config_getter = lambda: {
            "primary_model_endpoint": "gemini-4-hydrogen:instant",
            "ttrpg_model_endpoint": "gemini-4-argon:medium",
        }

        self.assertEqual(eng._get_active_cortex_profile(), "gemini-4-hydrogen:instant")
        eng._ttrpg_session_active = True
        self.assertEqual(eng._get_active_cortex_profile(), "gemini-4-argon:medium")
        eng._ttrpg_session_active = False
        eng.active_workspace_type = "campaign"
        self.assertEqual(eng._get_active_cortex_profile(), "gemini-4-argon:medium")

    def test_split_complete_sentences_preserves_abbreviations_and_streams_cleanly(self):
        sentences, rem = AetherEngine._split_complete_sentences(
            "Dr. Vane opened the dusty iron gate carefully. What do you see inside the crypt? Wait",
            min_chars=24,
        )
        self.assertEqual(
            sentences,
            [
                "Dr. Vane opened the dusty iron gate carefully.",
                "What do you see inside the crypt?",
            ],
        )
        self.assertEqual(rem, "Wait")

    def test_stream_chat_message_resilient_streams_chunks_and_function_calls(self):
        eng = object.__new__(AetherEngine)
        eng._prepare_model_turns_payload = lambda: None

        chunks = [
            pytypes.SimpleNamespace(
                candidates=[
                    pytypes.SimpleNamespace(
                        content=pytypes.SimpleNamespace(
                            parts=[types.Part.from_text(text="The heavy oak door groans open. ")]
                        ),
                        finish_reason=None,
                        grounding_metadata=None,
                    )
                ],
                function_calls=[],
                prompt_feedback=None,
            ),
            pytypes.SimpleNamespace(
                candidates=[
                    pytypes.SimpleNamespace(
                        content=pytypes.SimpleNamespace(
                            parts=[types.Part.from_text(text="Inside, a hearth glows warmly.")]
                        ),
                        finish_reason="STOP",
                        grounding_metadata=None,
                    )
                ],
                function_calls=[],
                prompt_feedback=None,
            ),
        ]

        fake_chat = MagicMock()
        fake_chat.send_message_stream.return_value = iter(chunks)
        emitted = []

        async def _run():
            resp, first_tok_ms = await eng._stream_chat_message_resilient(
                fake_chat,
                "Describe the tavern.",
                on_text_chunk=lambda t: emitted.append(t),
            )
            return resp, first_tok_ms

        resp, ft_ms = asyncio.run(_run())
        self.assertEqual("".join(emitted), "The heavy oak door groans open. Inside, a hearth glows warmly.")
        self.assertEqual(resp.text, "The heavy oak door groans open. Inside, a hearth glows warmly.")
        self.assertGreaterEqual(ft_ms, 0.0)

    def test_modular_live_stt_client_streams_and_finishes_turn(self):
        fake_msgs = [
            pytypes.SimpleNamespace(
                server_content=pytypes.SimpleNamespace(
                    interim_input_transcription=pytypes.SimpleNamespace(text="Open Chrome"),
                    input_transcription=None,
                    generation_complete=False,
                )
            ),
            pytypes.SimpleNamespace(
                server_content=pytypes.SimpleNamespace(
                    interim_input_transcription=None,
                    input_transcription=pytypes.SimpleNamespace(text="Open Chrome please."),
                    generation_complete=True,
                )
            ),
        ]
        fake_session = _FakeLiveSTTSession(fake_msgs)
        fake_client = MagicMock()
        fake_client.aio.live.connect.return_value = _FakeConnectCtx(fake_session)

        async def _run():
            stt = ModularLiveSTTClient(fake_client, model="gemini-3.5-transcribe-live")
            ok = await stt.ensure_connected()
            self.assertTrue(ok)
            await stt.send_pcm(b"\x01\x02" * 800)
            await stt.signal_end_of_turn()
            txt = await stt.finish_turn(timeout=1.0)
            await stt.close()
            return txt

        transcript = asyncio.run(_run())
        self.assertEqual(transcript, "Open Chrome please.")
        self.assertTrue(any(call.get("audio_stream_end") is True for call in fake_session.sent_inputs))

    def test_streaming_wsola_scaler_brisk_measured_natural(self):
        import numpy as np

        sr = 24000
        t = np.arange(sr * 2, dtype=np.float32) / sr
        pcm = (np.sin(2 * np.pi * 220 * t) * 10000).astype(np.int16).tobytes()

        # Brisk (1.18x) should shorten duration cleanly
        brisk = StreamingWSOLAScaler(speed=1.18, sample_rate=sr)
        out_fast = bytearray()
        for i in range(0, len(pcm), 4800):
            out_fast.extend(brisk.process(pcm[i:i + 4800]))
        out_fast.extend(brisk.flush())
        self.assertLess(len(out_fast), len(pcm) * 0.90)
        self.assertGreater(len(out_fast), len(pcm) * 0.78)

        # Measured (0.88x) should lengthen duration cleanly
        measured = StreamingWSOLAScaler(speed=0.88, sample_rate=sr)
        out_slow = bytearray()
        for i in range(0, len(pcm), 4800):
            out_slow.extend(measured.process(pcm[i:i + 4800]))
        out_slow.extend(measured.flush())
        self.assertGreater(len(out_slow), len(pcm) * 1.06)

        # Natural (1.0x) bypasses untouched
        natural = StreamingWSOLAScaler(speed=1.0, sample_rate=sr)
        self.assertEqual(natural.process(pcm[:4800]), pcm[:4800])

    def test_dispatcher_execute_moderate_task_uses_hydrogen_tier(self):
        from tools.dispatcher import ToolDispatcher, DELEGATE_MODERATE_TASK_DECLARATION

        disp = ToolDispatcher(
            config_getter=lambda: {
                "tier2_moderate_model": "gemini-4-hydrogen:medium",
                "tier3_heavy_model": "gemini-4-argon:medium",
            }
        )
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = pytypes.SimpleNamespace(text="Press the left rune first.")
        disp.genai_client = fake_client

        res = asyncio.run(
            disp.execute_moderate_task(
                task_type="puzzle_reasoning",
                title="Dungeon Rune Lock",
                instructions="Which rune goes first?",
            )
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["model"], "gemini-4-hydrogen")
        self.assertEqual(res["thinking_level"], "medium")
        self.assertIn("left rune", res["answer"])
        core_names = [d["name"] for d in disp.get_core_declarations()]
        self.assertIn(DELEGATE_MODERATE_TASK_DECLARATION["name"], core_names)


if __name__ == "__main__":
    unittest.main()
