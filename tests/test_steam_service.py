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
        mock_service = MagicMock(spec=SteamService)
        mock_service.is_configured.return_value = True
        mock_service.get_friends_activity = AsyncMock(return_value=[])
        mock_service.get_player_achievements = AsyncMock(return_value=None)
        mock_service.get_recent_news = AsyncMock(return_value=[{"title": "Note 1"}])

        tools = register_steam_tools(mock_dispatcher, mock_service, game_engine=mock_game_engine)
        self.assertIn("get_steam_friends_activity", tools)
        self.assertIn("get_steam_game_achievements", tools)
        self.assertIn("get_steam_patch_notes", tools)

        # Call patch notes without explicit appid -> should infer 359320 from active_profile
        res = asyncio.run(tools["get_steam_patch_notes"]())
        mock_service.get_recent_news.assert_called_with(359320, count=2)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["appid"], 359320)


if __name__ == "__main__":
    unittest.main()

