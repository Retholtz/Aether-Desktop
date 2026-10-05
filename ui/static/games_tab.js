/**
 * Aether Desktop - Games Subsystem Frontend Controller
 * Manages game profile selection, dynamic add/delete of games, voice macros,
 * DirectInput scancode injection testing, binds synchronization, and scratchpad autosave.
 */

let currentGamesData = null;
let activeGameId = null;
let scratchpadDebounceTimer = null;

async function loadGamesUI() {
    try {
        if (!window.pywebview || !window.pywebview.api) {
            console.warn("pywebview API not available yet");
            return;
        }
        currentGamesData = await window.pywebview.api.get_game_profiles();
        activeGameId = currentGamesData.active_profile;
        renderGamesSidebar();
        renderActiveGameDetail();
        startTelemetryPolling();
    } catch (err) {
        console.error("Failed loading games subsystem:", err);
    }
}

function renderGamesSidebar() {
    const listEl = document.getElementById('games-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const profiles = currentGamesData ? (currentGamesData.profiles || {}) : {};
    const running = currentGamesData ? (currentGamesData.running_status || {}) : {};
    const gameIds = Object.keys(profiles);

    if (gameIds.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 16px; color: #7d8597; font-size: 0.82rem; text-align: center;">
                No games linked.<br>Click <strong>+ Add</strong> above.
            </div>
        `;
        return;
    }

    // Ensure activeGameId is valid if games exist
    if (!activeGameId || !profiles[activeGameId]) {
        activeGameId = gameIds[0];
    }

    gameIds.forEach(gid => {
        const p = profiles[gid];
        const isOnline = running[gid] || false;

        const item = document.createElement('div');
        item.className = `game-item ${gid === activeGameId ? 'active' : ''}`;
        item.onclick = () => switchActiveGame(gid);

        item.innerHTML = `
            <span class="game-item-name">${p.display_name || gid}</span>
            <span class="badge-status ${isOnline ? 'badge-online' : 'badge-offline'}">
                ${isOnline ? 'RUNNING' : 'IDLE'}
            </span>
        `;
        listEl.appendChild(item);
    });
}

async function switchActiveGame(gid) {
    if (activeGameId === gid) return;
    activeGameId = gid;
    try {
        await window.pywebview.api.set_active_game_profile(gid);
    } catch (e) {
        console.error("Failed switching active game:", e);
    }
    renderGamesSidebar();
    renderActiveGameDetail();
}

function renderActiveGameDetail() {
    const emptyView = document.getElementById('games-empty-view');
    const detailsContent = document.getElementById('game-details-content');
    const banner = document.getElementById('game-active-banner');

    const profiles = currentGamesData ? (currentGamesData.profiles || {}) : {};
    const hasGames = Object.keys(profiles).length > 0 && activeGameId && profiles[activeGameId];

    if (!hasGames) {
        if (emptyView) emptyView.classList.remove('hidden');
        if (detailsContent) detailsContent.classList.add('hidden');
        if (banner) banner.classList.add('hidden');
        return;
    }

    if (emptyView) emptyView.classList.add('hidden');
    if (detailsContent) detailsContent.classList.remove('hidden');
    if (banner) banner.classList.remove('hidden');

    const profile = profiles[activeGameId];
    const isOnline = (currentGamesData.running_status || {})[activeGameId];

    // Banner
    const titleEl = document.getElementById('game-active-title');
    if (titleEl) titleEl.textContent = profile.display_name || activeGameId;

    const indicator = document.getElementById('game-process-indicator');
    if (indicator) {
        indicator.textContent = isOnline ? 'Process Detected' : 'Process Inactive';
        indicator.className = `badge-status ${isOnline ? 'badge-online' : 'badge-offline'}`;
    }

    const gmIndicator = document.getElementById('game-mode-indicator');
    if (gmIndicator) {
        const gmEnabled = Boolean(currentGamesData && currentGamesData.game_mode_enabled);
        gmIndicator.textContent = gmEnabled ? '🎮 Game Mode: ON (Universe Focused)' : '🌐 Open Chat (Macros Standby)';
        gmIndicator.className = `badge-status ${gmEnabled ? 'badge-info' : 'badge-warning'}`;
        gmIndicator.onclick = async () => {
            try {
                const res = await window.pywebview.api.toggle_game_mode();
                if (currentGamesData) {
                    currentGamesData.game_mode_enabled = res.game_mode_enabled;
                }
                renderActiveGameDetail();
            } catch (e) {
                console.error("Failed toggling game mode:", e);
            }
        };
    }

    // Keybinds Table
    const tbody = document.getElementById('tbody-game-binds');
    if (tbody) {
        tbody.innerHTML = '';
        const keybinds = profile.keybinds || {};
        const phrases = Object.keys(keybinds);

        if (phrases.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #7d8597; padding: 20px;">No voice macros configured yet. Click <strong>+ Add Keybind</strong> or <strong>Scan / Sync</strong>.</td></tr>`;
        } else {
            phrases.forEach(phrase => {
                const bind = keybinds[phrase];
                const tr = document.createElement('tr');

                const modsText = (bind.modifiers && bind.modifiers.length > 0) ? bind.modifiers.join(' + ').toUpperCase() : 'None';
                const keyText = (bind.key || '').toUpperCase();
                const safePhrase = phrase.replace(/\\/g, '\\\\').replace(/'/g, "\\'");

                tr.innerHTML = `
                    <td><strong>"${phrase}"</strong></td>
                    <td><code>${modsText}</code></td>
                    <td><code>${keyText}</code></td>
                    <td>${bind.description || ''}</td>
                    <td>
                        <button type="button" class="btn-secondary-sm" onclick="testMacro('${safePhrase}')">Test</button>
                        <button type="button" class="btn-danger-sm" onclick="deleteMacro('${safePhrase}')">Delete</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }
    }

    // Scratchpad
    const pad = document.getElementById('game-scratchpad-input');
    if (pad) {
        pad.value = profile.scratchpad_raw || '';
    }

    // Co-Pilot Intel & Log
    renderCopilotLog(profile.copilot_log || []);
}

function renderCopilotLog(entries) {
    const listEl = document.getElementById('copilot-log-list');
    const badgeEl = document.getElementById('badge-copilot-count');
    if (!listEl) return;

    const list = Array.isArray(entries) ? entries : [];
    if (badgeEl) badgeEl.textContent = list.length;

    if (list.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 24px 12px; text-align: center; color: #64748b; font-size: 0.8rem;">
                No co-pilot observations logged yet.<br>
                Aether records landmarks, clues, and milestones as you explore.
            </div>
        `;
        return;
    }

    listEl.innerHTML = '';
    const reversed = [...list].reverse();
    reversed.forEach(entry => {
        const item = document.createElement('div');
        item.className = 'copilot-entry-item';
        const cat = (entry.category || 'intel').toLowerCase();
        const catClass = `cat-${cat}`;
        const locText = entry.location ? `📍 ${entry.location}` : '';
        const timeText = entry.time_str || '';
        const entryId = entry.id || '';

        item.innerHTML = `
            <div class="copilot-entry-header">
                <div style="display: flex; align-items: center;">
                    <span class="copilot-entry-cat ${catClass}">${cat}</span>
                    ${locText ? `<span class="copilot-entry-loc">${locText}</span>` : ''}
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 0.68rem; color: #64748b;">${timeText}</span>
                    <button type="button" class="copilot-entry-del" title="Dismiss" onclick="deleteCopilotEntry('${entryId}')">✕</button>
                </div>
            </div>
            <div class="copilot-entry-summary">${entry.summary || ''}</div>
        `;
        listEl.appendChild(item);
    });
}

async function deleteCopilotEntry(entryId) {
    if (!activeGameId || !entryId) return;
    try {
        const res = await window.pywebview.api.delete_copilot_log_entry(activeGameId, entryId);
        if (res && res.success) {
            const profile = currentGamesData?.profiles?.[activeGameId];
            if (profile && Array.isArray(profile.copilot_log)) {
                profile.copilot_log = profile.copilot_log.filter(e => e.id !== entryId);
                renderCopilotLog(profile.copilot_log);
            }
        }
    } catch (e) {
        console.error("deleteCopilotEntry error:", e);
    }
}

async function handleClearCopilotLog() {
    if (!activeGameId) return;
    if (!confirm("Clear all Co-Pilot observations and milestone entries for this game?")) return;
    try {
        const res = await window.pywebview.api.clear_copilot_log(activeGameId);
        if (res && res.success) {
            const profile = currentGamesData?.profiles?.[activeGameId];
            if (profile) profile.copilot_log = [];
            renderCopilotLog([]);
        }
    } catch (e) {
        console.error("handleClearCopilotLog error:", e);
    }
}

// --- Modal & In-App Dialog Controllers ---

function openAddGameModal() {
    const modal = document.getElementById('modal-add-game');
    if (!modal) return;
    const nameInput = document.getElementById('input-add-game-name');
    const procInput = document.getElementById('input-add-game-proc');
    const procPanel = document.getElementById('panel-running-processes');
    if (nameInput) {
        nameInput.value = '';
        nameInput.style.borderColor = '';
    }
    if (procInput) procInput.value = '';
    if (procPanel) procPanel.classList.add('hidden');
    modal.classList.remove('hidden');
    if (nameInput) nameInput.focus();
}

function closeAddGameModal() {
    const modal = document.getElementById('modal-add-game');
    if (modal) modal.classList.add('hidden');
}

async function handleBrowseGameExe() {
    try {
        const res = await window.pywebview.api.browse_game_executable();
        if (res && res.success) {
            const procInput = document.getElementById('input-add-game-proc');
            const nameInput = document.getElementById('input-add-game-name');
            if (procInput) {
                procInput.value = res.file_path || res.file_name || '';
                procInput.style.borderColor = '#10b981';
                procInput.style.boxShadow = '0 0 0 2px rgba(16, 185, 129, 0.25)';
                setTimeout(() => {
                    if (procInput) {
                        procInput.style.borderColor = '';
                        procInput.style.boxShadow = '';
                    }
                }, 3000);
                procInput.dispatchEvent(new Event('input', { bubbles: true }));
                procInput.dispatchEvent(new Event('change', { bubbles: true }));
                procInput.scrollLeft = procInput.scrollWidth;
            }
            if (nameInput && !nameInput.value.trim()) {
                nameInput.value = res.suggested_name || '';
                nameInput.dispatchEvent(new Event('input', { bubbles: true }));
                nameInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
    } catch (err) {
        console.error("Failed browsing game executable:", err);
    }
}

let cachedRunningProcesses = null;

async function toggleRunningProcesses() {
    const panel = document.getElementById('panel-running-processes');
    if (!panel) return;
    const isHidden = panel.classList.contains('hidden');
    if (isHidden) {
        panel.classList.remove('hidden');
        await loadRunningProcesses();
    } else {
        panel.classList.add('hidden');
    }
}

async function loadRunningProcesses() {
    const list = document.getElementById('list-running-processes');
    if (!list) return;
    list.innerHTML = '<div style="padding: 12px; text-align: center; color: #64748b; font-size: 0.8rem;">Scanning running processes...</div>';
    try {
        const res = await window.pywebview.api.get_running_processes();
        cachedRunningProcesses = res;
        renderRunningProcessesList(res);
    } catch (err) {
        list.innerHTML = '<div style="padding: 12px; text-align: center; color: #f87171; font-size: 0.8rem;">Could not load running processes.</div>';
    }
}

function renderRunningProcessesList(data, filterText = '') {
    const list = document.getElementById('list-running-processes');
    if (!list) return;
    list.innerHTML = '';

    const q = (filterText || '').toLowerCase().trim();
    const apps = (data && data.applications) || [];
    const procs = (data && data.processes) || [];

    const filteredApps = apps.filter(a => !q || a.name.toLowerCase().includes(q) || (a.title && a.title.toLowerCase().includes(q)));
    const filteredProcs = procs.filter(p => !q || p.toLowerCase().includes(q));

    if (filteredApps.length === 0 && filteredProcs.length === 0) {
        list.innerHTML = '<div style="padding: 12px; text-align: center; color: #64748b; font-size: 0.8rem;">No matching processes found.</div>';
        return;
    }

    // 1. Applications with windows
    if (filteredApps.length > 0) {
        const sectionHeader = document.createElement('div');
        sectionHeader.style.cssText = 'padding: 6px 10px; background: rgba(59, 130, 246, 0.08); font-size: 0.72rem; color: #60a5fa; font-weight: 700;';
        sectionHeader.textContent = 'RUNNING WINDOWED APPLICATIONS';
        list.appendChild(sectionHeader);

        filteredApps.forEach(app => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title">🪟 ${app.title || app.name}</span>
                <span class="proc-item-exe">${app.name}</span>
            `;
            item.onclick = () => selectRunningProcess(app.name, app.title);
            list.appendChild(item);
        });
    }

    // 2. All running processes
    if (filteredProcs.length > 0) {
        const sectionHeader = document.createElement('div');
        sectionHeader.style.cssText = 'padding: 6px 10px; background: rgba(255, 255, 255, 0.03); font-size: 0.72rem; color: #94a3b8; font-weight: 700;';
        sectionHeader.textContent = 'ALL RUNNING PROCESSES';
        list.appendChild(sectionHeader);

        filteredProcs.forEach(pname => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title" style="color: #cbd5e1;">⚡ ${pname}</span>
                <span class="proc-item-exe">${pname}</span>
            `;
            item.onclick = () => selectRunningProcess(pname);
            list.appendChild(item);
        });
    }
}

function selectRunningProcess(exeName, windowTitle = '') {
    const procInput = document.getElementById('input-add-game-proc');
    const nameInput = document.getElementById('input-add-game-name');
    if (procInput) procInput.value = exeName;
    if (nameInput && !nameInput.value.trim()) {
        let clean = windowTitle || exeName.replace(/\.exe$/i, '');
        clean = clean.replace(/([a-z])([A-Z0-9])/g, '$1 $2').replace(/[_-]/g, ' ').trim();
        nameInput.value = clean.charAt(0).toUpperCase() + clean.slice(1);
    }
    const panel = document.getElementById('panel-running-processes');
    if (panel) panel.classList.add('hidden');
}

function handleFilterRunningProcesses(e) {
    const query = e.target.value;
    if (cachedRunningProcesses) {
        renderRunningProcessesList(cachedRunningProcesses, query);
    }
}

async function handleOpenTaskManager() {
    try {
        await window.pywebview.api.open_task_manager();
    } catch (err) {
        console.error("Failed opening task manager:", err);
    }
}

async function handleConfirmAddGame() {
    const nameInput = document.getElementById('input-add-game-name');
    const procInput = document.getElementById('input-add-game-proc');
    const name = (nameInput?.value || '').trim();
    const proc = (procInput?.value || '').trim();

    if (!name) {
        if (nameInput) {
            nameInput.style.borderColor = '#ef4444';
            nameInput.focus();
        }
        return;
    }

    try {
        const res = await window.pywebview.api.add_game_profile(name, proc);
        if (res && res.success) {
            closeAddGameModal();
            await loadGamesUI();
        } else {
            alert((res && res.error) || 'Failed to add game profile.');
        }
    } catch (err) {
        console.error("add_game_profile error:", err);
    }
}

// Add Keybind Modal Handlers
function openAddKeybindModal() {
    if (!activeGameId) {
        alert("Please select or add a game first.");
        return;
    }
    const modal = document.getElementById('modal-add-keybind');
    if (!modal) return;
    const p = document.getElementById('input-bind-phrase');
    const k = document.getElementById('input-bind-key');
    const m = document.getElementById('input-bind-mods');
    const d = document.getElementById('input-bind-desc');
    if (p) { p.value = ''; p.style.borderColor = ''; }
    if (k) { k.value = ''; k.style.borderColor = ''; }
    if (m) m.value = '';
    if (d) d.value = '';
    modal.classList.remove('hidden');
    if (p) p.focus();
}

function closeAddKeybindModal() {
    const modal = document.getElementById('modal-add-keybind');
    if (modal) modal.classList.add('hidden');
}

async function handleConfirmAddKeybind() {
    const p = document.getElementById('input-bind-phrase');
    const k = document.getElementById('input-bind-key');
    const m = document.getElementById('input-bind-mods');
    const d = document.getElementById('input-bind-desc');

    const phrase = (p?.value || '').trim();
    const key = (k?.value || '').trim();
    const modsInput = (m?.value || '').trim();
    const modifiers = modsInput ? modsInput.split(',').map(s => s.trim().toLowerCase()).filter(Boolean) : [];
    const desc = (d?.value || '').trim() || phrase;

    if (!phrase) {
        if (p) { p.style.borderColor = '#ef4444'; p.focus(); }
        return;
    }
    if (!key) {
        if (k) { k.style.borderColor = '#ef4444'; k.focus(); }
        return;
    }

    try {
        const res = await window.pywebview.api.save_game_keybind(activeGameId, phrase, key, modifiers, desc);
        if (res && res.success) {
            currentGamesData.profiles[activeGameId].keybinds = res.keybinds;
            closeAddKeybindModal();
            renderActiveGameDetail();
        }
    } catch (e) {
        console.error("save_game_keybind error:", e);
    }
}

// Legacy alias pointing to modern in-app modal
const promptAddGame = openAddGameModal;

// Delete Active Game Profile
async function promptDeleteGame() {
    if (!activeGameId || !currentGamesData || !currentGamesData.profiles || !currentGamesData.profiles[activeGameId]) {
        return;
    }
    const profile = currentGamesData.profiles[activeGameId];
    if (!confirm(`Are you sure you want to delete "${profile.display_name || activeGameId}" and all its macros?`)) {
        return;
    }
    try {
        const res = await window.pywebview.api.delete_game_profile(activeGameId);
        if (res && res.success) {
            await loadGamesUI();
        } else {
            alert((res && res.error) || "Failed to delete game profile.");
        }
    } catch (e) {
        console.error("delete_game_profile error:", e);
    }
}

// Macro Testing
async function testMacro(phrase) {
    try {
        const res = await window.pywebview.api.test_game_macro(phrase);
        if (res && res.status === 'executed') {
            console.log(`Pulsed ${phrase} via DirectInput`);
        } else {
            alert((res && res.message) || 'Execution error');
        }
    } catch (e) {
        console.error("testMacro error:", e);
    }
}

// Macro Deletion
async function deleteMacro(phrase) {
    if (!confirm(`Delete voice trigger "${phrase}"?`)) return;
    try {
        const res = await window.pywebview.api.delete_game_keybind(activeGameId, phrase);
        if (res && res.success) {
            currentGamesData.profiles[activeGameId].keybinds = res.keybinds;
            renderActiveGameDetail();
        }
    } catch (e) {
        console.error("deleteMacro error:", e);
    }
}

function initGamesTabEvents() {
    // Add Game buttons
    document.getElementById('btn-add-game')?.addEventListener('click', openAddGameModal);
    document.getElementById('btn-add-game-empty')?.addEventListener('click', openAddGameModal);

    // Modal Add Game actions
    document.getElementById('btn-close-add-game-modal')?.addEventListener('click', closeAddGameModal);
    document.getElementById('btn-cancel-add-game')?.addEventListener('click', closeAddGameModal);
    document.getElementById('btn-confirm-add-game')?.addEventListener('click', handleConfirmAddGame);
    document.getElementById('btn-browse-game-exe')?.addEventListener('click', handleBrowseGameExe);
    document.getElementById('btn-toggle-running-procs')?.addEventListener('click', toggleRunningProcesses);
    document.getElementById('btn-refresh-running-procs')?.addEventListener('click', loadRunningProcesses);
    document.getElementById('btn-open-taskmgr')?.addEventListener('click', handleOpenTaskManager);
    document.getElementById('input-filter-running-proc')?.addEventListener('input', handleFilterRunningProcesses);

    // Modal Add Keybind actions
    document.getElementById('btn-add-keybind')?.addEventListener('click', openAddKeybindModal);
    document.getElementById('btn-close-add-keybind-modal')?.addEventListener('click', closeAddKeybindModal);
    document.getElementById('btn-cancel-add-keybind')?.addEventListener('click', closeAddKeybindModal);
    document.getElementById('btn-confirm-add-keybind')?.addEventListener('click', handleConfirmAddKeybind);

    // Close modals on clicking overlay backdrop
    document.getElementById('modal-add-game')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-add-game') closeAddGameModal();
    });
    document.getElementById('modal-add-keybind')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-add-keybind') closeAddKeybindModal();
    });

    // Close modals on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeAddGameModal();
            closeAddKeybindModal();
        }
    });

    // Clear Co-Pilot Log
    document.getElementById('btn-clear-copilot-log')?.addEventListener('click', handleClearCopilotLog);

    // Delete Game button
    document.getElementById('btn-delete-game')?.addEventListener('click', promptDeleteGame);

    // Binds Synchronization (Auto-Detection)
    document.getElementById('btn-sync-game-binds')?.addEventListener('click', async () => {
        if (!activeGameId) return;
        const btn = document.getElementById('btn-sync-game-binds');
        if (!btn) return;
        btn.disabled = true;
        btn.textContent = 'Scanning files...';

        try {
            const res = await window.pywebview.api.sync_game_bindings(activeGameId);
            if (res && res.success) {
                currentGamesData.profiles[activeGameId].keybinds = res.keybinds;
                renderActiveGameDetail();
                alert(`Auto-detection successful! Loaded ${res.count} keybinds.`);
            } else {
                alert((res && res.reason) || 'Auto-scan could not find bindings for this game. You can add them manually.');
            }
        } catch (e) {
            console.error("sync error:", e);
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<span class="icon">🔍</span> Scan / Sync Keybinds';
        }
    });

    // Scratchpad Autosave
    document.getElementById('game-scratchpad-input')?.addEventListener('input', (e) => {
        if (!activeGameId) return;
        const status = document.getElementById('scratchpad-save-status');
        if (status) status.textContent = 'Saving...';
        clearTimeout(scratchpadDebounceTimer);

        scratchpadDebounceTimer = setTimeout(async () => {
            const text = e.target.value;
            try {
                await window.pywebview.api.update_game_scratchpad(activeGameId, text);
                if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[activeGameId]) {
                    currentGamesData.profiles[activeGameId].scratchpad_raw = text;
                }
                if (status) status.textContent = 'All changes saved locally';
            } catch (err) {
                console.error("scratchpad save error:", err);
                if (status) status.textContent = 'Error saving changes';
            }
        }, 600);
    });
}

let telemetryPollInterval = null;

function startTelemetryPolling() {
    if (telemetryPollInterval) clearInterval(telemetryPollInterval);
    telemetryPollInterval = setInterval(async () => {
        if (activeGameId && window.pywebview && window.pywebview.api) {
            try {
                const telemetry = await window.pywebview.api.get_game_telemetry();
                if (activeGameId === 'elite_dangerous') {
                    updateTelemetryHUD(telemetry);
                }
                if (telemetry) {
                    if (telemetry.copilot_entries) {
                        const profile = currentGamesData?.profiles?.[activeGameId];
                        if (profile) profile.copilot_log = telemetry.copilot_entries;
                        renderCopilotLog(telemetry.copilot_entries);
                    }
                    if (telemetry.scratchpad !== undefined && document.activeElement !== document.getElementById('game-scratchpad-input')) {
                        const pad = document.getElementById('game-scratchpad-input');
                        if (pad && pad.value !== telemetry.scratchpad) {
                            pad.value = telemetry.scratchpad;
                            const profile = currentGamesData?.profiles?.[activeGameId];
                            if (profile) profile.scratchpad_raw = telemetry.scratchpad;
                        }
                    }
                }
            } catch (e) {
                // Ignore background poll errors
            }
        }
    }, 2500);
}

function updateTelemetryHUD(data) {
    const banner = document.getElementById('game-process-indicator');
    if (banner && data && data.active) {
        banner.textContent = `${data.star_system} | ${data.docked ? 'Docked (' + data.station + ')' : (data.supercruise ? 'Supercruise' : 'Normal Space')}`;
        banner.className = 'badge-status badge-online';
    }
}

// Global exposure for onclick handlers & external callers
window.testMacro = testMacro;
window.deleteMacro = deleteMacro;
window.deleteCopilotEntry = deleteCopilotEntry;
window.renderCopilotLog = renderCopilotLog;
window.loadGamesUI = loadGamesUI;
window.switchActiveGame = switchActiveGame;
window.promptAddGame = promptAddGame;
window.promptDeleteGame = promptDeleteGame;
window.startTelemetryPolling = startTelemetryPolling;
window.updateTelemetryHUD = updateTelemetryHUD;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGamesTabEvents);
} else {
    initGamesTabEvents();
}
