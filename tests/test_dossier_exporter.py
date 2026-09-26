import os
import unittest
from tools.dossier_exporter import export_research_dossier, DOSSIER_DIR, LATEST_DOSSIER_LINK

class TestDossierExporter(unittest.TestCase):
    def test_export_file_creation_and_content(self):
        title = "Test Family Tree: Dr. Smith"
        markdown_body = "### Immediate Relatives\n- Brother: John\n- Sister: Mary\n- Location: Ohio"
        
        result = export_research_dossier(
            title=title,
            content_markdown=markdown_body,
            category="genealogy",
            open_immediately=False
        )

        self.assertEqual(result["status"], "success")
        file_path = result["file_path"]
        self.assertTrue(os.path.exists(file_path))

        # Check content in saved file
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Test Family Tree: Dr. Smith", content)
            self.assertIn("Brother: John", content)

        # Check scratchpad link
        self.assertTrue(os.path.exists(LATEST_DOSSIER_LINK))
        with open(LATEST_DOSSIER_LINK, "r", encoding="utf-8") as f:
            scratchpad_content = f.read()
            self.assertEqual(content, scratchpad_content)

        # Clean up test artifact
        try:
            os.remove(file_path)
        except OSError:
            pass

if __name__ == "__main__":
    unittest.main()
