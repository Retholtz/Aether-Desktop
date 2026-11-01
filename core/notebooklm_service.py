"""
core/notebooklm_service.py - Client for Gemini Cloud RAG (FileSearchStores API)
Handles cloud store creation, source synchronization, and semantic chunk retrieval.
"""
import asyncio
import io
import mimetypes
import os
from typing import Optional, Dict, Any, List
import aiohttp
from google import genai
from google.genai import types
from core.logger import get_logger

logger = get_logger("NotebookLM")

NOTEBOOK_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

MIME_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".py": "text/x-python",
    ".json": "application/json",
    ".js": "text/javascript",
    ".html": "text/html",
    ".css": "text/css",
}


class NotebookLMService:
    def __init__(self, api_key: Optional[str] = None):
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = self._resolve_default_api_key()
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _resolve_default_api_key() -> str:
        env_key = os.getenv("GEMINI_API_KEY", "")
        if env_key:
            return env_key
        if os.getenv("AETHER_TESTING") == "1":
            return ""
        try:
            import json
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                enc = cfg.get("api", {}).get("api_key_encrypted", "")
                if enc:
                    from security.crypto import unprotect_secret
                    return unprotect_secret(enc) or ""
        except Exception:
            pass
        return ""

    def _get_genai_client(self) -> genai.Client:
        if not self.api_key:
            self.api_key = self._resolve_default_api_key()
        return genai.Client(api_key=self.api_key)

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
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20.0))
        return self._session

    def is_configured(self) -> bool:
        if not self.api_key and os.getenv("AETHER_TESTING") != "1":
            self.api_key = self._resolve_default_api_key()
        return bool(self.api_key)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _normalize_store_name(notebook_id: str) -> str:
        clean = (notebook_id or "").strip()
        if not clean:
            return ""
        if not clean.startswith("fileSearchStores/"):
            return f"fileSearchStores/{clean}"
        return clean

    @staticmethod
    def _guess_mime_type(filename: str) -> str:
        ext = os.path.splitext(filename or "")[1].lower()
        if ext in MIME_TYPE_MAP:
            return MIME_TYPE_MAP[ext]
        guessed, _ = mimetypes.guess_type(filename or "")
        return guessed or "text/plain"

    # --- Cloud Store Management ---
    async def list_notebooks(self) -> List[Dict[str, Any]]:
        """Lists available Gemini Cloud File Search Stores."""
        if not self.is_configured():
            return []

        def _sync_list():
            client = self._get_genai_client()
            results = []
            for store in client.file_search_stores.list():
                results.append({
                    "name": getattr(store, "name", ""),
                    "title": getattr(store, "display_name", ""),
                })
            return results

        try:
            return await asyncio.to_thread(_sync_list)
        except Exception as e:
            logger.error(f"[NOTEBOOK] Error listing cloud stores: {e}")
            return []

    async def create_notebook(self, title: str) -> Optional[Dict[str, Any]]:
        """Creates a Gemini Cloud File Search Store container for project RAG."""
        if not self.is_configured():
            return None

        def _sync_create():
            client = self._get_genai_client()
            store = client.file_search_stores.create(config={"display_name": title})
            store_name = getattr(store, "name", "")
            return {
                "name": store_name,
                "notebookId": store_name,
                "title": getattr(store, "display_name", title) or title,
            }

        try:
            res = await asyncio.to_thread(_sync_create)
            logger.info(f"[NOTEBOOK] Created cloud FileSearchStore '{res.get('name')}' for '{title}'")
            return res
        except Exception as e:
            logger.error(f"[NOTEBOOK] Create cloud store failed: {e}")
            return None

    async def delete_notebook(self, notebook_id: str) -> bool:
        """Deletes a Gemini Cloud File Search Store container."""
        if not self.is_configured() or not notebook_id:
            return False

        store_name = self._normalize_store_name(notebook_id)

        def _sync_delete():
            client = self._get_genai_client()
            client.file_search_stores.delete(name=store_name, config={"force": True})
            return True

        try:
            return await asyncio.to_thread(_sync_delete)
        except Exception as e:
            logger.warning(f"[NOTEBOOK] Delete cloud store warning ({store_name}): {e}")
            return False

    # --- Source Synchronization ---
    async def batch_create_sources(self, notebook_id: str, sources: List[Dict[str, Any]]) -> bool:
        """
        Uploads or syncs source content into the Gemini Cloud FileSearchStore.
        Supported format per source:
          - {"displayName": str, "filePath": str}
          - {"displayName": str, "contentBytes": bytes}
          - {"displayName": str, "textContent": str}
        """
        if not self.is_configured() or not notebook_id or not sources:
            return False

        store_name = self._normalize_store_name(notebook_id)

        def _sync_upload():
            client = self._get_genai_client()
            uploaded_any = False
            for src in sources:
                display_name = src.get("displayName") or src.get("filename") or "document.txt"
                mime_type = src.get("mimeType") or self._guess_mime_type(display_name)
                file_path = src.get("filePath")
                content_bytes = src.get("contentBytes")
                text_content = src.get("textContent")

                if file_path and os.path.exists(file_path):
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".rtf"):
                        from core.project_service import ProjectService
                        extracted = ProjectService.extract_file_text(file_path)
                        bio = io.BytesIO((extracted or "").encode("utf-8"))
                        client.file_search_stores.upload_to_file_search_store(
                            file_search_store_name=store_name,
                            file=bio,
                            config={"display_name": display_name, "mime_type": "text/plain"},
                        )
                    else:
                        client.file_search_stores.upload_to_file_search_store(
                            file_search_store_name=store_name,
                            file=file_path,
                            config={"display_name": display_name, "mime_type": mime_type},
                        )
                    uploaded_any = True
                elif content_bytes is not None:
                    bio = io.BytesIO(content_bytes)
                    client.file_search_stores.upload_to_file_search_store(
                        file_search_store_name=store_name,
                        file=bio,
                        config={"display_name": display_name, "mime_type": mime_type},
                    )
                    uploaded_any = True
                elif text_content is not None:
                    bio = io.BytesIO(text_content.encode("utf-8"))
                    client.file_search_stores.upload_to_file_search_store(
                        file_search_store_name=store_name,
                        file=bio,
                        config={"display_name": display_name, "mime_type": mime_type},
                    )
                    uploaded_any = True
            return uploaded_any

        try:
            ok = await asyncio.to_thread(_sync_upload)
            if ok:
                logger.info(f"[NOTEBOOK] Synced {len(sources)} source(s) to cloud store '{store_name}'")
            return ok
        except Exception as e:
            logger.error(f"[NOTEBOOK] Failed syncing sources to '{store_name}': {e}")
            return False

    # --- Semantic RAG Retrieval ---
    async def retrieve_relevant_chunks(self, notebook_id: str, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        """Queries the Gemini Cloud FileSearchStore for semantically ranked passages with source references."""
        if not self.is_configured() or not notebook_id or not query:
            return []

        store_name = self._normalize_store_name(notebook_id)

        def _sync_retrieve():
            client = self._get_genai_client()
            resp = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[
                        types.Tool(
                            file_search=types.FileSearch(
                                file_search_store_names=[store_name],
                                top_k=top_k,
                            )
                        )
                    ]
                ),
            )
            chunks: List[Dict[str, Any]] = []
            candidates = getattr(resp, "candidates", None) or []
            if candidates:
                gm = getattr(candidates[0], "grounding_metadata", None)
                g_chunks = getattr(gm, "grounding_chunks", None) or []
                for gc in g_chunks:
                    rc = getattr(gc, "retrieved_context", None)
                    if rc:
                        title = getattr(rc, "title", None) or getattr(rc, "document_name", None) or "Cloud Document"
                        text = getattr(rc, "text", None) or ""
                        if text:
                            chunks.append({
                                "sourceDisplayName": title,
                                "text": text,
                            })
            if not chunks and getattr(resp, "text", None):
                chunks.append({
                    "sourceDisplayName": "Gemini Cloud Store",
                    "text": resp.text,
                })
            return chunks

        try:
            return await asyncio.to_thread(_sync_retrieve)
        except Exception as e:
            logger.error(f"[NOTEBOOK] Error retrieving chunks from '{store_name}': {e}")
            return []
