import unittest
from unittest.mock import MagicMock, patch

from core.stt_service import (
    resolve_stt_model_endpoint,
    transcribe_audio_buffer,
    set_active_engine,
    get_active_engine,
)
from tools.speech_pipeline import (
    transcribe_audio_buffer as tool_transcribe,
    resolve_stt_model_endpoint as tool_resolve,
)


class TestSTTService(unittest.TestCase):
    def tearDown(self):
        set_active_engine(None)

    def test_re_export_parity(self):
        self.assertIs(transcribe_audio_buffer, tool_transcribe)
        self.assertIs(resolve_stt_model_endpoint, tool_resolve)

    @patch("core.stt_service.load_config")
    def test_resolve_stt_model_endpoint_sanitization(self, mock_load_config):
        mock_load_config.return_value = {
            "primary_model_endpoint": "gemini-3.8-flash",
            "tier1_fast_model": "gemini-3.8-flash"
        }

        # WebSocket / fake transcribe IDs must resolve to the valid multimodal Flash endpoint
        for non_model in (
            "gemini_live_audio",
            "gemini_live",
            "gemini-3.5-transcribe",
            "gemini-3.5-transcribe-live",
            "gemini-3.8-transcribe",
            "gemini-3.8-transcribe-live",
            "primary_flash_stt",
            "primary_flash",
        ):
            resolved = resolve_stt_model_endpoint(non_model)
            self.assertEqual(resolved, "gemini-3.8-flash", f"Failed for {non_model}")

        # Real model endpoint preserved
        self.assertEqual(resolve_stt_model_endpoint("gemini-2.5-flash"), "gemini-2.5-flash")
        self.assertEqual(resolve_stt_model_endpoint("models/gemini-2.5-flash"), "gemini-2.5-flash")

    @patch("core.stt_service.load_config")
    def test_transcribe_audio_buffer_routes_to_live_engine_if_available(self, mock_load_config):
        mock_load_config.return_value = {
            "primary_model_endpoint": "gemini-3.8-flash",
            "stt_endpoint": "gemini_live_audio"
        }

        mock_engine = MagicMock()
        mock_engine.send_live_audio_buffer.return_value = "live_dispatched"
        set_active_engine(mock_engine)

        res = transcribe_audio_buffer(b"dummy_wav_data", mime_type="audio/wav", stt_endpoint="gemini_live_audio")
        self.assertEqual(res, "live_dispatched")
        mock_engine.send_live_audio_buffer.assert_called_once_with(b"dummy_wav_data")

    @patch("core.stt_service.get_genai_client")
    @patch("core.stt_service.load_config")
    def test_transcribe_audio_buffer_falls_back_to_flash_not_gemini_live_audio(self, mock_load_config, mock_get_client):
        mock_load_config.return_value = {
            "primary_model_endpoint": "gemini-3.8-flash",
            "stt_endpoint": "gemini_live_audio"
        }
        set_active_engine(None)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "Hello world from audio"
        mock_client.models.generate_content.return_value = mock_resp
        mock_get_client.return_value = mock_client

        # Call with gemini_live_audio when live connection is unavailable
        text = transcribe_audio_buffer(b"dummy_wav_data", stt_endpoint="gemini_live_audio")
        self.assertEqual(text, "Hello world from audio")

        # Crucial check: model argument passed to generate_content MUST NOT be 'gemini_live_audio'
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-3.8-flash")
        self.assertNotEqual(call_kwargs["model"], "gemini_live_audio")

    @patch("core.stt_service.get_genai_client")
    @patch("core.stt_service.load_config")
    def test_transcribe_audio_buffer_primary_flash_stt(self, mock_load_config, mock_get_client):
        mock_load_config.return_value = {
            "primary_model_endpoint": "gemini-3.8-flash",
            "stt_endpoint": "primary_flash_stt"
        }
        set_active_engine(None)

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "Testing primary flash STT"
        mock_client.models.generate_content.return_value = mock_resp
        mock_get_client.return_value = mock_client

        text = transcribe_audio_buffer(b"dummy_wav_data", stt_endpoint="primary_flash_stt")
        self.assertEqual(text, "Testing primary flash STT")

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-3.8-flash")


if __name__ == "__main__":
    unittest.main()
