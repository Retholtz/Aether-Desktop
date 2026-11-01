import unittest
import os
import shutil
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock
from core.project_service import ProjectService
from core.notebooklm_service import NotebookLMService
from tools.project_tools import register_project_tools


class FakeDispatcher:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, description, parameters, handler):
        self.tools[name] = {
            "description": description,
            "parameters": parameters,
            "handler": handler
        }


class TestProjectService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.test_dir, "projects.json")
        self.service = ProjectService(base_dir=self.test_dir, manifest_path=self.manifest_path)

    async def asyncTearDown(self):
        await self.service.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_and_delete_project(self):
        res = self.service.create_project("Test Project", "Test Project Name", "Desc", "Act as coder")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["project"]["id"], "test_project")
        self.assertEqual(self.service.data["active_project"], "test_project")

        # Duplicate check
        dup = self.service.create_project("Test Project", "Again")
        self.assertEqual(dup["status"], "error")

        # Delete
        deleted = self.service.delete_project("test_project")
        self.assertTrue(deleted)
        self.assertIsNone(self.service.data["active_project"])

    def test_add_and_list_files(self):
        self.service.create_project("alpha", "Alpha")
        content = b"Hello world! This is a test document for token estimation."
        meta = self.service.add_file("alpha", "notes.txt", content, source_type="upload")

        self.assertEqual(meta["filename"], "notes.txt")
        self.assertGreater(meta["estimated_tokens"], 0)

        files = self.service.list_files("alpha")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["filename"], "notes.txt")

    @patch("aiohttp.ClientSession.get")
    async def test_import_url_markdown_conversion(self, mock_get):
        self.service.create_project("web_proj", "Web Project")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text.return_value = "<html><head><title>Test Doc</title></head><body><h1>Header</h1><p>Body text here.</p></body></html>"
        mock_get.return_value.__aenter__.return_value = mock_resp

        res = await self.service.import_url("web_proj", "https://example.com/article")
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["metadata"]["filename"].endswith(".md"))

        files = self.service.list_files("web_proj")
        self.assertEqual(len(files), 1)

    async def test_project_tools_registration_and_execution(self):
        self.service.create_project("tool_proj", "Tool Project", "Desc", "Use concise style")
        nb_service = NotebookLMService(api_key="")
        dispatcher = FakeDispatcher()
        register_project_tools(dispatcher, self.service, nb_service)

        self.assertIn("query_project_knowledge_base", dispatcher.tools)
        self.assertIn("read_project_document", dispatcher.tools)
        self.assertIn("save_project_artifact", dispatcher.tools)
        self.assertIn("import_project_url", dispatcher.tools)

        # Save an artifact via tool
        save_handler = dispatcher.tools["save_project_artifact"]["handler"]
        save_res = await save_handler("design_spec.md", "# Spec\nDetails about architecture.")
        self.assertEqual(save_res["status"], "success")
        self.assertEqual(save_res["saved"]["filename"], "design_spec.md")

        # Read document via tool
        read_handler = dispatcher.tools["read_project_document"]["handler"]
        read_res = await read_handler("design_spec.md")
        self.assertEqual(read_res["status"], "success")
        self.assertIn("# Spec", read_res["content"])

        # Query local fallback via tool
        query_handler = dispatcher.tools["query_project_knowledge_base"]["handler"]
        query_res = await query_handler("architecture")
        self.assertEqual(query_res["status"], "success")
        self.assertIn("design_spec.md", query_res["available_files"])

        await nb_service.close()

    async def test_notebooklm_service_file_search_stores(self):
        nb_service = NotebookLMService(api_key="test-key")
        mock_client = MagicMock()
        mock_store = MagicMock()
        mock_store.name = "fileSearchStores/test-store-123"
        mock_store.display_name = "Test Store"
        mock_client.file_search_stores.create.return_value = mock_store

        with patch.object(nb_service, "_get_genai_client", return_value=mock_client):
            created = await nb_service.create_notebook("Test Store")
            self.assertEqual(created["name"], "fileSearchStores/test-store-123")

            synced = await nb_service.batch_create_sources(
                "fileSearchStores/test-store-123",
                [{"displayName": "doc.md", "textContent": "# Hello"}]
            )
            self.assertTrue(synced)
            mock_client.file_search_stores.upload_to_file_search_store.assert_called_once()

        await nb_service.close()

    def test_two_way_google_drive_sync(self):
        fake_drive_root = tempfile.mkdtemp()
        try:
            self.service.create_project("drive_proj", "Drive Project")
            self.service.add_file("drive_proj", "local_doc.md", b"# Local Doc\nContent from Aether.")

            with patch.object(self.service, "find_google_drive_root", return_value=fake_drive_root):
                # 1. Push local -> Drive
                res1 = self.service.sync_with_google_drive("drive_proj")
                self.assertEqual(res1["status"], "success")
                self.assertEqual(res1["pushed"], 1)
                drive_folder = res1["drive_folder"]
                self.assertTrue(os.path.exists(os.path.join(drive_folder, "local_doc.md")))

                # 2. Add a file directly in Google Drive folder -> Pull into Aether
                with open(os.path.join(drive_folder, "from_drive.txt"), "wb") as f:
                    f.write(b"Notes added via Google Drive")

                res2 = self.service.sync_with_google_drive("drive_proj")
                self.assertEqual(res2["status"], "success")
                self.assertEqual(res2["pulled"], 1)

                files = {f["filename"]: f for f in self.service.list_files("drive_proj")}
                self.assertIn("from_drive.txt", files)
                self.assertEqual(files["from_drive.txt"]["source_type"], "google_drive")
        finally:
            shutil.rmtree(fake_drive_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
