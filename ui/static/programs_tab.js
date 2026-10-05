/**
 * Aether Desktop - Whitelisted Programs Frontend Controller
 * Manages allowed programs list, launch parameters, extensions (--elevate),
 * real-time running process detection, and program launching/closing.
 */

let currentProgramsData = null;
let activeProgramId = null;
let programStatusPollInterval = null;
let cachedProgramRunningProcs = null;

async function loadProgramsUI() {
    try {
        if (!window.pywebview || !window.pywebview.api) {
            console.warn("pywebview API not available yet");
            return;
        }
        currentProgramsData = await window.pywebview.api.get_programs();
        activeProgramId = currentProgramsData.active_program;
        renderProgramsSidebar();
        renderActiveProgramDetail();
        startProgramStatusPolling();
    } catch (err) {
        console.error("Failed loading programs subsystem:", err);
    }
}

function renderProgramsSidebar() {
    const listEl = document.getElementById('programs-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const programs = currentProgramsData ? (currentProgramsData.programs || {}) : {};
    const running = currentProgramsData ? (currentProgramsData.running_status || {}) : {};
    const programIds = Object.keys(programs);

    if (programIds.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 16px; color: #7d8597; font-size: 0.82rem; text-align: center;">
                No programs whitelisted.<br>Click <strong>+ Add</strong> above.
            </div>
        `;
        return;
    }

    if (!activeProgramId || !programs[activeProgramId]) {
        activeProgramId = programIds[0];
    }

    programIds.forEach(pid => {
        const p = programs[pid];
        const isOnline = running[pid] || false;

        const item = document.createElement('div');
        item.className = `program-item ${pid === activeProgramId ? 'active' : ''}`;
        item.onclick = () => switchActiveProgram(pid);

        item.innerHTML = `
            <span class="program-item-name">${p.name || pid}</span>
            <span class="badge-status ${isOnline ? 'badge-online' : 'badge-offline'}">
                ${isOnline ? 'RUNNING' : 'IDLE'}
            </span>
        `;
        listEl.appendChild(item);
    });
}

async function switchActiveProgram(pid) {
    if (activeProgramId === pid) return;
    activeProgramId = pid;
    try {
        await window.pywebview.api.set_active_program(pid);
    } catch (e) {
        console.error("Failed setting active program:", e);
    }
    renderProgramsSidebar();
    renderActiveProgramDetail();
}

function renderActiveProgramDetail() {
    const emptyView = document.getElementById('programs-empty-view');
    const detailsContent = document.getElementById('program-details-content');
    const banner = document.getElementById('program-active-banner');

    const programs = currentProgramsData ? (currentProgramsData.programs || {}) : {};
    const hasPrograms = Object.keys(programs).length > 0 && activeProgramId && programs[activeProgramId];

    if (!hasPrograms) {
        if (emptyView) { emptyView.classList.remove('hidden'); emptyView.style.display = 'block'; }
        if (detailsContent) { detailsContent.classList.add('hidden'); detailsContent.style.display = 'none'; }
        if (banner) { banner.classList.add('hidden'); banner.style.display = 'none'; }
        return;
    }

    if (emptyView) { emptyView.classList.add('hidden'); emptyView.style.display = 'none'; }
    if (detailsContent) { detailsContent.classList.remove('hidden'); detailsContent.style.display = 'block'; }
    if (banner) { banner.classList.remove('hidden'); banner.style.display = 'flex'; }

    const program = programs[activeProgramId];
    const isOnline = (currentProgramsData.running_status || {})[activeProgramId];

    // Banner Title & Badges
    const titleEl = document.getElementById('program-active-title');
    if (titleEl) titleEl.textContent = program.name || activeProgramId;

    const procInd = document.getElementById('program-process-indicator');
    if (procInd) {
        procInd.textContent = isOnline ? 'Process Detected' : 'Process Inactive';
        procInd.className = `badge-status ${isOnline ? 'badge-online' : 'badge-offline'}`;
    }

    const elevInd = document.getElementById('program-elevate-indicator');
    if (elevInd) {
        const isElev = Boolean(program.elevate || (program.arguments && program.arguments.toLowerCase().includes('--elevate')));
        elevInd.textContent = isElev ? '🛡️ Elevated (Admin)' : 'Standard User';
        elevInd.className = `badge-status ${isElev ? 'badge-warning' : 'badge-info'}`;
    }

    // Detail Inputs
    const nameInput = document.getElementById('input-prog-name');
    const pathInput = document.getElementById('input-prog-path');
    const argsInput = document.getElementById('input-prog-args');
    const cwdInput = document.getElementById('input-prog-cwd');
    const elevCheckbox = document.getElementById('input-prog-elevate');

    if (nameInput) nameInput.value = program.name || '';
    if (pathInput) pathInput.value = program.path || '';
    if (argsInput) argsInput.value = program.arguments || '';
    if (cwdInput) cwdInput.value = program.working_dir || '';
    if (elevCheckbox) elevCheckbox.checked = Boolean(program.elevate);
}

async function handleLaunchActiveProgram() {
    if (!activeProgramId) return;
    const btn = document.getElementById('btn-launch-program');
    if (btn) btn.disabled = true;

    try {
        const res = await window.pywebview.api.launch_program(activeProgramId);
        if (res && res.status === 'success') {
            if (window.AetherApp && typeof window.AetherApp.showToast === 'function') {
                window.AetherApp.showToast(res.message || "Launched successfully");
            }
            setTimeout(async () => {
                await refreshProgramsStatusOnly();
            }, 1000);
        } else {
            alert((res && (res.error || res.message)) || "Failed to launch application.");
        }
    } catch (e) {
        console.error("handleLaunchActiveProgram error:", e);
        alert("Launch failed: " + e);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function handleCloseActiveProgram() {
    if (!activeProgramId) return;
    try {
        const res = await window.pywebview.api.close_program(activeProgramId);
        if (res && res.status === 'success') {
            setTimeout(async () => {
                await refreshProgramsStatusOnly();
            }, 800);
        } else {
            alert((res && (res.error || res.message)) || "Process was not running.");
        }
    } catch (e) {
        console.error("handleCloseActiveProgram error:", e);
    }
}

async function handleSaveActiveProgram() {
    if (!activeProgramId) return;
    const name = (document.getElementById('input-prog-name')?.value || '').trim();
    const path = (document.getElementById('input-prog-path')?.value || '').trim();
    const args = (document.getElementById('input-prog-args')?.value || '').trim();
    const cwd = (document.getElementById('input-prog-cwd')?.value || '').trim();
    const elevate = Boolean(document.getElementById('input-prog-elevate')?.checked);

    if (!name) {
        alert("Program display name is required.");
        return;
    }

    try {
        const res = await window.pywebview.api.update_program(activeProgramId, name, path, args, elevate, cwd);
        if (res && res.success) {
            if (currentProgramsData && currentProgramsData.programs) {
                currentProgramsData.programs[activeProgramId] = res.program;
            }
            renderProgramsSidebar();
            renderActiveProgramDetail();
            const btn = document.getElementById('btn-save-program');
            if (btn) {
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Saved!';
                setTimeout(() => { btn.innerHTML = orig; }, 1500);
            }
        } else {
            alert((res && res.error) || "Failed to save program.");
        }
    } catch (e) {
        console.error("handleSaveActiveProgram error:", e);
    }
}

async function handleDeleteActiveProgram() {
    if (!activeProgramId) return;
    const prog = currentProgramsData?.programs?.[activeProgramId];
    const name = prog?.name || activeProgramId;
    if (!confirm(`Are you sure you want to remove "${name}" from your allowed programs whitelist?`)) return;

    try {
        const res = await window.pywebview.api.delete_program(activeProgramId);
        if (res && res.success) {
            await loadProgramsUI();
        } else {
            alert((res && res.error) || "Failed to remove program.");
        }
    } catch (e) {
        console.error("handleDeleteActiveProgram error:", e);
    }
}

// ----------------------------------------------------------------------------
// Add Program Modal & File / Process Browsing
// ----------------------------------------------------------------------------
function openAddProgramModal() {
    const modal = document.getElementById('modal-add-program');
    if (!modal) return;
    const nameInput = document.getElementById('input-add-prog-name');
    const pathInput = document.getElementById('input-add-prog-path');
    const argsInput = document.getElementById('input-add-prog-args');
    const cwdInput = document.getElementById('input-add-prog-cwd');
    const elevCheck = document.getElementById('input-add-prog-elevate');
    const panel = document.getElementById('panel-add-prog-running');
    const banner = document.getElementById('add-prog-browse-banner');

    if (nameInput) { nameInput.value = ''; nameInput.style.borderColor = ''; }
    if (pathInput) { pathInput.value = ''; pathInput.style.borderColor = ''; pathInput.style.boxShadow = ''; }
    if (argsInput) argsInput.value = '';
    if (cwdInput) { cwdInput.value = ''; cwdInput.style.borderColor = ''; }
    if (elevCheck) elevCheck.checked = false;
    if (panel) panel.classList.add('hidden');
    if (banner) { banner.classList.add('hidden'); banner.style.display = 'none'; }

    modal.classList.remove('hidden');
    if (nameInput) nameInput.focus();
}

function closeAddProgramModal() {
    const modal = document.getElementById('modal-add-program');
    if (modal) modal.classList.add('hidden');
}

async function handleBrowseProgramExe(isAddModal = true) {
    try {
        const res = await window.pywebview.api.browse_program_executable();
        if (res && res.success && res.file_path) {
            const targetPath = isAddModal ? document.getElementById('input-add-prog-path') : document.getElementById('input-prog-path');
            const targetName = isAddModal ? document.getElementById('input-add-prog-name') : document.getElementById('input-prog-name');
            const targetCwd = isAddModal ? document.getElementById('input-add-prog-cwd') : document.getElementById('input-prog-cwd');
            const banner = isAddModal ? document.getElementById('add-prog-browse-banner') : null;
            const bannerText = isAddModal ? document.getElementById('add-prog-browse-text') : null;
            const bannerSubtext = isAddModal ? document.getElementById('add-prog-browse-subtext') : null;

            if (targetPath) {
                targetPath.value = res.file_path;
                targetPath.style.borderColor = '#10b981';
                targetPath.style.boxShadow = '0 0 0 2px rgba(16, 185, 129, 0.25)';
                setTimeout(() => {
                    if (targetPath) {
                        targetPath.style.borderColor = '';
                        targetPath.style.boxShadow = '';
                    }
                }, 3000);
                targetPath.dispatchEvent(new Event('input', { bubbles: true }));
                targetPath.dispatchEvent(new Event('change', { bubbles: true }));
                // Scroll horizontally so the executable file name is immediately visible
                targetPath.scrollLeft = targetPath.scrollWidth;
            }

            if (targetName && (!targetName.value || !targetName.value.trim())) {
                targetName.value = res.suggested_name || res.file_name || '';
                targetName.dispatchEvent(new Event('input', { bubbles: true }));
                targetName.dispatchEvent(new Event('change', { bubbles: true }));
            }

            if (targetCwd && res.directory) {
                targetCwd.value = res.directory;
                targetCwd.style.borderColor = '#10b981';
                setTimeout(() => {
                    if (targetCwd) targetCwd.style.borderColor = '';
                }, 3000);
                targetCwd.dispatchEvent(new Event('input', { bubbles: true }));
                targetCwd.dispatchEvent(new Event('change', { bubbles: true }));
            }

            if (banner && bannerText) {
                bannerText.textContent = `✓ Grabbed: ${res.file_name}`;
                if (bannerSubtext) bannerSubtext.textContent = `Path: ${res.file_path} | Dir: ${res.directory || 'Root'}`;
                banner.classList.remove('hidden');
                banner.style.display = 'flex';
            }

            if (window.AetherApp && typeof window.AetherApp.showToast === 'function') {
                window.AetherApp.showToast(`Grabbed: ${res.file_name}`);
            }
        }
    } catch (e) {
        console.error("handleBrowseProgramExe error:", e);
    }
}

async function toggleProgramRunningProcesses(isAddModal = true) {
    const panelId = isAddModal ? 'panel-add-prog-running' : 'panel-prog-detail-running';
    const listId = isAddModal ? 'list-add-prog-running' : 'list-prog-detail-running';
    const panel = document.getElementById(panelId);
    if (!panel) return;

    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        await loadProgramRunningProcessesList(listId, isAddModal);
    } else {
        panel.classList.add('hidden');
    }
}

async function loadProgramRunningProcessesList(listContainerId, isAddModal = true) {
    const list = document.getElementById(listContainerId);
    if (!list) return;
    list.innerHTML = '<div style="padding: 10px; text-align: center; color: #64748b; font-size: 0.78rem;">Scanning running processes...</div>';

    try {
        const res = await window.pywebview.api.get_running_processes();
        cachedProgramRunningProcs = res;
        renderProgramProcsList(listContainerId, res, '', isAddModal);
    } catch (e) {
        list.innerHTML = '<div style="padding: 10px; text-align: center; color: #f87171; font-size: 0.78rem;">Could not load running processes.</div>';
    }
}

function renderProgramProcsList(containerId, data, filterText = '', isAddModal = true) {
    const list = document.getElementById(containerId);
    if (!list) return;
    list.innerHTML = '';

    const q = (filterText || '').toLowerCase().trim();
    const apps = (data && data.applications) || [];
    const procs = (data && data.processes) || [];

    const filteredApps = apps.filter(a => !q || a.name.toLowerCase().includes(q) || (a.title && a.title.toLowerCase().includes(q)));
    const filteredProcs = procs.filter(p => !q || p.toLowerCase().includes(q));

    if (filteredApps.length === 0 && filteredProcs.length === 0) {
        list.innerHTML = '<div style="padding: 8px; text-align: center; color: #64748b; font-size: 0.75rem;">No matching processes found.</div>';
        return;
    }

    if (filteredApps.length > 0) {
        const header = document.createElement('div');
        header.style.cssText = 'padding: 4px 8px; background: rgba(59, 130, 246, 0.12); font-size: 0.7rem; color: #60a5fa; font-weight: 700;';
        header.textContent = 'RUNNING WINDOWED APPLICATIONS';
        list.appendChild(header);

        filteredApps.forEach(app => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title">🪟 ${app.title || app.name}</span>
                <span class="proc-item-exe">${app.name}</span>
            `;
            item.onclick = () => selectProgramRunningProcess(app.name, app.title, isAddModal);
            list.appendChild(item);
        });
    }

    if (filteredProcs.length > 0) {
        const header = document.createElement('div');
        header.style.cssText = 'padding: 4px 8px; background: rgba(255, 255, 255, 0.04); font-size: 0.7rem; color: #94a3b8; font-weight: 700;';
        header.textContent = 'OTHER RUNNING PROCESSES';
        list.appendChild(header);

        filteredProcs.forEach(pname => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title" style="color: #cbd5e1;">⚡ ${pname}</span>
                <span class="proc-item-exe">${pname}</span>
            `;
            item.onclick = () => selectProgramRunningProcess(pname, '', isAddModal);
            list.appendChild(item);
        });
    }
}

function selectProgramRunningProcess(exeName, windowTitle = '', isAddModal = true) {
    const targetPath = isAddModal ? document.getElementById('input-add-prog-path') : document.getElementById('input-prog-path');
    const targetName = isAddModal ? document.getElementById('input-add-prog-name') : document.getElementById('input-prog-name');
    const panel = isAddModal ? document.getElementById('panel-add-prog-running') : document.getElementById('panel-prog-detail-running');
    const banner = isAddModal ? document.getElementById('add-prog-browse-banner') : null;
    const bannerText = isAddModal ? document.getElementById('add-prog-browse-text') : null;
    const bannerSubtext = isAddModal ? document.getElementById('add-prog-browse-subtext') : null;

    if (targetPath) {
        targetPath.value = exeName;
        targetPath.style.borderColor = '#10b981';
        targetPath.style.boxShadow = '0 0 0 2px rgba(16, 185, 129, 0.25)';
        setTimeout(() => {
            if (targetPath) {
                targetPath.style.borderColor = '';
                targetPath.style.boxShadow = '';
            }
        }, 3000);
        targetPath.dispatchEvent(new Event('input', { bubbles: true }));
        targetPath.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (targetName && !targetName.value.trim()) {
        let clean = windowTitle || exeName.replace(/\.exe$/i, '');
        clean = clean.replace(/([a-z])([A-Z0-9])/g, '$1 $2').replace(/[_-]/g, ' ').trim();
        targetName.value = clean.charAt(0).toUpperCase() + clean.slice(1);
        targetName.dispatchEvent(new Event('input', { bubbles: true }));
        targetName.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (panel) panel.classList.add('hidden');

    if (banner && bannerText) {
        bannerText.textContent = `✓ Selected Process: ${exeName}`;
        if (bannerSubtext) bannerSubtext.textContent = windowTitle ? `Window: "${windowTitle}"` : 'Active Windows process';
        banner.classList.remove('hidden');
        banner.style.display = 'flex';
    }
}

async function handleConfirmAddProgram() {
    const nameInput = document.getElementById('input-add-prog-name');
    const pathInput = document.getElementById('input-add-prog-path');
    const argsInput = document.getElementById('input-add-prog-args');
    const cwdInput = document.getElementById('input-add-prog-cwd');
    const elevCheckbox = document.getElementById('input-add-prog-elevate');

    const name = (nameInput?.value || '').trim();
    const path = (pathInput?.value || '').trim();
    const args = (argsInput?.value || '').trim();
    const cwd = (cwdInput?.value || '').trim();
    const elevate = Boolean(elevCheckbox?.checked);

    if (!name) {
        if (nameInput) {
            nameInput.style.borderColor = '#ef4444';
            nameInput.focus();
        }
        return;
    }

    const confirmBtn = document.getElementById('btn-confirm-add-prog');
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Adding...';
    }

    try {
        const res = await window.pywebview.api.add_program(name, path, args, elevate, cwd);
        if (res && res.success) {
            closeAddProgramModal();
            activeProgramId = res.program_id;
            await loadProgramsUI();
            if (window.AetherApp && typeof window.AetherApp.showToast === 'function') {
                window.AetherApp.showToast(`Saved "${name}" to allowed programs.`);
            }
        } else {
            alert((res && res.error) || 'Failed to add whitelisted program.');
        }
    } catch (err) {
        console.error("add_program error:", err);
        alert("Error adding program: " + err);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Add Program';
        }
    }
}

// ----------------------------------------------------------------------------
// Real-time Process Polling
// ----------------------------------------------------------------------------
function startProgramStatusPolling() {
    if (programStatusPollInterval) clearInterval(programStatusPollInterval);
    programStatusPollInterval = setInterval(async () => {
        const progPane = document.getElementById('tab-programs');
        if (progPane && !progPane.classList.contains('hidden') && progPane.classList.contains('active')) {
            await refreshProgramsStatusOnly();
        }
    }, 3000);
}

async function refreshProgramsStatusOnly() {
    try {
        if (!window.pywebview || !window.pywebview.api) return;
        const data = await window.pywebview.api.get_programs();
        if (data && data.running_status) {
            if (currentProgramsData) {
                currentProgramsData.running_status = data.running_status;
            }
            // Update sidebar badges
            const listEl = document.getElementById('programs-list');
            if (listEl) {
                const items = listEl.querySelectorAll('.program-item');
                const pids = Object.keys(data.programs || {});
                items.forEach((item, idx) => {
                    const pid = pids[idx];
                    if (pid) {
                        const isOnline = data.running_status[pid] || false;
                        const badge = item.querySelector('.badge-status');
                        if (badge) {
                            badge.className = `badge-status ${isOnline ? 'badge-online' : 'badge-offline'}`;
                            badge.textContent = isOnline ? 'RUNNING' : 'IDLE';
                        }
                    }
                });
            }
            // Update active banner badge
            if (activeProgramId) {
                const isOnline = data.running_status[activeProgramId] || false;
                const procInd = document.getElementById('program-process-indicator');
                if (procInd) {
                    procInd.textContent = isOnline ? 'Process Detected' : 'Process Inactive';
                    procInd.className = `badge-status ${isOnline ? 'badge-online' : 'badge-offline'}`;
                }
            }
        }
    } catch (e) {
        // silent poll error
    }
}

// Global DOM Hook Initializer
function initProgramsTabEvents() {
    // Add Program Buttons
    const addBtn = document.getElementById('btn-add-program');
    const addEmptyBtn = document.getElementById('btn-add-program-empty');
    if (addBtn) addBtn.onclick = openAddProgramModal;
    if (addEmptyBtn) addEmptyBtn.onclick = openAddProgramModal;

    // Modal close & confirm
    const closeBtn = document.getElementById('btn-close-add-program-modal');
    const cancelBtn = document.getElementById('btn-cancel-add-prog');
    const confirmBtn = document.getElementById('btn-confirm-add-prog');
    if (closeBtn) closeBtn.onclick = closeAddProgramModal;
    if (cancelBtn) cancelBtn.onclick = closeAddProgramModal;
    if (confirmBtn) confirmBtn.onclick = handleConfirmAddProgram;

    // Browse & Running Apps in Add Modal
    const browseModalBtn = document.getElementById('btn-add-prog-browse');
    const runningModalBtn = document.getElementById('btn-add-prog-running');
    if (browseModalBtn) browseModalBtn.onclick = () => handleBrowseProgramExe(true);
    if (runningModalBtn) runningModalBtn.onclick = () => toggleProgramRunningProcesses(true);

    const searchAddProc = document.getElementById('input-add-prog-proc-search');
    if (searchAddProc) {
        searchAddProc.oninput = (e) => {
            if (cachedProgramRunningProcs) {
                renderProgramProcsList('list-add-prog-running', cachedProgramRunningProcs, e.target.value, true);
            }
        };
    }

    // Detail Panel Actions
    const launchBtn = document.getElementById('btn-launch-program');
    const closeProgBtn = document.getElementById('btn-close-program');
    const saveBtn = document.getElementById('btn-save-program');
    const delBtn = document.getElementById('btn-delete-program');
    if (launchBtn) launchBtn.onclick = handleLaunchActiveProgram;
    if (closeProgBtn) closeProgBtn.onclick = handleCloseActiveProgram;
    if (saveBtn) saveBtn.onclick = handleSaveActiveProgram;
    if (delBtn) delBtn.onclick = handleDeleteActiveProgram;

    // Browse & Running Apps in Detail Panel
    const browseDetailBtn = document.getElementById('btn-browse-prog-exe');
    const runningDetailBtn = document.getElementById('btn-prog-running-apps');
    if (browseDetailBtn) browseDetailBtn.onclick = () => handleBrowseProgramExe(false);
    if (runningDetailBtn) runningDetailBtn.onclick = () => toggleProgramRunningProcesses(false);

    const searchDetailProc = document.getElementById('input-prog-detail-proc-search');
    if (searchDetailProc) {
        searchDetailProc.oninput = (e) => {
            if (cachedProgramRunningProcs) {
                renderProgramProcsList('list-prog-detail-running', cachedProgramRunningProcs, e.target.value, false);
            }
        };
    }
}

if (document.readyState === 'loading') {
    document.addEventListener("DOMContentLoaded", initProgramsTabEvents);
} else {
    initProgramsTabEvents();
}

window.loadProgramsUI = loadProgramsUI;
window.openAddProgramModal = openAddProgramModal;
window.closeAddProgramModal = closeAddProgramModal;
window.handleBrowseProgramExe = handleBrowseProgramExe;
window.handleConfirmAddProgram = handleConfirmAddProgram;
window.handleLaunchActiveProgram = handleLaunchActiveProgram;
window.handleCloseActiveProgram = handleCloseActiveProgram;
window.handleSaveActiveProgram = handleSaveActiveProgram;
window.handleDeleteActiveProgram = handleDeleteActiveProgram;
