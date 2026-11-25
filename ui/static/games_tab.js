/**
 * Aether Desktop - Games Subsystem Frontend Controller
 * Manages game profile selection, dynamic add/delete of games, voice macros,
 * DirectInput scancode injection testing, binds synchronization, and scratchpad autosave.
 */

let currentGamesData = null;
let activeGameId = null;
let activeGameSubtab = 'settings';
let currentGamePoisList = [];
let currentGameTriggersList = [];
let currentGameTriggerEvents = [];
let scratchpadDebounceTimer = null;
let callsignDebounceTimer = null;
let suppressAlertsDebounceTimer = null;

function switchGameSubtab(tabKey) {
    const valid = ['settings', 'intel', 'controls'];
    const target = valid.includes(tabKey) ? tabKey : 'settings';
    activeGameSubtab = target;
    document.querySelectorAll('.game-subtab-btn').forEach(btn => {
        const isAct = btn.dataset.gameSubtab === target;
        btn.classList.toggle('active', isAct);
        btn.setAttribute('aria-selected', isAct ? 'true' : 'false');
    });
    valid.forEach(k => {
        const pane = document.getElementById(`game-subtab-${k}`);
        if (pane) pane.classList.toggle('hidden', k !== target);
    });
}
window.switchGameSubtab = switchGameSubtab;

function openRenameGameInline() {
    if (!activeGameId || !currentGamesData?.profiles?.[activeGameId]) return;
    const titleEl = document.getElementById('game-active-title');
    const editBtn = document.getElementById('btn-rename-game');
    const inlineWrap = document.getElementById('game-rename-inline');
    const inputEl = document.getElementById('input-rename-game');
    if (!inlineWrap || !inputEl) return;
    const currentName = currentGamesData.profiles[activeGameId].display_name || activeGameId;
    inputEl.value = currentName;
    if (titleEl) titleEl.classList.add('hidden');
    if (editBtn) editBtn.classList.add('hidden');
    inlineWrap.classList.remove('hidden');
    inputEl.focus();
    inputEl.select();
}

function closeRenameGameInline() {
    const titleEl = document.getElementById('game-active-title');
    const editBtn = document.getElementById('btn-rename-game');
    const inlineWrap = document.getElementById('game-rename-inline');
    if (inlineWrap) inlineWrap.classList.add('hidden');
    if (titleEl) titleEl.classList.remove('hidden');
    if (editBtn) editBtn.classList.remove('hidden');
}

async function confirmRenameGame() {
    if (!activeGameId) return;
    const inputEl = document.getElementById('input-rename-game');
    const newName = (inputEl?.value || '').trim();
    if (!newName) {
        if (inputEl) { inputEl.style.borderColor = '#ef4444'; inputEl.focus(); }
        return;
    }
    try {
        if (window.pywebview?.api?.rename_game_profile) {
            const res = await window.pywebview.api.rename_game_profile(activeGameId, newName);
            if (res && res.success) {
                if (currentGamesData?.profiles?.[activeGameId]) {
                    currentGamesData.profiles[activeGameId].display_name = res.display_name || newName;
                }
                const titleEl = document.getElementById('game-active-title');
                if (titleEl) titleEl.textContent = res.display_name || newName;
                closeRenameGameInline();
                renderGamesSidebar();
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(`Renamed game to "${res.display_name || newName}".`);
                }
            } else {
                alert((res && res.error) || 'Failed to rename game.');
            }
        }
    } catch (err) {
        console.error("rename_game_profile error:", err);
    }
}

function updateFpsPillUI(isEnabled) {
    const btn = document.getElementById('fps-toggle-btn');
    if (!btn) return;
    if (isEnabled) {
        btn.classList.add('active');
        btn.classList.remove('inactive');
        btn.innerHTML = '<span class="pill-icon">⚡</span><span class="pill-text">FPS: ON</span>';
        btn.setAttribute('aria-pressed', 'true');
    } else {
        btn.classList.add('inactive');
        btn.classList.remove('active');
        btn.innerHTML = '<span class="pill-icon">⚡</span><span class="pill-text">FPS: OFF</span>';
        btn.setAttribute('aria-pressed', 'false');
    }
}
window.updateFpsPillUI = updateFpsPillUI;

function getCurrentlySelectedGameId() {
    if (activeGameId) return activeGameId;
    if (window.activeGameId) return window.activeGameId;
    if (currentGamesData && currentGamesData.active_profile) return currentGamesData.active_profile;
    const activeItem = document.querySelector('.game-item.active') || document.querySelector('.game-list-item.active');
    if (activeItem && activeItem.dataset.gameId) return activeItem.dataset.gameId;
    if (currentGamesData && currentGamesData.profiles) {
        const keys = Object.keys(currentGamesData.profiles);
        if (keys.length > 0) return keys[0];
    }
    return null;
}
window.getCurrentlySelectedGameId = getCurrentlySelectedGameId;

async function handleToggleFpsMonitoring(e) {
    if (e) {
        try { if (typeof e.preventDefault === 'function') e.preventDefault(); } catch (_) {}
        try { if (typeof e.stopPropagation === 'function') e.stopPropagation(); } catch (_) {}
    }

    const fpsBtn = document.getElementById('fps-toggle-btn');
    if (!fpsBtn) return;
    const targetGameId = getCurrentlySelectedGameId();
    if (!targetGameId) return;

    // Detect current visual active state
    const isCurrentlyActive = fpsBtn.classList.contains('active');
    const newState = !isCurrentlyActive;

    // Instant optimistic visual feedback
    updateFpsPillUI(newState);
    if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[targetGameId]) {
        currentGamesData.profiles[targetGameId].fps_monitoring = newState;
    }

    // Persist via PyWebView bridge
    try {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.toggle_game_fps_monitoring === 'function') {
            const res = await window.pywebview.api.toggle_game_fps_monitoring(targetGameId, newState);
            if (res && res.fps_monitoring !== undefined) {
                const confirmedState = Boolean(res.fps_monitoring);
                if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[targetGameId]) {
                    currentGamesData.profiles[targetGameId].fps_monitoring = confirmedState;
                }
                updateFpsPillUI(confirmedState);
            }
        }
    } catch (err) {
        console.error("[FPS TOGGLE] Error toggling FPS tracking:", err);
    }
}
window.handleToggleFpsMonitoring = handleToggleFpsMonitoring;

function updateTelemetryPillUI(isEnabled) {
    const btn = document.getElementById('btn-toggle-telemetry');
    if (!btn) return;
    if (isEnabled) {
        btn.classList.add('active');
        btn.classList.remove('inactive');
        btn.innerHTML = '<span class="pill-icon">📡</span><span class="pill-text">TELEMETRY: ON</span>';
        btn.setAttribute('aria-pressed', 'true');
    } else {
        btn.classList.add('inactive');
        btn.classList.remove('active');
        btn.innerHTML = '<span class="pill-icon">📡</span><span class="pill-text">TELEMETRY: OFF</span>';
        btn.setAttribute('aria-pressed', 'false');
    }
}
window.updateTelemetryPillUI = updateTelemetryPillUI;

async function handleToggleTelemetry(e) {
    if (e) {
        try { if (typeof e.preventDefault === 'function') e.preventDefault(); } catch (_) {}
        try { if (typeof e.stopPropagation === 'function') e.stopPropagation(); } catch (_) {}
    }

    const telBtn = document.getElementById('btn-toggle-telemetry');
    if (!telBtn) return;
    const targetGameId = getCurrentlySelectedGameId();
    if (!targetGameId) return;

    const isCurrentlyActive = telBtn.classList.contains('active');
    const newState = !isCurrentlyActive;

    updateTelemetryPillUI(newState);
    if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[targetGameId]) {
        currentGamesData.profiles[targetGameId].telemetry_enabled = newState;
    }

    try {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.toggle_game_telemetry === 'function') {
            const res = await window.pywebview.api.toggle_game_telemetry(targetGameId, newState);
            if (res && res.telemetry_enabled !== undefined) {
                const confirmedState = Boolean(res.telemetry_enabled);
                if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[targetGameId]) {
                    currentGamesData.profiles[targetGameId].telemetry_enabled = confirmedState;
                }
                updateTelemetryPillUI(confirmedState);
            }
        }
    } catch (err) {
        console.error("[TELEMETRY TOGGLE] Error toggling game telemetry:", err);
    }
}
window.handleToggleTelemetry = handleToggleTelemetry;

function escapeGameHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

async function loadGamesUI() {
    try {
        if (!window.pywebview || !window.pywebview.api) {
            console.warn("pywebview API not available yet");
            return;
        }
        currentGamesData = await window.pywebview.api.get_game_profiles();
        activeGameId = currentGamesData.active_profile; window.activeGameId = activeGameId;
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
    const gameIds = Object.keys(profiles).sort((a, b) => {
        const nameA = (profiles[a]?.display_name || a).toLowerCase();
        const nameB = (profiles[b]?.display_name || b).toLowerCase();
        return nameA.localeCompare(nameB, undefined, { numeric: true });
    });

    if (gameIds.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 16px; color: var(--text-muted); font-size: 0.82rem; text-align: center;">
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
        item.dataset.gameId = gid;
        item.onclick = () => switchActiveGame(gid);

        item.innerHTML = `
            <span class="game-item-name">${escapeGameHtml(p.display_name || gid)}</span>
            <span class="badge-status ${isOnline ? 'badge-online' : 'badge-offline'}">
                ${isOnline ? 'RUNNING' : 'IDLE'}
            </span>
        `;
        listEl.appendChild(item);
    });
}

async function switchActiveGame(gid) {
    if (activeGameId === gid) return;
    activeGameId = gid; window.activeGameId = gid;
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
    closeRenameGameInline();
    const titleEl = document.getElementById('game-active-title');
    if (titleEl) titleEl.textContent = profile.display_name || activeGameId;
    switchGameSubtab(activeGameSubtab);

    // Per-Game HUD Preference Selector
    const hudPrefSelect = document.getElementById('select-game-hud-pref');
    if (hudPrefSelect) {
        hudPrefSelect.value = profile.hud_preference || 'default';
        hudPrefSelect.onchange = async (e) => {
            const newPref = e.target.value;
            try {
                if (window.pywebview?.api?.set_game_hud_preference) {
                    const res = await window.pywebview.api.set_game_hud_preference(activeGameId, newPref);
                    if (res && res.success) {
                        profile.hud_preference = res.hud_preference || newPref;
                    }
                }
            } catch (err) {
                console.error("Failed setting game HUD preference:", err);
            }
        };
    }

    const multiCharChk = document.getElementById('chk-game-multiple-characters');
    if (multiCharChk) {
        multiCharChk.checked = Boolean(profile.multiple_main_characters);
    }

    const callsignInput = document.getElementById('input-game-callsign');
    if (callsignInput) {
        const defaultCallsign = (profile.callsign !== undefined && profile.callsign !== null)
            ? String(profile.callsign)
            : '';
        callsignInput.value = defaultCallsign;
    }

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

    // Guidance Level Selector
    const guidanceSelect = document.getElementById('select-guidance-level');
    if (guidanceSelect) {
        guidanceSelect.value = profile.guidance_level || 'tactical';
        guidanceSelect.onchange = async (e) => {
            const newLevel = e.target.value;
            try {
                const res = await window.pywebview.api.set_guidance_level(activeGameId, newLevel);
                if (res && res.success) {
                    profile.guidance_level = newLevel;
                }
            } catch (err) {
                console.error("Failed setting guidance level:", err);
            }
        };
    }

    // Navigation Mode Selector
    const navSelect = document.getElementById('select-navigation-mode');

    // FPS Monitoring Pill UI Sync & Click Handler
    const fpsBtn = document.getElementById('fps-toggle-btn');
    if (fpsBtn) {
        const isFpsEnabled = profile.fps_monitoring !== false;
        updateFpsPillUI(isFpsEnabled);
        fpsBtn.onclick = handleToggleFpsMonitoring;
    }

    // Telemetry Co-Pilot Pill UI Sync & Click Handler
    const telBtn = document.getElementById('btn-toggle-telemetry');
    if (telBtn) {
        const isTelemetryEnabled = Boolean(profile.telemetry_enabled);
        updateTelemetryPillUI(isTelemetryEnabled);
        telBtn.onclick = handleToggleTelemetry;
    }
    if (navSelect) {
        navSelect.value = profile.navigation_mode || 'controller';
        navSelect.onchange = async (e) => {
            const newMode = e.target.value;
            try {
                const res = await window.pywebview.api.set_navigation_mode(activeGameId, newMode);
                if (res && res.success) {
                    profile.navigation_mode = newMode;
                }
            } catch (err) {
                console.error("Failed setting navigation mode:", err);
            }
        };
    }

    // Input Safety Mode (Autonomous vs Spotter-Only Anti-Cheat Safe)
    const inputModeSelect = document.getElementById('select-game-input-mode');
    if (inputModeSelect) {
        inputModeSelect.value = profile.input_mode || 'autonomous';
        inputModeSelect.onchange = async (e) => {
            const newMode = e.target.value;
            try {
                if (window.pywebview?.api?.set_game_input_mode) {
                    const res = await window.pywebview.api.set_game_input_mode(activeGameId, newMode);
                    if (res && res.success) {
                        profile.input_mode = newMode;
                        if (newMode === 'spotter_only' && navSelect) {
                            profile.navigation_mode = 'co_pilot_spotter';
                            navSelect.value = 'co_pilot_spotter';
                        }
                    }
                }
            } catch (err) {
                console.error("Failed setting input safety mode:", err);
            }
        };
    }

    // 3rd-Party Co-Pilot Quiet / Alert Suppression List
    const suppressInput = document.getElementById('input-game-suppress-alerts');
    if (suppressInput) {
        const list = Array.isArray(profile.suppress_alerts_when_running)
            ? profile.suppress_alerts_when_running
            : [];
        suppressInput.value = list.join(', ');
    }

    // Check Admin / UIPI elevation status for the selected game
    refreshGameElevationWarning(activeGameId);

    // 3rd-Party Companion Apps List (render cached first, then refresh live running status)
    renderCompanionAppsList(Array.isArray(profile.companion_apps) ? profile.companion_apps : []);
    refreshCompanionAppsStatus(activeGameId);

    // Tier 2 Community Python Extensions (SHA-256 Opt-In)
    refreshGameExtensions();

    // Keybinds Table (with live filter + count badge)
    renderKeybindsTable(profile);

    // Co-Pilot Vibe & Custom Free-Text Personality
    const vibeSelect = document.getElementById('select-game-copilot-vibe');
    const personaArea = document.getElementById('textarea-game-copilot-personality');
    const vibeStatus = document.getElementById('copilot-vibe-save-status');
    if (vibeSelect) vibeSelect.value = profile.copilot_vibe || 'witty';
    if (personaArea) personaArea.value = profile.copilot_personality || '';
    if (vibeStatus) vibeStatus.textContent = '';

    // Companion Chit-Chat Controls
    const ccFreq = document.getElementById('select-game-chitchat-freq');
    const ccScreen = document.getElementById('chk-game-chitchat-screen');
    const ccProfanity = document.getElementById('chk-game-chitchat-profanity');
    const ccEmotive = document.getElementById('chk-game-chitchat-emotive');
    const ccJokes = document.getElementById('chk-game-chitchat-jokes');
    const ccFacts = document.getElementById('chk-game-chitchat-facts');
    const ccRelevant = document.getElementById('chk-game-chitchat-relevant');
    const ccChatLocal = document.getElementById('chk-game-chat-local');
    const ccChatSystem = document.getElementById('chk-game-chat-system');
    const ccChatNpc = document.getElementById('chk-game-chat-npc');
    const ccChatSquad = document.getElementById('chk-game-chat-squadron');
    const ccChatPlayer = document.getElementById('chk-game-chat-player');
    const ccChatTranslate = document.getElementById('chk-game-chat-translate');
    const ccStatus = document.getElementById('chitchat-save-status');
    if (ccFreq) ccFreq.value = profile.chitchat_frequency || 'off';
    if (ccScreen) ccScreen.checked = Boolean(profile.chitchat_screen_peek);
    if (ccProfanity) ccProfanity.checked = Boolean(profile.chitchat_allow_profanity);
    if (ccEmotive) ccEmotive.checked = Boolean(profile.chitchat_emotive_voice);
    if (ccJokes) ccJokes.checked = Boolean(profile.chitchat_topic_jokes);
    if (ccFacts) ccFacts.checked = profile.chitchat_topic_facts !== false;
    if (ccRelevant) ccRelevant.checked = profile.chitchat_topic_relevant !== false;
    if (ccChatLocal) ccChatLocal.checked = Boolean(profile.chitchat_chat_local);
    if (ccChatSystem) ccChatSystem.checked = Boolean(profile.chitchat_chat_system);
    if (ccChatNpc) ccChatNpc.checked = Boolean(profile.chitchat_chat_npc);
    if (ccChatSquad) ccChatSquad.checked = profile.chitchat_chat_squadron !== false;
    if (ccChatPlayer) ccChatPlayer.checked = profile.chitchat_chat_player !== false;
    if (ccChatTranslate) ccChatTranslate.checked = profile.chitchat_chat_translate !== false;
    if (ccStatus) ccStatus.textContent = '';

    // Event Triggers (When / If -> Then) Table
    refreshGameTriggersTable(activeGameId);

    // Special Instructions (game-specific setup notes from profile or module default)
    refreshGameSpecialInstructions(activeGameId);

    // Custom Visual & Multi-Step Macros Table
    refreshGameMacrosTable(activeGameId, profile);

    // Points of Interest & Saved Locations Table
    refreshGamePoisTable(activeGameId);

    // Scratchpad
    const pad = document.getElementById('game-scratchpad-input');
    if (pad) {
        pad.value = profile.scratchpad_raw || '';
    }

    // Co-Pilot Intel & Log
    renderCopilotLog(profile.copilot_log || []);
}

let currentGameMacrosMap = {};

function renderKeybindsTable(profile) {
    const tbody = document.getElementById('tbody-game-binds');
    const countBadge = document.getElementById('badge-keybinds-count');
    const filterInput = document.getElementById('input-filter-keybinds');
    if (!tbody) return;

    tbody.innerHTML = '';
    const keybinds = (profile && profile.keybinds) ? profile.keybinds : {};
    const allPhrases = Object.keys(keybinds);
    if (countBadge) countBadge.textContent = String(allPhrases.length);

    const q = (filterInput && filterInput.value ? filterInput.value : '').trim().toLowerCase();
    const phrases = q
        ? allPhrases.filter(ph => {
            const b = keybinds[ph] || {};
            return ph.toLowerCase().includes(q)
                || String(b.key || '').toLowerCase().includes(q)
                || String(b.description || '').toLowerCase().includes(q);
        })
        : allPhrases;

    if (phrases.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 20px;">${allPhrases.length === 0 ? 'No keybinds configured yet. Click <strong>+ Add Keybind</strong> or <strong>Scan / Sync</strong>.' : 'No keybinds match your filter.'}</td></tr>`;
        return;
    }

    phrases.forEach(phrase => {
        const bind = keybinds[phrase];
        const tr = document.createElement('tr');

        const modsText = (bind.modifiers && bind.modifiers.length > 0) ? bind.modifiers.join(' + ').toUpperCase() : 'None';
        const keyText = (bind.key || '').toUpperCase();

        tr.innerHTML = `
            <td><strong>"${escapeGameHtml(phrase)}"</strong></td>
            <td><code>${escapeGameHtml(modsText)}</code></td>
            <td><code>${escapeGameHtml(keyText)}</code></td>
            <td>${escapeGameHtml(bind.description || '')}</td>
            <td class="macro-actions-cell"></td>
        `;
        const actionsCell = tr.querySelector('.macro-actions-cell');
        if (actionsCell) {
            const testBtn = document.createElement('button');
            testBtn.type = 'button';
            testBtn.className = 'btn-secondary-sm';
            testBtn.textContent = 'Test';
            testBtn.onclick = () => testMacro(phrase);

            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'btn-danger-sm';
            delBtn.style.marginLeft = '4px';
            delBtn.textContent = 'Delete';
            delBtn.onclick = () => deleteMacro(phrase);

            actionsCell.appendChild(testBtn);
            actionsCell.appendChild(delBtn);
        }
        tbody.appendChild(tr);
    });
}

function summarizeMacroSteps(steps) {
    if (!Array.isArray(steps) || steps.length === 0) return '0 steps';
    const labels = steps.slice(0, 3).map(s => {
        if (!s || typeof s !== 'object') return 'step';
        const act = String(s.action || 'press').toLowerCase();
        if (act === 'vision_click') return `👁 Click "${s.target || 'UI'}"`;
        if (act === 'press') return `⌨ ${String(s.key || '').toUpperCase()}${(s.repeat && s.repeat > 1) ? ` ×${s.repeat}` : ''}`;
        if (act === 'trigger_bind') return `🔗 ${s.phrase || s.bind || 'bind'}${(s.repeat && s.repeat > 1) ? ` ×${s.repeat}` : ''}`;
        if (act === 'wait') return `⏱ ${s.delay_ms || 0}ms`;
        if (act === 'paste' || act === 'type') return `📝 ${act}`;
        return act;
    });
    const extra = steps.length > 3 ? ` +${steps.length - 3} more` : '';
    return `${steps.length} step${steps.length === 1 ? '' : 's'}: ${labels.join(' → ')}${extra}`;
}

async function refreshGameMacrosTable(gameId, profile) {
    let macros = (profile && profile.sequences) ? { ...profile.sequences } : {};
    try {
        if (window.pywebview?.api?.get_game_macros && gameId) {
            const res = await window.pywebview.api.get_game_macros(gameId);
            if (res && res.success && res.macros) {
                macros = res.macros;
            }
        }
    } catch (e) {
        console.error("get_game_macros error:", e);
    }
    currentGameMacrosMap = macros;
    renderGameMacrosTable(macros);
}

function renderGameMacrosTable(macros) {
    const tbody = document.getElementById('tbody-game-macros');
    const badge = document.getElementById('badge-macros-count');
    if (!tbody) return;

    tbody.innerHTML = '';
    const names = Object.keys(macros || {});
    if (badge) badge.textContent = String(names.length);

    if (names.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 20px;">No custom macros yet. Click <strong>+ Add Macro</strong> or ask Aether by voice to create one.</td></tr>`;
        return;
    }

    names.forEach(name => {
        const m = macros[name] || {};
        const tr = document.createElement('tr');
        const aliases = Array.isArray(m.aliases) && m.aliases.length > 0
            ? `<div style="font-size: 0.72rem; color: var(--text-dim); margin-top: 2px;">Aliases: ${m.aliases.map(a => `"${escapeGameHtml(a)}"`).join(', ')}</div>`
            : '';
        const cond = m.condition
            ? `<span class="badge-status badge-info" style="font-size: 0.65rem; margin-left: 6px;">${escapeGameHtml(m.condition)}</span>`
            : '';
        const srcTag = m.source === 'module_default'
            ? `<span style="font-size: 0.65rem; color: var(--text-dim); margin-left: 6px;">(Module)</span>`
            : '';

        tr.innerHTML = `
            <td><strong>"${escapeGameHtml(name)}"</strong>${srcTag}${cond}</td>
            <td>
                <div>${escapeGameHtml(m.description || '')}</div>
                ${aliases}
            </td>
            <td style="font-size: 0.78rem; color: var(--text-muted);">${escapeGameHtml(summarizeMacroSteps(m.steps))}</td>
            <td class="custom-macro-actions-cell"></td>
        `;
        const actionsCell = tr.querySelector('.custom-macro-actions-cell');
        if (actionsCell) {
            const testBtn = document.createElement('button');
            testBtn.type = 'button';
            testBtn.className = 'btn-secondary-sm';
            testBtn.textContent = 'Test';
            testBtn.onclick = () => testMacro(name);

            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn-secondary-sm';
            editBtn.style.marginLeft = '4px';
            editBtn.textContent = 'Edit';
            editBtn.onclick = () => openMacroModal(name, m);

            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'btn-danger-sm';
            delBtn.style.marginLeft = '4px';
            delBtn.textContent = 'Delete';
            delBtn.onclick = () => deleteGameMacro(name);

            actionsCell.appendChild(testBtn);
            actionsCell.appendChild(editBtn);
            actionsCell.appendChild(delBtn);
        }
        tbody.appendChild(tr);
    });
}

function openMacroModal(existingName = '', existingDef = null) {
    if (!activeGameId) {
        alert("Please select a game profile first.");
        return;
    }
    const modal = document.getElementById('modal-edit-macro');
    if (!modal) return;

    const titleEl = document.getElementById('modal-edit-macro-title');
    const oldEl = document.getElementById('input-macro-old-name');
    const nameEl = document.getElementById('input-macro-name');
    const aliasEl = document.getElementById('input-macro-aliases');
    const descEl = document.getElementById('input-macro-desc');
    const condEl = document.getElementById('input-macro-condition');
    const ackEl = document.getElementById('input-macro-ack');
    const stepsEl = document.getElementById('textarea-macro-steps');
    const aiEl = document.getElementById('input-macro-ai-prompt');
    const shotChk = document.getElementById('chk-macro-ai-screenshot');
    const errEl = document.getElementById('macro-steps-error');

    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
    if (aiEl) aiEl.value = '';
    if (shotChk) {
        const isRunning = Boolean(currentGamesData?.running_status?.[activeGameId]);
        shotChk.checked = isRunning;
    }

    const isEdit = Boolean(existingName && existingDef);
    if (titleEl) titleEl.textContent = isEdit ? `Edit Macro: ${existingName}` : 'Add Custom Macro';
    if (oldEl) oldEl.value = isEdit ? existingName : '';
    if (nameEl) { nameEl.value = isEdit ? existingName : ''; nameEl.style.borderColor = ''; }
    if (aliasEl) aliasEl.value = isEdit && Array.isArray(existingDef.aliases) ? existingDef.aliases.join(', ') : '';
    if (descEl) descEl.value = isEdit ? (existingDef.description || '') : '';
    if (condEl) condEl.value = isEdit ? (existingDef.condition || '') : '';
    if (ackEl) ackEl.value = isEdit ? (existingDef.ack || '') : '';
    if (stepsEl) {
        const defaultSteps = isEdit && Array.isArray(existingDef.steps)
            ? existingDef.steps
            : [{ action: "vision_click", target: "Target button label", button: "left", hold_ms: 100, abort_if_not_found: true, delay_ms: 200 }];
        stepsEl.value = JSON.stringify(defaultSteps, null, 2);
    }

    modal.classList.remove('hidden');
    if (nameEl) nameEl.focus();
}

function closeMacroModal() {
    const modal = document.getElementById('modal-edit-macro');
    if (modal) modal.classList.add('hidden');
}

function insertMacroStepSnippet(kind) {
    const stepsEl = document.getElementById('textarea-macro-steps');
    if (!stepsEl) return;
    const snippets = {
        vision_click: { action: "vision_click", target: "Button label on screen", button: "left", hold_ms: 100, abort_if_not_found: true, delay_ms: 200 },
        press: { action: "press", key: "x", modifiers: [], hold_ms: 80, repeat: 5, interval_ms: 20, delay_ms: 100 },
        trigger_bind: { action: "trigger_bind", phrase: "divert power to engines", repeat: 1, interval_ms: 50, delay_ms: 100 },
        wait: { action: "wait", delay_ms: 500 },
        paste: { action: "paste", text: "Text to paste", delay_ms: 150 },
    };
    const snippet = snippets[kind];
    if (!snippet) return;
    let arr = [];
    try {
        const parsed = JSON.parse(stepsEl.value || '[]');
        if (Array.isArray(parsed)) arr = parsed;
    } catch (_) {
        arr = [];
    }
    arr.push(snippet);
    stepsEl.value = JSON.stringify(arr, null, 2);
}

async function handleGenerateMacroSteps() {
    const aiInput = document.getElementById('input-macro-ai-prompt');
    const shotChk = document.getElementById('chk-macro-ai-screenshot');
    const btn = document.getElementById('btn-generate-macro-steps');
    const statusEl = document.getElementById('macro-ai-status');
    const instruction = (aiInput?.value || '').trim();
    const includeScreenshot = Boolean(shotChk && shotChk.checked);
    if (!instruction) {
        if (aiInput) aiInput.focus();
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
    if (statusEl) {
        statusEl.textContent = includeScreenshot
            ? 'Capturing live game screen & drafting macro steps...'
            : 'Aether is drafting your macro steps...';
    }

    try {
        const res = await window.pywebview.api.generate_game_macro_from_text(activeGameId, instruction, includeScreenshot);
        if (res && res.success && res.macro) {
            const m = res.macro;
            const nameEl = document.getElementById('input-macro-name');
            const aliasEl = document.getElementById('input-macro-aliases');
            const descEl = document.getElementById('input-macro-desc');
            const condEl = document.getElementById('input-macro-condition');
            const ackEl = document.getElementById('input-macro-ack');
            const stepsEl = document.getElementById('textarea-macro-steps');

            if (nameEl && m.name && !nameEl.value.trim()) nameEl.value = m.name;
            if (aliasEl && Array.isArray(m.aliases)) aliasEl.value = m.aliases.join(', ');
            if (descEl && m.description) descEl.value = m.description;
            if (condEl && m.condition !== undefined) condEl.value = m.condition || '';
            if (ackEl && m.ack) ackEl.value = m.ack;
            if (stepsEl && Array.isArray(m.steps)) stepsEl.value = JSON.stringify(m.steps, null, 2);
            if (statusEl) {
                statusEl.textContent = res.used_screenshot
                    ? 'Draft ready (analyzed live game screenshot)! Review or tweak below, then click Save Macro.'
                    : 'Draft ready! Review or edit the steps below, then click Save Macro.';
            }
        } else {
            if (statusEl) statusEl.textContent = (res && res.error) || 'Could not generate macro steps.';
        }
    } catch (e) {
        console.error("generate_game_macro_from_text error:", e);
        if (statusEl) statusEl.textContent = 'Error generating macro steps.';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
    }
}

async function handleConfirmSaveMacro() {
    const oldName = (document.getElementById('input-macro-old-name')?.value || '').trim();
    const nameEl = document.getElementById('input-macro-name');
    const aliasEl = document.getElementById('input-macro-aliases');
    const descEl = document.getElementById('input-macro-desc');
    const condEl = document.getElementById('input-macro-condition');
    const ackEl = document.getElementById('input-macro-ack');
    const stepsEl = document.getElementById('textarea-macro-steps');
    const errEl = document.getElementById('macro-steps-error');

    const name = (nameEl?.value || '').trim();
    if (!name) {
        if (nameEl) { nameEl.style.borderColor = '#ef4444'; nameEl.focus(); }
        return;
    }

    let parsedSteps = null;
    try {
        parsedSteps = JSON.parse(stepsEl?.value || '[]');
        if (!Array.isArray(parsedSteps) || parsedSteps.length === 0) {
            throw new Error('Steps must be a non-empty JSON array.');
        }
        if (errEl) errEl.style.display = 'none';
    } catch (parseErr) {
        if (errEl) {
            errEl.textContent = `Invalid steps JSON: ${parseErr.message}`;
            errEl.style.display = 'block';
        }
        return;
    }

    const aliases = (aliasEl?.value || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
    const desc = (descEl?.value || '').trim() || name;
    const condition = (condEl?.value || '').trim();
    const ack = (ackEl?.value || '').trim();

    try {
        const res = await window.pywebview.api.save_game_macro(
            activeGameId, name, parsedSteps, desc, condition, ack, aliases, oldName
        );
        if (res && res.success) {
            currentGameMacrosMap = res.macros || {};
            if (currentGamesData?.profiles?.[activeGameId]) {
                currentGamesData.profiles[activeGameId].sequences = res.macros || {};
            }
            closeMacroModal();
            renderGameMacrosTable(currentGameMacrosMap);
        } else if (errEl) {
            errEl.textContent = (res && res.error) || 'Failed to save macro.';
            errEl.style.display = 'block';
        }
    } catch (e) {
        console.error("save_game_macro error:", e);
    }
}

async function deleteGameMacro(name) {
    if (!activeGameId || !name) return;
    if (!confirm(`Delete custom macro "${name}"?`)) return;
    try {
        const res = await window.pywebview.api.delete_game_macro(activeGameId, name);
        if (res && res.success) {
            currentGameMacrosMap = res.macros || {};
            renderGameMacrosTable(currentGameMacrosMap);
        }
    } catch (e) {
        console.error("delete_game_macro error:", e);
    }
}

async function refreshGamePoisTable(gameId) {
    if (!gameId || !window.pywebview?.api?.get_game_pois) return;
    try {
        const res = await window.pywebview.api.get_game_pois(gameId);
        if (res && res.success && Array.isArray(res.pois)) {
            currentGamePoisList = res.pois;
            renderGamePoisTable(res.pois);
        }
    } catch (e) {
        console.error("get_game_pois error:", e);
    }
}

function renderGamePoisTable(pois) {
    const tbody = document.getElementById('tbody-game-pois');
    if (!tbody) return;

    const list = Array.isArray(pois) ? pois : [];
    currentGamePoisList = list;

    tbody.innerHTML = '';
    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 20px;">No POIs or mobile assets tracked yet. Click <strong>+ Add POI</strong> or say <em>"Aether, remember that [Name] is in [System]"</em>.</td></tr>`;
        return;
    }

    list.forEach(poi => {
        const tr = document.createElement('tr');
        const callsignTag = poi.callsign
            ? ` <span style="font-size: 0.72rem; color: var(--text-dim);">(${escapeGameHtml(poi.callsign)})</span>`
            : '';
        const srcBadge = poi.is_live && poi.source === 'telemetry'
            ? `<span class="badge-status badge-online" style="font-size: 0.62rem; margin-left: 6px;">LIVE</span>`
            : (poi.source === 'voice'
                ? `<span class="badge-status badge-info" style="font-size: 0.62rem; margin-left: 6px;">VOICE</span>`
                : `<span class="badge-status badge-offline" style="font-size: 0.62rem; margin-left: 6px;">SAVED</span>`);
        const aliasesText = Array.isArray(poi.aliases) && poi.aliases.length > 0
            ? poi.aliases.map(a => `<code>${escapeGameHtml(a)}</code>`).join(' ')
            : '<span style="color: var(--text-dim); font-size: 0.76rem;">—</span>';

        tr.innerHTML = `
            <td><strong>${escapeGameHtml(poi.name || poi.id || '')}</strong>${callsignTag}${srcBadge}</td>
            <td><code>${escapeGameHtml(poi.system || '')}</code></td>
            <td>${aliasesText}</td>
            <td class="poi-actions-cell"></td>
        `;
        const actionsCell = tr.querySelector('.poi-actions-cell');
        if (actionsCell) {
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn-secondary-sm';
            editBtn.textContent = 'Edit';
            editBtn.onclick = () => openPoiModal(poi);
            actionsCell.appendChild(editBtn);

            if (poi.saved_id || !poi.is_live) {
                const delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.className = 'btn-danger-sm';
                delBtn.style.marginLeft = '4px';
                delBtn.textContent = 'Delete';
                delBtn.onclick = () => deleteGamePoi(poi.saved_id || poi.id, poi.name || poi.id);
                actionsCell.appendChild(delBtn);
            }
        }
        tbody.appendChild(tr);
    });
}

function openPoiModal(existingPoi = null) {
    if (!activeGameId) {
        alert("Please select a game profile first.");
        return;
    }
    const modal = document.getElementById('modal-edit-poi');
    if (!modal) return;
    const titleEl = document.getElementById('modal-edit-poi-title');
    const idEl = document.getElementById('input-poi-id');
    const nameEl = document.getElementById('input-poi-name');
    const callsignEl = document.getElementById('input-poi-callsign');
    const locEl = document.getElementById('input-poi-location');
    const aliasesEl = document.getElementById('input-poi-aliases');
    const errEl = document.getElementById('poi-modal-error');

    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
    const isEdit = Boolean(existingPoi);
    if (titleEl) titleEl.textContent = isEdit ? `Edit POI: ${existingPoi.name || existingPoi.id}` : 'Add Point of Interest';
    if (idEl) idEl.value = isEdit ? (existingPoi.saved_id || existingPoi.id || '') : '';
    if (nameEl) { nameEl.value = isEdit ? (existingPoi.name || '') : ''; nameEl.style.borderColor = ''; }
    if (callsignEl) callsignEl.value = isEdit ? (existingPoi.callsign || '') : '';
    if (locEl) { locEl.value = isEdit ? (existingPoi.system || '') : ''; locEl.style.borderColor = ''; }
    if (aliasesEl) aliasesEl.value = isEdit && Array.isArray(existingPoi.aliases) ? existingPoi.aliases.join(', ') : '';

    modal.classList.remove('hidden');
    if (nameEl) nameEl.focus();
}

function closePoiModal() {
    const modal = document.getElementById('modal-edit-poi');
    if (modal) modal.classList.add('hidden');
}

async function handleConfirmSavePoi() {
    if (!activeGameId) return;
    const idEl = document.getElementById('input-poi-id');
    const nameEl = document.getElementById('input-poi-name');
    const callsignEl = document.getElementById('input-poi-callsign');
    const locEl = document.getElementById('input-poi-location');
    const aliasesEl = document.getElementById('input-poi-aliases');
    const errEl = document.getElementById('poi-modal-error');

    const poiId = (idEl?.value || '').trim();
    const name = (nameEl?.value || '').trim();
    const callsign = (callsignEl?.value || '').trim();
    const location = (locEl?.value || '').trim();
    const aliases = (aliasesEl?.value || '').split(',').map(s => s.trim()).filter(Boolean);

    if (!name) {
        if (nameEl) { nameEl.style.borderColor = '#ef4444'; nameEl.focus(); }
        return;
    }
    if (!location) {
        if (locEl) { locEl.style.borderColor = '#ef4444'; locEl.focus(); }
        return;
    }

    try {
        const res = await window.pywebview.api.save_game_poi(activeGameId, name, location, aliases, poiId, callsign);
        if (res && res.success) {
            closePoiModal();
            renderGamePoisTable(res.pois || []);
        } else if (errEl) {
            errEl.textContent = (res && res.error) || 'Failed to save POI.';
            errEl.style.display = 'block';
        }
    } catch (e) {
        console.error("save_game_poi error:", e);
    }
}

async function deleteGamePoi(poiId, poiName) {
    if (!activeGameId || !poiId) return;
    if (!confirm(`Delete saved POI "${poiName || poiId}"?`)) return;
    try {
        const res = await window.pywebview.api.delete_game_poi(activeGameId, poiId);
        if (res && res.success) {
            renderGamePoisTable(res.pois || []);
        }
    } catch (e) {
        console.error("delete_game_poi error:", e);
    }
}

let specialInstructionsDebounceTimer = null;

async function refreshGameSpecialInstructions(gameId) {
    const area = document.getElementById('textarea-game-special-instructions');
    const resetBtn = document.getElementById('btn-reset-special-instructions');
    const statusEl = document.getElementById('special-instructions-save-status');
    if (!area || !gameId || !window.pywebview?.api?.get_game_special_instructions) return;
    if (statusEl) statusEl.textContent = '';
    try {
        const res = await window.pywebview.api.get_game_special_instructions(gameId);
        if (res && res.success) {
            area.value = res.text || '';
            if (resetBtn) {
                resetBtn.classList.toggle('hidden', !(res.module_default && res.is_custom));
            }
        }
    } catch (e) {
        console.error("get_game_special_instructions error:", e);
    }
}

async function refreshGameTriggersTable(gameId) {
    if (!gameId || !window.pywebview?.api?.get_game_triggers) return;
    try {
        const res = await window.pywebview.api.get_game_triggers(gameId);
        if (res && res.success) {
            currentGameTriggersList = Array.isArray(res.triggers) ? res.triggers : [];
            currentGameTriggerEvents = Array.isArray(res.trigger_events) ? res.trigger_events : [];
            renderGameTriggersTable(currentGameTriggersList);
        }
    } catch (e) {
        console.error("get_game_triggers error:", e);
    }
}

function summarizeTriggerAction(tr) {
    if (tr.action_label) return tr.action_label;
    const t = String(tr.action_type || 'keybind').toLowerCase();
    if (t === 'keybind') return `Keybind: "${tr.phrase || ''}"${tr.hold_ms ? ` (hold ${tr.hold_ms}ms)` : ''}`;
    if (t === 'macro') return `Macro: "${tr.macro_name || ''}"`;
    if (t === 'ui_task') return `UI Task: ${tr.goal || ''}`;
    if (t === 'speak') return `Speak: "${tr.speak_text || ''}"`;
    if (t === 'key_sequence') return summarizeMacroSteps(tr.steps || []);
    return t;
}

function renderGameTriggersTable(triggers) {
    const tbody = document.getElementById('tbody-game-triggers');
    if (!tbody) return;
    const list = Array.isArray(triggers) ? triggers : [];
    tbody.innerHTML = '';

    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 20px;">No event triggers configured yet. Click <strong>+ Add Trigger</strong> to automate actions on live game events.</td></tr>`;
        return;
    }

    list.forEach(tr => {
        const row = document.createElement('tr');
        const whenText = tr.event_label || tr.event || '';
        const condBadge = tr.condition
            ? `<div style="font-size: 0.72rem; color: #93c5fd; margin-top: 2px;">IF <code>${escapeGameHtml(tr.condition)}</code></div>`
            : '';
        const delayNote = tr.delay_ms
            ? `<span style="font-size: 0.7rem; color: var(--text-dim); margin-left: 6px;">(${(tr.delay_ms / 1000).toFixed(1)}s delay)</span>`
            : '';

        row.innerHTML = `
            <td class="trigger-toggle-cell" style="text-align: center;"></td>
            <td>
                <strong>${escapeGameHtml(tr.name || tr.id || '')}</strong>
                ${tr.description ? `<div style="font-size: 0.72rem; color: var(--text-dim); margin-top: 2px;">${escapeGameHtml(tr.description)}</div>` : ''}
            </td>
            <td>
                <span>WHEN <strong>${escapeGameHtml(whenText)}</strong></span>
                ${condBadge}
            </td>
            <td>${escapeGameHtml(summarizeTriggerAction(tr))}${delayNote}</td>
            <td class="trigger-actions-cell"></td>
        `;

        const toggleCell = row.querySelector('.trigger-toggle-cell');
        if (toggleCell) {
            const chk = document.createElement('input');
            chk.type = 'checkbox';
            chk.checked = Boolean(tr.enabled);
            chk.title = tr.enabled ? 'Trigger is ON (click to disable)' : 'Trigger is OFF (click to enable)';
            chk.onchange = () => toggleGameTrigger(tr.id, chk.checked);
            toggleCell.appendChild(chk);
        }

        const actionsCell = row.querySelector('.trigger-actions-cell');
        if (actionsCell) {
            const testBtn = document.createElement('button');
            testBtn.type = 'button';
            testBtn.className = 'btn-secondary-sm';
            testBtn.textContent = 'Test';
            testBtn.onclick = () => testGameTrigger(tr.id, tr.name || tr.id);

            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn-secondary-sm';
            editBtn.style.marginLeft = '4px';
            editBtn.textContent = 'Edit';
            editBtn.onclick = () => openTriggerModal(tr);

            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'btn-danger-sm';
            delBtn.style.marginLeft = '4px';
            delBtn.textContent = 'Delete';
            delBtn.onclick = () => deleteGameTrigger(tr.id, tr.name || tr.id);

            actionsCell.appendChild(testBtn);
            actionsCell.appendChild(editBtn);
            actionsCell.appendChild(delBtn);
        }
        tbody.appendChild(row);
    });
}

async function toggleGameTrigger(triggerId, enabled) {
    if (!activeGameId || !triggerId || !window.pywebview?.api?.toggle_game_trigger) return;
    try {
        const res = await window.pywebview.api.toggle_game_trigger(activeGameId, triggerId, enabled);
        if (res && res.success && Array.isArray(res.triggers)) {
            currentGameTriggersList = res.triggers;
            renderGameTriggersTable(res.triggers);
        }
    } catch (e) {
        console.error("toggle_game_trigger error:", e);
    }
}

async function testGameTrigger(triggerId, name) {
    if (!activeGameId || !triggerId || !window.pywebview?.api?.test_game_trigger) return;
    try {
        const res = await window.pywebview.api.test_game_trigger(activeGameId, triggerId);
        const msg = (res && res.message) || `Tested trigger "${name}".`;
        if (window.aetherUI?.showToast) {
            window.aetherUI.showToast(msg);
        } else {
            alert(msg);
        }
    } catch (e) {
        console.error("test_game_trigger error:", e);
    }
}

async function deleteGameTrigger(triggerId, name) {
    if (!activeGameId || !triggerId) return;
    if (!confirm(`Delete event trigger "${name}"?`)) return;
    try {
        const res = await window.pywebview.api.delete_game_trigger(activeGameId, triggerId);
        if (res && res.success) {
            currentGameTriggersList = res.triggers || [];
            renderGameTriggersTable(currentGameTriggersList);
        }
    } catch (e) {
        console.error("delete_game_trigger error:", e);
    }
}

function syncTriggerActionFields() {
    const typeEl = document.getElementById('select-trigger-action-type');
    const act = typeEl ? typeEl.value : 'keybind';
    const map = {
        keybind: 'field-trigger-keybind',
        macro: 'field-trigger-macro',
        key_sequence: 'field-trigger-sequence',
        ui_task: 'field-trigger-ui-task',
        speak: 'field-trigger-speak',
    };
    Object.entries(map).forEach(([k, id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', k !== act);
    });
}

function openTriggerModal(existingRule = null) {
    if (!activeGameId) {
        alert("Please select a game profile first.");
        return;
    }
    const modal = document.getElementById('modal-edit-trigger');
    if (!modal) return;

    const titleEl = document.getElementById('modal-edit-trigger-title');
    const idEl = document.getElementById('input-trigger-id');
    const nameEl = document.getElementById('input-trigger-name');
    const evSel = document.getElementById('select-trigger-event');
    const customEv = document.getElementById('input-trigger-custom-event');
    const condEl = document.getElementById('input-trigger-condition');
    const typeEl = document.getElementById('select-trigger-action-type');
    const delayEl = document.getElementById('input-trigger-delay-ms');
    const cdEl = document.getElementById('input-trigger-cooldown-sec');
    const phraseEl = document.getElementById('input-trigger-phrase');
    const holdEl = document.getElementById('input-trigger-hold-ms');
    const macroEl = document.getElementById('input-trigger-macro-name');
    const stepsEl = document.getElementById('textarea-trigger-steps');
    const goalEl = document.getElementById('input-trigger-goal');
    const speakEl = document.getElementById('input-trigger-speak-text');
    const labelEl = document.getElementById('input-trigger-action-label');
    const enChk = document.getElementById('chk-trigger-enabled');
    const aiEl = document.getElementById('input-trigger-ai-prompt');
    const aiStatus = document.getElementById('trigger-ai-status');
    const errEl = document.getElementById('trigger-modal-error');

    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
    if (aiEl) aiEl.value = '';
    if (aiStatus) aiStatus.textContent = 'Aether will draft the When/If condition and Then action below for you to review or tweak.';

    // Populate event dropdown from active module's declarative trigger_events (grouped by category if present)
    if (evSel) {
        evSel.innerHTML = '';
        const grouped = new Map();
        (currentGameTriggerEvents || []).forEach(ev => {
            const cat = ev.category || '';
            if (!grouped.has(cat)) grouped.set(cat, []);
            grouped.get(cat).push(ev);
        });
        grouped.forEach((items, cat) => {
            if (cat) {
                const og = document.createElement('optgroup');
                og.label = cat;
                items.forEach(ev => {
                    const opt = document.createElement('option');
                    opt.value = ev.event;
                    opt.textContent = ev.label || ev.event;
                    og.appendChild(opt);
                });
                evSel.appendChild(og);
            } else {
                items.forEach(ev => {
                    const opt = document.createElement('option');
                    opt.value = ev.event;
                    opt.textContent = ev.label || ev.event;
                    evSel.appendChild(opt);
                });
            }
        });
        const customOpt = document.createElement('option');
        customOpt.value = '__custom__';
        customOpt.textContent = 'Custom Telemetry Event...';
        evSel.appendChild(customOpt);
    }

    const isEdit = Boolean(existingRule);
    if (titleEl) titleEl.textContent = isEdit ? `Edit Trigger: ${existingRule.name || existingRule.id}` : 'Add Event Trigger';
    if (idEl) idEl.value = isEdit ? (existingRule.id || '') : '';
    if (nameEl) { nameEl.value = isEdit ? (existingRule.name || '') : ''; nameEl.style.borderColor = ''; }

    const targetEv = isEdit ? (existingRule.event || '') : (currentGameTriggerEvents[0]?.event || '__custom__');
    const hasKnownEv = (currentGameTriggerEvents || []).some(e => e.event === targetEv);
    if (evSel) evSel.value = hasKnownEv ? targetEv : '__custom__';
    if (customEv) {
        customEv.value = hasKnownEv ? '' : targetEv;
        customEv.classList.toggle('hidden', hasKnownEv);
    }

    if (condEl) condEl.value = isEdit ? (existingRule.condition || '') : '';
    if (typeEl) typeEl.value = isEdit ? (existingRule.action_type || 'keybind') : 'keybind';
    if (delayEl) delayEl.value = isEdit ? String(existingRule.delay_ms || 0) : '0';
    if (cdEl) cdEl.value = isEdit ? String(existingRule.cooldown_sec != null ? existingRule.cooldown_sec : 5) : '5';
    if (phraseEl) phraseEl.value = isEdit ? (existingRule.phrase || '') : '';
    if (holdEl) holdEl.value = isEdit ? String(existingRule.hold_ms || 0) : '0';
    if (macroEl) macroEl.value = isEdit ? (existingRule.macro_name || '') : '';
    if (stepsEl) stepsEl.value = isEdit && Array.isArray(existingRule.steps) ? JSON.stringify(existingRule.steps, null, 2) : '[{"action": "click", "button": "right", "hold_ms": 5800}]';
    if (goalEl) goalEl.value = isEdit ? (existingRule.goal || '') : '';
    if (speakEl) speakEl.value = isEdit ? (existingRule.speak_text || '') : '';
    if (labelEl) labelEl.value = isEdit ? (existingRule.action_label || '') : '';
    if (enChk) enChk.checked = isEdit ? Boolean(existingRule.enabled) : true;

    syncTriggerActionFields();
    modal.classList.remove('hidden');
    if (nameEl) nameEl.focus();
}

function closeTriggerModal() {
    const modal = document.getElementById('modal-edit-trigger');
    if (modal) modal.classList.add('hidden');
}

async function handleGenerateTrigger() {
    const aiInput = document.getElementById('input-trigger-ai-prompt');
    const btn = document.getElementById('btn-generate-trigger');
    const statusEl = document.getElementById('trigger-ai-status');
    const instruction = (aiInput?.value || '').trim();
    if (!instruction) {
        if (aiInput) aiInput.focus();
        return;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }
    if (statusEl) statusEl.textContent = 'Aether is drafting your When/If -> Then trigger...';
    try {
        const res = await window.pywebview.api.generate_game_trigger_from_text(activeGameId, instruction);
        if (res && res.success && res.trigger) {
            const t = res.trigger;
            const nameEl = document.getElementById('input-trigger-name');
            const evSel = document.getElementById('select-trigger-event');
            const customEv = document.getElementById('input-trigger-custom-event');
            const condEl = document.getElementById('input-trigger-condition');
            const typeEl = document.getElementById('select-trigger-action-type');
            const delayEl = document.getElementById('input-trigger-delay-ms');
            const cdEl = document.getElementById('input-trigger-cooldown-sec');
            const phraseEl = document.getElementById('input-trigger-phrase');
            const holdEl = document.getElementById('input-trigger-hold-ms');
            const macroEl = document.getElementById('input-trigger-macro-name');
            const stepsEl = document.getElementById('textarea-trigger-steps');
            const goalEl = document.getElementById('input-trigger-goal');
            const speakEl = document.getElementById('input-trigger-speak-text');
            const labelEl = document.getElementById('input-trigger-action-label');

            if (nameEl && t.name) nameEl.value = t.name;
            if (t.event && evSel) {
                const known = (currentGameTriggerEvents || []).some(e => e.event === t.event);
                evSel.value = known ? t.event : '__custom__';
                if (customEv) {
                    customEv.value = known ? '' : t.event;
                    customEv.classList.toggle('hidden', known);
                }
            }
            if (condEl && t.condition !== undefined) condEl.value = t.condition || '';
            if (typeEl && t.action_type) typeEl.value = t.action_type;
            if (delayEl && t.delay_ms != null) delayEl.value = String(t.delay_ms);
            if (cdEl && t.cooldown_sec != null) cdEl.value = String(t.cooldown_sec);
            if (phraseEl && t.phrase) phraseEl.value = t.phrase;
            if (holdEl && t.hold_ms != null) holdEl.value = String(t.hold_ms);
            if (macroEl && t.macro_name) macroEl.value = t.macro_name;
            if (stepsEl && Array.isArray(t.steps)) stepsEl.value = JSON.stringify(t.steps, null, 2);
            if (goalEl && t.goal) goalEl.value = t.goal;
            if (speakEl && t.speak_text) speakEl.value = t.speak_text;
            if (labelEl && t.action_label) labelEl.value = t.action_label;
            syncTriggerActionFields();
            if (statusEl) statusEl.textContent = 'Draft ready! Review or tweak the fields below, then click Save Trigger.';
        } else if (statusEl) {
            statusEl.textContent = (res && res.error) || 'Could not draft trigger.';
        }
    } catch (e) {
        console.error("generate_game_trigger_from_text error:", e);
        if (statusEl) statusEl.textContent = 'Error generating trigger.';
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '✨ Generate'; }
    }
}

async function handleConfirmSaveTrigger() {
    if (!activeGameId) return;
    const idEl = document.getElementById('input-trigger-id');
    const nameEl = document.getElementById('input-trigger-name');
    const evSel = document.getElementById('select-trigger-event');
    const customEv = document.getElementById('input-trigger-custom-event');
    const condEl = document.getElementById('input-trigger-condition');
    const typeEl = document.getElementById('select-trigger-action-type');
    const delayEl = document.getElementById('input-trigger-delay-ms');
    const cdEl = document.getElementById('input-trigger-cooldown-sec');
    const phraseEl = document.getElementById('input-trigger-phrase');
    const holdEl = document.getElementById('input-trigger-hold-ms');
    const macroEl = document.getElementById('input-trigger-macro-name');
    const stepsEl = document.getElementById('textarea-trigger-steps');
    const goalEl = document.getElementById('input-trigger-goal');
    const speakEl = document.getElementById('input-trigger-speak-text');
    const labelEl = document.getElementById('input-trigger-action-label');
    const enChk = document.getElementById('chk-trigger-enabled');
    const errEl = document.getElementById('trigger-modal-error');

    const name = (nameEl?.value || '').trim();
    const selVal = evSel ? evSel.value : '__custom__';
    const eventName = selVal === '__custom__' ? (customEv?.value || '').trim() : selVal;
    const evObj = (currentGameTriggerEvents || []).find(e => e.event === eventName);
    const actionType = typeEl ? typeEl.value : 'keybind';

    if (!name) {
        if (nameEl) { nameEl.style.borderColor = '#ef4444'; nameEl.focus(); }
        return;
    }
    if (!eventName) {
        if (errEl) { errEl.textContent = 'Please select or enter a telemetry event name.'; errEl.style.display = 'block'; }
        return;
    }

    let parsedSteps = null;
    if (actionType === 'key_sequence') {
        try {
            parsedSteps = JSON.parse(stepsEl?.value || '[]');
            if (!Array.isArray(parsedSteps) || parsedSteps.length === 0) {
                throw new Error('Steps must be a non-empty JSON array.');
            }
        } catch (parseErr) {
            if (errEl) { errEl.textContent = `Invalid steps JSON: ${parseErr.message}`; errEl.style.display = 'block'; }
            return;
        }
    }

    const rule = {
        id: (idEl?.value || '').trim(),
        name,
        event: eventName,
        event_label: evObj ? evObj.label : eventName,
        condition: (condEl?.value || '').trim(),
        action_type: actionType,
        action_label: (labelEl?.value || '').trim(),
        phrase: (phraseEl?.value || '').trim(),
        hold_ms: parseInt(holdEl?.value || '0', 10) || 0,
        macro_name: (macroEl?.value || '').trim(),
        steps: parsedSteps,
        goal: (goalEl?.value || '').trim(),
        speak_text: (speakEl?.value || '').trim(),
        delay_ms: parseInt(delayEl?.value || '0', 10) || 0,
        cooldown_sec: parseFloat(cdEl?.value || '5') || 0,
        enabled: Boolean(enChk ? enChk.checked : true),
    };

    try {
        const res = await window.pywebview.api.save_game_trigger(activeGameId, rule);
        if (res && res.success) {
            currentGameTriggersList = res.triggers || [];
            closeTriggerModal();
            renderGameTriggersTable(currentGameTriggersList);
        } else if (errEl) {
            errEl.textContent = (res && res.error) || 'Failed to save trigger.';
            errEl.style.display = 'block';
        }
    } catch (e) {
        console.error("save_game_trigger error:", e);
    }
}

async function handleScanGamePois(e) {
    if (!activeGameId || !window.pywebview?.api?.scan_game_pois) return;
    const btn = e ? e.currentTarget : document.getElementById('btn-scan-pois');
    const origText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '🔍 Scanning...'; }
    try {
        const res = await window.pywebview.api.scan_game_pois(activeGameId);
        if (res && res.success) {
            renderGamePoisTable(res.pois || []);
            const added = Number(res.added || 0);
            const updated = Number(res.updated || 0);
            const msg = (added > 0 || updated > 0)
                ? `POI scan complete: ${added} added, ${updated} updated.`
                : 'POI scan complete: all known POIs are already up to date.';
            if (window.aetherUI?.showToast) {
                window.aetherUI.showToast(msg);
            }
        } else {
            alert((res && res.error) || 'Failed to scan POIs.');
        }
    } catch (err) {
        console.error("scan_game_pois error:", err);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = origText; }
    }
}

function renderCopilotLog(entries) {
    const listEl = document.getElementById('copilot-log-list');
    const badgeEl = document.getElementById('badge-copilot-count');
    if (!listEl) return;

    const list = Array.isArray(entries) ? entries : [];
    if (badgeEl) badgeEl.textContent = list.length;

    if (list.length === 0) {
        listEl.innerHTML = `
            <div style="padding: 24px 12px; text-align: center; color: var(--text-dim); font-size: 0.8rem;">
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
        const cat = String(entry.category || 'intel').toLowerCase().replace(/[^a-z0-9_-]/g, '');
        const catClass = `cat-${cat}`;
        const locText = entry.location ? `📍 ${escapeGameHtml(entry.location)}` : '';
        const timeText = escapeGameHtml(entry.time_str || '');
        const entryId = String(entry.id || '');

        item.innerHTML = `
            <div class="copilot-entry-header">
                <div style="display: flex; align-items: center;">
                    <span class="copilot-entry-cat ${catClass}">${escapeGameHtml(cat)}</span>
                    ${locText ? `<span class="copilot-entry-loc">${locText}</span>` : ''}
                </div>
                <div class="copilot-entry-right" style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 0.68rem; color: var(--text-dim);">${timeText}</span>
                </div>
            </div>
            <div class="copilot-entry-summary">${escapeGameHtml(entry.summary || '')}</div>
        `;
        const rightContainer = item.querySelector('.copilot-entry-right');
        if (rightContainer) {
            const delBtn = document.createElement('button');
            delBtn.type = 'button';
            delBtn.className = 'copilot-entry-del';
            delBtn.title = 'Dismiss';
            delBtn.textContent = '✕';
            delBtn.onclick = () => deleteCopilotEntry(entryId);
            rightContainer.appendChild(delBtn);
        }
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
    list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-dim); font-size: 0.8rem;">Scanning running processes...</div>';
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
        list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-dim); font-size: 0.8rem;">No matching processes found.</div>';
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
                <span class="proc-item-title">🪟 ${escapeGameHtml(app.title || app.name)}</span>
                <span class="proc-item-exe">${escapeGameHtml(app.name)}</span>
            `;
            item.onclick = () => selectRunningProcess(app.name, app.title);
            list.appendChild(item);
        });
    }

    // 2. All running processes
    if (filteredProcs.length > 0) {
        const sectionHeader = document.createElement('div');
        sectionHeader.style.cssText = 'padding: 6px 10px; background: var(--bg-panel); font-size: 0.72rem; color: var(--text-muted); font-weight: 700;';
        sectionHeader.textContent = 'ALL RUNNING PROCESSES';
        list.appendChild(sectionHeader);

        filteredProcs.forEach(pname => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title" style="color: var(--text-secondary);">⚡ ${escapeGameHtml(pname)}</span>
                <span class="proc-item-exe">${escapeGameHtml(pname)}</span>
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
    // Games 3-Tab Sub-Navigation
    document.querySelectorAll('.game-subtab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchGameSubtab(btn.dataset.gameSubtab));
    });

    // Inline Game Display-Name Rename
    document.getElementById('btn-rename-game')?.addEventListener('click', openRenameGameInline);
    document.getElementById('btn-save-rename-game')?.addEventListener('click', confirmRenameGame);
    document.getElementById('btn-cancel-rename-game')?.addEventListener('click', closeRenameGameInline);
    document.getElementById('input-rename-game')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); confirmRenameGame(); }
        else if (e.key === 'Escape') { e.preventDefault(); closeRenameGameInline(); }
    });

    // Special Instructions autosave & Reset to Module Default
    document.getElementById('textarea-game-special-instructions')?.addEventListener('input', (e) => {
        if (!activeGameId || !window.pywebview?.api?.set_game_special_instructions) return;
        const targetGid = activeGameId;
        const val = e.target.value;
        const statusEl = document.getElementById('special-instructions-save-status');
        const resetBtn = document.getElementById('btn-reset-special-instructions');
        if (statusEl) statusEl.textContent = 'Saving...';
        clearTimeout(specialInstructionsDebounceTimer);
        specialInstructionsDebounceTimer = setTimeout(async () => {
            try {
                const res = await window.pywebview.api.set_game_special_instructions(targetGid, val);
                if (res && res.success && activeGameId === targetGid) {
                    if (statusEl) {
                        statusEl.textContent = 'Saved';
                        setTimeout(() => { if (statusEl && statusEl.textContent === 'Saved') statusEl.textContent = ''; }, 1800);
                    }
                    if (resetBtn) resetBtn.classList.toggle('hidden', !(res.module_default && res.is_custom));
                }
            } catch (err) {
                console.error("set_game_special_instructions error:", err);
                if (statusEl && activeGameId === targetGid) statusEl.textContent = 'Error saving';
            }
        }, 550);
    });
    document.getElementById('btn-reset-special-instructions')?.addEventListener('click', async () => {
        if (!activeGameId || !window.pywebview?.api?.set_game_special_instructions) return;
        try {
            const res = await window.pywebview.api.set_game_special_instructions(activeGameId, null);
            if (res && res.success) {
                const area = document.getElementById('textarea-game-special-instructions');
                const resetBtn = document.getElementById('btn-reset-special-instructions');
                if (area) area.value = res.text || '';
                if (resetBtn) resetBtn.classList.add('hidden');
            }
        } catch (err) {
            console.error("reset special_instructions error:", err);
        }
    });

    // Modal Add / Edit Event Trigger actions
    document.getElementById('btn-add-trigger')?.addEventListener('click', () => openTriggerModal(null));
    document.getElementById('btn-close-edit-trigger-modal')?.addEventListener('click', closeTriggerModal);
    document.getElementById('btn-cancel-edit-trigger')?.addEventListener('click', closeTriggerModal);
    document.getElementById('btn-confirm-save-trigger')?.addEventListener('click', handleConfirmSaveTrigger);
    document.getElementById('btn-generate-trigger')?.addEventListener('click', handleGenerateTrigger);
    document.getElementById('select-trigger-action-type')?.addEventListener('change', syncTriggerActionFields);
    document.getElementById('select-trigger-event')?.addEventListener('change', (e) => {
        const customEv = document.getElementById('input-trigger-custom-event');
        if (customEv) customEv.classList.toggle('hidden', e.target.value !== '__custom__');
    });
    document.getElementById('modal-edit-trigger')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-edit-trigger') closeTriggerModal();
    });

    // Companion Chit-Chat settings autosave
    const persistChitChat = async () => {
        if (!activeGameId || !window.pywebview?.api?.set_game_chitchat_settings) return;
        const freqEl = document.getElementById('select-game-chitchat-freq');
        const scrEl = document.getElementById('chk-game-chitchat-screen');
        const profEl = document.getElementById('chk-game-chitchat-profanity');
        const emoEl = document.getElementById('chk-game-chitchat-emotive');
        const jokesEl = document.getElementById('chk-game-chitchat-jokes');
        const factsEl = document.getElementById('chk-game-chitchat-facts');
        const relEl = document.getElementById('chk-game-chitchat-relevant');
        const cLocalEl = document.getElementById('chk-game-chat-local');
        const cSysEl = document.getElementById('chk-game-chat-system');
        const cNpcEl = document.getElementById('chk-game-chat-npc');
        const cSquadEl = document.getElementById('chk-game-chat-squadron');
        const cPlayerEl = document.getElementById('chk-game-chat-player');
        const cTransEl = document.getElementById('chk-game-chat-translate');
        const stEl = document.getElementById('chitchat-save-status');
        try {
            const res = await window.pywebview.api.set_game_chitchat_settings(
                activeGameId,
                freqEl ? freqEl.value : 'off',
                Boolean(scrEl && scrEl.checked),
                Boolean(profEl && profEl.checked),
                Boolean(emoEl && emoEl.checked),
                Boolean(jokesEl && jokesEl.checked),
                factsEl ? Boolean(factsEl.checked) : true,
                relEl ? Boolean(relEl.checked) : true,
                Boolean(cLocalEl && cLocalEl.checked),
                Boolean(cSysEl && cSysEl.checked),
                Boolean(cNpcEl && cNpcEl.checked),
                cSquadEl ? Boolean(cSquadEl.checked) : true,
                cPlayerEl ? Boolean(cPlayerEl.checked) : true,
                cTransEl ? Boolean(cTransEl.checked) : true,
            );
            if (res && res.success) {
                const prof = currentGamesData?.profiles?.[activeGameId];
                if (prof) {
                    prof.chitchat_frequency = res.frequency;
                    prof.chitchat_screen_peek = res.screen_peek;
                    prof.chitchat_allow_profanity = res.allow_profanity;
                    prof.chitchat_emotive_voice = res.emotive_voice;
                    prof.chitchat_topic_jokes = res.topic_jokes;
                    prof.chitchat_topic_facts = res.topic_facts;
                    prof.chitchat_topic_relevant = res.topic_relevant;
                    prof.chitchat_chat_local = res.chat_local;
                    prof.chitchat_chat_system = res.chat_system;
                    prof.chitchat_chat_npc = res.chat_npc;
                    prof.chitchat_chat_squadron = res.chat_squadron;
                    prof.chitchat_chat_player = res.chat_player;
                    prof.chitchat_chat_translate = res.chat_translate;
                }
                if (stEl) {
                    stEl.textContent = 'Saved';
                    setTimeout(() => { if (stEl && stEl.textContent === 'Saved') stEl.textContent = ''; }, 1800);
                }
            }
        } catch (err) {
            console.error("set_game_chitchat_settings error:", err);
        }
    };
    document.getElementById('select-game-chitchat-freq')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-screen')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-profanity')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-emotive')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-jokes')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-facts')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chitchat-relevant')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-local')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-system')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-npc')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-squadron')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-player')?.addEventListener('change', persistChitChat);
    document.getElementById('chk-game-chat-translate')?.addEventListener('change', persistChitChat);

    // Modal Add / Edit POI actions & Scan POIs
    document.getElementById('btn-scan-pois')?.addEventListener('click', handleScanGamePois);
    document.getElementById('btn-add-poi')?.addEventListener('click', () => openPoiModal(null));
    document.getElementById('btn-close-edit-poi-modal')?.addEventListener('click', closePoiModal);
    document.getElementById('btn-cancel-edit-poi')?.addEventListener('click', closePoiModal);
    document.getElementById('btn-confirm-save-poi')?.addEventListener('click', handleConfirmSavePoi);
    document.getElementById('modal-edit-poi')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-edit-poi') closePoiModal();
    });

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
    document.getElementById('input-filter-keybinds')?.addEventListener('input', () => {
        const prof = currentGamesData?.profiles?.[activeGameId];
        if (prof) renderKeybindsTable(prof);
    });

    // Modal Add / Edit Custom Macro actions
    document.getElementById('btn-add-macro')?.addEventListener('click', () => openMacroModal());
    document.getElementById('btn-close-edit-macro-modal')?.addEventListener('click', closeMacroModal);
    document.getElementById('btn-cancel-edit-macro')?.addEventListener('click', closeMacroModal);
    document.getElementById('btn-confirm-save-macro')?.addEventListener('click', handleConfirmSaveMacro);
    document.getElementById('btn-generate-macro-steps')?.addEventListener('click', handleGenerateMacroSteps);
    document.querySelectorAll('.btn-macro-snippet').forEach(btn => {
        btn.addEventListener('click', () => insertMacroStepSnippet(btn.dataset.snippet));
    });

    // Co-Pilot Vibe Preset & Free-Text Personality autosave
    let copilotVibeDebounce = null;
    const persistCopilotVibe = async () => {
        if (!activeGameId || !window.pywebview?.api?.set_game_copilot_vibe) return;
        const vibeEl = document.getElementById('select-game-copilot-vibe');
        const textEl = document.getElementById('textarea-game-copilot-personality');
        const statusEl = document.getElementById('copilot-vibe-save-status');
        const vibe = vibeEl ? vibeEl.value : 'witty';
        const customText = textEl ? textEl.value : '';
        try {
            const res = await window.pywebview.api.set_game_copilot_vibe(activeGameId, vibe, customText);
            if (res && res.success) {
                const prof = currentGamesData?.profiles?.[activeGameId];
                if (prof) {
                    prof.copilot_vibe = res.vibe;
                    prof.copilot_personality = res.custom_personality;
                }
                if (statusEl) {
                    statusEl.textContent = 'Saved';
                    setTimeout(() => { if (statusEl && statusEl.textContent === 'Saved') statusEl.textContent = ''; }, 1800);
                }
            }
        } catch (err) {
            console.error("set_game_copilot_vibe error:", err);
        }
    };
    document.getElementById('select-game-copilot-vibe')?.addEventListener('change', persistCopilotVibe);
    document.getElementById('textarea-game-copilot-personality')?.addEventListener('input', () => {
        if (copilotVibeDebounce) clearTimeout(copilotVibeDebounce);
        copilotVibeDebounce = setTimeout(persistCopilotVibe, 600);
    });

    // Close modals on clicking overlay backdrop
    document.getElementById('modal-add-game')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-add-game') closeAddGameModal();
    });
    document.getElementById('modal-add-keybind')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-add-keybind') closeAddKeybindModal();
    });
    document.getElementById('modal-edit-macro')?.addEventListener('click', (e) => {
        if (e.target.id === 'modal-edit-macro') closeMacroModal();
    });

    // Close modals on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeAddGameModal();
            closeAddKeybindModal();
            closeMacroModal();
            closePoiModal();
            closeTriggerModal();
            closeRenameGameInline();
        }
    });

    // Clear Co-Pilot Log
    document.getElementById('btn-clear-copilot-log')?.addEventListener('click', handleClearCopilotLog);

    // Delete Game button
    document.getElementById('btn-delete-game')?.addEventListener('click', promptDeleteGame);

    // Binds Synchronization (Auto-Detection) & Telemetry Toggle
    const fpsBtnInit = document.getElementById('fps-toggle-btn'); if (fpsBtnInit) { fpsBtnInit.onclick = handleToggleFpsMonitoring; }
    const telBtnInit = document.getElementById('btn-toggle-telemetry'); if (telBtnInit) { telBtnInit.onclick = handleToggleTelemetry; }

    document.getElementById('btn-scan-keybinds')?.addEventListener('click', async () => {
        if (!activeGameId) return;
        const btn = document.getElementById('btn-scan-keybinds');
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

    // Per-Game Multiple Main Characters Toggle
    document.getElementById('chk-game-multiple-characters')?.addEventListener('change', async (e) => {
        if (!activeGameId) return;
        const checked = Boolean(e.target.checked);
        try {
            if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.set_game_multiple_characters === 'function') {
                const res = await window.pywebview.api.set_game_multiple_characters(activeGameId, checked);
                if (res && res.success && currentGamesData?.profiles?.[activeGameId]) {
                    currentGamesData.profiles[activeGameId].multiple_main_characters = Boolean(res.multiple_main_characters);
                }
            }
        } catch (err) {
            console.error("Failed saving multiple main characters flag:", err);
        }
    });

    // Per-Game Callsign Autosave
    document.getElementById('input-game-callsign')?.addEventListener('input', (e) => {
        if (!activeGameId) return;
        clearTimeout(callsignDebounceTimer);
        const targetGameId = activeGameId;
        const val = e.target.value;
        callsignDebounceTimer = setTimeout(async () => {
            try {
                if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.set_game_callsign === 'function') {
                    const res = await window.pywebview.api.set_game_callsign(targetGameId, val);
                    if (res && res.success && currentGamesData?.profiles?.[targetGameId]) {
                        currentGamesData.profiles[targetGameId].callsign = res.callsign;
                    }
                }
            } catch (err) {
                console.error("Failed saving game callsign:", err);
            }
        }, 450);
    });

    // 3rd-Party Co-Pilot Alert Suppression Autosave
    document.getElementById('input-game-suppress-alerts')?.addEventListener('input', (e) => {
        if (!activeGameId) return;
        clearTimeout(suppressAlertsDebounceTimer);
        const targetGameId = activeGameId;
        const rawVal = e.target.value;
        suppressAlertsDebounceTimer = setTimeout(async () => {
            try {
                if (window.pywebview?.api?.set_game_suppress_alerts) {
                    const res = await window.pywebview.api.set_game_suppress_alerts(targetGameId, rawVal);
                    if (res && res.success && currentGamesData?.profiles?.[targetGameId]) {
                        currentGamesData.profiles[targetGameId].suppress_alerts_when_running = res.suppress_alerts_when_running || [];
                        if (Array.isArray(res.companion_apps)) {
                            currentGamesData.profiles[targetGameId].companion_apps = res.companion_apps;
                            refreshCompanionAppsStatus(targetGameId);
                        }
                    }
                }
            } catch (err) {
                console.error("Failed saving alert suppression processes:", err);
            }
        }, 500);
    });

    // 3rd-Party Companion App Modal Triggers & Controls
    document.getElementById('btn-add-companion-app')?.addEventListener('click', openAddCompanionAppModal);
    document.getElementById('btn-close-add-companion-modal')?.addEventListener('click', closeAddCompanionAppModal);
    document.getElementById('btn-cancel-add-companion')?.addEventListener('click', closeAddCompanionAppModal);
    document.getElementById('btn-confirm-add-companion')?.addEventListener('click', confirmAddCompanionApp);
    document.getElementById('btn-browse-companion-exe')?.addEventListener('click', handleBrowseCompanionExe);
    document.getElementById('btn-toggle-companion-running-procs')?.addEventListener('click', toggleCompanionRunningProcesses);
    document.getElementById('btn-refresh-companion-running-procs')?.addEventListener('click', loadCompanionRunningProcesses);
    document.getElementById('input-filter-companion-running-proc')?.addEventListener('input', (e) => {
        if (cachedRunningProcesses) {
            renderCompanionRunningProcessesList(cachedRunningProcesses, e.target.value);
        }
    });
    document.getElementById('modal-add-companion-app')?.addEventListener('click', (e) => {
        if (e.target && e.target.id === 'modal-add-companion-app') closeAddCompanionAppModal();
    });

    // Tier 2 Community Python Extension Import Button
    document.getElementById('btn-import-game-extension')?.addEventListener('click', async () => {
        if (!window.pywebview?.api?.import_game_extension_dialog) return;
        try {
            const res = await window.pywebview.api.import_game_extension_dialog(activeGameId || '');
            if (res && res.cancelled) return;
            if (res && res.success) {
                await refreshGameExtensions();
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(res.message || `Imported Python extension for ${res.module_id}`);
                }
            } else {
                alert((res && res.error) || 'Failed to import Python extension.');
            }
        } catch (err) {
            console.error("Import game extension error:", err);
        }
    });

    // Copy AI Module Authoring Prompt Button (paste into any LLM -> save .aethermodule.json -> Import Module)
    document.getElementById('btn-copy-module-ai-prompt')?.addEventListener('click', async (e) => {
        if (!window.pywebview?.api?.get_game_module_ai_prompt) return;
        const btn = e.currentTarget;
        const origText = btn ? btn.textContent : '';
        try {
            const res = await window.pywebview.api.get_game_module_ai_prompt(activeGameId || '');
            if (!res || !res.success || !res.prompt) {
                alert((res && res.error) || 'Could not generate AI module prompt.');
                return;
            }
            let copied = false;
            if (navigator.clipboard && navigator.clipboard.writeText) {
                try {
                    await navigator.clipboard.writeText(res.prompt);
                    copied = true;
                } catch (_) {
                    copied = false;
                }
            }
            if (!copied) {
                const ta = document.createElement('textarea');
                ta.value = res.prompt;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                copied = document.execCommand('copy');
                document.body.removeChild(ta);
            }
            if (btn) {
                btn.textContent = '✅ Prompt Copied!';
                setTimeout(() => { btn.textContent = origText; }, 2200);
            }
            if (window.aetherUI?.showToast) {
                window.aetherUI.showToast(
                    `Copied AI Module Builder prompt for ${res.game_name}. Paste into Gemini/ChatGPT/Claude, save as .aethermodule.json, then click Import Module.`
                );
            }
        } catch (err) {
            console.error("Copy AI module prompt error:", err);
        }
    });

    // Game Module Import / Export / Auto-Build Buttons
    document.getElementById('btn-import-game-module')?.addEventListener('click', async () => {
        if (!window.pywebview?.api?.import_game_module_dialog) return;
        try {
            const res = await window.pywebview.api.import_game_module_dialog();
            if (res && res.cancelled) return;
            if (res && res.success) {
                await loadGamesUI();
                if (res.module_id) await switchActiveGame(res.module_id);
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(`Imported game module: ${res.display_name || res.module_id}`);
                }
            } else {
                alert((res && res.error) || 'Failed to import game module.');
            }
        } catch (err) {
            console.error("Import game module error:", err);
        }
    });

    document.getElementById('btn-export-game-module')?.addEventListener('click', async () => {
        if (!activeGameId || !window.pywebview?.api?.export_game_module_dialog) return;
        try {
            const res = await window.pywebview.api.export_game_module_dialog(activeGameId);
            if (res && res.cancelled) return;
            if (res && res.success) {
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(`Exported module to ${res.exported_path}`);
                }
            } else {
                alert((res && res.error) || 'Failed to export game module.');
            }
        } catch (err) {
            console.error("Export game module error:", err);
        }
    });

    document.getElementById('btn-calibrate-hud-bars')?.addEventListener('click', async (e) => {
        if (!activeGameId || !window.pywebview?.api?.calibrate_game_hud_bars) return;
        const btn = e.currentTarget;
        const origText = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = '🎯 Focusing Game (3s)...';
        }
        if (window.aetherUI?.showToast) {
            window.aetherUI.showToast('Switching to game — unpause if needed. Capturing live HUD in 3 seconds...');
        }
        try {
            const res = await window.pywebview.api.calibrate_game_hud_bars(activeGameId);
            if (res && res.success) {
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(res.message || 'HUD bars calibrated.');
                }
            } else {
                alert((res && res.error) || 'Could not calibrate HUD bars.');
            }
        } catch (err) {
            console.error("HUD bar calibration error:", err);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = origText;
            }
        }
    });

    document.getElementById('btn-autobuild-game-module')?.addEventListener('click', async (e) => {
        if (!activeGameId || !window.pywebview?.api?.auto_build_game_module) return;
        const btn = e.currentTarget;
        const origText = btn ? btn.textContent : '';
        // Warn before overwriting an existing local module or shadowing a built-in bundled module
        if (window.pywebview?.api?.get_game_module_status) {
            try {
                const st = await window.pywebview.api.get_game_module_status(activeGameId);
                if (st && st.has_module) {
                    const label = st.display_name || activeGameId;
                    const msg = st.is_user_module
                        ? `A local Game Module already exists for "${label}" (${st.source_path}).\n\nRunning Auto-Build will merge and overwrite that file. Continue?`
                        : `"${label}" already has a built-in Aether Game Module.\n\nRunning Auto-Build will save a local override in data/game_modules/ that shadows future built-in updates until deleted. Continue?`;
                    if (!confirm(msg)) return;
                }
            } catch (_) {
                // Non-fatal: proceed if status check unavailable
            }
        }
        if (btn) {
            btn.disabled = true;
            btn.textContent = '✨ Building (Auto-Starting Aether)...';
        }
        try {
            const res = await window.pywebview.api.auto_build_game_module(activeGameId);
            if (res && res.success) {
                await loadGamesUI();
                const summary = (res.notes && res.notes.length) ? res.notes.join(' ') : 'Module drafted and assimilated.';
                if (window.aetherUI?.showToast) {
                    window.aetherUI.showToast(`Auto-Built ${res.display_name}: ${summary}`);
                }
            } else {
                alert((res && res.error) || 'Auto-Build module failed.');
            }
        } catch (err) {
            console.error("Auto-build game module error:", err);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = origText;
            }
        }
    });

    // Scratchpad Autosave
    document.getElementById('game-scratchpad-input')?.addEventListener('input', (e) => {
        if (!activeGameId) return;
        const targetGameId = activeGameId;
        const text = e.target.value;
        const status = document.getElementById('scratchpad-save-status');
        if (status) status.textContent = 'Saving...';
        clearTimeout(scratchpadDebounceTimer);

        scratchpadDebounceTimer = setTimeout(async () => {
            try {
                await window.pywebview.api.update_game_scratchpad(targetGameId, text);
                if (currentGamesData && currentGamesData.profiles && currentGamesData.profiles[targetGameId]) {
                    currentGamesData.profiles[targetGameId].scratchpad_raw = text;
                }
                if (status && activeGameId === targetGameId) status.textContent = 'All changes saved locally';
            } catch (err) {
                console.error("scratchpad save error:", err);
                if (status && activeGameId === targetGameId) status.textContent = 'Error saving changes';
            }
        }, 600);
    });
}

let telemetryPollInterval = null;

function startTelemetryPolling() {
    if (telemetryPollInterval) clearInterval(telemetryPollInterval);
    telemetryPollInterval = setInterval(async () => {
        const gamesPane = document.getElementById('tab-games');
        if (gamesPane && gamesPane.classList.contains('hidden')) return;
        if (activeGameId && window.pywebview && window.pywebview.api) {
            try {
                const telemetry = await window.pywebview.api.get_game_telemetry();
                if (telemetry && telemetry.active) {
                    updateTelemetryHUD(telemetry);
                }
                if (telemetry) {
                    if (telemetry.copilot_entries) {
                        const profile = currentGamesData?.profiles?.[activeGameId];
                        if (profile) profile.copilot_log = telemetry.copilot_entries;
                        renderCopilotLog(telemetry.copilot_entries);
                    }
                    if (Array.isArray(telemetry.pois)) {
                        renderGamePoisTable(telemetry.pois);
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
                // Refresh live running indicators for linked 3rd-party companion apps
                refreshCompanionAppsStatus(activeGameId);
            } catch (e) {
                // Ignore background poll errors
            }
        }
    }, 2500);
}

// --- 3rd-Party Companion Apps UI & Modal Controller ---

async function refreshCompanionAppsStatus(gameId) {
    if (!gameId || !window.pywebview?.api?.get_game_companion_apps) return;
    try {
        const res = await window.pywebview.api.get_game_companion_apps(gameId);
        if (res && res.success && gameId === activeGameId) {
            const apps = Array.isArray(res.apps) ? res.apps : [];
            if (currentGamesData?.profiles?.[gameId]) {
                currentGamesData.profiles[gameId].companion_apps = apps;
            }
            renderCompanionAppsList(apps);
        }
    } catch (_) {
        // Ignore transient poll errors
    }
}

function renderCompanionAppsList(apps) {
    const container = document.getElementById('game-companion-apps-list');
    if (!container) return;
    const list = Array.isArray(apps) ? apps : [];

    if (list.length === 0) {
        container.innerHTML = `
            <div style="padding: 14px 12px; border: 1px dashed rgba(148, 163, 184, 0.2); border-radius: 8px; text-align: center; color: var(--text-muted); font-size: 0.82rem;">
                No 3rd-party companion programs linked yet. Click <strong>+ Add 3rd-Party Program</strong> to link external maps, telemetry loggers, overlays, or helper apps.
            </div>
        `;
        return;
    }

    container.innerHTML = '';
    list.forEach(app => {
        const row = document.createElement('div');
        row.className = 'companion-app-row';
        row.style.cssText = 'display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 12px; border-radius: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(148, 163, 184, 0.16);';

        const isRunning = Boolean(app.running);
        const exeLabel = app.executable ? `<code>${escapeGameHtml(app.executable)}</code>` : '';
        const purposeLabel = app.purpose ? `<span style="color: var(--text-muted); font-size: 0.78rem;">— ${escapeGameHtml(app.purpose)}</span>` : '';
        const hasPath = Boolean(app.path);

        const leftDiv = document.createElement('div');
        leftDiv.style.cssText = 'display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 220px;';
        leftDiv.innerHTML = `
            <span class="badge-status ${isRunning ? 'badge-online' : 'badge-offline'}" style="font-size: 0.68rem; padding: 2px 7px;">
                ${isRunning ? 'RUNNING' : 'IDLE'}
            </span>
            <strong style="color: var(--text-main); font-size: 0.88rem;">${escapeGameHtml(app.name || app.id)}</strong>
            ${exeLabel}
            ${purposeLabel}
        `;

        const rightDiv = document.createElement('div');
        rightDiv.style.cssText = 'display: flex; flex-wrap: wrap; align-items: center; gap: 10px;';

        // Prompt to Launch toggle
        const promptLabel = document.createElement('label');
        promptLabel.style.cssText = 'display: inline-flex; align-items: center; gap: 5px; font-size: 0.76rem; color: var(--text-secondary); cursor: pointer;';
        promptLabel.title = 'Ask to start this program when the game launches if it is not running';
        const promptChk = document.createElement('input');
        promptChk.type = 'checkbox';
        promptChk.checked = app.prompt_to_launch !== false;
        promptChk.onchange = () => toggleCompanionAppFlag(app.id, 'prompt_to_launch', promptChk.checked);
        promptLabel.appendChild(promptChk);
        promptLabel.appendChild(document.createTextNode('Prompt on Launch'));

        // Mute Alerts toggle
        const muteLabel = document.createElement('label');
        muteLabel.style.cssText = 'display: inline-flex; align-items: center; gap: 5px; font-size: 0.76rem; color: var(--text-secondary); cursor: pointer;';
        muteLabel.title = 'Mute duplicate Aether ship/HUD voice alerts while this program is running';
        const muteChk = document.createElement('input');
        muteChk.type = 'checkbox';
        muteChk.checked = Boolean(app.mute_aether_alerts);
        muteChk.onchange = () => toggleCompanionAppFlag(app.id, 'mute_aether_alerts', muteChk.checked);
        muteLabel.appendChild(muteChk);
        muteLabel.appendChild(document.createTextNode('Mute Alerts'));

        // Quiet When Running toggle
        const quietLabel = document.createElement('label');
        quietLabel.style.cssText = 'display: inline-flex; align-items: center; gap: 5px; font-size: 0.76rem; color: var(--text-secondary); cursor: pointer;';
        quietLabel.title = 'Keep Aether quiet/passive (no unsolicited alerts or chatter) while this program is running';
        const quietChk = document.createElement('input');
        quietChk.type = 'checkbox';
        quietChk.checked = Boolean(app.quiet_when_running);
        quietChk.onchange = () => toggleCompanionAppFlag(app.id, 'quiet_when_running', quietChk.checked);
        quietLabel.appendChild(quietChk);
        quietLabel.appendChild(document.createTextNode('Quiet When Running'));

        // Launch button
        const launchBtn = document.createElement('button');
        launchBtn.type = 'button';
        launchBtn.className = 'btn-secondary-sm';
        launchBtn.textContent = isRunning ? 'Active' : '▶ Launch';
        launchBtn.disabled = isRunning;
        launchBtn.title = hasPath ? `Launch ${app.path}` : `Launch ${app.executable || app.name}`;
        launchBtn.onclick = () => launchCompanionApp(app.id);

        // Remove button
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn-danger-sm';
        delBtn.textContent = 'Delete';
        delBtn.onclick = () => removeCompanionApp(app.id, app.name || app.id);

        rightDiv.appendChild(promptLabel);
        rightDiv.appendChild(muteLabel);
        rightDiv.appendChild(quietLabel);
        rightDiv.appendChild(launchBtn);
        rightDiv.appendChild(delBtn);

        row.appendChild(leftDiv);
        row.appendChild(rightDiv);
        container.appendChild(row);
    });
}

async function toggleCompanionAppFlag(appId, field, value) {
    if (!activeGameId || !window.pywebview?.api?.update_game_companion_app) return;
    try {
        const res = await window.pywebview.api.update_game_companion_app(activeGameId, appId, { [field]: Boolean(value) });
        if (res && res.success) {
            const prof = currentGamesData?.profiles?.[activeGameId];
            if (prof) {
                if (Array.isArray(res.companion_apps)) prof.companion_apps = res.companion_apps;
                if (Array.isArray(res.suppress_alerts_when_running)) {
                    prof.suppress_alerts_when_running = res.suppress_alerts_when_running;
                    const suppressInput = document.getElementById('input-game-suppress-alerts');
                    if (suppressInput) suppressInput.value = res.suppress_alerts_when_running.join(', ');
                }
            }
            if (Array.isArray(res.companion_apps)) renderCompanionAppsList(res.companion_apps);
        }
    } catch (err) {
        console.error("Failed updating companion app toggle:", err);
    }
}

async function launchCompanionApp(appId) {
    if (!activeGameId || !window.pywebview?.api?.launch_game_companion_app) return;
    try {
        const res = await window.pywebview.api.launch_game_companion_app(activeGameId, appId);
        if (res && res.success) {
            if (window.aetherUI?.showToast) {
                window.aetherUI.showToast(res.message || 'Launched companion program.');
            }
            setTimeout(() => refreshCompanionAppsStatus(activeGameId), 1200);
        } else {
            alert((res && res.error) || 'Could not launch companion program.');
        }
    } catch (err) {
        console.error("Failed launching companion app:", err);
    }
}

async function removeCompanionApp(appId, appName) {
    if (!activeGameId || !window.pywebview?.api?.remove_game_companion_app) return;
    if (!confirm(`Remove 3rd-party program "${appName}" from this game?`)) return;
    try {
        const res = await window.pywebview.api.remove_game_companion_app(activeGameId, appId);
        if (res && res.success) {
            const prof = currentGamesData?.profiles?.[activeGameId];
            if (prof) {
                prof.companion_apps = res.companion_apps || [];
                if (Array.isArray(res.suppress_alerts_when_running)) {
                    prof.suppress_alerts_when_running = res.suppress_alerts_when_running;
                    const suppressInput = document.getElementById('input-game-suppress-alerts');
                    if (suppressInput) suppressInput.value = res.suppress_alerts_when_running.join(', ');
                }
            }
            renderCompanionAppsList(res.companion_apps || []);
        }
    } catch (err) {
        console.error("Failed removing companion app:", err);
    }
}

function openAddCompanionAppModal() {
    if (!activeGameId) return;
    const modal = document.getElementById('modal-add-companion-app');
    if (!modal) return;
    const nameInput = document.getElementById('input-companion-app-name');
    const procInput = document.getElementById('input-companion-app-proc');
    const purposeInput = document.getElementById('input-companion-app-purpose');
    const promptChk = document.getElementById('chk-companion-prompt-launch');
    const muteChk = document.getElementById('chk-companion-mute-alerts');
    const quietChk = document.getElementById('chk-companion-quiet-running');
    const procPanel = document.getElementById('panel-companion-running-processes');
    if (nameInput) {
        nameInput.value = '';
        nameInput.style.borderColor = '';
    }
    if (procInput) {
        procInput.value = '';
        procInput.style.borderColor = '';
    }
    if (purposeInput) purposeInput.value = '';
    if (promptChk) promptChk.checked = true;
    if (muteChk) muteChk.checked = false;
    if (quietChk) quietChk.checked = false;
    if (procPanel) procPanel.classList.add('hidden');
    modal.classList.remove('hidden');
    if (nameInput) nameInput.focus();
}

function closeAddCompanionAppModal() {
    const modal = document.getElementById('modal-add-companion-app');
    if (modal) modal.classList.add('hidden');
}

async function handleBrowseCompanionExe() {
    try {
        const fn = window.pywebview?.api?.browse_companion_executable || window.pywebview?.api?.browse_game_executable;
        if (!fn) return;
        const res = await fn();
        if (res && res.success) {
            const procInput = document.getElementById('input-companion-app-proc');
            const nameInput = document.getElementById('input-companion-app-name');
            if (procInput) {
                procInput.value = res.file_path || res.file_name || '';
                procInput.style.borderColor = '#10b981';
                setTimeout(() => { if (procInput) procInput.style.borderColor = ''; }, 2500);
                procInput.scrollLeft = procInput.scrollWidth;
            }
            if (nameInput && !nameInput.value.trim()) {
                nameInput.value = res.suggested_name || '';
            }
        }
    } catch (err) {
        console.error("Failed browsing companion executable:", err);
    }
}

async function toggleCompanionRunningProcesses() {
    const panel = document.getElementById('panel-companion-running-processes');
    if (!panel) return;
    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        await loadCompanionRunningProcesses();
    } else {
        panel.classList.add('hidden');
    }
}

async function loadCompanionRunningProcesses() {
    const list = document.getElementById('list-companion-running-processes');
    if (!list) return;
    list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-dim); font-size: 0.8rem;">Scanning running processes...</div>';
    try {
        const res = await window.pywebview.api.get_running_processes();
        cachedRunningProcesses = res;
        const filterVal = document.getElementById('input-filter-companion-running-proc')?.value || '';
        renderCompanionRunningProcessesList(res, filterVal);
    } catch (err) {
        list.innerHTML = '<div style="padding: 12px; text-align: center; color: #f87171; font-size: 0.8rem;">Could not load running processes.</div>';
    }
}

function renderCompanionRunningProcessesList(data, filterText = '') {
    const list = document.getElementById('list-companion-running-processes');
    if (!list) return;
    list.innerHTML = '';

    const q = (filterText || '').toLowerCase().trim();
    const apps = (data && data.applications) || [];
    const procs = (data && data.processes) || [];
    const pathMap = (data && data.process_paths) || {};

    const filteredApps = apps.filter(a => !q || a.name.toLowerCase().includes(q) || (a.title && a.title.toLowerCase().includes(q)));
    const filteredProcs = procs.filter(p => !q || p.toLowerCase().includes(q));

    if (filteredApps.length === 0 && filteredProcs.length === 0) {
        list.innerHTML = '<div style="padding: 12px; text-align: center; color: var(--text-dim); font-size: 0.8rem;">No matching processes found.</div>';
        return;
    }

    if (filteredApps.length > 0) {
        const header = document.createElement('div');
        header.style.cssText = 'padding: 6px 10px; background: rgba(59, 130, 246, 0.08); font-size: 0.72rem; color: #60a5fa; font-weight: 700;';
        header.textContent = 'RUNNING WINDOWED APPLICATIONS';
        list.appendChild(header);

        filteredApps.forEach(app => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title">🪟 ${escapeGameHtml(app.title || app.name)}</span>
                <span class="proc-item-exe">${escapeGameHtml(app.name)}</span>
            `;
            const targetPath = app.exe_path || pathMap[app.name.toLowerCase()] || app.name;
            item.onclick = () => selectCompanionRunningProcess(targetPath, app.name, app.title);
            list.appendChild(item);
        });
    }

    if (filteredProcs.length > 0) {
        const header = document.createElement('div');
        header.style.cssText = 'padding: 6px 10px; background: var(--bg-panel); font-size: 0.72rem; color: var(--text-muted); font-weight: 700;';
        header.textContent = 'ALL RUNNING PROCESSES';
        list.appendChild(header);

        filteredProcs.forEach(pname => {
            const item = document.createElement('div');
            item.className = 'proc-item';
            item.innerHTML = `
                <span class="proc-item-title" style="color: var(--text-secondary);">⚡ ${escapeGameHtml(pname)}</span>
                <span class="proc-item-exe">${escapeGameHtml(pname)}</span>
            `;
            const targetPath = pathMap[pname.toLowerCase()] || pname;
            item.onclick = () => selectCompanionRunningProcess(targetPath, pname, '');
            list.appendChild(item);
        });
    }
}

function selectCompanionRunningProcess(targetPath, exeName, windowTitle = '') {
    const procInput = document.getElementById('input-companion-app-proc');
    const nameInput = document.getElementById('input-companion-app-name');
    const panel = document.getElementById('panel-companion-running-processes');
    if (procInput) {
        procInput.value = targetPath || exeName;
        procInput.scrollLeft = procInput.scrollWidth;
    }
    if (nameInput && !nameInput.value.trim()) {
        const cleaned = (windowTitle || exeName.replace(/\.exe$/i, '')).split(' - ')[0].trim();
        nameInput.value = cleaned;
    }
    if (panel) panel.classList.add('hidden');
}

async function confirmAddCompanionApp() {
    if (!activeGameId || !window.pywebview?.api?.add_game_companion_app) return;
    const nameInput = document.getElementById('input-companion-app-name');
    const procInput = document.getElementById('input-companion-app-proc');
    const purposeInput = document.getElementById('input-companion-app-purpose');
    const promptChk = document.getElementById('chk-companion-prompt-launch');
    const muteChk = document.getElementById('chk-companion-mute-alerts');
    const quietChk = document.getElementById('chk-companion-quiet-running');

    const name = (nameInput?.value || '').trim();
    const procOrPath = (procInput?.value || '').trim();
    const purpose = (purposeInput?.value || '').trim();
    const promptToLaunch = promptChk ? Boolean(promptChk.checked) : true;
    const muteAlerts = muteChk ? Boolean(muteChk.checked) : false;
    const quietWhenRunning = quietChk ? Boolean(quietChk.checked) : false;

    if (!name && !procOrPath) {
        if (nameInput) {
            nameInput.style.borderColor = '#ef4444';
            nameInput.focus();
        }
        return;
    }

    try {
        const res = await window.pywebview.api.add_game_companion_app(
            activeGameId,
            name,
            procOrPath,
            purpose,
            muteAlerts,
            promptToLaunch,
            quietWhenRunning
        );
        if (res && res.success) {
            const prof = currentGamesData?.profiles?.[activeGameId];
            if (prof) {
                prof.companion_apps = res.companion_apps || [];
                if (Array.isArray(res.suppress_alerts_when_running)) {
                    prof.suppress_alerts_when_running = res.suppress_alerts_when_running;
                    const suppressInput = document.getElementById('input-game-suppress-alerts');
                    if (suppressInput) suppressInput.value = res.suppress_alerts_when_running.join(', ');
                }
            }
            renderCompanionAppsList(res.companion_apps || []);
            closeAddCompanionAppModal();
            if (window.aetherUI?.showToast) {
                window.aetherUI.showToast(`Linked 3rd-party program: ${res.app?.name || name}`);
            }
        } else {
            alert((res && res.error) || 'Failed to add 3rd-party program.');
        }
    } catch (err) {
        console.error("Failed adding companion app:", err);
    }
}

async function refreshGameExtensions() {
    const container = document.getElementById('game-extensions-list');
    const badge = document.getElementById('badge-extensions-count');
    if (!container) return;
    if (!window.pywebview?.api?.list_game_extensions) {
        container.innerHTML = '';
        return;
    }
    try {
        const res = await window.pywebview.api.list_game_extensions();
        const exts = (res && res.success && Array.isArray(res.extensions)) ? res.extensions : [];
        if (badge) badge.textContent = String(exts.length);
        renderGameExtensionsList(exts);
    } catch (err) {
        console.error("Failed loading game extensions:", err);
    }
}

function renderGameExtensionsList(exts) {
    const container = document.getElementById('game-extensions-list');
    if (!container) return;
    const list = Array.isArray(exts) ? exts : [];

    if (list.length === 0) {
        container.innerHTML = `
            <div style="padding: 14px 12px; border: 1px dashed rgba(148, 163, 184, 0.2); border-radius: 8px; text-align: center; color: var(--text-muted); font-size: 0.82rem;">
                No homebrew Python extensions installed. Click <strong>+ Import .py Extension</strong> to load a custom <code>GameExtension</code> script for this game.
            </div>
        `;
        return;
    }

    container.innerHTML = '';
    list.forEach(ext => {
        const row = document.createElement('div');
        row.style.cssText = 'display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border-radius: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(148, 163, 184, 0.16);';

        const statusBadge = ext.trusted
            ? `<span class="badge-status badge-online" style="font-size: 0.68rem; padding: 2px 7px;">TRUSTED</span>`
            : (ext.hash_changed
                ? `<span class="badge-status" style="font-size: 0.68rem; padding: 2px 7px; background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.45);">MODIFIED — RE-TRUST</span>`
                : `<span class="badge-status badge-offline" style="font-size: 0.68rem; padding: 2px 7px;">UNTRUSTED (DISABLED)</span>`);

        const shortSha = ext.sha256 ? String(ext.sha256).slice(0, 12) : '—';
        const leftDiv = document.createElement('div');
        leftDiv.style.cssText = 'display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 220px;';
        leftDiv.innerHTML = `
            ${statusBadge}
            <strong style="color: var(--text-main); font-size: 0.88rem;">${escapeGameHtml(ext.module_id)}</strong>
            <code style="font-size: 0.74rem; opacity: 0.85;">SHA-256: ${escapeGameHtml(shortSha)}…</code>
        `;

        const rightDiv = document.createElement('div');
        rightDiv.style.cssText = 'display: flex; align-items: center; gap: 8px;';

        if (!ext.trusted) {
            const btnTrust = document.createElement('button');
            btnTrust.type = 'button';
            btnTrust.className = 'btn-primary-sm btn-primary-blue';
            btnTrust.textContent = ext.hash_changed ? 'Re-Trust & Enable' : 'Trust & Enable';
            btnTrust.onclick = async () => {
                const ok = confirm(
                    `Enable Python extension for '${ext.module_id}'?\n\n` +
                    `SHA-256: ${ext.sha256}\n\n` +
                    `Only trust .py extensions from sources you review and trust.`
                );
                if (!ok) return;
                const r = await window.pywebview.api.trust_game_extension(ext.module_id);
                if (r && r.success) {
                    await refreshGameExtensions();
                    if (window.aetherUI?.showToast) window.aetherUI.showToast(`Trusted & enabled extension '${ext.module_id}'.`);
                } else {
                    alert((r && r.error) || 'Failed to trust extension.');
                }
            };
            rightDiv.appendChild(btnTrust);
        } else {
            const btnRevoke = document.createElement('button');
            btnRevoke.type = 'button';
            btnRevoke.className = 'btn-secondary-sm';
            btnRevoke.textContent = 'Revoke Trust';
            btnRevoke.onclick = async () => {
                const r = await window.pywebview.api.revoke_game_extension(ext.module_id);
                if (r && r.success) {
                    await refreshGameExtensions();
                    if (window.aetherUI?.showToast) window.aetherUI.showToast(`Revoked trust for extension '${ext.module_id}'.`);
                }
            };
            rightDiv.appendChild(btnRevoke);
        }

        const btnDelete = document.createElement('button');
        btnDelete.type = 'button';
        btnDelete.className = 'btn-danger-sm';
        btnDelete.textContent = 'Delete';
        btnDelete.onclick = async () => {
            if (!confirm(`Delete Python extension for '${ext.module_id}' from disk?`)) return;
            const r = await window.pywebview.api.delete_game_extension(ext.module_id);
            if (r && r.success) {
                await refreshGameExtensions();
                if (window.aetherUI?.showToast) window.aetherUI.showToast(`Deleted extension '${ext.module_id}'.`);
            }
        };
        rightDiv.appendChild(btnDelete);

        row.appendChild(leftDiv);
        row.appendChild(rightDiv);
        container.appendChild(row);
    });
}

async function refreshGameElevationWarning(gameId) {
    const banner = document.getElementById('game-uipi-warning-banner');
    if (!banner) return;
    if (!gameId || !window.pywebview?.api?.check_game_elevation_status) {
        banner.classList.add('hidden');
        banner.textContent = '';
        return;
    }
    try {
        const res = await window.pywebview.api.check_game_elevation_status(gameId);
        if (res && res.uipi_blocked && res.warning) {
            banner.textContent = `⚠️ ${res.warning}`;
            banner.classList.remove('hidden');
        } else {
            banner.classList.add('hidden');
            banner.textContent = '';
        }
    } catch (_) {
        banner.classList.add('hidden');
    }
}

function updateTelemetryHUD(data) {
    const banner = document.getElementById('game-process-indicator');
    if (banner && data && data.active) {
        if (data.status_summary) {
            banner.textContent = data.status_summary;
        } else if (data.star_system) {
            banner.textContent = `${data.star_system} | ${data.docked ? 'Docked (' + data.station + ')' : (data.supercruise ? 'Supercruise' : 'Normal Space')}`;
        } else {
            banner.textContent = 'Telemetry Active';
        }
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
window.escapeGameHtml = escapeGameHtml;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGamesTabEvents);
} else {
    initGamesTabEvents();
}
