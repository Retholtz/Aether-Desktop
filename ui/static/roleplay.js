/**
 * Aether Desktop - Universal Tabletop Roleplaying Engine (Role Play Tab)
 * System-agnostic campaign manager, combat initiative ladder, dynamic party vitals roster,
 * DM document library (rulesets, campaigns, character sheets), and cryptographic dice audit ledger.
 */

let ttrpgData = {
    active_campaign: "iron_reach",
    campaigns: {},
    active_campaign_meta: null,
    state: null,
    projects: {}
};
let activeDiceRollMode = "normal";
let campaignModalEditMode = false;
let editingCharacterId = null;
let currentPreviewDocFilename = null;
let isTogglingAdventure = false;
let isAdventureActive = false;

const SYSTEM_PRESET_LABELS = {
    "dnd_5e": "D&D 5E",
    "pathfinder_2e": "PATHFINDER 2E",
    "cyberpunk": "CYBERPUNK",
    "call_of_cthulhu": "CALL OF CTHULHU",
    "heroic_d20": "HEROIC D20",
    "investigative_d100": "SKILL PERCENTILE D100",
    "cybernetic_action": "CYBERNETIC ACTION",
    "narrative_clocks": "NARRATIVE DICE & CLOCKS",
    "sandbox_custom": "À LA CARTE / SANDBOX"
};

const RULESET_FAMILY_MAP = {
    "dnd_5e": "dnd_5e",
    "dnd": "dnd_5e",
    "5e": "dnd_5e",
    "d&d": "dnd_5e",
    "d&d_5e": "dnd_5e",
    "heroic_d20": "dnd_5e",
    "heroic_fantasy": "dnd_5e",
    "dungeons_and_dragons": "dnd_5e",
    "pathfinder_2e": "pathfinder_2e",
    "pathfinder": "pathfinder_2e",
    "pf2e": "pathfinder_2e",
    "cyberpunk": "cyberpunk",
    "cyberpunk_red": "cyberpunk",
    "cybernetic_action": "cyberpunk",
    "call_of_cthulhu": "call_of_cthulhu",
    "coc": "call_of_cthulhu",
    "investigative_d100": "call_of_cthulhu",
    "space_opera": "space_opera",
    "narrative_clocks": "narrative_clocks",
    "sandbox_custom": "sandbox_custom"
};

function areRulesetsCompatible(r1, r2) {
    if (!r1 || !r2) return true;
    const s1 = String(r1).toLowerCase().trim().replace(/[\s-]/g, "_");
    const s2 = String(r2).toLowerCase().trim().replace(/[\s-]/g, "_");
    if (s1 === s2) return true;
    const fam1 = RULESET_FAMILY_MAP[s1] || s1;
    const fam2 = RULESET_FAMILY_MAP[s2] || s2;
    return fam1 === fam2;
}

function getRulesetDisplayLabel(r) {
    if (!r) return "D&D 5E";
    const s = String(r).toLowerCase().trim().replace(/[\s-]/g, "_");
    const fam = RULESET_FAMILY_MAP[s] || s;
    const map = {
        "dnd_5e": "D&D 5E",
        "pathfinder_2e": "Pathfinder 2E",
        "cyberpunk": "Cyberpunk",
        "call_of_cthulhu": "Call of Cthulhu",
        "space_opera": "Space Opera",
        "narrative_clocks": "Narrative Clocks",
        "sandbox_custom": "Custom Sandbox"
    };
    return map[fam] || SYSTEM_PRESET_LABELS[r] || r.replace(/_/g, " ").toUpperCase();
}

const DOC_CATEGORY_LABELS = {
    "ruleset": "RULESET",
    "campaign": "CAMPAIGN",
    "character_sheet": "CHAR SHEET",
    "notes": "NOTES"
};

function escapeRpHtml(str) {
    return String(str == null ? "" : str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function escapeRpJs(str) {
    return String(str == null ? "" : str)
        .replace(/\\/g, "\\\\")
        .replace(/'/g, "\\'")
        .replace(/"/g, '\\"');
}

function showAetherConfirm(message, title = "Confirm Action", confirmBtnText = "Confirm", isDestructive = true) {
    return new Promise((resolve) => {
        const modal = document.getElementById("modal-rp-confirm");
        const titleEl = document.getElementById("modal-rp-confirm-title");
        const msgEl = document.getElementById("modal-rp-confirm-message");
        const okBtn = document.getElementById("btn-ok-rp-confirm");
        const cancelBtn = document.getElementById("btn-cancel-rp-confirm");
        const closeBtn = document.getElementById("btn-close-rp-confirm");
        const iconEl = document.getElementById("modal-rp-confirm-icon");

        if (!modal || !okBtn || !cancelBtn) {
            resolve(window.confirm(message));
            return;
        }

        if (titleEl) titleEl.textContent = title;
        if (msgEl) msgEl.textContent = message;
        if (cancelBtn) cancelBtn.style.display = "inline-block";
        if (okBtn) {
            okBtn.textContent = confirmBtnText;
            if (isDestructive) {
                okBtn.style.background = "#ef4444";
                okBtn.style.borderColor = "#dc2626";
            } else {
                okBtn.style.background = "#38bdf8";
                okBtn.style.borderColor = "#0284c7";
            }
        }
        if (iconEl) iconEl.textContent = isDestructive ? "🗑️" : "⚠️";

        function cleanup(result) {
            modal.classList.add("hidden");
            okBtn.removeEventListener("click", onOk);
            cancelBtn.removeEventListener("click", onCancel);
            if (closeBtn) closeBtn.removeEventListener("click", onCancel);
            resolve(result);
        }

        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }

        okBtn.addEventListener("click", onOk);
        cancelBtn.addEventListener("click", onCancel);
        if (closeBtn) closeBtn.addEventListener("click", onCancel);

        modal.classList.remove("hidden");
    });
}
window.showAetherConfirm = showAetherConfirm;

function showAetherAlert(message, title = "Notice") {
    return new Promise((resolve) => {
        const modal = document.getElementById("modal-rp-confirm");
        const titleEl = document.getElementById("modal-rp-confirm-title");
        const msgEl = document.getElementById("modal-rp-confirm-message");
        const okBtn = document.getElementById("btn-ok-rp-confirm");
        const cancelBtn = document.getElementById("btn-cancel-rp-confirm");
        const closeBtn = document.getElementById("btn-close-rp-confirm");
        const iconEl = document.getElementById("modal-rp-confirm-icon");

        if (!modal || !okBtn) {
            console.warn("[Aether]", message);
            resolve();
            return;
        }

        if (titleEl) titleEl.textContent = title;
        if (msgEl) msgEl.textContent = message;
        if (cancelBtn) cancelBtn.style.display = "none";
        if (okBtn) {
            okBtn.textContent = "OK";
            okBtn.style.background = "#38bdf8";
            okBtn.style.borderColor = "#0284c7";
        }
        if (iconEl) iconEl.textContent = "ℹ️";

        function cleanup() {
            modal.classList.add("hidden");
            if (cancelBtn) cancelBtn.style.display = "inline-block";
            okBtn.removeEventListener("click", onOk);
            if (closeBtn) closeBtn.removeEventListener("click", onOk);
            resolve();
        }

        function onOk() { cleanup(); }

        okBtn.addEventListener("click", onOk);
        if (closeBtn) closeBtn.addEventListener("click", onOk);

        modal.classList.remove("hidden");
    });
}
window.showAetherAlert = showAetherAlert;
window.alert = function(msg) {
    showAetherAlert(String(msg));
};

async function loadRolePlayUI() {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_ttrpg_data) {
            const data = await window.pywebview.api.get_ttrpg_data();
            if (data) {
                ttrpgData = data;
                if (typeof data.is_adventure_active === "boolean") {
                    isAdventureActive = data.is_adventure_active;
                }
            }
        }
    } catch (e) {
        console.error("[RolePlay] Failed loading TTRPG data:", e);
    }
    renderRolePlayWorkspace();
}

function renderRolePlayWorkspace() {
    renderCampaignHeader();
    renderCampaignDocuments();
    renderInitiativeLadder();
    renderPartyRoster();
    renderSessionJournal();
    renderDiceModeToggle();
}

function renderDiceModeToggle() {
    const campaigns = ttrpgData.campaigns || {};
    const activeId = ttrpgData.active_campaign;
    const meta = (activeId && campaigns[activeId]) ? campaigns[activeId] : ttrpgData.active_campaign_meta;
    const mode = (meta && meta.dice_mode) ? meta.dice_mode : (ttrpgData.dice_mode || 'digital');
    updateDiceModeUI(mode);
}

function updateDiceModeUI(mode) {
    const isPhysical = mode === 'physical';
    const btnDigital = document.getElementById('btn-dice-source-digital');
    const btnPhysical = document.getElementById('btn-dice-source-physical');
    const indicator = document.getElementById('dice-source-indicator');
    const indicatorText = document.getElementById('dice-source-indicator-text');

    if (btnDigital) {
        btnDigital.classList.toggle('active', !isPhysical);
    }
    if (btnPhysical) {
        btnPhysical.classList.toggle('active', isPhysical);
    }
    if (indicator && indicatorText) {
        indicator.className = 'dice-source-indicator ' + (isPhysical ? 'physical' : 'digital');
        indicatorText.innerHTML = isPhysical 
            ? '<span>🎲</span> <span><strong>Physical Dice</strong>: Players roll at table &amp; speak results</span>' 
            : '<span>⚡</span> <span><strong>Aether Rolls</strong>: Digital dice rolled automatically</span>';
    }
}
window.updateDiceModeUI = updateDiceModeUI;
window.handleRoleplayDiceModeEvent = function(data) {
    if (data && data.dice_mode) {
        updateDiceModeUI(data.dice_mode);
    }
};

function renderCampaignHeader() {
    const titleEl = document.getElementById("active-campaign-title");
    const tagEl = document.getElementById("active-campaign-system-tag");
    const selectEl = document.getElementById("rp-campaign-select");
    const overviewEl = document.getElementById("active-campaign-overview");
    const toneEl = document.getElementById("active-campaign-tone");
    const editBtn = document.getElementById("edit-campaign-btn");
    const deleteBtn = document.getElementById("delete-campaign-btn");

    const campaigns = ttrpgData.campaigns || {};
    const activeId = ttrpgData.active_campaign;
    const meta = (activeId && campaigns[activeId]) ? campaigns[activeId] : ttrpgData.active_campaign_meta;

    if (selectEl) {
        selectEl.innerHTML = "";
        const ids = Object.keys(campaigns);
        if (ids.length === 0) {
            const opt = document.createElement("option");
            opt.value = "";
            opt.textContent = "No Campaigns";
            selectEl.appendChild(opt);
        } else {
            ids.forEach(cid => {
                const c = campaigns[cid];
                const opt = document.createElement("option");
                opt.value = cid;
                opt.textContent = c.title || cid;
                if (cid === activeId) opt.selected = true;
                selectEl.appendChild(opt);
            });
        }
    }

    if (meta) {
        if (titleEl) titleEl.textContent = meta.title || activeId;
        if (tagEl) {
            const presetKey = (meta.system_preset || "heroic_d20").toLowerCase();
            tagEl.textContent = SYSTEM_PRESET_LABELS[presetKey] || presetKey.replace(/_/g, " ").toUpperCase();
        }
        if (overviewEl) {
            overviewEl.textContent = meta.setting_overview || "";
            overviewEl.style.display = meta.setting_overview ? "block" : "none";
        }
        if (toneEl) {
            toneEl.textContent = meta.tone ? `Tone: ${meta.tone}` : "";
            toneEl.style.display = meta.tone ? "block" : "none";
        }
        if (editBtn) editBtn.disabled = false;
        if (deleteBtn) deleteBtn.disabled = false;

        const storyTitle = document.getElementById("rp-story-title");
        if (storyTitle) storyTitle.textContent = `⚔️ ${meta.title || activeId} Chronicle`;

        const bottomInput = document.getElementById("chatTextInput");
        if (bottomInput) bottomInput.placeholder = `State your action to the Game Master (${meta.title || activeId})...`;

        if (activeId && window._loadedCampaignStoryId !== activeId) {
            window._loadedCampaignStoryId = activeId;
            loadCampaignStoryHistory(activeId);
        }
    } else {
        if (titleEl) titleEl.textContent = "No Active Campaign";
        if (tagEl) tagEl.textContent = "SANDBOX";
        if (overviewEl) overviewEl.style.display = "none";
        if (toneEl) toneEl.style.display = "none";
        if (editBtn) editBtn.disabled = true;
        if (deleteBtn) deleteBtn.disabled = true;
    }

    // Start / Resume Campaign Button & Difficulty Badge
    const diffBadge = document.getElementById("rp-active-difficulty-badge");

    if (diffBadge) {
        const diff = ((meta && meta.difficulty) || "helpful").toLowerCase();
        const diffMap = {
            "helpful": "Helpful DM",
            "easy": "Easy DM",
            "medium": "Medium DM",
            "hard": "Hard DM"
        };
        diffBadge.textContent = diffMap[diff] || (diff.charAt(0).toUpperCase() + diff.slice(1) + " DM");
        diffBadge.className = `rp-badge diff-${diff}`;
    }

    if (!isTogglingAdventure) {
        const uiApp = window.aetherUI || window.app;
        const isRunning = Boolean(uiApp && uiApp.isAssistantRunning);
        updateAdventureButtonState(isRunning && isAdventureActive);
    }
}

function updateAdventureButtonState(active) {
    if (isTogglingAdventure && active === undefined) return;
    const btn = document.getElementById("btn-start-resume-campaign");
    const label = document.getElementById("lbl-start-resume-text");
    const icon = btn ? btn.querySelector(".adventure-btn-icon") : null;
    const hint = document.getElementById("rp-start-resume-hint");
    if (!btn || !label) return;

    if (active !== undefined) {
        isAdventureActive = Boolean(active);
    }

    if (isAdventureActive) {
        btn.className = "rp-adventure-btn stop";
        if (icon) icon.textContent = "🛑";
        label.textContent = "STOP ADVENTURE";
        if (hint) hint.textContent = "Adventure session is active (Target Speaker Filter paused for party play). Click to stop.";
    } else {
        btn.className = "rp-adventure-btn start";
        if (icon) icon.textContent = "▶";
        const state = ttrpgData.state || {};
        const hasRecapOrJournal = Boolean(state.latest_recap || (state.journal && state.journal.length > 0));
        label.textContent = hasRecapOrJournal ? "RESUME ADVENTURE" : "START ADVENTURE";
        if (hint) {
            if (hasRecapOrJournal) {
                const loc = state.current_location || "where you left off";
                hint.textContent = `Pick up at ${loc}. Aether will recap your last turn and prompt your party!`;
            } else {
                hint.textContent = "Begin your tabletop adventure. Aether will narrate the opening scene and guide your party!";
            }
        }
    }
}

function renderCampaignDocuments() {
    const listEl = document.getElementById("rp-documents-list");
    if (!listEl) return;

    const state = ttrpgData.state || {};
    const docsObj = state.documents || {};
    const docNames = Object.keys(docsObj);

    listEl.innerHTML = "";
    if (docNames.length === 0) {
        listEl.innerHTML = `
            <div style="text-align: center; color: #64748b; font-size: 11px; padding: 6px 4px;">
                No adventure modules, rulesets, or notes loaded yet.
            </div>
        `;
        return;
    }

    docNames.forEach(fname => {
        const doc = docsObj[fname] || { filename: fname, category: "ruleset" };
        const catKey = (doc.category || "ruleset").toLowerCase();
        const badgeLabel = DOC_CATEGORY_LABELS[catKey] || catKey.toUpperCase();
        const tokens = Number(doc.token_estimate || 0);

        const row = document.createElement("div");
        row.className = "rp-doc-item";
        row.innerHTML = `
            <div class="rp-doc-item-info" title="Click to preview ${escapeRpHtml(fname)}" onclick="handlePreviewRpDocument('${escapeRpHtml(fname)}')">
                <div class="rp-doc-filename">📄 ${escapeRpHtml(fname)}</div>
                <div class="rp-doc-submeta">
                    <span class="rp-doc-badge">${escapeRpHtml(badgeLabel)}</span>
                    <span>~${tokens.toLocaleString()} tok</span>
                </div>
            </div>
            <div style="display: flex; align-items: center; gap: 4px;">
                <button type="button" class="vital-step-btn" title="Have Aether DM review this document" onclick="handleReviewSingleRpDocument('${escapeRpHtml(fname)}', '${escapeRpHtml(catKey)}')">🧙‍♂️</button>
                <button type="button" class="vital-step-btn" title="Remove document" onclick="handleDeleteRpDocument('${escapeRpHtml(fname)}')">✕</button>
            </div>
        `;
        listEl.appendChild(row);
    });
}

function renderInitiativeLadder() {
    const roundEl = document.getElementById("round-counter");
    const ladderEl = document.getElementById("initiative-ladder");
    if (!ladderEl) return;

    const state = ttrpgData.state || {};
    if (roundEl) {
        roundEl.textContent = String(state.current_round || 1);
    }

    const order = Array.isArray(state.initiative_order) ? state.initiative_order : [];
    const activeIdx = Number(state.active_turn_index || 0);

    ladderEl.innerHTML = "";
    if (order.length === 0) {
        ladderEl.innerHTML = `
            <div style="padding: 14px 8px; text-align: center; color: #64748b; font-size: 12px;">
                No combatants on the ladder.<br>Add combatants below to track turn order.
            </div>
        `;
        return;
    }

    order.forEach((item, idx) => {
        const row = document.createElement("div");
        const isActive = idx === activeIdx;
        row.className = `ladder-item${isActive ? " active" : ""}`;
        const badgeIcon = item.is_player ? "🛡️" : "⚔️";
        row.innerHTML = `
            <div style="display: flex; align-items: center; gap: 6px; overflow: hidden;">
                <span>${isActive ? "▶" : badgeIcon}</span>
                <span style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${escapeRpHtml(item.name)}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 6px;">
                <span class="ladder-roll-pill">${Number(item.roll || 0)}</span>
                <button type="button" class="vital-step-btn" title="Remove from initiative" onclick="handleRemoveInitiative('${escapeRpHtml(item.name)}')">✕</button>
            </div>
        `;
        ladderEl.appendChild(row);
    });
}

function extractCharacterFeatures(ch) {
    if (!ch) return [];
    const candidates = [
        ch.features_and_traits,
        ch.traits_and_abilities,
        ch.features_traits,
        ch.features,
        ch.tagged_traits,
        ch.cyberware,
        ch.native_system_data?.features_and_traits,
        ch.native_system_data?.features_traits,
        ch.native_system_data?.cyberware,
        ch.universal_core?.features_and_traits,
        ch.universal_core?.tagged_traits,
        ch.universal_core?.traits
    ];
    let list = [];
    for (const cand of candidates) {
        if (!cand) continue;
        if (typeof cand === "string" && cand.trim().length > 0) {
            list = cand;
            break;
        }
        if (Array.isArray(cand) && cand.length > 0) {
            list = cand;
            break;
        }
    }
    if (typeof list === "string") {
        return list.split(/\n+|;\s*|,\s*(?![^()]*\))/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
    }
    if (Array.isArray(list)) {
        return list.map(f => {
            if (typeof f === "string") return f.trim();
            if (f && typeof f === "object") {
                if (f.name && f.description && f.description !== f.name) return `${f.name}: ${f.description}`;
                return f.name || f.description || "";
            }
            return String(f || "").trim();
        }).filter(Boolean);
    }
    return [];
}

function extractCharacterInventory(ch) {
    if (!ch) return [];
    const candidates = [
        ch.inventory,
        ch.live_inventory,
        ch.native_system_data?.inventory,
        ch.universal_core?.tagged_inventory,
        ch.universal_core?.inventory
    ];
    for (const cand of candidates) {
        if (Array.isArray(cand) && cand.length > 0) {
            return cand.map(i => typeof i === "string" ? i.trim() : (i?.name || String(i || ""))).filter(Boolean);
        }
        if (typeof cand === "string" && cand.trim().length > 0) {
            return cand.split(/[\n,]+/).map(i => i.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
        }
    }
    return [];
}

function extractCharacterSpells(ch) {
    if (!ch) return [];
    const candidates = [
        ch.spells,
        ch.spells_list,
        ch.native_system_data?.spells,
        ch.universal_core?.spells
    ];
    for (const cand of candidates) {
        if (Array.isArray(cand) && cand.length > 0) {
            return cand.map(s => typeof s === "string" ? s.trim() : (s?.name || String(s || ""))).filter(Boolean);
        }
        if (typeof cand === "string" && cand.trim().length > 0) {
            return cand.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
        }
    }
    return [];
}

function renderPartyRoster() {
    const gridEl = document.getElementById("party-roster-grid");
    if (!gridEl) return;

    const state = ttrpgData.state || {};
    const charsObj = state.characters || {};
    const charIds = Object.keys(charsObj);

    gridEl.innerHTML = "";
    if (charIds.length === 0) {
        gridEl.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; padding: 48px 20px; color: #64748b;">
                <div style="font-size: 2.2rem; margin-bottom: 10px;">🎲</div>
                <div style="font-size: 0.95rem; color: #cbd5e1; font-weight: 600; margin-bottom: 4px;">No Characters in Party Roster</div>
                <div style="font-size: 0.82rem;">Click <strong>+ Add Character</strong> above or upload a <strong>Character Sheet</strong> in the DM Library for Aether to import.</div>
            </div>
        `;
        return;
    }

    charIds.forEach(cid => {
        const ch = charsObj[cid];
        const card = document.createElement("div");
        const charIdAttr = ch.id || cid;
        const isCaller = Boolean(state.party_caller_id && (state.party_caller_id === cid || state.party_caller_id === ch.id));
        const isFocused = Boolean(state.focused_speaker_id && (state.focused_speaker_id === cid || state.focused_speaker_id === ch.id));

        card.className = "character-card" + (isFocused ? " active-speaker" : "");
        card.setAttribute("data-char-id", charIdAttr);
        card.id = `card-${charIdAttr}`;

        const vitals = ch.vitals || {};
        const hp = vitals.health || { current: 20, max: 20, label: "Health" };
        const guard = vitals.guard || { current: 12, max: 12, label: "Armor Defense" };
        const hpPct = hp.max > 0 ? Math.max(0, Math.min(100, Math.round((hp.current / hp.max) * 100))) : 0;

        // Build custom meters HTML
        const customMeters = ch.custom_meters || {};
        let customMetersHtml = "";
        Object.keys(customMeters).forEach(mKey => {
            const m = customMeters[mKey];
            const pct = m.max > 0 ? Math.max(0, Math.min(100, Math.round((m.current / m.max) * 100))) : 0;
            customMetersHtml += `
                <div class="vital-row">
                    <div class="vital-labels">
                        <span>${escapeRpHtml(m.label || mKey)}</span>
                        <div class="vital-controls-inline">
                            <button type="button" class="vital-step-btn" onclick="handleModifyCharacterStat('${escapeRpHtml(ch.name)}', '${escapeRpHtml(mKey)}', -1, true)">-</button>
                            <span>${Number(m.current)} / ${Number(m.max)}</span>
                            <button type="button" class="vital-step-btn" onclick="handleModifyCharacterStat('${escapeRpHtml(ch.name)}', '${escapeRpHtml(mKey)}', 1, true)">+</button>
                        </div>
                    </div>
                    <div class="vital-progress-bg">
                        <div class="vital-fill-custom" style="width: ${pct}%;"></div>
                    </div>
                </div>
            `;
        });

        // Build attributes strip HTML (Canonically adapted for ruleset)
        const attrs = ch.attributes || {};
        const rLow = String(ch.ruleset || ch.native_system || "").toLowerCase();
        let displayKeys = [];
        let isDndChar = false;
        if (rLow.includes("cyber") || (attrs.REF !== undefined && attrs.TECH !== undefined)) {
            displayKeys = ["INT", "REF", "DEX", "TECH", "COOL", "WILL", "LUCK", "MOVE", "BODY", "EMP"];
            isDndChar = false;
        } else if (rLow.includes("cthulhu") || attrs.SAN !== undefined) {
            displayKeys = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU", "SAN"].filter(k => attrs[k] !== undefined);
            isDndChar = false;
        } else if (rLow.includes("dnd") || rLow.includes("pathfinder") || attrs.Strength !== undefined) {
            displayKeys = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"];
            isDndChar = true;
        } else {
            displayKeys = ["Might", "Agility", "Vigor", "Intellect", "Presence"].filter(k => attrs[k] !== undefined);
            if (displayKeys.length === 0) displayKeys = Object.keys(attrs);
            isDndChar = false;
        }

        let attrsHtml = "";
        if (displayKeys.length > 0) {
            attrsHtml = `<div class="attributes-strip">` + displayKeys.map(k => {
                const val = Number(attrs[k] ?? 10);
                const mod = isDndChar ? Math.floor((val - 10) / 2) : null;
                const modStr = mod !== null ? ` <span style="font-size: 8.5px; color: ${mod >= 0 ? '#38bdf8' : '#f87171'}; font-weight: 700;">(${mod >= 0 ? '+' + mod : mod})</span>` : "";
                return `
                    <div class="attr-cell" title="${escapeRpHtml(k)}: ${val}${mod !== null ? ' (Mod: ' + (mod >= 0 ? '+' + mod : mod) + ')' : ''}">
                        <span class="attr-cell-label">${escapeRpHtml(k.slice(0, 3).toUpperCase())}</span>
                        <span class="attr-cell-val">${val}${modStr}</span>
                    </div>
                `;
            }).join("") + `</div>`;
        }

        // Build conditions HTML
        const conditions = Array.isArray(ch.conditions) ? ch.conditions : [];
        const condBadges = conditions.map(cond => `
            <span class="condition-badge" title="Click to clear condition" onclick="handleToggleCondition('${escapeRpHtml(ch.name)}', '${escapeRpHtml(cond)}', false)">
                ${escapeRpHtml(cond)} ✕
            </span>
        `).join("");

        // Build Weapons Shelf HTML
        const wpns = Array.isArray(ch.weapons) ? ch.weapons : [];
        const wpnsHtml = `
            <div class="char-weapons-shelf" style="margin-top: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(100, 116, 139, 0.2); border-radius: 6px; padding: 6px 8px;">
                <div style="font-size: 10px; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; display: flex; justify-content: space-between; align-items: center;">
                    <span>⚔️ WEAPONS &amp; ATTACKS</span>
                    <button type="button" class="cyber-btn-small" style="padding: 1px 7px; font-size: 9.5px; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35);" onclick="handleOpenAddWeaponModal('${escapeRpJs(ch.name)}')">+ Add Weapon</button>
                </div>
                ${wpns.length > 0 ? wpns.map(w => `
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 3px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.04);">
                        <div style="font-size: 11px;">
                            <strong style="color: #f1f5f9;">${escapeRpHtml(w.name)}</strong>
                            <span style="color: #94a3b8; font-size: 10px; margin-left: 4px;">${escapeRpHtml(w.atk_bonus || "+0")} (${escapeRpHtml(w.damage || "")})</span>
                            ${w.notes ? `<span style="font-size: 9.5px; color: #64748b; margin-left: 4px;">[${escapeRpHtml(w.notes)}]</span>` : ""}
                        </div>
                        <div style="display: flex; gap: 4px; align-items: center;">
                            <button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px;" title="Roll Attack Check" onclick="handleRollWeaponAttack('${escapeRpJs(ch.name)}', '${escapeRpJs(w.name)}', '${escapeRpJs(w.atk_bonus || "+0")}')">🎲 Atk</button>
                            <button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px;" title="Roll Damage" onclick="handleRollWeaponDamage('${escapeRpJs(ch.name)}', '${escapeRpJs(w.name)}', '${escapeRpJs(w.damage || "1d4")}')">💥 Dmg</button>
                            <button type="button" class="vital-step-btn" style="font-size: 9px; padding: 0 4px; color: #f87171;" title="Remove Weapon" onclick="handleDeleteWeapon('${escapeRpJs(ch.name)}', '${escapeRpJs(w.name)}')">✕</button>
                        </div>
                    </div>
                `).join("") : `<div style="font-size: 10.5px; color: #64748b; font-style: italic; padding: 2px 0;">No weapons equipped.</div>`}
            </div>
        `;

        // Build Inventory Shelf HTML
        const inv = Array.isArray(ch.inventory) ? ch.inventory : [];
        const invHtml = `
            <div class="char-inventory-shelf" style="margin-top: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(100, 116, 139, 0.2); border-radius: 6px; padding: 6px 8px;">
                <div style="font-size: 10px; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center;">
                    <span>🎒 INVENTORY &amp; GEAR</span>
                    <div style="display: flex; gap: 6px; align-items: center;">
                        <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600;">${inv.length} ITEMS</span>
                        <button type="button" class="cyber-btn-small" style="padding: 1px 7px; font-size: 9.5px; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35);" onclick="handleOpenAddInventoryModal('${escapeRpJs(ch.name)}')">+ Add Items</button>
                    </div>
                </div>
                ${inv.length > 0 ? `
                    <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                        ${inv.map(item => `
                            <span class="rp-gear-badge" style="background: rgba(30, 41, 59, 0.85); border: 1px solid rgba(100, 116, 139, 0.35); border-radius: 4px; padding: 2px 7px; font-size: 10px; color: #cbd5e1; display: inline-flex; align-items: center; gap: 4px;">
                                ${escapeRpHtml(item)}
                                <span style="cursor: pointer; color: #94a3b8; font-size: 9px; margin-left: 2px;" title="Remove Item" onclick="handleDeleteInventoryItem('${escapeRpHtml(ch.name)}', '${escapeRpHtml(item)}')">✕</span>
                            </span>
                        `).join("")}
                    </div>
                ` : `<div style="font-size: 10.5px; color: #64748b; font-style: italic;">Inventory empty.</div>`}
            </div>
        `;

        // Build Spells & Cantrips Shelf HTML
        const spells = Array.isArray(ch.spells) ? ch.spells : [];
        const isCaster = (ch.spellcasting_ability && ch.spellcasting_ability !== "none") || spells.length > 0;
        let spellsHtml = "";
        if (isCaster || spells.length > 0) {
            spellsHtml = `
                <div class="char-spells-shelf" style="margin-top: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(168, 85, 247, 0.25); border-radius: 6px; padding: 6px 8px;">
                    <div style="font-size: 10px; font-weight: 700; color: #c084fc; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center;">
                        <span>✨ SPELLS &amp; CANTRIPS</span>
                        <div style="display: flex; gap: 6px; align-items: center;">
                            <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600;">${spells.length} SPELLS</span>
                            <button type="button" class="cyber-btn-small" style="padding: 1px 7px; font-size: 9.5px; background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.35);" onclick="handleOpenAddSpellModal('${escapeRpJs(ch.name)}')">+ Add Spell</button>
                        </div>
                    </div>
                    ${spells.length > 0 ? spells.map(s => {
                        const sName = typeof s === "object" ? (s.name || "Spell") : String(s);
                        const sLvl = typeof s === "object" ? (s.level || "") : "";
                        const sAtk = typeof s === "object" ? (s.atk_bonus || s.save_dc || "") : "";
                        const sDmg = typeof s === "object" ? (s.damage || "") : "";
                        const sRange = typeof s === "object" ? (s.range || "") : "";
                        const sNotes = typeof s === "object" ? (s.notes || "") : "";

                        // Parse potential damage formula from text if s is a string (e.g. "Fire Bolt (1d10)")
                        const dmgMatch = sDmg || (sName.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i) ? sName.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i)[0] : "");

                        return `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 3px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.04);">
                                <div style="font-size: 11px;">
                                    <strong style="color: #f1f5f9;">${escapeRpHtml(sName)}</strong>
                                    ${sLvl ? `<span style="font-size: 9px; color: #c084fc; background: rgba(168, 85, 247, 0.15); padding: 1px 4px; border-radius: 3px; margin-left: 4px;">${escapeRpHtml(sLvl)}</span>` : ""}
                                    ${sAtk ? `<span style="color: #38bdf8; font-size: 10px; margin-left: 4px;">${escapeRpHtml(sAtk)}</span>` : ""}
                                    ${dmgMatch ? `<span style="color: #f87171; font-size: 10px; margin-left: 4px;">(${escapeRpHtml(dmgMatch)})</span>` : ""}
                                    ${sRange ? `<span style="font-size: 9.5px; color: #94a3b8; margin-left: 4px;">📍 ${escapeRpHtml(sRange)}</span>` : ""}
                                    ${sNotes ? `<span style="font-size: 9px; color: #64748b; margin-left: 4px;">[${escapeRpHtml(sNotes)}]</span>` : ""}
                                </div>
                                <div style="display: flex; gap: 4px; align-items: center;">
                                    <button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px; color: #c084fc; border-color: rgba(168, 85, 247, 0.35);" title="Cast Spell / Announce" onclick="handleCastSpell('${escapeRpJs(ch.name)}', '${escapeRpJs(sName)}', '${escapeRpJs(sRange)}', '${escapeRpJs(sNotes)}')">✨ Cast</button>
                                    ${sAtk ? `<button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px;" title="Roll Spell Attack" onclick="handleRollSpellAttack('${escapeRpJs(ch.name)}', '${escapeRpJs(sName)}', '${escapeRpJs(sAtk)}')">🎲 Atk</button>` : ""}
                                    ${dmgMatch ? `<button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px;" title="Roll Damage / Healing" onclick="handleRollSpellDamage('${escapeRpJs(ch.name)}', '${escapeRpJs(sName)}', '${escapeRpJs(dmgMatch)}')">💥 Dmg</button>` : ""}
                                    <button type="button" class="vital-step-btn" style="font-size: 9px; padding: 0 4px; color: #f87171;" title="Remove Spell" onclick="handleDeleteSpell('${escapeRpJs(ch.name)}', '${escapeRpJs(sName)}')">✕</button>
                                </div>
                            </div>
                        `;
                    }).join("") : `<div style="font-size: 10.5px; color: #64748b; font-style: italic; padding: 2px 0;">No spells prepared. Click <strong>+ Add Spell</strong> to add.</div>`}
                </div>
            `;
        }

        // Build Features & Traits Shelf HTML
        const feats = extractCharacterFeatures(ch);
        let featsHtml = "";
        if (feats.length > 0) {
            featsHtml = `
                <div class="char-features-shelf" style="margin-top: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(100, 116, 139, 0.2); border-radius: 6px; padding: 6px 8px;">
                    <div style="font-size: 10px; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center;">
                        <span>📜 FEATURES &amp; TRAITS</span>
                        <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600;">${feats.length} TRAITS</span>
                    </div>
                    <div style="font-size: 10.5px; color: #cbd5e1; line-height: 1.45; max-height: 160px; overflow-y: auto;">
                        ${feats.map(f => {
                            const raw = typeof f === "string" ? f : f.name + (f.description ? ": " + f.description : "");
                            const colonIdx = raw.indexOf(":");
                            if (colonIdx !== -1) {
                                const title = raw.substring(0, colonIdx).trim();
                                const desc = raw.substring(colonIdx + 1).trim();
                                return `<div style="margin-bottom: 3px;"><strong style="color: #f1f5f9;">• ${escapeRpHtml(title)}</strong>: <span style="color: #94a3b8; font-size: 10px;">${escapeRpHtml(desc)}</span></div>`;
                            }
                            return `<div style="margin-bottom: 3px;"><strong style="color: #f1f5f9;">• ${escapeRpHtml(raw)}</strong></div>`;
                        }).join("")}
                    </div>
                </div>
            `;
        }

        const charLevel = ch.level || 1;
        const charXp = ch.experience_points || 0;
        const isCoc = Boolean(rLow.includes("cthulhu") || rLow.includes("coc") || (attrs.STR !== undefined && attrs.SIZ !== undefined));
        const isCyber = Boolean(rLow.includes("cyber") || (attrs.REF !== undefined && attrs.TECH !== undefined));
        const isPf = Boolean(rLow.includes("pathfinder") || rLow.includes("pf2e") || rLow.includes("pf1e") || ch.ancestry || ch.class_dc || (attrs.CMB !== undefined || attrs.CMD !== undefined));
        const isDnd = !isCoc && !isCyber && !isPf && Boolean(ch.attributes?.Strength !== undefined || ch.ruleset === "dnd_5e");

        let acText = `${Number(guard.current)}`;
        if (isCoc) {
            acText = `Dodge ${ch.dodge ?? Number(guard.current)}%`;
        } else if (isCyber) {
            acText = `SP ${Number(guard.current)}`;
        } else if (isPf || isDnd) {
            acText = `${Number(guard.current)} AC`;
        }

        const initBadge = ch.initiative ? `<span class="char-guard-badge" style="color: #fbbf24; border-color: rgba(251, 191, 36, 0.35); background: rgba(251, 191, 36, 0.1);" title="Initiative Modifier">⚡ Init ${escapeRpHtml(ch.initiative)}</span>` : "";
        const speedBadge = ch.speed ? `<span class="char-guard-badge" style="color: #94a3b8; border-color: rgba(148, 163, 184, 0.35); background: rgba(148, 163, 184, 0.1);" title="Movement Speed">🏃 ${escapeRpHtml(ch.speed)}</span>` : "";
        const defensesBadge = (ch.defenses || ch.resistances) ? `<span class="char-guard-badge" style="color: #38bdf8; border-color: rgba(56, 189, 248, 0.35); background: rgba(56, 189, 248, 0.1);" title="Defenses & Resistances">🛡️ ${escapeRpHtml(ch.defenses || ch.resistances)}</span>` : "";
        const sensesBadge = ch.senses ? `<span class="char-guard-badge" style="color: #a78bfa; border-color: rgba(167, 139, 250, 0.35); background: rgba(167, 139, 250, 0.1);" title="Senses & Vision">👁️ ${escapeRpHtml(ch.senses)}</span>` : "";
        const goldVal = ch.currency && typeof ch.currency === "object" ? ch.currency.gp : null;
        let coinBadge = "";
        if (!isCoc) {
            if (isCyber) {
                const ebVal = ch.currency?.eb ?? 0;
                coinBadge = `<span class="char-guard-badge" style="color: #38bdf8; border-color: rgba(56, 189, 248, 0.35); background: rgba(56, 189, 248, 0.1);" title="Eurodollars">💶 ${ebVal} eb</span>`;
            } else if (goldVal !== null && goldVal !== undefined) {
                coinBadge = `<span class="char-guard-badge" style="color: #facc15; border-color: rgba(250, 204, 21, 0.35); background: rgba(250, 204, 21, 0.1);" title="Gold Pieces">🪙 ${goldVal} GP</span>`;
            }
        } else {
            const cashVal = ch.currency?.cash || (ch.credit_rating ? `CR ${ch.credit_rating}%` : "");
            if (cashVal) {
                coinBadge = `<span class="char-guard-badge" style="color: #facc15; border-color: rgba(250, 204, 21, 0.35); background: rgba(250, 204, 21, 0.1);" title="Cash / Credit Rating">💵 ${escapeRpHtml(String(cashVal))}</span>`;
            }
        }

        let rulesetBadge = `Lv. ${charLevel}`;
        if (isCoc) rulesetBadge = '🐙 CoC 7E';
        else if (isCyber) rulesetBadge = '⚡ Cyberpunk';
        else if (isPf) rulesetBadge = '🗡️ Pathfinder';

        card.innerHTML = `
            <div class="card-header char-card-top">
                <div>
                    <div class="card-identity" style="display: flex; align-items: center; gap: 6px;">
                        <span class="mic-pulse-indicator" title="Active Single-Mic Speaker">🎙️</span>
                        <h4 class="char-name char-name-title">${escapeRpHtml(ch.name)}</h4>
                        <span class="caller-crown ${isCaller ? 'active' : ''}" title="Designated Party Caller">👑 CALLER</span>
                        <span class="rp-badge" style="background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.35); color: #38bdf8; font-size: 10px; padding: 1px 6px;">${rulesetBadge}</span>
                        ${(charXp && !isCoc) ? `<span style="font-size: 10px; color: #94a3b8;">${charXp} XP</span>` : ""}
                    </div>
                    <div class="char-sub-archetype">${escapeRpHtml(ch.archetype || "Adventurer")} ${ch.player_name ? `• ${escapeRpHtml(ch.player_name)}` : ""}</div>
                </div>
                <div class="card-controls" style="display: flex; align-items: center; gap: 5px; flex-wrap: wrap; justify-content: flex-end;">
                    <button type="button" class="focus-speaker-btn cyber-btn-tiny" title="Set active single-mic focus">Focus</button>
                    <button type="button" class="set-caller-btn cyber-btn-tiny" title="Set as Party Caller">Crown</button>
                    <span class="char-guard-badge" title="${escapeRpHtml(guard.label || "Armor Defense")}">🛡️ ${acText}</span>
                    ${initBadge}
                    ${speedBadge}
                    ${defensesBadge}
                    ${sensesBadge}
                    ${coinBadge}
                    <button type="button" class="vital-step-btn" title="Save this hero permanently to the Character Vault" onclick="handleExportRosterCharToVault('${escapeRpHtml(ch.name)}')">🏛️</button>
                    <button type="button" class="vital-step-btn" title="Award XP or Level Up" onclick="handlePromptProgressCharacter('${escapeRpHtml(ch.name)}')">⭐</button>
                    <button type="button" class="vital-step-btn" title="Edit character details" onclick="handleOpenEditCharacterModal('${escapeRpHtml(ch.id || cid)}')">✏️</button>
                    <button type="button" class="vital-step-btn" title="Remove character" onclick="handleDeleteCharacter('${escapeRpHtml(ch.id || cid)}')">🗑️</button>
                </div>
            </div>

            <!-- Health Vital Row -->
            <div class="vital-row">
                <div class="vital-labels">
                    <span>${escapeRpHtml(hp.label || "Health")}</span>
                    <div class="vital-controls-inline">
                        <button type="button" class="vital-step-btn" onclick="handleModifyCharacterStat('${escapeRpHtml(ch.name)}', 'health', -1, false)">-</button>
                        <span>${Number(hp.current)} / ${Number(hp.max)}</span>
                        <button type="button" class="vital-step-btn" onclick="handleModifyCharacterStat('${escapeRpHtml(ch.name)}', 'health', 1, false)">+</button>
                    </div>
                </div>
                <div class="vital-progress-bg">
                    <div class="vital-fill-health" style="width: ${hpPct}%;"></div>
                </div>
            </div>

            ${customMetersHtml}
            ${attrsHtml}
            ${wpnsHtml}
            ${invHtml}
            ${spellsHtml}
            ${featsHtml}

            <!-- Conditions Row -->
            <div class="conditions-wrap" style="margin-top: 8px;">
                ${condBadges}
                <button type="button" class="cyber-btn-small" style="padding: 1px 7px; font-size: 10.5px;" onclick="handlePromptAddCondition('${escapeRpHtml(ch.name)}')">+ Condition</button>
            </div>
        `;
        gridEl.appendChild(card);
    });
}

function renderSessionJournal() {
    const recapBox = document.getElementById("rp-journal-recap-box");
    const metaRow = document.getElementById("rp-journal-meta-row");
    const entriesList = document.getElementById("rp-journal-entries-list");
    if (!recapBox || !metaRow || !entriesList) return;

    const state = ttrpgData.state || {};
    const recap = state.latest_recap || "";
    const loc = state.current_location || "";
    const quest = state.active_quest || "";
    const clues = state.discovered_clues || [];
    const journal = state.journal || [];

    // 1. Live Recap / Last Turn Memory
    if (recap) {
        recapBox.innerHTML = `
            <div style="font-weight: 700; color: #38bdf8; font-size: 0.76rem; text-transform: uppercase; margin-bottom: 4px; letter-spacing: 0.05em; display: flex; align-items: center; gap: 6px;">
                <span>📍</span> <span>Last Turn &amp; Session Memory (Preserved across Restarts):</span>
            </div>
            <div style="color: #f1f5f9; font-size: 0.86rem; line-height: 1.45;">${escapeRpHtml(recap)}</div>
        `;
    } else {
        recapBox.innerHTML = `
            <div style="color: #64748b; font-size: 0.82rem; font-style: italic;">
                No session recap recorded yet. As your campaign progresses, Aether automatically records your scene beats, discovered clues, and last turn memory here so they persist when you restart.
            </div>
        `;
    }

    // 2. Meta Pills (Location, Quest, Discovered Clues)
    let metaHtml = "";
    if (loc) {
        metaHtml += `<div class="rp-meta-pill"><span>📍</span> <strong>Location:</strong> <span>${escapeRpHtml(loc)}</span></div>`;
    }
    if (quest) {
        metaHtml += `<div class="rp-meta-pill"><span>🎯</span> <strong>Active Quest:</strong> <span>${escapeRpHtml(quest)}</span></div>`;
    }
    if (clues.length > 0) {
        metaHtml += `<div class="rp-meta-pill"><span>🔍</span> <strong>Clues:</strong> ${clues.map(c => `<span class="rp-clue-badge">${escapeRpHtml(c)}</span>`).join(" ")}</div>`;
    }
    metaRow.innerHTML = metaHtml;

    // 3. Chronological Journal Entries
    if (journal.length === 0) {
        entriesList.innerHTML = `<div style="color: #64748b; font-size: 0.78rem; text-align: center; padding: 12px 0;">No journal entries logged yet. Click <strong>+ Add Note</strong> to record your first session beat!</div>`;
    } else {
        entriesList.innerHTML = "";
        [...journal].reverse().forEach(ent => {
            const card = document.createElement("div");
            card.className = "rp-journal-entry-card";
            const timeStr = ent.timestamp ? new Date(ent.timestamp).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : "";
            const cluesHtml = (ent.key_clues && ent.key_clues.length > 0)
                ? `<div style="margin-top: 5px;">${ent.key_clues.map(c => `<span class="rp-clue-badge">${escapeRpHtml(c)}</span>`).join(" ")}</div>`
                : "";

            card.innerHTML = `
                <div class="entry-meta">
                    <span style="font-weight: 600; color: #94a3b8;">${escapeRpHtml(ent.location || "Session Beat")}</span>
                    <span>${escapeRpHtml(timeStr)}</span>
                </div>
                <div style="color: #e2e8f0; font-size: 0.83rem;">${escapeRpHtml(ent.summary || "")}</div>
                ${cluesHtml}
            `;
            entriesList.appendChild(card);
        });
    }
}

const _recentDiceRolls = new Set();
let _lastDiceSignature = '';
let _lastDiceTimestamp = 0;

function appendDiceAuditEntry(rollData) {
    const logEl = document.getElementById("dice-audit-log");
    if (!logEl || !rollData || rollData.status !== "success") return;

    // Deduplication guard: prevent identical roll dispatches from rendering twice
    if (rollData.roll_id) {
        if (_recentDiceRolls.has(rollData.roll_id)) {
            return;
        }
        _recentDiceRolls.add(rollData.roll_id);
        if (_recentDiceRolls.size > 100) {
            const first = _recentDiceRolls.values().next().value;
            _recentDiceRolls.delete(first);
        }
    } else {
        const sig = `${rollData.formula || ''}|${rollData.purpose || ''}|${rollData.summary || ''}|${rollData.total}`;
        const now = Date.now();
        if (_lastDiceSignature === sig && (now - _lastDiceTimestamp) < 1200) {
            return;
        }
        _lastDiceSignature = sig;
        _lastDiceTimestamp = now;
    }

    const emptyHint = logEl.querySelector(".dice-audit-empty");
    if (emptyHint) emptyHint.remove();

    const entry = document.createElement("div");
    entry.className = "dice-audit-entry";
    if (rollData.roll_id) {
        entry.dataset.rollId = rollData.roll_id;
    }
    const nowStr = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const purposeHtml = rollData.purpose ? `<div style="color: #38bdf8; font-weight: 600; font-size: 11px;">${escapeRpHtml(rollData.purpose)}</div>` : "";

    entry.innerHTML = `
        <div class="dice-audit-header">
            <span>🎲 ${escapeRpHtml(rollData.formula || "Roll")} (${escapeRpHtml(rollData.roll_type || "normal")})</span>
            <span>${nowStr}</span>
        </div>
        ${purposeHtml}
        <div style="display: flex; justify-content: space-between; align-items: baseline;">
            <span class="dice-audit-summary">${escapeRpHtml(rollData.summary || "")}</span>
            <span class="dice-audit-total">${Number(rollData.total)}</span>
        </div>
    `;
    logEl.prepend(entry);
}

async function handleRollDiceFormula(formula) {
    const clean = (formula || "").trim();
    if (!clean) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.roll_ttrpg_dice) {
            await window.pywebview.api.roll_ttrpg_dice(clean, activeDiceRollMode, null);
        }
    } catch (e) {
        console.error("[RolePlay] Dice roll error:", e);
    }
}

async function handleAdvanceTurn() {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.advance_ttrpg_turn) {
            await window.pywebview.api.advance_ttrpg_turn();
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Advance turn error:", e);
    }
}

async function handleAddInitiativeItem() {
    const nameInput = document.getElementById("rp-init-name-input");
    const rollInput = document.getElementById("rp-init-roll-input");
    if (!nameInput || !rollInput) return;

    const name = nameInput.value.trim();
    const rollVal = parseInt(rollInput.value || "10", 10);
    if (!name) return;

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.add_ttrpg_initiative) {
            await window.pywebview.api.add_ttrpg_initiative(name, isNaN(rollVal) ? 10 : rollVal, true);
            nameInput.value = "";
            rollInput.value = "";
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Add initiative error:", e);
    }
}

async function handleRemoveInitiative(name) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.remove_ttrpg_initiative) {
            await window.pywebview.api.remove_ttrpg_initiative(name);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Remove initiative error:", e);
    }
}

async function handleModifyCharacterStat(characterName, statKey, delta, isCustomMeter) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.modify_ttrpg_character_stat) {
            await window.pywebview.api.modify_ttrpg_character_stat(characterName, statKey, delta, Boolean(isCustomMeter));
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Modify character stat error:", e);
    }
}

async function handleToggleCondition(characterName, condition, isActive) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.update_ttrpg_condition) {
            await window.pywebview.api.update_ttrpg_condition(characterName, condition, Boolean(isActive));
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Condition update error:", e);
    }
}

async function handlePromptAddCondition(characterName) {
    const cond = prompt(`Enter status condition for ${characterName} (e.g., Bleeding, Poisoned, Stunned, Inspired):`);
    if (!cond || !cond.trim()) return;
    await handleToggleCondition(characterName, cond.trim(), true);
}

async function handleDeleteCharacter(charId) {
    const chars = ttrpgData.characters || [];
    const target = chars.find(c => c.id === charId);
    const charName = target ? target.name : "this hero";
    if (!(await showAetherConfirm(`Remove '${charName}' from the active party roster?`, "Remove Character", "Remove", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_ttrpg_character) {
            await window.pywebview.api.delete_ttrpg_character(charId);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Delete character error:", e);
    }
}

function handleRollWeaponAttack(charName, weaponName, atkBonus) {
    let cleanBonus = String(atkBonus || "+0").trim();
    if (!cleanBonus.startsWith("+") && !cleanBonus.startsWith("-")) {
        cleanBonus = "+" + cleanBonus;
    }
    const formula = `1d20${cleanBonus}`;
    handleRollDiceFormula(formula, `${charName} ${weaponName} Attack`);
}

function handleRollWeaponDamage(charName, weaponName, damageExpr) {
    let cleanDmg = String(damageExpr || "1d4").trim();
    const m = cleanDmg.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?|\d+)/i);
    const formula = m ? m[0].replace(/\s+/g, "") : "1d4";
    handleRollDiceFormula(formula, `${charName} ${weaponName} Damage`);
}

window.handleRollWeaponAttack = handleRollWeaponAttack;
window.handleRollWeaponDamage = handleRollWeaponDamage;

function calcDndMod(score) {
    const num = parseInt(score, 10);
    return isNaN(num) ? 0 : Math.floor((num - 10) / 2);
}

function formatMod(mod) {
    return mod >= 0 ? `+${mod}` : `${mod}`;
}

window.calcDndMod = calcDndMod;
window.formatMod = formatMod;

let stagedCharacterWeapons = [];

function updateHiddenWeaponInputs() {
    const formatted = stagedCharacterWeapons.map(w => {
        const dmgStr = w.damage || "1d4";
        return `${w.name} (${w.atk_bonus || "+0"}, ${dmgStr}${w.notes ? '; ' + w.notes : ''})`;
    }).join("; ");
    ["input-dnd-weapons", "input-pf-strikes", "input-cyber-weapons", "input-coc-weapons", "input-rp-char-weapons"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = formatted;
    });
}

function renderStagedWeapons() {
    updateHiddenWeaponInputs();
    const containers = [
        document.getElementById("dnd-weapons-chips-container"),
        document.getElementById("pf-weapons-chips-container"),
        document.getElementById("cyber-weapons-chips-container"),
        document.getElementById("coc-weapons-chips-container"),
        document.getElementById("agnostic-weapons-chips-container")
    ].filter(Boolean);

    containers.forEach(cont => {
        if (!stagedCharacterWeapons || stagedCharacterWeapons.length === 0) {
            cont.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px;">No weapons equipped. Click <strong>+ Add Weapon</strong> above to add with structured fields.</div>`;
            return;
        }
        cont.innerHTML = stagedCharacterWeapons.map((w, idx) => `
            <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(30, 41, 59, 0.65); border: 1px solid rgba(100, 116, 139, 0.35); border-radius: 6px; padding: 5px 8px;">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                    <span style="font-size: 0.85rem;">⚔️</span>
                    <strong style="color: #f1f5f9; font-size: 0.8rem;">${escapeRpHtml(w.name)}</strong>
                    <span style="color: #38bdf8; font-size: 0.74rem; font-weight: 600;">${escapeRpHtml(w.atk_bonus || "+0")}</span>
                    <span style="color: #cbd5e1; font-size: 0.74rem;">(${escapeRpHtml(w.damage || "1d4")})</span>
                    ${w.type ? `<span style="color: #94a3b8; font-size: 0.68rem; background: rgba(148, 163, 184, 0.15); padding: 1px 5px; border-radius: 3px;">${escapeRpHtml(w.type)}</span>` : ""}
                    ${w.notes ? `<span style="color: #64748b; font-size: 0.7rem; font-style: italic;">[${escapeRpHtml(w.notes)}]</span>` : ""}
                </div>
                <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove weapon" onclick="handleRemoveStagedWeapon(${idx})">✕</button>
            </div>
        `).join("");
    });
}

window.handleRemoveStagedWeapon = function(idx) {
    if (idx >= 0 && idx < stagedCharacterWeapons.length) {
        stagedCharacterWeapons.splice(idx, 1);
        renderStagedWeapons();
    }
};

let stagedCharacterSpells = [];

function updateHiddenSpellInputs() {
    const formatted = stagedCharacterSpells.map(s => {
        if (typeof s === "string") return s;
        const dmgStr = s.damage ? ` (${s.damage})` : "";
        const atkStr = s.atk_bonus ? ` [${s.atk_bonus}]` : (s.save_dc ? ` [${s.save_dc}]` : "");
        const lvlStr = s.level ? ` - ${s.level}` : "";
        return `${s.name}${lvlStr}${atkStr}${dmgStr}`;
    }).join("; ");
    ["input-dnd-spells", "input-pf-spells", "input-rp-char-spells"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = formatted;
    });
}

function renderStagedSpells() {
    updateHiddenSpellInputs();
    const containers = [
        document.getElementById("dnd-spells-chips-container"),
        document.getElementById("pf-spells-chips-container"),
        document.getElementById("agnostic-spells-chips-container")
    ].filter(Boolean);

    containers.forEach(cont => {
        if (!stagedCharacterSpells || stagedCharacterSpells.length === 0) {
            cont.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px;">No spells added. Click <strong>+ Add Spell</strong> above to add with level, damage, DC, and range fields.</div>`;
            return;
        }
        cont.innerHTML = stagedCharacterSpells.map((s, idx) => {
            const sName = typeof s === "string" ? s : s.name;
            const sLvl = typeof s === "object" ? (s.level || "Cantrip") : "Cantrip";
            const sDmg = typeof s === "object" ? s.damage : "";
            const sAtk = typeof s === "object" ? (s.atk_bonus || s.save_dc || "") : "";
            const sRange = typeof s === "object" ? s.range : "";
            const sNotes = typeof s === "object" ? s.notes : "";
            return `
                <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(30, 41, 59, 0.65); border: 1px solid rgba(168, 85, 247, 0.35); border-radius: 6px; padding: 5px 8px;">
                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                        <span style="font-size: 0.85rem;">✨</span>
                        <strong style="color: #f1f5f9; font-size: 0.8rem;">${escapeRpHtml(sName)}</strong>
                        <span style="color: #c084fc; font-size: 0.68rem; background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.3); padding: 1px 5px; border-radius: 3px;">${escapeRpHtml(sLvl)}</span>
                        ${sAtk ? `<span style="color: #38bdf8; font-size: 0.74rem; font-weight: 600;">${escapeRpHtml(sAtk)}</span>` : ""}
                        ${sDmg ? `<span style="color: #f87171; font-size: 0.74rem; font-weight: 600;">(${escapeRpHtml(sDmg)})</span>` : ""}
                        ${sRange ? `<span style="color: #94a3b8; font-size: 0.7rem;">📍 ${escapeRpHtml(sRange)}</span>` : ""}
                        ${sNotes ? `<span style="color: #64748b; font-size: 0.7rem; font-style: italic;">[${escapeRpHtml(sNotes)}]</span>` : ""}
                    </div>
                    <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove spell" onclick="handleRemoveStagedSpell(${idx})">✕</button>
                </div>
            `;
        }).join("");
    });
}

window.handleRemoveStagedSpell = function(idx) {
    if (idx >= 0 && idx < stagedCharacterSpells.length) {
        stagedCharacterSpells.splice(idx, 1);
        renderStagedSpells();
    }
};

function updateSpellLivePreview() {
    const previewEl = document.getElementById("preview-spell-formatted");
    if (!previewEl) return;

    const name = (document.getElementById("input-spell-name")?.value || "").trim() || "Spell";
    const lvl = document.getElementById("select-spell-level")?.value || "Cantrip";
    const atk = (document.getElementById("input-spell-atk")?.value || "").trim();
    const dmg = (document.getElementById("input-spell-dmg")?.value || "").trim();
    const dmgType = document.getElementById("select-spell-dmg-type")?.value || "";
    const range = (document.getElementById("input-spell-range")?.value || "").trim();

    let parts = [`<strong>${escapeRpHtml(name)}</strong> (${escapeRpHtml(lvl)})`];
    if (atk) parts.push(`<span style="color: #38bdf8;">${escapeRpHtml(atk)}</span>`);
    if (dmg) {
        const fullDmg = dmgType && !dmg.toLowerCase().includes(dmgType.toLowerCase()) ? `${dmg} ${dmgType}` : dmg;
        parts.push(`<span style="color: #f87171;">(${escapeRpHtml(fullDmg)})</span>`);
    }
    if (range) parts.push(`<span style="color: #94a3b8;">${escapeRpHtml(range)}</span>`);

    previewEl.innerHTML = parts.join(" • ");
}

window.handleOpenAddSpellModal = function(charNameOrMode) {
    const modal = document.getElementById("modal-add-spell");
    const titleEl = document.getElementById("modal-add-spell-title");
    const charInput = document.getElementById("input-add-spell-char-name");
    const smartRaw = document.getElementById("input-spell-smart-raw");
    const nameInput = document.getElementById("input-spell-name");
    const lvlSelect = document.getElementById("select-spell-level");
    const atkInput = document.getElementById("input-spell-atk");
    const dmgInput = document.getElementById("input-spell-dmg");
    const dmgTypeSelect = document.getElementById("select-spell-dmg-type");
    const rangeInput = document.getElementById("input-spell-range");
    const notesInput = document.getElementById("input-spell-notes");

    const isEditMode = (charNameOrMode === "__active_edit__");
    if (charInput) charInput.value = charNameOrMode || "";
    if (titleEl) {
        titleEl.textContent = isEditMode
            ? "Add Spell / Cantrip to Character Sheet"
            : `Add Spell / Cantrip for ${charNameOrMode || 'Hero'}`;
    }

    if (smartRaw) smartRaw.value = "";
    if (nameInput) nameInput.value = "";
    if (lvlSelect) lvlSelect.value = "Cantrip";
    if (atkInput) atkInput.value = "+5";
    if (dmgInput) dmgInput.value = "1d10";
    if (dmgTypeSelect) dmgTypeSelect.value = "Fire";
    if (rangeInput) rangeInput.value = "120 ft.";
    if (notesInput) notesInput.value = "1 Action";

    updateSpellLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleConfirmAddSpell = async function() {
    const confirmBtn = document.getElementById("btn-confirm-add-spell");
    if (confirmBtn && confirmBtn.disabled) return;

    const targetMode = document.getElementById("input-add-spell-char-name")?.value || "";
    const name = (document.getElementById("input-spell-name")?.value || "").trim();
    const lvl = document.getElementById("select-spell-level")?.value || "Cantrip";
    const atk = (document.getElementById("input-spell-atk")?.value || "").trim();
    const dmg = (document.getElementById("input-spell-dmg")?.value || "").trim();
    const dmgType = document.getElementById("select-spell-dmg-type")?.value || "";
    const range = (document.getElementById("input-spell-range")?.value || "60 ft.").trim();
    const notes = (document.getElementById("input-spell-notes")?.value || "1 Action").trim();

    if (!name) {
        await showAetherAlert("Please enter a spell name.", "Missing Name");
        return;
    }

    const fullDmg = dmg && dmgType && !dmg.toLowerCase().includes(dmgType.toLowerCase()) ? `${dmg} ${dmgType}`.trim() : dmg;
    const spellObj = {
        name: name,
        level: lvl,
        atk_bonus: atk,
        damage: fullDmg,
        range: range,
        notes: notes
    };

    const modal = document.getElementById("modal-add-spell");

    if (targetMode === "__active_edit__") {
        stagedCharacterSpells.push(spellObj);
        renderStagedSpells();
        if (modal) modal.classList.add("hidden");
        return;
    }

    if (!targetMode) {
        await showAetherAlert("No target character specified.", "Error");
        return;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Adding...";
    }

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            const res = await window.pywebview.api.manage_ttrpg_character_inventory(targetMode, "add", null, null, [spellObj], `Added spell ${name}`);
            if (res && res.status === "error") {
                await showAetherAlert(`Could not add spell: ${res.message}`, "Error");
                return;
            }
        }
        if (modal) modal.classList.add("hidden");
        await loadRolePlayUI(true);
    } catch (err) {
        console.error("[RolePlay] Error adding spell:", err);
        await showAetherAlert(`Error adding spell: ${err.message}`, "Error");
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Add Spell";
        }
    }
};

window.handleDeleteSpell = async function(charName, spellName) {
    if (!(await showAetherConfirm(`Remove spell '${spellName}' from ${charName}?`, "Remove Spell", "Remove", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            await window.pywebview.api.manage_ttrpg_character_inventory(charName, "remove", null, null, [spellName], `Removed spell ${spellName}`);
            await loadRolePlayUI(true);
        }
    } catch (err) {
        console.error("[RolePlay] Error removing spell:", err);
        await showAetherAlert(`Error removing spell: ${err.message}`, "Error");
    }
};

window.handleRollSpellAttack = function(charName, spellName, atkBonus) {
    let cleanBonus = String(atkBonus || "+0").trim();
    if (!cleanBonus.startsWith("+") && !cleanBonus.startsWith("-")) {
        cleanBonus = "+" + cleanBonus;
    }
    const formula = `1d20${cleanBonus}`;
    handleRollDiceFormula(formula, `${charName} ${spellName} Spell Attack`);
};

window.handleRollSpellDamage = function(charName, spellName, damageExpr) {
    let cleanDmg = String(damageExpr || "1d10").trim();
    const m = cleanDmg.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?|\d+)/i);
    const formula = m ? m[0].replace(/\s+/g, "") : "1d10";
    handleRollDiceFormula(formula, `${charName} ${spellName} Damage / Healing`);
};

window.handleCastSpell = function(charName, spellName, range, notes) {
    const rangeTxt = range ? ` (${range})` : "";
    const notesTxt = notes ? ` [${notes}]` : "";
    const inputEl = document.getElementById("input-rp-dm-prompt");
    if (inputEl) {
        inputEl.value = `*[SPELL CAST]* ${charName} casts **${spellName}**${rangeTxt}${notesTxt}.`;
        const sendBtn = document.getElementById("btn-send-rp-dm-prompt");
        if (sendBtn) sendBtn.click();
    }
};

window.handleSmartReformatSpell = function() {
    const raw = (document.getElementById("input-spell-smart-raw")?.value || "").trim();
    if (!raw) return;

    const nameInput = document.getElementById("input-spell-name");
    const lvlSelect = document.getElementById("select-spell-level");
    const atkInput = document.getElementById("input-spell-atk");
    const dmgInput = document.getElementById("input-spell-dmg");
    const dmgTypeSelect = document.getElementById("select-spell-dmg-type");
    const rangeInput = document.getElementById("input-spell-range");

    const lvlM = raw.match(/(\d+(?:st|nd|rd|th)\s+level|cantrip|focus(?:\s+spell)?)/i);
    if (lvlM && lvlSelect) {
        const foundLvl = lvlM[1].toLowerCase();
        for (let opt of lvlSelect.options) {
            if (foundLvl.includes(opt.value.toLowerCase()) || opt.value.toLowerCase().includes(foundLvl)) {
                lvlSelect.value = opt.value;
                break;
            }
        }
    }

    const dmgM = raw.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i);
    if (dmgM && dmgInput) {
        dmgInput.value = dmgM[1].replace(/\s+/g, "");
    }

    const dcM = raw.match(/(DC\s*\d+\s*[A-Za-z]+|\+\d+)/i);
    if (dcM && atkInput) {
        atkInput.value = dcM[1].trim();
    }

    const types = ["Fire", "Radiant", "Healing", "Force", "Necrotic", "Cold", "Lightning", "Thunder", "Acid", "Poison", "Psychic", "Buff / Utility"];
    for (let t of types) {
        if (raw.toLowerCase().includes(t.toLowerCase()) || (t === "Healing" && raw.toLowerCase().includes("heal"))) {
            if (dmgTypeSelect) dmgTypeSelect.value = t;
            break;
        }
    }

    const rangeM = raw.match(/(\d+\s*ft\.?|touch|self(?:\s*\([^)]*\))?)/i);
    if (rangeM && rangeInput) {
        rangeInput.value = rangeM[1].trim();
    }

    let cleanName = raw.split(/[,\(]/)[0].trim().replace(/^[-•*]\s*/, "").trim();
    if (cleanName && nameInput) {
        nameInput.value = cleanName;
    }

    updateSpellLivePreview();
};

function updateWeaponLivePreview() {
    const previewEl = document.getElementById("preview-weapon-formatted");
    if (!previewEl) return;

    const name = (document.getElementById("input-weapon-name")?.value || "").trim() || "Weapon";
    let atk = (document.getElementById("input-weapon-atk")?.value || "+0").trim();
    if (atk && !atk.startsWith("+") && !atk.startsWith("-") && !isNaN(parseInt(atk, 10))) {
        atk = `+${atk}`;
    }
    const dmg = (document.getElementById("input-weapon-dmg")?.value || "1d4").trim();
    const dmgType = document.getElementById("select-weapon-dmg-type")?.value || "";
    const notes = (document.getElementById("input-weapon-notes")?.value || "").trim();

    const fullDmg = dmgType && !dmg.toLowerCase().includes(dmgType.toLowerCase()) ? `${dmg} ${dmgType}`.trim() : dmg;
    previewEl.textContent = `${name} (${atk || '+0'}, ${fullDmg}${notes ? '; ' + notes : ''})`;
}

window.handleOpenAddWeaponModal = function(charNameOrMode) {
    const modal = document.getElementById("modal-add-weapon");
    const titleEl = document.getElementById("modal-add-weapon-title");
    const charInput = document.getElementById("input-add-weapon-char-name");
    const smartRaw = document.getElementById("input-weapon-smart-raw");
    const nameInput = document.getElementById("input-weapon-name");
    const typeSelect = document.getElementById("select-weapon-type");
    const atkInput = document.getElementById("input-weapon-atk");
    const dmgInput = document.getElementById("input-weapon-dmg");
    const dmgTypeSelect = document.getElementById("select-weapon-dmg-type");
    const notesInput = document.getElementById("input-weapon-notes");

    const isEditMode = (charNameOrMode === "__active_edit__");
    if (charInput) charInput.value = charNameOrMode || "";
    if (titleEl) {
        titleEl.textContent = isEditMode
            ? "Add Weapon / Attack to Character Sheet"
            : `Add Weapon / Attack for ${charNameOrMode || 'Hero'}`;
    }

    if (smartRaw) smartRaw.value = "";
    if (nameInput) nameInput.value = "";
    if (typeSelect) typeSelect.value = "Melee Weapon";
    if (atkInput) {
        const strEl = document.getElementById("input-dnd-str");
        const strMod = strEl ? (typeof calcDndMod === "function" ? calcDndMod(strEl.value) : 0) : 0;
        atkInput.value = (typeof formatMod === "function") ? formatMod(strMod) : `+${strMod}`;
    }
    if (dmgInput) dmgInput.value = "1d6";
    if (dmgTypeSelect) dmgTypeSelect.value = "Slashing";
    if (notesInput) notesInput.value = "";

    updateWeaponLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleConfirmAddWeapon = async function() {
    const confirmBtn = document.getElementById("btn-confirm-add-weapon");
    if (confirmBtn && confirmBtn.disabled) return;

    const targetMode = document.getElementById("input-add-weapon-char-name")?.value || "";
    const name = (document.getElementById("input-weapon-name")?.value || "").trim();
    const type = document.getElementById("select-weapon-type")?.value || "Melee Weapon";
    let atk = (document.getElementById("input-weapon-atk")?.value || "+0").trim();
    if (atk && !atk.startsWith("+") && !atk.startsWith("-") && !isNaN(parseInt(atk, 10))) {
        atk = `+${atk}`;
    }
    const dmg = (document.getElementById("input-weapon-dmg")?.value || "1d4").trim();
    const dmgType = document.getElementById("select-weapon-dmg-type")?.value || "";
    const notes = (document.getElementById("input-weapon-notes")?.value || "").trim();

    if (!name) {
        alert("Please enter a weapon or attack name.");
        return;
    }

    const fullDmg = dmgType && !dmg.toLowerCase().includes(dmgType.toLowerCase()) ? `${dmg} ${dmgType}`.trim() : dmg;
    const weaponObj = {
        name: name,
        type: type,
        atk_bonus: atk || "+0",
        damage: fullDmg,
        notes: notes
    };

    const modal = document.getElementById("modal-add-weapon");

    if (targetMode === "__active_edit__") {
        stagedCharacterWeapons.push(weaponObj);
        renderStagedWeapons();
        if (modal) modal.classList.add("hidden");
        return;
    }

    if (!targetMode) {
        alert("No target character specified.");
        return;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Adding...";
    }

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            const res = await window.pywebview.api.manage_ttrpg_character_inventory(targetMode, "add", null, [weaponObj], `Added weapon ${name}`);
            if (res && res.status === "error") {
                alert(`Could not add weapon: ${res.message}`);
                return;
            }
        }
        if (modal) modal.classList.add("hidden");
        await loadRolePlayUI(true);
    } catch (err) {
        console.error("[RolePlay] Error adding weapon:", err);
        alert(`Error adding weapon: ${err.message}`);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Add Weapon";
        }
    }
};

window.handleDeleteWeapon = async function(charName, weaponName) {
    if (!(await showAetherConfirm(`Remove weapon '${weaponName}' from ${charName}?`, "Remove Weapon", "Remove", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            await window.pywebview.api.manage_ttrpg_character_inventory(charName, "remove", null, [{ name: weaponName }], `Removed weapon ${weaponName}`);
        }
        await loadRolePlayUI(true);
    } catch (err) {
        console.error("[RolePlay] Error removing weapon:", err);
        await showAetherAlert(`Error removing weapon: ${err.message}`, "Error");
    }
};

window.handleOpenAddInventoryModal = function(charName) {
    const modal = document.getElementById("modal-add-inventory");
    const titleEl = document.getElementById("modal-add-inventory-title");
    const charInput = document.getElementById("input-add-inventory-char-name");
    const multiInput = document.getElementById("input-inventory-multiline");

    const isEditMode = (charName === "__active_edit__");
    if (charInput) charInput.value = charName || "";
    if (titleEl) {
        titleEl.textContent = isEditMode
            ? "Add Items to Character Sheet Inventory"
            : `Add Items to Inventory for ${charName || 'Hero'}`;
    }
    if (multiInput) multiInput.value = "";

    if (modal) {
        modal.classList.remove("hidden");
        if (multiInput) setTimeout(() => multiInput.focus(), 60);
    }
};

window.handleConfirmAddInventory = async function() {
    const confirmBtn = document.getElementById("btn-confirm-add-inventory");
    if (confirmBtn && confirmBtn.disabled) return;

    const charName = document.getElementById("input-add-inventory-char-name")?.value || "";
    const rawItems = (document.getElementById("input-inventory-multiline")?.value || "").trim();

    if (!charName) {
        alert("No target character specified.");
        return;
    }
    if (!rawItems) {
        alert("Please enter at least one item to add.");
        return;
    }

    // Split by newlines or commas (preserving commas inside parentheses)
    const items = rawItems
        .split(/[\r\n]+|,\s*(?![^()]*\))/)
        .map(i => i.trim())
        .filter(i => i.length > 0);

    if (items.length === 0) {
        alert("Please enter at least one item to add.");
        return;
    }

    if (charName === "__active_edit__") {
        ["input-dnd-inventory", "input-pf-gear", "input-cyber-gear", "input-rp-char-inventory"].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                const cur = el.value.trim();
                el.value = cur ? `${cur}, ${items.join(', ')}` : items.join(', ');
            }
        });
        const modal = document.getElementById("modal-add-inventory");
        if (modal) modal.classList.add("hidden");
        return;
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Adding...";
    }

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            const res = await window.pywebview.api.manage_ttrpg_character_inventory(charName, "add", items, null, `Added ${items.length} item(s)`);
            if (res && res.status === "error") {
                alert(`Could not add items: ${res.message}`);
                return;
            }
        }
        const modal = document.getElementById("modal-add-inventory");
        if (modal) modal.classList.add("hidden");
        await loadRolePlayUI(true);
    } catch (err) {
        console.error("[RolePlay] Error adding inventory items:", err);
        alert(`Error adding items: ${err.message}`);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Add to Inventory";
        }
    }
};

window.handleDeleteInventoryItem = async function(charName, itemName) {
    if (!(await showAetherConfirm(`Remove '${itemName}' from ${charName}'s inventory?`, "Remove Item", "Remove", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            await window.pywebview.api.manage_ttrpg_character_inventory(charName, "remove", [itemName], null, `Removed item ${itemName}`);
        }
        await loadRolePlayUI(true);
    } catch (err) {
        console.error("[RolePlay] Error removing inventory item:", err);
        await showAetherAlert(`Error removing item: ${err.message}`, "Error");
    }
};

async function handlePromptProgressCharacter(charName) {
    const input = prompt(`Level progression for ${charName}:\nEnter XP amount to award (e.g. 300) OR type 'level' to level up immediately:`);
    if (!input) return;
    const clean = input.trim().toLowerCase();
    let action = "award_xp";
    let amount = 0;
    if (clean === "level" || clean === "lvl" || clean === "levelup") {
        action = "level_up";
    } else {
        const num = parseInt(clean, 10);
        if (isNaN(num) || num <= 0) {
            alert("Please enter a valid positive XP number or 'level'.");
            return;
        }
        amount = num;
    }
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_progression) {
            const res = await window.pywebview.api.manage_ttrpg_progression(charName, action, amount, null);
            if (res && res.message) {
                alert(res.message);
            }
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("Progression error:", e);
    }
}

// Canonical D&D 5E Skills Definition
const DND_5E_SKILLS_DEF = [
    { name: "Acrobatics", stat: "dex" },
    { name: "Animal Handling", stat: "wis" },
    { name: "Arcana", stat: "int" },
    { name: "Athletics", stat: "str" },
    { name: "Deception", stat: "cha" },
    { name: "History", stat: "int" },
    { name: "Insight", stat: "wis" },
    { name: "Intimidation", stat: "cha" },
    { name: "Investigation", stat: "int" },
    { name: "Medicine", stat: "wis" },
    { name: "Nature", stat: "int" },
    { name: "Perception", stat: "wis" },
    { name: "Performance", stat: "cha" },
    { name: "Persuasion", stat: "cha" },
    { name: "Religion", stat: "int" },
    { name: "Sleight of Hand", stat: "dex" },
    { name: "Stealth", stat: "dex" },
    { name: "Survival", stat: "wis" }
];

function renderDndSkillsGrid(skillsObj) {
    const container = document.getElementById("dnd-skills-grid-container");
    if (!container) return;

    skillsObj = skillsObj || {};
    container.innerHTML = DND_5E_SKILLS_DEF.map(s => {
        const cleanKey = s.name.replace(/[^a-zA-Z]/g, '');
        let isProf = false;
        if (skillsObj[s.name] !== undefined) {
            const v = String(skillsObj[s.name]);
            isProf = v.includes("prof") || v.includes("P") || v.startsWith("+") || parseInt(v, 10) > 0;
        }

        return `
            <label class="dnd-skill-item" style="display: flex; align-items: center; gap: 5px; background: rgba(30, 41, 59, 0.55); border: 1px solid rgba(100, 116, 139, 0.25); border-radius: 4px; padding: 3px 6px; cursor: pointer; font-size: 11px;">
                <input type="checkbox" class="dnd-skill-chk" data-skill="${escapeRpHtml(s.name)}" data-stat="${s.stat}" ${isProf ? 'checked' : ''} />
                <span style="color: #cbd5e1; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${escapeRpHtml(s.name)} (${s.stat.toUpperCase()})">
                    ${escapeRpHtml(s.name)} <span style="font-size: 9px; color: #64748b;">${s.stat.toUpperCase()}</span>
                </span>
                <span id="val-dnd-skill-${cleanKey}" style="margin-left: auto; font-weight: 700; font-size: 10.5px; color: ${isProf ? '#4ade80' : '#94a3b8'};">+0</span>
            </label>
        `;
    }).join("");

    container.querySelectorAll(".dnd-skill-chk").forEach(chk => {
        chk.addEventListener("change", () => updateDndCalculations());
    });
}

function updateDndCalculations() {
    const strVal = parseInt(document.getElementById("input-dnd-str")?.value || "10", 10);
    const dexVal = parseInt(document.getElementById("input-dnd-dex")?.value || "10", 10);
    const conVal = parseInt(document.getElementById("input-dnd-con")?.value || "10", 10);
    const intVal = parseInt(document.getElementById("input-dnd-int")?.value || "10", 10);
    const wisVal = parseInt(document.getElementById("input-dnd-wis")?.value || "10", 10);
    const chaVal = parseInt(document.getElementById("input-dnd-cha")?.value || "10", 10);

    const mods = {
        str: calcDndMod(strVal),
        dex: calcDndMod(dexVal),
        con: calcDndMod(conVal),
        int: calcDndMod(intVal),
        wis: calcDndMod(wisVal),
        cha: calcDndMod(chaVal)
    };

    // Update Modifier Badges
    for (const [stat, mod] of Object.entries(mods)) {
        const badge = document.getElementById(`badge-dnd-${stat}-mod`);
        if (badge) {
            badge.textContent = formatMod(mod);
            badge.style.color = mod >= 0 ? "#38bdf8" : "#f87171";
            badge.style.background = mod >= 0 ? "rgba(56, 189, 248, 0.15)" : "rgba(239, 68, 68, 0.15)";
        }
    }

    // Parse Prof Bonus
    const rawProf = document.getElementById("input-dnd-prof-bonus")?.value || "+2";
    const profBonus = Math.max(0, parseInt(rawProf.replace(/[^0-9-]/g, "") || "2", 10));

    // Update Saving Throws
    for (const [stat, mod] of Object.entries(mods)) {
        const chk = document.getElementById(`chk-dnd-save-${stat}`);
        const valEl = document.getElementById(`val-dnd-save-${stat}`);
        if (valEl) {
            const isProf = Boolean(chk && chk.checked);
            const total = mod + (isProf ? profBonus : 0);
            valEl.textContent = formatMod(total);
            valEl.style.color = isProf ? "#4ade80" : (total >= 0 ? "#94a3b8" : "#f87171");
        }
    }

    // Auto-update Initiative if not customized
    const initEl = document.getElementById("input-dnd-init");
    if (initEl && (!initEl.dataset.customized || initEl.dataset.customized === "false")) {
        initEl.value = formatMod(mods.dex);
    }

    // Update 18 Skills
    const container = document.getElementById("dnd-skills-grid-container");
    if (container) {
        DND_5E_SKILLS_DEF.forEach(s => {
            const cleanKey = s.name.replace(/[^a-zA-Z]/g, '');
            const chk = container.querySelector(`input[data-skill="${s.name}"]`);
            const valEl = document.getElementById(`val-dnd-skill-${cleanKey}`);
            if (valEl) {
                const isProf = Boolean(chk && chk.checked);
                const statMod = mods[s.stat] || 0;
                const total = statMod + (isProf ? profBonus : 0);
                valEl.textContent = formatMod(total);
                valEl.style.color = isProf ? "#4ade80" : (total >= 0 ? "#94a3b8" : "#f87171");
            }
        });
    }

    // Update Passive Perception, Insight, Investigation
    const passPercepEl = document.getElementById("input-dnd-pass-percep");
    const passInsightEl = document.getElementById("input-dnd-pass-insight");
    const passInvestEl = document.getElementById("input-dnd-pass-invest");
    if (container) {
        const percepProf = container.querySelector('input[data-skill="Perception"]')?.checked;
        const insightProf = container.querySelector('input[data-skill="Insight"]')?.checked;
        const investProf = container.querySelector('input[data-skill="Investigation"]')?.checked;

        if (passPercepEl && !passPercepEl.dataset.customized) {
            passPercepEl.value = 10 + mods.wis + (percepProf ? profBonus : 0);
        }
        if (passInsightEl && !passInsightEl.dataset.customized) {
            passInsightEl.value = 10 + mods.wis + (insightProf ? profBonus : 0);
        }
        if (passInvestEl && !passInvestEl.dataset.customized) {
            passInvestEl.value = 10 + mods.int + (investProf ? profBonus : 0);
        }
    }

    // Update Spell DC and Attack
    const spellAbility = document.getElementById("select-dnd-spell-ability")?.value || "INT";
    const spellDcEl = document.getElementById("input-dnd-spell-dc");
    const spellAtkEl = document.getElementById("input-dnd-spell-atk");
    if (spellAbility !== "none" && mods[spellAbility.toLowerCase()] !== undefined) {
        const sMod = mods[spellAbility.toLowerCase()];
        if (spellDcEl && !spellDcEl.dataset.customized) {
            spellDcEl.value = 8 + profBonus + sMod;
        }
        if (spellAtkEl && !spellAtkEl.dataset.customized) {
            spellAtkEl.value = formatMod(profBonus + sMod);
        }
    }
}

const COC_7E_SKILLS_DEF = [
    { name: "Accounting", def: 5 },
    { name: "Anthropology", def: 1 },
    { name: "Appraise", def: 5 },
    { name: "Archaeology", def: 1 },
    { name: "Art/Craft", def: 5 },
    { name: "Charm", def: 15 },
    { name: "Climb", def: 20 },
    { name: "Credit Rating", def: 0 },
    { name: "Cthulhu Mythos", def: 0 },
    { name: "Disguise", def: 5 },
    { name: "Dodge", def: 20 },
    { name: "Drive Auto", def: 20 },
    { name: "Elec Repair", def: 10 },
    { name: "Fast Talk", def: 5 },
    { name: "Fighting (Brawl)", def: 25 },
    { name: "Firearms (Handgun)", def: 20 },
    { name: "Firearms (Rifle/Shotgun)", def: 25 },
    { name: "First Aid", def: 30 },
    { name: "History", def: 5 },
    { name: "Intimidate", def: 15 },
    { name: "Jump", def: 20 },
    { name: "Language (Own)", def: 60 },
    { name: "Language (Other)", def: 1 },
    { name: "Law", def: 5 },
    { name: "Library Use", def: 20 },
    { name: "Listen", def: 20 },
    { name: "Locksmith", def: 1 },
    { name: "Mech. Repair", def: 10 },
    { name: "Medicine", def: 1 },
    { name: "Natural World", def: 10 },
    { name: "Navigate", def: 10 },
    { name: "Occult", def: 5 },
    { name: "Op. Hv. Machine", def: 1 },
    { name: "Persuade", def: 10 },
    { name: "Pilot", def: 1 },
    { name: "Psychology", def: 10 },
    { name: "Psychoanalysis", def: 1 },
    { name: "Ride", def: 5 },
    { name: "Science", def: 1 },
    { name: "Sleight of Hand", def: 10 },
    { name: "Spot Hidden", def: 25 },
    { name: "Stealth", def: 20 },
    { name: "Survival", def: 10 },
    { name: "Swim", def: 20 },
    { name: "Throw", def: 20 },
    { name: "Track", def: 10 }
];

function updateCocCalculations() {
    const stats = ["str", "con", "siz", "dex", "app", "int", "pow", "edu"];
    stats.forEach(st => {
        const input = document.getElementById(`input-coc-${st}`);
        const halfEl = document.getElementById(`coc-half-${st}`);
        const fifthEl = document.getElementById(`coc-fifth-${st}`);
        if (input && halfEl && fifthEl) {
            const v = parseInt(input.value || "50", 10) || 0;
            halfEl.textContent = Math.floor(v / 2);
            fifthEl.textContent = Math.floor(v / 5);
        }
    });
    const dodgeInp = document.getElementById("input-coc-dodge");
    const halfDodge = document.getElementById("coc-half-dodge");
    const fifthDodge = document.getElementById("coc-fifth-dodge");
    if (dodgeInp && halfDodge && fifthDodge) {
        const v = parseInt(dodgeInp.value || "40", 10) || 0;
        halfDodge.textContent = Math.floor(v / 2);
        fifthDodge.textContent = Math.floor(v / 5);
    }
}

function renderCocSkillsGrid(skillsObj = {}) {
    const container = document.getElementById("coc-skills-grid-container");
    if (!container) return;

    const getSkillVal = (name, def) => {
        if (!skillsObj || typeof skillsObj !== "object") return def;
        const normName = name.toLowerCase().replace(/[^a-z0-9]/g, "");
        for (const [k, v] of Object.entries(skillsObj)) {
            const normK = k.toLowerCase().replace(/[^a-z0-9]/g, "");
            if (normK === normName || normK.includes(normName) || normName.includes(normK)) {
                const parsed = parseInt(String(v).replace(/[^0-9]/g, ""), 10);
                if (!isNaN(parsed)) return parsed;
            }
        }
        return def;
    };

    container.innerHTML = COC_7E_SKILLS_DEF.map(s => {
        const val = getSkillVal(s.name, s.def);
        const half = Math.floor(val / 2);
        const fifth = Math.floor(val / 5);
        const cleanKey = s.name.replace(/[^a-zA-Z0-9]/g, "_");
        const isUpgraded = val > s.def;

        return `
            <div class="coc-skill-item" style="display: flex; justify-content: space-between; align-items: center; padding: 3px 6px; background: rgba(15, 23, 42, 0.45); border-radius: 4px; border: 1px solid ${isUpgraded ? 'rgba(56, 189, 248, 0.3)' : 'rgba(255, 255, 255, 0.05)'};">
                <span style="font-size: 0.72rem; color: ${isUpgraded ? '#f1f5f9' : '#94a3b8'}; font-weight: ${isUpgraded ? '600' : '400'};" title="${escapeRpHtml(s.name)} (Base: ${s.def}%)">${escapeRpHtml(s.name)}</span>
                <div style="display: flex; align-items: center; gap: 4px;">
                    <input type="number" id="input-coc-skill-${cleanKey}" data-skill="${escapeRpHtml(s.name)}" class="modal-input coc-skill-input" value="${val}" min="0" max="99" style="width: 44px; padding: 2px 4px; font-size: 0.75rem; text-align: center; font-weight: 700; ${isUpgraded ? 'color: #38bdf8;' : ''}" />
                    <span style="font-size: 0.65rem; color: #64748b;"><span id="half-coc-skill-${cleanKey}" style="color: #38bdf8;">${half}</span>/<span id="fifth-coc-skill-${cleanKey}" style="color: #eab308;">${fifth}</span></span>
                </div>
            </div>
        `;
    }).join("");

    container.querySelectorAll(".coc-skill-input").forEach(inp => {
        inp.addEventListener("input", () => {
            const val = parseInt(inp.value || "0", 10) || 0;
            const skillName = inp.getAttribute("data-skill") || "";
            const cleanKey = skillName.replace(/[^a-zA-Z0-9]/g, "_");
            const halfEl = document.getElementById(`half-coc-skill-${cleanKey}`);
            const fifthEl = document.getElementById(`fifth-coc-skill-${cleanKey}`);
            if (halfEl) halfEl.textContent = Math.floor(val / 2);
            if (fifthEl) fifthEl.textContent = Math.floor(val / 5);
        });
    });
}

function onRulesetDropdownChanged(rulesetId) {
    const norm = (rulesetId || "dnd_5e").toLowerCase();
    const dndPanel = document.getElementById("view-char-dnd5e");
    const pfPanel = document.getElementById("view-char-pathfinder");
    const cyberPanel = document.getElementById("view-char-cyberpunk");
    const cocPanel = document.getElementById("view-char-coc");
    const agnPanel = document.getElementById("view-char-agnostic");
    const hintEl = document.getElementById("modal-char-ruleset-hint");

    if (dndPanel) dndPanel.style.display = "none";
    if (pfPanel) pfPanel.style.display = "none";
    if (cyberPanel) cyberPanel.style.display = "none";
    if (cocPanel) cocPanel.style.display = "none";
    if (agnPanel) agnPanel.style.display = "none";

    if (norm.includes("pathfinder")) {
        if (pfPanel) pfPanel.style.display = "block";
        if (hintEl) hintEl.innerHTML = "📜 Pathfinder 2E Canonical Mode";
    } else if (norm.includes("cyber")) {
        if (cyberPanel) cyberPanel.style.display = "block";
        if (hintEl) hintEl.innerHTML = "⚡ Cyberpunk RED / 2020 Canonical Mode";
    } else if (norm.includes("cthulhu") || norm.includes("coc")) {
        if (cocPanel) cocPanel.style.display = "block";
        if (hintEl) hintEl.innerHTML = "🐙 Call of Cthulhu (7th Edition) Canonical Mode";
        updateCocCalculations();
    } else if (norm.includes("agnostic") || norm.includes("sandbox") || norm.includes("custom")) {
        if (agnPanel) agnPanel.style.display = "block";
        if (hintEl) hintEl.innerHTML = "🎲 Universal / Agnostic Mode";
    } else {
        if (dndPanel) dndPanel.style.display = "block";
        if (hintEl) hintEl.innerHTML = "⚔️ D&amp;D 5E Canonical Mode";
        updateDndCalculations();
    }
}

let editingCharacterSource = "campaign";
let currentEditingCharacterObj = null;

async function handleOpenEditCharacterModal(charId, source = "campaign") {
    editingCharacterId = charId;
    editingCharacterSource = source;

    let ch = null;
    if (source === "vault") {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_vault_character) {
            try {
                ch = await window.pywebview.api.get_vault_character(charId);
            } catch (err) {
                console.error("[Vault] Error fetching character for editing:", err);
            }
        }
    } else {
        const charsObj = (ttrpgData.state && ttrpgData.state.characters) || {};
        ch = charsObj[charId];
    }
    if (!ch) {
        alert("Could not load character sheet for editing.");
        return;
    }
    currentEditingCharacterObj = ch;

    const heading = document.getElementById("modal-char-heading");
    if (heading) {
        heading.textContent = source === "vault"
            ? `Edit Vault Character: ${ch.name}`
            : `Edit Character: ${ch.name}`;
    }

    // Detect ruleset with robust attribute fallback
    const attrs = ch.attributes || {};
    let rawR = (ch.ruleset || ch.native_system || ch.native_genre || "").toLowerCase();
    if (!rawR) {
        if (attrs.REF !== undefined || attrs.TECH !== undefined || attrs.EMP !== undefined || attrs.COOL !== undefined || attrs.BODY !== undefined || attrs.WILL !== undefined) {
            rawR = "cyberpunk";
        } else if (attrs.SAN !== undefined || attrs.POW !== undefined || attrs.EDU !== undefined) {
            rawR = "call_of_cthulhu";
        } else if (ch.ancestry || ch.class_dc) {
            rawR = "pathfinder_2e";
        } else if (attrs.Strength !== undefined || attrs.Dexterity !== undefined) {
            rawR = "dnd_5e";
        } else {
            rawR = (ttrpgData.active_campaign_meta?.system_preset || "dnd_5e").toLowerCase();
        }
    }
    let normRuleset = "dnd_5e";
    if (rawR.includes("cyber")) normRuleset = "cyberpunk";
    else if (rawR.includes("pathfinder")) normRuleset = "pathfinder_2e";
    else if (rawR.includes("cthulhu") || rawR.includes("coc") || attrs.SAN !== undefined || attrs.POW !== undefined || attrs.EDU !== undefined || (attrs.STR !== undefined && attrs.SIZ !== undefined)) normRuleset = "call_of_cthulhu";
    else if (rawR.includes("agnostic") || rawR.includes("sandbox") || rawR.includes("custom")) normRuleset = "agnostic";

    const rulesetSelect = document.getElementById("select-rp-char-ruleset");
    if (rulesetSelect) {
        rulesetSelect.value = normRuleset;
    }
    onRulesetDropdownChanged(normRuleset);

    // Common Identity
    const nameEl = document.getElementById("input-rp-char-name");
    const playerEl = document.getElementById("input-rp-char-player");
    if (nameEl) nameEl.value = ch.name || "";
    if (playerEl) playerEl.value = ch.player_name || "";

    const wpns = Array.isArray(ch.weapons) && ch.weapons.length > 0
        ? ch.weapons
        : (ch.native_system_data?.weapons || []);
    const wpnsFormatted = wpns.map(w => typeof w === "string" ? w : `${w.name} (${w.atk_bonus || "+0"}, ${w.damage || ""}${w.notes ? '; ' + w.notes : ''})`).join("; ");
    const inv = extractCharacterInventory(ch);
    const invFormatted = inv.join(", ");
    const spells = extractCharacterSpells(ch);
    const spellsFormatted = spells.join(", ");
    const feats = extractCharacterFeatures(ch);
    const featsFormatted = feats.join("\n");

    // Populate D&D 5E View Fields
    const dndClassEl = document.getElementById("input-dnd-class-level");
    const dndSpeciesEl = document.getElementById("input-dnd-species");
    const dndBgEl = document.getElementById("input-dnd-background");
    const dndXpEl = document.getElementById("input-dnd-xp");

    if (dndClassEl) dndClassEl.value = ch.class_and_level || ch.archetype || "Fighter 1";
    if (dndSpeciesEl) dndSpeciesEl.value = ch.species || ch.race || "";
    if (dndBgEl) dndBgEl.value = ch.background || "";
    if (dndXpEl) dndXpEl.value = ch.experience_points ?? 0;

    const strEl = document.getElementById("input-dnd-str");
    const dexEl = document.getElementById("input-dnd-dex");
    const conEl = document.getElementById("input-dnd-con");
    const intEl = document.getElementById("input-dnd-int");
    const wisEl = document.getElementById("input-dnd-wis");
    const chaEl = document.getElementById("input-dnd-cha");

    if (strEl) strEl.value = attrs.Strength ?? attrs.Might ?? 15;
    if (dexEl) dexEl.value = attrs.Dexterity ?? attrs.Agility ?? 12;
    if (conEl) conEl.value = attrs.Constitution ?? attrs.Vigor ?? 14;
    if (intEl) intEl.value = attrs.Intelligence ?? attrs.Intellect ?? 13;
    if (wisEl) wisEl.value = attrs.Wisdom ?? attrs.Presence ?? 10;
    if (chaEl) chaEl.value = attrs.Charisma ?? attrs.Presence ?? 8;

    const dndAcEl = document.getElementById("input-dnd-ac");
    const dndInitEl = document.getElementById("input-dnd-init");
    const dndSpeedEl = document.getElementById("input-dnd-speed");
    const dndProfEl = document.getElementById("input-dnd-prof-bonus");
    const dndMaxHpEl = document.getElementById("input-dnd-max-hp");
    const dndCurHpEl = document.getElementById("input-dnd-cur-hp");
    const dndTempHpEl = document.getElementById("input-dnd-temp-hp");
    const dndHitDiceEl = document.getElementById("input-dnd-hit-dice");

    if (dndAcEl) dndAcEl.value = ch.armor_class ?? ch.vitals?.guard?.current ?? 19;
    if (dndInitEl) {
        dndInitEl.value = ch.initiative || "+1";
        dndInitEl.dataset.customized = ch.initiative ? "true" : "false";
    }
    if (dndSpeedEl) dndSpeedEl.value = ch.speed || "30 ft. (Walking)";
    if (dndProfEl) dndProfEl.value = ch.prof_bonus || "+2";
    if (dndMaxHpEl) dndMaxHpEl.value = ch.vitals?.health?.max ?? 14;
    if (dndCurHpEl) dndCurHpEl.value = ch.vitals?.health?.current ?? 14;
    if (dndTempHpEl) dndTempHpEl.value = ch.temp_hp ?? 0;
    if (dndHitDiceEl) dndHitDiceEl.value = ch.hit_dice || "1d10";

    // Saving Throws Checkboxes
    const saves = ch.saving_throws || {};
    const strSaveChk = document.getElementById("chk-dnd-save-str");
    const dexSaveChk = document.getElementById("chk-dnd-save-dex");
    const conSaveChk = document.getElementById("chk-dnd-save-con");
    const intSaveChk = document.getElementById("chk-dnd-save-int");
    const wisSaveChk = document.getElementById("chk-dnd-save-wis");
    const chaSaveChk = document.getElementById("chk-dnd-save-cha");

    const profBonusNum = parseInt((ch.prof_bonus || "+2").replace(/[^0-9-]/g, "") || "2", 10);
    const strMod = calcDndMod(strEl?.value);
    const conMod = calcDndMod(conEl?.value);

    if (strSaveChk) strSaveChk.checked = Boolean(saves.Strength && (saves.Strength.proficient || parseInt(saves.Strength, 10) > strMod));
    if (dexSaveChk) dexSaveChk.checked = Boolean(saves.Dexterity && (saves.Dexterity.proficient || parseInt(saves.Dexterity, 10) > calcDndMod(dexEl?.value)));
    if (conSaveChk) conSaveChk.checked = Boolean(saves.Constitution && (saves.Constitution.proficient || parseInt(saves.Constitution, 10) > conMod));
    if (intSaveChk) intSaveChk.checked = Boolean(saves.Intelligence && (saves.Intelligence.proficient || parseInt(saves.Intelligence, 10) > calcDndMod(intEl?.value)));
    if (wisSaveChk) wisSaveChk.checked = Boolean(saves.Wisdom && (saves.Wisdom.proficient || parseInt(saves.Wisdom, 10) > calcDndMod(wisEl?.value)));
    if (chaSaveChk) chaSaveChk.checked = Boolean(saves.Charisma && (saves.Charisma.proficient || parseInt(saves.Charisma, 10) > calcDndMod(chaEl?.value)));

    // Senses
    const passPercep = document.getElementById("input-dnd-pass-percep");
    const passInsight = document.getElementById("input-dnd-pass-insight");
    const passInvest = document.getElementById("input-dnd-pass-invest");
    const dndSenses = document.getElementById("input-dnd-senses");
    const dndDefenses = document.getElementById("input-dnd-defenses");
    if (passPercep) passPercep.value = ch.passives?.perception ?? 12;
    if (passInsight) passInsight.value = ch.passives?.insight ?? 12;
    if (passInvest) passInvest.value = ch.passives?.investigation ?? 13;
    if (dndSenses) dndSenses.value = ch.senses || "";
    if (dndDefenses) dndDefenses.value = ch.defenses || ch.resistances || "";

    // Render Skills Grid
    renderDndSkillsGrid(ch.skills);

    // Proficiencies & Training
    const profArmorEl = document.getElementById("input-dnd-prof-armor");
    const profWpnEl = document.getElementById("input-dnd-prof-weapons");
    const profToolEl = document.getElementById("input-dnd-prof-tools");
    const profLangEl = document.getElementById("input-dnd-languages");
    if (profArmorEl) profArmorEl.value = ch.proficiencies_armor || "Heavy Armor, Light Armor, Medium Armor, Shields";
    if (profWpnEl) profWpnEl.value = ch.proficiencies_weapons || "Martial Weapons, Simple Weapons";
    if (profToolEl) profToolEl.value = ch.proficiencies_tools || "Dice Set";
    if (profLangEl) profLangEl.value = ch.proficiencies_languages || "Common, Draconic, Elvish";

    // Currency
    const cur = ch.currency || ch.native_system_data?.currency || {};
    const coinPp = document.getElementById("input-dnd-coin-pp");
    const coinGp = document.getElementById("input-dnd-coin-gp");
    const coinEp = document.getElementById("input-dnd-coin-ep");
    const coinSp = document.getElementById("input-dnd-coin-sp");
    const coinCp = document.getElementById("input-dnd-coin-cp");
    if (coinPp) coinPp.value = cur.pp !== undefined ? cur.pp : 0;
    if (coinGp) coinGp.value = cur.gp !== undefined ? cur.gp : (cur.gold !== undefined ? cur.gold : 13);
    if (coinEp) coinEp.value = cur.ep !== undefined ? cur.ep : 0;
    if (coinSp) coinSp.value = cur.sp !== undefined ? cur.sp : 0;
    if (coinCp) coinCp.value = cur.cp !== undefined ? cur.cp : 0;

    // Weapons, Inventory, Features, Spells
    const dndWpnsEl = document.getElementById("input-dnd-weapons");
    const dndInvEl = document.getElementById("input-dnd-inventory");
    const dndFeatsEl = document.getElementById("input-dnd-features");
    const dndSpellsEl = document.getElementById("input-dnd-spells");
    if (dndWpnsEl) dndWpnsEl.value = wpnsFormatted;
    if (dndInvEl) dndInvEl.value = invFormatted;
    if (dndFeatsEl) dndFeatsEl.value = featsFormatted;
    if (dndSpellsEl) dndSpellsEl.value = spellsFormatted;

    const spellAbilityEl = document.getElementById("select-dnd-spell-ability");
    const spellDcEl = document.getElementById("input-dnd-spell-dc");
    const spellAtkEl = document.getElementById("input-dnd-spell-atk");
    if (spellAbilityEl) {
        const rawSa = (ch.spellcasting_ability || "").trim().toUpperCase();
        if (rawSa.includes("INT")) spellAbilityEl.value = "INT";
        else if (rawSa.includes("WIS")) spellAbilityEl.value = "WIS";
        else if (rawSa.includes("CHA")) spellAbilityEl.value = "CHA";
        else spellAbilityEl.value = "none";
    }
    if (spellDcEl && ch.spell_save_dc) {
        spellDcEl.value = ch.spell_save_dc;
        spellDcEl.dataset.customized = "true";
    }
    if (spellAtkEl && ch.spell_attack_bonus) {
        spellAtkEl.value = ch.spell_attack_bonus;
        spellAtkEl.dataset.customized = "true";
    }

    // Staged weapons array for structured editing (filter out any cantrip/spell attacks)
    const spellNameSet = new Set((ch.spells || []).map(s => (typeof s === "object" && s ? s.name : String(s)).trim().toLowerCase()));
    stagedCharacterWeapons = (Array.isArray(ch.weapons) ? JSON.parse(JSON.stringify(ch.weapons)) : []).filter(w => {
        const wName = (typeof w === "object" && w ? (w.name || "") : String(w)).trim().toLowerCase();
        return !spellNameSet.has(wName);
    });
    renderStagedWeapons();

    // Staged spells array for structured editing
    const rawSpells = Array.isArray(ch.spells) ? ch.spells : [];
    stagedCharacterSpells = rawSpells.map(s => {
        if (typeof s === "object" && s !== null) return JSON.parse(JSON.stringify(s));
        const sStr = String(s).trim();
        const dmgM = sStr.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i);
        const dcM = sStr.match(/(DC\s*\d+\s*[A-Za-z]+|\+\d+)/i);
        return {
            name: sStr.replace(/\s*\([^)]*\)/g, "").trim() || sStr,
            level: "Cantrip/Spell",
            atk_bonus: dcM ? dcM[0] : "",
            damage: dmgM ? dmgM[0] : "",
            range: "60 ft.",
            notes: ""
        };
    });
    renderStagedSpells();

    // Recalculate D&D stats
    updateDndCalculations();

    // Populate Pathfinder 2E View Fields
    const pfClassEl = document.getElementById("input-pf-class-level");
    const pfAncestryEl = document.getElementById("input-pf-ancestry");
    const pfBgEl = document.getElementById("input-pf-background");
    const pfXpEl = document.getElementById("input-pf-xp");
    const pfStrEl = document.getElementById("input-pf-str");
    const pfDexEl = document.getElementById("input-pf-dex");
    const pfConEl = document.getElementById("input-pf-con");
    const pfIntEl = document.getElementById("input-pf-int");
    const pfWisEl = document.getElementById("input-pf-wis");
    const pfChaEl = document.getElementById("input-pf-cha");
    const pfAcEl = document.getElementById("input-pf-ac");
    const pfHpEl = document.getElementById("input-pf-max-hp");
    const pfSpeedEl = document.getElementById("input-pf-speed");
    const pfPercepEl = document.getElementById("input-pf-perception");
    const pfFortEl = document.getElementById("input-pf-save-fort");
    const pfRefEl = document.getElementById("input-pf-save-ref");
    const pfWillEl = document.getElementById("input-pf-save-will");
    const pfStrikesEl = document.getElementById("input-pf-strikes");
    const pfSkillsEl = document.getElementById("input-pf-skills");
    const pfInvEl = document.getElementById("input-pf-inventory");

    if (pfClassEl) pfClassEl.value = ch.class_and_level || ch.archetype || "Fighter 1";
    if (pfAncestryEl) pfAncestryEl.value = ch.species || ch.race || "";
    if (pfBgEl) pfBgEl.value = ch.background || "";
    if (pfXpEl) pfXpEl.value = ch.experience_points ?? 0;
    if (pfStrEl) pfStrEl.value = attrs.Strength ?? attrs.Might ?? 18;
    if (pfDexEl) pfDexEl.value = attrs.Dexterity ?? attrs.Agility ?? 14;
    if (pfConEl) pfConEl.value = attrs.Constitution ?? attrs.Vigor ?? 14;
    if (pfIntEl) pfIntEl.value = attrs.Intelligence ?? attrs.Intellect ?? 10;
    if (pfWisEl) pfWisEl.value = attrs.Wisdom ?? attrs.Presence ?? 12;
    if (pfChaEl) pfChaEl.value = attrs.Charisma ?? attrs.Presence ?? 10;
    if (pfAcEl) pfAcEl.value = ch.armor_class ?? ch.vitals?.guard?.current ?? 18;
    if (pfHpEl) pfHpEl.value = ch.vitals?.health?.max ?? 20;
    if (pfSpeedEl) pfSpeedEl.value = ch.speed || "25 ft.";
    if (pfPercepEl) pfPercepEl.value = ch.initiative || (ch.passives?.perception ? `+${ch.passives.perception - 10}` : "+5");
    if (pfFortEl) pfFortEl.value = saves.Fortitude || saves.Constitution || "+7";
    if (pfRefEl) pfRefEl.value = saves.Reflex || saves.Dexterity || "+5";
    if (pfWillEl) pfWillEl.value = saves.Will || saves.Wisdom || "+4";
    if (pfStrikesEl) pfStrikesEl.value = wpnsFormatted;
    if (pfSkillsEl) pfSkillsEl.value = typeof ch.skills === "object" ? Object.entries(ch.skills).map(([k, v]) => `${k} (${v})`).join(", ") : (ch.proficiencies || "");
    if (pfInvEl) pfInvEl.value = invFormatted;
    const pfFeatsEl = document.getElementById("input-pf-features");
    const pfSpellsEl = document.getElementById("input-pf-spells");
    if (pfFeatsEl) pfFeatsEl.value = featsFormatted;
    if (pfSpellsEl) pfSpellsEl.value = spellsFormatted;

    // Populate Cyberpunk RED View Fields
    const cybRoleEl = document.getElementById("input-cyber-role");
    const cybOriginEl = document.getElementById("input-cyber-origin");
    const cybRepEl = document.getElementById("input-cyber-rep");
    const cybIpEl = document.getElementById("input-cyber-ip");
    const cybIntEl = document.getElementById("input-cyber-int");
    const cybRefEl = document.getElementById("input-cyber-ref");
    const cybDexEl = document.getElementById("input-cyber-dex");
    const cybTechEl = document.getElementById("input-cyber-tech");
    const cybCoolEl = document.getElementById("input-cyber-cool");
    const cybWillEl = document.getElementById("input-cyber-will");
    const cybLuckEl = document.getElementById("input-cyber-luck");
    const cybMoveEl = document.getElementById("input-cyber-move");
    const cybBodyEl = document.getElementById("input-cyber-body");
    const cybEmpEl = document.getElementById("input-cyber-emp");
    const cybHpEl = document.getElementById("input-cyber-hp");
    const cybHeadSpEl = document.getElementById("input-cyber-head-sp");
    const cybBodySpEl = document.getElementById("input-cyber-body-sp");
    const cybHumanityEl = document.getElementById("input-cyber-humanity");
    const cybWpnsEl = document.getElementById("input-cyber-weapons");
    const cybEbEl = document.getElementById("input-cyber-eb");
    const cybWareEl = document.getElementById("input-cyber-cyberware");
    const cybInvEl = document.getElementById("input-cyber-inventory");

    if (cybRoleEl) cybRoleEl.value = ch.archetype || "Solo 4";
    if (cybOriginEl) cybOriginEl.value = ch.background || ch.origin || "Night City";
    if (cybRepEl) cybRepEl.value = ch.reputation ?? ch.rep ?? 3;
    if (cybIpEl) cybIpEl.value = ch.experience_points ?? 0;
    if (cybIntEl) cybIntEl.value = attrs.INT ?? attrs.Intelligence ?? attrs.Intellect ?? 7;
    if (cybRefEl) cybRefEl.value = attrs.REF ?? attrs.Dexterity ?? attrs.Agility ?? 8;
    if (cybDexEl) cybDexEl.value = attrs.DEX ?? attrs.Dexterity ?? attrs.Agility ?? 8;
    if (cybTechEl) cybTechEl.value = attrs.TECH ?? 5;
    if (cybCoolEl) cybCoolEl.value = attrs.COOL ?? attrs.Charisma ?? attrs.Presence ?? 6;
    if (cybWillEl) cybWillEl.value = attrs.WILL ?? attrs.Wisdom ?? 6;
    if (cybLuckEl) cybLuckEl.value = attrs.LUCK ?? 7;
    if (cybMoveEl) cybMoveEl.value = attrs.MOVE ?? 6;
    if (cybBodyEl) cybBodyEl.value = attrs.BODY ?? attrs.Constitution ?? attrs.Strength ?? attrs.Might ?? 8;
    if (cybEmpEl) cybEmpEl.value = attrs.EMP ?? 6;
    if (cybHpEl) cybHpEl.value = ch.vitals?.health?.max ?? 40;
    if (cybHeadSpEl) cybHeadSpEl.value = ch.head_sp ?? 11;
    if (cybBodySpEl) cybBodySpEl.value = ch.body_sp ?? ch.vitals?.guard?.current ?? 11;
    if (cybHumanityEl) cybHumanityEl.value = ch.humanity ?? 48;
    if (cybWpnsEl) cybWpnsEl.value = wpnsFormatted;
    if (cybEbEl) cybEbEl.value = ch.currency?.eb ?? 850;
    if (cybWareEl) cybWareEl.value = featsFormatted;
    if (cybInvEl) cybInvEl.value = invFormatted;

    // Populate Call of Cthulhu 7E View Fields
    const cocOccEl = document.getElementById("input-coc-occupation");
    const cocAgeEl = document.getElementById("input-coc-age");
    const cocSexEl = document.getElementById("input-coc-sex");
    const cocResEl = document.getElementById("input-coc-residence");
    const cocBirthEl = document.getElementById("input-coc-birthplace");

    if (cocOccEl) cocOccEl.value = ch.archetype || ch.occupation || ch.native_system_data?.occupation || "History Student";
    if (cocAgeEl) cocAgeEl.value = ch.age ?? ch.native_system_data?.age ?? 20;
    if (cocSexEl) cocSexEl.value = ch.sex || ch.native_system_data?.sex || "";
    if (cocResEl) cocResEl.value = ch.residence || ch.native_system_data?.residence || "";
    if (cocBirthEl) cocBirthEl.value = ch.birthplace || ch.native_system_data?.birthplace || "";

    const cocStrEl = document.getElementById("input-coc-str");
    const cocConEl = document.getElementById("input-coc-con");
    const cocSizEl = document.getElementById("input-coc-siz");
    const cocDexEl = document.getElementById("input-coc-dex");
    const cocAppEl = document.getElementById("input-coc-app");
    const cocIntEl = document.getElementById("input-coc-int");
    const cocPowEl = document.getElementById("input-coc-pow");
    const cocEduEl = document.getElementById("input-coc-edu");
    const cocMoveEl = document.getElementById("input-coc-move");

    const natChar = ch.native_system_data?.characteristics || {};
    if (cocStrEl) cocStrEl.value = attrs.STR ?? natChar.STR ?? attrs.Might ?? 70;
    if (cocConEl) cocConEl.value = attrs.CON ?? natChar.CON ?? attrs.Vigor ?? 80;
    if (cocSizEl) cocSizEl.value = attrs.SIZ ?? natChar.SIZ ?? 50;
    if (cocDexEl) cocDexEl.value = attrs.DEX ?? natChar.DEX ?? attrs.Agility ?? 40;
    if (cocAppEl) cocAppEl.value = attrs.APP ?? natChar.APP ?? attrs.Presence ?? 50;
    if (cocIntEl) cocIntEl.value = attrs.INT ?? natChar.INT ?? attrs.Intellect ?? 60;
    if (cocPowEl) cocPowEl.value = attrs.POW ?? natChar.POW ?? 50;
    if (cocEduEl) cocEduEl.value = attrs.EDU ?? natChar.EDU ?? 60;
    if (cocMoveEl) cocMoveEl.value = attrs.Move ?? natChar.Move ?? ch.move_rate ?? 8;

    const cocCurHpEl = document.getElementById("input-coc-cur-hp");
    const cocMaxHpEl = document.getElementById("input-coc-max-hp");
    const cocMajorWoundChk = document.getElementById("chk-coc-major-wound");
    const cocCurSanEl = document.getElementById("input-coc-cur-san");
    const cocMaxSanEl = document.getElementById("input-coc-max-san");
    const cocTempInsaneChk = document.getElementById("chk-coc-temp-insane");
    const cocIndefInsaneChk = document.getElementById("chk-coc-indef-insane");
    const cocLuckEl = document.getElementById("input-coc-luck");
    const cocCurMpEl = document.getElementById("input-coc-cur-mp");
    const cocMaxMpEl = document.getElementById("input-coc-max-mp");

    const cMeters = ch.custom_meters || {};
    const natVitals = ch.native_system_data?.vitals || {};
    if (cocCurHpEl) cocCurHpEl.value = ch.vitals?.health?.current ?? natVitals.hit_points?.current ?? ch.current_health ?? 13;
    if (cocMaxHpEl) cocMaxHpEl.value = ch.vitals?.health?.max ?? natVitals.hit_points?.max ?? ch.max_health ?? 13;
    if (cocMajorWoundChk) cocMajorWoundChk.checked = Boolean(ch.major_wound || natVitals.major_wound);
    if (cocCurSanEl) cocCurSanEl.value = cMeters.sanity?.current ?? natVitals.sanity?.current ?? ch.sanity ?? attrs.SAN ?? 50;
    if (cocMaxSanEl) cocMaxSanEl.value = cMeters.sanity?.max ?? natVitals.sanity?.max ?? ch.max_sanity ?? 99;
    if (cocTempInsaneChk) cocTempInsaneChk.checked = Boolean(ch.temp_insane || natVitals.temp_insane);
    if (cocIndefInsaneChk) cocIndefInsaneChk.checked = Boolean(ch.indef_insane || natVitals.indef_insane);
    if (cocLuckEl) cocLuckEl.value = cMeters.luck?.current ?? natVitals.luck?.current ?? ch.luck ?? 55;
    if (cocCurMpEl) cocCurMpEl.value = cMeters.magic_points?.current ?? natVitals.magic_points?.current ?? ch.magic_points ?? 10;
    if (cocMaxMpEl) cocMaxMpEl.value = cMeters.magic_points?.max ?? natVitals.magic_points?.max ?? 10;

    const cocDbEl = document.getElementById("input-coc-db");
    const cocBuildEl = document.getElementById("input-coc-build");
    const cocDodgeEl = document.getElementById("input-coc-dodge");
    const natCombat = ch.native_system_data?.combat || {};
    if (cocDbEl) cocDbEl.value = ch.damage_bonus || natCombat.damage_bonus || "none";
    if (cocBuildEl) cocBuildEl.value = ch.build ?? natCombat.build ?? 0;
    if (cocDodgeEl) cocDodgeEl.value = ch.dodge ?? natCombat.dodge ?? ch.vitals?.guard?.current ?? 40;

    // Backstory
    const cocDescEl = document.getElementById("input-coc-desc");
    const cocIdeoEl = document.getElementById("input-coc-ideology");
    const cocPeopEl = document.getElementById("input-coc-people");
    const cocLocEl = document.getElementById("input-coc-locations");
    const cocPossEl = document.getElementById("input-coc-possessions");
    const cocTraitsEl = document.getElementById("input-coc-traits");

    const natBackstory = ch.native_system_data?.backstory || {};
    let bDesc = ch.personal_description || natBackstory.personal_description || "";
    let bIdeo = ch.ideology || natBackstory.ideology || "";
    let bPeop = ch.significant_people || natBackstory.significant_people || "";
    let bLoc = ch.meaningful_locations || natBackstory.meaningful_locations || "";
    let bPoss = ch.treasured_possessions || natBackstory.treasured_possessions || "";
    let bTraits = ch.backstory_traits || natBackstory.traits || "";

    const allFeats = extractCharacterFeatures(ch);
    allFeats.forEach(f => {
        const str = typeof f === "string" ? f : (f.name + ": " + (f.description || ""));
        const low = str.toLowerCase();
        if (low.includes("personal description:") && !bDesc) bDesc = str.split(/:\s*/)[1] || "";
        else if (low.includes("ideology") && !bIdeo) bIdeo = str.split(/:\s*/)[1] || "";
        else if (low.includes("significant people:") && !bPeop) bPeop = str.split(/:\s*/)[1] || "";
        else if (low.includes("meaningful locations:") && !bLoc) bLoc = str.split(/:\s*/)[1] || "";
        else if (low.includes("treasured possessions:") && !bPoss) bPoss = str.split(/:\s*/)[1] || "";
        else if (low.includes("traits:") && !bTraits) bTraits = str.split(/:\s*/)[1] || "";
    });

    if (cocDescEl) cocDescEl.value = bDesc;
    if (cocIdeoEl) cocIdeoEl.value = bIdeo;
    if (cocPeopEl) cocPeopEl.value = bPeop;
    if (cocLocEl) cocLocEl.value = bLoc;
    if (cocPossEl) cocPossEl.value = bPoss;
    if (cocTraitsEl) cocTraitsEl.value = bTraits;

    // Gear & Assets
    const cocInvEl = document.getElementById("input-coc-inventory");
    const cocCrEl = document.getElementById("input-coc-credit-rating");
    const cocCashEl = document.getElementById("input-coc-cash");
    const cocAssetsEl = document.getElementById("input-coc-assets");
    if (cocInvEl) cocInvEl.value = invFormatted;
    if (cocCrEl) cocCrEl.value = ch.credit_rating ?? ch.native_system_data?.credit_rating ?? 40;
    if (cocCashEl) cocCashEl.value = ch.currency?.cash || ch.cash || "$80";
    if (cocAssetsEl) cocAssetsEl.value = ch.currency?.assets || ch.assets || "$2,000";

    // Skills Grid & Half/Fifth Calculations
    const cocSkills = ch.skills || ch.native_system_data?.skills || {};
    renderCocSkillsGrid(cocSkills);
    updateCocCalculations();

    // Also populate Agnostic Fallback Inputs
    const archEl = document.getElementById("input-rp-char-archetype");
    const hpEl = document.getElementById("input-rp-char-hp");
    const guardEl = document.getElementById("input-rp-char-guard");
    const mgtEl = document.getElementById("input-rp-attr-might");
    const agiEl = document.getElementById("input-rp-attr-agility");
    const vigEl = document.getElementById("input-rp-attr-vigor");
    const intElAgn = document.getElementById("input-rp-attr-intellect");
    const preEl = document.getElementById("input-rp-attr-presence");
    const lvlEl = document.getElementById("input-rp-char-level");
    const xpEl = document.getElementById("input-rp-char-xp");
    const wpnsElAgn = document.getElementById("input-rp-char-weapons");
    const invElAgn = document.getElementById("input-rp-char-inventory");
    const spellsElAgn = document.getElementById("input-rp-char-spells");
    const featsElAgn = document.getElementById("input-rp-char-features");
    const skillsElAgn = document.getElementById("input-rp-char-skills");

    if (archEl) archEl.value = ch.archetype || "";
    if (hpEl) hpEl.value = ch.vitals?.health?.max ?? 25;
    if (guardEl) guardEl.value = ch.vitals?.guard?.current ?? 14;
    if (mgtEl) mgtEl.value = attrs.Strength ?? attrs.Might ?? 10;
    if (agiEl) agiEl.value = attrs.Dexterity ?? attrs.Agility ?? 10;
    if (vigEl) vigEl.value = attrs.Constitution ?? attrs.Vigor ?? 10;
    if (intElAgn) intElAgn.value = attrs.Intelligence ?? attrs.Intellect ?? 10;
    if (preEl) preEl.value = attrs.Charisma ?? attrs.Presence ?? 10;
    if (lvlEl) lvlEl.value = ch.level ?? 1;
    if (xpEl) xpEl.value = ch.experience_points ?? 0;
    if (wpnsElAgn) wpnsElAgn.value = wpnsFormatted;
    if (invElAgn) invElAgn.value = invFormatted;
    if (spellsElAgn) spellsElAgn.value = spellsFormatted;
    if (featsElAgn) featsElAgn.value = featsFormatted;
    if (skillsElAgn) skillsElAgn.value = typeof ch.skills === "object" ? Object.entries(ch.skills).map(([k, v]) => `${k} (${v})`).join(", ") : (ch.proficiencies || "");

    const charModal = document.getElementById("modal-add-character");
    if (charModal) charModal.classList.remove("hidden");
}

// --- Campaign Document Upload & Review Functions ---
let pendingRulesetRequest = null;

function promptRulesetSelection(requestData, onSelectedCallback) {
    pendingRulesetRequest = { requestData, onSelectedCallback };
    const modal = document.getElementById("ruleset-picker-modal");
    const fnEl = document.getElementById("ruleset-picker-filename");
    if (fnEl) fnEl.textContent = requestData.filename || (requestData.character && requestData.character.name) || "Character Sheet";
    if (modal) modal.style.display = "flex";
}

function closeRulesetPickerModal() {
    const modal = document.getElementById("ruleset-picker-modal");
    if (modal) modal.style.display = "none";
    pendingRulesetRequest = null;
}

async function handleUploadRpFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    const categorySelect = document.getElementById("rp-doc-category-select");
    const category = categorySelect ? categorySelect.value : "ruleset";

    for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = async (ev) => {
                try {
                    const base64Data = ev.target.result;
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.upload_ttrpg_document) {
                        const res = await window.pywebview.api.upload_ttrpg_document(
                            file.name,
                            base64Data,
                            category,
                            ttrpgData.active_campaign
                        );
                        if (res && res.status === "requires_ruleset_selection") {
                            promptRulesetSelection(res, async (selectedRuleset, req) => {
                                const retryRes = await window.pywebview.api.upload_ttrpg_document(
                                    req.filename,
                                    req.base64_content,
                                    req.doc_category || "character_sheet",
                                    req.campaign_id,
                                    selectedRuleset
                                );
                                if (retryRes && retryRes.status === "success") {
                                    const importedNames = (retryRes.imported_characters && retryRes.imported_characters.length > 0)
                                        ? retryRes.imported_characters.map(c => typeof c === "object" ? (c.name || c.id) : c).join(", ")
                                        : "character";
                                    if (typeof renderRolePlayStoryBubble === "function") {
                                        renderRolePlayStoryBubble({
                                            type: "system",
                                            content: `👤 Imported ${importedNames} into Party Roster & Character Vault (${retryRes.ruleset || 'Hero'})! Sheet file removed from file list (one-and-done) to prevent overwriting evolved character data.`
                                        });
                                    }
                                    alert(`👤 Imported ${importedNames} into Party Roster & Character Vault (${retryRes.ruleset || 'Hero'})!`);
                                    await loadRolePlayUI(true);
                                }
                            });
                        } else if (res && res.status === "success" && res.removed_after_import) {
                            const importedNames = (res.imported_characters && res.imported_characters.length > 0)
                                ? res.imported_characters.map(c => typeof c === "object" ? (c.name || c.id) : c).join(", ")
                                : "character";
                            if (typeof renderRolePlayStoryBubble === "function") {
                                renderRolePlayStoryBubble({
                                    type: "system",
                                    content: `👤 Imported ${importedNames} into Party Roster & Character Vault (${res.ruleset || 'Hero'})! Sheet file removed from file list (one-and-done) to prevent overwriting evolved character data.`
                                });
                            }
                            alert(`👤 Imported ${importedNames} into Party Roster & Character Vault (${res.ruleset || 'Hero'})!`);
                        }
                    }
                } catch (err) {
                    console.error("[RolePlay] Error uploading document:", err);
                }
                resolve();
            };
            reader.onerror = () => resolve();
            reader.readAsDataURL(file);
        });
    }
    await loadRolePlayUI();
}

async function handlePreviewRpDocument(filename) {
    if (!filename) return;
    currentPreviewDocFilename = filename;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.read_ttrpg_document) {
            const res = await window.pywebview.api.read_ttrpg_document(filename, ttrpgData.active_campaign);
            const modal = document.getElementById("modal-rp-doc-preview");
            const titleEl = document.getElementById("modal-rp-doc-title");
            const contentEl = document.getElementById("modal-rp-doc-content");
            if (titleEl) titleEl.textContent = `📜 ${filename}`;
            if (contentEl) {
                contentEl.textContent = (res && res.status === "success")
                    ? (res.content || "(Empty document)")
                    : (res?.message || "Unable to read document.");
            }
            if (modal) modal.classList.remove("hidden");
        }
    } catch (e) {
        console.error("[RolePlay] Document preview error:", e);
    }
}

async function handleDeleteRpDocument(filename) {
    if (!filename) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_ttrpg_document) {
            await window.pywebview.api.delete_ttrpg_document(filename, ttrpgData.active_campaign);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Delete document error:", e);
    }
}

async function handleReviewSingleRpDocument(filename, category) {
    if (!filename) return;
    let promptText = `Please call \`read_campaign_document(filename='${filename}')\` to review our uploaded ${category.replace("_", " ")} document '${filename}'.`;
    if (category === "character_sheet") {
        promptText += ` Review this document to understand the character's background and abilities, without overwriting any active roster stats, and summarize the character for us!`;
    } else {
        promptText += ` Summarize the key rules, lore, or encounters and get ready to run it as our Dungeon Master (DM)!`;
    }
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.ask_aether_dm_review) {
            await window.pywebview.api.ask_aether_dm_review(promptText);
        }
    } catch (e) {
        console.error("[RolePlay] Ask DM review error:", e);
    }
}

async function handleBecomeDmClick() {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.ask_aether_dm_review) {
            await window.pywebview.api.ask_aether_dm_review(null);
        }
    } catch (e) {
        console.error("[RolePlay] Become DM error:", e);
    }
}

async function handleRefreshRosterAndFiles() {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.refresh_ttrpg_roster_and_files) {
            await window.pywebview.api.refresh_ttrpg_roster_and_files(ttrpgData.active_campaign);
        }
    } catch (e) {
        console.error("[RolePlay] Refresh roster & files error:", e);
    }
    await loadRolePlayUI();
}

async function handleDeleteActiveCampaign() {
    const activeId = ttrpgData.active_campaign;
    if (!activeId) return;
    const campaigns = ttrpgData.campaigns || {};
    const meta = campaigns[activeId] || ttrpgData.active_campaign_meta || {};
    const title = meta.title || activeId;
    if (!(await showAetherConfirm(`Delete campaign "${title}"? This cannot be undone.`, "Delete Campaign", "Delete", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_ttrpg_campaign) {
            await window.pywebview.api.delete_ttrpg_campaign(activeId);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Delete campaign error:", e);
    }
}

// Event hooks invoked by window.aetherUI.handleEvent
window.handleRoleplayDiceEvent = function(rollData) {
    appendDiceAuditEntry(rollData);
};

window.handleRoleplayInitiativeEvent = function() {
    loadRolePlayUI();
};

window.handleRoleplayStateEvent = function() {
    loadRolePlayUI();
};

window.handleRoleplayVaultEvent = function() {
    if (typeof refreshVaultModalUI === "function") {
        refreshVaultModalUI();
    }
    loadRolePlayUI();
};

window.handleRoleplayAdventureModeChanged = function(payload) {
    if (payload && typeof payload.active === "boolean") {
        isAdventureActive = payload.active;
        updateAdventureButtonState(isAdventureActive);
    }
};

window.handleRoleplayAssistantStatus = function(running) {
    if (!running) {
        isAdventureActive = false;
        updateAdventureButtonState(false);
    }
};

window.roleplayApp = {
    loadRolePlayUI,
    updateAdventureButtonState
};

function renderRolePlayStoryBubble(item) {
    const container = document.getElementById('rpStoryChatHistory');
    if (!container) return;

    const bubble = document.createElement('div');
    let bubbleType = item.type || 'system';
    if (item.type === 'assistant' || item.role === 'model') {
        bubbleType = 'gm';
    }
    const safeType = String(bubbleType).replace(/[^a-zA-Z0-9_-]/g, '');
    bubble.className = 'chat-bubble ' + safeType;

    const timeStr = item.timestamp 
        ? new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    let senderLabel = 'SYSTEM';
    if (item.type === 'user') {
        senderLabel = item.source === 'voice' ? 'PLAYER [VOICE]' : 'PLAYER [ACTION]';
    } else if (bubbleType === 'gm') {
        senderLabel = 'GAME MASTER';
    } else if (item.type === 'tool') {
        senderLabel = item.name || 'DICE / RULES';
    } else if (item.type === 'error') {
        senderLabel = 'ERROR';
    }

    const escape = (str) => {
        const div = document.createElement('div');
        div.innerText = str || '';
        return div.innerHTML;
    };

    bubble.innerHTML = `
        <div class="bubble-meta">
            <span class="sender-tag ${safeType}">${escape(senderLabel)}</span>
            <span class="timestamp">${escape(timeStr)}</span>
        </div>
        <div class="bubble-body">${escape(item.content)}</div>
    `;

    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;
}
window.renderRolePlayStoryBubble = renderRolePlayStoryBubble;

async function loadCampaignStoryHistory(cid) {
    const container = document.getElementById('rpStoryChatHistory');
    if (!container) return;
    container.innerHTML = '';

    if (!cid) {
        container.innerHTML = `<div class="chat-bubble system"><div class="bubble-body">Select or create a campaign to begin your tabletop adventure chronicle.</div></div>`;
        return;
    }

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.get_workspace_history) {
            const res = await window.pywebview.api.get_workspace_history('campaign', cid);
            if (res && res.history && res.history.length > 0) {
                res.history.forEach(t => renderRolePlayStoryBubble(t));
                return;
            }
        }
    } catch (e) {
        console.error('[RolePlay] Failed to load campaign story history:', e);
    }

    // Default system welcome bubble
    renderRolePlayStoryBubble({
        type: 'system',
        content: `Tabletop campaign chronicle active. Narratives, NPC dialogue, dice rolls, and combat events here are strictly isolated to this campaign. Speak into your microphone or state actions below to play with Aether as your Game Master!`
    });
}
window.loadCampaignStoryHistory = loadCampaignStoryHistory;

function initRolePlayStoryChatUI() {
    // Subnav pane switcher
    const rpTabs = document.querySelectorAll('#rp-subnav-tabs .rp-tab-btn');
    const ws = document.querySelector('.rp-workspace');
    const panes = {
        story: document.getElementById('rp-story-pane'),
        roster: document.getElementById('rp-roster-pane'),
        journal: document.getElementById('rp-journal-pane')
    };

    rpTabs.forEach(btn => {
        btn.onclick = () => {
            rpTabs.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const target = btn.dataset.rpPane || 'story';

            if (ws) {
                if (target === 'all') {
                    ws.classList.add('view-all');
                    Object.values(panes).forEach(p => { if (p) p.classList.add('active'); });
                } else {
                    ws.classList.remove('view-all');
                    Object.entries(panes).forEach(([k, p]) => {
                        if (p) {
                            if (k === target) p.classList.add('active');
                            else p.classList.remove('active');
                        }
                    });
                }
            }
        };
    });

    // In-Panel Story Form
    const storyForm = document.getElementById('rpStoryChatForm');
    const storyInput = document.getElementById('rpStoryChatInput');
    if (storyForm && storyInput) {
        storyForm.onsubmit = async (e) => {
            e.preventDefault();
            const text = storyInput.value.trim();
            const cid = ttrpgData.active_campaign;
            if (!text || !cid) return;
            storyInput.value = '';

            try {
                if (window.pywebview && window.pywebview.api) {
                    if (window.pywebview.api.send_workspace_message) {
                        await window.pywebview.api.send_workspace_message('campaign', cid, text);
                    } else if (window.pywebview.api.send_chat_message) {
                        await window.pywebview.api.send_chat_message(text);
                    }
                }
            } catch (err) {
                renderRolePlayStoryBubble({ type: 'error', content: `Failed to send action: ${err}` });
            }
        };
    }

    // Story quick chips
    document.querySelectorAll('.rp-story-chips .rp-chip').forEach(chip => {
        chip.onclick = () => {
            const action = chip.dataset.action;
            if (storyInput && action) {
                storyInput.value = action;
                if (storyForm) storyForm.dispatchEvent(new Event('submit', { cancelable: true }));
            }
        };
    });

    // Quick d20 Check
    const quickD20Btn = document.getElementById('btn-rp-quick-d20');
    if (quickD20Btn) {
        quickD20Btn.onclick = async () => {
            const cid = ttrpgData.active_campaign;
            if (!cid) return;
            const rollVal = Math.floor(Math.random() * 20) + 1;
            const text = `I roll a 1d20 check. Result: [${rollVal}]!`;
            if (storyInput) {
                storyInput.value = text;
                if (storyForm) storyForm.dispatchEvent(new Event('submit', { cancelable: true }));
            }
        };
    }

    // New Scene Button
    const newSceneBtn = document.getElementById('btn-rp-new-scene');
    if (newSceneBtn) {
        newSceneBtn.onclick = async () => {
            const cid = ttrpgData.active_campaign;
            if (!cid) return;
            if (!(await showAetherConfirm('Start a new scene? This signals the Game Master to set the stage for a new encounter.', 'New Scene', 'Start Scene', false))) return;
            try {
                renderRolePlayStoryBubble({
                    type: 'system',
                    content: '✨ --- NEW SCENE / ENCOUNTER --- ✨'
                });
                if (window.pywebview && window.pywebview.api && window.pywebview.api.send_workspace_message) {
                    await window.pywebview.api.send_workspace_message('campaign', cid, 'Begin the next scene or transition.');
                }
            } catch (err) {
                console.error('[RolePlay] Error beginning new scene:', err);
            }
        };
    }

    // Clear Story Transcript Button
    const clearStoryBtn = document.getElementById('btn-rp-clear-story');
    if (clearStoryBtn) {
        clearStoryBtn.onclick = async () => {
            const cid = ttrpgData.active_campaign;
            if (!cid) return;
            if (!(await showAetherConfirm('Clear all story and dialogue logs for this campaign?', 'Clear Logs', 'Clear Logs', true))) return;
            try {
                if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_workspace_history) {
                    await window.pywebview.api.clear_workspace_history('campaign', cid);
                }
                const container = document.getElementById('rpStoryChatHistory');
                if (container) container.innerHTML = '';
                renderRolePlayStoryBubble({
                    type: 'system',
                    content: 'Campaign story transcript cleared.'
                });
            } catch (err) {
                console.error('[RolePlay] Error clearing story transcript:', err);
            }
        };
    }
}

function initDiceModeToggle() {
    const btnDigital = document.getElementById('btn-dice-source-digital');
    const btnPhysical = document.getElementById('btn-dice-source-physical');

    const handleSwitch = async (mode) => {
        updateDiceModeUI(mode);
        const cid = ttrpgData.active_campaign;
        if (ttrpgData.campaigns && ttrpgData.campaigns[cid]) {
            ttrpgData.campaigns[cid].dice_mode = mode;
        }
        if (ttrpgData.active_campaign_meta) {
            ttrpgData.active_campaign_meta.dice_mode = mode;
        }
        ttrpgData.dice_mode = mode;

        try {
            if (window.pywebview && window.pywebview.api && window.pywebview.api.set_ttrpg_dice_mode) {
                await window.pywebview.api.set_ttrpg_dice_mode(mode, cid);
            }
            if (typeof renderRolePlayStoryBubble === 'function') {
                renderRolePlayStoryBubble({
                    type: 'system',
                    content: mode === 'physical'
                        ? '🎲 Dice Mode: Physical Dice active. Players roll at the table and tell Aether their results!'
                        : '⚡ Dice Mode: Digital Dice active. Aether will roll dice automatically for player actions.'
                });
            }
        } catch (err) {
            console.error('[RolePlay] Error updating dice mode:', err);
        }
    };

    if (btnDigital) {
        btnDigital.onclick = () => handleSwitch('digital');
    }
    if (btnPhysical) {
        btnPhysical.onclick = () => handleSwitch('physical');
    }
}

window.addEventListener("pywebviewready", () => {
    loadRolePlayUI();
});

document.addEventListener("DOMContentLoaded", () => {
    initRolePlayStoryChatUI();
    initDiceModeToggle();
    loadRolePlayUI();
    // Quick Dice Buttons (d4, d6, d8, d10, d12, d20, d100)
    document.querySelectorAll(".dice-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const sides = btn.getAttribute("data-sides") || "20";
            handleRollDiceFormula(`1d${sides}`);
        });
    });

    // Dice Roll Mode Chips (Normal / Advantage / Disadvantage)
    document.querySelectorAll(".dice-mode-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            document.querySelectorAll(".dice-mode-chip").forEach(c => c.classList.remove("active"));
            chip.classList.add("active");
            activeDiceRollMode = chip.getAttribute("data-roll-mode") || "normal";
        });
    });

    // Manual Dice Formula Submit
    const rollSubmitBtn = document.getElementById("roll-submit-btn");
    const formulaInput = document.getElementById("dice-formula-input");
    if (rollSubmitBtn && formulaInput) {
        rollSubmitBtn.addEventListener("click", () => {
            handleRollDiceFormula(formulaInput.value || "1d20");
        });
        formulaInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                handleRollDiceFormula(formulaInput.value || "1d20");
            }
        });
    }

    // Next Turn Button
    const nextTurnBtn = document.getElementById("next-turn-btn");
    if (nextTurnBtn) {
        nextTurnBtn.addEventListener("click", handleAdvanceTurn);
    }

    // Add Initiative Combatant Button
    const addInitBtn = document.getElementById("rp-add-init-btn");
    if (addInitBtn) {
        addInitBtn.addEventListener("click", handleAddInitiativeItem);
    }

    // Campaign Selector Dropdown
    const campaignSelect = document.getElementById("rp-campaign-select");
    if (campaignSelect) {
        campaignSelect.addEventListener("change", async () => {
            const cid = campaignSelect.value;
            if (cid && window.pywebview && window.pywebview.api && window.pywebview.api.set_active_campaign) {
                await window.pywebview.api.set_active_campaign(cid);
                await loadRolePlayUI();
            }
        });
    }

    // Campaign Document Upload Controls & Dropzone
    const uploadDocBtn = document.getElementById("btn-upload-rp-doc");
    const docFileInput = document.getElementById("rp-doc-file-input");
    const docDropzone = document.getElementById("rp-doc-dropzone");
    const becomeDmBtn = document.getElementById("btn-become-dm");

    if (uploadDocBtn && docFileInput) {
        uploadDocBtn.addEventListener("click", () => docFileInput.click());
        docFileInput.addEventListener("change", async (e) => {
            if (e.target.files && e.target.files.length > 0) {
                await handleUploadRpFiles(e.target.files);
                docFileInput.value = "";
            }
        });
    }

    if (docDropzone && docFileInput) {
        docDropzone.addEventListener("click", () => docFileInput.click());
        docDropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            docDropzone.classList.add("drag-over");
        });
        docDropzone.addEventListener("dragleave", () => {
            docDropzone.classList.remove("drag-over");
        });
        docDropzone.addEventListener("drop", async (e) => {
            e.preventDefault();
            docDropzone.classList.remove("drag-over");
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                await handleUploadRpFiles(e.dataTransfer.files);
            }
        });
    }

    if (becomeDmBtn) {
        becomeDmBtn.addEventListener("click", handleBecomeDmClick);
    }

    // Refresh Buttons (Party Roster & DM Library)
    const refreshRosterBtn = document.getElementById("refresh-roster-btn");
    const refreshDocsBtn = document.getElementById("refresh-docs-btn");
    const deleteCampBtn = document.getElementById("delete-campaign-btn");

    if (refreshRosterBtn) {
        refreshRosterBtn.addEventListener("click", handleRefreshRosterAndFiles);
    }
    if (refreshDocsBtn) {
        refreshDocsBtn.addEventListener("click", handleRefreshRosterAndFiles);
    }
    if (deleteCampBtn) {
        deleteCampBtn.addEventListener("click", handleDeleteActiveCampaign);
    }

    // Document Preview Modal Controls
    const docPreviewModal = document.getElementById("modal-rp-doc-preview");
    const closeDocPreviewBtn = document.getElementById("btn-close-rp-doc-modal");
    const closeDocFooterBtn = document.getElementById("btn-close-rp-doc-footer");
    const askDmThisDocBtn = document.getElementById("btn-ask-dm-this-doc");

    if (closeDocPreviewBtn && docPreviewModal) {
        closeDocPreviewBtn.addEventListener("click", () => docPreviewModal.classList.add("hidden"));
    }
    if (closeDocFooterBtn && docPreviewModal) {
        closeDocFooterBtn.addEventListener("click", () => docPreviewModal.classList.add("hidden"));
    }
    if (askDmThisDocBtn && docPreviewModal) {
        askDmThisDocBtn.addEventListener("click", async () => {
            docPreviewModal.classList.add("hidden");
            if (currentPreviewDocFilename) {
                const docObj = (ttrpgData.state && ttrpgData.state.documents && ttrpgData.state.documents[currentPreviewDocFilename]) || {};
                await handleReviewSingleRpDocument(currentPreviewDocFilename, docObj.category || "ruleset");
            }
        });
    }

    // New / Edit Campaign Modal Controls
    const newCampBtn = document.getElementById("new-campaign-btn");
    const editCampBtn = document.getElementById("edit-campaign-btn");
    const campModal = document.getElementById("modal-new-campaign");
    const campHeading = document.getElementById("modal-campaign-heading");
    const closeCampBtn = document.getElementById("btn-close-campaign-modal");
    const cancelCampBtn = document.getElementById("btn-cancel-campaign");
    const confirmCampBtn = document.getElementById("btn-confirm-campaign");
    const deleteCampModalBtn = document.getElementById("btn-delete-campaign-modal");
    const modalCampFilesSection = document.getElementById("modal-camp-files-section");
    const modalCampDropzone = document.getElementById("modal-camp-dropzone");
    const modalCampFileInput = document.getElementById("input-modal-camp-files");
    const modalCampStagedList = document.getElementById("modal-camp-staged-list");

    let stagedCampaignModalFiles = [];

    function renderStagedCampaignFiles() {
        if (!modalCampStagedList) return;
        modalCampStagedList.innerHTML = "";
        if (stagedCampaignModalFiles.length === 0) {
            modalCampStagedList.style.display = "none";
            return;
        }
        modalCampStagedList.style.display = "flex";

        stagedCampaignModalFiles.forEach((item, idx) => {
            const itemRow = document.createElement("div");
            itemRow.className = "rp-staged-file-item";

            const sizeKb = Math.round(item.size / 1024);
            const sizeStr = sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${sizeKb} KB`;

            let icon = "📜";
            if (item.category === "character_sheet") icon = "👤";
            else if (item.category === "ruleset") icon = "📖";
            else if (item.category === "notes") icon = "📝";

            itemRow.innerHTML = `
                <div class="rp-staged-file-info">
                    <span>${icon}</span>
                    <span class="rp-staged-file-name" title="${item.name}">${item.name}</span>
                    <span class="rp-staged-file-size">(${sizeStr})</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <select class="rp-staged-cat-select" data-idx="${idx}">
                        <option value="campaign" ${item.category === "campaign" ? "selected" : ""}>Adventure</option>
                        <option value="ruleset" ${item.category === "ruleset" ? "selected" : ""}>Ruleset</option>
                        <option value="character_sheet" ${item.category === "character_sheet" ? "selected" : ""}>Character</option>
                        <option value="notes" ${item.category === "notes" ? "selected" : ""}>Notes</option>
                    </select>
                    <button type="button" class="rp-staged-remove-btn" data-idx="${idx}" title="Remove file">✕</button>
                </div>
            `;

            const catSelect = itemRow.querySelector(".rp-staged-cat-select");
            if (catSelect) {
                catSelect.addEventListener("change", (e) => {
                    stagedCampaignModalFiles[idx].category = e.target.value;
                    renderStagedCampaignFiles();
                });
            }

            const rmBtn = itemRow.querySelector(".rp-staged-remove-btn");
            if (rmBtn) {
                rmBtn.addEventListener("click", () => {
                    stagedCampaignModalFiles.splice(idx, 1);
                    renderStagedCampaignFiles();
                });
            }

            modalCampStagedList.appendChild(itemRow);
        });
    }

    async function stageFilesForNewCampaign(fileList) {
        if (!fileList || fileList.length === 0) return;
        const titleInput = document.getElementById("input-rp-camp-title");

        for (let i = 0; i < fileList.length; i++) {
            const file = fileList[i];
            const lower = file.name.toLowerCase();

            // Auto-detect category
            let cat = "campaign";
            if (lower.includes("sheet") || lower.includes("char") || lower.includes("pc") || lower.includes("retholtz")) {
                cat = "character_sheet";
            } else if (lower.includes("rule") || lower.includes("handbook") || lower.includes("srd") || lower.includes("basic")) {
                cat = "ruleset";
            }

            // Auto-prefill Title if empty
            if (titleInput && !titleInput.value.trim() && (cat === "campaign" || cat === "ruleset")) {
                let stem = file.name.replace(/\.[^/.]+$/, "");
                stem = stem.replace(/[\d_\-]+$/, "").replace(/_/g, " ").trim();
                if (stem) {
                    titleInput.value = stem.replace(/\b\w/g, l => l.toUpperCase());
                }
            }

            await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = (ev) => {
                    stagedCampaignModalFiles.push({
                        name: file.name,
                        size: file.size,
                        category: cat,
                        base64: ev.target.result
                    });
                    resolve();
                };
                reader.onerror = () => resolve();
                reader.readAsDataURL(file);
            });
        }

        renderStagedCampaignFiles();
    }

    if (modalCampDropzone && modalCampFileInput) {
        modalCampDropzone.addEventListener("click", () => modalCampFileInput.click());
        modalCampFileInput.addEventListener("change", async (e) => {
            if (e.target.files && e.target.files.length > 0) {
                await stageFilesForNewCampaign(e.target.files);
                modalCampFileInput.value = "";
            }
        });
        modalCampDropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            modalCampDropzone.classList.add("drag-over");
        });
        modalCampDropzone.addEventListener("dragleave", (e) => {
            e.preventDefault();
            modalCampDropzone.classList.remove("drag-over");
        });
        modalCampDropzone.addEventListener("drop", async (e) => {
            e.preventDefault();
            modalCampDropzone.classList.remove("drag-over");
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                await stageFilesForNewCampaign(e.dataTransfer.files);
            }
        });
    }

    if (newCampBtn && campModal) {
        newCampBtn.addEventListener("click", () => {
            campaignModalEditMode = false;
            if (campHeading) campHeading.textContent = "Create New Campaign";
            if (confirmCampBtn) confirmCampBtn.textContent = "Create Campaign";
            if (deleteCampModalBtn) deleteCampModalBtn.classList.add("hidden");
            if (modalCampFilesSection) modalCampFilesSection.classList.remove("hidden");

            stagedCampaignModalFiles = [];
            renderStagedCampaignFiles();

            const titleInput = document.getElementById("input-rp-camp-title");
            const presetSelect = document.getElementById("select-rp-camp-preset");
            const gmModeSelect = document.getElementById("select-rp-camp-gmmode");
            const overviewInput = document.getElementById("input-rp-camp-overview");
            const toneInput = document.getElementById("input-rp-camp-tone");
            const diffSelect = document.getElementById("select-rp-camp-difficulty");

            if (titleInput) titleInput.value = "";
            if (presetSelect) presetSelect.value = "dnd_5e";
            if (gmModeSelect) gmModeSelect.value = "interactive_narrator";
            if (overviewInput) overviewInput.value = "";
            if (toneInput) toneInput.value = "Gritty, tense, grounded, cinematic";
            if (diffSelect) diffSelect.value = "helpful";

            campModal.classList.remove("hidden");
        });
    }

    if (editCampBtn && campModal) {
        editCampBtn.addEventListener("click", () => {
            const activeId = ttrpgData.active_campaign;
            const campaigns = ttrpgData.campaigns || {};
            const meta = (activeId && campaigns[activeId]) ? campaigns[activeId] : ttrpgData.active_campaign_meta;
            if (!meta) return;

            campaignModalEditMode = true;
            if (campHeading) campHeading.textContent = "Edit Campaign Details";
            if (confirmCampBtn) confirmCampBtn.textContent = "Save Changes";
            if (deleteCampModalBtn) deleteCampModalBtn.classList.remove("hidden");
            if (modalCampFilesSection) modalCampFilesSection.classList.add("hidden");

            stagedCampaignModalFiles = [];
            renderStagedCampaignFiles();

            const titleInput = document.getElementById("input-rp-camp-title");
            const presetSelect = document.getElementById("select-rp-camp-preset");
            const gmModeSelect = document.getElementById("select-rp-camp-gmmode");
            const overviewInput = document.getElementById("input-rp-camp-overview");
            const toneInput = document.getElementById("input-rp-camp-tone");
            const diffSelect = document.getElementById("select-rp-camp-difficulty");

            if (titleInput) titleInput.value = meta.title || "";
            if (presetSelect) presetSelect.value = meta.system_preset || "dnd_5e";
            if (gmModeSelect) gmModeSelect.value = meta.gm_mode || "interactive_narrator";
            if (overviewInput) overviewInput.value = meta.setting_overview || "";
            if (toneInput) toneInput.value = meta.tone || "Gritty, tense, grounded, cinematic";
            if (diffSelect) diffSelect.value = meta.difficulty || "helpful";

            campModal.classList.remove("hidden");
        });
    }

    if (closeCampBtn && campModal) {
        closeCampBtn.addEventListener("click", () => campModal.classList.add("hidden"));
    }
    if (cancelCampBtn && campModal) {
        cancelCampBtn.addEventListener("click", () => campModal.classList.add("hidden"));
    }
    if (deleteCampModalBtn && campModal) {
        deleteCampModalBtn.addEventListener("click", async () => {
            const activeId = ttrpgData.active_campaign;
            if (!activeId) return;
            if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_ttrpg_campaign) {
                await window.pywebview.api.delete_ttrpg_campaign(activeId);
                campModal.classList.add("hidden");
                await loadRolePlayUI();
            }
        });
    }
    if (confirmCampBtn && campModal) {
        confirmCampBtn.addEventListener("click", async () => {
            let title = (document.getElementById("input-rp-camp-title")?.value || "").trim();
            const preset = document.getElementById("select-rp-camp-preset")?.value || "dnd_5e";
            const gmMode = document.getElementById("select-rp-camp-gmmode")?.value || "interactive_narrator";
            const overview = (document.getElementById("input-rp-camp-overview")?.value || "").trim();
            const tone = (document.getElementById("input-rp-camp-tone")?.value || "Gritty, tense, grounded, cinematic").trim();
            const difficulty = document.getElementById("select-rp-camp-difficulty")?.value || "helpful";

            if (!title && stagedCampaignModalFiles.length > 0) {
                let stem = stagedCampaignModalFiles[0].name.replace(/\.[^/.]+$/, "").replace(/[\d_\-]+$/, "").replace(/_/g, " ").trim();
                if (stem) title = stem.replace(/\b\w/g, l => l.toUpperCase());
            }

            if (!title) return;

            if (campaignModalEditMode) {
                if (window.pywebview && window.pywebview.api && window.pywebview.api.update_ttrpg_campaign) {
                    await window.pywebview.api.update_ttrpg_campaign(
                        ttrpgData.active_campaign,
                        title,
                        preset,
                        gmMode,
                        overview,
                        tone,
                        difficulty,
                        null
                    );
                    campModal.classList.add("hidden");
                    await loadRolePlayUI();
                }
            } else {
                confirmCampBtn.disabled = true;
                const origText = confirmCampBtn.textContent;
                confirmCampBtn.textContent = stagedCampaignModalFiles.length > 0 ? "Creating & Ingesting... ⏳" : "Creating... ⏳";
                try {
                    const initialFilesPayload = stagedCampaignModalFiles.map(f => ({
                        filename: f.name,
                        data: f.base64,
                        category: f.category
                    }));
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.create_ttrpg_campaign) {
                        await window.pywebview.api.create_ttrpg_campaign(
                            title,
                            preset,
                            gmMode,
                            overview,
                            tone,
                            difficulty,
                            "",
                            initialFilesPayload
                        );
                        stagedCampaignModalFiles = [];
                        campModal.classList.add("hidden");
                        await loadRolePlayUI();
                    }
                } catch (err) {
                    console.error("[RolePlay] Error creating campaign:", err);
                } finally {
                    confirmCampBtn.disabled = false;
                    confirmCampBtn.textContent = origText;
                }
            }
        });
    }

    // Add / Edit Character Modal Controls
    const addCharBtn = document.getElementById("add-character-btn");
    const charModal = document.getElementById("modal-add-character");
    const charHeading = document.getElementById("modal-char-heading");
    const closeCharBtn = document.getElementById("btn-close-char-modal");
    const cancelCharBtn = document.getElementById("btn-cancel-char");
    const confirmCharBtn = document.getElementById("btn-confirm-char");

    if (addCharBtn && charModal) {
        addCharBtn.addEventListener("click", () => {
            editingCharacterId = null;
            editingCharacterSource = "campaign";
            currentEditingCharacterObj = null;
            if (charHeading) charHeading.textContent = "Add Character to Roster";

            const rulesetSelect = document.getElementById("select-rp-char-ruleset");
            const campPreset = ttrpgData.active_campaign_meta?.system_preset || "dnd_5e";
            let defaultRuleset = "dnd_5e";
            if (campPreset.includes("cyber")) defaultRuleset = "cyberpunk";
            else if (campPreset.includes("pathfinder")) defaultRuleset = "pathfinder_2e";
            else if (campPreset.includes("cthulhu")) defaultRuleset = "call_of_cthulhu";
            else if (campPreset.includes("agnostic") || campPreset.includes("sandbox") || campPreset.includes("custom")) defaultRuleset = "agnostic";

            if (rulesetSelect) rulesetSelect.value = defaultRuleset;
            onRulesetDropdownChanged(defaultRuleset);

            const nameEl = document.getElementById("input-rp-char-name");
            const playerEl = document.getElementById("input-rp-char-player");
            if (nameEl) nameEl.value = "";
            if (playerEl) playerEl.value = "";

            // Reset D&D fields
            const dndClassEl = document.getElementById("input-dnd-class-level");
            const dndSpeciesEl = document.getElementById("input-dnd-species");
            const dndBgEl = document.getElementById("input-dnd-background");
            const dndXpEl = document.getElementById("input-dnd-xp");
            if (dndClassEl) dndClassEl.value = "Fighter 1";
            if (dndSpeciesEl) dndSpeciesEl.value = "Human";
            if (dndBgEl) dndBgEl.value = "Noble";
            if (dndXpEl) dndXpEl.value = "0";
            const dndDefensesReset = document.getElementById("input-dnd-defenses");
            if (dndDefensesReset) dndDefensesReset.value = "";
            const dndInitReset = document.getElementById("input-dnd-init");
            if (dndInitReset) dndInitReset.dataset.customized = "false";

            renderDndSkillsGrid({});
            updateDndCalculations();

            stagedCharacterWeapons = [];
            renderStagedWeapons();
            stagedCharacterSpells = [];
            renderStagedSpells();

            charModal.classList.remove("hidden");
        });
    }

    // Ruleset pulldown listener
    const rulesetSelect = document.getElementById("select-rp-char-ruleset");
    if (rulesetSelect) {
        rulesetSelect.addEventListener("change", (e) => onRulesetDropdownChanged(e.target.value));
    }

    // Real-time calculation listeners for D&D 5E
    document.querySelectorAll(".dnd-stat-input").forEach(inp => {
        inp.addEventListener("input", () => updateDndCalculations());
        inp.addEventListener("change", () => updateDndCalculations());
    });
    document.querySelectorAll(".dnd-save-chk").forEach(chk => {
        chk.addEventListener("change", () => updateDndCalculations());
    });
    const dndProfBonusEl = document.getElementById("input-dnd-prof-bonus");
    if (dndProfBonusEl) {
        dndProfBonusEl.addEventListener("input", () => updateDndCalculations());
    }
    const dndSpellAbilityEl = document.getElementById("select-dnd-spell-ability");
    if (dndSpellAbilityEl) {
        dndSpellAbilityEl.addEventListener("change", () => updateDndCalculations());
    }
    const dndInitInputEl = document.getElementById("input-dnd-init");
    if (dndInitInputEl) {
        dndInitInputEl.addEventListener("input", () => {
            dndInitInputEl.dataset.customized = "true";
        });
    }

    // Modal "Load File" Character Upload Handlers
    const modalLoadCharBtn = document.getElementById("btn-modal-load-char-file");
    const modalLoadCharBodyBtn = document.getElementById("btn-modal-load-char-file-body");
    const modalCharFileInput = document.getElementById("input-modal-char-file");

    function triggerModalCharUpload() {
        if (modalCharFileInput) {
            modalCharFileInput.value = "";
            modalCharFileInput.click();
        }
    }

    if (modalLoadCharBtn) modalLoadCharBtn.addEventListener("click", triggerModalCharUpload);
    if (modalLoadCharBodyBtn) modalLoadCharBodyBtn.addEventListener("click", triggerModalCharUpload);

    if (modalCharFileInput) {
        modalCharFileInput.addEventListener("change", async (e) => {
            const file = e.target.files && e.target.files[0];
            if (!file) return;

            if (modalLoadCharBtn) {
                modalLoadCharBtn.disabled = true;
                modalLoadCharBtn.textContent = "⏳ Uploading...";
            }
            if (modalLoadCharBodyBtn) {
                modalLoadCharBodyBtn.disabled = true;
                modalLoadCharBodyBtn.textContent = "⏳ Uploading...";
            }

            try {
                const reader = new FileReader();
                reader.onload = async (ev) => {
                    try {
                        const base64Data = ev.target.result;
                        if (editingCharacterSource === "vault") {
                            const b64 = base64Data.includes(",") ? base64Data.split(",")[1] : base64Data;
                            if (window.pywebview && window.pywebview.api && window.pywebview.api.upload_vault_character_document) {
                                const res = await window.pywebview.api.upload_vault_character_document(file.name, b64);
                                if (res && res.status === "requires_ruleset_selection") {
                                    promptRulesetSelection(res, async (selectedRuleset, req) => {
                                        const retryRes = await window.pywebview.api.upload_vault_character_document(
                                            req.filename,
                                            req.base64_content,
                                            selectedRuleset
                                        );
                                        if (retryRes && retryRes.status === "success") {
                                            alert(`Successfully imported character from '${req.filename}' into Character Vault!`);
                                            if (charModal) charModal.classList.add("hidden");
                                            await refreshVaultModalUI();
                                        } else {
                                            alert(`Failed to load character: ${retryRes?.message || 'Unknown error'}`);
                                        }
                                    });
                                    return;
                                } else if (res && res.status === "error") {
                                    alert(`Failed to load character: ${res.message || 'Unknown error'}`);
                                } else if (res && res.status === "success") {
                                    alert(`Successfully imported character from '${file.name}' into Character Vault!`);
                                    if (charModal) charModal.classList.add("hidden");
                                    await refreshVaultModalUI();
                                }
                            }
                        } else {
                            if (window.pywebview && window.pywebview.api && window.pywebview.api.upload_ttrpg_document) {
                                const res = await window.pywebview.api.upload_ttrpg_document(
                                    file.name,
                                    base64Data,
                                    "character_sheet",
                                    ttrpgData.active_campaign
                                );
                                if (res && res.status === "requires_ruleset_selection") {
                                    promptRulesetSelection(res, async (selectedRuleset, req) => {
                                        const retryRes = await window.pywebview.api.upload_ttrpg_document(
                                            req.filename,
                                            req.base64_content,
                                            "character_sheet",
                                            req.campaign_id,
                                            selectedRuleset
                                        );
                                        if (retryRes && retryRes.status === "success") {
                                            const importedNames = (retryRes.imported_characters && retryRes.imported_characters.length > 0)
                                                ? retryRes.imported_characters.map(c => typeof c === "object" ? (c.name || c.id) : c).join(", ")
                                                : "character";
                                            alert(`👤 Imported ${importedNames} into Party Roster & Character Vault (${retryRes.ruleset || 'Hero'})!`);
                                            if (charModal) charModal.classList.add("hidden");
                                            await loadRolePlayUI(true);
                                        } else {
                                            alert(`Failed to load character: ${retryRes?.message || 'Unknown error'}`);
                                        }
                                    });
                                    return;
                                } else if (res && res.status === "error") {
                                    alert(`Failed to load character: ${res.message || 'Unknown error'}`);
                                } else if (res && res.status === "success") {
                                    const importedNames = (res.imported_characters && res.imported_characters.length > 0)
                                        ? res.imported_characters.map(c => typeof c === "object" ? (c.name || c.id) : c).join(", ")
                                        : "character";
                                    alert(`👤 Imported ${importedNames} into Party Roster & Character Vault (${res.ruleset || 'Hero'})!`);
                                    if (charModal) charModal.classList.add("hidden");
                                    await loadRolePlayUI(true);
                                }
                            }
                        }
                    } catch (err) {
                        console.error("[RolePlay] Error uploading character document from modal:", err);
                        alert(`Error uploading character: ${err.message}`);
                    } finally {
                        if (charModal) charModal.classList.add("hidden");
                        if (modalLoadCharBtn) {
                            modalLoadCharBtn.disabled = false;
                            modalLoadCharBtn.textContent = "📄 Load File";
                        }
                        if (modalLoadCharBodyBtn) {
                            modalLoadCharBodyBtn.disabled = false;
                            modalLoadCharBodyBtn.textContent = "📄 Load File";
                        }
                        if (editingCharacterSource === "vault") {
                            await refreshVaultModalUI();
                        } else {
                            await loadRolePlayUI(true);
                        }
                    }
                };
                reader.readAsDataURL(file);
            } catch (err) {
                console.error("[RolePlay] Error reading file:", err);
                if (charModal) charModal.classList.add("hidden");
                if (modalLoadCharBtn) {
                    modalLoadCharBtn.disabled = false;
                    modalLoadCharBtn.textContent = "📄 Load File";
                }
                if (modalLoadCharBodyBtn) {
                    modalLoadCharBodyBtn.disabled = false;
                    modalLoadCharBodyBtn.textContent = "📄 Load File";
                }
            }
        });
    }
    if (closeCharBtn && charModal) {
        closeCharBtn.addEventListener("click", () => {
            editingCharacterId = null;
            editingCharacterSource = "campaign";
            currentEditingCharacterObj = null;
            charModal.classList.add("hidden");
        });
    }
    if (cancelCharBtn && charModal) {
        cancelCharBtn.addEventListener("click", () => {
            editingCharacterId = null;
            editingCharacterSource = "campaign";
            currentEditingCharacterObj = null;
            charModal.classList.add("hidden");
        });
    }
    if (confirmCharBtn && charModal) {
        confirmCharBtn.addEventListener("click", async () => {
            try {
                const name = (document.getElementById("input-rp-char-name")?.value || "").trim();
                if (!name) {
                    alert("Please enter a character name.");
                    return;
                }

                const playerName = (document.getElementById("input-rp-char-player")?.value || "Player").trim();
                const activeRuleset = (document.getElementById("select-rp-char-ruleset")?.value || "dnd_5e").toLowerCase();

                const safeInt = (val, def = 0) => {
                    if (val === null || val === undefined) return def;
                    const m = String(val).match(/[-+]?\d+/);
                    if (!m) return def;
                    const parsed = parseInt(m[0], 10);
                    return isNaN(parsed) ? def : parsed;
                };

                const existingChar = currentEditingCharacterObj || (editingCharacterId ? ((ttrpgData.state && ttrpgData.state.characters) || {})[editingCharacterId] : null);

                let payload = {
                    id: editingCharacterId || undefined,
                    name: name,
                    player_name: playerName,
                    ruleset: activeRuleset
                };

                if (activeRuleset === "dnd_5e") {
                    const clsLvl = (document.getElementById("input-dnd-class-level")?.value || "Fighter 1").trim();
                    const species = (document.getElementById("input-dnd-species")?.value || "").trim();
                    const bg = (document.getElementById("input-dnd-background")?.value || "").trim();
                    const xp = safeInt(document.getElementById("input-dnd-xp")?.value, 0);
                    const mLvl = clsLvl.match(/\d+/);
                    const lvl = mLvl ? safeInt(mLvl[0], 1) : 1;

                    const str = safeInt(document.getElementById("input-dnd-str")?.value, 10);
                    const dex = safeInt(document.getElementById("input-dnd-dex")?.value, 10);
                    const con = safeInt(document.getElementById("input-dnd-con")?.value, 10);
                    const intVal = safeInt(document.getElementById("input-dnd-int")?.value, 10);
                    const wis = safeInt(document.getElementById("input-dnd-wis")?.value, 10);
                    const cha = safeInt(document.getElementById("input-dnd-cha")?.value, 10);

                    const ac = safeInt(document.getElementById("input-dnd-ac")?.value, 14);
                    const init = (document.getElementById("input-dnd-init")?.value || "+0").trim();
                    const speed = (document.getElementById("input-dnd-speed")?.value || "30 ft.").trim();
                    const profBonus = (document.getElementById("input-dnd-prof-bonus")?.value || "+2").trim();
                    const maxHp = Math.max(1, safeInt(document.getElementById("input-dnd-max-hp")?.value, 14));
                    const curHp = Math.max(0, Math.min(maxHp, safeInt(document.getElementById("input-dnd-cur-hp")?.value, maxHp)));
                    const tempHp = safeInt(document.getElementById("input-dnd-temp-hp")?.value, 0);
                    const hitDice = (document.getElementById("input-dnd-hit-dice")?.value || "1d10").trim();

                    const savingThrows = {
                        Strength: document.getElementById("val-dnd-save-str")?.textContent || "+0",
                        Dexterity: document.getElementById("val-dnd-save-dex")?.textContent || "+0",
                        Constitution: document.getElementById("val-dnd-save-con")?.textContent || "+0",
                        Intelligence: document.getElementById("val-dnd-save-int")?.textContent || "+0",
                        Wisdom: document.getElementById("val-dnd-save-wis")?.textContent || "+0",
                        Charisma: document.getElementById("val-dnd-save-cha")?.textContent || "+0"
                    };

                    const skillsDict = {};
                    const skillsContainer = document.getElementById("dnd-skills-grid-container");
                    if (skillsContainer) {
                        DND_5E_SKILLS_DEF.forEach(s => {
                            const cleanKey = s.name.replace(/[^a-zA-Z]/g, '');
                            const val = document.getElementById(`val-dnd-skill-${cleanKey}`)?.textContent || "+0";
                            const isProf = skillsContainer.querySelector(`input[data-skill="${s.name}"]`)?.checked;
                            skillsDict[s.name] = isProf ? `${val} (Proficient)` : val;
                        });
                    }

                    const currency = {
                        pp: safeInt(document.getElementById("input-dnd-coin-pp")?.value, 0),
                        gp: safeInt(document.getElementById("input-dnd-coin-gp")?.value, 0),
                        ep: safeInt(document.getElementById("input-dnd-coin-ep")?.value, 0),
                        sp: safeInt(document.getElementById("input-dnd-coin-sp")?.value, 0),
                        cp: safeInt(document.getElementById("input-dnd-coin-cp")?.value, 0)
                    };

                    const armor = (document.getElementById("input-dnd-prof-armor")?.value || "").trim();
                    const weaponsProf = (document.getElementById("input-dnd-prof-weapons")?.value || "").trim();
                    const tools = (document.getElementById("input-dnd-prof-tools")?.value || "").trim();
                    const languages = (document.getElementById("input-dnd-languages")?.value || "").trim();
                    const profLang = [
                        armor ? `=== ARMOR ===\n${armor}` : "",
                        weaponsProf ? `=== WEAPONS ===\n${weaponsProf}` : "",
                        tools ? `=== TOOLS ===\n${tools}` : "",
                        languages ? `=== LANGUAGES ===\n${languages}` : ""
                    ].filter(Boolean).join("\n\n");

                    const wpnsRaw = (document.getElementById("input-dnd-weapons")?.value || "").trim();
                    const weapons = (stagedCharacterWeapons && stagedCharacterWeapons.length > 0)
                        ? stagedCharacterWeapons
                        : (wpnsRaw ? wpnsRaw.split(/;\s*|\n+|,\s*(?![^()]*\))/).map(w => {
                            const parts = w.trim();
                            const m = parts.match(/^([^(]+)(?:\(([^,)]*)(?:,\s*([^)]*))?\))?/);
                            return {
                                name: m ? m[1].trim() : parts,
                                atk_bonus: m && m[2] ? m[2].trim() : "+0",
                                damage: m && m[3] ? m[3].trim() : "1d4"
                            };
                        }).filter(w => Boolean(w.name)) : []);

                    const invRaw = (document.getElementById("input-dnd-inventory")?.value || "").trim();
                    const inventory = invRaw ? invRaw.split(/[\n,]+/).map(i => i.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [];
                    const featsRaw = (document.getElementById("input-dnd-features")?.value || "").trim();
                    const features = featsRaw ? featsRaw.split(/\n+|;\s*|,\s*(?![^()]*\))/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [];
                    const spellsRaw = (document.getElementById("input-dnd-spells")?.value || "").trim();
                    const spells = (stagedCharacterSpells && stagedCharacterSpells.length > 0)
                        ? stagedCharacterSpells
                        : (spellsRaw ? spellsRaw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []);

                    const archetype = `${clsLvl}${species || bg ? ' (' + [species, bg].filter(Boolean).join(', ') + ')' : ''}`;

                    payload = {
                        ...payload,
                        archetype: archetype,
                        class_and_level: clsLvl,
                        species: species,
                        background: bg,
                        level: lvl,
                        experience_points: xp,
                        max_health: maxHp,
                        current_health: curHp,
                        temp_hp: tempHp,
                        guard: ac,
                        armor_class: ac,
                        speed: speed,
                        hit_dice: hitDice,
                        prof_bonus: profBonus,
                        initiative: init,
                        attributes: {
                            Strength: str, Dexterity: dex, Constitution: con,
                            Intelligence: intVal, Wisdom: wis, Charisma: cha,
                            Might: str, Agility: dex, Vigor: con,
                            Intellect: intVal, Presence: Math.max(wis, cha)
                        },
                        saving_throws: savingThrows,
                        skills: skillsDict,
                        currency: currency,
                        passives: {
                            perception: safeInt(document.getElementById("input-dnd-pass-percep")?.value, 10),
                            insight: safeInt(document.getElementById("input-dnd-pass-insight")?.value, 10),
                            investigation: safeInt(document.getElementById("input-dnd-pass-invest")?.value, 10)
                        },
                        senses: (document.getElementById("input-dnd-senses")?.value || "").trim(),
                        defenses: (document.getElementById("input-dnd-defenses")?.value || "").trim(),
                        resistances: (document.getElementById("input-dnd-defenses")?.value || "").trim(),
                        proficiencies: profLang,
                        proficiencies_armor: armor,
                        proficiencies_weapons: weaponsProf,
                        proficiencies_tools: tools,
                        proficiencies_languages: languages,
                        weapons: weapons,
                        inventory: inventory,
                        features_and_traits: features.length > 0 ? features : (existingChar ? extractCharacterFeatures(existingChar) : []),
                        traits_and_abilities: features.length > 0 ? features : (existingChar ? extractCharacterFeatures(existingChar) : []),
                        spells: spells,
                        spellcasting_ability: document.getElementById("select-dnd-spell-ability")?.value || "none",
                        spell_save_dc: (document.getElementById("input-dnd-spell-dc")?.value || "").trim(),
                        spell_attack_bonus: (document.getElementById("input-dnd-spell-atk")?.value || "").trim()
                    };
                } else if (activeRuleset === "pathfinder_2e") {
                    const clsLvl = (document.getElementById("input-pf-class-level")?.value || "Fighter 1").trim();
                    const ancestry = (document.getElementById("input-pf-ancestry")?.value || "").trim();
                    const bg = (document.getElementById("input-pf-background")?.value || "").trim();
                    const xp = safeInt(document.getElementById("input-pf-xp")?.value, 0);
                    const str = safeInt(document.getElementById("input-pf-str")?.value, 10);
                    const dex = safeInt(document.getElementById("input-pf-dex")?.value, 10);
                    const con = safeInt(document.getElementById("input-pf-con")?.value, 10);
                    const intVal = safeInt(document.getElementById("input-pf-int")?.value, 10);
                    const wis = safeInt(document.getElementById("input-pf-wis")?.value, 10);
                    const cha = safeInt(document.getElementById("input-pf-cha")?.value, 10);
                    const ac = safeInt(document.getElementById("input-pf-ac")?.value, 15);
                    const hp = Math.max(1, safeInt(document.getElementById("input-pf-max-hp")?.value, 20));
                    const speed = (document.getElementById("input-pf-speed")?.value || "25 ft.").trim();
                    const init = (document.getElementById("input-pf-perception")?.value || "+5").trim();

                    payload = {
                        ...payload,
                        archetype: `${clsLvl}${ancestry || bg ? ' (' + [ancestry, bg].filter(Boolean).join(', ') + ')' : ''}`,
                        species: ancestry,
                        background: bg,
                        level: Math.max(1, safeInt(clsLvl.match(/\d+/)?.[0], 1)),
                        experience_points: xp,
                        max_health: hp,
                        current_health: hp,
                        guard: ac,
                        armor_class: ac,
                        speed: speed,
                        initiative: init,
                        attributes: {
                            Strength: str, Dexterity: dex, Constitution: con,
                            Intelligence: intVal, Wisdom: wis, Charisma: cha,
                            Might: str, Agility: dex, Vigor: con, Intellect: intVal, Presence: Math.max(wis, cha)
                        },
                        saving_throws: {
                            Fortitude: document.getElementById("input-pf-save-fort")?.value || "+7",
                            Reflex: document.getElementById("input-pf-save-ref")?.value || "+5",
                            Will: document.getElementById("input-pf-save-will")?.value || "+4"
                        },
                        weapons: (stagedCharacterWeapons && stagedCharacterWeapons.length > 0)
                            ? stagedCharacterWeapons
                            : (() => {
                                const strikesRaw = (document.getElementById("input-pf-strikes")?.value || "").trim();
                                return strikesRaw ? strikesRaw.split(/;\s*|\n+|,\s*(?![^()]*\))/).map(w => {
                                    const parts = w.trim();
                                    const m = parts.match(/^([^(]+)(?:\(([^,)]*)(?:,\s*([^)]*))?\))?/);
                                    return {
                                        name: m ? m[1].trim() : parts,
                                        atk_bonus: m && m[2] ? m[2].trim() : "+0",
                                        damage: m && m[3] ? m[3].trim() : "1d6"
                                    };
                                }).filter(w => Boolean(w.name)) : [];
                            })(),
                        proficiencies: document.getElementById("input-pf-skills")?.value || "",
                        inventory: (document.getElementById("input-pf-inventory")?.value || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean),
                        features_and_traits: (() => {
                            const raw = (document.getElementById("input-pf-features")?.value || "").trim();
                            if (raw) return raw.split(/[\n,;]+/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
                            return existingChar ? extractCharacterFeatures(existingChar) : [];
                        })(),
                        traits_and_abilities: (() => {
                            const raw = (document.getElementById("input-pf-features")?.value || "").trim();
                            if (raw) return raw.split(/[\n,;]+/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
                            return existingChar ? extractCharacterFeatures(existingChar) : [];
                        })(),
                        spells: (stagedCharacterSpells && stagedCharacterSpells.length > 0)
                            ? stagedCharacterSpells
                            : (() => {
                                const raw = (document.getElementById("input-pf-spells")?.value || "").trim();
                                if (raw) return raw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean);
                                return existingChar ? extractCharacterSpells(existingChar) : [];
                            })()
                    };
                } else if (activeRuleset === "cyberpunk") {
                    const role = (document.getElementById("input-cyber-role")?.value || "Solo 4").trim();
                    const origin = (document.getElementById("input-cyber-origin")?.value || "").trim();
                    const rep = safeInt(document.getElementById("input-cyber-rep")?.value, 0);
                    const ip = safeInt(document.getElementById("input-cyber-ip")?.value, 0);

                    const stats = {
                        INT: safeInt(document.getElementById("input-cyber-int")?.value, 6),
                        REF: safeInt(document.getElementById("input-cyber-ref")?.value, 6),
                        DEX: safeInt(document.getElementById("input-cyber-dex")?.value, 6),
                        TECH: safeInt(document.getElementById("input-cyber-tech")?.value, 6),
                        COOL: safeInt(document.getElementById("input-cyber-cool")?.value, 6),
                        WILL: safeInt(document.getElementById("input-cyber-will")?.value, 6),
                        LUCK: safeInt(document.getElementById("input-cyber-luck")?.value, 6),
                        MOVE: safeInt(document.getElementById("input-cyber-move")?.value, 6),
                        BODY: safeInt(document.getElementById("input-cyber-body")?.value, 6),
                        EMP: safeInt(document.getElementById("input-cyber-emp")?.value, 6)
                    };

                    const hp = Math.max(1, safeInt(document.getElementById("input-cyber-hp")?.value, 40));
                    const headSp = safeInt(document.getElementById("input-cyber-head-sp")?.value, 11);
                    const bodySp = safeInt(document.getElementById("input-cyber-body-sp")?.value, 11);
                    const humanity = safeInt(document.getElementById("input-cyber-humanity")?.value, 48);
                    const eb = safeInt(document.getElementById("input-cyber-eb")?.value, 0);

                    const wpnsRaw = (document.getElementById("input-cyber-weapons")?.value || "").trim();
                    const weapons = (stagedCharacterWeapons && stagedCharacterWeapons.length > 0)
                        ? stagedCharacterWeapons
                        : (wpnsRaw ? wpnsRaw.split(/;\s*|\n+|,\s*(?![^()]*\))/).map(w => {
                            const parts = w.trim();
                            const m = parts.match(/^([^(]+)(?:\(([^,)]*)(?:,\s*([^)]*))?\))?/);
                            return {
                                name: m ? m[1].trim() : parts,
                                atk_bonus: m && m[2] ? m[2].trim() : "+0",
                                damage: m && m[3] ? m[3].trim() : "2d6"
                            };
                        }).filter(w => Boolean(w.name)) : []);

                    const cyberFeatsRaw = (document.getElementById("input-cyber-cyberware")?.value || "").trim();
                    const cyberFeatures = cyberFeatsRaw ? cyberFeatsRaw.split(/[\n,]+/).map(s => s.trim()).filter(Boolean) : [];

                    payload = {
                        ...payload,
                        archetype: `${role}${origin ? ' (' + origin + ')' : ''}`,
                        level: Math.max(1, safeInt(role.match(/\d+/)?.[0], 1)),
                        experience_points: ip,
                        max_health: hp,
                        current_health: hp,
                        guard: bodySp,
                        armor_class: bodySp,
                        head_sp: headSp,
                        body_sp: bodySp,
                        humanity: humanity,
                        currency: { eb: eb },
                        attributes: {
                            ...stats,
                            Might: stats.BODY,
                            Agility: Math.max(stats.REF, stats.DEX),
                            Vigor: stats.BODY,
                            Intellect: stats.INT,
                            Presence: stats.COOL
                        },
                        weapons: weapons,
                        features_and_traits: cyberFeatures.length > 0 ? cyberFeatures : (existingChar ? extractCharacterFeatures(existingChar) : []),
                        traits_and_abilities: cyberFeatures.length > 0 ? cyberFeatures : (existingChar ? extractCharacterFeatures(existingChar) : []),
                        inventory: (document.getElementById("input-cyber-inventory")?.value || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
                    };
                } else if (activeRuleset === "call_of_cthulhu") {
                    const occ = (document.getElementById("input-coc-occupation")?.value || "Investigator").trim();
                    const age = safeInt(document.getElementById("input-coc-age")?.value, 20);
                    const sex = (document.getElementById("input-coc-sex")?.value || "").trim();
                    const residence = (document.getElementById("input-coc-residence")?.value || "").trim();
                    const birthplace = (document.getElementById("input-coc-birthplace")?.value || "").trim();

                    const stats = {
                        STR: safeInt(document.getElementById("input-coc-str")?.value, 50),
                        CON: safeInt(document.getElementById("input-coc-con")?.value, 50),
                        SIZ: safeInt(document.getElementById("input-coc-siz")?.value, 50),
                        DEX: safeInt(document.getElementById("input-coc-dex")?.value, 50),
                        APP: safeInt(document.getElementById("input-coc-app")?.value, 50),
                        INT: safeInt(document.getElementById("input-coc-int")?.value, 50),
                        POW: safeInt(document.getElementById("input-coc-pow")?.value, 50),
                        EDU: safeInt(document.getElementById("input-coc-edu")?.value, 50),
                        Move: safeInt(document.getElementById("input-coc-move")?.value, 8)
                    };

                    const curHp = Math.max(1, safeInt(document.getElementById("input-coc-cur-hp")?.value, 13));
                    const maxHp = Math.max(1, safeInt(document.getElementById("input-coc-max-hp")?.value, 13));
                    const curSan = Math.max(0, safeInt(document.getElementById("input-coc-cur-san")?.value, 50));
                    const maxSan = Math.max(1, safeInt(document.getElementById("input-coc-max-san")?.value, 99));
                    const luck = Math.max(0, safeInt(document.getElementById("input-coc-luck")?.value, 55));
                    const curMp = Math.max(0, safeInt(document.getElementById("input-coc-cur-mp")?.value, 10));
                    const maxMp = Math.max(1, safeInt(document.getElementById("input-coc-max-mp")?.value, 10));

                    const db = (document.getElementById("input-coc-db")?.value || "none").trim();
                    const build = safeInt(document.getElementById("input-coc-build")?.value, 0);
                    const dodge = safeInt(document.getElementById("input-coc-dodge")?.value, Math.floor(stats.DEX / 2));

                    // Harvest skills from the rendered skills grid
                    const skillsObj = {};
                    document.querySelectorAll(".coc-skill-input").forEach(inp => {
                        const sName = inp.getAttribute("data-skill");
                        const sVal = safeInt(inp.value, 0);
                        if (sName) {
                            skillsObj[sName] = `${sVal}%`;
                        }
                    });

                    // Harvest backstory & lore
                    const desc = (document.getElementById("input-coc-desc")?.value || "").trim();
                    const ideology = (document.getElementById("input-coc-ideology")?.value || "").trim();
                    const people = (document.getElementById("input-coc-people")?.value || "").trim();
                    const locs = (document.getElementById("input-coc-locations")?.value || "").trim();
                    const possessions = (document.getElementById("input-coc-possessions")?.value || "").trim();
                    const traits = (document.getElementById("input-coc-traits")?.value || "").trim();

                    const cocTraitsList = [];
                    if (desc) cocTraitsList.push(`Personal Description: ${desc}`);
                    if (ideology) cocTraitsList.push(`Ideology/Beliefs: ${ideology}`);
                    if (people) cocTraitsList.push(`Significant People: ${people}`);
                    if (locs) cocTraitsList.push(`Meaningful Locations: ${locs}`);
                    if (possessions) cocTraitsList.push(`Treasured Possessions: ${possessions}`);
                    if (traits) cocTraitsList.push(`Traits: ${traits}`);

                    // Weapons
                    const weapons = (stagedCharacterWeapons && stagedCharacterWeapons.length > 0)
                        ? stagedCharacterWeapons
                        : (existingChar?.weapons || [{ name: "Unarmed", atk_bonus: "60%", damage: "1d3 + db", notes: "Attacks: 1" }]);

                    // Inventory & currency
                    const invRaw = (document.getElementById("input-coc-inventory")?.value || "").trim();
                    const invList = invRaw ? invRaw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : (existingChar?.inventory || []);
                    const cr = safeInt(document.getElementById("input-coc-credit-rating")?.value, 40);
                    const cash = (document.getElementById("input-coc-cash")?.value || "$80").trim();
                    const assets = (document.getElementById("input-coc-assets")?.value || "$2,000").trim();

                    payload = {
                        ...payload,
                        ruleset: "call_of_cthulhu",
                        ruleset_label: "Call of Cthulhu",
                        native_genre: "modern_horror",
                        native_system: "call_of_cthulhu",
                        archetype: occ,
                        age: age,
                        sex: sex,
                        residence: residence,
                        birthplace: birthplace,
                        level: 1,
                        experience_points: 0,
                        max_health: maxHp,
                        current_health: curHp,
                        guard: dodge,
                        armor_class: dodge,
                        dodge: dodge,
                        damage_bonus: db,
                        build: build,
                        credit_rating: cr,
                        currency: { cash: cash, assets: assets, cp: 0, sp: 0, ep: 0, gp: 0, pp: 0 },
                        vitals: {
                            health: { current: curHp, max: maxHp, label: "Health" },
                            guard: { current: dodge, max: dodge, label: "Dodge Defense" }
                        },
                        custom_meters: {
                            sanity: { current: curSan, max: maxSan, label: "Sanity" },
                            luck: { current: luck, max: 99, label: "Luck" },
                            magic_points: { current: curMp, max: maxMp, label: "Magic Points" },
                            move_rate: { current: stats.Move, max: stats.Move, label: "Move Rate" }
                        },
                        attributes: {
                            ...stats,
                            Might: stats.STR,
                            Agility: stats.DEX,
                            Vigor: stats.CON,
                            Intellect: stats.INT,
                            Presence: Math.max(stats.POW, stats.APP)
                        },
                        skills: skillsObj,
                        weapons: weapons,
                        inventory: invList,
                        features_and_traits: cocTraitsList.length > 0 ? cocTraitsList : (existingChar ? extractCharacterFeatures(existingChar) : []),
                        traits_and_abilities: cocTraitsList.length > 0 ? cocTraitsList : (existingChar ? extractCharacterFeatures(existingChar) : [])
                    };
                } else {
                    // Agnostic / Custom Fallback
                    const lvl = safeInt(document.getElementById("input-rp-char-level")?.value, 1);
                    const xp = safeInt(document.getElementById("input-rp-char-xp")?.value, 0);
                    const wpnsRaw = (document.getElementById("input-rp-char-weapons")?.value || "").trim();
                    const invRaw = (document.getElementById("input-rp-char-inventory")?.value || "").trim();
                    const spellsRaw = (document.getElementById("input-rp-char-spells")?.value || "").trim();
                    const featsRaw = (document.getElementById("input-rp-char-features")?.value || "").trim();
                    const skillsRaw = (document.getElementById("input-rp-char-skills")?.value || "").trim();

                    const weapons = (stagedCharacterWeapons && stagedCharacterWeapons.length > 0)
                        ? stagedCharacterWeapons
                        : (wpnsRaw ? wpnsRaw.split(/;\s*|\n+|,\s*(?![^()]*\))/).map(w => {
                            const parts = w.trim();
                            const m = parts.match(/^([^(]+)(?:\(([^,)]*)(?:,\s*([^)]*))?\))?/);
                            return {
                                name: m ? m[1].trim() : parts,
                                atk_bonus: m && m[2] ? m[2].trim() : "+0",
                                damage: m && m[3] ? m[3].trim() : "1d4"
                            };
                        }).filter(w => Boolean(w.name)) : []);

                    const maxHp = Math.max(1, safeInt(document.getElementById("input-rp-char-hp")?.value, 28));
                    payload = {
                        ...payload,
                        archetype: (document.getElementById("input-rp-char-archetype")?.value || "Adventurer").trim(),
                        level: Math.max(1, lvl),
                        experience_points: Math.max(0, xp),
                        max_health: maxHp,
                        current_health: maxHp,
                        guard: safeInt(document.getElementById("input-rp-char-guard")?.value, 15),
                        custom_meter_label: (document.getElementById("input-rp-char-meter-label")?.value || "Stamina").trim(),
                        custom_meter_max: Math.max(1, safeInt(document.getElementById("input-rp-char-meter-max")?.value, 5)),
                        Might: safeInt(document.getElementById("input-rp-attr-might")?.value, 12),
                        Agility: safeInt(document.getElementById("input-rp-attr-agility")?.value, 12),
                        Vigor: safeInt(document.getElementById("input-rp-attr-vigor")?.value, 12),
                        Intellect: safeInt(document.getElementById("input-rp-attr-intellect")?.value, 12),
                        Presence: safeInt(document.getElementById("input-rp-attr-presence")?.value, 12),
                        weapons: weapons,
                        inventory: invRaw ? invRaw.split(/[\n,]+/).map(i => i.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [],
                        spells: (stagedCharacterSpells && stagedCharacterSpells.length > 0)
                            ? stagedCharacterSpells
                            : (spellsRaw ? spellsRaw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []),
                        features_and_traits: featsRaw ? featsRaw.split(/[\n,;]+/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [],
                        proficiencies: skillsRaw
                    };
                }

                confirmCharBtn.disabled = true;
                const origText = confirmCharBtn.textContent;
                confirmCharBtn.textContent = "Saving... ⏳";
                try {
                    if (editingCharacterSource === "vault") {
                        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_vault_character) {
                            const res = await window.pywebview.api.save_vault_character(payload);
                            if (res && res.status === "error") {
                                alert(`Could not save vault character: ${res.message || "Unknown error"}`);
                                return;
                            }
                            editingCharacterId = null;
                            editingCharacterSource = "campaign";
                            currentEditingCharacterObj = null;
                            charModal.classList.add("hidden");
                            await refreshVaultModalUI();
                            if (typeof renderRolePlayStoryBubble === "function") {
                                renderRolePlayStoryBubble({
                                    type: "system",
                                    content: `💾 Character Vault: Successfully updated hero '${payload.name || "Hero"}'!`
                                });
                            }
                            alert(`Hero '${payload.name}' successfully saved in Character Vault!`);
                        }
                    } else {
                        if (window.pywebview && window.pywebview.api && window.pywebview.api.add_ttrpg_character) {
                            const res = await window.pywebview.api.add_ttrpg_character(payload);
                            if (res && res.status === "error") {
                                alert(`Could not save character: ${res.message || "Unknown error"}`);
                                return;
                            }
                            editingCharacterId = null;
                            editingCharacterSource = "campaign";
                            currentEditingCharacterObj = null;
                            charModal.classList.add("hidden");
                            await loadRolePlayUI();
                        } else {
                            console.warn("[RolePlay] pywebview add_ttrpg_character API not available.");
                            editingCharacterId = null;
                            editingCharacterSource = "campaign";
                            currentEditingCharacterObj = null;
                            charModal.classList.add("hidden");
                        }
                    }
                } finally {
                    confirmCharBtn.disabled = false;
                    confirmCharBtn.textContent = origText;
                }
            } catch (err) {
                console.error("[RolePlay] Error saving character:", err);
                alert(`Error saving character: ${err.message || err}`);
            }
        });
    }

    // Journal & Session Notes Modal Controls
    const addNoteBtn = document.getElementById("btn-add-journal-note");
    const noteModal = document.getElementById("modal-rp-add-note");
    const closeNoteBtn = document.getElementById("btn-close-rp-note-modal");
    const cancelNoteBtn = document.getElementById("btn-cancel-rp-note");
    const saveNoteBtn = document.getElementById("btn-save-rp-note");

    if (addNoteBtn && noteModal) {
        addNoteBtn.addEventListener("click", () => {
            const summaryEl = document.getElementById("input-rp-note-summary");
            const locEl = document.getElementById("input-rp-note-location");
            const questEl = document.getElementById("input-rp-note-quest");
            const cluesEl = document.getElementById("input-rp-note-clues");

            const state = ttrpgData.state || {};
            if (summaryEl) summaryEl.value = "";
            if (locEl) locEl.value = state.current_location || "";
            if (questEl) questEl.value = state.active_quest || "";
            if (cluesEl) cluesEl.value = "";

            noteModal.classList.remove("hidden");
        });
    }

    if (closeNoteBtn && noteModal) {
        closeNoteBtn.addEventListener("click", () => noteModal.classList.add("hidden"));
    }
    if (cancelNoteBtn && noteModal) {
        cancelNoteBtn.addEventListener("click", () => noteModal.classList.add("hidden"));
    }

    if (saveNoteBtn && noteModal) {
        saveNoteBtn.addEventListener("click", async () => {
            const summary = (document.getElementById("input-rp-note-summary")?.value || "").trim();
            if (!summary) return;
            const location = (document.getElementById("input-rp-note-location")?.value || "").trim();
            const quest = (document.getElementById("input-rp-note-quest")?.value || "").trim();
            const cluesRaw = (document.getElementById("input-rp-note-clues")?.value || "").trim();
            const clues = cluesRaw ? cluesRaw.split(",").map(c => c.trim()).filter(Boolean) : [];

            if (window.pywebview && window.pywebview.api && window.pywebview.api.record_ttrpg_notes) {
                await window.pywebview.api.record_ttrpg_notes(summary, location, clues, quest, null);
                noteModal.classList.add("hidden");
                await loadRolePlayUI();
            }
        });
    }

    // Refresh Journal Notes Button
    const refreshJournalBtn = document.getElementById("btn-refresh-journal");
    if (refreshJournalBtn) {
        refreshJournalBtn.addEventListener("click", async () => {
            await loadRolePlayUI();
        });
    }

    // Start / Resume / Stop Adventure Button
    const startResumeCampBtn = document.getElementById("btn-start-resume-campaign");
    if (startResumeCampBtn) {
        startResumeCampBtn.addEventListener("click", async () => {
            if (isTogglingAdventure) {
                console.log("[RolePlay] Adventure toggle already in flight, ignoring duplicate click.");
                return;
            }
            isTogglingAdventure = true;
            startResumeCampBtn.disabled = true;
            startResumeCampBtn.style.opacity = "0.7";
            try {
                const uiApp = window.aetherUI || window.app;

                // If adventure is currently active, the user clicked "STOP ADVENTURE"
                if (isAdventureActive) {
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_ttrpg_session_active) {
                        await window.pywebview.api.set_ttrpg_session_active(false);
                    }
                    if (uiApp && uiApp.isAssistantRunning && typeof uiApp.toggleAssistant === "function") {
                        await uiApp.toggleAssistant();
                    }
                    isAdventureActive = false;
                    updateAdventureButtonState(false);
                    await loadRolePlayUI();
                    return;
                }

                // If starting/resuming adventure:
                // Auto-toggle Target Speaker Filter OFF so all players around the microphone can speak
                // and put engine in Tabletop Role Play Mode
                if (window.pywebview && window.pywebview.api && window.pywebview.api.set_ttrpg_session_active) {
                    await window.pywebview.api.set_ttrpg_session_active(true);
                }

                // If assistant is not running, start Aether by default!
                if (uiApp && !uiApp.isAssistantRunning && typeof uiApp.toggleAssistant === "function") {
                    await uiApp.toggleAssistant();
                    // Brief delay for connection establishment
                    await new Promise(r => setTimeout(r, 800));
                }

                isAdventureActive = true;
                updateAdventureButtonState(true);

                // Call backend to trigger adventure start or resume
                if (window.pywebview && window.pywebview.api && window.pywebview.api.start_or_resume_ttrpg_campaign) {
                    await window.pywebview.api.start_or_resume_ttrpg_campaign();
                }
            } catch (err) {
                console.error("[RolePlay] Error starting/stopping adventure:", err);
            } finally {
                isTogglingAdventure = false;
                startResumeCampBtn.disabled = false;
                startResumeCampBtn.style.opacity = "1";
            }
        });
    }

    // Setup Character Vault & Cross-Genre Modal Controls
    const openVaultBtn = document.getElementById("open-vault-btn");
    if (openVaultBtn) {
        openVaultBtn.addEventListener("click", () => openVaultModal());
    }

    const closeVaultBtn = document.getElementById("close-vault-modal-btn");
    const closeVaultFooter = document.getElementById("btn-close-vault-footer");
    if (closeVaultBtn) closeVaultBtn.addEventListener("click", () => closeVaultModal());
    if (closeVaultFooter) closeVaultFooter.addEventListener("click", () => closeVaultModal());

    const closeCrossBtn = document.getElementById("close-modal-btn");
    const cancelCrossBtn = document.getElementById("cancel-import-btn");
    const confirmCrossBtn = document.getElementById("confirm-crossover-btn");
    if (closeCrossBtn) closeCrossBtn.addEventListener("click", () => {
        const m = document.getElementById("crossover-modal");
        if (m) m.style.display = "none";
    });
    if (cancelCrossBtn) cancelCrossBtn.addEventListener("click", () => {
        const m = document.getElementById("crossover-modal");
        if (m) m.style.display = "none";
    });
    if (confirmCrossBtn) confirmCrossBtn.addEventListener("click", () => confirmCrossoverImport());

    // Weapon Modal Controls
    const addWeaponModal = document.getElementById("modal-add-weapon");
    const closeAddWeaponBtn = document.getElementById("btn-close-add-weapon-modal");
    const cancelAddWeaponBtn = document.getElementById("btn-cancel-add-weapon");
    const confirmAddWeaponBtn = document.getElementById("btn-confirm-add-weapon");

    if (closeAddWeaponBtn && addWeaponModal) {
        closeAddWeaponBtn.addEventListener("click", () => addWeaponModal.classList.add("hidden"));
    }
    if (cancelAddWeaponBtn && addWeaponModal) {
        cancelAddWeaponBtn.addEventListener("click", () => addWeaponModal.classList.add("hidden"));
    }
    if (confirmAddWeaponBtn) {
        confirmAddWeaponBtn.addEventListener("click", () => window.handleConfirmAddWeapon());
    }

    // Inventory Modal Controls
    const addInventoryModal = document.getElementById("modal-add-inventory");
    const closeAddInventoryBtn = document.getElementById("btn-close-add-inventory-modal");
    const cancelAddInventoryBtn = document.getElementById("btn-cancel-add-inventory");
    const confirmAddInventoryBtn = document.getElementById("btn-confirm-add-inventory");

    if (closeAddInventoryBtn && addInventoryModal) {
        closeAddInventoryBtn.addEventListener("click", () => addInventoryModal.classList.add("hidden"));
    }
    if (cancelAddInventoryBtn && addInventoryModal) {
        cancelAddInventoryBtn.addEventListener("click", () => addInventoryModal.classList.add("hidden"));
    }
    if (confirmAddInventoryBtn) {
        confirmAddInventoryBtn.addEventListener("click", () => window.handleConfirmAddInventory());
    }

    // Character Sheet In-Modal Add Weapon Buttons
    ["btn-modal-add-weapon-dnd", "btn-modal-add-weapon-pf", "btn-modal-add-weapon-cyber", "btn-modal-add-weapon-coc", "btn-modal-add-weapon-agnostic"].forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (btn) {
            btn.addEventListener("click", () => window.handleOpenAddWeaponModal("__active_edit__"));
        }
    });

    // Character Sheet In-Modal Add Inventory Button
    const btnModalAddInvDnd = document.getElementById("btn-modal-add-inventory-dnd");
    if (btnModalAddInvDnd) {
        btnModalAddInvDnd.addEventListener("click", () => window.handleOpenAddInventoryModal("__active_edit__"));
    }

    // Spell Modal Controls
    const addSpellModal = document.getElementById("modal-add-spell");
    const closeAddSpellBtn = document.getElementById("btn-close-add-spell-modal");
    const cancelAddSpellBtn = document.getElementById("btn-cancel-add-spell");
    const confirmAddSpellBtn = document.getElementById("btn-confirm-add-spell");

    if (closeAddSpellBtn && addSpellModal) {
        closeAddSpellBtn.addEventListener("click", () => addSpellModal.classList.add("hidden"));
    }
    if (cancelAddSpellBtn && addSpellModal) {
        cancelAddSpellBtn.addEventListener("click", () => addSpellModal.classList.add("hidden"));
    }
    if (confirmAddSpellBtn) {
        confirmAddSpellBtn.addEventListener("click", () => window.handleConfirmAddSpell());
    }

    // Character Sheet In-Modal Add Spell Buttons
    ["btn-modal-add-spell-dnd", "btn-modal-add-spell-pf", "btn-modal-add-spell-agnostic"].forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (btn) {
            btn.addEventListener("click", () => window.handleOpenAddSpellModal("__active_edit__"));
        }
    });

    // Spell Live Preview Listeners
    ["input-spell-name", "input-spell-atk", "input-spell-dmg", "input-spell-range", "input-spell-notes"].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", () => updateSpellLivePreview());
            el.addEventListener("change", () => updateSpellLivePreview());
        }
    });
    ["select-spell-level", "select-spell-dmg-type"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", () => updateSpellLivePreview());
    });

    // ⚡ Aether Smart Reformat Spell Button
    const btnSmartSpell = document.getElementById("btn-spell-smart-reformat");
    if (btnSmartSpell) {
        btnSmartSpell.addEventListener("click", () => window.handleSmartReformatSpell());
    }

    // Enter key triggers smart reformat on spell raw input
    const inputSpellSmartRaw = document.getElementById("input-spell-smart-raw");
    if (inputSpellSmartRaw) {
        inputSpellSmartRaw.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                window.handleSmartReformatSpell();
            }
        });
    }

    // Weapon Live Preview Listeners
    ["input-weapon-name", "input-weapon-atk", "input-weapon-dmg", "input-weapon-notes"].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", () => updateWeaponLivePreview());
            el.addEventListener("change", () => updateWeaponLivePreview());
        }
    });
    ["select-weapon-type", "select-weapon-dmg-type"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", () => updateWeaponLivePreview());
    });

    // Weapon Property Pills
    document.querySelectorAll(".btn-weapon-prop-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            const prop = pill.getAttribute("data-prop") || pill.textContent.replace(/^\+\s*/, "").trim();
            const notesInput = document.getElementById("input-weapon-notes");
            if (notesInput && prop) {
                const cur = notesInput.value.trim();
                if (!cur.toLowerCase().includes(prop.toLowerCase())) {
                    notesInput.value = cur ? `${cur}, ${prop}` : prop;
                    updateWeaponLivePreview();
                }
            }
        });
    });

    // ⚡ Aether Smart Reformat Weapon Button
    const btnSmartWeapon = document.getElementById("btn-weapon-smart-reformat");
    if (btnSmartWeapon) {
        btnSmartWeapon.addEventListener("click", async () => {
            const rawInput = document.getElementById("input-weapon-smart-raw");
            const rawVal = (rawInput?.value || "").trim();
            if (!rawVal) {
                alert("Please paste some weapon text into the box to reformat.");
                return;
            }

            const activeRuleset = (document.getElementById("select-rp-char-ruleset")?.value || "dnd_5e").toLowerCase();
            btnSmartWeapon.disabled = true;
            const origText = btnSmartWeapon.textContent;
            btnSmartWeapon.textContent = "⏳ Parsing...";

            try {
                let res = null;
                if (window.pywebview && window.pywebview.api && window.pywebview.api.reformat_ttrpg_weapons_or_items) {
                    res = await window.pywebview.api.reformat_ttrpg_weapons_or_items(rawVal, activeRuleset);
                }
                if (res && res.status === "success" && res.weapons && res.weapons.length > 0) {
                    const w = res.weapons[0];
                    const nameInput = document.getElementById("input-weapon-name");
                    const typeSelect = document.getElementById("select-weapon-type");
                    const atkInput = document.getElementById("input-weapon-atk");
                    const dmgInput = document.getElementById("input-weapon-dmg");
                    const dmgTypeSelect = document.getElementById("select-weapon-dmg-type");
                    const notesInput = document.getElementById("input-weapon-notes");

                    if (nameInput) nameInput.value = w.name || "";
                    if (typeSelect && w.type) typeSelect.value = w.type;
                    if (atkInput) atkInput.value = w.atk_bonus || "+0";
                    if (dmgInput) dmgInput.value = w.damage || "1d4";
                    if (dmgTypeSelect && w.damage_type) dmgTypeSelect.value = w.damage_type;
                    if (notesInput) notesInput.value = w.notes || "";

                    updateWeaponLivePreview();

                    if (res.weapons.length > 1) {
                        const targetMode = document.getElementById("input-add-weapon-char-name")?.value || "";
                        if (targetMode === "__active_edit__") {
                            for (let i = 1; i < res.weapons.length; i++) {
                                const extraW = res.weapons[i];
                                const extraDmg = extraW.damage_type && !extraW.damage.toLowerCase().includes(extraW.damage_type.toLowerCase()) ? `${extraW.damage} ${extraW.damage_type}` : extraW.damage;
                                stagedCharacterWeapons.push({
                                    name: extraW.name,
                                    type: extraW.type || "Melee Weapon",
                                    atk_bonus: extraW.atk_bonus || "+0",
                                    damage: extraDmg || "1d4",
                                    notes: extraW.notes || ""
                                });
                            }
                            renderStagedWeapons();
                            alert(`Reformatted '${w.name}' and also added ${res.weapons.length - 1} other weapon(s) to the character sheet!`);
                        }
                    }
                } else {
                    alert("Could not automatically parse weapon fields. Please enter them manually into the form.");
                }
            } catch (err) {
                console.error("[RolePlay] Error reformatting weapon:", err);
                alert(`Error reformatting: ${err.message}`);
            } finally {
                btnSmartWeapon.disabled = false;
                btnSmartWeapon.textContent = origText;
            }
        });
    }

    // ⚡ Aether Smart Reformat Inventory Button
    const btnSmartInv = document.getElementById("btn-inventory-smart-reformat");
    if (btnSmartInv) {
        btnSmartInv.addEventListener("click", async () => {
            const multiInput = document.getElementById("input-inventory-multiline");
            const rawVal = (multiInput?.value || "").trim();
            if (!rawVal) return;

            btnSmartInv.disabled = true;
            const origText = btnSmartInv.textContent;
            btnSmartInv.textContent = "⏳ Parsing...";
            try {
                if (window.pywebview && window.pywebview.api && window.pywebview.api.reformat_ttrpg_weapons_or_items) {
                    const res = await window.pywebview.api.reformat_ttrpg_weapons_or_items(rawVal, "dnd_5e");
                    if (res && res.status === "success") {
                        const items = res.items || [];
                        if (items.length > 0) {
                            multiInput.value = items.join("\n");
                        }
                        if (res.weapons && res.weapons.length > 0) {
                            alert(`Aether noticed ${res.weapons.length} weapon(s) in your text! You can add weapons cleanly via the + Add Weapon button.`);
                        }
                    }
                }
            } catch (err) {
                console.error("[RolePlay] Error smart formatting inventory:", err);
            } finally {
                btnSmartInv.disabled = false;
                btnSmartInv.textContent = origText;
            }
        });
    }

    // Wire Create New Vault Hero button
    const createVaultCharBtn = document.getElementById("btn-create-vault-char");
    if (createVaultCharBtn && charModal) {
        createVaultCharBtn.addEventListener("click", () => {
            editingCharacterId = null;
            editingCharacterSource = "vault";
            currentEditingCharacterObj = null;
            if (charHeading) charHeading.textContent = "Create New Vault Hero";

            const rulesetSelect = document.getElementById("select-rp-char-ruleset");
            const campPreset = ttrpgData.active_campaign_meta?.system_preset || "dnd_5e";
            let defaultRuleset = "dnd_5e";
            if (campPreset.includes("cyber")) defaultRuleset = "cyberpunk";
            else if (campPreset.includes("pathfinder")) defaultRuleset = "pathfinder_2e";
            else if (campPreset.includes("cthulhu")) defaultRuleset = "call_of_cthulhu";
            else if (campPreset.includes("agnostic") || campPreset.includes("sandbox") || campPreset.includes("custom")) defaultRuleset = "agnostic";

            if (rulesetSelect) rulesetSelect.value = defaultRuleset;
            onRulesetDropdownChanged(defaultRuleset);

            const nameEl = document.getElementById("input-rp-char-name");
            const playerEl = document.getElementById("input-rp-char-player");
            if (nameEl) nameEl.value = "";
            if (playerEl) playerEl.value = "";

            // Reset D&D fields
            const dndClassEl = document.getElementById("input-dnd-class-level");
            const dndSpeciesEl = document.getElementById("input-dnd-species");
            const dndBgEl = document.getElementById("input-dnd-background");
            const dndXpEl = document.getElementById("input-dnd-xp");
            if (dndClassEl) dndClassEl.value = "Fighter 1";
            if (dndSpeciesEl) dndSpeciesEl.value = "Human";
            if (dndBgEl) dndBgEl.value = "Noble";
            if (dndXpEl) dndXpEl.value = "0";
            const dndDefensesReset = document.getElementById("input-dnd-defenses");
            if (dndDefensesReset) dndDefensesReset.value = "";
            const dndInitReset = document.getElementById("input-dnd-init");
            if (dndInitReset) dndInitReset.dataset.customized = "false";

            renderDndSkillsGrid({});
            updateDndCalculations();

            stagedCharacterWeapons = [];
            renderStagedWeapons();
            stagedCharacterSpells = [];
            renderStagedSpells();

            charModal.classList.remove("hidden");
        });
    }

    // Wire Sheet Upload (PDF/JSON) into Vault (Dropzone and Button)
    const uploadSheetBtn = document.getElementById("btn-upload-vault-char");
    const vaultFileInput = document.getElementById("input-vault-file");
    const vaultDropzone = document.getElementById("vault-dropzone");

    async function handleUploadVaultFiles(files, rulesetOverride = null) {
        if (!files || files.length === 0) return;
        const origText = uploadSheetBtn ? uploadSheetBtn.textContent : "";
        if (uploadSheetBtn) {
            uploadSheetBtn.disabled = true;
            uploadSheetBtn.textContent = "⏳ Parsing Sheet...";
        }

        try {
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                await new Promise((resolve) => {
                    const reader = new FileReader();
                    reader.onload = async (evt) => {
                        try {
                            const b64 = evt.target.result.split(",")[1];
                            if (window.pywebview && window.pywebview.api && window.pywebview.api.upload_vault_character_document) {
                                const res = await window.pywebview.api.upload_vault_character_document(file.name, b64, rulesetOverride);
                                if (res && res.status === "requires_ruleset_selection") {
                                    promptRulesetSelection(res, async (selectedRuleset, req) => {
                                        const retryRes = await window.pywebview.api.upload_vault_character_document(
                                            req.filename,
                                            req.base64_content,
                                            selectedRuleset
                                        );
                                        if (retryRes && retryRes.status === "success") {
                                            alert(`Successfully imported character(s) from '${req.filename}' into the Character Vault!`);
                                            await refreshVaultModalUI();
                                            await loadRolePlayUI(true);
                                        } else {
                                            alert(`Failed to import character: ${retryRes?.message || 'Unknown error'}`);
                                        }
                                    });
                                } else if (res && res.status === "success") {
                                    alert(`Successfully imported character(s) from '${file.name}' into the Character Vault!`);
                                    await refreshVaultModalUI();
                                    await loadRolePlayUI(true);
                                } else {
                                    alert(`Failed to import character: ${res?.message || 'Unknown error'}`);
                                }
                            }
                        } catch (err) {
                            console.error("[Vault] Error processing uploaded sheet:", err);
                            alert(`Error uploading sheet: ${err.message}`);
                        }
                        resolve();
                    };
                    reader.onerror = () => resolve();
                    reader.readAsDataURL(file);
                });
            }
        } finally {
            if (uploadSheetBtn) {
                uploadSheetBtn.disabled = false;
                uploadSheetBtn.textContent = origText;
            }
        }
    }

    if (uploadSheetBtn && vaultFileInput) {
        uploadSheetBtn.addEventListener("click", () => {
            vaultFileInput.value = "";
            vaultFileInput.click();
        });
        vaultFileInput.addEventListener("change", async (e) => {
            if (e.target.files && e.target.files.length > 0) {
                await handleUploadVaultFiles(e.target.files);
            }
        });
    }

    if (vaultDropzone && vaultFileInput) {
        vaultDropzone.addEventListener("click", () => {
            vaultFileInput.value = "";
            vaultFileInput.click();
        });
        vaultDropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            vaultDropzone.classList.add("drag-over");
        });
        vaultDropzone.addEventListener("dragleave", () => {
            vaultDropzone.classList.remove("drag-over");
        });
        vaultDropzone.addEventListener("drop", async (e) => {
            e.preventDefault();
            vaultDropzone.classList.remove("drag-over");
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                await handleUploadVaultFiles(e.dataTransfer.files);
            }
        });
    }

    // Wire Ruleset Picker Modal Option Buttons & Cancel Handlers
    document.querySelectorAll(".btn-ruleset-option").forEach(btn => {
        btn.addEventListener("click", () => {
            const ruleset = btn.getAttribute("data-ruleset");
            if (pendingRulesetRequest && typeof pendingRulesetRequest.onSelectedCallback === "function") {
                const { requestData, onSelectedCallback } = pendingRulesetRequest;
                closeRulesetPickerModal();
                onSelectedCallback(ruleset, requestData);
            } else {
                closeRulesetPickerModal();
            }
        });
    });

    const closeRulesetBtn = document.getElementById("close-ruleset-picker-btn");
    const cancelRulesetBtn = document.getElementById("cancel-ruleset-picker-btn");
    if (closeRulesetBtn) closeRulesetBtn.addEventListener("click", closeRulesetPickerModal);
    if (cancelRulesetBtn) cancelRulesetBtn.addEventListener("click", closeRulesetPickerModal);

    // Wire Import Active Campaign Character into Vault
    const importFromCampBtn = document.getElementById("btn-import-campaign-to-vault");
    if (importFromCampBtn) {
        importFromCampBtn.addEventListener("click", async () => {
            try {
                let rosterChars = [];
                if (window.pywebview && window.pywebview.api && window.pywebview.api.list_campaign_characters_for_vault) {
                    rosterChars = await window.pywebview.api.list_campaign_characters_for_vault();
                } else if (ttrpgData.state && ttrpgData.state.characters) {
                    rosterChars = Object.values(ttrpgData.state.characters);
                }

                if (!rosterChars || rosterChars.length === 0) {
                    alert("No characters found in the active campaign party roster.");
                    return;
                }

                const names = rosterChars.map(c => c.name).join(", ");
                const chosenName = prompt(
                    `Enter character name to preserve in the Vault as a Canonical Root hero:\n\nActive Heroes: ${names}`,
                    rosterChars[0].name
                );
                if (!chosenName || !chosenName.trim()) return;

                if (window.pywebview && window.pywebview.api && window.pywebview.api.save_campaign_character_to_vault) {
                    const res = await window.pywebview.api.save_campaign_character_to_vault(chosenName.trim());
                    if (res && res.status === "success") {
                        alert(`Hero '${chosenName.trim()}' preserved in Character Vault as a Canonical Root!`);
                        await refreshVaultModalUI();
                    } else {
                        alert(`Could not save character: ${res?.message || 'Unknown error'}`);
                    }
                }
            } catch (err) {
                console.error("[Vault] Error saving from campaign:", err);
                alert(`Error saving hero to vault: ${err.message}`);
            }
        });
    }

    // Setup background auto-refresh for notes and live combat ladder
    setupRoleplayAutoRefresh();
});

// Standalone function for Party Roster card "Save to Vault" button
window.handleExportRosterCharToVault = async function(charName) {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_campaign_character_to_vault) return;
    if (!(await showAetherConfirm(`Save '${charName}' permanently into the Character Vault as a Canonical Root hero?`, "Save to Vault", "Save Hero", false))) return;
    try {
        const res = await window.pywebview.api.save_campaign_character_to_vault(charName);
        if (res && res.status === "success") {
            await showAetherAlert(`Hero '${charName}' has been successfully preserved in the Character Vault!`, "Character Saved");
            await refreshVaultModalUI();
        } else {
            await showAetherAlert(res?.message || "Failed saving character to vault.", "Vault Notice");
        }
    } catch (e) {
        console.error("[Vault] Error saving roster character to vault:", e);
    }
};

let pendingCrossover = null;

async function refreshVaultModalUI() {
    const container = document.getElementById("vault-characters-container");
    const countBadge = document.getElementById("vault-count-badge");
    if (!container) return;

    let chars = [];
    if (window.pywebview && window.pywebview.api && window.pywebview.api.list_vault_characters) {
        try {
            chars = await window.pywebview.api.list_vault_characters();
        } catch (e) {
            console.error("[Vault] Error listing vault characters:", e);
        }
    }

    if (countBadge) {
        countBadge.textContent = `${chars.length} CHARACTERS IN VAULT`;
    }

    if (chars.length === 0) {
        container.innerHTML = `
            <div style="text-align: center; color: #64748b; font-size: 12px; padding: 24px 8px;">
                No characters in the vault yet.<br>Click <strong>📄 Upload Sheet (PDF)</strong> or <strong>🏰 From Campaign</strong> to add your first character.
            </div>
        `;
        return;
    }

    container.innerHTML = chars.map(c => {
        const isRoot = Boolean(c.is_root);
        const badgeClass = isRoot ? "vault-char-badge-root" : "vault-char-badge-crossover";
        const badgeText = isRoot ? "CANONICAL ROOT" : (c.variant_label || "CROSSOVER VARIANT");
        const rulesetKey = c.ruleset || c.native_system || c.native_genre || "dnd_5e";
        const rulesetLabel = c.ruleset_label || getRulesetDisplayLabel(rulesetKey);
        const genreLabel = SYSTEM_PRESET_LABELS[c.native_genre] || c.native_genre || "Heroic Fantasy";
        const currentGenreLabel = c.current_genre ? (SYSTEM_PRESET_LABELS[c.current_genre] || c.current_genre) : genreLabel;
        const parentHint = (!isRoot && c.parent_id) ? `<span style="font-size: 10px; color: #94a3b8; margin-left: 6px;">(Branch from: ${escapeRpHtml(c.parent_id)})</span>` : "";

        return `
            <div class="vault-char-card" data-char-id="${escapeRpHtml(c.id)}">
                <div style="display: flex; flex-direction: column; gap: 4px;">
                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #f1f5f9; font-size: 13.5px;">${escapeRpHtml(c.name)}</span>
                        <span class="${badgeClass}">${escapeRpHtml(badgeText)}</span>
                        <span class="rp-badge ruleset-badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); font-size: 10px; padding: 2px 7px; border-radius: 4px; font-weight: 600;">📜 ${escapeRpHtml(rulesetLabel)}</span>
                        ${parentHint}
                    </div>
                    <div style="font-size: 11px; color: #94a3b8;">
                        Lv. ${c.level || 1} ${escapeRpHtml(c.archetype || "Hero")} • Ruleset: <span style="color: #38bdf8; font-weight: 600;">${escapeRpHtml(rulesetLabel)}</span>
                        ${!isRoot ? ` • Active Setting: <span style="color: #c084fc;">${escapeRpHtml(currentGenreLabel)}</span>` : ""}
                    </div>
                </div>
                <div style="display: flex; gap: 6px; align-items: center;">
                    <button type="button" class="cyber-btn-small btn-vault-import" data-char-id="${escapeRpHtml(c.id)}" style="background: rgba(34, 197, 94, 0.15); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.4);" title="Import into active campaign">
                        📥 Import
                    </button>
                    <button type="button" class="cyber-btn-small btn-vault-edit" data-char-id="${escapeRpHtml(c.id)}" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4);" title="Edit character sheet in Character Editor">
                        ✏️ Edit
                    </button>
                    <button type="button" class="cyber-btn-small btn-vault-levelup" data-char-id="${escapeRpHtml(c.id)}" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4);" title="Level up hero">
                        ⬆️ Level
                    </button>
                    <button type="button" class="cyber-btn-small btn-vault-delete" data-char-id="${escapeRpHtml(c.id)}" style="background: rgba(239, 68, 68, 0.12); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35);" title="Delete from vault">
                        🗑️
                    </button>
                </div>
            </div>
        `;
    }).join("");

    // Attach actions to dynamically created buttons
    container.querySelectorAll(".btn-vault-import").forEach(btn => {
        btn.addEventListener("click", () => handleVaultImport(btn.getAttribute("data-char-id")));
    });
    container.querySelectorAll(".btn-vault-edit").forEach(btn => {
        btn.addEventListener("click", () => handleOpenEditCharacterModal(btn.getAttribute("data-char-id"), "vault"));
    });
    container.querySelectorAll(".btn-vault-levelup").forEach(btn => {
        btn.addEventListener("click", () => handleVaultLevelUp(btn.getAttribute("data-char-id")));
    });
    container.querySelectorAll(".btn-vault-delete").forEach(btn => {
        btn.addEventListener("click", () => handleVaultDelete(btn.getAttribute("data-char-id")));
    });
}

async function handleVaultImport(charId) {
    if (!window.pywebview || !window.pywebview.api) return;

    let charData = null;
    try {
        charData = await window.pywebview.api.get_vault_character(charId);
    } catch (e) {
        console.error("[Vault] Failed fetching character details:", e);
        return;
    }
    if (!charData) return;

    const activeMeta = ttrpgData.active_campaign_meta || {};
    const activeRuleset = activeMeta.system_preset || activeMeta.ruleset || activeMeta.genre || "heroic_d20";
    const charRuleset = charData.ruleset || charData.native_system || charData.native_genre || "heroic_d20";

    // Check if matching ruleset or compatible family (e.g. D&D 5E character into D&D 5E / heroic_d20 campaign)
    // If compatible, import directly into active campaign with zero cross-genre adjudication or quarantine!
    if (areRulesetsCompatible(charRuleset, activeRuleset)) {
        try {
            await window.pywebview.api.import_vault_character_to_campaign(charId, null, false);
            closeVaultModal();
            await loadRolePlayUI(true);
        } catch (e) {
            console.error("[Vault] Error importing character:", e);
        }
        return;
    }

    // Cross-genre branch required! Open Adjudication Modal
    openCrossoverAdjudicationModal(charData, activeRuleset);
}

function openCrossoverAdjudicationModal(charData, targetGenre) {
    pendingCrossover = { charId: charData.id, targetGenre: targetGenre };

    const modal = document.getElementById("crossover-modal");
    const desc = document.getElementById("crossover-desc");
    const container = document.getElementById("adjudication-items-container");
    if (!modal || !desc || !container) return;

    const genreLabel = SYSTEM_PRESET_LABELS[targetGenre] || targetGenre.replace(/_/g, " ").toUpperCase();
    const nativeGenreLabel = SYSTEM_PRESET_LABELS[charData.native_genre] || (charData.native_genre || "Heroic Fantasy");

    const allItems = [];
    const seen = new Set();

    (charData.inventory || []).forEach(item => {
        const itName = typeof item === "string" ? item.trim() : (item?.name || item?.item || item?.label || "").trim();
        if (itName && !seen.has(itName.toLowerCase())) {
            seen.add(itName.toLowerCase());
            allItems.push({ name: itName, type: "Gear", original: item });
        }
    });

    (charData.weapons || []).forEach(w => {
        const wName = typeof w === "string" ? w.trim() : (w?.name || "").trim();
        if (wName && !seen.has(wName.toLowerCase())) {
            seen.add(wName.toLowerCase());
            allItems.push({ name: wName, type: "Weapon", original: w });
        }
    });

    desc.innerHTML = `
        <strong>${escapeRpHtml(charData.name)}</strong> (${escapeRpHtml(nativeGenreLabel)}) is entering a <strong>${escapeRpHtml(genreLabel)}</strong> setting.<br>
        ${allItems.length} item(s) require resolution:
    `;

    if (allItems.length === 0) {
        container.innerHTML = `
            <div style="color: #64748b; font-size: 12px; padding: 12px 4px; text-align: center;">
                No physical items requiring cross-genre adjudication.
            </div>
        `;
    } else {
        container.innerHTML = allItems.map(item => {
            const itName = item.name;
            const icon = item.type === "Weapon" ? "⚔️" : "🎒";
            return `
                <div class="adjudication-row" style="display: flex; justify-content: space-between; align-items: center; padding: 6px 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); gap: 10px;">
                    <div style="display: flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span style="font-size: 0.85rem;">${icon}</span>
                        <span class="item-name" style="font-weight: 600; color: #f1f5f9; font-size: 0.82rem;">${escapeRpHtml(itName)}</span>
                        <span style="font-size: 0.68rem; color: #94a3b8; background: rgba(148, 163, 184, 0.15); padding: 1px 5px; border-radius: 3px;">${item.type}</span>
                    </div>
                    <select class="cyber-dropdown select-item-adjudication" data-item-name="${escapeRpHtml(itName)}" style="max-width: 280px; font-size: 0.78rem;">
                        <option value="quarantine">Move to Quarantined Stash (Dormant)</option>
                        <option value="transmute">Transmute to In-Genre Equivalent (Fair Spec)</option>
                        <option value="relic">Permit as Ancient Relic</option>
                    </select>
                </div>
            `;
        }).join("");
    }

    modal.style.display = "flex";
}

async function confirmCrossoverImport() {
    if (!pendingCrossover) return;
    const modal = document.getElementById("crossover-modal");
    const container = document.getElementById("adjudication-items-container");
    const confirmBtn = document.getElementById("confirm-crossover-btn");

    const adjudications = {};
    if (container) {
        container.querySelectorAll(".select-item-adjudication").forEach(sel => {
            const itName = sel.getAttribute("data-item-name");
            if (itName) {
                adjudications[itName] = sel.value;
            }
        });
    }

    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Forking Branch...";
    }

    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.import_vault_character_to_campaign) {
            await window.pywebview.api.import_vault_character_to_campaign(
                pendingCrossover.charId,
                null,
                true,
                adjudications
            );
        }
        if (modal) modal.style.display = "none";
        closeVaultModal();
        pendingCrossover = null;
        await loadRolePlayUI(true);
    } catch (e) {
        console.error("[Vault] Error confirming crossover:", e);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Confirm & Fork Branch";
        }
    }
}

async function handleVaultLevelUp(charId) {
    if (!window.pywebview || !window.pywebview.api) return;
    const boostAttr = prompt("Optional: Enter attribute to increase by +1 (e.g. Agility, Might, Vigor):", "Agility");
    const statBoosts = boostAttr && boostAttr.trim() ? { [boostAttr.trim()]: 1 } : null;

    try {
        await window.pywebview.api.level_up_vault_character(charId, 1, statBoosts);
        await refreshVaultModalUI();
        await loadRolePlayUI(true);
    } catch (e) {
        console.error("[Vault] Error leveling up character:", e);
    }
}

async function handleVaultDelete(charId) {
    if (!(await showAetherConfirm("Are you sure you want to delete this character from the vault?", "Delete Character", "Delete", true))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_vault_character) {
            await window.pywebview.api.delete_vault_character(charId);
            await refreshVaultModalUI();
        }
    } catch (e) {
        console.error("[Vault] Error deleting character:", e);
    }
}

function openVaultModal() {
    const modal = document.getElementById("vault-modal");
    if (modal) {
        modal.style.display = "flex";
        refreshVaultModalUI();
    }
}

function closeVaultModal() {
    const modal = document.getElementById("vault-modal");
    if (modal) modal.style.display = "none";
}

let roleplayRefreshInterval = null;
function setupRoleplayAutoRefresh() {
    if (roleplayRefreshInterval) clearInterval(roleplayRefreshInterval);
    roleplayRefreshInterval = setInterval(() => {
        const rpTab = document.getElementById("tab-roleplay");
        if (rpTab && !rpTab.classList.contains("hidden")) {
            // Check if any modal is open
            const openModal = document.querySelector(".modal-overlay:not(.hidden)");
            if (!openModal) {
                loadRolePlayUI(true);
            }
        }
    }, 4500);
}

// =========================================================================
// Single-Mic Tabletop Coordination Controls & Clarification Intercepts
// =========================================================================

// 1. Tabletop Focus & Caller Button Dispatchers
document.addEventListener('click', (e) => {
  // Focus Speaker Toggle
  if (e.target.classList.contains('focus-speaker-btn')) {
    const card = e.target.closest('.character-card');
    if (!card) return;
    const charId = card.getAttribute('data-char-id');
    
    document.querySelectorAll('.character-card').forEach(c => c.classList.remove('active-speaker'));
    card.classList.add('active-speaker');
    
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_active_speaker_focus(charId);
    }
  }

  // Set Party Caller Toggle
  if (e.target.classList.contains('set-caller-btn')) {
    const card = e.target.closest('.character-card');
    if (!card) return;
    const charId = card.getAttribute('data-char-id');
    const nameEl = card.querySelector('.char-name');
    const charName = nameEl ? nameEl.innerText.trim() : "";

    document.querySelectorAll('.caller-crown').forEach(badge => badge.classList.remove('active'));
    const crown = card.querySelector('.caller-crown');
    if (crown) crown.classList.add('active');

    if (window.pywebview && window.pywebview.api && charName) {
      window.pywebview.api.set_party_caller(charName);
    }
  }
});

// 2. Real-time Backend Event Listeners
window.addEventListener('aether_speaker_focused', (event) => {
  const { character_id } = event.detail || {};
  document.querySelectorAll('.character-card').forEach(c => {
    if (c.getAttribute('data-char-id') === character_id) {
      c.classList.add('active-speaker');
    } else {
      c.classList.remove('active-speaker');
    }
  });
});

window.addEventListener('aether_clarification_requested', (event) => {
  const { question, action } = event.detail || {};
  console.log(`[AETHER CLARIFICATION] ${question} (Action: ${action})`);
  // Optional: Visual toast notification for physical players glancing at the TV
  showClarificationToast(question);
});

function showClarificationToast(text) {
  let toast = document.getElementById('tabletop-clarification-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'tabletop-clarification-toast';
    toast.style.cssText = `
      position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
      background: #1b2838; border: 2px solid #00F3FF; color: #fff;
      padding: 12px 24px; border-radius: 8px; font-weight: bold;
      box-shadow: 0 0 20px rgba(0,243,255,0.4); z-index: 9999;
    `;
    document.body.appendChild(toast);
  }
  toast.innerText = `🎙️ ${text}`;
  toast.style.display = 'block';
  setTimeout(() => { toast.style.display = 'none'; }, 6000);
}
