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
}

// Add New Game Profile
async function promptAddGame() {
    const name = prompt("Enter Game Name (e.g. Star Citizen, Cyberpunk 2077):");
    if (!name || !name.trim()) return;

    const proc = prompt("Enter executable/process name (optional, e.g. StarCitizen.exe):", "");

    try {
        const res = await window.pywebview.api.add_game_profile(name.trim(), proc ? proc.trim() : "");
        if (res && res.success) {
            await loadGamesUI();
        } else {
            alert((res && res.error) || "Failed to add game profile.");
        }
    } catch (e) {
        console.error("add_game_profile error:", e);
    }
}

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
    document.getElementById('btn-add-game')?.addEventListener('click', promptAddGame);
    document.getElementById('btn-add-game-empty')?.addEventListener('click', promptAddGame);

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

    // Prompt Modal for Adding Keybind
    document.getElementById('btn-add-keybind')?.addEventListener('click', async () => {
        if (!activeGameId) {
            alert("Please select or add a game first.");
            return;
        }

        const phrase = prompt("Enter voice trigger phrase (e.g., 'deploy heat sink' or 'open map'):");
        if (!phrase) return;

        const key = prompt("Enter primary key (e.g., 'v', 'i', 'delete', 'f1'):");
        if (!key) return;

        const modsInput = prompt("Enter modifier keys separated by comma (e.g., 'shift' or 'ctrl') or leave blank:");
        const modifiers = modsInput ? modsInput.split(',').map(s => s.trim()).filter(Boolean) : [];

        const desc = prompt("Enter brief description:", phrase);

        try {
            const res = await window.pywebview.api.save_game_keybind(activeGameId, phrase, key, modifiers, desc || phrase);
            if (res && res.success) {
                currentGamesData.profiles[activeGameId].keybinds = res.keybinds;
                renderActiveGameDetail();
            }
        } catch (e) {
            console.error("save_game_keybind error:", e);
        }
    });
}

// Global exposure for onclick handlers & external callers
window.testMacro = testMacro;
window.deleteMacro = deleteMacro;
window.loadGamesUI = loadGamesUI;
window.switchActiveGame = switchActiveGame;
window.promptAddGame = promptAddGame;
window.promptDeleteGame = promptDeleteGame;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGamesTabEvents);
} else {
    initGamesTabEvents();
}
