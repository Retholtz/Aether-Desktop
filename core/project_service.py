"""
core/project_service.py - Project Workspace & Document Ingestion Manager
Manages local file storage, token calculation, and asynchronous web scraping.
"""
import asyncio
import os
import io
import re
import json
import shutil
import html
import ssl
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import certifi
import requests
import aiohttp
from core.logger import get_logger

logger = get_logger("ProjectService")


DEFAULT_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


def is_bot_challenge_page(text: str) -> bool:
    """Detects whether returned HTML is a Cloudflare / Akamai bot challenge page."""
    if not text:
        return False
    lower = text.lower()
    return (
        "<title>just a moment...</title>" in lower
        or "<title>attention required!" in lower
        or "security check to access" in lower
        or ("cloudflare" in lower and "turnstile" in lower and "<title>" in lower)
    )


def find_system_browser() -> Optional[str]:
    """Locates Edge or Chrome binary on the local system for headless scraping fallback."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
        shutil.which("chrome"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def fetch_with_headless_browser(url: str, timeout: int = 15) -> Optional[str]:
    """
    Fallback headless browser fetch for sites guarded by strict CDN/WAF anti-bot protections.
    Runs headless Chromium/Edge with anti-bot automation flags disabled to dump the DOM.
    """
    browser_exe = find_system_browser()
    if not browser_exe:
        return None
    try:
        cmd = [
            browser_exe,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            "--disable-blink-features=AutomationControlled",
            f"--user-agent={DEFAULT_SCRAPE_HEADERS['User-Agent']}",
            "--dump-dom",
            url
        ]
        flags = 0x08000000 if sys.platform == "win32" else 0
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags
        )
        if proc.stdout and "<html" in proc.stdout.lower() and len(proc.stdout) > 200:
            if not is_bot_challenge_page(proc.stdout):
                return proc.stdout
    except Exception as e:
        logger.warning(f"[PROJECT] Headless browser fetch failed for '{url}': {e}")
    return None


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

    def build_project_system_prompt(self, project_id: str) -> str:
        """Constructs dedicated system instructions for an active project workspace."""
        clean_id = (project_id or "").strip()
        if not clean_id or clean_id not in self.data.get("projects", {}):
            return ""
        proj = self.data["projects"][clean_id]
        name = proj.get("name") or clean_id
        desc = (proj.get("description") or "").strip()
        custom_inst = (proj.get("custom_instructions") or "").strip()
        files = self.list_files(clean_id)

        lines = [
            f"[ACTIVE PROJECT WORKSPACE: {name} ({clean_id})]",
            "You are operating within this specific project context. Focus your research, answers, and assistance on this project's goals."
        ]
        if desc:
            lines.append(f"- Project Scope / Description: {desc}")
        if custom_inst:
            lines.append(f"- Project Directives & Instructions: {custom_inst}")
        if files:
            file_names = [f["filename"] for f in files if f.get("filename")]
            if file_names:
                lines.append(f"- Knowledge Base Documents ({len(file_names)} files): " + ", ".join(file_names))
                lines.append("Use your project tools (query_project_knowledge_base, read_project_document) to retrieve document contents when needed.")

        return "\n".join(lines) + "\n"

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
        Fast, deterministic extraction using pypdf with fallback to raw byte splitting.
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

        # 1. Primary: Use pypdf to extract fields and annotations (handles /ObjStm object streams and PDF string encodings)
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            raw_fields = reader.get_fields()
            if raw_fields:
                for k, v in raw_fields.items():
                    val = v.get('/V') if isinstance(v, dict) else getattr(v, 'value', None)
                    if val is not None:
                        fields[str(k).strip()] = str(val).strip()
            # If get_fields() was empty or missed widget annotations, walk page /Annots
            if not fields:
                for page in reader.pages:
                    annots = page.get('/Annots')
                    if annots:
                        annots_list = annots.get_object() if hasattr(annots, 'get_object') else annots
                        for a in annots_list:
                            try:
                                obj = a.get_object() if hasattr(a, 'get_object') else a
                                t = obj.get('/T')
                                v = obj.get('/V')
                                if t is not None:
                                    fields[str(t).strip()] = str(v) if v is not None else ''
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"[PROJECT] pypdf AcroForm extraction fallback: {e}")

        if fields:
            return fields

        objects = content.split(b"endobj")
        for obj in objects:
            if b"/Subtype/Widget" in obj or b"/Subtype /Widget" in obj or b"/FT" in obj:
                # 1. Field name /T (literal string or hex string)
                k = None
                t_hex = re.search(rb'/T\s*<([0-9A-Fa-f\s]+)>', obj)
                if t_hex:
                    h = re.sub(rb'\s+', b'', t_hex.group(1))
                    try:
                        raw_k = bytes.fromhex(h.decode('ascii'))
                        k = raw_k.decode('utf-16-be' if raw_k.startswith(b'\xfe\xff') else 'utf-8', errors='ignore').lstrip('\ufeff').strip()
                    except Exception:
                        k = t_hex.group(1).decode('latin-1', errors='ignore').strip()
                else:
                    t_lit = re.search(rb'/T\s*\(([^)]+)\)', obj)
                    if t_lit:
                        k = t_lit.group(1).decode("latin-1", errors="ignore").strip()

                if not k:
                    continue

                # 2. Field value /V (hex string, literal string with escapes, or name/number)
                v = None
                v_hex = re.search(rb'/V\s*<([0-9A-Fa-f\s]+)>', obj)
                if v_hex:
                    h = re.sub(rb'\s+', b'', v_hex.group(1))
                    try:
                        raw_v = bytes.fromhex(h.decode('ascii'))
                        if raw_v.startswith(b'\xfe\xff'):
                            v = raw_v.decode('utf-16-be', errors='replace').lstrip('\ufeff').strip()
                        else:
                            v = raw_v.decode('utf-8', errors='replace').strip()
                    except Exception:
                        v = v_hex.group(1).decode('latin-1', errors='ignore').strip()
                else:
                    v_pos = obj.find(b'/V')
                    if v_pos != -1:
                        p_start = obj.find(b'(', v_pos)
                        if p_start != -1 and p_start - v_pos < 12:
                            depth = 0
                            idx = p_start
                            end_pos = -1
                            while idx < len(obj):
                                b = obj[idx:idx+1]
                                if b == b'\\' and idx + 1 < len(obj):
                                    idx += 2
                                    continue
                                elif b == b'(':
                                    depth += 1
                                elif b == b')':
                                    depth -= 1
                                    if depth == 0:
                                        end_pos = idx
                                        break
                                idx += 1
                            if end_pos != -1:
                                raw_lit = obj[p_start+1:end_pos]
                                v = raw_lit.decode("latin-1", errors="ignore").replace(r"\(", "(").replace(r"\)", ")").replace(r"\r", "\r").replace(r"\n", "\n").strip()
                    if v is None:
                        v_name = re.search(rb'/V\s*(?:\/([A-Za-z0-9_]+)|([0-9]+))', obj)
                        if v_name:
                            v = (v_name.group(1) or v_name.group(2) or b"").decode("latin-1", errors="ignore").strip()

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
            # 1. Try pypdf (lightweight, robust pure-python extractor)
            try:
                import pypdf
                reader = pypdf.PdfReader(file_path)
                for page in reader.pages:
                    ptxt = page.extract_text()
                    if ptxt:
                        extracted_pages.append(ptxt)
            except Exception as e:
                logger.debug(f"[PROJECT] pypdf extraction fallback for '{file_path}': {e}")

            # 2. Fallback to PySide6.QtPdf if pypdf was unavailable or empty
            if not extracted_pages:
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
        """
        Fetches web content using multi-tiered resilience (aiohttp -> requests -> headless Edge/Chrome),
        strips HTML markup/scripts, and saves clean Markdown into the project repository.
        """
        raw_url = (url or "").strip()
        if not raw_url:
            return {"status": "error", "message": "No URL provided."}

        # Normalize URL scheme
        if not raw_url.startswith(("http://", "https://")):
            target_url = "https://" + raw_url
        else:
            target_url = raw_url

        parsed = urllib.parse.urlparse(target_url)
        if not parsed.netloc:
            return {"status": "error", "message": f"Invalid URL '{url}'"}

        html_text: Optional[str] = None
        last_error_status: Optional[int] = None
        last_error_msg: Optional[str] = None

        # Strategy 1: Async aiohttp with modern browser headers and certifi SSL
        try:
            session = await self._get_session()
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            async with session.get(
                target_url,
                headers=DEFAULT_SCRAPE_HEADERS,
                ssl=ssl_ctx,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    if not is_bot_challenge_page(body):
                        html_text = body
                    else:
                        logger.info(f"[PROJECT] aiohttp received challenge page for '{target_url}', attempting fallback...")
                else:
                    last_error_status = resp.status
                    last_error_msg = f"HTTP {resp.status}"
                    logger.info(f"[PROJECT] aiohttp received status {resp.status} for '{target_url}', attempting fallback...")
        except Exception as e:
            logger.info(f"[PROJECT] aiohttp fetch error for '{target_url}': {e}, attempting fallback...")
            last_error_msg = str(e)

        # Strategy 2: requests.Session fallback (handles cookies, complex redirect chains, and alternate TLS handling)
        if not html_text:
            def _requests_fetch() -> Optional[str]:
                try:
                    s = requests.Session()
                    s.headers.update(DEFAULT_SCRAPE_HEADERS)
                    r = s.get(target_url, timeout=10, verify=certifi.where())
                    if r.status_code == 200 and not is_bot_challenge_page(r.text):
                        return r.text
                except Exception as ex:
                    logger.debug(f"[PROJECT] requests fallback error for '{target_url}': {ex}")
                return None

            try:
                html_text = await asyncio.to_thread(_requests_fetch)
            except Exception:
                pass

        # Strategy 3: Headless Edge/Chrome fallback (bypasses WAF, Akamai, Cloudflare, executes client-side JS)
        if not html_text:
            logger.info(f"[PROJECT] Invoking headless browser fallback for '{target_url}'...")
            try:
                html_text = await asyncio.to_thread(fetch_with_headless_browser, target_url, 15)
            except Exception as e:
                logger.warning(f"[PROJECT] Headless browser fallback failed for '{target_url}': {e}")

        if not html_text:
            err = last_error_msg or (f"HTTP {last_error_status}" if last_error_status else "Unable to retrieve page content")
            return {"status": "error", "message": f"Failed to fetch {target_url} ({err})"}

        # Extract title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html_text, re.IGNORECASE | re.DOTALL)
        if title_match:
            raw_title = re.sub(r'\s+', ' ', title_match.group(1)).strip()
            page_title = html.unescape(raw_title)
        else:
            h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_text, re.IGNORECASE | re.DOTALL)
            if h1_match:
                page_title = html.unescape(re.sub(r'<[^>]+>', '', h1_match.group(1)).strip())
            else:
                page_title = parsed.netloc or "scraped_page"

        clean_title = re.sub(r'[^a-zA-Z0-9_-]', '_', page_title).strip('_')[:50]
        if not clean_title:
            clean_title = f"web_page_{int(time.time())}"

        # Strip scripts, styles, boilerplates, embeds
        clean_html = re.sub(r'<script.*?</script>', '', html_text, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<noscript.*?</noscript>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<svg.*?</svg>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<nav.*?</nav>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<header.*?</header>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)
        clean_html = re.sub(r'<footer.*?</footer>', '', clean_html, flags=re.DOTALL | re.IGNORECASE)

        # Convert simple tags to markdown syntax
        clean_html = re.sub(r'<h[1-6][^>]*>(.*?)</h[1-6]>', r'\n## \1\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<br\s*/?>', '\n', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', clean_html, flags=re.IGNORECASE)
        clean_html = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', clean_html, flags=re.IGNORECASE)
        clean_text = re.sub(r'<[^>]+>', ' ', clean_html)
        clean_text = html.unescape(clean_text)
        clean_text = re.sub(r'[ \t]+', ' ', clean_text)
        clean_text = re.sub(r'\n\s*\n', '\n\n', clean_text).strip()

        final_md = f"# {page_title}\n**Source:** {target_url}\n**Ingested:** {datetime.now(timezone.utc).isoformat()}\n\n---\n\n{clean_text}"
        filename = f"{clean_title}.md"

        meta = self.add_file(
            project_id=project_id,
            filename=filename,
            content_bytes=final_md.encode("utf-8"),
            source_type="scraped_url",
            source_url=target_url
        )
        return {"status": "success", "metadata": meta, "file": meta}
