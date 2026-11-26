"""
tests/test_document_pipeline_work_order.py
Comprehensive Verification Suite for Work Order:
Bulletproof Multi-Model Document & Presentation Pipeline (Neon -> Hydrogen -> Workspace)

Covers all 7 Verification Checklist items & all 5 Tasks:
1. Single-source-of-truth `lecture_spec.json` extraction, schema & index integrity (26-slide Diabetes fixture)
2. Programmatic Slide 1 module roadmap matching Slides 1..26
3. Slide 19 DKA vs. HHS table placement (NOT Slide 16) + 2024 Consensus canonical_facts sync across Dossier/PPTX/DOCX
4. Zero-token text hygiene & safety linter (delimiters, truncated fragments, Markdown leaks, naked `.1` decimals, dropped content)
5. Slide 7 Glargine smooth curve continuity check (catches discontinuous piecewise step, passes smooth curve at 300 DPI)
6. Slide 25 multi-vignette PGY-1/2/3 structured cards[] rendering (and >3 cards auto-splitting onto Cont. slide)
7. Low-cost Visual QA gate at MEDIA_RESOLUTION_LOW, bounded 2-cycle auto-repair, upload gating, frozen runtime pins, & <$1.00 telemetry cost receipt
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from core.api_telemetry import VALID_FEATURE_TAGS, _finalize_and_log_call, format_cost_report
from core.doc_validator import (
    LECTURE_SPEC_END_MARKER,
    LECTURE_SPEC_START_MARKER,
    check_curve_continuity,
    extract_lecture_spec_from_text,
    lint_text_item,
    validate_compiled_artifacts,
    validate_lecture_spec,
)
from core.document_compiler import compile_presentation_bundle, render_chart_from_spec
from core.orchestrator import ORCHESTRATOR_ALLOWED_TOOL_NAMES
from core.visual_qa import log_pipeline_cost_receipt, run_bounded_visual_qa_with_repair, run_visual_qa_gate, stitch_contact_sheets
from security.ast_gatekeeper import ALLOWED_INTERNAL_MODULES, BUNDLED_THIRD_PARTY_MODULES
from tools.dispatcher import ToolDispatcher, get_all_tool_declarations
from tools.email_tools import upload_docx_to_google_docs, upload_pptx_to_google_slides


def _build_26_slide_diabetes_lecture_spec() -> dict:
    """Constructs a complete 26-slide Inpatient Diabetes Management `lecture_spec.json` fixture."""
    modules = [
        {"id": 1, "name": "Foundations & Evidence-Based Targets", "slide_start": 1, "slide_end": 4},
        {"id": 2, "name": "T1DM vs. T2DM & Insulin Pharmacokinetics", "slide_start": 5, "slide_end": 8},
        {"id": 3, "name": "Subcutaneous Insulin Regimens", "slide_start": 9, "slide_end": 12},
        {"id": 4, "name": "High-Risk Scenarios & Hypoglycemia", "slide_start": 13, "slide_end": 18},
        {"id": 5, "name": "Hyperglycemic Emergencies (DKA & HHS)", "slide_start": 19, "slide_end": 22},
        {"id": 6, "name": "Safe Transitions & Resident Cases", "slide_start": 23, "slide_end": 26},
    ]

    def _mod_for(n: int) -> int:
        for m in modules:
            if m["slide_start"] <= n <= m["slide_end"]:
                return m["id"]
        return 1

    # Smooth 24-hour insulin PK curves for Slide 7 (no discontinuity at 1.5h!)
    t_hrs = np.linspace(0.0, 24.0, 97)
    rapid_y = np.round(0.95 * np.exp(-((t_hrs - 1.5) / 1.1) ** 2) * (1.0 - np.exp(-t_hrs / 0.3)), 4).tolist()
    glargine_y = np.round(0.32 * (1.0 / (1.0 + np.exp(-(t_hrs - 2.0) / 0.8))), 4).tolist()

    slides = []
    for sn in range(1, 27):
        mid = _mod_for(sn)
        sl = {
            "slide_number": sn,
            "module_id": mid,
            "title": f"Inpatient Diabetes Topic {sn}",
            "layout_type": "bullets",
            "bullets": [
                f"Maintain inpatient blood glucose between 140 and 180 mg/dL for hospitalized adults on Slide {sn}.",
                "Initiate scheduled insulin therapy for persistent hyperglycemia >= 180 mg/dL.",
                "Monitor point-of-care glucose before meals and at bedtime daily.",
            ],
            "speaker_notes": f"Attending Clinical Pearl for Slide {sn}: Ask PGY-2/3 residents to justify inpatient glycemic targets.",
        }

        if sn == 1:
            sl["title"] = "The Inpatient Burden of Dysglycemia & Curriculum Roadmap"
            sl["layout_type"] = "title_roadmap"
            sl["bullets"] = [
                "Approximately 32% to 38% of non-ICU inpatients experience hyperglycemia during hospitalization.",
                "Inpatient dysglycemia follows a U-shaped mortality curve across severe hyperglycemia and hypoglycemia.",
                "Distinguish known diabetes, undiagnosed diabetes, and hospital-related stress hyperglycemia on admission.",
            ]
        elif sn == 7:
            sl["title"] = "Pharmacokinetics of Inpatient Insulins"
            sl["layout_type"] = "bullets_plus_chart"
            sl["bullets"] = [
                "Rapid-acting analogs peak at 1 to 2 hours and cover prandial carbohydrates.",
                "Glargine U-100 provides smooth, peakless basal coverage over 20 to 24 hours.",
                "Start weight-based IV insulin at 0.1 U/kg/hr during acute DKA.",
            ]
            sl["chart_spec"] = {
                "title": "Inpatient Insulin Pharmacokinetic Profiles",
                "chart_type": "line",
                "x_label": "Hours Post-Injection",
                "y_label": "Relative Insulin Activity",
                "smooth_continuous": True,
                "series": [
                    {"name": "Rapid (Lispro/Aspart)", "x": [round(float(v), 2) for v in t_hrs], "y": rapid_y},
                    {"name": "Glargine (Basal)", "x": [round(float(v), 2) for v in t_hrs], "y": glargine_y, "linestyle": "--"},
                ],
                "annotations": [],
            }
        elif sn == 16:
            sl["title"] = "Renal Impairment (CKD/ESRD) & Hepatic Failure"
            sl["layout_type"] = "bullets"
            sl["bullets"] = [
                "Reduced renal insulin clearance increases severe hypoglycemia risk in CKD and ESRD.",
                "Reduce starting total daily insulin dose to 0.25 to 0.3 U/kg/day.",
                "Avoid sulfonylureas and hold metformin when eGFR drops below 30 mL/min.",
            ]
        elif sn == 19:
            sl["title"] = "Diagnostic Criteria for DKA & HHS (2024 Consensus)"
            sl["layout_type"] = "table_slide"
            sl["bullets"] = [
                "2024 Consensus lowers DKA glucose threshold to >= 200 mg/dL or prior diabetes.",
                "HHS requires glucose >= 600 mg/dL and effective serum osmolality > 300 mOsm/kg.",
                "Confirm metabolic acidosis with arterial pH < 7.30 and bicarbonate < 18 mEq/L.",
            ]
            sl["table"] = {
                "title": "DKA vs. HHS Diagnostic Criteria (2024 Consensus)",
                "headers": ["Parameter", "DKA (2024 Consensus)", "HHS (2024 Consensus)"],
                "rows": [
                    ["Blood Glucose", ">= 200 mg/dL (or prior DM)", ">= 600 mg/dL"],
                    ["Effective Osmolality", "Variable", "> 300 mOsm/kg"],
                    ["Arterial / Venous pH", "< 7.30", ">= 7.30"],
                    ["Serum Bicarbonate", "< 18 mEq/L", ">= 15 mEq/L"],
                ],
            }
        elif sn == 20:
            sl["title"] = "Acute Resuscitation & Potassium Safety Gates"
            sl["layout_type"] = "bullets"
            sl["bullets"] = [
                "Hold IV insulin if serum potassium is < 3.5 mEq/L until repleted.",
                "Start fixed-rate IV regular insulin at 0.1 U/kg/hr once potassium is safe.",
                "Add dextrose-containing fluids once blood glucose falls < 250 mg/dL.",
            ]
        elif sn == 23:
            sl["title"] = "HbA1c-Guided Discharge Medication Reconciliation"
            sl["layout_type"] = "bullets"
            sl["bullets"] = [
                "Resume home regimen when admission HbA1c is < 7.0% and renal function is stable.",
                "Add or intensify basal insulin when admission HbA1c exceeds 8.0% to 9.0%.",
                "Schedule outpatient follow-up within 1 to 2 weeks for all insulin discharges.",
            ]
        elif sn == 25:
            sl["title"] = "Resident Ward & Board Case Vignettes (PGY-1 to PGY-3)"
            sl["layout_type"] = "case_cards"
            sl["bullets"] = []
            sl["cards"] = [
                {
                    "title": "Case 1 (PGY-1): NPO Type 1 Diabetes",
                    "items": [
                        "54-year-old with T1DM is NPO for morning colonoscopy with BG 92 mg/dL.",
                        "Action: Never hold basal insulin completely; give 80% glargine and start D5 fluids.",
                    ],
                },
                {
                    "title": "Case 2 (PGY-2): Steroid Hyperglycemia",
                    "items": [
                        "68-year-old on prednisone 40 mg daily spikes to 310 mg/dL every afternoon.",
                        "Action: Add morning NPH 0.3 U/kg timed with prednisone peak activity.",
                    ],
                },
                {
                    "title": "Case 3 (PGY-3): DKA Potassium Gate & Transition",
                    "items": [
                        "31-year-old in DKA presents with BG 420 mg/dL and potassium 3.1 mEq/L.",
                        "Action: Hold insulin until K+ >= 3.5 mEq/L, add D5 at BG < 250 mg/dL, overlap SubQ 2 hours.",
                    ],
                },
            ]
        elif sn == 26:
            sl["title"] = "High-Yield Bedside Summary Rules"
            sl["layout_type"] = "summary_rules"
            sl["bullets"] = [
                "Target BG 140 to 180 mg/dL for most ICU and floor patients.",
                "Never withhold basal insulin completely in Type 1 diabetes.",
                "Verify potassium >= 3.5 mEq/L before starting IV insulin in DKA.",
            ]

        slides.append(sl)

    return {
        "metadata": {
            "title": "Inpatient Diabetes Management",
            "subtitle": "Evidence-Based Hospital Glycemic Care",
            "audience": "PGY 1-3 Internal Medicine Residents",
            "total_slides": 26,
            "guidelines_version": "ADA 2024-2025 & 2024 DKA/HHS Consensus",
        },
        "canonical_facts": {
            "dka_bg_threshold": ">= 200 mg/dL",
            "dka_k_hold": "< 3.5 mEq/L",
            "dka_dextrose_switch": "< 250 mg/dL",
            "hhs_osmolality": "> 300 mOsm/kg",
            "discharge_a1c_tier1": "< 7.0%",
            "dka_iv_insulin_rate": "0.1 U/kg/hr",
        },
        "forbidden_legacy_values": {
            "legacy_2009_hhs_osmolality": "> 320 mOsm/kg",
        },
        "modules": modules,
        "slides": slides,
        "handout_sections": [
            {
                "title": "2024 Consensus DKA & HHS Quick Reference",
                "target_pages": 2,
                "rules": [
                    "Diagnose DKA at BG >= 200 mg/dL (or known diabetes) with pH < 7.30 and HCO3 < 18 mEq/L.",
                    "Hold IV insulin if K+ is < 3.5 mEq/L; start fixed-rate insulin at 0.1 U/kg/hr once K+ is repleted.",
                    "Switch to dextrose-containing fluids when BG drops < 250 mg/dL during DKA resuscitation.",
                    "Diagnose HHS at BG >= 600 mg/dL and effective osmolality > 300 mOsm/kg.",
                ],
                "tables": [
                    {
                        "title": "Discharge Strategy by Admission HbA1c",
                        "headers": ["Admission HbA1c", "Recommended Action"],
                        "rows": [
                            ["< 7.0%", "Resume home non-insulin regimen if renal function allows"],
                            ["7.0% to 9.0%", "Optimize home regimen or add basal insulin"],
                            ["> 9.0%", "Initiate basal-bolus or basal plus GLP-1/SGLT2i"],
                        ],
                    }
                ],
            }
        ],
    }


class TestDocumentPipelineWorkOrder(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_26_slide_diabetes_end_to_end_compile_and_zero_error_validation(self):
        """
        Verifies Checklist #1, #2, #3, #4, #5, #6 on the 26-slide Inpatient Diabetes fixture:
        - lecture_spec passes with 0 errors
        - Slide 1 roadmap matches all 6 module ranges (1-4, 5-8, 9-12, 13-18, 19-22, 23-26)
        - DKA vs. HHS table renders on Slide 19 (NOT Slide 16) with 2024 Consensus criteria
        - Slide 7 Glargine curve is smooth & saved at 300 DPI
        - Slide 25 renders all 3 PGY-1, PGY-2, PGY-3 case cards completely
        - Zero truncated bullets, unmatched delimiters, Markdown leaks, or naked decimals
        """
        spec = _build_26_slide_diabetes_lecture_spec()
        spec_path = os.path.join(self.temp_dir, "lecture_spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)

        with patch("core.document_compiler.get_outputs_dir", return_value=os.path.join(self.temp_dir, "outputs", "job_dm")):
            res = compile_presentation_bundle(
                spec_path,
                job_id="job_dm",
                build_pptx=True,
                build_docx=True,
                mirror_to_data_dirs=False,
            )

        self.assertTrue(res["passed"], f"Validation failed: {res.get('validation')}")
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["validation"]["errors"]), 0)
        self.assertTrue(os.path.isfile(res["pptx_path"]))
        self.assertTrue(os.path.isfile(res["docx_path"]))
        self.assertEqual(len(res["chart_pngs"]), 1)

        # Verify Slide 19 has the DKA table and Slide 16 does NOT
        from pptx import Presentation
        prs = Presentation(res["pptx_path"])
        self.assertEqual(len(prs.slides), 26)

        s16_tables = [sh for sh in prs.slides[15].shapes if sh.has_table]
        s19_tables = [sh for sh in prs.slides[18].shapes if sh.has_table]
        self.assertEqual(len(s16_tables), 0, "Slide 16 (Renal Impairment) must NOT contain the DKA table!")
        self.assertEqual(len(s19_tables), 1, "Slide 19 MUST contain the DKA vs. HHS table!")

        # Verify Slide 25 has all 3 cases
        s25_text = "\n".join(sh.text_frame.text for sh in prs.slides[24].shapes if sh.has_text_frame)
        self.assertIn("Case 1 (PGY-1)", s25_text)
        self.assertIn("Case 2 (PGY-2)", s25_text)
        self.assertIn("Case 3 (PGY-3)", s25_text)

        # Verify Speaker Notes populated on Slide 19 and absent from visible slide
        s19_visible = "\n".join(sh.text_frame.text for sh in prs.slides[18].shapes if sh.has_text_frame)
        self.assertNotIn("Attending Clinical Pearl", s19_visible)
        self.assertIn("Attending Clinical Pearl", prs.slides[18].notes_slide.notes_text_frame.text)

    def test_text_hygiene_linter_catches_all_observed_regression_defects(self):
        """Verifies Task 3.2 linter flags every regression defect from the Diabetes baseline."""
        bad_cases = [
            ("500–1.", "TRUNCATED_FRAGMENT"),
            ("Severe hypoglycemia (18.7%.", "UNBALANCED_DELIMITER"),
            ("Start drip at .1 U/kg/hr immediately.", "NAKED_LEADING_DECIMAL"),
            ("Use `basal-bolus` therapy.", "MARKDOWN_LEAK"),
            ("Target BG **140-180 mg/dL**", "MARKDOWN_LEAK"),
            ("Hospitalized glycemic targets:.", "TRUNCATED_FRAGMENT"),
            ("Compare intensive vs.", "TRUNCATED_FRAGMENT"),
            ("Intensive control increased 90-day...", "TRUNCATED_ELLIPSIS"),
            ("Attending Clinical Pearl: Ask PGY-2/3 residents why.", "PRESENTER_CUE_ON_SLIDE"),
        ]
        for text, expected_code in bad_cases:
            issues = lint_text_item(text, "test_loc", check_presenter_leak=True)
            codes = {i["code"] for i in issues}
            self.assertIn(expected_code, codes, f"Expected {expected_code} for '{text}', got {codes}")

        # Leading-zero decimal must pass cleanly
        ok_issues = lint_text_item("Start fixed-rate IV insulin at 0.1 U/kg/hr.", "test_loc", check_presenter_leak=True)
        self.assertEqual(ok_issues, [])

    def test_curve_continuity_catches_broken_glargine_piecewise_jump(self):
        """Verifies Task 3.4 catches a sharp vertical discontinuity like the Slide 7 Glargine bug."""
        t = np.linspace(0, 24, 120)
        broken_glargine = np.where(t > 1.5, 0.30, 0.0)  # Vertical step at t=1.5h!
        issues = check_curve_continuity(broken_glargine, series_name="Glargine (Basal)", location="slide_7")
        self.assertTrue(any(i["code"] == "CHART_DISCONTINUITY" for i in issues))

        # render_chart_from_spec must refuse to save a broken smooth_continuous curve
        bad_png = os.path.join(self.temp_dir, "bad_chart.png")
        out_p, r_issues = render_chart_from_spec(
            {
                "title": "Broken PK",
                "chart_type": "line",
                "smooth_continuous": True,
                "series": [{"name": "Glargine", "x": t.tolist(), "y": broken_glargine.tolist()}],
            },
            bad_png,
            slide_number=7,
        )
        self.assertIsNone(out_p)
        self.assertTrue(any(i["code"] == "CHART_DISCONTINUITY" for i in r_issues))

    def test_cross_artifact_consistency_and_upload_gate_block_drifted_artifacts(self):
        """Verifies Task 3.3 & Task 4.4 block Google Workspace upload when 2009 legacy drift exists."""
        spec = _build_26_slide_diabetes_lecture_spec()
        spec_path = os.path.join(self.temp_dir, "lecture_spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)

        # Create a drifted handout with outdated 2009 osmolality (> 320 mOsm/kg) and naked decimal
        import docx
        bad_docx = os.path.join(self.temp_dir, "drifted_handout.docx")
        d = docx.Document()
        d.add_paragraph("HHS requires serum osmolality > 320 mOsm/kg and insulin .1 U/kg.")
        d.save(bad_docx)

        val = validate_compiled_artifacts(spec_path, docx_path=bad_docx)
        self.assertFalse(val["passed"])
        codes = {e["code"] for e in val["errors"]}
        self.assertIn("LEGACY_FACT_CONTRADICTION", codes)
        self.assertIn("NAKED_LEADING_DECIMAL", codes)

        # Upload tool must refuse to upload the failed docx!
        with patch("tools.email_tools.get_oauth_manager") as mock_mgr:
            up_res = upload_docx_to_google_docs(bad_docx, lecture_spec_path=spec_path)
            self.assertFalse(up_res["success"])
            self.assertTrue(up_res.get("blocked_upload"))
            mock_mgr.assert_not_called()

    def test_visual_qa_uses_media_resolution_low_and_bounded_auto_repair(self):
        """Verifies Task 4 uses MEDIA_RESOLUTION_LOW, tags document_builder/visual_qa, and caps retries at 2."""
        from PIL import Image

        chart_png = os.path.join(self.temp_dir, "chart_slide_07.png")
        Image.new("RGB", (1200, 750), color=(25, 60, 100)).save(chart_png, dpi=(300, 300))

        # Test 4-up contact sheet stitching
        sheets = stitch_contact_sheets([chart_png] * 5, os.path.join(self.temp_dir, "sheets"))
        self.assertEqual(len(sheets), 2)

        seen_resolutions = []
        mock_client = MagicMock()

        def _fake_gen(model, contents, config=None):
            for part in contents[0].parts:
                mr = getattr(part, "media_resolution", None)
                if mr is not None:
                    seen_resolutions.append(str(getattr(mr, "level", "")).lower())
            # Fail first pass, pass second pass after repair
            if len(seen_resolutions) == 1:
                return SimpleNamespace(text=json.dumps({
                    "passed": False,
                    "defects": [{"target_id": "chart_slide_07.png", "severity": "ERROR", "issue": "Jagged line", "suggested_fix": "Smooth curve"}],
                }))
            return SimpleNamespace(text='{"passed": true, "defects": []}')

        mock_client.models.generate_content.side_effect = _fake_gen
        repair_calls = []

        def _repair_cb(defects, cycle):
            repair_calls.append((cycle, len(defects)))
            return {"chart_pngs": [chart_png]}

        qa_res = run_bounded_visual_qa_with_repair(
            client=mock_client,
            model="gemini-4-hydrogen",
            chart_pngs=[chart_png],
            repair_callback=_repair_cb,
            max_repair_cycles=2,
        )
        self.assertTrue(qa_res["passed"])
        self.assertEqual(qa_res["repair_cycles_used"], 1)
        self.assertEqual(len(repair_calls), 1)
        self.assertTrue(all("low" in r for r in seen_resolutions), f"Expected MEDIA_RESOLUTION_LOW, got {seen_resolutions}")

    async def test_neon_dossier_extracts_and_saves_lecture_spec_json(self):
        """Verifies Task 1: Neon dossier creation extracts lecture_spec.json into outputs/<job_id>/."""
        spec = _build_26_slide_diabetes_lecture_spec()
        fake_neon_text = (
            "# Inpatient Diabetes Management Teaching Dossier\n\n"
            "Full attending teaching dossier content.\n\n"
            f"{LECTURE_SPEC_START_MARKER}\n```json\n{json.dumps(spec)}\n```\n{LECTURE_SPEC_END_MARKER}\n"
        )
        clean_md, parsed, err = extract_lecture_spec_from_text(fake_neon_text)
        self.assertIsNone(err)
        self.assertIsNotNone(parsed)
        self.assertNotIn(LECTURE_SPEC_START_MARKER, clean_md)

        disp = ToolDispatcher(config_getter=lambda: {})
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(text=fake_neon_text)
        disp.genai_client = mock_client

        out_job_dir = os.path.join(self.temp_dir, "outputs", "job_neon")
        os.makedirs(out_job_dir, exist_ok=True)
        with patch("core.paths.get_outputs_dir", return_value=out_job_dir), \
             patch("tools.dispatcher.export_research_dossier", return_value={"status": "success", "file_path": os.path.join(self.temp_dir, "dossier.md"), "filename": "dossier.md"}):
            res = await disp.execute_heavy_task(
                task_type="research_dossier",
                title="Inpatient Diabetes Management",
                instructions="Create a 26-slide teaching dossier and presentation on inpatient diabetes.",
                paste_to_active_doc=False,
            )

        self.assertEqual(res["status"], "success")
        self.assertTrue(os.path.isfile(res["lecture_spec_path"]))
        self.assertTrue(res["spec_validation"]["passed"])

    def test_packaging_pins_tools_and_telemetry_cost_receipt_under_1_dollar(self):
        """Verifies Task 5: frozen dependencies pinned, pipeline tools registered, and cost receipt < $1.00."""
        for pkg in ("pptx", "docx", "matplotlib", "PIL", "numpy", "lxml"):
            self.assertIn(pkg, BUNDLED_THIRD_PARTY_MODULES)
        for internal_mod in ("core.doc_validator", "core.document_compiler", "core.paths"):
            self.assertIn(internal_mod, ALLOWED_INTERNAL_MODULES)
        self.assertIn("document_builder", VALID_FEATURE_TAGS)
        self.assertIn("dossier_creation", VALID_FEATURE_TAGS)

        decl_names = {d.get("name") for d in get_all_tool_declarations()}
        for tool_name in ("compile_presentation_bundle", "validate_document_artifacts", "run_visual_qa_check"):
            self.assertIn(tool_name, decl_names)
            self.assertIn(tool_name, ORCHESTRATOR_ALLOWED_TOOL_NAMES)

        # Verify aether_desktop.spec pins all required libraries
        with open("aether_desktop.spec", "r", encoding="utf-8") as sf:
            spec_src = sf.read()
        for mod in ('"pptx"', '"docx"', '"matplotlib"', '"PIL"', '"numpy"', '"lxml"'):
            self.assertIn(mod, spec_src)

        # Verify cost receipt attribution and total < $1.00
        db_path = os.path.join(self.temp_dir, "telemetry.db")
        t_start = time.time() - 1.0
        _finalize_and_log_call(
            ctx={"feature_tag": "dossier_creation", "sub_action": "heavy_research_dossier", "loop_step": 1, "turn_id": "n1"},
            model_name="gemini-4-neon",
            diag={"system_prompt_chars": 1000, "tool_declarations_count": 0, "history_message_count": 1, "thinking_config": "", "media_resolution": ""},
            imgs_kept=0,
            imgs_scrubbed=0,
            response=SimpleNamespace(
                text="ok",
                usage_metadata=SimpleNamespace(prompt_token_count=800, cached_content_token_count=0, tool_use_prompt_token_count=0, candidates_token_count=19000, thoughts_token_count=5000, total_token_count=24800, prompt_tokens_details=[]),
                candidates=[SimpleNamespace(grounding_metadata=None)],
            ),
            latency_ms=1000.0,
            db_path=db_path,
        )
        _finalize_and_log_call(
            ctx={"feature_tag": "document_builder", "sub_action": "visual_qa", "loop_step": 1, "turn_id": "q1"},
            model_name="gemini-4-hydrogen",
            diag={"system_prompt_chars": 400, "tool_declarations_count": 0, "history_message_count": 1, "thinking_config": "", "media_resolution": "low"},
            imgs_kept=7,
            imgs_scrubbed=0,
            response=SimpleNamespace(
                text='{"passed": true, "defects": []}',
                usage_metadata=SimpleNamespace(prompt_token_count=2400, cached_content_token_count=0, tool_use_prompt_token_count=0, candidates_token_count=40, thoughts_token_count=0, total_token_count=2440, prompt_tokens_details=[]),
                candidates=[SimpleNamespace(grounding_metadata=None)],
            ),
            latency_ms=400.0,
            db_path=db_path,
        )

        receipt = log_pipeline_cost_receipt("diabetes_26_slide", since_ts=t_start, db_path=db_path)
        self.assertGreater(receipt["dossier_cost_usd"], 0.0)
        self.assertGreater(receipt["visual_qa_cost_usd"], 0.0)
        self.assertLess(receipt["total_cost_usd"], 1.00)
        report = format_cost_report(db_path=db_path)
        self.assertIn("dossier_creation", report)
        self.assertIn("document_builder", report)


if __name__ == "__main__":
    unittest.main()
