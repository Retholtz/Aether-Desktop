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

    @patch("aiohttp.ClientSession.get")
    @patch("requests.Session.get")
    async def test_import_url_403_fallback_to_requests(self, mock_requests_get, mock_aio_get):
        self.service.create_project("fb_proj", "Fallback Project")

        # aiohttp returns 403 Forbidden
        mock_aio_resp = AsyncMock()
        mock_aio_resp.status = 403
        mock_aio_get.return_value.__aenter__.return_value = mock_aio_resp

        # requests fallback returns 200 OK
        mock_req_resp = MagicMock()
        mock_req_resp.status_code = 200
        mock_req_resp.text = "<html><head><title>Design Article &amp; Tips</title></head><body><h1>Living Room</h1><p>Tips for interior design.</p></body></html>"
        mock_requests_get.return_value = mock_req_resp

        res = await self.service.import_url("fb_proj", "example.com/interior-design")
        self.assertEqual(res["status"], "success")
        self.assertIn("Design_Article", res["metadata"]["filename"])
        self.assertEqual(res["metadata"]["source_url"], "https://example.com/interior-design")

        # Verify content has unescaped title and converted markdown
        p_dir = self.service.get_project_dir("fb_proj")
        with open(os.path.join(p_dir, res["metadata"]["filename"]), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Design Article & Tips", content)
        self.assertIn("## Living Room", content)

    @patch("aiohttp.ClientSession.get")
    @patch("requests.Session.get")
    @patch("core.project_service.fetch_with_headless_browser")
    async def test_import_url_403_fallback_to_headless_browser(self, mock_browser, mock_requests_get, mock_aio_get):
        self.service.create_project("browser_proj", "Browser Project")

        # Both aiohttp and requests fail with 403 / challenge
        mock_aio_resp = AsyncMock()
        mock_aio_resp.status = 403
        mock_aio_get.return_value.__aenter__.return_value = mock_aio_resp

        mock_req_resp = MagicMock()
        mock_req_resp.status_code = 403
        mock_requests_get.return_value = mock_req_resp

        # Headless browser succeeds with DOM
        mock_browser.return_value = "<html><head><title>Akamai Protected Store</title></head><body><h2>Products</h2><p>Exclusive furniture.</p></body></html>"

        res = await self.service.import_url("browser_proj", "https://store.example.com")
        self.assertEqual(res["status"], "success")
        self.assertIn("Akamai_Protected_Store", res["metadata"]["filename"])
        mock_browser.assert_called_once()

    def test_open_project_file_and_external_url(self):
        # Setup mock GuiBridge with ProjectService
        self.service.create_project("open_proj", "Open Project")
        self.service.add_file("open_proj", "sample.pdf", b"%PDF-1.4 test content")

        mock_bridge = MagicMock()
        mock_bridge.project_service = self.service
        from core.gui_bridge import GuiBridge

        # 1. Test open_project_file success
        with patch("os.startfile", create=True) as mock_startfile:
            res = GuiBridge.open_project_file(mock_bridge, "open_proj", "sample.pdf")
            self.assertEqual(res["status"], "success")
            mock_startfile.assert_called_once()
            self.assertTrue(res["path"].endswith("sample.pdf"))

        # 2. Test open_project_file not found
        res_missing = GuiBridge.open_project_file(mock_bridge, "open_proj", "nonexistent.docx")
        self.assertEqual(res_missing["status"], "error")
        self.assertIn("not exist", res_missing["message"])

        # 3. Test open_external_url
        with patch("os.startfile", create=True) as mock_startfile:
            res_url = GuiBridge.open_external_url(mock_bridge, "https://butcherblockco.com/product/lerustica")
            self.assertEqual(res_url["status"], "success")
            mock_startfile.assert_called_once_with("https://butcherblockco.com/product/lerustica")

        # 4. Test open_external_url normalizes bare domain
        with patch("os.startfile", create=True) as mock_startfile:
            res_url2 = GuiBridge.open_external_url(mock_bridge, "crateandbarrel.com")
            self.assertEqual(res_url2["status"], "success")
            mock_startfile.assert_called_once_with("https://crateandbarrel.com")


if __name__ == "__main__":
    unittest.main()
