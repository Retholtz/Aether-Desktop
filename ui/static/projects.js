/**
 * Aether Desktop - Projects Workspace & NotebookLM Cloud Sync Controller
 * Manages local document repositories, drag-and-drop file ingestion, URL scraping,
 * token estimation badges, custom project instructions, and NotebookLM RAG sync.
 */

let currentProjectsData = { active_project: null, projects: {} };
let activeProjectId = null;
let instructionsSaveTimer = null;

function escapeProjHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function loadProjectsUI() {
    try {
        if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_projects_data) {
            return;
        }
        const data = await window.pywebview.api.get_projects_data();
        currentProjectsData = data || { active_project: null, projects: {} };
        activeProjectId = currentProjectsData.active_project;

        const projects = currentProjectsData.projects || {};
        const pids = Object.keys(projects);
        if ((!activeProjectId || !projects[activeProjectId]) && pids.length > 0) {
            activeProjectId = pids[0];
        }

        renderProjectsSidebar();
        await renderActiveProjectWorkspace();
    } catch (err) {
        console.error("Failed loading projects UI:", err);
    }
}

function renderProjectsSidebar() {
    const listEl = document.getElementById('projects-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const projects = (currentProjectsData && currentProjectsData.projects) ? currentProjectsData.projects : {};
    const projectIds = Object.keys(projects).sort((a, b) => {
        const nameA = (projects[a]?.name || a).toLowerCase();
        const nameB = (projects[b]?.name || b).toLowerCase();
        return nameA.localeCompare(nameB, undefined, { numeric: true });
    });

    if (projectIds.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 16px; color: #7d8597; font-size: 0.82rem; text-align: center;">
                No projects linked.<br>Click <strong>+ Add</strong> above to create a workspace.
            </div>
        `;
        return;
    }

    projectIds.forEach(pid => {
        const proj = projects[pid];
        const isSync = Boolean(proj.notebooklm && proj.notebooklm.sync_enabled);
        const item = document.createElement('div');
        item.className = `project-item game-item ${pid === activeProjectId ? 'active' : ''}`;
        item.dataset.projectId = pid;
        item.onclick = () => selectProject(pid);

        item.innerHTML = `
            <span class="project-item-name game-item-name">${escapeProjHtml(proj.name || pid)}</span>
            <span class="badge-status ${isSync ? 'badge-online' : 'badge-offline'}">
                ${isSync ? '⚡ CLOUD' : 'LOCAL'}
            </span>
        `;
        listEl.appendChild(item);
    });
}

async function selectProject(pid) {
    if (!pid || pid === activeProjectId) return;
    activeProjectId = pid;
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.set_active_project(pid);
        }
        currentProjectsData.active_project = pid;
    } catch (e) {
        console.error("Failed setting active project:", e);
    }
    renderProjectsSidebar();
    await renderActiveProjectWorkspace();
}

async function renderActiveProjectWorkspace() {
    const projects = (currentProjectsData && currentProjectsData.projects) ? currentProjectsData.projects : {};
    const proj = activeProjectId ? projects[activeProjectId] : null;

    const titleEl = document.getElementById('project-title');
    const syncCheckbox = document.getElementById('notebooklm-sync-checkbox');
    const syncHint = document.getElementById('cloud-sync-status-hint');
    const syncBtn = document.getElementById('btn-sync-project');
    const driveBtn = document.getElementById('btn-open-drive-folder');
    const deleteBtn = document.getElementById('delete-project-btn');
    const instInput = document.getElementById('project-instructions');
    const filesBody = document.getElementById('project-files-body');

    if (!proj) {
        if (titleEl) titleEl.textContent = 'Select a Project';
        if (syncCheckbox) { syncCheckbox.checked = false; syncCheckbox.disabled = true; }
        if (syncHint) { syncHint.textContent = ''; syncHint.className = 'cloud-sync-status-hint'; }
        if (syncBtn) syncBtn.disabled = true;
        if (driveBtn) driveBtn.disabled = true;
        if (deleteBtn) deleteBtn.disabled = true;
        if (instInput) { instInput.value = ''; instInput.disabled = true; }
        if (filesBody) {
            filesBody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; color: #7d8597; padding: 24px;">
                        Create or select a project on the left to manage documents, scrape web URLs, and enable Cloud Sync.
                    </td>
                </tr>
            `;
        }
        return;
    }

    const isSynced = Boolean(proj.notebooklm && proj.notebooklm.sync_enabled);
    const hasDriveFolder = Boolean(proj.notebooklm && proj.notebooklm.drive_folder);
    if (titleEl) titleEl.textContent = proj.name || activeProjectId;
    if (syncCheckbox) {
        syncCheckbox.disabled = false;
        syncCheckbox.checked = isSynced;
    }
    if (syncBtn) syncBtn.disabled = false;
    if (driveBtn) {
        driveBtn.disabled = false;
        if (proj.notebooklm && proj.notebooklm.drive_folder) {
            driveBtn.title = `Open synced Google Drive folder: ${proj.notebooklm.drive_folder}`;
        }
    }

    if (syncHint) {
        if (isSynced && hasDriveFolder) {
            syncHint.textContent = '• Synced to Google Drive';
            syncHint.className = 'cloud-sync-status-hint synced';
        } else if (isSynced) {
            syncHint.textContent = '• Enabled';
            syncHint.className = 'cloud-sync-status-hint synced';
        } else {
            syncHint.textContent = '';
            syncHint.className = 'cloud-sync-status-hint';
        }
    }
    if (deleteBtn) deleteBtn.disabled = false;
    if (instInput) {
        instInput.disabled = false;
        instInput.value = proj.custom_instructions || '';
    }

    await refreshProjectFilesList();
}

async function refreshProjectFilesList() {
    const filesBody = document.getElementById('project-files-body');
    if (!filesBody || !activeProjectId) return;

    try {
        const files = await window.pywebview.api.list_project_files(activeProjectId);
        filesBody.innerHTML = '';

        if (!files || files.length === 0) {
            filesBody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; color: #7d8597; padding: 20px;">
                        No documents in this project yet. Drag & drop files above or import a Web URL.
                    </td>
                </tr>
            `;
            return;
        }

        files.forEach(file => {
            const tr = document.createElement('tr');
            const tokFormatted = Number(file.estimated_tokens || 0).toLocaleString();
            const sourceLabel = file.source_type === 'scraped_url'
                ? '🌐 Web URL'
                : (file.source_type === 'agent_artifact'
                    ? '🤖 Agent Artifact'
                    : (file.source_type === 'google_drive' ? '☁️ Google Drive' : '📄 Local Upload'));

            tr.innerHTML = `
                <td style="font-weight: 500; color: #e2e8f0;">${escapeProjHtml(file.filename)}</td>
                <td><span class="badge-status badge-info">${escapeProjHtml(file.file_type || '.txt')}</span></td>
                <td><span class="badge-count">~${tokFormatted} tokens</span></td>
                <td>${sourceLabel}</td>
                <td>
                    <button type="button" class="btn-danger-sm" onclick="handleDeleteProjectFile('${escapeProjHtml(file.filename)}')" title="Delete file">
                        🗑️ Delete
                    </button>
                </td>
            `;
            filesBody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed listing project files:", e);
    }
}

async function handleDeleteProjectFile(filename) {
    if (!activeProjectId || !filename) return;
    try {
        await window.pywebview.api.delete_project_file(activeProjectId, filename);
        await refreshProjectFilesList();
    } catch (e) {
        console.error("Failed deleting project file:", e);
    }
}

async function handleUploadFiles(fileList) {
    if (!fileList || fileList.length === 0) return;

    if (!activeProjectId) {
        alert("Please create or select a project first.");
        return;
    }

    for (const file of Array.from(fileList)) {
        try {
            const base64Content = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const res = String(reader.result || '');
                    const base64 = res.includes(',') ? res.split(',')[1] : res;
                    resolve(base64);
                };
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });

            await window.pywebview.api.upload_project_file(activeProjectId, file.name, base64Content);
        } catch (err) {
            console.error(`Failed uploading file ${file.name}:`, err);
        }
    }

    await refreshProjectFilesList();
}

async function handleImportProjectUrl() {
    const urlInput = document.getElementById('import-url-input');
    const importBtn = document.getElementById('import-url-btn');
    if (!urlInput) return;

    const url = urlInput.value.trim();
    if (!url) return;

    if (!activeProjectId) {
        alert("Please create or select a project first.");
        return;
    }

    const origText = importBtn ? importBtn.textContent : 'Import Web URL';
    if (importBtn) {
        importBtn.disabled = true;
        importBtn.textContent = 'Scraping...';
    }

    try {
        const res = await window.pywebview.api.import_project_url(activeProjectId, url);
        if (res && res.status === 'success') {
            urlInput.value = '';
            await refreshProjectFilesList();
        } else {
            alert((res && res.message) || "Failed to import URL.");
        }
    } catch (e) {
        console.error("Failed importing project URL:", e);
        alert("Error importing URL: " + e);
    } finally {
        if (importBtn) {
            importBtn.disabled = false;
            importBtn.textContent = origText;
        }
    }
}

async function handleSaveProjectInstructions() {
    if (!activeProjectId) return;
    const instInput = document.getElementById('project-instructions');
    if (!instInput) return;
    const val = instInput.value;

    try {
        const res = await window.pywebview.api.update_project(
            activeProjectId,
            null,
            null,
            val,
            null,
            null
        );
        if (res && res.status === 'success' && currentProjectsData.projects[activeProjectId]) {
            currentProjectsData.projects[activeProjectId].custom_instructions = val;
        }
    } catch (e) {
        console.error("Failed saving project instructions:", e);
    }
}

async function handleToggleNotebookLMSync(checked) {
    if (!activeProjectId) return;
    const syncHint = document.getElementById('cloud-sync-status-hint');
    if (syncHint && checked) {
        syncHint.textContent = '• Syncing...';
        syncHint.className = 'cloud-sync-status-hint syncing';
    }
    try {
        const res = await window.pywebview.api.update_project(
            activeProjectId,
            null,
            null,
            null,
            Boolean(checked),
            null
        );
        if (res && res.status === 'success' && res.project) {
            currentProjectsData.projects[activeProjectId] = res.project;
            renderProjectsSidebar();
            await renderActiveProjectWorkspace();
        }
    } catch (e) {
        console.error("Failed toggling Cloud Sync:", e);
    }
}

async function handleManualSyncProject() {
    if (!activeProjectId || !window.pywebview || !window.pywebview.api) return;
    const syncBtn = document.getElementById('btn-sync-project');
    const syncHint = document.getElementById('cloud-sync-status-hint');
    const origText = syncBtn ? syncBtn.textContent : '🔄 Sync';
    if (syncBtn) {
        syncBtn.disabled = true;
        syncBtn.textContent = '⏳ Syncing...';
    }
    if (syncHint) {
        syncHint.textContent = '• Syncing Google Drive & Cloud...';
        syncHint.className = 'cloud-sync-status-hint syncing';
    }
    try {
        const res = await window.pywebview.api.sync_project(activeProjectId);
        if (res && res.status === 'success') {
            if (res.project) {
                currentProjectsData.projects[activeProjectId] = res.project;
            }
            renderProjectsSidebar();
            await renderActiveProjectWorkspace();
        }
    } catch (e) {
        console.error("Failed syncing project:", e);
    } finally {
        if (syncBtn) {
            syncBtn.disabled = false;
            syncBtn.textContent = origText;
        }
    }
}

async function handleOpenDriveFolder() {
    if (!activeProjectId || !window.pywebview || !window.pywebview.api) return;
    try {
        await window.pywebview.api.open_project_drive_folder(activeProjectId);
    } catch (e) {
        console.error("Failed opening project Drive folder:", e);
    }
}

async function handleOpenNotebookLM() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
        await window.pywebview.api.open_project_notebooklm(activeProjectId);
    } catch (e) {
        console.error("Failed opening NotebookLM:", e);
    }
}

function openAddProjectModal() {
    const modal = document.getElementById('modal-add-project');
    if (modal) {
        const nameIn = document.getElementById('input-add-project-name');
        const descIn = document.getElementById('input-add-project-desc');
        const instIn = document.getElementById('input-add-project-inst');
        if (nameIn) nameIn.value = '';
        if (descIn) descIn.value = '';
        if (instIn) instIn.value = '';
        modal.classList.remove('hidden');
        if (nameIn) nameIn.focus();
        return;
    }

    const name = prompt("Enter Project Name:");
    if (!name || !name.trim()) return;
    createNewProject(name.trim(), "", "");
}

function closeAddProjectModal() {
    const modal = document.getElementById('modal-add-project');
    if (modal) modal.classList.add('hidden');
}

async function createNewProject(name, description, instructions) {
    const cleanId = name.toLowerCase().trim().replace(/[^a-z0-9_-]/g, '_');
    try {
        const res = await window.pywebview.api.create_project(cleanId, name, description || '', instructions || '');
        if (res && res.status === 'success') {
            closeAddProjectModal();
            await loadProjectsUI();
        } else {
            alert((res && res.message) || "Failed creating project.");
        }
    } catch (e) {
        console.error("Failed creating project:", e);
    }
}

async function handleDeleteActiveProject() {
    if (!activeProjectId) return;
    const proj = currentProjectsData?.projects?.[activeProjectId];
    const name = proj?.name || activeProjectId;
    if (!confirm(`Delete project "${name}" and all its local documents?`)) return;

    try {
        const res = await window.pywebview.api.delete_project(activeProjectId);
        if (res && res.status === 'success') {
            activeProjectId = res.active_project || null;
            await loadProjectsUI();
        }
    } catch (e) {
        console.error("Failed deleting project:", e);
    }
}

function initProjectsEvents() {
    const addBtn = document.getElementById('add-project-btn');
    if (addBtn) addBtn.onclick = openAddProjectModal;

    const closeAddModalBtn = document.getElementById('btn-close-add-project-modal');
    if (closeAddModalBtn) closeAddModalBtn.onclick = closeAddProjectModal;

    const cancelAddBtn = document.getElementById('btn-cancel-add-project');
    if (cancelAddBtn) cancelAddBtn.onclick = closeAddProjectModal;

    const confirmAddBtn = document.getElementById('btn-confirm-add-project');
    if (confirmAddBtn) {
        confirmAddBtn.onclick = () => {
            const name = (document.getElementById('input-add-project-name')?.value || '').trim();
            const desc = (document.getElementById('input-add-project-desc')?.value || '').trim();
            const inst = (document.getElementById('input-add-project-inst')?.value || '').trim();
            if (!name) {
                alert("Project Name is required.");
                return;
            }
            createNewProject(name, desc, inst);
        };
    }

    const delBtn = document.getElementById('delete-project-btn');
    if (delBtn) delBtn.onclick = handleDeleteActiveProject;

    const syncCheck = document.getElementById('notebooklm-sync-checkbox');
    if (syncCheck) {
        syncCheck.onchange = (e) => handleToggleNotebookLMSync(e.target.checked);
    }

    const syncNowBtn = document.getElementById('btn-sync-project');
    if (syncNowBtn) syncNowBtn.onclick = handleManualSyncProject;

    const openDriveBtn = document.getElementById('btn-open-drive-folder');
    if (openDriveBtn) openDriveBtn.onclick = handleOpenDriveFolder;

    const instTextarea = document.getElementById('project-instructions');
    if (instTextarea) {
        instTextarea.addEventListener('input', () => {
            if (instructionsSaveTimer) clearTimeout(instructionsSaveTimer);
            instructionsSaveTimer = setTimeout(handleSaveProjectInstructions, 600);
        });
        instTextarea.addEventListener('blur', handleSaveProjectInstructions);
    }

    const dropzone = document.getElementById('file-dropzone');
    const fileInput = document.getElementById('file-upload-input');
    if (dropzone && fileInput) {
        dropzone.onclick = (e) => {
            if (e.target !== fileInput) fileInput.click();
        };
        fileInput.onchange = (e) => {
            if (e.target.files && e.target.files.length > 0) {
                handleUploadFiles(e.target.files);
                fileInput.value = '';
            }
        };
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('drag-over');
        });
        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('drag-over');
        });
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('drag-over');
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                handleUploadFiles(e.dataTransfer.files);
            }
        });
    }

    const importUrlBtn = document.getElementById('import-url-btn');
    if (importUrlBtn) importUrlBtn.onclick = handleImportProjectUrl;

    const importUrlInput = document.getElementById('import-url-input');
    if (importUrlInput) {
        importUrlInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleImportProjectUrl();
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initProjectsEvents();
});

window.addEventListener('pywebviewready', () => {
    loadProjectsUI();
});

