import unittest
from tools.skill_router import BM25CatalogRouter
from tools.dispatcher import ToolDispatcher


class TestSkillRouter(unittest.TestCase):
    def setUp(self):
        self.router = BM25CatalogRouter()
        self.dummy_skills = [
            {
                "name": "check_disk_health",
                "intent": "Check NVMe and HDD SMART health status and free capacity",
                "description": "Scans physical drives for errors and remaining disk space"
            },
            {
                "name": "organize_downloads",
                "intent": "Sort and move files in Downloads folder by extension",
                "description": "Groups pdf, images, and installer files into subdirectories"
            },
            {
                "name": "fetch_stock_quote",
                "intent": "Retrieve current price and volume for a ticker symbol",
                "description": "Fetches market quote data via financial API"
            }
        ]
        self.router.build_index(self.dummy_skills)

    def test_routing_relevance(self):
        # Prompt matching disk health
        results = self.router.query("Can you inspect my hard drive space and health?", top_k=2)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "check_disk_health")

        # Prompt matching downloads organizer
        results2 = self.router.query("Clean up and sort all the installer files in my downloads", top_k=2)
        self.assertGreaterEqual(len(results2), 1)
        self.assertEqual(results2[0]["name"], "organize_downloads")

    def test_unrelated_prompt_no_injection(self):
        # Unrelated query should not match with high score
        results = self.router.query("What is the capital of France?", top_k=2, min_score_threshold=1.5)
        self.assertEqual(len(results), 0)

    def test_dispatcher_dynamic_injection_and_zero_bloat(self):
        dispatcher = ToolDispatcher()
        dispatcher.skill_router.build_index([
            {
                "name": "backup_vault_drive",
                "intent": "Back up the vault directory to external storage",
                "description": "Copies encrypted vault folder and verifies checksums",
                "parameters": {"destination": {"type": "STRING", "description": "Target drive"}}
            }
        ])

        core_names = {d["name"] for d in dispatcher.get_core_declarations()}
        self.assertIn("inspect_screen_context", core_names)
        self.assertIn("query_user_memory", core_names)
        self.assertIn("search_past_sessions", core_names)
        self.assertIn("execute_automation_script", core_names)
        self.assertIn("send_desktop_notification", core_names)

        # 1. Matching prompt injects backup_vault_drive
        routed_vault = dispatcher.get_routed_tool_declarations("Back up the vault directory.")
        routed_vault_names = {d["name"] for d in routed_vault}
        self.assertIn("backup_vault_drive", routed_vault_names)

        # 2. General prompt injects zero dynamic catalog skills
        routed_general = dispatcher.get_routed_tool_declarations(
            "What is the difference between an asteroid and a comet?"
        )
        routed_general_names = {d["name"] for d in routed_general}
        self.assertEqual(routed_general_names, core_names)
        self.assertNotIn("backup_vault_drive", routed_general_names)


if __name__ == "__main__":
    unittest.main()
