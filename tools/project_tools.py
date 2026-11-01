"""
tools/project_tools.py - Agent Tools for Workspace Knowledge Retrieval
Allows Aether to read files, ingest URLs, save research artifacts, and query NotebookLM.
"""
import os
from typing import Dict, Any, Optional
from core.project_service import ProjectService
from core.notebooklm_service import NotebookLMService


def register_project_tools(dispatcher, project_service: ProjectService, notebook_service: NotebookLMService):

    def _get_target_project(explicit_id: Optional[str] = None) -> Optional[str]:
        return explicit_id or project_service.data.get("active_project")

    # 1. Query Project Knowledge (Hybrid: NotebookLM RAG + Local Fallback)
    async def query_project_knowledge_base(query: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Queries the current project's knowledge base and document repository.
        Uses NotebookLM semantic chunk retrieval if linked, otherwise inspects local documents.
        """
        pid = _get_target_project(project_id)
        if not pid or pid not in project_service.data["projects"]:
            return {"status": "error", "message": "No active project selected."}

        p_data = project_service.data["projects"][pid]
        nb_cfg = p_data.get("notebooklm", {})

        # Route A: Query Cloud NotebookLM RAG
        if nb_cfg.get("sync_enabled") and nb_cfg.get("notebook_id") and notebook_service.is_configured():
            chunks = await notebook_service.retrieve_relevant_chunks(nb_cfg["notebook_id"], query, top_k=4)
            if chunks:
                formatted_passages = []
                for c in chunks:
                    source_title = c.get("sourceDisplayName", "Document")
                    text = c.get("text", "")
                    formatted_passages.append(f"[{source_title}]: {text}")
                return {
                    "status": "success",
                    "mode": "notebooklm_rag",
                    "project_id": pid,
                    "results": formatted_passages
                }

        # Route B: Local Document List
        files = project_service.list_files(pid)
        return {
            "status": "success",
            "mode": "local_manifest",
            "project_id": pid,
            "message": f"NotebookLM unlinked. Project contains {len(files)} files.",
            "available_files": [f["filename"] for f in files]
        }

    # 2. Read Local Document
    async def read_project_document(filename: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Reads the full text of a specific file inside the project directory (including PDF text extraction)."""
        pid = _get_target_project(project_id)
        if not pid:
            return {"status": "error", "message": "No active project selected."}

        p_dir = project_service.get_project_dir(pid)
        target = os.path.join(p_dir, filename)
        if not os.path.exists(target):
            return {"status": "error", "message": f"File '{filename}' does not exist in project '{pid}'."}

        try:
            content = project_service.extract_file_text(target)
            return {"status": "success", "filename": filename, "content": content}
        except Exception as e:
            return {"status": "error", "message": f"Failed reading file: {e}"}

    # 3. Save Research / Code Artifact
    async def save_project_artifact(filename: str, content: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Saves an agent-generated markdown report, notes, or code file into the active project."""
        pid = _get_target_project(project_id)
        if not pid:
            return {"status": "error", "message": "No active project selected."}

        meta = project_service.add_file(
            project_id=pid,
            filename=filename,
            content_bytes=content.encode("utf-8"),
            source_type="agent_artifact"
        )

        # Sync to NotebookLM if active
        p_data = project_service.data["projects"].get(pid, {})
        nb_cfg = p_data.get("notebooklm", {})
        if nb_cfg.get("sync_enabled") and nb_cfg.get("notebook_id"):
            await notebook_service.batch_create_sources(
                nb_cfg["notebook_id"],
                [{"displayName": filename, "textContent": content}]
            )

        return {"status": "success", "saved": meta}

    # 4. Import Web URL
    async def import_project_url(url: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        """Scrapes and converts a web page or online documentation directly into a markdown document in the project."""
        pid = _get_target_project(project_id)
        if not pid:
            return {"status": "error", "message": "No active project selected."}

        res = await project_service.import_url(pid, url)
        return res

    # Register tools
    dispatcher.register_tool(
        name="query_project_knowledge_base",
        description="Searches project documents and retrieves ranked semantic passages from NotebookLM or local files.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search prompt or question to resolve against project docs."},
                "project_id": {"type": "string", "description": "Optional project identifier. Uses active project if omitted."}
            },
            "required": ["query"]
        },
        handler=query_project_knowledge_base
    )

    dispatcher.register_tool(
        name="read_project_document",
        description="Reads the complete content of a document stored inside the active project.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Name of the file to open (e.g. 'notes.md')."},
                "project_id": {"type": "string", "description": "Optional project identifier."}
            },
            "required": ["filename"]
        },
        handler=read_project_document
    )

    dispatcher.register_tool(
        name="save_project_artifact",
        description="Saves a generated report, notes, or code file directly into the project repository.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "File name (e.g. 'summary.md', 'script.py')."},
                "content": {"type": "string", "description": "Text or code content to save."},
                "project_id": {"type": "string", "description": "Optional project identifier."}
            },
            "required": ["filename", "content"]
        },
        handler=save_project_artifact
    )

    dispatcher.register_tool(
        name="import_project_url",
        description="Scrapes a web page or documentation link and adds it as a structured Markdown file in the project.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Web URL to scrape and store."},
                "project_id": {"type": "string", "description": "Optional project identifier."}
            },
            "required": ["url"]
        },
        handler=import_project_url
    )
