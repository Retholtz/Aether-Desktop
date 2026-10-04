import os
import json
import unittest
from unittest.mock import MagicMock, patch

from core.model_discovery import (
    load_cached_models,
    load_cached_categorized_models,
    fetch_available_gemini_models,
    verify_chat_endpoint,
    verify_tts_endpoint,
    verify_stt_endpoint,
    model_chronological_key,
    MANIFEST_PATH,
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_CATEGORIZED_MODELS,
)
from core.gui_bridge import GuiBridge
from core.config_manager import sync_config_schema, save_config, load_config


class TestModelCategorization(unittest.TestCase):
    def tearDown(self):
        if os.path.exists(MANIFEST_PATH):
            try:
                os.remove(MANIFEST_PATH)
            except Exception:
                pass

    def test_model_categorization_buckets(self):
        mock_client = MagicMock()
        
        m_chat = MagicMock(name="models/gemini-3.8-flash")
        m_chat.name = "models/gemini-3.8-flash"
        m_chat.display_name = "Gemini 3.8 Flash"

        m_tts = MagicMock(name="models/gemini-3.8-flash-tts")
        m_tts.name = "models/gemini-3.8-flash-tts"
        m_tts.display_name = "Gemini 3.8 Flash TTS"

        m_stt = MagicMock(name="models/gemini-3.5-transcribe")
        m_stt.name = "models/gemini-3.5-transcribe"
        m_stt.display_name = "Gemini 3.5 Transcribe"

        m_video = MagicMock(name="models/veo-3.1-generate-preview")
        m_video.name = "models/veo-3.1-generate-preview"
        m_video.display_name = "Veo 3.1"

        mock_client.models.list.return_value = [m_chat, m_tts, m_stt, m_video]

        categorized = fetch_available_gemini_models(mock_client)

        chat_ids = [m["id"] for m in categorized["chat_models"]]
        tts_ids = [m["id"] for m in categorized["tts_models"]]
        stt_ids = [m["id"] for m in categorized["stt_models"]]

        # Chat verification
        self.assertIn("gemini-3.8-flash", chat_ids)
        self.assertNotIn("gemini-3.8-flash-tts", chat_ids)
        self.assertNotIn("veo-3.1-generate-preview", chat_ids)

        # TTS & STT verification
        self.assertIn("gemini-3.8-flash-tts", tts_ids)
        self.assertIn("gemini-3.5-transcribe", stt_ids)

    def test_strict_chat_exclusions(self):
        mock_client = MagicMock()

        excluded_model_names = [
            "models/gemini-nano-banana",
            "models/gemini-robotics-er2",
            "models/gemini-3.8-computer-use",
            "models/gemini-omni-flash",
            "models/gemini-live-native-audio",
            "models/gemini-3.8-live",
            "models/gemini-3.8-live-extended-thinking",
            "models/imagen-3.0-generate",
            "models/text-embedding-004",
        ]
        allowed_model_names = [
            "models/gemini-3.8-flash",
            "models/gemini-3.1-pro-preview",
            "models/gemini-3.5-flash",
        ]

        mocks = []
        for name in excluded_model_names + allowed_model_names:
            m = MagicMock()
            m.name = name
            m.display_name = name.replace("models/", "")
            mocks.append(m)

        mock_client.models.list.return_value = mocks

        categorized = fetch_available_gemini_models(mock_client)
        chat_ids = [m["id"] for m in categorized["chat_models"]]

        # Disqualified non-general chat endpoints must NOT be in chat_models
        self.assertNotIn("gemini-nano-banana", chat_ids)
        self.assertNotIn("gemini-robotics-er2", chat_ids)
        self.assertNotIn("gemini-3.8-computer-use", chat_ids)
        self.assertNotIn("gemini-omni-flash", chat_ids)
        self.assertNotIn("gemini-live-native-audio", chat_ids)
        self.assertNotIn("gemini-3.8-live", chat_ids)
        self.assertNotIn("gemini-3.8-live-extended-thinking", chat_ids)
        self.assertNotIn("imagen-3.0-generate", chat_ids)
        self.assertNotIn("text-embedding-004", chat_ids)

        # Conversational models must be present
        self.assertIn("gemini-3.8-flash", chat_ids)
        self.assertIn("gemini-3.1-pro-preview", chat_ids)
        self.assertIn("gemini-3.5-flash", chat_ids)


class TestModelDiscovery(unittest.TestCase):
    def setUp(self):
        if os.path.exists(MANIFEST_PATH):
            try:
                os.remove(MANIFEST_PATH)
            except Exception:
                pass

    def tearDown(self):
        if os.path.exists(MANIFEST_PATH):
            try:
                os.remove(MANIFEST_PATH)
            except Exception:
                pass

    def test_load_cached_fallback_when_empty(self):
        models = load_cached_models()
        self.assertEqual(models, DEFAULT_FALLBACK_MODELS)

    def test_load_cached_categorized_models_fallback_when_empty(self):
        cat_models = load_cached_categorized_models()
        self.assertEqual(cat_models, DEFAULT_CATEGORIZED_MODELS)

    def test_fetch_available_gemini_models_mocked(self):
        mock_client = MagicMock()
        
        m1 = MagicMock()
        m1.name = "models/gemini-future-flash"
        m1.display_name = "Gemini Future Flash"
        m1.supported_generation_methods = ["generateContent"]

        m2 = MagicMock()
        m2.name = "models/text-embedding-004"
        m2.display_name = "Embedding Model"
        m2.supported_generation_methods = ["embedContent"]

        mock_client.models.list.return_value = [m1, m2]

        discovered = fetch_available_gemini_models(mock_client)
        
        # Verify filtering: gemini-future-flash included in chat_models, embedding excluded
        chat_models = discovered.get("chat_models", [])
        ids = [m["id"] for m in chat_models]
        self.assertIn("gemini-future-flash", ids)
        self.assertNotIn("text-embedding-004", ids)

        # Verify manifest file write
        self.assertTrue(os.path.exists(MANIFEST_PATH))
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("chat_models", data)
        self.assertEqual(len(data["chat_models"]), 1)

    def test_load_cached_models_from_cache(self):
        cached_data = {
            "updated_at": 123456.0,
            "chat_models": [
                {
                    "id": "gemini-cached-test",
                    "display_name": "Gemini Cached Test",
                    "description": "Test cached model"
                }
            ],
            "tts_models": [],
            "stt_models": []
        }
        os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(cached_data, f)

        models = load_cached_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["id"], "gemini-cached-test")

    def test_load_cached_models_corrupt_cache_fallback(self):
        os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            f.write("corrupted { invalid json")

        models = load_cached_models()
        self.assertEqual(models, DEFAULT_FALLBACK_MODELS)

    def test_fetch_available_gemini_models_client_none(self):
        models = fetch_available_gemini_models(None)
        self.assertEqual(models, DEFAULT_CATEGORIZED_MODELS)

    def test_fetch_available_gemini_models_api_error_fallback(self):
        mock_client = MagicMock()
        mock_client.models.list.side_effect = RuntimeError("API Network Failure")

        models = fetch_available_gemini_models(mock_client)
        self.assertEqual(models, DEFAULT_CATEGORIZED_MODELS)

    def test_gui_bridge_model_discovery_methods(self):
        mock_bridge = MagicMock(spec=GuiBridge)
        mock_bridge.engine = MagicMock()
        mock_bridge.get_discovered_models = GuiBridge.get_discovered_models.__get__(mock_bridge)
        mock_bridge.refresh_discovered_models = GuiBridge.refresh_discovered_models.__get__(mock_bridge)

        # 1. get_discovered_models without cache returns categorized fallback
        res = mock_bridge.get_discovered_models()
        self.assertEqual(res, DEFAULT_CATEGORIZED_MODELS)
        self.assertIn("chat_models", res)
        self.assertIn("tts_models", res)
        self.assertIn("stt_models", res)

        # 2. refresh_discovered_models when engine has no client
        mock_bridge.engine.client = None
        refresh_res = mock_bridge.refresh_discovered_models()
        self.assertFalse(refresh_res["success"])
        self.assertIn("API client not initialized", refresh_res["error"])

        # 3. refresh_discovered_models with active mock client
        mock_client = MagicMock()
        m = MagicMock()
        m.name = "models/gemini-mock-3.9"
        m.display_name = "Gemini Mock 3.9"
        m.supported_generation_methods = ["generateContent"]
        mock_client.models.list.return_value = [m]

        mock_bridge.engine.client = mock_client
        refresh_res = mock_bridge.refresh_discovered_models()
        self.assertTrue(refresh_res["success"])
        self.assertIn("categorized_models", refresh_res)
        self.assertIn("models", refresh_res)
        self.assertEqual(len(refresh_res["models"]), 1)
        self.assertEqual(refresh_res["models"][0]["id"], "gemini-mock-3.9")

    def test_config_manager_primary_and_heavy_model_schema_sync(self):
        schema = sync_config_schema({
            "primary_model_endpoint": "gemini-custom-flash",
            "tier2_heavy_model": "gemini-custom-heavy",
            "stt_model_endpoint": "gemini-custom-transcribe",
            "tts_model_endpoint": "gemini-custom-tts"
        })
        self.assertEqual(schema["primary_model_endpoint"], "gemini-custom-flash")
        self.assertEqual(schema["tier1_fast_model"], "gemini-custom-flash")
        self.assertEqual(schema["api"]["model_id"], "gemini-custom-flash")
        self.assertEqual(schema["tier2_heavy_model"], "gemini-custom-heavy")
        self.assertEqual(schema["api"]["pro_model_id"], "gemini-custom-heavy")
        self.assertEqual(schema["stt_model_endpoint"], "gemini-custom-transcribe")
        self.assertEqual(schema["api"]["stt_model_id"], "gemini-custom-transcribe")
        self.assertEqual(schema["tts_model_endpoint"], "gemini-custom-tts")
        self.assertEqual(schema["api"]["tts_model_id"], "gemini-custom-tts")

    def test_chronological_model_sorting(self):
        models = [
            {"id": "gemini-1.0-pro"},
            {"id": "gemini-1.5-flash-001"},
            {"id": "gemini-1.5-flash-002"},
            {"id": "gemini-2.0-flash"},
            {"id": "gemini-2.5-flash"},
            {"id": "gemini-3.1-pro-preview"},
            {"id": "gemini-3.8-flash"},
        ]
        models.sort(key=model_chronological_key, reverse=True)
        sorted_ids = [m["id"] for m in models]
        self.assertEqual(sorted_ids, [
            "gemini-3.8-flash",
            "gemini-3.1-pro-preview",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-flash-002",
            "gemini-1.5-flash-001",
            "gemini-1.0-pro",
        ])


class TestTripleBucketProbing(unittest.TestCase):
    def tearDown(self):
        if os.path.exists(MANIFEST_PATH):
            try:
                os.remove(MANIFEST_PATH)
            except Exception:
                pass

    def test_verify_chat_success_and_failure(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value.text = "pong"
        self.assertTrue(verify_chat_endpoint(mock_client, "gemini-3.8-flash"))

        mock_client.models.generate_content.side_effect = Exception("404 Not Found")
        self.assertFalse(verify_chat_endpoint(mock_client, "gemini-deprecated"))

    def test_verify_tts_success_and_failure(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value.candidates = ["audio_part"]
        self.assertTrue(verify_tts_endpoint(mock_client, "gemini-3.8-flash-tts"))

        mock_client.models.generate_content.side_effect = Exception("500 Server Error")
        self.assertFalse(verify_tts_endpoint(mock_client, "gemini-broken-tts"))

    def test_verify_stt_success_and_failure(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value.text = "transcribed speech"
        self.assertTrue(verify_stt_endpoint(mock_client, "gemini-3.5-transcribe"))

        mock_client.models.generate_content.side_effect = Exception("Invalid Model")
        self.assertFalse(verify_stt_endpoint(mock_client, "gemini-broken-stt"))

    def test_fetch_available_gemini_models_probes_and_discards_failures(self):
        mock_client = MagicMock()
        m_good = MagicMock()
        m_good.name = "models/gemini-3.8-flash"
        m_good.display_name = "Gemini 3.8 Flash"

        m_bad = MagicMock()
        m_bad.name = "models/gemini-deprecated-flash"
        m_bad.display_name = "Gemini Deprecated Flash"

        mock_client.models.list.return_value = [m_good, m_bad]

        def fake_generate_content(model, **kwargs):
            if model == "gemini-deprecated-flash":
                raise RuntimeError("404 Not Found")
            resp = MagicMock()
            resp.text = "pong"
            return resp

        mock_client.models.generate_content.side_effect = fake_generate_content

        discovered = fetch_available_gemini_models(mock_client, probe_endpoints=True)
        chat_ids = [m["id"] for m in discovered["chat_models"]]
        self.assertIn("gemini-3.8-flash", chat_ids)
        self.assertNotIn("gemini-deprecated-flash", chat_ids)


if __name__ == "__main__":
    unittest.main()
