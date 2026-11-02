"""
core/project_service.py - Project Workspace & Document Ingestion Manager
Manages local file storage, token calculation, and asynchronous web scraping.
"""
import asyncio
import os
import re
import json
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import aiohttp
from core.logger import get_logger

logger = get_logger("ProjectService")


@dataclass
class ProjectFileMetadata:
    filename: str
    file_type: str
    file_size_bytes: int
    estimated_tokens: int
    source_type: str  # "upload", "scraped_url", "agent_artifact"
    source_url: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size_bytes": self.file_size_bytes,
            "estimated_tokens": self.estimated_tokens,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "created_at": self.created_at
        }


class ProjectService:
    def __init__(self, base_dir: str = "data/projects", manifest_path: str = "data/projects.json"):
        self.base_dir = os.path.abspath(base_dir)
        self.manifest_path = os.path.abspath(manifest_path)
        self.data: Dict[str, Any] = {"active_project": None, "projects": {}}
        self._session: Optional[aiohttp.ClientSession] = None

        os.makedirs(self.base_dir, exist_ok=True)
        self.load_manifest()

    async def _get_session(self) -> aiohttp.ClientSession:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if (
            self._session is None
            or self._session.closed
            or (loop is not None and getattr(self._session, "_loop", loop) is not loop)
        ):
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # --- Manifest Management ---
    def load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"[PROJECT] Failed to load manifest: {e}")
                self.data = {"active_project": None, "projects": {}}
        else:
            self.save_manifest()

    def save_manifest(self):
        os.makedirs(os.path.dirname(self.manifest_path), exist_ok=True)
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"[PROJECT] Error saving manifest: {e}")

    # --- Project Lifecycle ---
    def create_project(self, project_id: str, name: str, description: str = "", instructions: str = "") -> Dict[str, Any]:
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', project_id.lower().strip())
        if not clean_id:
            clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower().strip()) or "project"
        if clean_id in self.data["projects"]:
            return {"status": "error", "message": f"Project '{clean_id}' already exists."}

        p_dir = os.path.join(self.base_dir, clean_id, "files")
        os.makedirs(p_dir, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        project_obj = {
            "id": clean_id,
            "name": name.strip(),
            "description": description.strip(),
            "custom_instructions": instructions.strip(),
            "notebooklm": {
                "sync_enabled": False,
                "notebook_id": None
            },
            "created_at": now,
            "updated_at": now
        }

        self.data["projects"][clean_id] = project_obj
        if not self.data.get("active_project"):
            self.data["active_project"] = clean_id

        self.save_manifest()
        return {"status": "success", "project": project_obj}

    def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        sync_enabled: Optional[bool] = None,
        notebook_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if project_id not in self.data.get("projects", {}):
            return {"status": "error", "message": f"Project '{project_id}' not found."}

        proj = self.data["projects"][project_id]
        if name is not None:
            proj["name"] = name.strip()
        if description is not None:
            proj["description"] = description.strip()
        if custom_instructions is not None:
            proj["custom_instructions"] = custom_instructions.strip()

        nb = proj.setdefault("notebooklm", {"sync_enabled": False, "notebook_id": None})
        if sync_enabled is not None:
            nb["sync_enabled"] = bool(sync_enabled)
        if notebook_id is not None:
            nb["notebook_id"] = notebook_id

        proj["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save_manifest()
        return {"status": "success", "project": proj}

    def delete_project(self, project_id: str) -> bool:
        if project_id not in self.data["projects"]:
            return False

        del self.data["projects"][project_id]
        if self.data.get("active_project") == project_id:
            remaining = list(self.data["projects"].keys())
            self.data["active_project"] = remaining[0] if remaining else None

        self.save_manifest()

        p_dir = os.path.join(self.base_dir, project_id)
        if os.path.exists(p_dir):
            shutil.rmtree(p_dir, ignore_errors=True)

        return True

    def get_project_dir(self, project_id: str) -> str:
        return os.path.join(self.base_dir, project_id, "files")

    # --- Google Drive Desktop Two-Way Sync ---
    @staticmethod
    def find_google_drive_root() -> Optional[str]:
        """Auto-detects a local Google Drive for Desktop mount (e.g. G:\\My Drive)."""
        if os.getenv("AETHER_TESTING") == "1":
            return None
        candidates = ["G:\\My Drive"]
        for letter in "DEFHIJKLMNOPQRSTUVWXYZ":
            candidates.append(f"{letter}:\\My Drive")
        user_prof = os.path.expanduser("~")
        candidates.append(os.path.join(user_prof, "Google Drive", "My Drive"))
        candidates.append(os.path.join(user_prof, "Google Drive"))
        for path in candidates:
            if os.path.isdir(path):
                return path
        return None

    def get_drive_project_dir(self, project_id: str) -> Optional[str]:
        """Returns the Google Drive folder path for a given project (e.g. G:\\My Drive\\Aether Projects\\Aether Desktop)."""
        drive_root = self.find_google_drive_root()
        if not drive_root:
            return None
        proj = self.data.get("projects", {}).get(project_id, {})
        proj_name = proj.get("name") or project_id
        safe_folder = re.sub(r'[<>:"/\\|?*]', '_', proj_name).strip() or project_id
        return os.path.join(drive_root, "Aether Projects", safe_folder)

    def sync_with_google_drive(self, project_id: str) -> Dict[str, Any]:
        """
        Bi-directionally syncs files between data/projects/{project_id}/files/
        and G:\\My Drive\\Aether Projects\\{Project Name}\\ so files are accessible
        in Google Drive, NotebookLM web, and Gemini web.
        """
        if project_id not in self.data.get("projects", {}):
            return {"status": "error", "message": f"Project '{project_id}' not found."}

        drive_dir = self.get_drive_project_dir(project_id)
        if not drive_dir:
            return {"status": "skipped", "message": "Google Drive mount not found."}

        local_dir = self.get_project_dir(project_id)
        os.makedirs(local_dir, exist_ok=True)
        os.makedirs(drive_dir, exist_ok=True)

        pushed = 0
        pulled = 0

        # 1. Push Local -> Google Drive (new or newer local files)
        for fname in os.listdir(local_dir):
            local_fpath = os.path.join(local_dir, fname)
            if not os.path.isfile(local_fpath):
                continue
            drive_fpath = os.path.join(drive_dir, fname)
            try:
                if not os.path.exists(drive_fpath) or (os.path.getmtime(local_fpath) - os.path.getmtime(drive_fpath) > 1.0):
                    shutil.copy2(local_fpath, drive_fpath)
                    pushed += 1
            except Exception as e:
                logger.warning(f"[PROJECT] Failed copying '{fname}' to Google Drive: {e}")

        # 2. Pull Google Drive -> Local (files added/edited in Google Drive)
        allowed_exts = {
            ".pdf", ".txt", ".md", ".csv", ".py", ".json", ".js", ".html", ".css",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf"
        }
        for fname in os.listdir(drive_dir):
            if fname.startswith(".") or fname.lower() == "desktop.ini":
                continue
            drive_fpath = os.path.join(drive_dir, fname)
            if not os.path.isfile(drive_fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in allowed_exts:
                continue
            local_fpath = os.path.join(local_dir, fname)
            try:
                if not os.path.exists(local_fpath) or (os.path.getmtime(drive_fpath) - os.path.getmtime(local_fpath) > 1.0):
                    with open(drive_fpath, "rb") as df:
                        raw_bytes = df.read()
                    shutil.copy2(drive_fpath, local_fpath)
                    est_tokens = len(raw_bytes) // 4
                    extracted = self.extract_file_text(local_fpath)
                    if extracted:
                        est_tokens = self.estimate_tokens(extracted)
                    meta = ProjectFileMetadata(
                        filename=fname,
                        file_type=ext,
                        file_size_bytes=len(raw_bytes),
                        estimated_tokens=est_tokens,
                        source_type="google_drive",
                    )
                    self._update_file_metadata(project_id, meta)
                    pulled += 1
            except Exception as e:
                logger.warning(f"[PROJECT] Failed pulling '{fname}' from Google Drive: {e}")

        proj = self.data["projects"][project_id]
        nb = proj.setdefault("notebooklm", {"sync_enabled": False, "notebook_id": None})
        nb["drive_folder"] = drive_dir
        nb["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        self.save_manifest()

        return {
            "status": "success",
            "drive_folder": drive_dir,
            "pushed": pushed,
            "pulled": pulled,
        }

    # --- Document & Token Management ---
    @staticmethod
    def estimate_tokens(text: str) -> int:
        # Standard heuristic: ~4 characters per token for English prose and code
        return max(1, len(text) // 4)

    @staticmethod
    def extract_pdf_form_fields(file_path_or_bytes: Any) -> Dict[str, str]:
        """
        Extracts interactive form fields and AcroForm widget annotations from a PDF file or bytes.
        Fast, deterministic splitting without regular expression catastrophic backtracking.
        """
        try:
            if isinstance(file_path_or_bytes, bytes):
                content = file_path_or_bytes
            elif isinstance(file_path_or_bytes, str) and os.path.exists(file_path_or_bytes):
                with open(file_path_or_bytes, "rb") as f:
                    content = f.read()
            else:
                return {}
        except Exception:
            return {}

        fields: Dict[str, str] = {}
        objects = content.split(b"endobj")
        for obj in objects:
            if b"/Subtype/Widget" in obj or b"/Subtype /Widget" in obj or b"/FT" in obj:
                t_m = re.search(rb'/T\s*\(([^)]+)\)', obj)
                v_m = re.search(rb'/V\s*(?:\(([^)]*)\)|/([A-Za-z0-9_]+))', obj)
                if t_m and v_m:
                    k = t_m.group(1).decode("latin-1", errors="ignore").strip()
                    v = (v_m.group(1) or v_m.group(2) or b"").decode("latin-1", errors="ignore").strip()
                    v = v.replace("\\(", "(").replace("\\)", ")")
                    if k and v:
                        fields[k] = v
        return fields

    @staticmethod
    def extract_file_text(file_path: str) -> str:
        """Extracts plain text from a file on disk (supports PDF, DOCX, DOC, XLSX, XLS, PPTX, RTF, and UTF-8 text files)."""
        if not os.path.exists(file_path):
            return ""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            extracted_pages: List[str] = []
            try:
                from PySide6.QtPdf import QPdfDocument
                doc = QPdfDocument(None)
                doc.load(file_path)
                for i in range(doc.pageCount()):
                    page_txt = doc.getAllText(i).text()
                    if page_txt:
                        extracted_pages.append(page_txt)
            except Exception as e:
                logger.debug(f"[PROJECT] QPdfDocument extraction fallback for '{file_path}': {e}")

            form_fields = ProjectService.extract_pdf_form_fields(file_path)
            ff_parts = []
            if form_fields:
                ff_parts.append("=== PDF FORM FIELDS ===")
                for k in sorted(form_fields.keys()):
                    ff_parts.append(f"{k}: {form_fields[k]}")

            combined_pdf_text = "\n\n".join(filter(None, ["\n\n".join(extracted_pages).strip(), "\n".join(ff_parts).strip()])).strip()
            if combined_pdf_text:
                return combined_pdf_text
            return ""

        elif ext == ".docx":
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(file_path, "r") as zf:
                    if "word/document.xml" in zf.namelist():
                        xml_bytes = zf.read("word/document.xml")
                        root = ET.fromstring(xml_bytes)
                        paragraphs = []
                        for p in root.iter():
                            if p.tag.endswith("}p"):
                                texts = [t.text for t in p.iter() if t.tag.endswith("}t") and t.text]
                                if texts:
                                    paragraphs.append("".join(texts))
                        return "\n".join(paragraphs).strip()
            except Exception as e:
                logger.debug(f"[PROJECT] DOCX extraction error for '{file_path}': {e}")

        elif ext == ".xlsx":
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(file_path, "r") as zf:
                    shared_strings: List[str] = []
                    if "xl/sharedStrings.xml" in zf.namelist():
                        ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                        for si in ss_root.iter():
                            if si.tag.endswith("}si"):
                                parts = [t.text for t in si.iter() if t.tag.endswith("}t") and t.text]
                                shared_strings.append("".join(parts))

                    sheet_files = sorted(
                        name for name in zf.namelist()
                        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                    )
                    sheets_out = []
                    for s_idx, s_name in enumerate(sheet_files, start=1):
                        s_root = ET.fromstring(zf.read(s_name))
                        rows_out = [f"### Sheet {s_idx}"]
                        for row in s_root.iter():
                            if row.tag.endswith("}row"):
                                row_vals = []
                                for cell in row:
                                    if not cell.tag.endswith("}c"):
                                        continue
                                    c_type = cell.attrib.get("t", "")
                                    val_text = ""
                                    if c_type == "inlineStr":
                                        t_parts = [t.text for t in cell.iter() if t.tag.endswith("}t") and t.text]
                                        val_text = "".join(t_parts)
                                    else:
                                        for child in cell:
                                            if child.tag.endswith("}v") and child.text is not None:
                                                raw_v = child.text
                                                if c_type == "s" and raw_v.isdigit():
                                                    idx = int(raw_v)
                                                    val_text = shared_strings[idx] if 0 <= idx < len(shared_strings) else raw_v
                                                else:
                                                    val_text = raw_v
                                    row_vals.append(val_text)
                                if any(v != "" for v in row_vals):
                                    rows_out.append(" | ".join(row_vals))
                        if len(rows_out) > 1:
                            sheets_out.append("\n".join(rows_out))
                    return "\n\n".join(sheets_out).strip()
            except Exception as e:
                logger.debug(f"[PROJECT] XLSX extraction error for '{file_path}': {e}")

        elif ext == ".pptx":
            try:
                import zipfile
                import xml.etree.ElementTree as ET
                with zipfile.ZipFile(file_path, "r") as zf:
                    slide_files = sorted(
                        name for name in zf.namelist()
                        if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                    )
                    slides_out = []
                    for idx, s_name in enumerate(slide_files, start=1):
                        s_root = ET.fromstring(zf.read(s_name))
                        lines = [f"### Slide {idx}"]
                        for p in s_root.iter():
                            if p.tag.endswith("}p"):
                                texts = [t.text for t in p.iter() if t.tag.endswith("}t") and t.text]
                                if texts:
                                    lines.append("".join(texts))
                        if len(lines) > 1:
                            slides_out.append("\n".join(lines))
                    return "\n\n".join(slides_out).strip()
            except Exception as e:
                logger.debug(f"[PROJECT] PPTX extraction error for '{file_path}': {e}")

        elif ext == ".rtf":
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_rtf = f.read()
                cleaned = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw_rtf)
                cleaned = re.sub(r"\\[a-zA-Z]+\d*\s?", " ", cleaned)
                cleaned = re.sub(r"[{}]", "", cleaned)
                return re.sub(r"\s+", " ", cleaned).strip()
            except Exception:
                pass

        elif ext in (".doc", ".xls", ".ppt"):
            try:
                with open(file_path, "rb") as f:
                    raw_bytes = f.read()
                utf16_chunks = [
                    m.group(0).decode("utf-16le", errors="ignore")
                    for m in re.finditer(rb"(?:[\x20-\x7e\r\n\t]\x00){5,}", raw_bytes)
                ]
                ascii_chunks = [
                    m.group(0).decode("latin-1", errors="ignore")
                    for m in re.finditer(rb"[\x20-\x7e\r\n\t]{6,}", raw_bytes)
                ]
                combined = "\n".join(utf16_chunks + ascii_chunks).strip()
                if combined:
                    return combined
            except Exception:
                pass

        if ext in (".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".exe", ".bin"):
            return ""

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    def read_file_text(self, project_id: str, filename: str) -> Optional[str]:
        p_dir = self.get_project_dir(project_id)
        target = os.path.join(p_dir, filename)
        if not os.path.exists(target):
            return None
        return self.extract_file_text(target)

    def list_files(self, project_id: str) -> List[Dict[str, Any]]:
        p_dir = self.get_project_dir(project_id)
        if not os.path.exists(p_dir):
            return []

        files_meta_path = os.path.join(self.base_dir, project_id, "metadata.json")
        saved_meta = {}
        if os.path.exists(files_meta_path):
            try:
                with open(files_meta_path, "r", encoding="utf-8") as f:
                    saved_meta = json.load(f)
            except Exception:
                saved_meta = {}

        results = []
        for fname in os.listdir(p_dir):
            fpath = os.path.join(p_dir, fname)
            if not os.path.isfile(fpath):
                continue

            if fname in saved_meta:
                results.append(saved_meta[fname])
            else:
                sz = os.path.getsize(fpath)
                ext = os.path.splitext(fname)[1].lower()
                toks = sz // 4
                meta = ProjectFileMetadata(
                    filename=fname,
                    file_type=ext,
                    file_size_bytes=sz,
                    estimated_tokens=toks,
                    source_type="upload"
                ).to_dict()
                results.append(meta)

        return results

    def add_file(self, project_id: str, filename: str, content_bytes: bytes, source_type: str = "upload", source_url: Optional[str] = None) -> Dict[str, Any]:
        p_dir = self.get_project_dir(project_id)
        os.makedirs(p_dir, exist_ok=True)

        safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        target_path = os.path.join(p_dir, safe_filename)

        with open(target_path, "wb") as f:
            f.write(content_bytes)

        # Estimate tokens
        est_tokens = len(content_bytes) // 4
        try:
            extracted_text = self.extract_file_text(target_path)
            if extracted_text:
                est_tokens = self.estimate_tokens(extracted_text)
        except Exception:
            pass

        meta = ProjectFileMetadata(
            filename=safe_filename,
            file_type=os.path.splitext(safe_filename)[1].lower(),
            file_size_bytes=len(content_bytes),
            estimated_tokens=est_tokens,
            source_type=source_type,
            source_url=source_url
        )

        self._update_file_metadata(project_id, meta)

        # Mirror to Google Drive folder if Cloud Sync is enabled
        proj = self.data.get("projects", {}).get(project_id, {})
        if proj.get("notebooklm", {}).get("sync_enabled"):
            drive_dir = self.get_drive_project_dir(project_id)
            if drive_dir:
                try:
                    os.makedirs(drive_dir, exist_ok=True)
                    shutil.copy2(target_path, os.path.join(drive_dir, safe_filename))
                except Exception as e:
                    logger.debug(f"[PROJECT] Mirror to Google Drive warning: {e}")

        return meta.to_dict()

    def _update_file_metadata(self, project_id: str, meta: ProjectFileMetadata):
        meta_path = os.path.join(self.base_dir, project_id, "metadata.json")
        saved = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
            except Exception:
                saved = {}

        saved[meta.filename] = meta.to_dict()
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)

    def delete_file(self, project_id: str, filename: str) -> bool:
        p_dir = self.get_project_dir(project_id)
        target = os.path.join(p_dir, filename)
        if os.path.exists(target):
            os.remove(target)

        # Also remove from Google Drive mirror if present
        drive_dir = self.get_drive_project_dir(project_id)
        if drive_dir:
            drive_target = os.path.join(drive_dir, filename)
            if os.path.exists(drive_target):
                try:
                    os.remove(drive_target)
                except Exception:
                    pass

        meta_path = os.path.join(self.base_dir, project_id, "metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if filename in saved:
                    del saved[filename]
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(saved, f, indent=2)
            except Exception:
                pass
        return True

    # --- Web URL Scraper & Markdown Converter ---
    async def import_url(self, project_id: str, url: str) -> Dict[str, Any]:
        """Fetches web content, strips HTML markup/scripts, and saves clean Markdown."""
        session = await self._get_session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AetherAssistant/1.0"
        }

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return {"status": "error", "message": f"HTTP {resp.status} while fetching {url}"}

                html_text = await resp.text()
        except Exception as e:
            logger.error(f"[PROJECT] Failed to fetch URL '{url}': {e}")
            return {"status": "error", "message": f"Network error: {str(e)}"}

        # Extract title
        title_match = re.search(r'<title>(.*?)</title>', html_text, re.IGNORECASE | re.DOTALL)
        page_title = title_match.group(1).strip() if title_match else "scraped_page"
        clean_title = re.sub(r'[^a-zA-Z0-9_-]', '_', page_title)[:40]

        # Strip scripts, styles, boilerplates
        clean_html = re.sub(r'<script.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<nav.*?</nav>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<footer.*?</footer>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)

        # Convert simple tags to markdown syntax
        clean_html = re.sub(r'<h[1-6]>(.*?)</h[1-6]>', r'\n## \1\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<p>(.*?)</p>', r'\n\1\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<br\s*/?>', '\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', clean_html, flags=re.IGNORECASE)
        clean_text = re.sub(r'<[^>]+>', ' ', clean_html)
        clean_text = re.sub(r'\n\s*\n', '\n\n', clean_text).strip()

        final_md = f"# {page_title}\n**Source:** {url}\n**Ingested:** {datetime.now(timezone.utc).isoformat()}\n\n---\n\n{clean_text}"
        filename = f"{clean_title}.md"

        meta = self.add_file(
            project_id=project_id,
            filename=filename,
            content_bytes=final_md.encode("utf-8"),
            source_type="scraped_url",
            source_url=url
        )
        return {"status": "success", "metadata": meta, "file": meta}
