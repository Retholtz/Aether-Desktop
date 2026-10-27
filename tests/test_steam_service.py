import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from core.steam_service import SteamService
from tools.steam_tools import register_steam_tools
from core.game_engine import GameEngine


class TestSteamService(unittest.TestCase):
    def setUp(self):
        self.service = SteamService(api_key="TEST_KEY", steam_id64="76561198000000000")

    def tearDown(self):
        # Synchronously cleanup session loop
        import asyncio
        if self.service._session and not self.service._session.closed:
            asyncio.run(self.service.close())

    @patch("aiohttp.ClientSession.get")
    def test_friends_activity_parsing(self, mock_get):
        # Mock friend list call followed by summary call
        mock_friends_resp = AsyncMock()
        mock_friends_resp.status = 200
        mock_friends_resp.json = AsyncMock(return_value={
            "friendslist": {"friends": [{"steamid": "12345"}]}
        })

        mock_summary_resp = AsyncMock()
        mock_summary_resp.status = 200
        mock_summary_resp.json = AsyncMock(return_value={
            "response": {
                "players": [{
                    "steamid": "12345",
                    "personaname": "Viper",
                    "personastate": 1,
                    "gameextrainfo": "Elite Dangerous",
                    "gameid": "359320"
                }]
            }
        })

        mock_get.side_effect = [mock_friends_resp, mock_summary_resp]

        import asyncio
        results = asyncio.run(self.service.get_friends_activity())

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].persona_name, "Viper")
        self.assertEqual(results[0].game_title, "Elite Dangerous")

    @patch("aiohttp.ClientSession.get")
    def test_achievements_percentage_calculation(self, mock_get):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "playerstats": {
                "success": True,
                "achievements": [
                    {"apiname": "ACH_01", "achieved": 1},
                    {"apiname": "ACH_02", "achieved": 0}
                ]
            }
        })
        mock_get.return_value = mock_resp

        import asyncio
        summary = asyncio.run(self.service.get_player_achievements(359320))
        self.assertIsNotNone(summary)
        self.assertEqual(summary.total_achievements, 2)
        self.assertEqual(summary.unlocked_count, 1)
        self.assertEqual(summary.completion_percentage, 50.0)

    @patch("aiohttp.ClientSession.get")
    def test_recent_news_html_and_bbcode_stripping(self, mock_get):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "appnews": {
                "newsitems": [
                    {
                        "title": "Update 18.0",
                        "url": "https://steamcommunity.com/news/18",
                        "date": 1700000000,
                        "contents": "<b>Patch deployed!</b> [b]New features[/b] <a href='https://foo'>details</a>"
                    }
                ]
            }
        })
        mock_get.return_value = mock_resp

        import asyncio
        news = asyncio.run(self.service.get_recent_news(359320, count=1))
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["title"], "Update 18.0")
        self.assertNotIn("<b>", news[0]["summary"])
        self.assertNotIn("[b]", news[0]["summary"])
        self.assertIn("Patch deployed!", news[0]["summary"])

    def test_unconfigured_service_safeguards(self):
        unconfigured = SteamService(api_key="", steam_id64="")
        self.assertFalse(unconfigured.is_configured())

        import asyncio
        friends = asyncio.run(unconfigured.get_friends_activity())
        self.assertEqual(friends, [])
        achievements = asyncio.run(unconfigured.get_player_achievements(359320))
        self.assertIsNone(achievements)

    def test_game_engine_core_tools_contain_steam_tools(self):
        self.assertIn("get_steam_friends_activity", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("get_steam_friend_games", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("get_steam_owned_games", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("get_steam_game_achievements", GameEngine.CORE_GAME_TOOL_NAMES)
        self.assertIn("get_steam_patch_notes", GameEngine.CORE_GAME_TOOL_NAMES)

    def test_steam_tools_registration_and_appid_resolution(self):
        import asyncio

        mock_dispatcher = MagicMock()
        mock_dispatcher.register_tool = MagicMock()

        # Mock game_engine with active profile
        mock_game_engine = MagicMock()
        mock_game_engine.manager.data = {"active_profile": "elite_dangerous"}
        mock_game_engine.manager.get_profile.return_value = {
            "display_name": "Elite Dangerous",
            "steam_appid": 359320
        }

        # Mock SteamService methods
        from core.steam_service import SteamFriendSummary
        mock_service = MagicMock(spec=SteamService)
        mock_service.is_configured.return_value = True
        mock_service.get_friends_activity = AsyncMock(return_value=[
            SteamFriendSummary(steamid="101", persona_name="Rainmaker", status="Offline")
        ])
        mock_service.resolve_friend = AsyncMock(return_value=SteamFriendSummary(
            steamid="101", persona_name="Rainmaker", status="Offline"
        ))
        mock_service.get_owned_games = AsyncMock(return_value=[
            {"appid": 105600, "name": "Terraria", "playtime_hours": 12.5}
        ])
        mock_service.get_recently_played_games = AsyncMock(return_value=[])
        mock_service.get_player_achievements = AsyncMock(return_value=None)
        mock_service.get_recent_news = AsyncMock(return_value=[{"title": "Note 1"}])

        tools = register_steam_tools(mock_dispatcher, mock_service, game_engine=mock_game_engine)
        self.assertIn("get_steam_friends_activity", tools)
        self.assertIn("get_steam_friend_games", tools)
        self.assertIn("get_steam_owned_games", tools)
        self.assertIn("get_steam_game_achievements", tools)
        self.assertIn("get_steam_patch_notes", tools)

        # Call patch notes without explicit appid -> should infer 359320 from active_profile
        res = asyncio.run(tools["get_steam_patch_notes"]())
        mock_service.get_recent_news.assert_called_with(359320, count=2)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["appid"], 359320)

        # Call get_steam_friend_games for Rainmaker -> should return Rainmaker's owned games
        fg_res = asyncio.run(tools["get_steam_friend_games"](friend="Rainmaker"))
        self.assertEqual(fg_res["status"], "success")
        self.assertEqual(fg_res["friend_name"], "Rainmaker")
        self.assertEqual(fg_res["owned_games"][0]["name"], "Terraria")

    def test_friend_status_change_detection(self):
        import asyncio
        from core.steam_service import SteamFriendSummary

        # Baseline: Styx is Offline, Viper is Online in Elite
        f1_off = SteamFriendSummary(steamid="101", persona_name="Styx", status="Offline", game_title=None)
        f2_on = SteamFriendSummary(steamid="102", persona_name="Viper", status="Online", game_title="Elite Dangerous")

        with patch.object(self.service, "get_friends_activity", new_callable=AsyncMock) as mock_get_friends:
            # Poll 1 (Baseline seeding): should report NO changes
            mock_get_friends.return_value = [f1_off, f2_on]
            changes1 = asyncio.run(self.service.check_friends_status_changes())
            self.assertEqual(changes1, [])

            # Poll 2: Styx is still Offline, Viper still Online in Elite -> NO changes
            changes2 = asyncio.run(self.service.check_friends_status_changes())
            self.assertEqual(changes2, [])

            # Poll 3: Styx starts playing Crimson Desert!
            f1_playing = SteamFriendSummary(steamid="101", persona_name="Styx", status="Online", game_title="Crimson Desert")
            mock_get_friends.return_value = [f1_playing, f2_on]
            changes3 = asyncio.run(self.service.check_friends_status_changes())
            self.assertEqual(len(changes3), 1)
            self.assertEqual(changes3[0]["steamid"], "101")
            self.assertIn("Styx is now playing Crimson Desert.", changes3[0]["message"])

            # Poll 4: Styx stops playing but remains online
            f1_online = SteamFriendSummary(steamid="101", persona_name="Styx", status="Online", game_title=None)
            mock_get_friends.return_value = [f1_online, f2_on]
            changes4 = asyncio.run(self.service.check_friends_status_changes())
            self.assertEqual(len(changes4), 1)
            self.assertIn("Styx stopped playing Crimson Desert.", changes4[0]["message"])

            # Poll 5: Styx goes offline
            mock_get_friends.return_value = [f1_off, f2_on]
            changes5 = asyncio.run(self.service.check_friends_status_changes())
            self.assertEqual(len(changes5), 1)
            self.assertIn("Styx is now offline.", changes5[0]["message"])

    def test_friend_polling_controls(self):
        self.service.set_friend_polling(enabled=True, interval_minutes=10)
        self.assertTrue(self.service.poll_friends_status)
        self.assertEqual(self.service.poll_interval_minutes, 10)

        self.service.set_friend_polling(enabled=False)
        self.assertFalse(self.service.poll_friends_status)
        self.assertFalse(self.service._poll_worker_running)


if __name__ == "__main__":
    unittest.main()

