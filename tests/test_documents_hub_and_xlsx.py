"""
tests/test_documents_hub_and_xlsx.py
Verifies the unified Documents Hub (Dossiers / DOCX / PPTX / XLSX),
XSS-safe native HTML renderers, path-traversal guards, and XLSX / Google Sheets support.
"""

import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.document_viewer import (
    delete_document_file,
    get_document_details,
    list_documents,
    safe_document_path,
)
from core.orchestrator import ORCHESTRATOR_ALLOWED_TOOL_NAMES
from tools.payload_sanitizer import EXEMPT_TOOLS


class TestDocumentsHubAndXlsx(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="aether_doc_hub_")
        for sub in ("dossiers", "docx", "pptx", "xlsx"):
            os.makedirs(os.path.join(self.temp_dir, "data", sub), exist_ok=True)
        self.patcher = patch(
            "core.document_viewer.user_data_path",
            side_effect=lambda *parts: os.path.join(self.temp_dir, *parts),
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_docx_pptx_xlsx_renderers_and_xss_escaping(self):
        # 1. Create a real .docx with heading, bold run, bullet, and table + XSS payload
        import docx
        d = docx.Document()
        d.add_heading("Inpatient Diabetes Handout", level=1)
        p = d.add_paragraph()
        r = p.add_run("Target: 140-180 mg/dL <script>alert(1)</script>")
        r.bold = True
        tbl = d.add_table(rows=2, cols=2)
        tbl.cell(0, 0).text = "Metric"
        tbl.cell(0, 1).text = "Value"
        tbl.cell(1, 0).text = "K+ Hold"
        tbl.cell(1, 1).text = "< 3.5 mEq/L"
        docx_fp = os.path.join(self.temp_dir, "data", "docx", "20261126_095500_Diabetes_Handout.docx")
        d.save(docx_fp)

        docx_list = list_documents("docx")
        self.assertEqual(len(docx_list), 1)
        self.assertEqual(docx_list[0]["filename"], "20261126_095500_Diabetes_Handout.docx")
        docx_det = get_document_details("docx", "20261126_095500_Diabetes_Handout.docx")
        self.assertEqual(docx_det["status"], "success")
        html_docx = docx_det["document"]["rendered_html"]
        self.assertIn("<h1>Inpatient Diabetes Handout</h1>", html_docx)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_docx)
        self.assertNotIn("<script>alert(1)</script>", html_docx)
        self.assertIn("<table", html_docx)

        # 2. Create a real .pptx with title, bullets, embedded PNG chart, and Speaker Notes
        from pptx import Presentation
        from pptx.util import Inches
        from PIL import Image
        prs = Presentation()
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        tb = sl.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(8), Inches(1))
        tb.text_frame.text = "NICE-SUGAR Mortality Outcomes"
        tb2 = sl.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(5), Inches(2))
        tb2.text_frame.text = "Conventional target 140-180 mg/dL reduced mortality."
        img_buf = io.BytesIO()
        Image.new("RGB", (60, 40), color=(0, 128, 128)).save(img_buf, format="PNG")
        img_buf.seek(0)
        sl.shapes.add_picture(img_buf, Inches(6), Inches(1.6), width=Inches(3))
        sl.notes_slide.notes_text_frame.text = "Attending pearl: highlight hypoglycemia hazard."
        pptx_fp = os.path.join(self.temp_dir, "data", "pptx", "20261126_095501_Diabetes_Deck.pptx")
        prs.save(pptx_fp)

        pptx_list = list_documents("pptx")
        self.assertEqual(len(pptx_list), 1)
        pptx_det = get_document_details("pptx", "20261126_095501_Diabetes_Deck.pptx")
        self.assertEqual(pptx_det["status"], "success")
        html_pptx = pptx_det["document"]["rendered_html"]
        self.assertIn("NICE-SUGAR Mortality Outcomes", html_pptx)
        self.assertIn("data:image/png;base64,", html_pptx)
        self.assertIn("Attending pearl: highlight hypoglycemia hazard.", html_pptx)
        self.assertIn("togglePptxNotesChk", html_pptx)

        # 3. Create a real multi-sheet .xlsx with formulas, merged cells, and XSS check
        import openpyxl
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Budget"
        ws1["A1"] = "Item"
        ws1["B1"] = "Cost"
        ws1["A2"] = "<b>Server</b>"
        ws1["B2"] = 120.5
        ws1["A3"] = "Total"
        ws1["B3"] = "=SUM(B2:B2)"
        ws2 = wb.create_sheet("Summary")
        ws2["A1"] = "Quarterly Overview"
        ws2.merge_cells("A1:B1")
        xlsx_fp = os.path.join(self.temp_dir, "data", "xlsx", "20261126_095502_Budget.xlsx")
        wb.save(xlsx_fp)

        xlsx_list = list_documents("xlsx")
        self.assertEqual(len(xlsx_list), 1)
        xlsx_det = get_document_details("xlsx", "20261126_095502_Budget.xlsx")
        self.assertEqual(xlsx_det["status"], "success")
        html_xlsx = xlsx_det["document"]["rendered_html"]
        self.assertIn('data-xlsx-sheet="0"', html_xlsx)
        self.assertIn('data-xlsx-sheet="1"', html_xlsx)
        self.assertIn("&lt;b&gt;Server&lt;/b&gt;", html_xlsx)
        self.assertIn('title="Formula: =SUM(B2:B2)"', html_xlsx)
        self.assertIn('colspan="2"', html_xlsx)

        # 4. Delete works cleanly inside category
        del_res = delete_document_file("xlsx", "20261126_095502_Budget.xlsx")
        self.assertEqual(del_res["status"], "success")
        self.assertEqual(list_documents("xlsx"), [])

    def test_path_traversal_blocked_across_categories(self):
        for cat in ("docx", "pptx", "xlsx"):
            self.assertIsNone(safe_document_path(cat, "../config.json"))
            self.assertIsNone(safe_document_path(cat, "..\\..\\secret.docx"))
            self.assertIsNone(safe_document_path(cat, "evil.exe"))

    def test_xlsx_google_sheets_tool_registered_and_uploads(self):
        from tools.dispatcher import _CUSTOM_TOOL_DECLARATIONS
        self.assertIn("upload_xlsx_to_google_sheets", _CUSTOM_TOOL_DECLARATIONS)
        self.assertIn("upload_xlsx_to_google_sheets", EXEMPT_TOOLS)
        self.assertIn("upload_xlsx_to_google_sheets", ORCHESTRATOR_ALLOWED_TOOL_NAMES)

        import openpyxl
        xlsx_fp = os.path.join(self.temp_dir, "data", "xlsx", "sample.xlsx")
        wb = openpyxl.Workbook()
        wb.active["A1"] = "Hello"
        wb.save(xlsx_fp)

        from core.oauth_manager import OAuthManager
        mgr = object.__new__(OAuthManager)
        mgr._google_headers = lambda: {"Authorization": "Bearer fake-token"}
        fake_resp = MagicMock(
            ok=True,
            status_code=200,
            content=b'{"id":"sheet123","name":"Sample Sheet","webViewLink":"https://docs.google.com/spreadsheets/d/sheet123/edit"}',
        )
        fake_resp.json.return_value = {
            "id": "sheet123",
            "name": "Sample Sheet",
            "webViewLink": "https://docs.google.com/spreadsheets/d/sheet123/edit",
        }
        with patch("core.oauth_manager.requests.post", return_value=fake_resp) as mock_post:
            res = mgr.upload_xlsx_to_google_sheets(xlsx_fp, title="Sample Sheet")
        self.assertTrue(res["success"])
        self.assertEqual(res["file_id"], "sheet123")
        self.assertIn("spreadsheets/d/sheet123", res["sheets_url"])
        self.assertEqual(res["drive_folder"], "Aether/xlsx")
        self.assertGreaterEqual(mock_post.call_count, 1)

    def test_google_drive_aether_folder_hierarchy_for_all_types_and_projects(self):
        """Verifies /Aether/{docs, dossiers, pptx, xlsx, projects/<Name>} folder hierarchy on Drive uploads."""
        from core.oauth_manager import OAuthManager
        mgr = object.__new__(OAuthManager)
        mgr._google_headers = lambda: {"Authorization": "Bearer fake-token"}

        created_folders = []
        uploaded_parents = []
        folder_seq = {"n": 0}

        def _fake_get(url, headers=None, params=None, timeout=15):
            r = MagicMock(ok=True, status_code=200, content=b'{"files":[]}')
            r.json.return_value = {"files": []}
            return r

        def _fake_post(url, headers=None, params=None, json=None, data=None, timeout=60):
            if json is not None:
                folder_seq["n"] += 1
                fid = f"fld_{folder_seq['n']}_{json['name']}"
                created_folders.append((json["name"], (json.get("parents") or ["root"])[0]))
                r = MagicMock(ok=True, status_code=200, content=b'{"id":"ok"}')
                r.json.return_value = {"id": fid, "name": json["name"]}
                return r
            # Multipart upload
            body_txt = data.decode("utf-8", errors="ignore")
            uploaded_parents.append(body_txt.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
            r = MagicMock(ok=True, status_code=200, content=b'{"id":"file_ok"}')
            r.json.return_value = {"id": "file_ok", "name": "Uploaded", "webViewLink": "https://docs.google.com/ok"}
            return r

        # Create dummy files for each type
        import docx as _docx, openpyxl as _xl
        from pptx import Presentation as _Prs
        docx_fp = os.path.join(self.temp_dir, "data", "docx", "handout.docx")
        _docx.Document().save(docx_fp)
        md_fp = os.path.join(self.temp_dir, "data", "dossiers", "diabetes.md")
        with open(md_fp, "w", encoding="utf-8") as f:
            f.write("# Diabetes Dossier\n")
        pptx_fp = os.path.join(self.temp_dir, "data", "pptx", "deck.pptx")
        _Prs().save(pptx_fp)
        xlsx_fp = os.path.join(self.temp_dir, "data", "xlsx", "sheet.xlsx")
        _xl.Workbook().save(xlsx_fp)

        with patch("core.oauth_manager.requests.get", side_effect=_fake_get), \
             patch("core.oauth_manager.requests.post", side_effect=_fake_post):
            r_doc = mgr.upload_docx_to_google_docs(docx_fp, title="Handout")
            r_dos = mgr.upload_docx_to_google_docs(md_fp, title="Dossier")
            r_ppt = mgr.upload_pptx_to_google_slides(pptx_fp, title="Deck")
            r_xls = mgr.upload_xlsx_to_google_sheets(xlsx_fp, title="Sheet")
            r_prj = mgr.upload_pptx_to_google_slides(pptx_fp, title="Proj Deck", project_name="Diabetes Curriculum")

        self.assertEqual(r_doc["drive_folder"], "Aether/docs")
        self.assertEqual(r_dos["drive_folder"], "Aether/dossiers")
        self.assertEqual(r_ppt["drive_folder"], "Aether/pptx")
        self.assertEqual(r_xls["drive_folder"], "Aether/xlsx")
        self.assertEqual(r_prj["drive_folder"], "Aether/projects/Diabetes Curriculum")

        # Confirm 'Aether' root folder was created only ONCE (cached for all subsequent uploads!)
        aether_roots = [c for c in created_folders if c[0] == "Aether"]
        self.assertEqual(len(aether_roots), 1)
        self.assertIn(("Diabetes Curriculum", "fld_6_projects"), created_folders)
        # Confirm every multipart upload JSON metadata included its exact leaf parent folder ID
        self.assertIn("fld_2_docs", uploaded_parents[0])
        self.assertIn("fld_3_dossiers", uploaded_parents[1])
        self.assertIn("fld_4_pptx", uploaded_parents[2])
        self.assertIn("fld_5_xlsx", uploaded_parents[3])
        self.assertIn("fld_7_Diabetes Curriculum", uploaded_parents[4])

    def test_in_place_compile_overwrites_without_duplicates_and_new_copy_preserves(self):
        """Verifies create_new_copy=False overwrites in place (no duplicate pptx clutter), while create_new_copy=True preserves original."""
        from core.document_compiler import compile_presentation_bundle
        from tests.test_document_pipeline_work_order import _build_26_slide_diabetes_lecture_spec
        import json

        spec = _build_26_slide_diabetes_lecture_spec()
        spec_path = os.path.join(self.temp_dir, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)

        job_dir = os.path.join(self.temp_dir, "outputs", "inpatient_diabetes")
        with patch("core.document_compiler.get_outputs_dir", return_value=job_dir), \
             patch("core.document_compiler.user_data_path", side_effect=lambda *parts: os.path.join(self.temp_dir, *parts)):
            r1 = compile_presentation_bundle(spec_path, job_id="inpatient_diabetes", build_pptx=True, build_docx=True, create_new_copy=False)
            r2 = compile_presentation_bundle(spec_path, job_id="inpatient_diabetes", build_pptx=True, build_docx=True, create_new_copy=False)
            r3 = compile_presentation_bundle(spec_path, job_id="inpatient_diabetes", build_pptx=True, build_docx=False, create_new_copy=True)

        self.assertTrue(r1["passed"] and r2["passed"] and r3["passed"])
        # Two in-place compiles produce the EXACT same filename (1 file, not 2!)
        self.assertEqual(r1["pptx_path"], r2["pptx_path"])
        self.assertEqual(os.path.basename(r1["pptx_path"]), "Inpatient_Diabetes_Management.pptx")
        # Explicit create_new_copy=True writes a second distinct file without clobbering the original
        self.assertNotEqual(r1["pptx_path"], r3["pptx_path"])
        pptx_files = os.listdir(os.path.join(self.temp_dir, "data", "pptx"))
        self.assertEqual(len(pptx_files), 2)

    def test_active_viewer_context_and_orphan_spec_cleanup_on_delete(self):
        """Verifies active viewer document tracking + orphan outputs/<job_id> cleanup when a deck is deleted."""
        from core.document_viewer import (
            clear_active_viewer_document,
            delete_document_file,
            format_active_viewer_context,
            get_active_viewer_document,
            set_active_viewer_document,
        )
        from pptx import Presentation as _Prs

        v1_job = os.path.join(self.temp_dir, "outputs", "deck_v1")
        v2_job = os.path.join(self.temp_dir, "outputs", "deck_v2")
        os.makedirs(v1_job, exist_ok=True)
        os.makedirs(v2_job, exist_ok=True)
        with open(os.path.join(v1_job, "lecture_spec.json"), "w", encoding="utf-8") as f:
            f.write('{"v": 1}')
        with open(os.path.join(v2_job, "lecture_spec.json"), "w", encoding="utf-8") as f:
            f.write('{"v": 2}')

        v1_pptx_data = os.path.join(self.temp_dir, "data", "pptx", "deck_v1.pptx")
        v2_pptx_data = os.path.join(self.temp_dir, "data", "pptx", "deck_v2.pptx")
        _Prs().save(v1_pptx_data)
        _Prs().save(v2_pptx_data)
        _Prs().save(os.path.join(v1_job, "deck_v1.pptx"))
        _Prs().save(os.path.join(v2_job, "deck_v2.pptx"))

        # Selecting deck_v2.pptx in the viewer binds active context directly to outputs/deck_v2/lecture_spec.json
        set_active_viewer_document("pptx", "deck_v2.pptx", v2_pptx_data, "Deck V2")
        active = get_active_viewer_document()
        self.assertIsNotNone(active)
        self.assertTrue(active["lecture_spec_path"].endswith(os.path.join("deck_v2", "lecture_spec.json")))
        ctx = format_active_viewer_context()
        self.assertIn("deck_v2.pptx", ctx)
        self.assertIn("deck_v2", ctx)

        # Deleting deck_v1.pptx from data/pptx/ also removes orphaned outputs/deck_v1/
        del_res = delete_document_file("pptx", "deck_v1.pptx")
        self.assertEqual(del_res["status"], "success")
        self.assertFalse(os.path.exists(v1_job))
        self.assertTrue(os.path.exists(v2_job))
        clear_active_viewer_document()
        self.assertIsNone(get_active_viewer_document())

    def test_rename_document_updates_dossier_heading_and_office_filename(self):
        """Verifies inline rename removes underscores in dossiers (updating H1 + filename) and strips v3 in docx/pptx while preserving timestamp."""
        from core.document_viewer import rename_document_file
        import docx as _docx

        # 1. Dossier rename updates both filename and top '# Heading'
        md_fp = os.path.join(self.temp_dir, "data", "dossiers", "20261116_130258_Renewable_Energy_in_the_US.md")
        with open(md_fp, "w", encoding="utf-8") as f:
            f.write("# Renewable_Energy_in_the_US\n\nBody text.\n")
        res_md = rename_document_file("dossiers", "20261116_130258_Renewable_Energy_in_the_US.md", "Renewable Energy in the United States")
        self.assertEqual(res_md["status"], "success")
        self.assertEqual(res_md["new_filename"], "20261116_130258_Renewable_Energy_in_the_United_States.md")
        with open(res_md["file_path"], "r", encoding="utf-8") as f:
            self.assertTrue(f.readline().startswith("# Renewable Energy in the United States"))

        # 2. DOCX rename strips v3 while preserving timestamp prefix
        dx_fp = os.path.join(self.temp_dir, "data", "docx", "20261126_104756_Inpatient_Diabetes_Management_v3_Handout.docx")
        _docx.Document().save(dx_fp)
        res_dx = rename_document_file("docx", "20261126_104756_Inpatient_Diabetes_Management_v3_Handout.docx", "Inpatient Diabetes Management Handout")
        self.assertEqual(res_dx["status"], "success")
        self.assertEqual(res_dx["new_filename"], "20261126_104756_Inpatient_Diabetes_Management_Handout.docx")
        self.assertTrue(os.path.isfile(res_dx["file_path"]))
        self.assertFalse(os.path.exists(dx_fp))


if __name__ == "__main__":
    unittest.main()
