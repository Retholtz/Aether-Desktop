"""
Batch 4 Unit Tests: Hardware, Audio, Voice, STT, User Memory, OAuth/IMAP, Email Monitor & Proactive Engine.
"""

import asyncio
import ctypes
import datetime
import io
import os
import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np


def _make_wav_bytes(duration_sec: float = 0.75, sr: int = 16000, freq: float = 220.0, amp: float = 0.25) -> bytes:
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    samples = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


class TestBatch4HardwareService(unittest.TestCase):
    def test_amd_adl_malloc_restype_is_64bit_void_p(self):
        from core.hardware_service import AmdAdlBackend

        fake_dll = MagicMock()
        fake_dll.ADL_Main_Control_Create.return_value = 0
        fake_dll.ADL_Adapter_NumberOfAdapters_Get.return_value = 0

        fake_msvcrt = MagicMock()
        fake_msvcrt.malloc = ctypes.cdll.msvcrt.malloc

        with patch("core.hardware_service.os.name", "nt"), \
             patch("ctypes.windll.LoadLibrary", return_value=fake_dll), \
             patch("ctypes.cdll.msvcrt", fake_msvcrt):
            backend = AmdAdlBackend()
            self.assertEqual(fake_msvcrt.malloc.restype, ctypes.c_void_p)
            self.assertEqual(fake_msvcrt.malloc.argtypes, [ctypes.c_size_t])
            self.assertIsNotNone(backend._malloc_cb)

    def test_wmi_com_initialized_once_per_thread(self):
        from core.hardware_service import CpuMonitorBackend
        import collections

        cpu = CpuMonitorBackend.__new__(CpuMonitorBackend)
        cpu._tls = __import__("threading").local()
        cpu._temp_provider = None
        cpu._cached_temp_c = None
        cpu._last_temp_poll_ts = 0.0
        cpu._temp_samples = collections.deque(maxlen=3)

        fake_pythoncom = MagicMock()
        fake_wmi_root = MagicMock()
        fake_wmi_root.InstancesOf.return_value = []
        fake_locator = MagicMock()
        fake_locator.ConnectServer.return_value = fake_wmi_root
        fake_win32_client = MagicMock()
        fake_win32_client.Dispatch.return_value = fake_locator
        fake_win32 = MagicMock()
        fake_win32.client = fake_win32_client

        with patch.dict("sys.modules", {"pythoncom": fake_pythoncom, "win32com": fake_win32, "win32com.client": fake_win32_client}):
            cpu._query_user_mode_cpu_temp()
            cpu._temp_provider = None  # allow second poll attempt
            cpu._query_user_mode_cpu_temp()

        # CoInitialize should only be called once for this thread, not leaked on every 1.5s poll
        self.assertEqual(fake_pythoncom.CoInitialize.call_count, 1)
        self.assertTrue(getattr(cpu._tls, "com_initialized", False))

    def test_frame_lock_exists_and_get_fps_metrics_safe(self):
        from core.hardware_service import HardwareService
        import collections

        svc = HardwareService.__new__(HardwareService)
        svc._frame_lock = __import__("threading").Lock()
        svc._frame_times = collections.deque([16.6, 16.7, 16.5, 16.8], maxlen=600)
        svc._presentmon_active = True
        svc._presentmon_proc = MagicMock()
        svc._presentmon_proc.poll.return_value = None
        svc._monitored_process_name = "game.exe"
        svc._last_frame_timestamp = __import__("time").time()

        metrics = svc.get_fps_metrics()
        self.assertIsNotNone(metrics["fps_avg"])
        self.assertGreater(metrics["fps_avg"], 50)


class TestBatch4AudioOutputAndDucking(unittest.TestCase):
    def test_apply_micro_fade_out_fades_tail_smoothly(self):
        from core.audio_output import apply_micro_fade_out

        # Ramp from 1000 to 20000 across 400 samples; tail is ~20000, head is ~1000
        ramp = np.linspace(1000, 20000, 400, dtype=np.int16)
        faded_bytes = apply_micro_fade_out(ramp.tobytes(), sample_rate=24000, fade_ms=8.0)
        faded = np.frombuffer(faded_bytes, dtype=np.int16)
        # First sample of faded tail should start near the tail level (~10k+), NOT near 1000 (the head!)
        self.assertGreater(int(faded[0]), 9000)
        # Last sample should taper to 0
        self.assertEqual(int(faded[-1]), 0)

    def test_ducking_disambiguates_multi_session_same_pid_and_only_overrides_own_pid(self):
        from core.audio_ducking_service import AudioDuckingService

        svc = AudioDuckingService.__new__(AudioDuckingService)
        svc.enabled = True
        svc.own_pid = 1001
        svc.duck_percentage = 75
        svc._is_ducked = False
        svc._original_volumes = {}
        svc._session_volumes = {}
        svc._sync_lock = __import__("threading").RLock()

        def _make_session(pid, proc_name, inst_id, vol):
            s = MagicMock()
            s.Process = MagicMock()
            s.Process.pid = pid
            s.Process.name.return_value = proc_name
            s.InstanceIdentifier = inst_id
            vol_ctl = MagicMock()
            vol_ctl.GetMasterVolume.return_value = vol
            vol_ctl.GetMute.return_value = 0
            s._ctl = MagicMock()
            s._ctl.QueryInterface.return_value = vol_ctl
            s.SimpleAudioVolume = vol_ctl
            return s

        # Two Chrome audio sessions sharing PID 5000 with different volumes, plus an unrelated python.exe (PID 9999)
        s_chrome_1 = _make_session(5000, "chrome.exe", "chrome-tab-1", 0.80)
        s_chrome_2 = _make_session(5000, "chrome.exe", "chrome-tab-2", 0.40)
        s_other_py = _make_session(9999, "python.exe", "other-py", 0.50)
        s_own_py = _make_session(1001, "python.exe", "own-aether", 0.50)

        fake_au = MagicMock()
        fake_au.GetAllSessions.return_value = [s_chrome_1, s_chrome_2, s_other_py, s_own_py]

        with patch("core.audio_ducking_service.PYCAW_AVAILABLE", True), \
             patch("core.audio_ducking_service.AudioUtilities", fake_au), \
             patch.object(svc, "_com_apartment") as mock_com:
            mock_com.return_value.__enter__.return_value = None
            mock_com.return_value.__exit__.return_value = False

            svc.duck_all()
            # Own PID is ensured at 1.0, unrelated python.exe is NOT forced to 1.0!
            s_own_py.SimpleAudioVolume.SetMasterVolume.assert_called_with(1.0, None)
            s_other_py.SimpleAudioVolume.SetMasterVolume.assert_not_called()
            # Both Chrome sessions saved distinct original volumes
            self.assertAlmostEqual(svc._session_volumes[(5000, "chrome-tab-1")], 0.80, places=3)
            self.assertAlmostEqual(svc._session_volumes[(5000, "chrome-tab-2")], 0.40, places=3)

            svc.restore_all()
            s_chrome_1.SimpleAudioVolume.SetMasterVolume.assert_called_with(0.80, None)
            s_chrome_2.SimpleAudioVolume.SetMasterVolume.assert_called_with(0.40, None)


class TestBatch4AudioStream(unittest.TestCase):
    def test_high_pass_filter_matches_exact_recurrence(self):
        from core.audio_stream import AudioPipeline

        p = AudioPipeline(mode="always_on", wake_word_enabled=False)
        rng = np.random.default_rng(42)
        chunk1 = rng.uniform(-0.5, 0.5, 1024).astype(np.float32)
        chunk2 = rng.uniform(-0.5, 0.5, 1024).astype(np.float32)

        # Reference exact scalar recurrence
        alpha = p._hp_alpha
        def _ref_hp(chunks):
            px, py = 0.0, 0.0
            outs = []
            for ch in chunks:
                y = np.empty(len(ch), dtype=np.float64)
                for i, x in enumerate(ch):
                    py = alpha * (py + float(x) - px)
                    px = float(x)
                    y[i] = py
                outs.append(y.astype(np.float32))
            return outs

        ref1, ref2 = _ref_hp([chunk1, chunk2])
        got1 = p._apply_high_pass_filter(chunk1)
        got2 = p._apply_high_pass_filter(chunk2)

        self.assertLess(float(np.max(np.abs(got1 - ref1))), 1e-5)
        self.assertLess(float(np.max(np.abs(got2 - ref2))), 1e-5)

    def test_wsola_handles_odd_byte_length_chunks(self):
        from core.audio_stream import StreamingWSOLAScaler

        scaler = StreamingWSOLAScaler(speed=1.25, sample_rate=24000)
        pcm = (np.sin(np.linspace(0, 20 * np.pi, 4800)) * 12000).astype(np.int16).tobytes()
        # Split into odd-length chunks
        out1 = scaler.process(pcm[:1001])
        out2 = scaler.process(pcm[1001:3002])
        out3 = scaler.process(pcm[3002:])
        flushed = scaler.flush()
        total = out1 + out2 + out3 + flushed
        self.assertGreater(len(total), 0)
        self.assertEqual(len(total) % 2, 0)

    def test_ptt_release_does_not_emit_duplicate_end_of_turn(self):
        from core.audio_stream import AudioPipeline

        p = AudioPipeline(mode="ptt", wake_word_enabled=False)
        p._running = True
        p.hw_in_rate = 16000
        p.target_input_rate = 16000

        # Press PTT, stream a tiny short frame (<2400 bytes), then release PTT
        p.set_ptt(True)
        tiny_frame = (np.ones(160, dtype=np.float32) * 0.1)
        p._input_callback(tiny_frame, len(tiny_frame), None, None)
        p.set_ptt(False)

        markers = []
        while not p.input_queue.empty():
            item = p.input_queue.get_nowait()
            if item in (b"__END_OF_TURN__", b"__DISCARD_TURN__"):
                markers.append(item)

        self.assertEqual(len(markers), 1, f"Expected exactly 1 terminal marker on PTT release, got {markers}")

    def test_query_windows_sound_devices_tolerates_rpc_e_changed_mode(self):
        from core import audio_stream

        fake_ole32 = MagicMock()
        # RPC_E_CHANGED_MODE (-2147417850) on Win11 STA UI thread
        fake_ole32.CoInitializeEx.return_value = -2147417850
        # Return non-zero from CoCreateInstance so we exit cleanly after COM init check
        fake_ole32.CoCreateInstance.return_value = -1

        with patch("core.audio_stream.sys.platform", "win32"), \
             patch("ctypes.windll.ole32", fake_ole32, create=True):
            res = audio_stream._query_windows_system_sound_devices()

        self.assertIsNone(res)
        # Must NOT call CoUninitialize when CoInitializeEx returned RPC_E_CHANGED_MODE
        fake_ole32.CoUninitialize.assert_not_called()
        # Must still have proceeded to CoCreateInstance (not aborted on RPC_E_CHANGED_MODE!)
        fake_ole32.CoCreateInstance.assert_called_once()


class TestBatch4WakeWordAndVoiceVerifier(unittest.TestCase):
    def test_wake_word_rejects_everyday_false_triggers_and_precompiles(self):
        from core.wake_word import WakeWordDetector

        det = WakeWordDetector(wake_phrase="Hey Aether", sleep_phrase="Aether stop listening")
        self.assertIsNotNone(det._wake_clean_re)
        self.assertIsNotNone(det._sleep_clean_re)

        for false_phrase in [
            "he later went to the store",
            "ok after lunch we can talk",
            "the weather is really nice today",
            "either way it works for me",
            "read the paper please",
            "he ever said that",
            "ok later then",
        ]:
            matched, _ = det.matches_wake_phrase(false_phrase)
            self.assertFalse(matched, f"Everyday sentence should NOT false-trigger wake word: '{false_phrase}'")

        # Valid variants still work
        for valid_phrase in ["Hey Aether", "Hey Aker what time is it", "Okay Aether", "Aether open Chrome"]:
            matched, _ = det.matches_wake_phrase(valid_phrase)
            self.assertTrue(matched, f"Valid wake phrase failed: '{valid_phrase}'")

    def test_voice_verifier_fails_closed_when_profile_on_disk_but_uninitialized(self):
        from core.voice_verifier import VoiceProfileVerifier, DEFAULT_MODEL_PATH
        from core.paths import get_app_dir

        # Default model path must be under writable app_dir, not read-only bundle
        self.assertTrue(os.path.abspath(DEFAULT_MODEL_PATH).startswith(os.path.abspath(get_app_dir())))

        with tempfile.TemporaryDirectory() as tmpdir:
            prof = Path(tmpdir) / "user_voiceprint.npy"
            np.save(str(prof), np.ones(512, dtype=np.float32))

            # Simulate sherpa_onnx missing / extractor failed to initialize
            with patch("core.voice_verifier.sherpa_onnx", None):
                v = VoiceProfileVerifier(model_path=str(Path(tmpdir) / "missing.onnx"), profile_path=str(prof))
                self.assertFalse(v.is_enrolled())
                is_match, score = v.verify(_make_wav_bytes(0.75))
                self.assertFalse(is_match, "Must fail CLOSED when voiceprint exists on disk but verifier is uninitialized")
                self.assertEqual(score, 0.0)

    def test_voice_verifier_short_clip_floor_blocks_low_similarity(self):
        from core.voice_verifier import VoiceProfileVerifier

        with tempfile.TemporaryDirectory() as tmpdir:
            v = VoiceProfileVerifier(profile_path=str(Path(tmpdir) / "voice.npy"))
            ref = np.zeros(512, dtype=np.float32)
            ref[0] = 1.0
            v.enrolled_embedding = ref
            v._is_initialized = True
            v.extractor = MagicMock()

            # Low similarity 0.25 must be REJECTED (previously passed due to stacked relaxations down to ~0.23!)
            low_cand = np.zeros(512, dtype=np.float32)
            low_cand[0] = 0.25
            low_cand[1] = float(np.sqrt(1.0 - 0.25**2))

            with patch.object(v, "_compute_stream_embedding", return_value=low_cand):
                is_match, score = v.verify(_make_wav_bytes(0.75), threshold=0.40)
                self.assertFalse(is_match, f"0.25 similarity must be rejected, got match with score={score}")
                self.assertLess(score, 0.30)
            self.assertFalse(hasattr(v, "_last_voiced_duration_sec"))


class TestBatch4LiveSTT(unittest.IsolatedAsyncioTestCase):
    async def test_live_stt_turn2_audio_not_wiped_by_turn1_finish_and_ctx_closed(self):
        from core.stt_service import ModularLiveSTTClient

        ctx_exited = asyncio.Event()
        turn1_release = asyncio.Event()
        turn2_release = asyncio.Event()

        class FakeSession:
            async def send_realtime_input(self, **kwargs):
                return None

            async def receive(self):
                await turn1_release.wait()
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        interim_input_transcription=None,
                        input_transcription=SimpleNamespace(text="hello turn one"),
                        generation_complete=True,
                    )
                )
                await turn2_release.wait()
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        interim_input_transcription=None,
                        input_transcription=SimpleNamespace(text="second turn words"),
                        generation_complete=True,
                    )
                )

        class FakeCtx:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, exc_type, exc, tb):
                ctx_exited.set()

        fake_client = MagicMock()
        fake_client.aio.live.connect.return_value = FakeCtx()

        stt = ModularLiveSTTClient(fake_client)
        # Turn 1 streams & ends
        await stt.send_pcm(b"\x01\x02" * 160)
        await stt.signal_end_of_turn()

        # Turn 2 begins streaming BEFORE Turn 1 generation_complete arrives!
        await stt.send_pcm(b"\x03\x04" * 160)

        # Release Turn 1 completion and finish Turn 1
        turn1_release.set()
        t1_text = await stt.finish_turn(timeout=1.0)
        self.assertEqual(t1_text, "hello turn one")

        # Turn 2 audio MUST still be intact!
        self.assertTrue(stt._has_streamed_audio)
        await stt.signal_end_of_turn()
        turn2_release.set()
        t2_text = await stt.finish_turn(timeout=1.0)
        self.assertEqual(t2_text, "second turn words")

        # Stream ends naturally -> _receive_loop finally must clean up FakeCtx.__aexit__
        await asyncio.wait_for(ctx_exited.wait(), timeout=1.0)
        self.assertIsNone(stt._ctx)
        self.assertIsNone(stt._session)


class TestBatch4UserMemoryAndProactive(unittest.IsolatedAsyncioTestCase):
    def test_dual_unique_constraints_no_integrity_error_and_replace_preserves_ids(self):
        from core.user_memory import UserMemory, add_fact_batch, replace_facts

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = os.path.join(tmpdir, "mem.db")
            mem = UserMemory(db_path=db)
            try:
                # 1. Dual UNIQUE collision: same fact text via different category/key
                mem.remember_fact("dates", "mom_birthday", "1960-05-12", data_type="date")
                dates_before = mem.get_facts(category="dates")
                orig_id = dates_before[0]["id"]

                # add_fact_batch with same (category, key) but updated fact text -> must NOT raise IntegrityError
                add_fact_batch(
                    [{"category": "dates", "key": "mom_birthday", "fact": "mom_birthday: May 12, 1960"}],
                    source_session="sess_1",
                    db_path=db,
                )
                # remember_fact producing same fact text under a different category -> must NOT raise IntegrityError
                mem.remember_fact("general", "dup_test", "hello")
                add_fact_batch(
                    [{"category": "work", "key": "other_key", "fact": "dup_test: hello"}],
                    source_session="sess_2",
                    db_path=db,
                )
                res = mem.remember_fact("general", "dup_test", "hello")
                self.assertEqual(res["status"], "success")

                # 2. replace_facts must preserve SQLite ID of protected 'dates' row
                replace_facts([{"category": "general", "fact": "User likes coffee"}], db_path=db)
                dates_after = mem.get_facts(category="dates")
                self.assertEqual(len(dates_after), 1)
                self.assertEqual(dates_after[0]["id"], orig_id, "replace_facts must preserve dates row ID!")
            finally:
                if mem._conn is not None:
                    mem._conn.close()

    async def test_proactive_invalid_date_and_oauth_dedup_across_restart(self):
        from core.proactive_engine import parse_date_event, ProactiveEngine
        from core.user_memory import UserMemory

        self.assertIsNone(parse_date_event("2026-02-30"))
        self.assertIsNone(parse_date_event("13-45"))
        self.assertIsNone(parse_date_event("04-31"))
        self.assertEqual(parse_date_event("2024-02-29"), (2, 29))

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = os.path.join(tmpdir, "mem.db")
            mem = UserMemory(db_path=db)
            mem2 = None
            try:
                eng = ProactiveEngine(memory=mem)
                cand = {
                    "type": "oauth_calendar",
                    "fact_id": None,
                    "dedup_key": "oauth_calendar:google:evt_123:2026-11-20T15:00:00-05:00",
                    "category": "calendar",
                    "key": "Dentist",
                    "value": "2026-11-20T15:00:00-05:00",
                    "days_away": 0,
                    "timing_text": "scheduled for 2026-11-20T15:00:00-05:00",
                    "description": "Upcoming calendar event 'Dentist'",
                }
                with patch.object(eng, "evaluate_candidates", return_value=[cand]):
                    res = await eng.check_proactive_briefing(user_name="Michael", genai_client=None)
                    self.assertIsNotNone(res)

                # A fresh ProactiveEngine / UserMemory instance (simulating app restart) must see it already fired today!
                mem2 = UserMemory(db_path=db)
                self.assertTrue(mem2.has_prompt_fired_today(prompt_text=cand["dedup_key"]))
            finally:
                if mem._conn is not None:
                    mem._conn.close()
                if mem2 is not None and mem2._conn is not None:
                    mem2._conn.close()


class TestBatch4OAuthImapAndEmailMonitor(unittest.IsolatedAsyncioTestCase):
    def test_imap_uses_uids_and_extracts_body_snippet(self):
        from core.oauth_manager import OAuthManager

        mgr = OAuthManager.__new__(OAuthManager)
        mgr.vault = MagicMock()
        mgr.vault.get_provider_tokens.return_value = {
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "account_email": "me@example.com",
            "app_password": "secret",
        }

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.__exit__.return_value = False
        fake_conn.uid.side_effect = [
            ("OK", [b"42"]),
            ("OK", [
                (b"1 (UID 42 RFC822.HEADER {60})", b"From: Alice <alice@example.com>\r\nSubject: Status\r\nDate: Fri, 20 Nov 2026 10:00:00 +0000\r\n\r\n"),
                (b"BODY[TEXT]<0> {32}", b"Actual email body snippet here!!"),
                b")",
            ]),
        ]

        with patch("core.oauth_manager.imaplib.IMAP4_SSL", return_value=fake_conn):
            res = mgr._imap_list_messages(max_results=5, query="", unread_only=True)

        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["messages"][0]["message_id"], "42")
        self.assertIn("Actual email body snippet here!!", res["messages"][0]["snippet"])
        # Confirm M.uid was used, not volatile sequence M.search/M.fetch
        fake_conn.search.assert_not_called()
        fake_conn.fetch.assert_not_called()
        self.assertEqual(fake_conn.uid.call_args_list[0].args[0], "search")
        self.assertEqual(fake_conn.uid.call_args_list[1].args[0], "fetch")

    def test_outlook_calendar_converts_utc_to_local_and_contacts_paginate(self):
        from core.oauth_manager import _normalize_graph_datetime, _parse_event_local_date, OAuthManager

        # Naive UTC string with 7 fractional digits -> converted to local tz-aware ISO
        norm = _normalize_graph_datetime({"dateTime": "2026-11-21T02:00:00.0000000", "timeZone": "UTC"})
        dt_norm = datetime.datetime.fromisoformat(norm)
        self.assertIsNotNone(dt_norm.tzinfo)
        expected_local = datetime.datetime(2026, 11, 21, 2, 0, tzinfo=datetime.timezone.utc).astimezone()
        self.assertEqual(dt_norm.date(), expected_local.date())
        self.assertEqual(_parse_event_local_date(norm), expected_local.date())

        # Outlook contacts pagination beyond 25
        mgr = OAuthManager.__new__(OAuthManager)
        page1 = MagicMock(ok=True)
        page1.json.return_value = {
            "value": [{"id": f"c{i}", "displayName": f"Person {i}", "emailAddresses": []} for i in range(30)],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/contacts?$skip=30",
        }
        page2 = MagicMock(ok=True)
        page2.json.return_value = {
            "value": [{"id": "c_target", "displayName": "Zelda Target", "emailAddresses": [{"address": "zelda@example.com"}]}],
        }
        with patch.object(mgr, "_microsoft_headers", return_value={"Authorization": "Bearer tok"}), \
             patch("core.oauth_manager.requests.get", side_effect=[page1, page2]) as mock_get:
            res = mgr._microsoft_search_contacts("Zelda")

        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["matches"][0]["name"], "Zelda Target")
        self.assertEqual(mock_get.call_args_list[0].kwargs["params"]["$top"], 200)

    async def test_email_monitor_wraps_untrusted_for_llm_and_blocks_backlog_storm(self):
        from core.email_monitor import EmailMonitorService
        from core.oauth_manager import wrap_untrusted_email

        captured_prompts = []
        fake_client = MagicMock()
        def _fake_gen(**kwargs):
            captured_prompts.append(kwargs["contents"])
            return SimpleNamespace(text="Hey Michael, Pam just emailed you about the closing.")
        fake_client.models.generate_content.side_effect = _fake_gen

        # 50 primed unread messages (saturating priming window), oldest at 2026-11-18T12:00:00Z
        primed_msgs = [
            {
                "message_id": f"old_{i}",
                "thread_id": f"old_{i}",
                "from": "Sender <s@example.com>",
                "subject": f"Old {i}",
                "snippet": "Old body",
                "date": f"2026-11-19T{10 + (i % 10):02d}:00:00Z" if i < 49 else "2026-11-18T12:00:00Z",
            }
            for i in range(50)
        ]
        # Second poll: an older backlog email (#51 from Nov 17) slid into the window, plus 1 genuinely new email!
        wrapped_snip = wrap_untrusted_email("Pam <pam@ex.com>", "Closing", "2026-11-20T10:00:00Z", "Quick question on closing", "new_1", "new_1")
        poll2_msgs = [
            {
                "message_id": "new_1",
                "thread_id": "new_1",
                "from": "Pam <pam@ex.com>",
                "subject": "Closing",
                "snippet": wrapped_snip,
                "date": "2026-11-20T10:00:00Z",
            },
            {
                "message_id": "backlog_51",
                "thread_id": "backlog_51",
                "from": "Oldie <old@ex.com>",
                "subject": "Ancient Backlog",
                "snippet": "Should NOT alert!",
                "date": "2026-11-17T08:00:00Z",
            },
        ]

        fake_mgr = MagicMock()
        fake_mgr.get_status.return_value = {"google": {"connected": True}}
        fake_mgr.list_messages.side_effect = [
            {"success": True, "messages": primed_msgs},
            {"success": True, "messages": poll2_msgs},
        ]

        monitor = EmailMonitorService(
            summarize_with_ai=True,
            oauth_manager_getter=lambda: fake_mgr,
            user_name_getter=lambda: "Michael",
            client_getter=lambda: fake_client,
            fast_model_getter=lambda: "gemini-3.8-flash",
        )

        self.assertEqual(await monitor.poll_once(), [])
        alerts = await monitor.poll_once()
        # Only new_1 alerts; backlog_51 is suppressed!
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].message_id, "new_1")
        self.assertEqual(alerts[0].snippet, "Quick question on closing")
        # LLM prompt wrapped untrusted metadata cleanly in <untrusted_external_email ...>
        self.assertEqual(len(captured_prompts), 1)
        self.assertIn("<untrusted_external_email", captured_prompts[0])
        self.assertIn("</untrusted_external_email>", captured_prompts[0])


if __name__ == "__main__":
    unittest.main()
