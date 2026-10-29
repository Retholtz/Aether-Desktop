import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from core.livestream_service import LiveStreamService, LiveStreamInfo
from tools.livestream_tools import register_livestream_tools


class TestLiveStreamService(unittest.TestCase):
    def setUp(self):
        self.callback_mock = MagicMock()
        self.service = LiveStreamService(
            twitch_client_id="MOCK_CLIENT_ID",
            twitch_client_secret="MOCK_SECRET",
            youtube_api_key="MOCK_YT_KEY",
            on_stream_went_live=self.callback_mock
        )

    def tearDown(self):
        if self.service._session and not self.service._session.closed:
            asyncio.run(self.service.close())

    @patch("aiohttp.ClientSession.post")
    @patch("aiohttp.ClientSession.get")
    def test_twitch_streams_parsing_and_edge_detection(self, mock_get, mock_post):
        # 1. Mock OAuth Token Response
        mock_token_resp = AsyncMock()
        mock_token_resp.status = 200
        mock_token_resp.json = AsyncMock(return_value={"access_token": "ACCESS_123", "expires_in": 3600})
        mock_post.return_value = mock_token_resp

        # 2. Mock Helix Streams Response (Channel live)
        mock_streams_resp = AsyncMock()
        mock_streams_resp.status = 200
        mock_streams_resp.json = AsyncMock(return_value={
            "data": [{
                "user_login": "cohhcarnage",
                "user_name": "CohhCarnage",
                "user_id": "123456",
                "title": "Playing RPGs!",
                "game_name": "Crimson Desert",
                "viewer_count": 8500,
                "started_at": "2026-10-29T12:00:00Z"
            }]
        })
        mock_get.return_value = mock_streams_resp

        # Pre-seed tracker state as False (offline) to test edge detection
        self.service._channel_states["twitch:cohhcarnage"] = False

        results = asyncio.run(self.service.get_twitch_streams(["cohhcarnage"]))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_live)
        self.assertEqual(results[0].game_or_category, "Crimson Desert")
        self.assertEqual(results[0].viewer_count, 8500)

        # Confirm transition callback executed
        self.callback_mock.assert_called_once()
        called_arg = self.callback_mock.call_args[0][0]
        self.assertEqual(called_arg.channel_name, "CohhCarnage")

    @patch("aiohttp.ClientSession.get")
    def test_youtube_live_parsing(self, mock_get):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "items": [{
                "id": {"videoId": "VID_999"},
                "snippet": {
                    "channelTitle": "SpaceNews",
                    "title": "Live Rocket Launch",
                    "publishedAt": "2026-10-29T10:00:00Z"
                }
            }]
        })
        mock_get.return_value = mock_resp

        res = asyncio.run(self.service.check_youtube_channel_live("UC_SPACE_1", "SpaceNews"))
        self.assertTrue(res.is_live)
        self.assertIn("watch?v=VID_999", res.url)
        self.assertEqual(res.title, "Live Rocket Launch")

    @patch("aiohttp.ClientSession.post")
    @patch("aiohttp.ClientSession.get")
    def test_twitch_streams_offline_and_no_edge(self, mock_get, mock_post):
        mock_token_resp = AsyncMock()
        mock_token_resp.status = 200
        mock_token_resp.json = AsyncMock(return_value={"access_token": "ACCESS_123", "expires_in": 3600})
        mock_post.return_value = mock_token_resp

        mock_streams_resp = AsyncMock()
        mock_streams_resp.status = 200
        mock_streams_resp.json = AsyncMock(return_value={"data": []})
        mock_get.return_value = mock_streams_resp

        results = asyncio.run(self.service.get_twitch_streams(["shroud"]))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_live)
        self.assertEqual(results[0].channel_name, "shroud")
        self.callback_mock.assert_not_called()

    @patch("aiohttp.ClientSession.post")
    @patch("aiohttp.ClientSession.get")
    def test_steady_state_suppression(self, mock_get, mock_post):
        mock_token_resp = AsyncMock()
        mock_token_resp.status = 200
        mock_token_resp.json = AsyncMock(return_value={"access_token": "ACCESS_123", "expires_in": 3600})
        mock_post.return_value = mock_token_resp

        mock_streams_resp = AsyncMock()
        mock_streams_resp.status = 200
        mock_streams_resp.json = AsyncMock(return_value={
            "data": [{
                "user_login": "cohhcarnage",
                "user_name": "CohhCarnage",
                "user_id": "123456",
                "title": "Still Playing!",
                "game_name": "Crimson Desert",
                "viewer_count": 9000
            }]
        })
        mock_get.return_value = mock_streams_resp

        # Pre-seed tracker state as True (already live)
        self.service._channel_states["twitch:cohhcarnage"] = True

        results = asyncio.run(self.service.get_twitch_streams(["cohhcarnage"]))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].is_live)
        # Should NOT trigger callback again because it was already live
        self.callback_mock.assert_not_called()

    @patch("aiohttp.ClientSession.get")
    def test_youtube_offline(self, mock_get):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"items": []})
        mock_get.return_value = mock_resp

        res = asyncio.run(self.service.check_youtube_channel_live("UC_OFFLINE_1", "OfflineChannel"))
        self.assertFalse(res.is_live)
        self.assertEqual(res.channel_name, "OfflineChannel")
        self.assertEqual(res.url, "https://youtube.com/channel/UC_OFFLINE_1")
        self.callback_mock.assert_not_called()

    def test_tools_registration_and_execution(self):
        registered_tools = {}

        class DummyDispatcher:
            def register_tool(self, name, declaration=None, handler=None, **kwargs):
                registered_tools[name] = handler or kwargs.get("handler")

        dispatcher = DummyDispatcher()
        register_livestream_tools(
            dispatcher,
            self.service,
            watched_twitch=["streamer1"],
            watched_youtube=[{"name": "YT Channel", "channel_id": "UC_YT_1"}]
        )

        self.assertIn("get_live_stream_status", registered_tools)
        tool_fn = registered_tools["get_live_stream_status"]

        with patch.object(self.service, "get_twitch_streams", new_callable=AsyncMock) as mock_tw, \
             patch.object(self.service, "check_youtube_channel_live", new_callable=AsyncMock) as mock_yt:

            mock_tw.return_value = [
                LiveStreamInfo(platform="twitch", channel_name="streamer1", channel_id="111", is_live=True, title="Live!", game_or_category="RPG")
            ]
            mock_yt.return_value = LiveStreamInfo(platform="youtube", channel_name="YT Channel", channel_id="UC_YT_1", is_live=False)

            result = asyncio.run(tool_fn())
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["total_queried"], 2)
            self.assertEqual(result["live_count"], 1)
            self.assertEqual(len(result["live_streams"]), 1)
            self.assertEqual(result["live_streams"][0]["channel_name"], "streamer1")
            self.assertEqual(len(result["offline_streams"]), 1)

    def test_engine_callback_and_verbal_alerts(self):
        from unittest.mock import MagicMock
        from core.engine import AetherEngine

        with patch.object(AetherEngine, "__init__", lambda self, *args, **kwargs: None):
            engine = AetherEngine()
            engine.settings = {
                "integrations": {
                    "streaming": {
                        "enabled": True,
                        "verbal_alerts": True
                    }
                }
            }
            engine.notify = MagicMock()
            engine.queue_whisper = MagicMock()
            engine.audio = None
            engine._is_speaking = False

            stream = LiveStreamInfo(
                platform="twitch",
                channel_name="CohhCarnage",
                channel_id="123456",
                is_live=True,
                game_or_category="Crimson Desert"
            )

            # Test verbal alert enabled
            engine._handle_stream_went_live(stream)
            engine.notify.assert_called_once_with("livestream_alert", stream.to_dict())
            engine.queue_whisper.assert_called_once_with("CohhCarnage is now live on Twitch playing Crimson Desert.")

            # Test verbal alert suppressed when already speaking
            engine.notify.reset_mock()
            engine.queue_whisper.reset_mock()
            engine._is_speaking = True
            engine._handle_stream_went_live(stream)
            engine.notify.assert_called_once_with("livestream_alert", stream.to_dict())
            engine.queue_whisper.assert_not_called()

    def test_engine_poll_worker_cycle(self):
        from unittest.mock import MagicMock
        from core.engine import AetherEngine

        with patch.object(AetherEngine, "__init__", lambda self, *args, **kwargs: None):
            engine = AetherEngine()
            engine.settings = {
                "integrations": {
                    "streaming": {
                        "enabled": True,
                        "poll_interval_seconds": 1,
                        "twitch": {"channels": ["streamer1"]},
                        "youtube": {"channels": [{"name": "YT", "channel_id": "UC_YT_1"}]}
                    }
                }
            }
            engine.livestream_service = MagicMock()
            engine.livestream_service.has_twitch.return_value = True
            engine.livestream_service.has_youtube.return_value = True
            engine.livestream_service.get_twitch_streams = AsyncMock()
            engine.livestream_service.check_youtube_channel_live = AsyncMock()

            async def run_poll_and_cancel():
                task = asyncio.create_task(engine._livestream_poll_worker())
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(run_poll_and_cancel())
            engine.livestream_service.get_twitch_streams.assert_called_with(["streamer1"])
            engine.livestream_service.check_youtube_channel_live.assert_called_with("UC_YT_1", "YT")

    def test_watchlist_management_tools(self):
        registered_tools = {}

        class DummyDispatcher:
            def register_tool(self, name, declaration=None, handler=None, **kwargs):
                registered_tools[name] = handler or kwargs.get("handler")

        dispatcher = DummyDispatcher()
        mock_engine = MagicMock()
        mock_engine.settings = {
            "integrations": {
                "streaming": {
                    "enabled": True,
                    "twitch": {"channels": ["cohhcarnage"]},
                    "youtube": {"channels": [{"name": "SpaceX", "channel_id": "UC_SPACEX"}]}
                }
            }
        }

        register_livestream_tools(
            dispatcher,
            self.service,
            engine=mock_engine,
            watched_twitch=["cohhcarnage"],
            watched_youtube=[{"name": "SpaceX", "channel_id": "UC_SPACEX"}]
        )

        add_tool = registered_tools["add_to_stream_watchlist"]
        remove_tool = registered_tools["remove_from_stream_watchlist"]
        list_tool = registered_tools["list_stream_watchlist"]

        # 1. List
        res_list = asyncio.run(list_tool())
        self.assertEqual(res_list["status"], "success")
        self.assertIn("cohhcarnage", res_list["platforms"]["twitch"])

        # 2. Add Twitch channel
        res_add_tw = asyncio.run(add_tool(platform="twitch", channel_name="shroud"))
        self.assertEqual(res_add_tw["status"], "success")
        self.assertIn("shroud", mock_engine.settings["integrations"]["streaming"]["twitch"]["channels"])

        # 3. Add YouTube channel
        res_add_yt = asyncio.run(add_tool(platform="youtube", channel_name="UC_NASA", display_name="NASA"))
        self.assertEqual(res_add_yt["status"], "success")
        yt_cids = [c["channel_id"] for c in mock_engine.settings["integrations"]["streaming"]["youtube"]["channels"]]
        self.assertIn("UC_NASA", yt_cids)

        # 4. Remove Twitch channel
        res_rem_tw = asyncio.run(remove_tool(platform="twitch", channel_name="cohhcarnage"))
        self.assertEqual(res_rem_tw["status"], "success")
        self.assertNotIn("cohhcarnage", mock_engine.settings["integrations"]["streaming"]["twitch"]["channels"])

        # 5. Remove YouTube channel
        res_rem_yt = asyncio.run(remove_tool(platform="youtube", channel_name="UC_SPACEX"))
        self.assertEqual(res_rem_yt["status"], "success")
        yt_cids_after = [c["channel_id"] for c in mock_engine.settings["integrations"]["streaming"]["youtube"]["channels"]]
        self.assertNotIn("UC_SPACEX", yt_cids_after)

    def test_gui_bridge_streaming_methods(self):
        import tempfile
        import os
        from core.gui_bridge import GuiBridge

        temp_dir = tempfile.mkdtemp()
        cfg_path = os.path.join(temp_dir, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write('{"agent_name": "Aether"}')

        loop = asyncio.new_event_loop()
        try:
            bridge = GuiBridge(config_path=cfg_path, loop=loop)
            mock_engine = MagicMock()
            mock_engine.settings = {
                "integrations": {
                    "streaming": {
                        "enabled": True,
                        "poll_interval_seconds": 120,
                        "verbal_alerts": False,
                        "twitch": {"client_id": "test_id", "client_secret": "test_secret", "channels": ["streamer_a"]},
                        "youtube": {"api_key": "yt_key_123", "channels": [{"name": "YT", "channel_id": "UC_123"}]}
                    }
                }
            }
            bridge._engine = mock_engine

            # 1. Get status
            status = bridge.get_streaming_status()
            self.assertTrue(status["success"])
            self.assertEqual(status["twitch_channels"], ["streamer_a"])
            self.assertTrue(status["has_twitch_id"])

            # 2. Add channel via bridge
            add_res = bridge.add_stream_channel("twitch", "streamer_b")
            self.assertTrue(add_res["success"])
            self.assertIn("streamer_b", add_res["status"]["twitch_channels"])

            # 3. Remove channel via bridge
            rem_res = bridge.remove_stream_channel("twitch", "streamer_a")
            self.assertTrue(rem_res["success"])
            self.assertNotIn("streamer_a", rem_res["status"]["twitch_channels"])

            # 4. Save config
            save_res = bridge.save_streaming_config(verbal_alerts=True, poll_interval_seconds=60)
            self.assertTrue(save_res["verbal_alerts"])
            self.assertEqual(save_res["poll_interval_seconds"], 60)

            # 5. Check live streams now via bridge
            mock_engine.livestream_service = self.service
            with patch.object(self.service, "get_twitch_streams", new_callable=AsyncMock) as mock_tw:
                mock_tw.return_value = [
                    LiveStreamInfo(platform="twitch", channel_name="streamer_b", channel_id="444", is_live=True)
                ]
                check_res = bridge.check_live_streams_now("twitch")
                self.assertTrue(check_res["success"])
                self.assertEqual(len(check_res["live_streams"]), 1)
                self.assertEqual(check_res["live_streams"][0]["channel_name"], "streamer_b")

            # 6. Test sync methods on GuiBridge
            with patch.object(mock_engine, "sync_twitch_follows_to_watchlist", return_value={"success": True, "added_count": 2, "total_count": 3}) as mock_s_tw:
                sync_tw_res = bridge.sync_twitch_follows()
                self.assertTrue(sync_tw_res["success"])
                self.assertEqual(sync_tw_res["added_count"], 2)

            with patch.object(mock_engine, "sync_youtube_subscriptions_to_watchlist", return_value={"success": True, "added_count": 1, "total_count": 1}) as mock_s_yt:
                sync_yt_res = bridge.sync_youtube_subscriptions()
                self.assertTrue(sync_yt_res["success"])
                self.assertEqual(sync_yt_res["added_count"], 1)
        finally:
            loop.close()

    @patch("aiohttp.ClientSession.get")
    def test_youtube_oauth_token_usage(self, mock_get):
        service = LiveStreamService(
            youtube_api_key="",
            google_token_provider=lambda: "MOCK_GOOGLE_OAUTH_TOKEN"
        )
        self.assertTrue(service.has_youtube())

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"items": []})
        mock_get.return_value = mock_resp

        try:
            res = asyncio.run(service.check_youtube_channel_live("UC12345", "TestYT"))
            self.assertFalse(res.is_live)
            # Verify Authorization header was sent with Bearer token
            call_kwargs = mock_get.call_args[1]
            self.assertIn("headers", call_kwargs)
            self.assertEqual(call_kwargs["headers"].get("Authorization"), "Bearer MOCK_GOOGLE_OAUTH_TOKEN")
            self.assertNotIn("key", call_kwargs.get("params", {}))
        finally:
            asyncio.run(service.close())

    @patch("aiohttp.ClientSession.get")
    def test_twitch_user_token_usage(self, mock_get):
        service = LiveStreamService(
            twitch_client_id="CLIENT_ABC",
            twitch_client_secret="",
            twitch_token_provider=lambda: "MOCK_USER_OAUTH_TOKEN"
        )
        self.assertTrue(service.has_twitch())

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"data": []})
        mock_get.return_value = mock_resp

        try:
            res = asyncio.run(service.get_twitch_streams(["streamer1"]))
            self.assertEqual(len(res), 1)
            call_kwargs = mock_get.call_args[1]
            self.assertEqual(call_kwargs["headers"].get("Authorization"), "Bearer MOCK_USER_OAUTH_TOKEN")
            self.assertEqual(call_kwargs["headers"].get("Client-Id"), "CLIENT_ABC")
        finally:
            asyncio.run(service.close())

    @patch("requests.get")
    def test_oauth_manager_twitch_and_youtube_sync(self, mock_req_get):
        from core.oauth_manager import OAuthManager

        mock_vault = MagicMock()
        mock_vault.get_client_config.return_value = {"client_id": "TWITCH_CID"}
        mock_vault.get_provider_tokens.side_effect = lambda prov: {
            "twitch": {"access_token": "TW_TOK", "user_id": "999888"},
            "google": {"access_token": "GOOG_TOK", "scopes": ["https://www.googleapis.com/auth/youtube.readonly"]}
        }.get(prov)

        mgr = OAuthManager(vault=mock_vault)
        with patch.object(mgr, "get_valid_access_token", side_effect=lambda prov: "VALID_TOK_" + prov):
            # 1. Twitch Followed Channels
            def fake_get(url, **kwargs):
                r = MagicMock()
                r.ok = True
                r.status_code = 200
                if "channels/followed" in url:
                    r.json.return_value = {
                        "data": [
                            {"broadcaster_name": "Shroud", "broadcaster_login": "shroud", "broadcaster_id": "1234"},
                            {"broadcaster_name": "Cohh", "broadcaster_login": "cohhcarnage", "broadcaster_id": "5678"}
                        ]
                    }
                elif "youtube/v3/subscriptions" in url:
                    r.json.return_value = {
                        "items": [
                            {
                                "snippet": {
                                    "title": "Veritasium",
                                    "resourceId": {"channelId": "UCxxxx1"}
                                }
                            }
                        ]
                    }
                return r

            mock_req_get.side_effect = fake_get

            tw_follows = mgr.fetch_twitch_followed_channels()
            self.assertEqual(len(tw_follows), 2)
            self.assertEqual(tw_follows[0]["login"], "shroud")
            self.assertEqual(tw_follows[1]["name"], "Cohh")

            yt_subs = mgr.fetch_youtube_subscriptions()
            self.assertEqual(len(yt_subs), 1)
            self.assertEqual(yt_subs[0]["name"], "Veritasium")
            self.assertEqual(yt_subs[0]["channel_id"], "UCxxxx1")


if __name__ == "__main__":
    unittest.main()
