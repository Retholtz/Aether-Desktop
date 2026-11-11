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

const CONDITION_ICONS = {
    "drunk": { icon: "🍺", desc: "Disadvantage on attack rolls & ability checks (Dex/Int/Wis)." },
    "intoxicated": { icon: "🍺", desc: "Disadvantage on attack rolls & ability checks." },
    "confused": { icon: "🌀", desc: "Disadvantage on Int/Wis checks, no reactions, rolls 1d10 behavior." },
    "poisoned": { icon: "☠️", desc: "Disadvantage on attack rolls and ability checks." },
    "frightened": { icon: "😨", desc: "Disadvantage on checks and attacks while source in sight." },
    "blinded": { icon: "🙈", desc: "Disadvantage on attacks; attacks against have advantage." },
    "restrained": { icon: "🕸️", desc: "Speed 0; disadvantage on attacks and Dex saves." },
    "prone": { icon: "🧎", desc: "Disadvantage on attacks; melee attacks within 5ft have advantage." },
    "charmed": { icon: "💖", desc: "Cannot attack the charmer or target them with harmful spells." },
    "stunned": { icon: "💫", desc: "Incapacitated: cannot take actions/reactions, auto-fails Str/Dex saves." },
    "paralyzed": { icon: "⚡", desc: "Incapacitated: cannot move/speak, auto-fails Str/Dex saves." },
    "unconscious": { icon: "💤", desc: "Incapacitated: unaware, drops items, prone, fails Str/Dex saves." },
    "incapacitated": { icon: "🛑", desc: "Cannot take actions, bonus actions, or reactions." },
    "petrified": { icon: "🗿", desc: "Turned to stone: incapacitated, fails Str/Dex saves." },
    "commanded: halt": { icon: "🛑", desc: "Commanded to halt: cannot take actions or move on turn." },
    "dead": { icon: "💀", desc: "Deceased. Cannot take turns, actions, or rolls until revived." },
    "stable": { icon: "🩹", desc: "Stable at 0 HP. Unconscious, but does not roll death saves." }
};

function getConditionMeta(condStr) {
    const raw = String(condStr || "").trim();
    const clean = raw.split("(")[0].trim().toLowerCase();
    return CONDITION_ICONS[clean] || { icon: "⚠️", desc: raw };
}

const DND_XP_THRESHOLDS = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500,
    6: 14000, 7: 23000, 8: 34000, 9: 48000, 10: 64000,
    11: 85000, 12: 100000, 13: 120000, 14: 140000, 15: 165000,
    16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000
};
window.DND_XP_THRESHOLDS = DND_XP_THRESHOLDS;

function parseTotalLevelFromClass(str, fallbackLevel = 1) {
    if (!str || typeof str !== "string") return Math.max(1, parseInt(fallbackLevel, 10) || 1);
    const clean = str.replace(/[\(\[].*?[\)\]]/g, "").trim();
    const segments = clean.split(/[/,|]+/).map(s => s.trim()).filter(Boolean);
    let total = 0;
    for (const seg of segments) {
        const m = seg.match(/\d+/);
        if (m) {
            total += parseInt(m[0], 10) || 0;
        }
    }
    return total > 0 ? total : Math.max(1, parseInt(fallbackLevel, 10) || 1);
}
window.parseTotalLevelFromClass = parseTotalLevelFromClass;

function getDndNextLevelXp(level) {
    const lvl = Math.max(1, Math.min(20, parseInt(level, 10) || 1));
    return DND_XP_THRESHOLDS[Math.min(20, lvl + 1)] || 355000;
}
window.getDndNextLevelXp = getDndNextLevelXp;

async function checkAndPromptLevelUp(charName, currentXp, currentLevel) {
    const curLvl = parseInt(currentLevel, 10) || 1;
    if (!charName || curLvl >= 20) return;
    const curXp = parseInt(currentXp, 10) || 0;
    const nextReq = getDndNextLevelXp(curLvl);
    if (curXp >= nextReq) {
        const wizardModal = document.getElementById("levelup-wizard-modal");
        if (wizardModal && !wizardModal.classList.contains("hidden") && wizardModal.style.display !== "none") return;

        const proceed = await showAetherConfirm(
            `🎉 Level Up Ready!\n\n${charName} has reached ${curXp.toLocaleString()} / ${nextReq.toLocaleString()} XP and qualifies for Level ${curLvl + 1}!\n\nWould you like to open the Guided Level-Up Wizard now?`,
            "Level Up Ready!",
            "Level Up Now ⭐",
            false
        );
        if (proceed) {
            await openLevelUpWizard(charName, "campaign", { reason: `Reached ${curXp.toLocaleString()} XP milestone` });
        }
    }
}
window.checkAndPromptLevelUp = checkAndPromptLevelUp;

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
    renderWorldStateLedger();
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
        const label = diffMap[diff] || (diff.charAt(0).toUpperCase() + diff.slice(1) + " DM");
        const diffText = document.getElementById("rp-active-difficulty-text");
        if (diffText) {
            diffText.textContent = label;
        } else {
            diffBadge.textContent = label;
        }
        diffBadge.className = `rp-badge rp-diff-btn diff-${diff}`;

        const dropdown = document.getElementById("rp-difficulty-dropdown");
        if (dropdown) {
            dropdown.querySelectorAll(".rp-diff-opt").forEach(opt => {
                opt.classList.toggle("selected", opt.getAttribute("data-diff") === diff);
            });
        }
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
    const roundInd = document.getElementById("rp-round-indicator");
    const endCombatBtn = document.getElementById("end-combat-btn");
    const nextTurnBtn = document.getElementById("next-turn-btn");
    const ladderEl = document.getElementById("initiative-ladder");
    if (!ladderEl) return;

    const state = ttrpgData.state || {};
    if (roundEl) {
        roundEl.textContent = String(state.current_round || 1);
    }

    const order = Array.isArray(state.initiative_order) ? state.initiative_order : [];
    const activeIdx = Number(state.active_turn_index || 0);

    // Living hostile enemies determine active combat mode
    const hasLivingEnemies = order.some(item => !item.is_player && item.status !== "Defeated" && (item.current_hp === undefined || item.current_hp > 0));
    const isCombatActive = order.length > 0 && hasLivingEnemies;

    if (endCombatBtn) {
        endCombatBtn.style.display = isCombatActive ? "inline-block" : "none";
    }
    if (nextTurnBtn) {
        nextTurnBtn.style.display = isCombatActive ? "inline-block" : "none";
    }
    if (roundInd) {
        roundInd.textContent = isCombatActive ? `(ROUND ${state.current_round || 1})` : `(Out of Combat)`;
    }

    ladderEl.innerHTML = "";
    if (!isCombatActive) {
        ladderEl.innerHTML = `
            <div class="ladder-exploration-mode">
                <div class="ladder-exp-icon">🍃</div>
                <div class="ladder-exp-title">Exploration Mode (Out of Combat)</div>
                <div class="ladder-exp-sub">Active combat is off. The party acts freely in open narrative. Turn order resumes automatically when combat begins.</div>
            </div>
        `;
        return;
    }

    order.forEach((item, idx) => {
        const row = document.createElement("div");
        const isActive = idx === activeIdx;
        const curHp = Number(item.current_hp ?? 20);
        const maxHp = Number(item.max_hp ?? 20);
        const isDefeated = item.status === "Defeated" || curHp <= 0;

        row.className = `ladder-item${isActive ? " active" : ""}${isDefeated ? " defeated" : ""}`;
        const badgeIcon = item.is_player ? "🛡️" : "⚔️";

        let hpDisplay = "";
        if (item.is_player) {
            const char = (ttrpgData.state?.characters && (ttrpgData.state.characters[item.entity_id] || Object.values(ttrpgData.state.characters).find(c => c.name?.toLowerCase() === item.name?.toLowerCase())));
            const hp = char?.vitals?.health?.current ?? item.current_hp;
            const max = char?.vitals?.health?.max ?? item.max_hp;
            const ds = char?.death_saves || item.death_saves;
            const dsStatus = String(ds?.status || "").toLowerCase();

            if (hp !== undefined && hp <= 0) {
                if (dsStatus === "dead" || ds?.failures >= 3 || item.status === "Dead") {
                    hpDisplay = `<span class="ladder-hp-pill" style="background: rgba(100, 116, 139, 0.35); color: #94a3b8; border: 1px solid #64748b;" title="Player is Dead">💀 Dead</span>`;
                } else if (dsStatus === "stable" || ds?.successes >= 3 || item.status?.includes("Stable")) {
                    hpDisplay = `<span class="ladder-hp-pill" style="background: rgba(14, 165, 233, 0.3); color: #38bdf8; border: 1px solid #0284c7;" title="Player is Stable at 0 HP">🩹 Stable (0 HP)</span>`;
                } else {
                    hpDisplay = `<span class="ladder-hp-pill" style="background: rgba(239, 68, 68, 0.35); color: #f87171; border: 1px solid #ef4444;" title="Player is Dying: Death Saves: ${ds?.successes || 0}S / ${ds?.failures || 0}F">💀 Dying (0 HP)</span>`;
                }
            } else if (hp !== undefined && max !== undefined) {
                hpDisplay = `<span class="ladder-hp-pill player-hp" title="Player Health: ${hp}/${max}">${hp}/${max} HP</span>`;
            }
        } else {
            // Enemy Combatant: Option A - D&D 5e Qualitative Health Status (DM-Only raw numbers)
            let status = item.status;
            if (!status) {
                if (curHp <= 0) status = "Defeated";
                else if (maxHp > 0 && curHp / maxHp <= 0.15) status = "Critical";
                else if (maxHp > 0 && curHp / maxHp <= 0.50) status = "Bloodied";
                else if (maxHp > 0 && curHp < maxHp) status = "Injured";
                else status = "Healthy";
            }

            let badgeClass = "status-healthy";
            let badgeLabel = "🟢 Healthy";
            if (status === "Defeated" || curHp <= 0) {
                badgeClass = "status-defeated";
                badgeLabel = "💀 Defeated";
            } else if (status === "Critical" || (maxHp > 0 && curHp / maxHp <= 0.15)) {
                badgeClass = "status-critical";
                badgeLabel = "🔴 Critical";
            } else if (status === "Bloodied" || (maxHp > 0 && curHp / maxHp <= 0.50)) {
                badgeClass = "status-bloodied";
                badgeLabel = "🟠 Bloodied";
            } else if (status === "Injured" || (maxHp > 0 && curHp < maxHp)) {
                badgeClass = "status-injured";
                badgeLabel = "🟡 Injured";
            }

            if (state.show_exact_enemy_hp) {
                hpDisplay = `<span class="ladder-hp-pill enemy-status ${badgeClass}" title="${status}: ${curHp}/${maxHp} HP">${curHp}/${maxHp} HP</span>`;
            } else {
                hpDisplay = `<span class="ladder-hp-pill enemy-status ${badgeClass}" title="D&D Status: ${status}">${badgeLabel}</span>`;
            }
        }

        const itemConds = Array.isArray(item.conditions) ? item.conditions : [];
        const ladderCondIcons = itemConds.map(c => {
            const meta = getConditionMeta(c);
            return `<span title="${escapeRpHtml(c)}: ${escapeRpHtml(meta.desc)}" style="font-size: 11px; margin-left: 2px;">${meta.icon}</span>`;
        }).join("");

        row.innerHTML = `
            <div style="display: flex; align-items: center; gap: 6px; overflow: hidden; flex: 1; min-width: 0;">
                <span style="flex-shrink: 0;">${isActive ? "▶" : badgeIcon}</span>
                <span class="ladder-name" style="text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${escapeRpHtml(item.name)}</span>
                ${ladderCondIcons}
            </div>
            <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                ${hpDisplay}
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
        ch.tagged_inventory,
        ch.inventory,
        ch.live_inventory,
        ch.native_system_data?.inventory,
        ch.native_system_data?.tagged_inventory,
        ch.universal_core?.tagged_inventory,
        ch.universal_core?.inventory
    ];
    // Prioritize structured arrays containing objects with name or weight
    for (const cand of candidates) {
        if (Array.isArray(cand) && cand.length > 0 && cand.some(i => typeof i === "object" && i !== null)) {
            return cand.map(i => {
                if (typeof i === "object" && i !== null) return i;
                let s = String(i || "").trim();
                if (s.includes("(") && !s.includes(")")) s += ")";
                return s;
            }).filter(Boolean);
        }
    }
    // Fallback to flat string arrays or delimiter-separated strings
    for (const cand of candidates) {
        if (Array.isArray(cand) && cand.length > 0) {
            return cand.map(i => {
                if (typeof i === "object" && i !== null) return i;
                let s = String(i || "").trim();
                if (s.includes("(") && !s.includes(")")) s += ")";
                return s;
            }).filter(Boolean);
        }
        if (typeof cand === "string" && cand.trim().length > 0) {
            return cand.split(/[\n,]+/).map(i => {
                let s = i.trim().replace(/^[-•*]\s*/, "");
                if (s.includes("(") && !s.includes(")")) s += ")";
                return s;
            }).filter(Boolean);
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

/**
 * ui/static/roleplay.js - Party Roster Spell Section Renderer
 */
function renderCardSpellsSection(char) {
  const native = char.native_system_data || {};
  const allSpells = (Array.isArray(native.spells) && native.spells.length > 0) ? native.spells : (Array.isArray(char.spells) ? char.spells : []);
  const slots = native.spell_slots || char.spell_slots || {};

  if (allSpells.length === 0 && Object.keys(slots).length === 0) {
    return ''; // Non-caster (e.g. Pure Fighter)
  }

  // 1. Filter: Prepared Spells & At-Will Cantrips
  const activeSpells = allSpells.filter(s => {
    if (!s) return false;
    if (typeof s === "string") return true;
    return Boolean(s.is_cantrip || s.prepared);
  });
  const unpreparedSpells = allSpells.filter(s => {
    if (!s) return false;
    if (typeof s === "string") return false;
    return !s.is_cantrip && !s.prepared;
  });

  const charId = char.id || char.name || "char";

  // 2. Build Interactive Slot Pips Bar
  let slotsBarHtml = '<div class="card-spell-slots-bar">';
  let hasSlots = false;

  for (let lvl = 1; lvl <= 9; lvl++) {
    const sData = slots[`level_${lvl}`];
    if (sData && sData.max > 0) {
      hasSlots = true;
      let pips = '';
      for (let i = 0; i < sData.max; i++) {
        const isExpended = i >= sData.current;
        pips += `<span class="slot-pip ${isExpended ? 'expended' : ''}" 
                       data-char-id="${escapeRpHtml(String(charId))}" 
                       data-level="${lvl}" 
                       data-index="${i}" 
                       title="Level ${lvl} Slot ${i+1} (${isExpended ? 'Expended - click to recover' : 'Available - click to expend'})"></span>`;
      }
      slotsBarHtml += `
        <div class="card-slot-rank-group">
          <span class="card-slot-rank-label">L${lvl}:</span>
          <div class="card-slot-pips">${pips}</div>
        </div>
      `;
    }
  }
  slotsBarHtml += '</div>';

  if (!hasSlots) {
    slotsBarHtml = ''; // Hide slot bar if character has only cantrips
  }

  // 3. Render Active Spell Items
  const activeSpellsHtml = activeSpells.map(s => renderSingleSpellRow(charId, s, slots)).join('');

  // 4. Render Collapsible Unprepared Drawer (if any exist)
  let unpreparedDrawerHtml = '';
  if (unpreparedSpells.length > 0) {
    const unprepListHtml = unpreparedSpells.map(s => renderSingleSpellRow(charId, s, slots, true)).join('');
    const charIdSafe = escapeRpJs(String(charId));
    unpreparedDrawerHtml = `
      <button type="button" class="unprepared-drawer-btn" onclick="toggleUnpreparedDrawer('${charIdSafe}')">
        <span id="drawer-arrow-${charIdSafe}">▶</span> Show ${unpreparedSpells.length} Unprepared Spells
      </button>
      <div id="unprepared-container-${charIdSafe}" class="unprepared-spells-container">
        ${unprepListHtml}
      </div>
    `;
  }

  const charNameSafe = escapeRpJs(String(char.name || charId));
  const prepLeveledCount = allSpells.filter(s => {
    if (!s || typeof s === "string") return false;
    const isCantrip = (s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (String(s.level || "").toLowerCase().includes("cantrip") || s.level === 0 || s.level === "0");
    const isAlwaysPrep = Boolean(s.is_always_prepared);
    return !isCantrip && !isAlwaysPrep && Boolean(s.prepared);
  }).length;
  const maxPrepAllowed = (typeof char.max_prepared_leveled_spells === "number")
    ? char.max_prepared_leveled_spells
    : (native.max_prepared_leveled_spells || 0);

  const isOverPrepared = (maxPrepAllowed > 0 && prepLeveledCount > maxPrepAllowed);
  const statusBadgeHtml = isOverPrepared
    ? `<span class="badge-count" style="color: #f87171; background: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); padding: 1px 6px; border-radius: 4px; font-size: 9.5px; font-weight: 700; cursor: pointer;" title="Character is over spell preparation limit! Click to edit character and unprepare spells." onclick="handleOpenEditCharacterModal('${escapeRpJs(String(charId))}')">⚠️ OVER LIMIT: ${prepLeveledCount}/${maxPrepAllowed}</span>`
    : `<span class="badge-count" style="color: #94a3b8; font-size: 9.5px; font-weight: 600;">${activeSpells.length} ACTIVE</span>`;

  return `
    <div class="card-section spells-section char-spells-shelf" style="margin-top: 8px; background: rgba(15, 23, 42, 0.45); border: 1px solid rgba(168, 85, 247, 0.25); border-radius: 6px; padding: 6px 8px;">
      <div class="section-header" style="font-size: 10px; font-weight: 700; color: #c084fc; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center;">
        <span class="section-title">✨ SPELLS &amp; CANTRIPS</span>
        <div style="display: flex; gap: 6px; align-items: center;">
          ${statusBadgeHtml}
          <button type="button" class="add-sub-item-btn cyber-btn-small" style="padding: 1px 7px; font-size: 9.5px; background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.35);" onclick="handleOpenAddSpellModal('${charNameSafe}')">+ Add Spell</button>
        </div>
      </div>
      ${slotsBarHtml}
      <div class="active-spells-list">
        ${activeSpellsHtml.length ? activeSpellsHtml : '<div class="empty-state" style="font-size: 10.5px; color: #64748b; font-style: italic; padding: 2px 0;">No spells prepared for today.</div>'}
      </div>
      ${unpreparedDrawerHtml}
    </div>
  `;
}

function renderSingleSpellRow(charId, spell, slots, isUnprepared = false) {
  if (typeof spell === "string") {
    spell = { name: spell, level: 0, is_cantrip: false, prepared: true };
  }
  const isCantrip = Boolean(spell.is_cantrip || (typeof spell.level === "string" && spell.level.toLowerCase().includes("cantrip")));
  let lvl = 0;
  if (!isCantrip) {
    if (typeof spell.level === "number") lvl = spell.level;
    else if (typeof spell.level === "string") {
      const match = spell.level.match(/\d+/);
      lvl = match ? parseInt(match[0], 10) : 1;
    }
  }
  const slotData = slots[`level_${lvl}`];
  
  // Disable cast button if a leveled spell has 0 slots remaining
  const isOutOfSlots = !isCantrip && (!slotData || slotData.current <= 0);

  const levelTag = isCantrip ? 'Cantrip' : `${lvl}${getOrdinal(lvl)} Level`;
  const ritualTag = spell.is_ritual ? ' [R]' : '';
  const concTag = spell.duration && spell.duration.toLowerCase().includes('concentration') ? ' &lt;C&gt;' : '';

  const charIdSafe = escapeRpJs(String(charId));
  const spellName = spell.name || "Spell";
  const spellNameSafe = escapeRpJs(String(spellName));
  const spellNameHtml = escapeRpHtml(String(spellName));
  const rangeText = spell.range || "Touch";
  const castTime = spell.cast_time || spell.casting_time || "1A";

  const hasAtk = Boolean(spell.save_atk || spell.atk_bonus);
  const dmgMatch = spell.damage || (spellName.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i) ? spellName.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i)[0] : "");

  return `
    <div class="spell-row-item ${isUnprepared ? 'unprepared-row' : ''}">
      <div class="spell-main-info">
        <strong class="spell-title">${spellNameHtml}${ritualTag}${concTag}</strong>
        <span class="spell-badge-type">${levelTag}</span>
        <span class="spell-subtext">${escapeRpHtml(rangeText)} | ${escapeRpHtml(castTime)}</span>
      </div>
      <div class="spell-action-group">
        <button type="button" 
                class="action-btn cast-btn ${isOutOfSlots ? 'out-of-slots' : ''}" 
                onclick="handleCardCastSpell('${charIdSafe}', '${spellNameSafe}', ${lvl})"
                title="${isOutOfSlots ? 'No spell slots remaining!' : 'Cast Spell'}">
          Cast
        </button>
        ${hasAtk ? `<button type="button" class="action-btn atk-btn" onclick="handleSpellAtk('${charIdSafe}', '${spellNameSafe}')">Atk</button>` : ''}
        ${dmgMatch ? `<button type="button" class="action-btn dmg-btn" onclick="handleSpellDmg('${charIdSafe}', '${spellNameSafe}')">Dmg</button>` : ''}
      </div>
    </div>
  `;
}

function getOrdinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}

function toggleUnpreparedDrawer(charId) {
  const container = document.getElementById(`unprepared-container-${charId}`);
  const arrow = document.getElementById(`drawer-arrow-${charId}`);
  if (container) {
    const isOpen = container.classList.toggle('open');
    if (arrow) arrow.innerText = isOpen ? '▼' : '▶';
  }
}

async function handleCardCastSpell(charId, spellName, spellLevel) {
  if (spellLevel > 0) {
    // Leveled spell: Call cast_spell to deduct slot and prompt GM narrative
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.invoke_ttrpg_tool === "function") {
      const res = await window.pywebview.api.invoke_ttrpg_tool('cast_spell', {
        character_name: charId,
        spell_name: spellName,
        spell_level: spellLevel
      });

      if (res && res.status === 'error') {
        alert(res.message);
        return;
      }
    }
    const inputEl = document.getElementById("input-rp-dm-prompt");
    if (inputEl) {
      inputEl.value = `*[SPELL CAST]* ${charId} casts **${spellName}** (Level ${spellLevel}).`;
      const sendBtn = document.getElementById("btn-send-rp-dm-prompt");
      if (sendBtn) sendBtn.click();
    }
  } else {
    // Cantrip: At-will cast
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.send_chat_message === "function") {
      await window.pywebview.api.send_chat_message(`Cast cantrip: ${spellName}`);
    }
    const inputEl = document.getElementById("input-rp-dm-prompt");
    if (inputEl) {
      inputEl.value = `*[SPELL CAST]* ${charId} casts cantrip **${spellName}**.`;
      const sendBtn = document.getElementById("btn-send-rp-dm-prompt");
      if (sendBtn) sendBtn.click();
    }
  }
}

function handleSpellAtk(charId, spellName) {
  const state = ttrpgData.state || {};
  const charsObj = state.characters || {};
  const char = charsObj[charId] || Object.values(charsObj).find(c => c.name === charId);
  const spells = char?.native_system_data?.spells || char?.spells || [];
  const sp = spells.find(s => (s.name || s) === spellName);
  const atk = sp?.atk_bonus || sp?.save_atk || "+5";
  if (typeof window.handleRollSpellAttack === "function") {
    window.handleRollSpellAttack(char?.name || charId, spellName, atk);
  }
}

function handleSpellDmg(charId, spellName) {
  const state = ttrpgData.state || {};
  const charsObj = state.characters || {};
  const char = charsObj[charId] || Object.values(charsObj).find(c => c.name === charId);
  const spells = char?.native_system_data?.spells || char?.spells || [];
  const sp = spells.find(s => (s.name || s) === spellName);
  const dmg = sp?.damage || "1d10";
  if (typeof window.handleRollSpellDamage === "function") {
    window.handleRollSpellDamage(char?.name || charId, spellName, dmg);
  }
}

window.renderCardSpellsSection = renderCardSpellsSection;
window.renderSingleSpellRow = renderSingleSpellRow;
window.getOrdinal = getOrdinal;
window.toggleUnpreparedDrawer = toggleUnpreparedDrawer;
window.handleCardCastSpell = handleCardCastSpell;
window.handleSpellAtk = handleSpellAtk;
window.handleSpellDmg = handleSpellDmg;
window.openAddSpellModal = function(cName) {
  if (typeof window.handleOpenAddSpellModal === "function") {
    window.handleOpenAddSpellModal(cName);
  }
};

function renderPartyRoster() {
    const gridEl = document.getElementById("party-roster-grid");
    if (!gridEl) return;

    // Preserve horizontal scroll and internal card scroll positions across re-renders
    const prevGridScrollLeft = gridEl.scrollLeft;
    const prevCardScrollTops = {};
    gridEl.querySelectorAll('.character-card').forEach(c => {
        const cid = c.getAttribute('data-char-id');
        const scrollEl = c.querySelector('.card-stats-scroll');
        if (cid && scrollEl) {
            prevCardScrollTops[cid] = scrollEl.scrollTop;
        }
    });

    const state = ttrpgData.state || {};
    const charsObj = state.characters || {};
    const charIds = Object.keys(charsObj);
    const storedItems = Array.isArray(state.stored_items) ? state.stored_items : [];

    // Render Confiscated / Stored Party Items banner if any gear was disarmed or stored in the world
    let bannerEl = document.getElementById("rp-roster-stored-items-banner");
    if (!bannerEl) {
        bannerEl = document.createElement("div");
        bannerEl.id = "rp-roster-stored-items-banner";
        const rosterHeader = document.querySelector("#rp-roster-pane .roster-header");
        if (rosterHeader && rosterHeader.parentNode) {
            rosterHeader.parentNode.insertBefore(bannerEl, gridEl);
        }
    }
    if (bannerEl) {
        if (storedItems.length > 0) {
            bannerEl.style.display = "block";
            bannerEl.innerHTML = `
                <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 0.78rem;">
                    <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                        <span style="font-weight: 700; color: #f87171;">⚠️ Confiscated Gear &amp; World Items (${storedItems.length}):</span>
                        <span style="color: #cbd5e1; display: inline-flex; flex-wrap: wrap; gap: 4px;">
                            ${storedItems.map(si => {
                                const isDestroyed = (si.status === 'destroyed' || si.salvageable === false);
                                const isInside = (si.status === 'inside_creature');
                                const isUnknown = (!isDestroyed && !isInside && (si.location_knowledge === 'unknown' || !si.location_knowledge));

                                let badgeColor = 'rgba(239, 68, 68, 0.3)';
                                let badgeIcon = '🔒';
                                let locLabel = 'Location Unknown';
                                let extraStyle = '';

                                if (isDestroyed) {
                                    badgeIcon = '🔥';
                                    badgeColor = 'rgba(100, 116, 139, 0.5)';
                                    locLabel = `Destroyed${si.storage_location ? ' in ' + si.storage_location : ''} (Lost forever)`;
                                    extraStyle = 'text-decoration: line-through; opacity: 0.85;';
                                } else if (isInside) {
                                    badgeIcon = '🐉';
                                    badgeColor = 'rgba(245, 158, 11, 0.4)';
                                    locLabel = `Inside ${si.holder_or_creature || 'Creature'} (Salvageable)`;
                                } else if (!isUnknown) {
                                    badgeIcon = '📍';
                                    badgeColor = 'rgba(56, 189, 248, 0.4)';
                                    locLabel = `Held at: ${si.storage_location || 'Evidence Cache'}${si.discovery_clue ? ' • ' + si.discovery_clue : ''}`;
                                }

                                return `<span style="background: rgba(15, 23, 42, 0.65); padding: 3px 8px; border-radius: 4px; border: 1px solid ${badgeColor}; display: inline-block;">
                                    <span>${badgeIcon} <strong>${escapeRpHtml(si.original_owner || 'Party')}</strong>: <span style="${extraStyle}">${escapeRpHtml(si.name)}</span></span>
                                    <em style="color: ${isDestroyed ? '#f87171' : (isInside ? '#fbbf24' : (isUnknown ? '#94a3b8' : '#38bdf8'))}; font-size: 0.7rem; margin-left: 4px;">[${escapeRpHtml(locLabel)}]</em>
                                </span>`;
                            }).join("")}
                        </span>
                    </div>
                </div>
            `;
        } else {
            bannerEl.style.display = "none";
            bannerEl.innerHTML = "";
        }
    }

    gridEl.innerHTML = "";
    if (charIds.length === 0) {
        gridEl.innerHTML = `
            <div style="flex: 1; text-align: center; padding: 48px 20px; color: #64748b;">
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
            const cols = displayKeys.length <= 6 ? displayKeys.length : Math.ceil(displayKeys.length / 2);
            attrsHtml = `<div class="attributes-strip" style="grid-template-columns: repeat(${cols}, 1fr);">` + displayKeys.map(k => {
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

        // Build conditions HTML with icons and tooltips
        const conditions = Array.isArray(ch.conditions) ? ch.conditions : [];
        const condBadges = conditions.map(cond => {
            const meta = getConditionMeta(cond);
            return `
                <span class="condition-badge" title="${escapeRpHtml(meta.desc)} (Click to clear)" onclick="handleToggleCondition('${escapeRpHtml(ch.name)}', '${escapeRpHtml(cond)}', false)">
                    <span>${meta.icon}</span> <span>${escapeRpHtml(cond)}</span> <span style="font-weight: 700; margin-left: 2px; opacity: 0.7;">✕</span>
                </span>
            `;
        }).join("");

        // Build Weapons Shelf HTML
        const wpns = Array.isArray(ch.weapons) ? ch.weapons : [];
        const charStored = storedItems.filter(si => (si.original_owner || "").toLowerCase() === (ch.name || "").toLowerCase() || si.original_owner_id === cid);
        let storedConfiscatedHtml = "";
        if (charStored.length > 0) {
            const sections = [];
            const destroyed = charStored.filter(si => si.status === 'destroyed' || si.salvageable === false);
            const inside = charStored.filter(si => !(si.status === 'destroyed' || si.salvageable === false) && si.status === 'inside_creature');
            const unknown = charStored.filter(si => !(si.status === 'destroyed' || si.salvageable === false) && si.status !== 'inside_creature' && (si.location_knowledge === 'unknown' || !si.location_knowledge));
            const known = charStored.filter(si => !(si.status === 'destroyed' || si.salvageable === false) && si.status !== 'inside_creature' && (si.location_knowledge === 'known' || si.location_knowledge === 'discovered'));

            if (unknown.length > 0) {
                sections.push(`
                    <div style="margin-top: 4px; padding: 4px 7px; background: rgba(239, 68, 68, 0.08); border: 1px dashed rgba(239, 68, 68, 0.35); border-radius: 4px; font-size: 9.5px; color: #fca5a5;">
                        <div style="font-weight: 600;">🔒 Confiscated Gear (Location Unknown):</div>
                        <div style="color: #cbd5e1; margin-top: 2px;">${unknown.map(s => escapeRpHtml(s.name)).join(", ")}</div>
                        <div style="font-size: 8.5px; color: #94a3b8; margin-top: 2px; font-style: italic;">Taken upon arrest • Location not yet discovered</div>
                    </div>
                `);
            }

            if (known.length > 0) {
                const locSummary = known[0].storage_location || "Guard Barracks";
                const clueSummary = known[0].discovery_clue ? ` • ${known[0].discovery_clue}` : "";
                sections.push(`
                    <div style="margin-top: 4px; padding: 4px 7px; background: rgba(56, 189, 248, 0.08); border: 1px dashed rgba(56, 189, 248, 0.35); border-radius: 4px; font-size: 9.5px; color: #38bdf8;">
                        <div style="font-weight: 600;">📍 Confiscated Gear (Location Known):</div>
                        <div style="color: #cbd5e1; margin-top: 2px;">${known.map(s => escapeRpHtml(s.name)).join(", ")}</div>
                        <div style="font-size: 8.5px; color: #94a3b8; margin-top: 2px;">Held at: <strong style="color: #e2e8f0;">${escapeRpHtml(locSummary)}</strong>${escapeRpHtml(clueSummary)}</div>
                    </div>
                `);
            }

            if (inside.length > 0) {
                const beastName = inside[0].holder_or_creature || "Creature";
                sections.push(`
                    <div style="margin-top: 4px; padding: 4px 7px; background: rgba(245, 158, 11, 0.08); border: 1px dashed rgba(245, 158, 11, 0.35); border-radius: 4px; font-size: 9.5px; color: #fbbf24;">
                        <div style="font-weight: 600;">🐉 Swallowed Gear (Inside ${escapeRpHtml(beastName)}):</div>
                        <div style="color: #cbd5e1; margin-top: 2px;">${inside.map(s => escapeRpHtml(s.name)).join(", ")}</div>
                        <div style="font-size: 8.5px; color: #d97706; margin-top: 2px; font-style: italic;">Can be salvaged upon slaying the creature</div>
                    </div>
                `);
            }

            if (destroyed.length > 0) {
                const destLoc = destroyed[0].storage_location || "Lava / Acid";
                sections.push(`
                    <div style="margin-top: 4px; padding: 4px 7px; background: rgba(71, 85, 105, 0.15); border: 1px dashed rgba(100, 116, 139, 0.4); border-radius: 4px; font-size: 9.5px; color: #94a3b8;">
                        <div style="font-weight: 600; color: #f87171;">🔥 Destroyed Gear (Lost Forever):</div>
                        <div style="color: #cbd5e1; margin-top: 2px; text-decoration: line-through;">${destroyed.map(s => escapeRpHtml(s.name)).join(", ")}</div>
                        <div style="font-size: 8.5px; color: #ef4444; margin-top: 2px; font-style: italic;">Destroyed in ${escapeRpHtml(destLoc)} • Cannot be recovered</div>
                    </div>
                `);
            }

            storedConfiscatedHtml = sections.join("");
        }
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
                ${storedConfiscatedHtml}
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
                        ${inv.map(item => {
                            const itemName = typeof item === "string" ? item : (item.name || "Item");
                            const itemQty = typeof item === "object" ? (item.qty || "1") : "1";
                            const itemWt = typeof item === "object" ? (item.weight || "") : "";
                            const itemNotes = typeof item === "object" ? (item.notes || "") : "";
                            const label = `${itemName}${itemQty && itemQty !== "1" ? ' x' + itemQty : ''}${itemWt ? ' [' + itemWt + ']' : ''}${itemNotes ? ' (' + itemNotes + ')' : ''}`;
                            return `
                            <span class="rp-gear-badge" style="background: rgba(30, 41, 59, 0.85); border: 1px solid rgba(100, 116, 139, 0.35); border-radius: 4px; padding: 2px 7px; font-size: 10px; color: #cbd5e1; display: inline-flex; align-items: center; gap: 4px;">
                                ${escapeRpHtml(label)}
                                <span style="cursor: pointer; color: #94a3b8; font-size: 9px; margin-left: 2px;" title="Remove Item" onclick="handleDeleteInventoryItem('${escapeRpJs(ch.name)}', '${escapeRpJs(itemName)}')">✕</span>
                            </span>
                            `;
                        }).join("")}
                    </div>
                ` : `<div style="font-size: 10.5px; color: #64748b; font-style: italic;">Inventory empty.</div>`}
            </div>
        `;

        // Build Spells & Cantrips Shelf HTML (Dynamic Roster Spell Filter & Live Slot Trackers)
        if (!ch.id) ch.id = cid;
        const spellsHtml = renderCardSpellsSection(ch);

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

        let xpBadgeHtml = "";
        if (!isCoc) {
            const nextLvlXp = getDndNextLevelXp(charLevel);
            if (charLevel >= 20) {
                xpBadgeHtml = `<span class="rp-badge" style="background: rgba(250, 204, 21, 0.12); border: 1px solid rgba(250, 204, 21, 0.35); color: #facc15; font-size: 10px; padding: 1px 6px; white-space: nowrap;" title="Maximum Level (Level 20)">XP: ${Number(charXp).toLocaleString()} (Max)</span>`;
            } else if (charXp >= nextLvlXp) {
                xpBadgeHtml = `<button type="button" class="rp-badge diff-easy" style="cursor: pointer; font-size: 10px; font-weight: bold; background: rgba(34, 197, 94, 0.22); border: 1px solid #22c55e; color: #4ade80; padding: 1px 7px; white-space: nowrap;" onclick="openLevelUpWizard('${escapeRpHtml(ch.name)}', 'campaign', { reason: 'Reached ${Number(charXp).toLocaleString()} XP milestone' })" title="Level Up Ready! Click to open Guided Level-Up Wizard">⭐ LEVEL UP READY (${Number(charXp).toLocaleString()} / ${Number(nextLvlXp).toLocaleString()})</button>`;
            } else {
                xpBadgeHtml = `<span class="rp-badge" style="background: rgba(148, 163, 184, 0.12); border: 1px solid rgba(148, 163, 184, 0.3); color: #cbd5e1; font-size: 10px; padding: 1px 6px; white-space: nowrap;" title="XP: Current / Next Level">XP: ${Number(charXp).toLocaleString()} / ${Number(nextLvlXp).toLocaleString()}</span>`;
            }
        }

        const locBadge = ch.location ? `
            <span class="char-location-badge ${ch.is_separated ? 'is-separated' : ''}" style="background: ${ch.is_separated ? 'rgba(239, 68, 68, 0.15)' : 'rgba(56, 189, 248, 0.1)'}; border: 1px solid ${ch.is_separated ? 'rgba(239, 68, 68, 0.4)' : 'rgba(56, 189, 248, 0.3)'}; color: ${ch.is_separated ? '#fca5a5' : '#7dd3fc'}; font-size: 10px; padding: 1px 6px; border-radius: 4px; display: inline-flex; align-items: center; gap: 3px;" title="${ch.is_separated ? 'Separated from main party!' : 'Current Location'}">
                📍 ${escapeRpHtml(ch.location)}${ch.is_separated ? ' <strong style="color: #ef4444;">⚠️ Separated</strong>' : ''}
            </span>
        ` : "";

        let deathSavesHtml = "";
        if (Number(hp.current) <= 0) {
            const ds = ch.death_saves || { successes: 0, failures: 0, status: "dying" };
            const succCount = Math.max(0, Math.min(3, Number(ds.successes || 0)));
            const failCount = Math.max(0, Math.min(3, Number(ds.failures || 0)));
            const dsStatus = String(ds.status || "dying").toLowerCase();

            let statusPill = "";
            if (dsStatus === "dead" || failCount >= 3) {
                statusPill = `<span style="background: rgba(100, 116, 139, 0.4); color: #cbd5e1; border: 1px solid #64748b; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 10px;">💀 DEAD</span>`;
            } else if (dsStatus === "stable" || succCount >= 3) {
                statusPill = `<span style="background: rgba(14, 165, 233, 0.25); color: #38bdf8; border: 1px solid #0284c7; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 10px;">🩹 STABLE (0 HP)</span>`;
            } else {
                statusPill = `<span style="background: rgba(239, 68, 68, 0.25); color: #f87171; border: 1px solid #ef4444; padding: 1px 6px; border-radius: 4px; font-weight: 700; font-size: 10px;">💀 DYING (0 HP)</span>`;
            }

            const succPips = [1, 2, 3].map(i => `<span style="color: ${i <= succCount ? '#22c55e' : '#475569'}; font-size: 12px;">${i <= succCount ? '●' : '○'}</span>`).join(" ");
            const failPips = [1, 2, 3].map(i => `<span style="color: ${i <= failCount ? '#ef4444' : '#475569'}; font-size: 12px;">${i <= failCount ? '●' : '○'}</span>`).join(" ");

            deathSavesHtml = `
                <div class="death-saves-panel" style="margin-top: 6px; padding: 5px 8px; background: rgba(15, 23, 42, 0.75); border: 1px solid ${dsStatus === 'dead' ? '#64748b' : (dsStatus === 'stable' ? '#0284c7' : '#ef4444')}; border-radius: 5px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 3px;">
                        <span style="font-size: 10.5px; font-weight: 700; color: #cbd5e1;">💀 Death Saves (DC 10):</span>
                        ${statusPill}
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 10px;">
                        <div>
                            <span style="color: #4ade80; font-weight: 600;">Succ:</span> ${succPips}
                            <span style="color: #64748b; margin: 0 4px;">|</span>
                            <span style="color: #f87171; font-weight: 600;">Fail:</span> ${failPips}
                        </div>
                        ${(dsStatus === 'dying' && failCount < 3 && succCount < 3) ? `
                            <button type="button" class="cyber-btn-small" style="padding: 1px 6px; font-size: 9.5px; background: rgba(239, 68, 68, 0.2); border: 1px solid #ef4444; color: #fca5a5;" onclick="handleRollDeathSave('${escapeRpHtml(ch.name)}')">🎲 Roll Save</button>
                        ` : ''}
                    </div>
                </div>
            `;
        }

        card.innerHTML = `
            <div class="card-header char-card-top">
                <div class="char-card-header-main">
                    <div class="char-card-identity-row">
                        <div class="card-identity" style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                            <span class="mic-pulse-indicator" title="Active Single-Mic Speaker">🎙️</span>
                            <h4 class="char-name char-name-title" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeRpHtml(ch.name)}</h4>
                            <span class="caller-crown ${isCaller ? 'active' : ''}" title="Designated Party Caller">👑 CALLER</span>
                            <span class="rp-badge" style="background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.35); color: #38bdf8; font-size: 10px; padding: 1px 6px; white-space: nowrap;">${rulesetBadge}</span>
                            ${xpBadgeHtml}
                        </div>
                        <div class="card-controls" style="display: flex; align-items: center; gap: 4px; flex-shrink: 0;">
                            <button type="button" class="focus-speaker-btn cyber-btn-tiny" title="Set active single-mic focus">Focus</button>
                            <button type="button" class="set-caller-btn cyber-btn-tiny" title="Set as Party Caller">Crown</button>
                            <button type="button" class="vital-step-btn" title="Save this hero permanently to the Character Vault" onclick="handleExportRosterCharToVault('${escapeRpHtml(ch.name)}')">🏛️</button>
                            <button type="button" class="vital-step-btn" title="Award XP or Level Up" onclick="handlePromptProgressCharacter('${escapeRpHtml(ch.name)}')">⭐</button>
                            <button type="button" class="vital-step-btn" title="View character sheet" onclick="handleOpenViewCharacterModal('${escapeRpHtml(ch.id || cid)}')">👁️</button>
                            <button type="button" class="vital-step-btn" title="Edit character details" onclick="handleOpenEditCharacterModal('${escapeRpHtml(ch.id || cid)}')">✏️</button>
                            <button type="button" class="vital-step-btn" title="Remove character" onclick="handleDeleteCharacter('${escapeRpHtml(ch.id || cid)}')">🗑️</button>
                        </div>
                    </div>
                    <div class="char-sub-archetype">${escapeRpHtml(ch.archetype || "Adventurer")} ${ch.player_name ? `• ${escapeRpHtml(ch.player_name)}` : ""}</div>
                    <div class="char-badges-strip">
                        <span class="char-guard-badge" title="${escapeRpHtml(guard.label || "Armor Defense")}">🛡️ ${acText}</span>
                        ${locBadge}
                        ${initBadge}
                        ${speedBadge}
                        ${coinBadge}
                        ${sensesBadge}
                        ${defensesBadge}
                    </div>
                </div>
            </div>

            <div class="card-stats-scroll">
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
                    ${deathSavesHtml}
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
            </div>
        `;
        gridEl.appendChild(card);
    });

    // Restore scroll positions
    gridEl.scrollLeft = prevGridScrollLeft;
    Object.keys(prevCardScrollTops).forEach(cid => {
        const cardEl = document.getElementById(`card-${cid}`);
        if (cardEl) {
            const scrollEl = cardEl.querySelector('.card-stats-scroll');
            if (scrollEl) {
                scrollEl.scrollTop = prevCardScrollTops[cid];
            }
        }
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

    // 2. Meta Pills (Location, Quest) & Dedicated Stacked Clues Section
    let metaHtml = "";
    if (loc) {
        metaHtml += `<div class="rp-meta-pill"><span>📍</span> <strong>Location:</strong> <span>${escapeRpHtml(loc)}</span></div>`;
    }
    if (quest) {
        metaHtml += `<div class="rp-meta-pill"><span>🎯</span> <strong>Active Quest:</strong> <span>${escapeRpHtml(quest)}</span></div>`;
    }

    // Clean and deduplicate discovered clues
    const rawClues = Array.isArray(clues) ? clues : [];
    const seenClues = new Set();
    const cleanClues = [];
    for (const c of rawClues) {
        const text = (typeof c === 'string' ? c : (c.name || JSON.stringify(c))).trim();
        const norm = text.toLowerCase();
        if (text && !seenClues.has(norm)) {
            seenClues.add(norm);
            cleanClues.push(text);
        }
    }

    if (cleanClues.length > 0) {
        metaHtml += `
            <div class="rp-journal-clues-box">
                <div class="rp-clues-header">
                    <span>🔍</span> <strong>Discovered Clues &amp; Evidence (${cleanClues.length}):</strong>
                </div>
                <div class="rp-clues-stack">
                    ${cleanClues.map(c => `<span class="rp-clue-badge">${escapeRpHtml(c)}</span>`).join("")}
                </div>
            </div>
        `;
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

    const actorName = rollData.character_name || (rollData.purpose && rollData.purpose.match(/^([A-Za-z0-9_'\s]+?)\s+(?:makes|rolls|checks|saves|attacks|athletics|perception|investigation|stealth)/i)?.[1]) || "";
    const isDm = actorName && (actorName.toLowerCase().includes("dm") || actorName.toLowerCase().includes("game master") || actorName.toLowerCase().includes("monster"));
    const actorBadge = actorName 
        ? `<span class="rp-badge" style="background: ${isDm ? 'rgba(239, 68, 68, 0.2)' : 'rgba(56, 189, 248, 0.2)'}; border: 1px solid ${isDm ? 'rgba(239, 68, 68, 0.4)' : 'rgba(56, 189, 248, 0.4)'}; color: ${isDm ? '#fca5a5' : '#38bdf8'}; font-weight: 700; font-size: 10px; padding: 1px 6px; border-radius: 4px; margin-right: 6px;">${escapeRpHtml(actorName.toUpperCase())}</span>`
        : "";

    entry.innerHTML = `
        <div class="dice-audit-header">
            <span style="display: flex; align-items: center; gap: 4px;">
                ${actorBadge}
                🎲 ${escapeRpHtml(rollData.formula || "Roll")} (${escapeRpHtml(rollData.roll_type || "normal")})
            </span>
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
            const res = await window.pywebview.api.advance_ttrpg_turn();
            await loadRolePlayUI();
            if (res && res.level_up_ready_characters && res.level_up_ready_characters.length > 0) {
                for (const ch of res.level_up_ready_characters) {
                    await checkAndPromptLevelUp(ch.name, ch.experience_points, ch.level);
                }
            }
        }
    } catch (e) {
        console.error("[RolePlay] Advance turn error:", e);
    }
}

async function handleEndCombat() {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.end_ttrpg_combat) {
            const res = await window.pywebview.api.end_ttrpg_combat();
            await loadRolePlayUI();
            if (res && res.level_up_ready_characters && res.level_up_ready_characters.length > 0) {
                for (const ch of res.level_up_ready_characters) {
                    await checkAndPromptLevelUp(ch.name, ch.experience_points, ch.level);
                }
            }
        }
    } catch (e) {
        console.error("[RolePlay] End combat error:", e);
    }
}

async function handleSelectDifficulty(diff) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.set_ttrpg_difficulty) {
            await window.pywebview.api.set_ttrpg_difficulty(diff);
            await loadRolePlayUI();
        } else if (window.pywebview && window.pywebview.api && window.pywebview.api.update_ttrpg_campaign) {
            await window.pywebview.api.update_ttrpg_campaign(null, null, null, null, null, null, diff);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Select difficulty error:", e);
    }
}
window.handleEndCombat = handleEndCombat;
window.handleSelectDifficulty = handleSelectDifficulty;

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

async function handleRollDeathSave(characterName) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.roll_ttrpg_dice) {
            await window.pywebview.api.roll_ttrpg_dice("1d20", "normal", `${characterName} Death Saving Throw (DC 10)`, characterName);
            await loadRolePlayUI();
        }
    } catch (e) {
        console.error("[RolePlay] Death save roll error:", e);
    }
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

let editingStagedWeaponIndex = null;

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
                ${isCurrentModalViewOnly ? "" : `
                <div style="display: flex; align-items: center; gap: 4px;">
                    <button type="button" class="vital-step-btn" style="color: #38bdf8; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(56, 189, 248, 0.3);" title="Edit weapon" onclick="handleEditStagedWeapon(${idx})">✏️</button>
                    <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove weapon" onclick="handleRemoveStagedWeapon(${idx})">✕</button>
                </div>
                `}
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

window.handleEditStagedWeapon = function(idx) {
    if (idx < 0 || idx >= stagedCharacterWeapons.length) return;
    editingStagedWeaponIndex = idx;
    const w = stagedCharacterWeapons[idx];
    const modal = document.getElementById("modal-add-weapon");
    const titleEl = document.getElementById("modal-add-weapon-title");
    const confirmBtn = document.getElementById("btn-confirm-add-weapon");
    const charInput = document.getElementById("input-add-weapon-char-name");

    if (charInput) charInput.value = "__active_edit__";
    if (titleEl) titleEl.textContent = `Edit Weapon: ${w.name || 'Weapon'}`;
    if (confirmBtn) confirmBtn.textContent = "Update Weapon";

    const nameInput = document.getElementById("input-weapon-name");
    const typeSelect = document.getElementById("select-weapon-type");
    const atkInput = document.getElementById("input-weapon-atk");
    const dmgInput = document.getElementById("input-weapon-dmg");
    const notesInput = document.getElementById("input-weapon-notes");

    if (nameInput) nameInput.value = w.name || "";
    if (typeSelect && w.type) typeSelect.value = w.type;
    if (atkInput) atkInput.value = w.atk_bonus || "+0";
    if (dmgInput) dmgInput.value = w.damage || "1d4";
    if (notesInput) notesInput.value = w.notes || "";

    updateWeaponLivePreview();
    if (modal) modal.classList.remove("hidden");
};

let stagedCharacterSpells = [];
let editingStagedSpellIndex = null;
let stagedCustomMeters = {};

function renderStagedCustomMeters(isViewOnly = isCurrentModalViewOnly) {
    const container = document.getElementById("custom-meters-container");
    if (!container) return;
    container.innerHTML = "";
    const entries = Object.entries(stagedCustomMeters || {});
    if (entries.length === 0) {
        container.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px; grid-column: 1 / -1;">No rest meters active. Click <strong>+ Add Tracker</strong> to track class abilities or magic item uses.</div>`;
        return;
    }

    entries.forEach(([key, m]) => {
        const label = m.label || key;
        const cur = m.current ?? 0;
        const max = m.max ?? 1;
        const resetOn = m.reset_on || "long_rest";
        let resetTag = "Long Rest";
        if (resetOn === "short_rest") resetTag = "Short Rest";
        else if (resetOn !== "long_rest") resetTag = "Special / Other";

        const card = document.createElement("div");
        card.className = "meter-card";
        card.setAttribute("data-meter-key", key);
        card.innerHTML = `
            <div class="meter-header">
                <span class="meter-title">${escapeRpHtml(label)}</span>
                <div style="display: flex; align-items: center; gap: 4px;">
                    <span class="meter-reset-tag">${escapeRpHtml(resetTag)}</span>
                    ${isViewOnly ? "" : `<button type="button" class="del-meter-btn" style="background: none; border: none; color: #f87171; cursor: pointer; font-size: 11px; padding: 0 3px;" title="Remove Tracker" onclick="handleRemoveCustomMeter('${escapeRpJs(key)}')">✕</button>`}
                </div>
            </div>
            <div class="meter-stepper">
                <button type="button" class="meter-btn-step dec" ${isViewOnly ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : ''} onclick="stepCustomMeter('${escapeRpJs(key)}', -1)">-</button>
                <span class="meter-val-display" id="meter-val-${escapeRpHtml(key)}">${cur} / ${max}</span>
                <button type="button" class="meter-btn-step inc" ${isViewOnly ? 'disabled style="opacity: 0.5; cursor: not-allowed;"' : ''} onclick="stepCustomMeter('${escapeRpJs(key)}', 1)">+</button>
            </div>
        `;
        container.appendChild(card);
    });
}

window.stepCustomMeter = function(key, delta) {
    if (stagedCustomMeters && stagedCustomMeters[key]) {
        const m = stagedCustomMeters[key];
        const newCur = Math.max(0, Math.min(m.max, (m.current ?? 0) + delta));
        m.current = newCur;
        const disp = document.getElementById(`meter-val-${key}`);
        if (disp) {
            disp.textContent = `${newCur} / ${m.max}`;
        }
    }
};

window.handleRemoveCustomMeter = function(key) {
    if (stagedCustomMeters && stagedCustomMeters[key]) {
        delete stagedCustomMeters[key];
        renderStagedCustomMeters(isCurrentModalViewOnly);
    }
};

function getActiveSpellsContainer() {
    const rulesetSelect = document.getElementById("select-rp-char-ruleset");
    const r = (rulesetSelect ? rulesetSelect.value : "dnd_5e").toLowerCase();
    if (r.includes("pathfinder")) {
        return document.getElementById("pf-spells-chips-container");
    } else if (r.includes("agnostic") || r.includes("sandbox") || r.includes("custom")) {
        return document.getElementById("agnostic-spells-chips-container");
    }
    return document.getElementById("dnd-spells-chips-container");
}

function updatePreparedSpellsTally() {
    window.stagedCharacterSpells = stagedCharacterSpells;
    if (typeof updatePreparedBadge === "function") {
        updatePreparedBadge();
        return;
    }
    const tallyEl = document.getElementById("prep-spells-tally");
    if (!tallyEl) return;
    const leveled = (stagedCharacterSpells || []).filter(s => {
        const lvl = typeof s === "object" ? String(s.level || "") : "";
        const cant = (s && s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (lvl.toLowerCase().includes("cantrip") || lvl === "0");
        const alwaysPrep = Boolean(s && s.is_always_prepared);
        return !cant && !alwaysPrep;
    });
    const prepCount = leveled.filter(s => s && s.prepared === true).length;
    const maxAllowed = (typeof activeMaxPreparedSpells === "number" && activeMaxPreparedSpells >= 0) ? activeMaxPreparedSpells : 3;
    if (prepCount > maxAllowed) {
        tallyEl.textContent = `${prepCount} / ${maxAllowed} (OVER LIMIT)`;
        tallyEl.style.color = '#f87171';
    } else {
        tallyEl.textContent = `${prepCount} / ${maxAllowed}`;
        tallyEl.style.color = (prepCount === maxAllowed) ? '#2ecc71' : '#00F3FF';
    }
}

window.handleToggleStagedSpellPrepared = function(idx, isPrep) {
    if (idx >= 0 && idx < stagedCharacterSpells.length) {
        const item = stagedCharacterSpells[idx];
        const sLvl = typeof item === "object" ? (item.level || "Cantrip") : "Cantrip";
        const isCantrip = (item && item.is_cantrip !== undefined) ? Boolean(item.is_cantrip) : (String(sLvl).toLowerCase().includes("cantrip") || sLvl === "0" || sLvl === 0);
        const isAlwaysPrep = Boolean(item && item.is_always_prepared);

        if (isCantrip || isAlwaysPrep) {
            if (typeof stagedCharacterSpells[idx] === "string") {
                stagedCharacterSpells[idx] = { name: stagedCharacterSpells[idx], prepared: true, is_cantrip: isCantrip, is_always_prepared: isAlwaysPrep };
            } else {
                stagedCharacterSpells[idx].prepared = true;
            }
            renderStagedSpells();
            return;
        }

        // If unchecking: always allow without limit check!
        if (!isPrep) {
            if (typeof stagedCharacterSpells[idx] === "string") {
                stagedCharacterSpells[idx] = { name: stagedCharacterSpells[idx], prepared: false };
            } else {
                stagedCharacterSpells[idx].prepared = false;
            }
            updatePreparedSpellsTally();
            return;
        }

        // If checking: calculate count of other prepared leveled spells
        const currentChecked = stagedCharacterSpells.filter((s, sIdx) => {
            if (sIdx === idx) return false;
            const lvl = typeof s === "object" ? String(s.level || "") : "";
            const cant = (s && s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (lvl.toLowerCase().includes("cantrip") || lvl === "0");
            const alwaysPrep = Boolean(s && s.is_always_prepared);
            return !cant && !alwaysPrep && s && s.prepared === true;
        }).length;

        const maxAllowed = (typeof activeMaxPreparedSpells === "number" && activeMaxPreparedSpells >= 0) ? activeMaxPreparedSpells : 3;
        if (currentChecked >= maxAllowed) {
            alert(`Preparation limit reached! This character can only prepare ${maxAllowed} leveled spells. Uncheck another spell first.`);
            if (typeof stagedCharacterSpells[idx] === "object") {
                stagedCharacterSpells[idx].prepared = false;
            }
            renderStagedSpells();
            return;
        }

        if (typeof stagedCharacterSpells[idx] === "string") {
            stagedCharacterSpells[idx] = { name: stagedCharacterSpells[idx], prepared: true };
        } else {
            stagedCharacterSpells[idx].prepared = true;
        }
        updatePreparedSpellsTally();
    }
};

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
    window.stagedCharacterSpells = stagedCharacterSpells;

    const activeCont = getActiveSpellsContainer();
    const allContainers = [
        document.getElementById("dnd-spells-chips-container"),
        document.getElementById("pf-spells-chips-container"),
        document.getElementById("agnostic-spells-chips-container")
    ].filter(Boolean);

    // Clear inactive containers to prevent duplicate DOM elements / duplicate checkboxes
    allContainers.forEach(c => {
        if (c !== activeCont) {
            c.innerHTML = "";
        }
    });

    if (!activeCont) return;

    if (!stagedCharacterSpells || stagedCharacterSpells.length === 0) {
        activeCont.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px;">No spells added. Click <strong>+ Add Spell</strong> above to add with level, damage, DC, and range fields.</div>`;
        updatePreparedSpellsTally();
        return;
    }

    activeCont.innerHTML = stagedCharacterSpells.map((s, idx) => {
        const sName = typeof s === "string" ? s : s.name;
        const sLvl = typeof s === "object" ? (s.level || "Cantrip") : "Cantrip";
        const sDmg = typeof s === "object" ? s.damage : "";
        const sAtk = typeof s === "object" ? (s.atk_bonus || s.save_dc || "") : "";
        const sRange = typeof s === "object" ? s.range : "";
        const sNotes = typeof s === "object" ? s.notes : "";
        const isCantrip = (s && s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (String(sLvl).toLowerCase().includes("cantrip") || sLvl === "0" || sLvl === 0);
        const isAlwaysPrep = Boolean(s && s.is_always_prepared);
        const isPrep = (isCantrip || isAlwaysPrep) ? true : (s && s.prepared === true);
        return `
            <div class="spell-item-row" data-is-cantrip="${isCantrip ? 'true' : 'false'}" data-is-always-prepared="${isAlwaysPrep ? 'true' : 'false'}" style="display: flex; justify-content: space-between; align-items: center; background: rgba(30, 41, 59, 0.65); border: 1px solid rgba(168, 85, 247, 0.35); border-radius: 6px; padding: 5px 8px;">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                    <span style="font-size: 0.85rem;">✨</span>
                    <strong style="color: #f1f5f9; font-size: 0.8rem;">${escapeRpHtml(sName)}</strong>
                    <span style="color: #c084fc; font-size: 0.68rem; background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.3); padding: 1px 5px; border-radius: 3px;">${escapeRpHtml(sLvl)}</span>
                    ${sAtk ? `<span style="color: #38bdf8; font-size: 0.74rem; font-weight: 600;">${escapeRpHtml(sAtk)}</span>` : ""}
                    ${sDmg ? `<span style="color: #f87171; font-size: 0.74rem; font-weight: 600;">(${escapeRpHtml(sDmg)})</span>` : ""}
                    ${sRange ? `<span style="color: #94a3b8; font-size: 0.7rem;">📍 ${escapeRpHtml(sRange)}</span>` : ""}
                    ${sNotes ? `<span style="color: #64748b; font-size: 0.7rem; font-style: italic;">[${escapeRpHtml(sNotes)}]</span>` : ""}
                    <label style="display: inline-flex; align-items: center; gap: 3px; font-size: 0.72rem; color: #a78bfa; cursor: pointer; margin-left: 4px;" title="Prepared Spell for Today">
                        <input type="checkbox" class="spell-prep-checkbox" data-idx="${idx}" ${(isCantrip || isAlwaysPrep) ? 'checked disabled' : (isPrep ? 'checked' : '')} ${isCurrentModalViewOnly ? 'disabled' : ''} onchange="handleToggleStagedSpellPrepared(${idx}, this.checked)" />
                        <span style="font-size: 0.68rem; font-weight: 600; color: #c084fc;">${isCantrip ? 'At-Will' : (isAlwaysPrep ? 'Oath/Domain' : 'Prep')}</span>
                    </label>
                </div>
                ${isCurrentModalViewOnly ? "" : `
                <div style="display: flex; align-items: center; gap: 4px;">
                    <button type="button" class="vital-step-btn" style="color: #c084fc; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(168, 85, 247, 0.3);" title="Edit spell" onclick="handleEditStagedSpell(${idx})">✏️</button>
                    <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove spell" onclick="handleRemoveStagedSpell(${idx})">✕</button>
                </div>
                `}
            </div>
        `;
    }).join("");

    updatePreparedSpellsTally();
}

window.handleRemoveStagedSpell = function(idx) {
    if (idx >= 0 && idx < stagedCharacterSpells.length) {
        stagedCharacterSpells.splice(idx, 1);
        renderStagedSpells();
    }
};

window.handleEditStagedSpell = function(idx) {
    if (idx < 0 || idx >= stagedCharacterSpells.length) return;
    editingStagedSpellIndex = idx;
    const s = stagedCharacterSpells[idx];
    const modal = document.getElementById("modal-add-spell");
    const titleEl = document.getElementById("modal-add-spell-title");
    const confirmBtn = document.getElementById("btn-confirm-add-spell");
    const charInput = document.getElementById("input-add-spell-char-name");

    const sName = typeof s === "string" ? s : (s.name || "");
    const sLvl = typeof s === "object" ? (s.level || "Cantrip") : "Cantrip";
    const sAtk = typeof s === "object" ? (s.atk_bonus || s.save_dc || "") : "";
    const sDmg = typeof s === "object" ? (s.damage || "") : "";
    const sRange = typeof s === "object" ? (s.range || "60 ft.") : "60 ft.";
    const sNotes = typeof s === "object" ? (s.notes || "") : "";

    if (charInput) charInput.value = "__active_edit__";
    if (titleEl) titleEl.textContent = `Edit Spell: ${sName || 'Spell'}`;
    if (confirmBtn) confirmBtn.textContent = "Update Spell";

    const nameInput = document.getElementById("input-spell-name");
    const lvlSelect = document.getElementById("select-spell-level");
    const atkInput = document.getElementById("input-spell-atk");
    const dmgInput = document.getElementById("input-spell-dmg");
    const rangeInput = document.getElementById("input-spell-range");
    const notesInput = document.getElementById("input-spell-notes");

    if (nameInput) nameInput.value = sName;
    if (lvlSelect) lvlSelect.value = sLvl;
    if (atkInput) atkInput.value = sAtk;
    if (dmgInput) dmgInput.value = sDmg;
    if (rangeInput) rangeInput.value = sRange;
    if (notesInput) notesInput.value = sNotes;

    updateSpellLivePreview();
    if (modal) modal.classList.remove("hidden");
};

/* ===================================================================
   CYBERPUNK CYBERWARE ENGINE & STAGING
   =================================================================== */
let stagedCharacterCyberware = [];
let editingStagedCyberwareIndex = null;

function updateHiddenCyberwareInputs() {
    const formatted = stagedCharacterCyberware.map(c => {
        if (typeof c === "string") return c;
        const cat = c.category ? ` (${c.category}` : "";
        const slot = c.slot ? `; ${c.slot}` : "";
        const det = c.details ? `; ${c.details}` : "";
        return `${c.name}${cat}${slot}${det}${cat ? ')' : ''}`;
    }).join("\n");
    const el = document.getElementById("input-cyber-cyberware");
    if (el) el.value = formatted;
}

function renderStagedCyberware() {
    updateHiddenCyberwareInputs();
    const cont = document.getElementById("cyber-cyberware-chips-container");
    if (!cont) return;

    if (!stagedCharacterCyberware || stagedCharacterCyberware.length === 0) {
        cont.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px;">No cyberware implants installed. Click <strong>+ Add Cyberware</strong> above to install neural links, cyberoptics, or armor.</div>`;
        return;
    }

    cont.innerHTML = stagedCharacterCyberware.map((cw, idx) => {
        const name = typeof cw === "string" ? cw : (cw.name || "Cyberware");
        const cat = typeof cw === "object" ? (cw.category || "") : "";
        const slot = typeof cw === "object" ? (cw.slot || "") : "";
        const det = typeof cw === "object" ? (cw.details || "") : "";
        return `
            <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(30, 41, 59, 0.65); border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 6px; padding: 5px 8px;">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                    <span style="font-size: 0.85rem;">🦾</span>
                    <strong style="color: #f1f5f9; font-size: 0.8rem;">${escapeRpHtml(name)}</strong>
                    ${cat ? `<span style="color: #38bdf8; font-size: 0.68rem; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.3); padding: 1px 5px; border-radius: 3px;">${escapeRpHtml(cat)}</span>` : ""}
                    ${slot ? `<span style="color: #a78bfa; font-size: 0.7rem; font-weight: 500;">[${escapeRpHtml(slot)}]</span>` : ""}
                    ${det ? `<span style="color: #94a3b8; font-size: 0.7rem; font-style: italic;">${escapeRpHtml(det)}</span>` : ""}
                </div>
                ${isCurrentModalViewOnly ? "" : `
                <div style="display: flex; align-items: center; gap: 4px;">
                    <button type="button" class="vital-step-btn" style="color: #38bdf8; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(56, 189, 248, 0.3);" title="Edit cyberware" onclick="handleEditStagedCyberware(${idx})">✏️</button>
                    <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove cyberware" onclick="handleRemoveStagedCyberware(${idx})">✕</button>
                </div>
                `}
            </div>
        `;
    }).join("");
}

window.handleRemoveStagedCyberware = function(idx) {
    if (idx >= 0 && idx < stagedCharacterCyberware.length) {
        stagedCharacterCyberware.splice(idx, 1);
        renderStagedCyberware();
    }
};

function updateCyberwareLivePreview() {
    const previewEl = document.getElementById("preview-cyberware-formatted");
    if (!previewEl) return;

    const name = (document.getElementById("input-cyberware-name")?.value || "").trim() || "Cyberware";
    const cat = document.getElementById("select-cyberware-category")?.value || "Neural Link";
    const slot = (document.getElementById("input-cyberware-slot")?.value || "").trim();
    const details = (document.getElementById("input-cyberware-details")?.value || "").trim();

    let parts = [`<strong>${escapeRpHtml(name)}</strong> (${escapeRpHtml(cat)})`];
    if (slot) parts.push(`<span style="color: #a78bfa;">[${escapeRpHtml(slot)}]</span>`);
    if (details) parts.push(`<span style="color: #94a3b8; font-style: italic;">${escapeRpHtml(details)}</span>`);

    previewEl.innerHTML = parts.join(" • ");
}

window.handleOpenAddCyberwareModal = function(charNameOrMode) {
    const modal = document.getElementById("modal-add-cyberware");
    const titleEl = document.getElementById("modal-add-cyberware-title");
    const confirmBtn = document.getElementById("btn-confirm-add-cyberware");
    const smartRaw = document.getElementById("input-cyberware-smart-raw");
    const nameInput = document.getElementById("input-cyberware-name");
    const catSelect = document.getElementById("select-cyberware-category");
    const slotInput = document.getElementById("input-cyberware-slot");
    const detInput = document.getElementById("input-cyberware-details");

    editingStagedCyberwareIndex = null;
    if (titleEl) titleEl.textContent = "Add Cyberware Implant";
    if (confirmBtn) confirmBtn.textContent = "Add Cyberware";
    if (smartRaw) smartRaw.value = "";
    if (nameInput) nameInput.value = "";
    if (catSelect) catSelect.value = "Neural Link";
    if (slotInput) slotInput.value = "";
    if (detInput) detInput.value = "";

    updateCyberwareLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleEditStagedCyberware = function(idx) {
    if (idx < 0 || idx >= stagedCharacterCyberware.length) return;
    editingStagedCyberwareIndex = idx;
    const cw = stagedCharacterCyberware[idx];
    const modal = document.getElementById("modal-add-cyberware");
    const titleEl = document.getElementById("modal-add-cyberware-title");
    const confirmBtn = document.getElementById("btn-confirm-add-cyberware");

    const name = typeof cw === "string" ? cw : (cw.name || "");
    const cat = typeof cw === "object" ? (cw.category || "Neural Link") : "Neural Link";
    const slot = typeof cw === "object" ? (cw.slot || "") : "";
    const det = typeof cw === "object" ? (cw.details || "") : "";

    if (titleEl) titleEl.textContent = `Edit Cyberware: ${name || 'Implant'}`;
    if (confirmBtn) confirmBtn.textContent = "Update Cyberware";

    const nameInput = document.getElementById("input-cyberware-name");
    const catSelect = document.getElementById("select-cyberware-category");
    const slotInput = document.getElementById("input-cyberware-slot");
    const detInput = document.getElementById("input-cyberware-details");

    if (nameInput) nameInput.value = name;
    if (catSelect) catSelect.value = cat;
    if (slotInput) slotInput.value = slot;
    if (detInput) detInput.value = det;

    updateCyberwareLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleConfirmAddCyberware = function() {
    const name = (document.getElementById("input-cyberware-name")?.value || "").trim();
    const cat = document.getElementById("select-cyberware-category")?.value || "Neural Link";
    const slot = (document.getElementById("input-cyberware-slot")?.value || "").trim();
    const details = (document.getElementById("input-cyberware-details")?.value || "").trim();

    if (!name) {
        alert("Please enter a cyberware name.");
        return;
    }

    const cyberObj = {
        name: name,
        category: cat,
        slot: slot,
        details: details
    };

    if (editingStagedCyberwareIndex !== null && editingStagedCyberwareIndex >= 0 && editingStagedCyberwareIndex < stagedCharacterCyberware.length) {
        stagedCharacterCyberware[editingStagedCyberwareIndex] = cyberObj;
    } else {
        stagedCharacterCyberware.push(cyberObj);
    }
    editingStagedCyberwareIndex = null;

    renderStagedCyberware();
    const modal = document.getElementById("modal-add-cyberware");
    if (modal) modal.classList.add("hidden");
};

function extractCharacterCyberware(ch) {
    if (!ch) return [];
    const cw = ch.cyberware || ch.native_system_data?.cyberware;
    if (cw) {
        if (Array.isArray(cw.installed_items) && cw.installed_items.length > 0) {
            return JSON.parse(JSON.stringify(cw.installed_items));
        }
        if (Array.isArray(cw) && cw.length > 0) {
            return JSON.parse(JSON.stringify(cw));
        }
    }
    const traits = ch.universal_core?.tagged_traits || ch.features_and_traits || ch.traits_and_abilities || [];
    const extracted = [];
    const cyberKeywords = ["cyberware", "cyberaudio", "cybereye", "cyberarm", "cyberleg", "neural link", "chassis", "fashionware", "borgware", "subdermal", "implant", "chemskin", "tech hair", "bioware", "sandevistan", "kerenzikov", "wolvers"];
    for (const t of traits) {
        const str = typeof t === "string" ? t : `${t.name || ""}: ${t.description || ""}`;
        const lower = str.toLowerCase();
        if (cyberKeywords.some(kw => lower.includes(kw))) {
            const nameMatch = str.match(/^([^:(]+)/);
            const catMatch = str.match(/\(([^;)]+)/);
            const name = nameMatch ? nameMatch[1].trim() : str;
            const category = catMatch ? catMatch[1].trim() : "Cyberware";
            const details = str.includes(";") ? str.split(";").slice(1).join(";").replace(/\)$/, "").trim() : (str.includes(":") ? str.split(":").slice(1).join(":").trim() : "");
            extracted.push({
                name: name,
                category: category,
                slot: "",
                details: details || ""
            });
        }
    }
    return extracted;
}

/* ===================================================================
   STRUCTURED INVENTORY ENGINE & STAGING
   =================================================================== */
let stagedCharacterInventory = [];
let editingStagedInventoryIndex = null;

function parseInventoryItemString(str) {
    if (typeof str !== "string") {
        if (typeof str === "object" && str !== null) {
            let nm = str.name || "";
            if (nm.includes("(") && !nm.includes(")")) nm += ")";
            return {
                name: nm,
                qty: str.qty || "1",
                weight: str.weight || "",
                category: str.category || "Gear",
                notes: str.notes || ""
            };
        }
        return str;
    }
    let s = str.trim().replace(/^[- *]\s*/, "");
    if (!s) return null;
    if (s.includes("(") && !s.includes(")")) {
        s += ")";
    }

    let qty = "1";
    let weight = "";
    let name = s;
    let notes = "";
    let category = "Gear";

    // Extract bracketed weight e.g. [10 lb.] or [1.5 lbs]
    const wtBracket = name.match(/\[(\d+(?:\.\d+)?\s*(?:lb|lbs|oz|kg))\s*\]/i);
    if (wtBracket) {
        weight = wtBracket[1];
        name = name.replace(wtBracket[0], "").trim();
    }

    const parenMatch = s.match(/^(.*?)\s*\((.*?)\)$/);
    if (parenMatch) {
        const inner = parenMatch[2].trim().toLowerCase();
        const wtMatch = inner.match(/^(\d+(?:\.\d+)?\s*(?:lb|lbs|oz|kg))$/i);
        if (wtMatch && !weight) {
            weight = wtMatch[1];
            name = parenMatch[1].trim();
        } else {
            const isSpec = /^\d+\s*(day|days|ft|feet|lb|lbs|oz|ounce|flask|bottle|torch|rounds|charges|uses)$/i.test(inner) || /^\d+$/.test(inner);
            if (!isSpec) {
                name = parenMatch[1].trim();
                notes = parenMatch[2].trim();
            }
        }
    }

    const qtyMatch = name.match(/^(.*?)\s*x\s*(\d+)$/i) || name.match(/^(.*?)\s*\(x\s*(\d+)\)$/i);
    if (qtyMatch) {
        name = qtyMatch[1].trim();
        qty = qtyMatch[2].trim();
    } else {
        const leadQtyMatch = name.match(/^(\d+)\s+(.+)$/);
        if (leadQtyMatch && parseInt(leadQtyMatch[1], 10) <= 999 && !leadQtyMatch[2].toLowerCase().startsWith("ft")) {
            qty = leadQtyMatch[1];
            name = leadQtyMatch[2].trim();
        }
    }

    const lower = (name + " " + notes).toLowerCase();
    if (lower.includes("potion") || lower.includes("ration") || lower.includes("elixir") || lower.includes("scroll") || lower.includes("antitoxin") || lower.includes("pill") || lower.includes("food") || lower.includes("water")) {
        category = "Consumable";
    } else if (lower.includes("armor") || lower.includes("shield") || lower.includes("plate") || lower.includes("mail") || lower.includes("vest") || lower.includes("jacket") || lower.includes("helmet") || lower.includes("armorjack")) {
        category = "Armor";
    } else if (lower.includes("sword") || lower.includes("dagger") || lower.includes("bow") || lower.includes("axe") || lower.includes("pistol") || lower.includes("rifle") || lower.includes("grenade") || lower.includes("mace") || lower.includes("spear") || lower.includes("blade")) {
        category = "Weapon";
    } else if (lower.includes("ammo") || lower.includes("arrow") || lower.includes("bolt") || lower.includes("bullet") || lower.includes("cartridge") || lower.includes("mag")) {
        category = "Ammunition";
    } else if (lower.includes("tool") || lower.includes("kit") || lower.includes("instrument") || lower.includes("lockpick") || lower.includes("thieves") || lower.includes("scanner") || lower.includes("laptop") || lower.includes("amp") || lower.includes("camera")) {
        category = "Tool";
    } else if (lower.includes("gold") || lower.includes("gem") || lower.includes("coin") || lower.includes("silver") || lower.includes("platinum") || lower.includes("jewelry") || lower.includes("eb") || lower.includes("cash") || lower.includes("eurodollar")) {
        category = "Treasure";
    }

    return {
        name: name,
        qty: qty || "1",
        weight: weight,
        category: category,
        notes: notes
    };
}

function updateHiddenInventoryInputs() {
    const formatted = stagedCharacterInventory.map(item => {
        if (typeof item === "string") return item;
        const qStr = item.qty && item.qty !== "1" ? ` x${item.qty}` : "";
        const wtStr = item.weight ? ` [${item.weight}]` : "";
        const noteStr = item.notes ? ` (${item.notes})` : "";
        return `${item.name}${qStr}${wtStr}${noteStr}`;
    }).join(", ");

    ["input-dnd-inventory", "input-pf-inventory", "input-cyber-inventory", "input-coc-inventory", "input-rp-char-inventory"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = formatted;
    });
}

function renderStagedInventory() {
    updateHiddenInventoryInputs();

    // Auto-calculate Total Weight Carried across all inventory items
    let totalWeightNum = 0;
    (stagedCharacterInventory || []).forEach(item => {
        if (!item) return;
        const wtStr = typeof item === "object" ? (item.weight || "") : "";
        const m = wtStr.match(/(\d+(?:\.\d+)?)/);
        if (m) {
            const wVal = parseFloat(m[1]) || 0;
            const qVal = parseFloat(item.qty) || 1;
            totalWeightNum += (wVal * qVal);
        }
    });

    const carriedInput = document.getElementById("input-dnd-weight-carried");
    if (carriedInput && (!carriedInput.value || carriedInput.dataset.autoCalculated === "true")) {
        carriedInput.value = totalWeightNum > 0 ? `${totalWeightNum.toFixed(1).replace(/\.0$/, '')} lb.` : "0 lb.";
        carriedInput.dataset.autoCalculated = "true";
    }

    const containers = [
        document.getElementById("dnd-inventory-chips-container"),
        document.getElementById("pf-inventory-chips-container"),
        document.getElementById("cyber-inventory-chips-container"),
        document.getElementById("coc-inventory-chips-container"),
        document.getElementById("agnostic-inventory-chips-container")
    ].filter(Boolean);

    containers.forEach(cont => {
        if (!stagedCharacterInventory || stagedCharacterInventory.length === 0) {
            cont.innerHTML = `<div style="font-size: 0.73rem; color: #64748b; font-style: italic; padding: 4px 2px;">No items in inventory. Click <strong>+ Add Item</strong> above to add gear, loot, potions, or supplies.</div>`;
            return;
        }

        cont.innerHTML = stagedCharacterInventory.map((item, idx) => {
            const name = typeof item === "string" ? item : (item.name || "Item");
            const qty = typeof item === "object" ? (item.qty || "1") : "1";
            const wt = typeof item === "object" ? (item.weight || "") : "";
            const cat = typeof item === "object" ? (item.category || "Gear") : "Gear";
            const notes = typeof item === "object" ? (item.notes || "") : "";

            let catColor = "#94a3b8";
            let catBg = "rgba(148, 163, 184, 0.15)";
            if (cat === "Consumable") { catColor = "#34d399"; catBg = "rgba(52, 211, 153, 0.15)"; }
            else if (cat === "Armor") { catColor = "#60a5fa"; catBg = "rgba(96, 165, 250, 0.15)"; }
            else if (cat === "Weapon") { catColor = "#f87171"; catBg = "rgba(248, 113, 113, 0.15)"; }
            else if (cat === "Treasure") { catColor = "#facc15"; catBg = "rgba(250, 204, 21, 0.15)"; }
            else if (cat === "Tool") { catColor = "#c084fc"; catBg = "rgba(192, 132, 252, 0.15)"; }

            return `
                <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(30, 41, 59, 0.65); border: 1px solid rgba(100, 116, 139, 0.35); border-radius: 6px; padding: 5px 8px;">
                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                        <span style="font-size: 0.85rem;">🎒</span>
                        <strong style="color: #f1f5f9; font-size: 0.8rem;">${escapeRpHtml(name)}</strong>
                        ${qty && qty !== "1" ? `<span style="color: #fbbf24; font-size: 0.72rem; font-weight: 700; background: rgba(251, 191, 36, 0.15); padding: 1px 5px; border-radius: 3px;">x${escapeRpHtml(qty)}</span>` : ""}
                        ${wt ? `<span style="color: #cbd5e1; font-size: 0.7rem; font-weight: 600; background: rgba(148, 163, 184, 0.15); border: 1px solid rgba(148, 163, 184, 0.3); padding: 1px 5px; border-radius: 3px;" title="Item Weight">⚖️ ${escapeRpHtml(wt)}</span>` : ""}
                        ${cat ? `<span style="color: ${catColor}; font-size: 0.68rem; background: ${catBg}; border: 1px solid ${catColor}44; padding: 1px 5px; border-radius: 3px;">${escapeRpHtml(cat)}</span>` : ""}
                        ${notes ? `<span style="color: #94a3b8; font-size: 0.7rem; font-style: italic;">[${escapeRpHtml(notes)}]</span>` : ""}
                    </div>
                    ${isCurrentModalViewOnly ? "" : `
                    <div class="staged-item-controls" style="display: flex; align-items: center; gap: 4px;">
                        <button type="button" class="vital-step-btn" style="color: #38bdf8; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(56, 189, 248, 0.3);" title="Edit item" onclick="handleEditStagedInventory(${idx})">✏️</button>
                        <button type="button" class="vital-step-btn" style="color: #f87171; font-size: 0.75rem; padding: 1px 6px; border-color: rgba(248, 113, 113, 0.3);" title="Remove item" onclick="handleRemoveStagedInventory(${idx})">✕</button>
                    </div>
                    `}
                </div>
            `;
        }).join("");
    });
}

window.handleRemoveStagedInventory = function(idx) {
    if (idx >= 0 && idx < stagedCharacterInventory.length) {
        stagedCharacterInventory.splice(idx, 1);
        renderStagedInventory();
    }
};

function updateItemLivePreview() {
    const previewEl = document.getElementById("preview-item-formatted");
    if (!previewEl) return;

    const name = (document.getElementById("input-item-name")?.value || "").trim() || "Item";
    const qty = (document.getElementById("input-item-qty")?.value || "1").trim();
    const wt = (document.getElementById("input-item-weight")?.value || "").trim();
    const cat = document.getElementById("select-item-category")?.value || "Gear";
    const notes = (document.getElementById("input-item-notes")?.value || "").trim();

    let parts = [`<strong>${escapeRpHtml(name)}</strong>`];
    if (qty && qty !== "1") parts.push(`<span style="color: #fbbf24; font-weight: 700;">x${escapeRpHtml(qty)}</span>`);
    if (wt) parts.push(`<span style="color: #cbd5e1; font-weight: 600;">⚖️ ${escapeRpHtml(wt)}</span>`);
    if (cat) parts.push(`<span style="color: #38bdf8;">(${escapeRpHtml(cat)})</span>`);
    if (notes) parts.push(`<span style="color: #94a3b8; font-style: italic;">[${escapeRpHtml(notes)}]</span>`);

    previewEl.innerHTML = parts.join(" • ");
}

window.handleOpenAddItemModal = function(charNameOrMode) {
    const modal = document.getElementById("modal-add-item");
    const titleEl = document.getElementById("modal-add-item-title");
    const confirmBtn = document.getElementById("btn-confirm-add-item");
    const smartRaw = document.getElementById("input-item-smart-raw");
    const nameInput = document.getElementById("input-item-name");
    const qtyInput = document.getElementById("input-item-qty");
    const weightInput = document.getElementById("input-item-weight");
    const catSelect = document.getElementById("select-item-category");
    const notesInput = document.getElementById("input-item-notes");

    editingStagedInventoryIndex = null;
    if (titleEl) titleEl.textContent = "Add Inventory Item";
    if (confirmBtn) confirmBtn.textContent = "Add Item";
    if (smartRaw) smartRaw.value = "";
    if (nameInput) nameInput.value = "";
    if (qtyInput) qtyInput.value = "1";
    if (weightInput) weightInput.value = "";
    if (catSelect) catSelect.value = "Gear";
    if (notesInput) notesInput.value = "";

    updateItemLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleEditStagedInventory = function(idx) {
    if (idx < 0 || idx >= stagedCharacterInventory.length) return;
    editingStagedInventoryIndex = idx;
    const item = stagedCharacterInventory[idx];
    const modal = document.getElementById("modal-add-item");
    const titleEl = document.getElementById("modal-add-item-title");
    const confirmBtn = document.getElementById("btn-confirm-add-item");

    const name = typeof item === "string" ? item : (item.name || "");
    const qty = typeof item === "object" ? (item.qty || "1") : "1";
    const weight = typeof item === "object" ? (item.weight || "") : "";
    const cat = typeof item === "object" ? (item.category || "Gear") : "Gear";
    const notes = typeof item === "object" ? (item.notes || "") : "";

    if (titleEl) titleEl.textContent = `Edit Item: ${name || 'Item'}`;
    if (confirmBtn) confirmBtn.textContent = "Update Item";

    const nameInput = document.getElementById("input-item-name");
    const qtyInput = document.getElementById("input-item-qty");
    const weightInput = document.getElementById("input-item-weight");
    const catSelect = document.getElementById("select-item-category");
    const notesInput = document.getElementById("input-item-notes");

    if (nameInput) nameInput.value = name;
    if (qtyInput) qtyInput.value = qty;
    if (weightInput) weightInput.value = weight;
    if (catSelect) catSelect.value = cat;
    if (notesInput) notesInput.value = notes;

    updateItemLivePreview();

    if (modal) {
        modal.classList.remove("hidden");
        if (nameInput) setTimeout(() => nameInput.focus(), 60);
    }
};

window.handleConfirmAddItem = function() {
    let name = (document.getElementById("input-item-name")?.value || "").trim();
    const qty = (document.getElementById("input-item-qty")?.value || "1").trim();
    const weight = (document.getElementById("input-item-weight")?.value || "").trim();
    const cat = document.getElementById("select-item-category")?.value || "Gear";
    const notes = (document.getElementById("input-item-notes")?.value || "").trim();

    if (!name) {
        alert("Please enter an item name.");
        return;
    }

    if (name.includes("(") && !name.includes(")")) {
        name += ")";
    }

    const itemObj = {
        name: name,
        qty: qty || "1",
        weight: weight,
        category: cat,
        notes: notes
    };

    if (editingStagedInventoryIndex !== null && editingStagedInventoryIndex >= 0 && editingStagedInventoryIndex < stagedCharacterInventory.length) {
        stagedCharacterInventory[editingStagedInventoryIndex] = itemObj;
    } else {
        stagedCharacterInventory.push(itemObj);
    }
    editingStagedInventoryIndex = null;

    renderStagedInventory();
    const modal = document.getElementById("modal-add-item");
    if (modal) modal.classList.add("hidden");
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
    editingStagedSpellIndex = null;
    const confirmBtn = document.getElementById("btn-confirm-add-spell");
    if (confirmBtn) confirmBtn.textContent = "Add Spell";

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
        if (editingStagedSpellIndex !== null && editingStagedSpellIndex >= 0 && editingStagedSpellIndex < stagedCharacterSpells.length) {
            stagedCharacterSpells[editingStagedSpellIndex] = spellObj;
        } else {
            stagedCharacterSpells.push(spellObj);
        }
        editingStagedSpellIndex = null;
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
    editingStagedWeaponIndex = null;
    const confirmBtn = document.getElementById("btn-confirm-add-weapon");
    if (confirmBtn) confirmBtn.textContent = "Add Weapon";

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
        if (editingStagedWeaponIndex !== null && editingStagedWeaponIndex >= 0 && editingStagedWeaponIndex < stagedCharacterWeapons.length) {
            stagedCharacterWeapons[editingStagedWeaponIndex] = weaponObj;
        } else {
            stagedCharacterWeapons.push(weaponObj);
        }
        editingStagedWeaponIndex = null;
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

window.handleReclaimPartyGear = async function(charName) {
    const targetLabel = charName || "the entire party";
    if (!(await showAetherConfirm(`Retrieve and restore confiscated gear for ${targetLabel}?`, "Reclaim Confiscated Gear", "Reclaim", false))) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.manage_ttrpg_character_inventory) {
            const res = await window.pywebview.api.manage_ttrpg_character_inventory(
                charName || "all",
                "retrieve",
                null,
                null,
                null,
                "Reclaimed by player/party"
            );
            if (res && res.status === "success") {
                await loadRolePlayUI(true);
            } else if (res && res.message) {
                await showAetherAlert(res.message, "Reclaim Gear");
            }
        }
    } catch (err) {
        console.error("[RolePlay] Error reclaiming gear:", err);
        await showAetherAlert(`Error reclaiming gear: ${err.message}`, "Error");
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
        for (const itmStr of items) {
            const parsed = parseInventoryItemString(itmStr);
            if (parsed) stagedCharacterInventory.push(parsed);
        }
        renderStagedInventory();
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
    if (!charName) return;
    await openLevelUpWizard(charName, "campaign");
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

    // Read current ability modifiers and prof bonus from the form
    const profBonusEl = document.getElementById("input-dnd-prof-bonus");
    const profBonus = profBonusEl ? Math.max(0, parseInt(profBonusEl.value.replace(/[^0-9-]/g, "") || "2", 10)) : 2;
    const mods = {
        str: calcDndMod(document.getElementById("input-dnd-str")?.value),
        dex: calcDndMod(document.getElementById("input-dnd-dex")?.value),
        con: calcDndMod(document.getElementById("input-dnd-con")?.value),
        int: calcDndMod(document.getElementById("input-dnd-int")?.value),
        wis: calcDndMod(document.getElementById("input-dnd-wis")?.value),
        cha: calcDndMod(document.getElementById("input-dnd-cha")?.value)
    };

    container.innerHTML = DND_5E_SKILLS_DEF.map(s => {
        const cleanKey = s.name.replace(/[^a-zA-Z]/g, '');
        let isProf = false;
        if (skillsObj[s.name] !== undefined && skillsObj[s.name] !== null) {
            const rawVal = skillsObj[s.name];
            if (typeof rawVal === "boolean") {
                isProf = rawVal;
            } else if (typeof rawVal === "object" && rawVal !== null) {
                isProf = Boolean(rawVal.proficient);
            } else {
                const v = String(rawVal).trim();
                const vLower = v.toLowerCase();
                if (vLower.includes("prof") || vLower.includes("expert") || v === "P" || v === "p" || v === "E" || v === "e" || vLower === "true") {
                    isProf = true;
                } else if (vLower === "false" || vLower === "none" || vLower === "off") {
                    isProf = false;
                } else {
                    // Compare numeric value against base ability mod + profBonus
                    const m = v.match(/[-+]?\d+/);
                    if (m && profBonus > 0) {
                        const numVal = parseInt(m[0], 10);
                        const baseMod = mods[s.stat] || 0;
                        if (numVal >= baseMod + profBonus) {
                            isProf = true;
                        }
                    }
                }
            }
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
    renderStagedSpells();
}

let editingCharacterSource = "campaign";
let currentEditingCharacterObj = null;
let isCurrentModalViewOnly = false;

function applyCharacterModalViewMode(isViewOnly) {
    isCurrentModalViewOnly = isViewOnly;
    const modal = document.getElementById("modal-add-character");
    if (!modal) return;

    // Toggle readonly / disabled on form inputs
    const formFields = modal.querySelectorAll("input:not([type='button']), textarea, select");
    formFields.forEach(el => {
        if (el.id === "input-modal-char-file") return;
        el.disabled = isViewOnly;
        if (isViewOnly) {
            el.classList.add("modal-view-mode");
        } else {
            el.classList.remove("modal-view-mode");
        }
    });

    // Checkboxes for saves & skills
    modal.querySelectorAll("input[type='checkbox']").forEach(chk => {
        chk.disabled = isViewOnly;
    });

    // Toggle add action buttons and upload buttons
    const addActionBtns = [
        "btn-modal-load-char-file",
        "btn-modal-load-char-file-body",
        "btn-modal-add-weapon-dnd",
        "btn-modal-add-spell-dnd",
        "btn-modal-add-item-dnd",
        "btn-modal-add-weapon-pf",
        "btn-modal-add-spell-pf",
        "btn-modal-add-item-pf",
        "btn-modal-add-weapon-cyber",
        "btn-modal-add-item-cyber",
        "btn-add-cyberware",
        "btn-modal-add-weapon-coc",
        "btn-modal-add-item-coc",
        "btn-modal-add-weapon-agnostic",
        "btn-modal-add-spell-agnostic",
        "btn-modal-add-item-agnostic",
        "add-custom-meter-btn"
    ];
    addActionBtns.forEach(id => {
        const btn = document.getElementById(id);
        if (btn) {
            if (isViewOnly) {
                btn.classList.add("hidden");
                btn.style.display = "none";
            } else {
                btn.classList.remove("hidden");
                btn.style.display = "";
            }
        }
    });

    // Footer buttons
    const confirmBtn = document.getElementById("btn-confirm-char");
    const toggleEditBtn = document.getElementById("btn-modal-char-view-toggle-edit");
    const cancelBtn = document.getElementById("btn-cancel-char");
    const heading = document.getElementById("modal-char-heading");

    if (confirmBtn) {
        if (isViewOnly) confirmBtn.classList.add("hidden");
        else confirmBtn.classList.remove("hidden");
    }
    if (toggleEditBtn) {
        if (isViewOnly) toggleEditBtn.classList.remove("hidden");
        else toggleEditBtn.classList.add("hidden");
    }
    if (cancelBtn) {
        cancelBtn.textContent = isViewOnly ? "Close" : "Cancel";
    }

    if (heading && currentEditingCharacterObj) {
        const cName = currentEditingCharacterObj.name || "Hero";
        if (isViewOnly) {
            heading.textContent = editingCharacterSource === "vault"
                ? `👁️ View Vault Character: ${cName}`
                : `👁️ View Character: ${cName}`;
        } else {
            heading.textContent = editingCharacterSource === "vault"
                ? `✏️ Edit Vault Character: ${cName}`
                : `✏️ Edit Character: ${cName}`;
        }
    }

    // Refresh chips to remove edit/delete icons in view mode
    renderStagedWeapons();
    renderStagedSpells();
    renderStagedInventory();
    renderStagedCyberware();
    renderStagedCustomMeters(isViewOnly);
}

async function handleOpenEditCharacterModal(charId, source = "campaign", isViewOnly = false) {
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

    let rawClsLvl = ch.class_and_level || ch.archetype || "Fighter 1";
    let parsedSpecies = ch.species || ch.race || "";
    let parsedBg = ch.background || "";
    if (rawClsLvl.includes("(") && rawClsLvl.includes(")")) {
        const mP = rawClsLvl.match(/\(([^)]+)\)/);
        if (mP) {
            const inner = mP[1].split(/,\s*/);
            if (!parsedSpecies && inner[0]) parsedSpecies = inner[0].trim();
            if (!parsedBg && inner[1]) parsedBg = inner[1].trim();
        }
        rawClsLvl = rawClsLvl.replace(/\s*\([^)]*\)/g, "").replace(/\s*\[[^\]]*\]/g, "").trim();
    }
    rawClsLvl = rawClsLvl.replace(/\s*\[[^\]]*\]/g, "").trim();
    if (dndClassEl) dndClassEl.value = rawClsLvl;
    if (dndSpeciesEl) dndSpeciesEl.value = parsedSpecies;
    if (dndBgEl) dndBgEl.value = parsedBg;
    if (dndXpEl) dndXpEl.value = ch.experience_points ?? 0;

    const curLvlVal = parseTotalLevelFromClass(rawClsLvl, ch.level || 1);
    function syncModalXpProgress(xpVal, lvlVal) {
        const xp = parseInt(xpVal, 10) || 0;
        const lvl = parseInt(lvlVal, 10) || 1;
        const nextReq = getDndNextLevelXp(lvl);
        const isMax = lvl >= 20;
        const maxText = isMax ? "/ MAX" : `/ ${nextReq.toLocaleString()}`;
        const isReady = !isMax && xp >= nextReq;

        const dndMax = document.getElementById("label-dnd-xp-max");
        const rpMax = document.getElementById("label-rp-char-xp-max");
        if (dndMax) {
            dndMax.textContent = maxText;
            dndMax.style.color = isReady ? "#4ade80" : "#94a3b8";
            dndMax.style.fontWeight = isReady ? "700" : "600";
            dndMax.title = isReady ? `Level Up Ready for Level ${lvl + 1}!` : `Next Level at ${nextReq.toLocaleString()} XP`;
        }
        if (rpMax) {
            rpMax.textContent = maxText;
            rpMax.style.color = isReady ? "#4ade80" : "#94a3b8";
            rpMax.style.fontWeight = isReady ? "700" : "600";
            rpMax.title = isReady ? `Level Up Ready for Level ${lvl + 1}!` : `Next Level at ${nextReq.toLocaleString()} XP`;
        }
    }
    const getActiveDndLevel = () => {
        return parseTotalLevelFromClass(dndClassEl?.value, curLvlVal);
    };
    if (dndClassEl) {
        dndClassEl.addEventListener("input", () => {
            syncModalXpProgress(dndXpEl ? dndXpEl.value : 0, getActiveDndLevel());
        });
    }
    if (dndXpEl) {
        dndXpEl.oninput = () => syncModalXpProgress(dndXpEl.value, getActiveDndLevel());
    }
    syncModalXpProgress(dndXpEl ? dndXpEl.value : 0, getActiveDndLevel());

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
    const saves = ch.saving_throws || ch.native_system_data?.saving_throws || {};
    const strSaveChk = document.getElementById("chk-dnd-save-str");
    const dexSaveChk = document.getElementById("chk-dnd-save-dex");
    const conSaveChk = document.getElementById("chk-dnd-save-con");
    const intSaveChk = document.getElementById("chk-dnd-save-int");
    const wisSaveChk = document.getElementById("chk-dnd-save-wis");
    const chaSaveChk = document.getElementById("chk-dnd-save-cha");

    const strMod = calcDndMod(strEl?.value);
    const dexMod = calcDndMod(dexEl?.value);
    const conMod = calcDndMod(conEl?.value);
    const intMod = calcDndMod(intEl?.value);
    const wisMod = calcDndMod(wisEl?.value);
    const chaMod = calcDndMod(chaEl?.value);

    function isSaveProficient(statName, shortStat, statMod) {
        const saveEntry = saves[statName] ?? saves[shortStat] ?? saves[shortStat.toLowerCase()];
        if (saveEntry === undefined || saveEntry === null) return false;
        if (typeof saveEntry === "object") {
            if (saveEntry.proficient !== undefined) return Boolean(saveEntry.proficient);
            const val = parseInt(String(saveEntry.value || saveEntry.bonus || 0).replace(/[^0-9-]/g, ""), 10);
            return val > statMod;
        }
        const strVal = String(saveEntry).trim();
        if (strVal.toLowerCase() === "true") return true;
        const num = parseInt(strVal.replace(/[^0-9-]/g, ""), 10);
        if (!isNaN(num)) return num > statMod;
        return false;
    }

    if (strSaveChk) strSaveChk.checked = isSaveProficient("Strength", "STR", strMod);
    if (dexSaveChk) dexSaveChk.checked = isSaveProficient("Dexterity", "DEX", dexMod);
    if (conSaveChk) conSaveChk.checked = isSaveProficient("Constitution", "CON", conMod);
    if (intSaveChk) intSaveChk.checked = isSaveProficient("Intelligence", "INT", intMod);
    if (wisSaveChk) wisSaveChk.checked = isSaveProficient("Wisdom", "WIS", wisMod);
    if (chaSaveChk) chaSaveChk.checked = isSaveProficient("Charisma", "CHA", chaMod);

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
    const clsLvlStrForSpells = ch.class_and_level || ch.archetype || ch.native_system_data?.class_level || "Fighter 1";
    window.activeCharacterClass = clsLvlStrForSpells;

    // Authoritative calculation of max prepared leveled spells
    let maxPrep = 3;
    const clsLower = clsLvlStrForSpells.toLowerCase();
    const mLvl = clsLvlStrForSpells.match(/\d+/);
    const lvlNum = mLvl ? parseInt(mLvl[0], 10) : (parseInt(ch.level, 10) || 1);
    const prepIntMod = calcDndMod(attrs.Intelligence ?? attrs.INT ?? 10);
    const prepWisMod = calcDndMod(attrs.Wisdom ?? attrs.WIS ?? 10);
    const prepChaMod = calcDndMod(attrs.Charisma ?? attrs.CHA ?? 10);

    if (typeof ch.max_prepared_leveled_spells === "number" && ch.max_prepared_leveled_spells >= 0) {
        maxPrep = ch.max_prepared_leveled_spells;
    } else if (ch.native_system_data && typeof ch.native_system_data.max_prepared_leveled_spells === "number" && ch.native_system_data.max_prepared_leveled_spells >= 0) {
        maxPrep = ch.native_system_data.max_prepared_leveled_spells;
    } else if (clsLower.includes("druid") || clsLower.includes("cleric")) {
        maxPrep = Math.max(1, prepWisMod + lvlNum);
    } else if (clsLower.includes("wizard")) {
        maxPrep = Math.max(1, prepIntMod + lvlNum);
    } else if (clsLower.includes("paladin")) {
        maxPrep = Math.max(1, prepChaMod + Math.floor(lvlNum / 2));
    } else if (clsLower.includes("artificer")) {
        maxPrep = Math.max(1, prepIntMod + Math.floor(lvlNum / 2));
    } else if (clsLower.includes("ranger")) {
        maxPrep = Math.max(1, prepWisMod + Math.floor(lvlNum / 2));
    } else {
        maxPrep = Math.max(1, prepIntMod + lvlNum);
    }

    const hasPreparedClass = /paladin|wizard|cleric|druid|artificer/i.test(clsLvlStrForSpells);
    const hasKnownClass = /warlock|sorcerer|bard/i.test(clsLvlStrForSpells);
    const isMulticlass = clsLvlStrForSpells.includes("/");
    const isPureKnownCaster = hasKnownClass && !hasPreparedClass && !isMulticlass;

    const rawSpells = Array.isArray(ch.spells) ? ch.spells : (ch.native_system_data?.spells || []);
    const seenModalSpells = new Set();
    stagedCharacterSpells = rawSpells.map(s => {
        if (typeof s === "object" && s !== null) {
            const clone = JSON.parse(JSON.stringify(s));
            const sLvl = clone.level || "Cantrip";
            const isCantrip = (clone.is_cantrip !== undefined)
                ? Boolean(clone.is_cantrip)
                : (String(sLvl).toLowerCase().includes("cantrip") || sLvl === "0" || sLvl === 0);
            clone.is_cantrip = isCantrip;
            if (isCantrip || clone.is_always_prepared) {
                clone.prepared = true;
            } else if (clone.prepared !== undefined && clone.prepared !== null) {
                clone.prepared = Boolean(clone.prepared);
            } else if (isPureKnownCaster) {
                clone.prepared = true;
            } else {
                clone.prepared = false;
            }
            return clone;
        }
        const sStr = String(s).trim();
        const dmgM = sStr.match(/(\d+d\d+(?:\s*[-+]\s*\d+)?)/i);
        const dcM = sStr.match(/(DC\s*\d+\s*[A-Za-z]+|\+\d+)/i);
        const isCantrip = sStr.toLowerCase().includes("cantrip");
        return {
            name: sStr.replace(/\s*\([^)]*\)/g, "").trim() || sStr,
            level: isCantrip ? "Cantrip" : "1st Level",
            is_cantrip: isCantrip,
            prepared: isCantrip ? true : (isPureKnownCaster ? true : false),
            atk_bonus: dcM ? dcM[0] : "",
            damage: dmgM ? dmgM[0] : "",
            range: "60 ft.",
            notes: ""
        };
    }).filter(sp => {
        const key = String(sp.name || "").replace(/\s*(?:\[.*?\]|<.*?>|\(.*?\))/g, "").trim().toLowerCase();
        if (!key || seenModalSpells.has(key)) return false;
        if (key.length <= 1) return false;
        seenModalSpells.add(key);
        return true;
    });

    // If total prepared leveled spells exceeds maxPrep on load (e.g. from previously over-prepped save data),
    // clamp or unprepare leveled non-subclass spells so the character starts in a legal state
    let currentlyPreparedLeveled = stagedCharacterSpells.filter(s => !s.is_cantrip && !s.is_always_prepared && s.prepared).length;
    if (currentlyPreparedLeveled > maxPrep) {
        let kept = 0;
        stagedCharacterSpells.forEach(s => {
            if (!s.is_cantrip && !s.is_always_prepared) {
                if (kept < maxPrep && s.prepared) {
                    kept++;
                } else {
                    s.prepared = false;
                }
            }
        });
    }

    window.stagedCharacterSpells = stagedCharacterSpells;

    if (typeof setupSpellPreparationHandlers === "function") {
        setupSpellPreparationHandlers(maxPrep, clsLvlStrForSpells);
    }

    renderStagedSpells();

    // Staged inventory array for structured editing
    const rawInvList = extractCharacterInventory(ch);
    stagedCharacterInventory = rawInvList.map(i => {
        if (typeof i === "object" && i !== null) return JSON.parse(JSON.stringify(i));
        return parseInventoryItemString(String(i));
    }).filter(Boolean);
    renderStagedInventory();

    // Staged cyberware array for structured editing
    const rawCyberList = extractCharacterCyberware(ch);
    stagedCharacterCyberware = rawCyberList.map(c => {
        if (typeof c === "object" && c !== null) return JSON.parse(JSON.stringify(c));
        return { name: String(c), category: "Neural Link", slot: "", details: "" };
    }).filter(Boolean);
    renderStagedCyberware();

    // Module B: Staged custom meters for structured editing
    stagedCustomMeters = JSON.parse(JSON.stringify(ch.custom_meters || ch.native_system_data?.custom_meters || {}));
    renderStagedCustomMeters(isViewOnly);

    // Module A: Inspiration, Save DC, Attuned Items
    const charInspToggle = document.getElementById("char-inspiration-toggle");
    const charAbilitySaveDc = document.getElementById("char-ability-save-dc");
    const charAttunedCurrent = document.getElementById("char-attuned-current");
    const charAttunedMax = document.getElementById("char-attuned-max");
    const natData = ch.native_system_data || {};
    if (charInspToggle) charInspToggle.checked = Boolean(ch.inspiration ?? natData.inspiration);
    if (charAbilitySaveDc) charAbilitySaveDc.value = ch.ability_save_dc ?? natData.ability_save_dc ?? (8 + 2 + calcDndMod(intEl?.value));
    const attunedObj = ch.attuned_magic_items || natData.attuned_magic_items || {};
    if (charAttunedCurrent) charAttunedCurrent.value = attunedObj.current ?? 0;
    if (charAttunedMax) charAttunedMax.value = attunedObj.max ?? 3;

    // Module C: Spell Slots Allocation (Levels 1–9 Dynamic)
    const spellSlots = ch.spell_slots || natData.spell_slots || {};
    if (typeof window.renderSpellSlotInputs === "function") {
        window.renderSpellSlotInputs(spellSlots);
    }

    // Module D: Roleplay Anchors
    const rpAnchors = ch.roleplay_anchors || natData.roleplay_anchors || ch.universal_core?.roleplay_anchors || {};
    const rpTraits = document.getElementById("rp-personality-traits");
    const rpIdeals = document.getElementById("rp-ideals");
    const rpBonds = document.getElementById("rp-bonds");
    const rpFlaws = document.getElementById("rp-flaws");
    const rpAllies = document.getElementById("rp-allies-orgs");
    if (rpTraits) rpTraits.value = rpAnchors.personality_traits || ch.personality_traits || "";
    if (rpIdeals) rpIdeals.value = rpAnchors.ideals || ch.ideals || "";
    if (rpBonds) rpBonds.value = rpAnchors.bonds || ch.bonds || "";
    if (rpFlaws) rpFlaws.value = rpAnchors.flaws || ch.flaws || "";
    if (rpAllies) rpAllies.value = rpAnchors.allies_and_organizations || ch.allies || "";

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
    if (lvlEl) {
        lvlEl.value = curLvlVal;
        lvlEl.oninput = () => syncModalXpProgress(xpEl ? xpEl.value : (dndXpEl?.value || 0), lvlEl.value);
    }
    if (xpEl) {
        xpEl.value = ch.experience_points ?? 0;
        syncModalXpProgress(xpEl.value, lvlEl ? lvlEl.value : curLvlVal);
        xpEl.oninput = () => syncModalXpProgress(xpEl.value, lvlEl ? lvlEl.value : curLvlVal);
    }
    if (wpnsElAgn) wpnsElAgn.value = wpnsFormatted;
    if (invElAgn) invElAgn.value = invFormatted;
    if (spellsElAgn) spellsElAgn.value = spellsFormatted;
    if (featsElAgn) featsElAgn.value = featsFormatted;
    if (skillsElAgn) skillsElAgn.value = typeof ch.skills === "object" ? Object.entries(ch.skills).map(([k, v]) => `${k} (${v})`).join(", ") : (ch.proficiencies || "");

    // Populate Physical Appearance & Character Details (Common for All Systems)
    const descObj = ch.description || ch.physical_description || ch.native_system_data?.description || ch.universal_core?.description || {};
    const genderEl = document.getElementById("input-char-gender");
    const ageEl = document.getElementById("input-char-age");
    const sizeEl = document.getElementById("input-char-size");
    const heightEl = document.getElementById("input-char-height");
    const weightEl = document.getElementById("input-char-weight");
    const creatureTypeEl = document.getElementById("input-char-creature-type");
    const alignmentEl = document.getElementById("input-char-alignment");
    const faithEl = document.getElementById("input-char-faith");
    const skinEl = document.getElementById("input-char-skin");
    const eyesEl = document.getElementById("input-char-eyes");
    const hairEl = document.getElementById("input-char-hair");

    if (genderEl) genderEl.value = descObj.gender || ch.gender || ch.sex || ch.native_system_data?.gender || ch.universal_core?.description?.gender || "";
    if (ageEl) ageEl.value = descObj.age || ch.age || ch.native_system_data?.age || ch.universal_core?.description?.age || "";
    if (sizeEl) sizeEl.value = descObj.size || ch.size || ch.native_system_data?.size || ch.universal_core?.description?.size || "Medium";
    if (heightEl) heightEl.value = descObj.height || ch.height || ch.native_system_data?.height || ch.universal_core?.description?.height || "";
    if (weightEl) weightEl.value = descObj.weight || ch.weight || ch.native_system_data?.weight || ch.universal_core?.description?.weight || "";
    if (creatureTypeEl) creatureTypeEl.value = descObj.creature_type || ch.creature_type || ch.native_system_data?.creature_type || ch.universal_core?.description?.creature_type || "Humanoid";
    if (alignmentEl) alignmentEl.value = descObj.alignment || ch.alignment || ch.native_system_data?.alignment || ch.universal_core?.description?.alignment || "";
    if (faithEl) faithEl.value = descObj.faith || ch.faith || ch.native_system_data?.faith || ch.universal_core?.description?.faith || "";
    if (skinEl) skinEl.value = descObj.skin || ch.skin || ch.native_system_data?.skin || ch.universal_core?.description?.skin || "";
    if (eyesEl) eyesEl.value = descObj.eyes || ch.eyes || ch.native_system_data?.eyes || ch.universal_core?.description?.eyes || "";
    if (hairEl) hairEl.value = descObj.hair || ch.hair || ch.native_system_data?.hair || ch.universal_core?.description?.hair || "";

    // Populate Backstory & Additional Notes
    const backstoryEl = document.getElementById("input-char-backstory");
    const notesEl = document.getElementById("input-char-additional-notes");
    if (backstoryEl) backstoryEl.value = ch.backstory || ch.native_system_data?.backstory || ch.personal_description || "";
    if (notesEl) notesEl.value = ch.additional_notes || ch.notes || ch.native_system_data?.additional_notes || "";

    // Populate D&D Weight Carried, Encumbered, and Push/Drag/Lift
    const weightCarriedEl = document.getElementById("input-dnd-weight-carried");
    const encumberedEl = document.getElementById("input-dnd-encumbered");
    const pushDragLiftEl = document.getElementById("input-dnd-push-drag-lift");
    const charStrVal = attrs.Strength ?? attrs.Might ?? 10;
    if (weightCarriedEl) {
        weightCarriedEl.value = ch.weight_carried || ch.native_system_data?.weight_carried || "";
        weightCarriedEl.dataset.autoCalculated = ch.weight_carried ? "false" : "true";
    }
    if (encumberedEl) {
        encumberedEl.value = ch.encumbered || ch.native_system_data?.encumbered || `${charStrVal * 15} lb.`;
    }
    if (pushDragLiftEl) {
        pushDragLiftEl.value = ch.push_drag_lift || ch.native_system_data?.push_drag_lift || `${charStrVal * 30} lb.`;
    }

    // Apply View or Edit mode styling and lock states
    applyCharacterModalViewMode(isViewOnly);

    const charModal = document.getElementById("modal-add-character");
    if (charModal) charModal.classList.remove("hidden");
}

window.handleOpenViewCharacterModal = async function(charId, source = "campaign") {
    await handleOpenEditCharacterModal(charId, source, true);
};
window.handleOpenEditCharacterModal = handleOpenEditCharacterModal;

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
    const selEl = document.getElementById("rp-campaign-select");
    const activeId = (selEl && selEl.value) ? selEl.value : ttrpgData.active_campaign;
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
window.handleDeleteActiveCampaign = handleDeleteActiveCampaign;

// Event hooks invoked by window.aetherUI.handleEvent
window.handleRoleplayDiceEvent = function(rollData) {
    appendDiceAuditEntry(rollData);
    if (typeof renderRolePlayStoryBubble === "function" && rollData && rollData.status === "success") {
        const actor = rollData.character_name ? `[${rollData.character_name}] ` : "";
        const purpose = rollData.purpose ? ` (${rollData.purpose})` : "";
        renderRolePlayStoryBubble({
            type: "tool",
            name: "DICE ROLL",
            content: `🎲 ${actor}${rollData.formula}: ${rollData.summary || rollData.total} -> Total: ${rollData.total}${purpose}`
        });
    }
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

    let cleanContent = item.content || '';
    if (typeof cleanContent === 'string' && cleanContent.startsWith('[TABLETOP ROLEPLAY ACTION')) {
        const m = cleanContent.match(/Player Action \/ Dialogue:\s*([\s\S]*?)(?:\n\[GAME MASTER DIRECTIVE|$)/);
        if (m && m[1]) cleanContent = m[1].trim();
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
        <div class="bubble-body">${escape(cleanContent)}</div>
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
        world: document.getElementById('rp-world-pane'),
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
            if (target === 'world') {
                renderWorldStateLedger();
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
            const cid = (ttrpgData && ttrpgData.active_campaign)
                     || (window.ttrpgData && window.ttrpgData.active_campaign)
                     || (window.ttrpgData && window.ttrpgData.manifest && window.ttrpgData.manifest.active_campaign);
            if (!text) return;
            if (!cid) {
                renderRolePlayStoryBubble({
                    type: 'error',
                    content: '⚠️ No adventure campaign is currently selected. Please select or create a campaign first before taking actions.'
                });
                return;
            }
            storyInput.value = '';

            try {
                if (window.pywebview && window.pywebview.api) {
                    if (window.pywebview.api.send_workspace_message) {
                        const res = await window.pywebview.api.send_workspace_message('campaign', cid, text);
                        if (res && res.error) {
                            renderRolePlayStoryBubble({ type: 'error', content: `Failed to send action: ${res.error}` });
                        }
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
    initByocWorldModals();
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

    // Next Turn & End Combat Buttons
    const nextTurnBtn = document.getElementById("next-turn-btn");
    if (nextTurnBtn) {
        nextTurnBtn.addEventListener("click", handleAdvanceTurn);
    }
    const endCombatBtn = document.getElementById("end-combat-btn");
    if (endCombatBtn) {
        endCombatBtn.addEventListener("click", handleEndCombat);
    }

    // Difficulty Selector Dropdown Toggle & Selection
    const diffBadge = document.getElementById("rp-active-difficulty-badge");
    const diffDropdown = document.getElementById("rp-difficulty-dropdown");
    if (diffBadge && diffDropdown) {
        diffBadge.addEventListener("click", (e) => {
            e.stopPropagation();
            const isHidden = diffDropdown.style.display === "none";
            diffDropdown.style.display = isHidden ? "flex" : "none";
        });

        diffDropdown.querySelectorAll(".rp-diff-opt").forEach(opt => {
            opt.addEventListener("click", async (e) => {
                e.stopPropagation();
                const chosenDiff = opt.getAttribute("data-diff");
                diffDropdown.style.display = "none";
                if (chosenDiff) {
                    await handleSelectDifficulty(chosenDiff);
                }
            });
        });

        document.addEventListener("click", (e) => {
            if (diffDropdown.style.display !== "none" && !diffDropdown.contains(e.target) && e.target !== diffBadge) {
                diffDropdown.style.display = "none";
            }
        });
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

            // Reset Universal Tone Sliders
            const sliderGrittiness = document.getElementById("campaign-tone-grittiness");
            const labelGrittiness = document.getElementById("val-tone-grittiness");
            const sliderMagic = document.getElementById("campaign-tone-magic");
            const labelMagic = document.getElementById("val-tone-magic");
            const sliderIntrigue = document.getElementById("campaign-tone-intrigue");
            const labelIntrigue = document.getElementById("val-tone-intrigue");

            if (sliderGrittiness) sliderGrittiness.value = "5";
            if (labelGrittiness) labelGrittiness.textContent = "5/10";
            if (sliderMagic) sliderMagic.value = "7";
            if (labelMagic) labelMagic.textContent = "7/10";
            if (sliderIntrigue) sliderIntrigue.value = "5";
            if (labelIntrigue) labelIntrigue.textContent = "5/10";

            // Reset Module Story Adherence Slider
            const sliderAdherence = document.getElementById("campaign-module-adherence");
            if (sliderAdherence) {
                sliderAdherence.value = "3";
                if (window._updateAdherenceDisplay) window._updateAdherenceDisplay("3");
            }

            // Reset segmented source selector
            const btnSrcModule = document.getElementById("btn-src-module");
            const btnSrcByoc = document.getElementById("btn-src-byoc");
            const secSrcModule = document.getElementById("section-src-module");
            const secSrcByoc = document.getElementById("section-src-byoc");
            if (btnSrcModule && btnSrcByoc && secSrcModule && secSrcByoc) {
                btnSrcModule.classList.add("active");
                btnSrcByoc.classList.remove("active");
                secSrcModule.classList.remove("hidden");
                secSrcByoc.classList.add("hidden");
            }

            tempByocState = getDefaultByocSeed();
            updateByocSummaryBadge();

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

            const sliderAdherence = document.getElementById("campaign-module-adherence");
            if (sliderAdherence) {
                const adh = (meta.module_adherence || "strict").toLowerCase().trim();
                sliderAdherence.value = (adh === "creative" || adh === "1") ? "1" : ((adh === "balanced" || adh === "2") ? "2" : "3");
                if (window._updateAdherenceDisplay) window._updateAdherenceDisplay(sliderAdherence.value);
            }

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
            const selEl = document.getElementById("rp-campaign-select");
            const activeId = (selEl && selEl.value) ? selEl.value : ttrpgData.active_campaign;
            if (!activeId) return;
            const campaigns = ttrpgData.campaigns || {};
            const meta = campaigns[activeId] || ttrpgData.active_campaign_meta || {};
            const title = meta.title || activeId;
            if (!(await showAetherConfirm(`Delete campaign "${title}"? This cannot be undone.`, "Delete Campaign", "Delete", true))) return;
            try {
                if (window.pywebview && window.pywebview.api && window.pywebview.api.delete_ttrpg_campaign) {
                    await window.pywebview.api.delete_ttrpg_campaign(activeId);
                    campModal.classList.add("hidden");
                    await loadRolePlayUI();
                }
            } catch (e) {
                console.error("[RolePlay] Delete campaign modal error:", e);
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
            const sliderAdherence = document.getElementById("campaign-module-adherence");
            const adherenceKey = sliderAdherence ? (
                sliderAdherence.value === "1" ? "creative" : (sliderAdherence.value === "2" ? "balanced" : "strict")
            ) : "strict";

            if (!title && stagedCampaignModalFiles.length > 0) {
                let stem = stagedCampaignModalFiles[0].name.replace(/\.[^/.]+$/, "").replace(/[\d_\-]+$/, "").replace(/_/g, " ").trim();
                if (stem) title = stem.replace(/\b\w/g, l => l.toUpperCase());
            }

            if (!title) {
                await showAetherAlert("Please enter a campaign title (or attach a module/adventure file).", "Campaign Title Required");
                return;
            }

            if (campaignModalEditMode) {
                if (window.pywebview && window.pywebview.api && window.pywebview.api.update_ttrpg_campaign) {
                    const res = await window.pywebview.api.update_ttrpg_campaign(
                        ttrpgData.active_campaign,
                        title,
                        preset,
                        gmMode,
                        overview,
                        tone,
                        difficulty,
                        null,
                        null,
                        adherenceKey
                    );
                    if (res && res.status === "error") {
                        await showAetherAlert(res.message || "Failed to update campaign.", "Campaign Error");
                        return;
                    }
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
                    const byocPayload = getByocModalData();
                    if (window.pywebview && window.pywebview.api && window.pywebview.api.create_ttrpg_campaign) {
                        const res = await window.pywebview.api.create_ttrpg_campaign(
                            title,
                            preset,
                            gmMode,
                            overview,
                            tone,
                            difficulty,
                            "",
                            initialFilesPayload,
                            byocPayload,
                            adherenceKey
                        );
                        if (res && res.status === "error") {
                            await showAetherAlert(res.message || "Failed to create campaign.", "Campaign Error");
                            return;
                        }
                        stagedCampaignModalFiles = [];
                        window._loadedCampaignStoryId = null;
                        campModal.classList.add("hidden");
                        await loadRolePlayUI();
                    }
                } catch (err) {
                    console.error("[RolePlay] Error creating campaign:", err);
                    await showAetherAlert(`Error creating campaign: ${err}`, "Campaign Error");
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
            stagedCharacterInventory = [];
            renderStagedInventory();
            stagedCharacterCyberware = [];
            renderStagedCyberware();
            stagedCustomMeters = {};
            renderStagedCustomMeters(false);

            // Reset Module A & C (Inspiration, DC, Attunement, Spell Slots)
            const resetInsp = document.getElementById("char-inspiration-toggle");
            if (resetInsp) resetInsp.checked = false;
            const resetDc = document.getElementById("char-ability-save-dc");
            if (resetDc) resetDc.value = "13";
            const resetAttCur = document.getElementById("char-attuned-current");
            if (resetAttCur) resetAttCur.value = "0";
            const resetAttMax = document.getElementById("char-attuned-max");
            if (resetAttMax) resetAttMax.value = "3";
            if (typeof window.renderSpellSlotInputs === "function") {
                window.renderSpellSlotInputs({});
            }
            const tallyEl = document.getElementById("prep-spells-tally");
            if (tallyEl) tallyEl.textContent = "0 / 0";

            // Reset Weight, Encumbrance, Appearance, Roleplay Anchors, and Backstory/Notes
            const weightCarriedEl = document.getElementById("input-dnd-weight-carried");
            const encumberedEl = document.getElementById("input-dnd-encumbered");
            const pushDragLiftEl = document.getElementById("input-dnd-push-drag-lift");
            if (weightCarriedEl) { weightCarriedEl.value = ""; weightCarriedEl.dataset.autoCalculated = "true"; }
            if (encumberedEl) encumberedEl.value = "120 lb.";
            if (pushDragLiftEl) pushDragLiftEl.value = "240 lb.";

            [
                "input-char-gender", "input-char-age", "input-char-height",
                "input-char-weight", "input-char-alignment",
                "input-char-faith", "input-char-skin", "input-char-eyes", "input-char-hair",
                "input-char-backstory", "input-char-additional-notes",
                "rp-personality-traits", "rp-ideals", "rp-bonds", "rp-flaws", "rp-allies-orgs"
            ].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            const sizeReset = document.getElementById("input-char-size");
            if (sizeReset) sizeReset.value = "Medium";
            const typeReset = document.getElementById("input-char-creature-type");
            if (typeReset) typeReset.value = "Humanoid";

            applyCharacterModalViewMode(false);

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
    const toggleEditCharBtn = document.getElementById("btn-modal-char-view-toggle-edit");
    if (toggleEditCharBtn) {
        toggleEditCharBtn.addEventListener("click", () => {
            applyCharacterModalViewMode(false);
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

                const gender = (document.getElementById("input-char-gender")?.value || "").trim();
                const age = (document.getElementById("input-char-age")?.value || "").trim();
                const size = (document.getElementById("input-char-size")?.value || "").trim() || "Medium";
                const height = (document.getElementById("input-char-height")?.value || "").trim();
                const weight = (document.getElementById("input-char-weight")?.value || "").trim();
                const creatureType = (document.getElementById("input-char-creature-type")?.value || "").trim() || "Humanoid";
                const alignment = (document.getElementById("input-char-alignment")?.value || "").trim();
                const faith = (document.getElementById("input-char-faith")?.value || "").trim();
                const skin = (document.getElementById("input-char-skin")?.value || "").trim();
                const eyes = (document.getElementById("input-char-eyes")?.value || "").trim();
                const hair = (document.getElementById("input-char-hair")?.value || "").trim();
                const backstory = (document.getElementById("input-char-backstory")?.value || "").trim();
                const additionalNotes = (document.getElementById("input-char-additional-notes")?.value || "").trim();

                const weightCarried = (document.getElementById("input-dnd-weight-carried")?.value || "").trim();
                const encumbered = (document.getElementById("input-dnd-encumbered")?.value || "").trim();
                const pushDragLift = (document.getElementById("input-dnd-push-drag-lift")?.value || "").trim();

                let payload = {
                    id: editingCharacterId || undefined,
                    name: name,
                    player_name: playerName,
                    ruleset: activeRuleset,
                    gender: gender,
                    age: age,
                    size: size,
                    height: height,
                    weight: weight,
                    creature_type: creatureType,
                    alignment: alignment,
                    faith: faith,
                    skin: skin,
                    eyes: eyes,
                    hair: hair,
                    description: {
                        gender, age, size, height, weight, creature_type: creatureType, alignment, faith, skin, eyes, hair
                    },
                    backstory: backstory,
                    additional_notes: additionalNotes,
                    weight_carried: weightCarried,
                    encumbered: encumbered,
                    push_drag_lift: pushDragLift,
                    roleplay_anchors: {
                        personality_traits: (document.getElementById("rp-personality-traits")?.value || "").trim(),
                        ideals: (document.getElementById("rp-ideals")?.value || "").trim(),
                        bonds: (document.getElementById("rp-bonds")?.value || "").trim(),
                        flaws: (document.getElementById("rp-flaws")?.value || "").trim(),
                        allies_and_organizations: (document.getElementById("rp-allies-orgs")?.value || "").trim()
                    },
                    custom_meters: JSON.parse(JSON.stringify(stagedCustomMeters || {}))
                };

                if (activeRuleset === "dnd_5e") {
                    const clsLvl = (document.getElementById("input-dnd-class-level")?.value || "Fighter 1").trim();
                    const species = (document.getElementById("input-dnd-species")?.value || "").trim();
                    const bg = (document.getElementById("input-dnd-background")?.value || "").trim();
                    const xp = safeInt(document.getElementById("input-dnd-xp")?.value, 0);
                    const lvl = parseTotalLevelFromClass(clsLvl, existingChar?.level || 1);

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
                    const inventory = (stagedCharacterInventory && stagedCharacterInventory.length > 0)
                        ? JSON.parse(JSON.stringify(stagedCharacterInventory))
                        : (invRaw ? invRaw.split(/[\n,]+/).map(i => i.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []);
                    const featsRaw = (document.getElementById("input-dnd-features")?.value || "").trim();
                    const features = featsRaw ? featsRaw.split(/\n+|;\s*|,\s*(?![^()]*\))/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [];
                    const spellsRaw = (document.getElementById("input-dnd-spells")?.value || "").trim();
                    const spells = (stagedCharacterSpells && stagedCharacterSpells.length > 0)
                        ? stagedCharacterSpells
                        : (spellsRaw ? spellsRaw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []);

                    const archetype = `${clsLvl}${species || bg ? ' (' + [species, bg].filter(Boolean).join(', ') + ')' : ''}`;
                    const savedSpellSlots = (typeof window.collectDynamicSpellSlots === "function")
                        ? window.collectDynamicSpellSlots()
                        : (existingChar?.spell_slots || existingChar?.native_system_data?.spell_slots || {});

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
                        spell_attack_bonus: (document.getElementById("input-dnd-spell-atk")?.value || "").trim(),
                        inspiration: Boolean(document.getElementById("char-inspiration-toggle")?.checked),
                        ability_save_dc: safeInt(document.getElementById("char-ability-save-dc")?.value, 13),
                        attuned_magic_items: {
                            current: Math.min(3, Math.max(0, safeInt(document.getElementById("char-attuned-current")?.value, 0))),
                            max: 3
                        },
                        spell_slots: savedSpellSlots,
                        native_system_data: {
                            ...(existingChar?.native_system_data || {}),
                            inspiration: Boolean(document.getElementById("char-inspiration-toggle")?.checked),
                            ability_save_dc: safeInt(document.getElementById("char-ability-save-dc")?.value, 13),
                            spell_attack_bonus: (document.getElementById("input-dnd-spell-atk")?.value || "").trim(),
                            attuned_magic_items: {
                                current: Math.min(3, Math.max(0, safeInt(document.getElementById("char-attuned-current")?.value, 0))),
                                max: 3
                            },
                            spell_slots: savedSpellSlots,
                            custom_meters: JSON.parse(JSON.stringify(stagedCustomMeters || {})),
                            roleplay_anchors: {
                                personality_traits: (document.getElementById("rp-personality-traits")?.value || "").trim(),
                                ideals: (document.getElementById("rp-ideals")?.value || "").trim(),
                                bonds: (document.getElementById("rp-bonds")?.value || "").trim(),
                                flaws: (document.getElementById("rp-flaws")?.value || "").trim(),
                                allies_and_organizations: (document.getElementById("rp-allies-orgs")?.value || "").trim()
                            },
                            max_prepared_leveled_spells: (typeof activeMaxPreparedSpells === "number" ? activeMaxPreparedSpells : 3),
                            spells: spells
                        }
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
                        level: parseTotalLevelFromClass(clsLvl, existingChar?.level || 1),
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
                        inventory: (stagedCharacterInventory && stagedCharacterInventory.length > 0)
                            ? JSON.parse(JSON.stringify(stagedCharacterInventory))
                            : (document.getElementById("input-pf-inventory")?.value || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean),
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
                        cyberware: (stagedCharacterCyberware && stagedCharacterCyberware.length > 0)
                            ? { installed_items: stagedCharacterCyberware }
                            : (existingChar?.cyberware || {}),
                        features_and_traits: (stagedCharacterCyberware && stagedCharacterCyberware.length > 0)
                            ? stagedCharacterCyberware.map(c => typeof c === "string" ? c : `${c.name} (${c.category || 'Cyberware'}${c.details ? '; ' + c.details : ''})`)
                            : (cyberFeatures.length > 0 ? cyberFeatures : (existingChar ? extractCharacterFeatures(existingChar) : [])),
                        traits_and_abilities: (stagedCharacterCyberware && stagedCharacterCyberware.length > 0)
                            ? stagedCharacterCyberware.map(c => typeof c === "string" ? c : `${c.name} (${c.category || 'Cyberware'}${c.details ? '; ' + c.details : ''})`)
                            : (cyberFeatures.length > 0 ? cyberFeatures : (existingChar ? extractCharacterFeatures(existingChar) : [])),
                        inventory: (stagedCharacterInventory && stagedCharacterInventory.length > 0)
                            ? JSON.parse(JSON.stringify(stagedCharacterInventory))
                            : (document.getElementById("input-cyber-inventory")?.value || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
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
                        inventory: (stagedCharacterInventory && stagedCharacterInventory.length > 0)
                            ? JSON.parse(JSON.stringify(stagedCharacterInventory))
                            : invList,
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
                        inventory: (stagedCharacterInventory && stagedCharacterInventory.length > 0)
                            ? JSON.parse(JSON.stringify(stagedCharacterInventory))
                            : (invRaw ? invRaw.split(/[\n,]+/).map(i => i.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []),
                        spells: (stagedCharacterSpells && stagedCharacterSpells.length > 0)
                            ? stagedCharacterSpells
                            : (spellsRaw ? spellsRaw.split(/[\n,]+/).map(s => s.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : []),
                        features_and_traits: featsRaw ? featsRaw.split(/[\n,;]+/).map(f => f.trim().replace(/^[-•*]\s*/, "")).filter(Boolean) : [],
                        proficiencies: skillsRaw
                    };
                }

                // Check spell preparation legality before saving:
                const checkedLeveled = (typeof getCheckedLeveledCount === "function") ? getCheckedLeveledCount() : 0;
                if (typeof activeMaxPreparedSpells === "number" && activeMaxPreparedSpells > 0 && checkedLeveled > activeMaxPreparedSpells) {
                    await showAetherAlert(
                        `Cannot save character: ${payload.name || "Hero"} is preparing ${checkedLeveled} leveled spells, which exceeds their allotted preparation limit of ${activeMaxPreparedSpells}.\n\nPlease uncheck (unprepare) ${checkedLeveled - activeMaxPreparedSpells} leveled spell(s) before saving.`,
                        "Spell Preparation Limit Exceeded"
                    );
                    return;
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
                    const res = await window.pywebview.api.start_or_resume_ttrpg_campaign();
                    if (res && res.error) {
                        console.error("[RolePlay] Error starting/resuming adventure:", res.error);
                        renderRolePlayStoryBubble({ type: "error", content: `⚠️ Failed to resume adventure: ${res.error}` });
                    }
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

    // Character Sheet In-Modal Add Item Buttons
    ["btn-modal-add-item-dnd", "btn-modal-add-item-pf", "btn-modal-add-item-cyber", "btn-modal-add-item-coc", "btn-modal-add-item-agnostic"].forEach(btnId => {
        const btn = document.getElementById(btnId);
        if (btn) {
            btn.addEventListener("click", () => window.handleOpenAddItemModal("__active_edit__"));
        }
    });

    // Character Sheet In-Modal Add Cyberware Button
    const btnModalAddCyberware = document.getElementById("btn-modal-add-cyberware");
    if (btnModalAddCyberware) {
        btnModalAddCyberware.addEventListener("click", () => window.handleOpenAddCyberwareModal("__active_edit__"));
    }

    // Character Sheet In-Modal Add Tracker / Meter Button
    const addMeterBtn = document.getElementById("add-custom-meter-btn");
    if (addMeterBtn) {
        addMeterBtn.addEventListener("click", () => {
            const name = prompt("Enter feature/tracker name (e.g. Healing Hands, Action Surge, Ki Points):");
            if (!name || !name.trim()) return;
            const maxValStr = prompt("Max charges / uses:", "1");
            const maxVal = Math.max(1, parseInt(maxValStr || "1", 10) || 1);
            const resetOn = prompt("Reset frequency (short_rest, long_rest, other):", "long_rest") || "long_rest";
            const key = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
            stagedCustomMeters[key] = {
                label: name.trim(),
                current: maxVal,
                max: maxVal,
                reset_on: resetOn
            };
            renderStagedCustomMeters(isCurrentModalViewOnly);
        });
    }

    // Item Modal Controls
    const addItemModal = document.getElementById("modal-add-item");
    const closeAddItemBtn = document.getElementById("btn-close-add-item-modal");
    const cancelAddItemBtn = document.getElementById("btn-cancel-add-item");
    const confirmAddItemBtn = document.getElementById("btn-confirm-add-item");

    if (closeAddItemBtn && addItemModal) {
        closeAddItemBtn.addEventListener("click", () => addItemModal.classList.add("hidden"));
    }
    if (cancelAddItemBtn && addItemModal) {
        cancelAddItemBtn.addEventListener("click", () => addItemModal.classList.add("hidden"));
    }
    if (confirmAddItemBtn) {
        confirmAddItemBtn.addEventListener("click", () => window.handleConfirmAddItem());
    }

    // Item Live Preview Listeners
    ["input-item-name", "input-item-qty", "input-item-notes"].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", () => updateItemLivePreview());
            el.addEventListener("change", () => updateItemLivePreview());
        }
    });
    const itemCatSelect = document.getElementById("select-item-category");
    if (itemCatSelect) {
        itemCatSelect.addEventListener("change", () => updateItemLivePreview());
    }

    const itemWeightInput = document.getElementById("input-item-weight");
    if (itemWeightInput) {
        itemWeightInput.addEventListener("input", () => updateItemLivePreview());
    }

    // Item Preset Pills
    document.querySelectorAll(".btn-item-preset-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            const name = pill.getAttribute("data-name") || "";
            const cat = pill.getAttribute("data-cat") || "Gear";
            const wt = pill.getAttribute("data-weight") || "";
            const notes = pill.getAttribute("data-notes") || "";
            const nameInput = document.getElementById("input-item-name");
            const catSelect = document.getElementById("select-item-category");
            const weightInput = document.getElementById("input-item-weight");
            const notesInput = document.getElementById("input-item-notes");
            if (nameInput) nameInput.value = name;
            if (catSelect) catSelect.value = cat;
            if (weightInput) weightInput.value = wt;
            if (notesInput) notesInput.value = notes;
            updateItemLivePreview();
        });
    });

    // ⚡ Item Smart Reformat Button
    const btnSmartItem = document.getElementById("btn-item-smart-reformat");
    if (btnSmartItem) {
        btnSmartItem.addEventListener("click", () => {
            const rawInput = document.getElementById("input-item-smart-raw");
            const rawVal = (rawInput?.value || "").trim();
            if (!rawVal) return;
            const parsed = parseInventoryItemString(rawVal);
            if (parsed) {
                const nameInput = document.getElementById("input-item-name");
                const qtyInput = document.getElementById("input-item-qty");
                const weightInput = document.getElementById("input-item-weight");
                const catSelect = document.getElementById("select-item-category");
                const notesInput = document.getElementById("input-item-notes");
                if (nameInput) nameInput.value = parsed.name;
                if (qtyInput) qtyInput.value = parsed.qty || "1";
                if (weightInput) weightInput.value = parsed.weight || "";
                if (catSelect) catSelect.value = parsed.category || "Gear";
                if (notesInput) notesInput.value = parsed.notes || "";
                updateItemLivePreview();
            }
        });
    }

    // Cyberware Modal Controls
    const addCyberModal = document.getElementById("modal-add-cyberware");
    const closeAddCyberBtn = document.getElementById("btn-close-add-cyberware-modal");
    const cancelAddCyberBtn = document.getElementById("btn-cancel-add-cyberware");
    const confirmAddCyberBtn = document.getElementById("btn-confirm-add-cyberware");

    if (closeAddCyberBtn && addCyberModal) {
        closeAddCyberBtn.addEventListener("click", () => addCyberModal.classList.add("hidden"));
    }
    if (cancelAddCyberBtn && addCyberModal) {
        cancelAddCyberBtn.addEventListener("click", () => addCyberModal.classList.add("hidden"));
    }
    if (confirmAddCyberBtn) {
        confirmAddCyberBtn.addEventListener("click", () => window.handleConfirmAddCyberware());
    }

    // Cyberware Live Preview Listeners
    ["input-cyberware-name", "input-cyberware-slot", "input-cyberware-details"].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", () => updateCyberwareLivePreview());
            el.addEventListener("change", () => updateCyberwareLivePreview());
        }
    });
    const cyberCatSelect = document.getElementById("select-cyberware-category");
    if (cyberCatSelect) {
        cyberCatSelect.addEventListener("change", () => updateCyberwareLivePreview());
    }

    // Cyberware Preset Pills
    document.querySelectorAll(".btn-cyber-preset-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            const name = pill.getAttribute("data-name") || "";
            const cat = pill.getAttribute("data-cat") || "Neural Link";
            const slot = pill.getAttribute("data-slot") || "";
            const det = pill.getAttribute("data-det") || "";
            const nameInput = document.getElementById("input-cyberware-name");
            const catSelect = document.getElementById("select-cyberware-category");
            const slotInput = document.getElementById("input-cyberware-slot");
            const detInput = document.getElementById("input-cyberware-details");
            if (nameInput) nameInput.value = name;
            if (catSelect) catSelect.value = cat;
            if (slotInput) slotInput.value = slot;
            if (detInput) detInput.value = det;
            updateCyberwareLivePreview();
        });
    });

    // ⚡ Cyberware Smart Reformat Button
    const btnSmartCyber = document.getElementById("btn-cyberware-smart-reformat");
    if (btnSmartCyber) {
        btnSmartCyber.addEventListener("click", () => {
            const rawInput = document.getElementById("input-cyberware-smart-raw");
            const rawVal = (rawInput?.value || "").trim();
            if (!rawVal) return;
            const nameMatch = rawVal.match(/^([^:(]+)/);
            const catMatch = rawVal.match(/\(([^;)]+)/);
            const name = nameMatch ? nameMatch[1].trim() : rawVal;
            const category = catMatch ? catMatch[1].trim() : "Neural Link";
            const details = rawVal.includes(";") ? rawVal.split(";").slice(1).join(";").replace(/\)$/, "").trim() : (rawVal.includes(":") ? rawVal.split(":").slice(1).join(":").trim() : "");
            const nameInput = document.getElementById("input-cyberware-name");
            const catSelect = document.getElementById("select-cyberware-category");
            const detInput = document.getElementById("input-cyberware-details");
            if (nameInput) nameInput.value = name;
            if (catSelect) {
                for (const opt of catSelect.options) {
                    if (opt.value.toLowerCase() === category.toLowerCase() || category.toLowerCase().includes(opt.value.toLowerCase())) {
                        catSelect.value = opt.value;
                        break;
                    }
                }
            }
            if (detInput) detInput.value = details;
            updateCyberwareLivePreview();
        });
    }

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
            stagedCharacterInventory = [];
            renderStagedInventory();
            stagedCharacterCyberware = [];
            renderStagedCyberware();
            stagedCustomMeters = {};
            renderStagedCustomMeters(false);

            // Reset Module A & C (Inspiration, DC, Attunement, Spell Slots)
            const resetInsp = document.getElementById("char-inspiration-toggle");
            if (resetInsp) resetInsp.checked = false;
            const resetDc = document.getElementById("char-ability-save-dc");
            if (resetDc) resetDc.value = "13";
            const resetAttCur = document.getElementById("char-attuned-current");
            if (resetAttCur) resetAttCur.value = "0";
            const resetAttMax = document.getElementById("char-attuned-max");
            if (resetAttMax) resetAttMax.value = "3";
            if (typeof window.renderSpellSlotInputs === "function") {
                window.renderSpellSlotInputs({});
            }
            const tallyEl = document.getElementById("prep-spells-tally");
            if (tallyEl) tallyEl.textContent = "0 / 0";

            // Reset Weight, Encumbrance, Appearance, Roleplay Anchors, and Backstory/Notes
            const weightCarriedEl = document.getElementById("input-dnd-weight-carried");
            const encumberedEl = document.getElementById("input-dnd-encumbered");
            const pushDragLiftEl = document.getElementById("input-dnd-push-drag-lift");
            if (weightCarriedEl) { weightCarriedEl.value = ""; weightCarriedEl.dataset.autoCalculated = "true"; }
            if (encumberedEl) encumberedEl.value = "120 lb.";
            if (pushDragLiftEl) pushDragLiftEl.value = "240 lb.";

            [
                "input-char-gender", "input-char-age", "input-char-height",
                "input-char-weight", "input-char-alignment",
                "input-char-faith", "input-char-skin", "input-char-eyes", "input-char-hair",
                "input-char-backstory", "input-char-additional-notes",
                "rp-personality-traits", "rp-ideals", "rp-bonds", "rp-flaws", "rp-allies-orgs"
            ].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            const sizeReset = document.getElementById("input-char-size");
            if (sizeReset) sizeReset.value = "Medium";
            const typeReset = document.getElementById("input-char-creature-type");
            if (typeReset) typeReset.value = "Humanoid";

            applyCharacterModalViewMode(false);

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
                    `Select hero from party roster to save to the Character Vault:\n\nActive Heroes: ${names}`,
                    rosterChars[0].name
                );
                if (!chosenName || !chosenName.trim()) return;
                await promptVaultExportChoice(chosenName.trim());
            } catch (err) {
                console.error("[Vault] Error saving from campaign:", err);
                alert(`Error saving hero to vault: ${err.message}`);
            }
        });
    }

    // Setup background auto-refresh for notes and live combat ladder
    setupRoleplayAutoRefresh();
});

// Prompt user for Character Vault Export: Overwrite Root vs Create Branch
async function promptVaultExportChoice(charName) {
    if (!window.pywebview || !window.pywebview.api || !charName) return;

    let exists = false;
    try {
        if (window.pywebview.api.check_vault_character_exists) {
            const check = await window.pywebview.api.check_vault_character_exists(charName);
            exists = Boolean(check && check.exists);
        }
    } catch (e) {
        console.warn("[Vault] check_vault_character_exists check failed:", e);
    }

    const modal = document.getElementById("modal-vault-export-choice");
    const nameEl = document.getElementById("vault-export-char-name");
    const previewEl = document.getElementById("vault-export-branch-preview");
    const btnOverwrite = document.getElementById("btn-vault-choice-overwrite");
    const btnBranch = document.getElementById("btn-vault-choice-branch");
    const btnCancel = document.getElementById("btn-cancel-vault-export-choice");
    const btnClose = document.getElementById("btn-close-vault-export-choice");

    const campTitle = ttrpgData.active_campaign_meta?.title || ttrpgData.active_campaign || "Campaign";

    if (!modal || !btnOverwrite || !btnBranch) {
        const proceed = await showAetherConfirm(
            `Save '${charName}' permanently into the Character Vault as a Canonical Root hero?`,
            "Save to Vault",
            "Save Hero",
            false
        );
        if (!proceed) return;
        const res = await window.pywebview.api.save_campaign_character_to_vault(charName, null, "overwrite");
        if (res && res.status === "success") {
            await showAetherAlert(`Hero '${charName}' has been successfully preserved in the Character Vault!`, "Character Saved");
            await refreshVaultModalUI();
        } else {
            await showAetherAlert(res?.message || "Failed saving character to vault.", "Vault Notice");
        }
        return;
    }

    if (nameEl) nameEl.textContent = charName;
    if (previewEl) previewEl.textContent = `${campTitle} Veteran`;

    modal.classList.remove("hidden");

    return new Promise((resolve) => {
        const cleanup = () => {
            modal.classList.add("hidden");
            btnOverwrite.onclick = null;
            btnBranch.onclick = null;
            if (btnCancel) btnCancel.onclick = null;
            if (btnClose) btnClose.onclick = null;
        };

        const doSave = async (mode) => {
            cleanup();
            try {
                const res = await window.pywebview.api.save_campaign_character_to_vault(charName, null, mode);
                if (res && res.status === "success") {
                    await showAetherAlert(res.message || `Hero '${charName}' saved to Character Vault!`, "Character Saved");
                    await refreshVaultModalUI();
                } else {
                    await showAetherAlert(res?.message || "Failed saving character to vault.", "Vault Notice");
                }
            } catch (err) {
                console.error("[Vault] Error saving character:", err);
                await showAetherAlert(`Error saving character: ${err.message}`, "Vault Error");
            }
            resolve();
        };

        btnOverwrite.onclick = () => doSave("overwrite");
        btnBranch.onclick = () => doSave("branch");
        if (btnCancel) btnCancel.onclick = () => { cleanup(); resolve(); };
        if (btnClose) btnClose.onclick = () => { cleanup(); resolve(); };
    });
}
window.promptVaultExportChoice = promptVaultExportChoice;

// Standalone function for Party Roster card "Save to Vault" button
window.handleExportRosterCharToVault = async function(charName) {
    await promptVaultExportChoice(charName);
};

let pendingCrossover = null;
let activeVaultTab = "dnd_5e";

function getVaultCharacterBucket(charObj) {
    const raw = String(charObj?.ruleset || charObj?.native_system || charObj?.native_genre || "dnd_5e")
        .toLowerCase()
        .trim()
        .replace(/[\s-]/g, "_");
    const fam = RULESET_FAMILY_MAP[raw] || raw;
    if (fam === "dnd_5e" || fam === "pathfinder_2e" || fam === "call_of_cthulhu" || fam === "cyberpunk") {
        return fam;
    }
    return "other";
}

function updateVaultTabButtonsUI() {
    const bar = document.getElementById("vault-system-tabs-bar");
    if (!bar) return;
    bar.querySelectorAll(".vault-tab-btn").forEach(btn => {
        const tabKey = btn.getAttribute("data-vault-tab");
        btn.classList.toggle("active", tabKey === activeVaultTab);
    });
}

async function refreshVaultModalUI() {
    const container = document.getElementById("vault-characters-container");
    const countBadge = document.getElementById("vault-count-badge");
    const crossBanner = document.getElementById("vault-crossover-banner");
    if (!container) return;

    let chars = [];
    if (window.pywebview && window.pywebview.api && window.pywebview.api.list_vault_characters) {
        try {
            chars = await window.pywebview.api.list_vault_characters();
        } catch (e) {
            console.error("[Vault] Error listing vault characters:", e);
        }
    }

    const counts = {
        dnd_5e: 0,
        pathfinder_2e: 0,
        call_of_cthulhu: 0,
        cyberpunk: 0,
        all: chars.length
    };
    chars.forEach(c => {
        const b = getVaultCharacterBucket(c);
        if (counts[b] !== undefined) counts[b] += 1;
    });
    Object.entries(counts).forEach(([k, val]) => {
        const el = document.getElementById(`vault-tab-count-${k}`);
        if (el) el.textContent = String(val);
    });

    const filtered = activeVaultTab === "all"
        ? chars
        : chars.filter(c => getVaultCharacterBucket(c) === activeVaultTab);

    if (countBadge) {
        const tabLabel = activeVaultTab === "all" ? "ALL SYSTEMS" : getRulesetDisplayLabel(activeVaultTab).toUpperCase();
        countBadge.textContent = `${filtered.length} SHOWN IN ${tabLabel} (${chars.length} TOTAL IN VAULT)`;
    }

    if (crossBanner) {
        const activeMeta = ttrpgData.active_campaign_meta || {};
        const activeCampRuleset = activeMeta.system_preset || activeMeta.ruleset || activeMeta.genre || "dnd_5e";
        const showBanner = (activeVaultTab !== "all" && !areRulesetsCompatible(activeVaultTab, activeCampRuleset));
        crossBanner.classList.toggle("hidden", !showBanner);
    }

    updateVaultTabButtonsUI();

    if (filtered.length === 0) {
        const label = activeVaultTab === "all" ? "Vault" : getRulesetDisplayLabel(activeVaultTab);
        container.innerHTML = `
            <div style="text-align: center; color: #64748b; font-size: 12px; padding: 24px 8px;">
                No ${escapeRpHtml(label)} characters in the vault yet.<br>Click <strong>➕ New Hero</strong>, <strong>📄 Upload Sheet</strong>, or switch tabs above.
            </div>
        `;
        return;
    }

    container.innerHTML = filtered.map(c => {
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
                    <button type="button" class="cyber-btn-small btn-vault-view" data-char-id="${escapeRpHtml(c.id)}" style="background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.4);" title="View character sheet">
                        👁️ View
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
    container.querySelectorAll(".btn-vault-view").forEach(btn => {
        btn.addEventListener("click", () => handleOpenViewCharacterModal(btn.getAttribute("data-char-id"), "vault"));
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
    if (!window.pywebview || !window.pywebview.api || !charId) return;
    await openLevelUpWizard(charId, "vault");
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
        const activeMeta = ttrpgData.active_campaign_meta || {};
        const rawCampRuleset = String(activeMeta.system_preset || activeMeta.ruleset || activeMeta.genre || "dnd_5e")
            .toLowerCase()
            .trim()
            .replace(/[\s-]/g, "_");
        const fam = RULESET_FAMILY_MAP[rawCampRuleset] || rawCampRuleset;
        activeVaultTab = (fam === "dnd_5e" || fam === "pathfinder_2e" || fam === "call_of_cthulhu" || fam === "cyberpunk")
            ? fam
            : "all";
        updateVaultTabButtonsUI();
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

  // Spell Slot Pip Toggle
  if (e.target && e.target.classList && e.target.classList.contains('slot-pip')) {
    const charId = e.target.getAttribute('data-char-id');
    const level = parseInt(e.target.getAttribute('data-level'), 10);
    const pipIdx = parseInt(e.target.getAttribute('data-index'), 10);
    const isExpended = e.target.classList.contains('expended');

    // If clicking an active pip -> expend it (current - 1)
    // If clicking an expended pip -> recover it (current + 1)
    const delta = isExpended ? 1 : -1;

    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.adjust_character_spell_slot === "function") {
      window.pywebview.api.adjust_character_spell_slot(charId, level, delta);
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

// =========================================================================
// Phase 2: BYOC (Build Your Own Campaign) & World State Ledger UI
// =========================================================================

function getDefaultByocSeed() {
    return {
        genre: "heroic_fantasy",
        tone_sliders: { grittiness: 5, magic_tech_level: 7, political_intrigue: 5 },
        locations: [
            {
                id: "loc_durahns_gate",
                name: "Durahn's Gate",
                type: "dungeon",
                description: "Ancient stone fortress gate guarding the underground reaches.",
                hazards: ["Freezing Cold (DC 10 CON)"]
            }
        ],
        factions: [
            {
                id: "fac_iron_consortium",
                name: "The Iron Consortium",
                disposition: "neutral",
                influence_score: 60,
                agenda: "Control deep mining operations and ancient relics.",
                public_reputation: "Disciplined dwarven trade guild."
            }
        ],
        quests: [
            {
                id: "quest_silent_outpost",
                title: "The Silent Outpost",
                giver_npc: "Elder Morvin",
                summary: "Investigate why the northern outpost went dark last week.",
                objectives: [
                    { id: "obj_1", description: "Reach the perimeter watchtower", is_completed: false, is_mandatory: true }
                ]
            }
        ],
        clues: [
            {
                id: "clue_sealed_orders",
                title: "Sealed Iron Orders",
                content: "Orders bearing the seal of the Grand Marshal hinting at a deeper subterranean breach.",
                is_revealed: false
            }
        ]
    };
}

let tempByocState = getDefaultByocSeed();
let wbPendingHazards = [];
let wbPendingObjectives = [];

function updateByocSummaryBadge() {
    const el = document.getElementById("byoc-config-summary");
    if (!el) return;
    const locs = (tempByocState.locations || []).length;
    const facs = (tempByocState.factions || []).length;
    const quests = (tempByocState.quests || []).length;
    const clues = (tempByocState.clues || []).length;
    if (locs === 1 && facs === 1 && quests === 1 && clues === 1) {
        el.textContent = "Custom World: Default Template";
    } else {
        el.textContent = `Custom World: ${locs} Loc, ${facs} Faction, ${quests} Quest, ${clues} Clue`;
    }
}

function renderWbPendingHazards() {
    const container = document.getElementById("wb-loc-hazards-tags");
    if (!container) return;
    container.innerHTML = wbPendingHazards.map((h, idx) => `
        <span class="wb-hazard-tag">
            <span>⚠️ ${escapeRpHtml(h)}</span>
            <span class="wb-tag-remove" onclick="window.removeWbPendingHazard(${idx})">✕</span>
        </span>
    `).join("");
}

window.removeWbPendingHazard = (idx) => {
    wbPendingHazards.splice(idx, 1);
    renderWbPendingHazards();
};

function renderWbPendingObjectives() {
    const container = document.getElementById("wb-quest-objs-list");
    if (!container) return;
    container.innerHTML = wbPendingObjectives.map((obj, idx) => `
        <div class="wb-obj-item-row">
            <span>🔹 ${escapeRpHtml(obj)}</span>
            <span class="wb-obj-remove" onclick="window.removeWbPendingObjective(${idx})">✕</span>
        </div>
    `).join("");
}

window.removeWbPendingObjective = (idx) => {
    wbPendingObjectives.splice(idx, 1);
    renderWbPendingObjectives();
};

function renderWbStagedLocations() {
    const container = document.getElementById("wb-staged-locs-list");
    if (!container) return;
    const locs = tempByocState.locations || [];
    if (locs.length === 0) {
        container.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No locations configured yet.</span>`;
        return;
    }
    container.innerHTML = locs.map((loc, idx) => `
        <div class="byoc-item-card" style="padding: 8px 10px;">
            <div class="byoc-item-header">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(loc.name)}</span>
                    <span class="system-tag" style="font-size: 0.6rem; padding: 1px 4px;">${escapeRpHtml((loc.type || "node").toUpperCase())}</span>
                </div>
                <button type="button" class="action-btn" style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);" onclick="window.removeWbStagedLocation(${idx})">✕</button>
            </div>
            ${loc.description ? `<div style="font-size: 0.7rem; color: #94a3b8; line-height: 1.25; margin-top: 3px;">${escapeRpHtml(loc.description)}</div>` : ""}
            ${loc.hazards && loc.hazards.length ? `<div style="font-size: 0.68rem; color: #f87171; margin-top: 2px;">⚠️ ${escapeRpHtml(Array.isArray(loc.hazards) ? loc.hazards.join(", ") : loc.hazards)}</div>` : ""}
        </div>
    `).join("");
}

window.removeWbStagedLocation = (idx) => {
    tempByocState.locations.splice(idx, 1);
    renderWbStagedLocations();
    updateByocSummaryBadge();
};

function renderWbStagedFactions() {
    const container = document.getElementById("wb-staged-factions-list");
    if (!container) return;
    const facs = tempByocState.factions || [];
    if (facs.length === 0) {
        container.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No factions configured yet.</span>`;
        return;
    }
    container.innerHTML = facs.map((fac, idx) => {
        const disp = (fac.disposition || "neutral").toLowerCase();
        return `
            <div class="byoc-item-card" style="padding: 8px 10px;">
                <div class="byoc-item-header">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(fac.name)}</span>
                        <span class="byoc-disp-badge byoc-disp-${disp}">${escapeRpHtml(disp)}</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 0.68rem; color: #38bdf8; font-weight: 600;">${fac.influence_score}% Inf</span>
                        <button type="button" class="action-btn" style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);" onclick="window.removeWbStagedFaction(${idx})">✕</button>
                    </div>
                </div>
                ${fac.agenda ? `<div style="font-size: 0.7rem; color: #94a3b8; margin-top: 3px;"><strong>Agenda:</strong> ${escapeRpHtml(fac.agenda)}</div>` : ""}
            </div>
        `;
    }).join("");
}

window.removeWbStagedFaction = (idx) => {
    tempByocState.factions.splice(idx, 1);
    renderWbStagedFactions();
    updateByocSummaryBadge();
};

function renderWbStagedQuests() {
    const container = document.getElementById("wb-staged-quests-list");
    if (!container) return;
    const quests = tempByocState.quests || [];
    if (quests.length === 0) {
        container.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No quests configured yet.</span>`;
        return;
    }
    container.innerHTML = quests.map((q, idx) => {
        const objs = (q.objectives || []).map(o => typeof o === 'string' ? o : o.description).join("; ");
        return `
            <div class="byoc-item-card" style="padding: 8px 10px;">
                <div class="byoc-item-header">
                    <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(q.title)}</span>
                    <button type="button" class="action-btn" style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);" onclick="window.removeWbStagedQuest(${idx})">✕</button>
                </div>
                ${q.giver_npc ? `<div style="font-size: 0.68rem; color: #64748b;">Giver: ${escapeRpHtml(q.giver_npc)}</div>` : ""}
                ${q.summary ? `<div style="font-size: 0.7rem; color: #94a3b8; margin-top: 2px;">${escapeRpHtml(q.summary)}</div>` : ""}
                ${objs ? `<div style="font-size: 0.68rem; color: #38bdf8; margin-top: 2px;">Objectives: ${escapeRpHtml(objs)}</div>` : ""}
            </div>
        `;
    }).join("");
}

window.removeWbStagedQuest = (idx) => {
    tempByocState.quests.splice(idx, 1);
    renderWbStagedQuests();
    updateByocSummaryBadge();
};

function renderWbStagedClues() {
    const container = document.getElementById("wb-staged-clues-list");
    if (!container) return;
    const clues = tempByocState.clues || [];
    if (clues.length === 0) {
        container.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No clues configured yet.</span>`;
        return;
    }
    container.innerHTML = clues.map((c, idx) => `
        <div class="byoc-item-card" style="padding: 8px 10px; border-left: 3px solid ${c.is_revealed ? '#38bdf8' : '#f59e0b'};">
            <div class="byoc-item-header">
                <span style="font-weight: 600; font-size: 0.78rem; color: ${c.is_revealed ? '#38bdf8' : '#f59e0b'};">
                    ${c.is_revealed ? '📜' : '🔒'} ${escapeRpHtml(c.title)}
                </span>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span class="system-tag" style="font-size: 0.6rem;">${c.is_revealed ? 'REVEALED' : 'GM SECRET'}</span>
                    <button type="button" class="action-btn" style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);" onclick="window.removeWbStagedClue(${idx})">✕</button>
                </div>
            </div>
            ${c.content ? `<div style="font-size: 0.7rem; color: #94a3b8; margin-top: 2px;">${escapeRpHtml(c.content)}</div>` : ""}
        </div>
    `).join("");
}

window.removeWbStagedClue = (idx) => {
    tempByocState.clues.splice(idx, 1);
    renderWbStagedClues();
    updateByocSummaryBadge();
};

function hydrateWorldBuilderModal() {
    renderWbPendingHazards();
    renderWbPendingObjectives();
    renderWbStagedLocations();
    renderWbStagedFactions();
    renderWbStagedQuests();
    renderWbStagedClues();
}

function initByocCreationModal() {
    // 1. Universal Tone Sliders in modal-new-campaign
    const sliderGrittiness = document.getElementById("campaign-tone-grittiness");
    const labelGrittiness = document.getElementById("val-tone-grittiness");
    if (sliderGrittiness && labelGrittiness) {
        sliderGrittiness.addEventListener("input", (e) => {
            labelGrittiness.textContent = `${e.target.value}/10`;
        });
    }

    const sliderMagic = document.getElementById("campaign-tone-magic");
    const labelMagic = document.getElementById("val-tone-magic");
    if (sliderMagic && labelMagic) {
        sliderMagic.addEventListener("input", (e) => {
            labelMagic.textContent = `${e.target.value}/10`;
        });
    }

    const sliderIntrigue = document.getElementById("campaign-tone-intrigue");
    const labelIntrigue = document.getElementById("val-tone-intrigue");
    if (sliderIntrigue && labelIntrigue) {
        sliderIntrigue.addEventListener("input", (e) => {
            labelIntrigue.textContent = `${e.target.value}/10`;
        });
    }

    // 1b. Module Story Adherence & GM Pacing Slider (3 Steps)
    const sliderAdherence = document.getElementById("campaign-module-adherence");
    const labelAdherence = document.getElementById("val-module-adherence");
    const descAdherence = document.getElementById("desc-module-adherence");

    const adherenceDescriptions = {
        1: {
            label: "Creative Sandbox",
            desc: "Treats module as an inspirational world guide and NPC bible. Aether is free to improvise encounters, remix scenes, and follow player-led side ventures.",
            key: "creative"
        },
        2: {
            label: "Guided / Balanced",
            desc: "Preserves key act milestones, major story beats, and boss fights, while accommodating creative player detours and adapting scene order organically.",
            key: "balanced"
        },
        3: {
            label: "Strict Module (By the Book)",
            desc: "Follows published acts, scenes, boxed text, and mandatory encounters sequentially by the book. High rolls grant immediate scene insight, never skipping intermediate encounters.",
            key: "strict"
        }
    };

    window._updateAdherenceDisplay = function(val) {
        const num = parseInt(val, 10) || 3;
        const info = adherenceDescriptions[num] || adherenceDescriptions[3];
        if (labelAdherence) labelAdherence.textContent = info.label;
        if (descAdherence) descAdherence.textContent = info.desc;
    };

    if (sliderAdherence) {
        sliderAdherence.addEventListener("input", (e) => {
            if (window._updateAdherenceDisplay) window._updateAdherenceDisplay(e.target.value);
        });
    }

    // 2. Segmented source selector: Module Upload vs Build Your Own World
    const btnSrcModule = document.getElementById("btn-src-module");
    const btnSrcByoc = document.getElementById("btn-src-byoc");
    const secSrcModule = document.getElementById("section-src-module");
    const secSrcByoc = document.getElementById("section-src-byoc");

    if (btnSrcModule && btnSrcByoc && secSrcModule && secSrcByoc) {
        btnSrcModule.addEventListener("click", () => {
            btnSrcModule.classList.add("active");
            btnSrcByoc.classList.remove("active");
            secSrcModule.classList.remove("hidden");
            secSrcByoc.classList.add("hidden");
        });
        btnSrcByoc.addEventListener("click", () => {
            btnSrcByoc.classList.add("active");
            btnSrcModule.classList.remove("active");
            secSrcByoc.classList.remove("hidden");
            secSrcModule.classList.add("hidden");
        });
    }

    // 3. Open dedicated World Builder Sub-Modal
    const openWbBtn = document.getElementById("btn-open-world-builder");
    const wbModal = document.getElementById("modal-world-builder");
    const closeWbBtn = document.getElementById("btn-close-world-builder");
    const cancelWbBtn = document.getElementById("btn-wb-cancel");
    const applyWbBtn = document.getElementById("btn-wb-apply");
    const revertWbBtn = document.getElementById("btn-wb-revert");

    if (openWbBtn && wbModal) {
        openWbBtn.addEventListener("click", () => {
            hydrateWorldBuilderModal();
            wbModal.classList.remove("hidden");
        });
    }
    if (closeWbBtn && wbModal) closeWbBtn.addEventListener("click", () => wbModal.classList.add("hidden"));
    if (cancelWbBtn && wbModal) cancelWbBtn.addEventListener("click", () => wbModal.classList.add("hidden"));

    // 4. Tab switching within World Builder Studio
    const tabBtns = document.querySelectorAll(".wb-tab-btn");
    const tabPanes = {
        location: document.getElementById("wb-pane-location"),
        factions: document.getElementById("wb-pane-factions"),
        quests: document.getElementById("wb-pane-quests"),
        clues: document.getElementById("wb-pane-clues")
    };
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            tabBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const targetTab = btn.getAttribute("data-wb-tab");
            Object.keys(tabPanes).forEach(k => {
                if (tabPanes[k]) {
                    if (k === targetTab) tabPanes[k].classList.remove("hidden");
                    else tabPanes[k].classList.add("hidden");
                }
            });
        });
    });

    // 5. Tab 1: Hazards tag addition & Location staging
    const addHazBtn = document.getElementById("btn-wb-add-hazard");
    const hazInput = document.getElementById("wb-loc-hazard-input");
    if (addHazBtn && hazInput) {
        const handleAddHazard = () => {
            const val = hazInput.value.trim();
            if (val) {
                wbPendingHazards.push(val);
                hazInput.value = "";
                renderWbPendingHazards();
            }
        };
        addHazBtn.addEventListener("click", handleAddHazard);
        hazInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); handleAddHazard(); }
        });
    }

    const stageLocBtn = document.getElementById("btn-wb-stage-loc");
    if (stageLocBtn) {
        stageLocBtn.addEventListener("click", () => {
            const name = (document.getElementById("wb-loc-name")?.value || "").trim();
            if (!name) return;
            const type = document.getElementById("wb-loc-type")?.value || "dungeon";
            const desc = (document.getElementById("wb-loc-desc")?.value || "").trim();
            tempByocState.locations.push({
                name,
                type,
                description: desc,
                hazards: [...wbPendingHazards]
            });
            document.getElementById("wb-loc-name").value = "";
            document.getElementById("wb-loc-desc").value = "";
            wbPendingHazards = [];
            renderWbPendingHazards();
            renderWbStagedLocations();
            updateByocSummaryBadge();
        });
    }

    // 6. Tab 2: Faction influence slider & Faction staging
    const facSlider = document.getElementById("wb-fac-influence");
    const valFacInf = document.getElementById("val-wb-fac-influence");
    if (facSlider && valFacInf) {
        facSlider.addEventListener("input", (e) => {
            valFacInf.textContent = `${e.target.value}%`;
        });
    }

    const stageFacBtn = document.getElementById("btn-wb-stage-fac");
    if (stageFacBtn) {
        stageFacBtn.addEventListener("click", () => {
            const name = (document.getElementById("wb-fac-name")?.value || "").trim();
            if (!name) return;
            const disposition = document.getElementById("wb-fac-disp")?.value || "neutral";
            const influence = parseInt(document.getElementById("wb-fac-influence")?.value || "50", 10);
            const agenda = (document.getElementById("wb-fac-agenda")?.value || "").trim();
            const rep = (document.getElementById("wb-fac-rep")?.value || "").trim();
            tempByocState.factions.push({
                name,
                disposition,
                influence_score: influence,
                agenda,
                public_reputation: rep
            });
            document.getElementById("wb-fac-name").value = "";
            document.getElementById("wb-fac-agenda").value = "";
            document.getElementById("wb-fac-rep").value = "";
            renderWbStagedFactions();
            updateByocSummaryBadge();
        });
    }

    // 7. Tab 3: Objective addition & Quest staging
    const addObjBtn = document.getElementById("btn-wb-add-obj");
    const objInput = document.getElementById("wb-quest-obj-input");
    if (addObjBtn && objInput) {
        const handleAddObj = () => {
            const val = objInput.value.trim();
            if (val) {
                wbPendingObjectives.push(val);
                objInput.value = "";
                renderWbPendingObjectives();
            }
        };
        addObjBtn.addEventListener("click", handleAddObj);
        objInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") { e.preventDefault(); handleAddObj(); }
        });
    }

    const stageQuestBtn = document.getElementById("btn-wb-stage-quest");
    if (stageQuestBtn) {
        stageQuestBtn.addEventListener("click", () => {
            const title = (document.getElementById("wb-quest-title")?.value || "").trim();
            if (!title) return;
            const giver = (document.getElementById("wb-quest-giver")?.value || "").trim();
            const summary = (document.getElementById("wb-quest-summary")?.value || "").trim();
            const objectives = wbPendingObjectives.map((desc, idx) => ({
                id: `obj_${idx + 1}`,
                description: desc,
                is_completed: false,
                is_mandatory: true
            }));
            tempByocState.quests.push({
                title,
                giver_npc: giver,
                summary,
                objectives
            });
            document.getElementById("wb-quest-title").value = "";
            document.getElementById("wb-quest-giver").value = "";
            document.getElementById("wb-quest-summary").value = "";
            wbPendingObjectives = [];
            renderWbPendingObjectives();
            renderWbStagedQuests();
            updateByocSummaryBadge();
        });
    }

    // 8. Tab 4: Clue staging
    const stageClueBtn = document.getElementById("btn-wb-stage-clue");
    if (stageClueBtn) {
        stageClueBtn.addEventListener("click", () => {
            const title = (document.getElementById("wb-clue-title")?.value || "").trim();
            const content = (document.getElementById("wb-clue-content")?.value || "").trim();
            if (!title || !content) return;
            const isRevealed = document.getElementById("wb-clue-revealed")?.checked || false;
            tempByocState.clues.push({
                title,
                content,
                is_revealed: isRevealed
            });
            document.getElementById("wb-clue-title").value = "";
            document.getElementById("wb-clue-content").value = "";
            document.getElementById("wb-clue-revealed").checked = false;
            renderWbStagedClues();
            updateByocSummaryBadge();
        });
    }

    // 9. Revert to Defaults
    if (revertWbBtn) {
        revertWbBtn.addEventListener("click", () => {
            tempByocState = getDefaultByocSeed();
            wbPendingHazards = [];
            wbPendingObjectives = [];
            hydrateWorldBuilderModal();
            updateByocSummaryBadge();
        });
    }

    // 10. Apply World Seed
    if (applyWbBtn && wbModal) {
        applyWbBtn.addEventListener("click", () => {
            // Auto-stage any pending inputs in the active tab
            const locName = (document.getElementById("wb-loc-name")?.value || "").trim();
            if (locName) {
                tempByocState.locations.push({
                    name: locName,
                    type: document.getElementById("wb-loc-type")?.value || "dungeon",
                    description: (document.getElementById("wb-loc-desc")?.value || "").trim(),
                    hazards: [...wbPendingHazards]
                });
                document.getElementById("wb-loc-name").value = "";
                document.getElementById("wb-loc-desc").value = "";
                wbPendingHazards = [];
            }
            const facName = (document.getElementById("wb-fac-name")?.value || "").trim();
            if (facName) {
                tempByocState.factions.push({
                    name: facName,
                    disposition: document.getElementById("wb-fac-disp")?.value || "neutral",
                    influence_score: parseInt(document.getElementById("wb-fac-influence")?.value || "50", 10),
                    agenda: (document.getElementById("wb-fac-agenda")?.value || "").trim(),
                    public_reputation: (document.getElementById("wb-fac-rep")?.value || "").trim()
                });
                document.getElementById("wb-fac-name").value = "";
                document.getElementById("wb-fac-agenda").value = "";
                document.getElementById("wb-fac-rep").value = "";
            }
            const questTitle = (document.getElementById("wb-quest-title")?.value || "").trim();
            if (questTitle) {
                const objectives = wbPendingObjectives.map((desc, idx) => ({
                    id: `obj_${idx + 1}`,
                    description: desc,
                    is_completed: false,
                    is_mandatory: true
                }));
                tempByocState.quests.push({
                    title: questTitle,
                    giver_npc: (document.getElementById("wb-quest-giver")?.value || "").trim(),
                    summary: (document.getElementById("wb-quest-summary")?.value || "").trim(),
                    objectives
                });
                document.getElementById("wb-quest-title").value = "";
                document.getElementById("wb-quest-giver").value = "";
                document.getElementById("wb-quest-summary").value = "";
                wbPendingObjectives = [];
            }
            const clueTitle = (document.getElementById("wb-clue-title")?.value || "").trim();
            const clueContent = (document.getElementById("wb-clue-content")?.value || "").trim();
            if (clueTitle && clueContent) {
                tempByocState.clues.push({
                    title: clueTitle,
                    content: clueContent,
                    is_revealed: document.getElementById("wb-clue-revealed")?.checked || false
                });
                document.getElementById("wb-clue-title").value = "";
                document.getElementById("wb-clue-content").value = "";
                document.getElementById("wb-clue-revealed").checked = false;
            }

            updateByocSummaryBadge();
            wbModal.classList.add("hidden");
        });
    }
}

function getByocModalData() {
    const grittiness = parseInt(document.getElementById("campaign-tone-grittiness")?.value || "5", 10);
    const magic = parseInt(document.getElementById("campaign-tone-magic")?.value || "7", 10);
    const intrigue = parseInt(document.getElementById("campaign-tone-intrigue")?.value || "5", 10);

    return {
        genre: tempByocState.genre || "heroic_fantasy",
        tone_sliders: {
            grittiness,
            magic_tech_level: magic,
            political_intrigue: intrigue
        },
        locations: tempByocState.locations || [],
        factions: tempByocState.factions || [],
        quests: tempByocState.quests || [],
        clues: tempByocState.clues || []
    };
}

async function renderWorldStateLedger() {
    const worldPane = document.getElementById("rp-world-pane");
    if (!worldPane) return;

    let worldState = ttrpgData.world_state;
    let worldContext = ttrpgData.world_context || "";

    if ((!worldState || !worldState.title) && window.pywebview && window.pywebview.api && window.pywebview.api.get_ttrpg_world_state) {
        try {
            const res = await window.pywebview.api.get_ttrpg_world_state();
            if (res && res.status === "success") {
                worldState = res.manifest;
                worldContext = res.context || "";
                ttrpgData.world_state = worldState;
                ttrpgData.world_context = worldContext;
            }
        } catch (e) {
            console.error("[RolePlay] Failed to query world state:", e);
        }
    }

    if (!worldState) {
        const meta = ttrpgData.active_campaign_meta || {};
        worldState = {
            title: meta.title || "Active Campaign",
            ruleset: meta.system_preset || "dnd_5e",
            genre: "heroic_fantasy",
            tone_sliders: { grittiness: 5, magic_tech_level: 7, political_intrigue: 5 },
            premise: meta.setting_overview || "No premise recorded yet.",
            locations: {},
            factions: {},
            quests: {},
            clues: {}
        };
    }

    // Header Banner
    const titleEl = document.getElementById("byoc-campaign-title");
    if (titleEl) titleEl.textContent = worldState.title || "World State & Narrative Anchors";

    const rulesetBadge = document.getElementById("byoc-ruleset-badge");
    if (rulesetBadge) rulesetBadge.textContent = (worldState.ruleset || "dnd_5e").toUpperCase().replace("_", " ");

    const genreBadge = document.getElementById("byoc-genre-badge");
    if (genreBadge) genreBadge.textContent = (worldState.genre || "heroic_fantasy").toUpperCase().replace("_", " ");

    const premiseEl = document.getElementById("byoc-campaign-premise");
    if (premiseEl) premiseEl.textContent = worldState.premise || "Ruleset-agnostic world ledger tracking geography, factions, quests, and secret clues.";

    const sliders = worldState.tone_sliders || { grittiness: 5, magic_tech_level: 7, political_intrigue: 5 };
    const pillG = document.getElementById("byoc-pill-grittiness");
    if (pillG) pillG.textContent = `${sliders.grittiness || 5}/10`;
    const pillM = document.getElementById("byoc-pill-magic");
    if (pillM) pillM.textContent = `${sliders.magic_tech_level || 7}/10`;
    const pillI = document.getElementById("byoc-pill-intrigue");
    if (pillI) pillI.textContent = `${sliders.political_intrigue || 5}/10`;

    // 1. Locations
    renderByocLocations(worldState);

    // 2. Factions
    renderByocFactions(worldState);

    // 3. Quests
    renderByocQuests(worldState);

    // 4. Clues
    renderByocClues(worldState);

    // 5. Context injection preview
    const preEl = document.getElementById("byoc-context-pre");
    if (preEl) {
        preEl.textContent = worldContext || "GM context assembled dynamically.";
    }
}

function renderByocLocations(worldState) {
    const activeBox = document.getElementById("byoc-active-location-card");
    const listEl = document.getElementById("byoc-location-list");
    if (!activeBox || !listEl) return;

    const locations = worldState.locations || {};
    const locEntries = Object.values(locations);
    const activeId = worldState.active_location_id;
    const activeLoc = activeId && locations[activeId] ? locations[activeId] : (locEntries[0] || null);

    if (activeLoc) {
        const hazardBadges = (activeLoc.hazards || []).map(h =>
            `<span class="byoc-disp-badge byoc-disp-hostile" style="font-size: 0.65rem;">⚠️ ${escapeRpHtml(h)}</span>`
        ).join(" ");

        const connectionBtns = (activeLoc.connected_location_ids || []).map(cid => {
            const dest = locations[cid];
            const name = dest ? dest.name : cid;
            return `<button type="button" class="action-btn" onclick="window.handleByocNavigateLocation('${cid}')">Travel to ${escapeRpHtml(name)} ▶</button>`;
        }).join(" ");

        activeBox.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="color: #38bdf8; font-weight: 700; font-size: 0.95rem;">${escapeRpHtml(activeLoc.name)}</span>
                    <span class="system-tag" style="font-size: 0.62rem;">${escapeRpHtml((activeLoc.type || "region").toUpperCase())}</span>
                </div>
                <span style="font-size: 0.68rem; color: #4ade80; font-weight: 600;">📍 ACTIVE POSITION</span>
            </div>
            <p style="margin: 2px 0 0 0; font-size: 0.74rem; color: #cbd5e1; line-height: 1.35;">${escapeRpHtml(activeLoc.description || "No description provided.")}</p>
            ${hazardBadges ? `<div style="display: flex; gap: 4px; flex-wrap: wrap; margin-top: 3px;">${hazardBadges}</div>` : ""}
            ${connectionBtns ? `<div style="display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; align-items: center;"><span style="font-size: 0.68rem; color: #94a3b8;">Pathways:</span>${connectionBtns}</div>` : ""}
        `;
    } else {
        activeBox.innerHTML = `<span style="font-size: 0.74rem; color: #64748b;">No active location. Click "+ Add Node" to establish your starting location.</span>`;
    }

    if (locEntries.length === 0) {
        listEl.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No locations recorded yet.</span>`;
        return;
    }

    listEl.innerHTML = locEntries.map(loc => {
        const isActive = activeLoc && activeLoc.id === loc.id;
        return `
            <div class="byoc-item-card" style="${isActive ? 'border-color: #0ea5e9;' : ''}">
                <div class="byoc-item-header">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(loc.name)}</span>
                        <span class="system-tag" style="font-size: 0.6rem; padding: 1px 4px;">${escapeRpHtml((loc.type || "node").toUpperCase())}</span>
                    </div>
                    ${isActive ? `<span style="font-size: 0.65rem; color: #38bdf8; font-weight: 600;">CURRENT</span>` :
                        `<button type="button" class="action-btn" onclick="window.handleByocNavigateLocation('${loc.id}')">Travel Here</button>`}
                </div>
                ${loc.description ? `<div style="font-size: 0.7rem; color: #94a3b8; line-height: 1.25;">${escapeRpHtml(loc.description)}</div>` : ""}
                ${loc.hazards && loc.hazards.length ? `<div style="font-size: 0.68rem; color: #f87171;">⚠️ ${escapeRpHtml(loc.hazards.join(", "))}</div>` : ""}
            </div>
        `;
    }).join("");
}

function renderByocFactions(worldState) {
    const listEl = document.getElementById("byoc-faction-list");
    if (!listEl) return;

    const factions = Object.values(worldState.factions || {});
    if (factions.length === 0) {
        listEl.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No regional factions defined. Click "+ Add Faction" to create one.</span>`;
        return;
    }

    listEl.innerHTML = factions.map(fac => {
        const disp = (fac.disposition || "neutral").toLowerCase();
        return `
            <div class="byoc-item-card">
                <div class="byoc-item-header">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(fac.name)}</span>
                        <span class="byoc-disp-badge byoc-disp-${disp}">${escapeRpHtml(disp)}</span>
                    </div>
                    <span style="font-size: 0.7rem; color: #38bdf8; font-weight: 600;">${fac.influence_score}% Inf</span>
                </div>
                <div class="byoc-influence-bar-bg">
                    <div class="byoc-influence-fill" style="width: ${Math.max(0, Math.min(100, fac.influence_score))}%;"></div>
                </div>
                ${fac.agenda ? `<div style="font-size: 0.7rem; color: #94a3b8; margin-top: 2px;"><strong>Agenda:</strong> ${escapeRpHtml(fac.agenda)}</div>` : ""}
                ${fac.public_reputation ? `<div style="font-size: 0.68rem; color: #64748b;">${escapeRpHtml(fac.public_reputation)}</div>` : ""}
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
                    <select style="background: #0f172a; border: 1px solid #334155; color: #cbd5e1; font-size: 0.68rem; border-radius: 4px; padding: 2px 4px;"
                            onchange="window.handleByocChangeFactionDisp('${fac.id}', this.value)">
                        <option value="allied" ${disp === 'allied' ? 'selected' : ''}>Allied</option>
                        <option value="friendly" ${disp === 'friendly' ? 'selected' : ''}>Friendly</option>
                        <option value="neutral" ${disp === 'neutral' ? 'selected' : ''}>Neutral</option>
                        <option value="suspicious" ${disp === 'suspicious' ? 'selected' : ''}>Suspicious</option>
                        <option value="hostile" ${disp === 'hostile' ? 'selected' : ''}>Hostile</option>
                    </select>
                    <div style="display: flex; gap: 4px;">
                        <button type="button" class="action-btn" onclick="window.handleByocAdjustFaction('${fac.id}', -5)">-5</button>
                        <button type="button" class="action-btn" onclick="window.handleByocAdjustFaction('${fac.id}', 5)">+5</button>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

function renderByocQuests(worldState) {
    const listEl = document.getElementById("byoc-quest-list");
    if (!listEl) return;

    const quests = Object.values(worldState.quests || {});
    if (quests.length === 0) {
        listEl.innerHTML = `<span style="font-size: 0.72rem; color: #64748b; font-style: italic;">No quests logged. Click "+ Add Quest" to add an adventure objective.</span>`;
        return;
    }

    listEl.innerHTML = quests.map(q => {
        const isDone = q.status === "completed";
        const isFail = q.status === "failed";
        const badgeClass = isDone ? "byoc-disp-friendly" : (isFail ? "byoc-disp-hostile" : "byoc-disp-neutral");

        const objsHtml = (q.objectives || []).map(obj => `
            <label class="byoc-obj-item ${obj.is_completed ? 'completed' : ''}" style="cursor: pointer; user-select: none;">
                <input type="checkbox" ${obj.is_completed ? 'checked' : ''} style="accent-color: #0ea5e9;"
                       onchange="window.handleByocCompleteObjective('${q.id}', '${obj.id}')">
                <span>${escapeRpHtml(obj.description)}</span>
            </label>
        `).join("");

        return `
            <div class="byoc-item-card">
                <div class="byoc-item-header">
                    <span style="font-weight: 600; font-size: 0.78rem; color: #f1f5f9;">${escapeRpHtml(q.title)}</span>
                    <span class="byoc-disp-badge ${badgeClass}">${escapeRpHtml(q.status.toUpperCase())}</span>
                </div>
                ${q.giver_npc ? `<div style="font-size: 0.68rem; color: #64748b;">Given by: ${escapeRpHtml(q.giver_npc)}</div>` : ""}
                ${q.summary ? `<div style="font-size: 0.7rem; color: #94a3b8; line-height: 1.25;">${escapeRpHtml(q.summary)}</div>` : ""}
                <div style="display: flex; flex-direction: column; gap: 4px; margin-top: 4px;">
                    ${objsHtml}
                </div>
            </div>
        `;
    }).join("");
}

function renderByocClues(worldState) {
    const listEl = document.getElementById("byoc-clue-list");
    if (!listEl) return;

    const clues = Object.values(worldState.clues || {});
    const hiddenClues = clues.filter(c => !c.is_revealed);
    const revealedClues = clues.filter(c => c.is_revealed);

    const secretBanner = `
        <div style="background: rgba(30, 41, 59, 0.6); border: 1px dashed rgba(148, 163, 184, 0.25); border-radius: 6px; padding: 6px 10px; font-size: 0.72rem; color: #94a3b8; display: flex; justify-content: space-between; align-items: center;">
            <span>🔒 Secret GM Partition: <strong>${hiddenClues.length}</strong> hidden</span>
            <span style="font-size: 0.65rem; color: #f59e0b;">Kept secret from player prompt</span>
        </div>
    `;

    const revealedHtml = revealedClues.map(c => `
        <div class="byoc-item-card" style="border-left: 3px solid #38bdf8;">
            <div class="byoc-item-header">
                <span style="font-weight: 600; font-size: 0.78rem; color: #38bdf8;">${escapeRpHtml(c.title)}</span>
                <span class="byoc-disp-badge byoc-disp-friendly" style="font-size: 0.6rem;">DISCOVERED</span>
            </div>
            <div style="font-size: 0.72rem; color: #cbd5e1; line-height: 1.3;">${escapeRpHtml(c.content)}</div>
        </div>
    `).join("");

    const hiddenHtml = hiddenClues.map(c => `
        <div class="byoc-item-card" style="border-left: 3px solid #f59e0b; background: rgba(22, 19, 15, 0.7);">
            <div class="byoc-item-header">
                <span style="font-weight: 600; font-size: 0.78rem; color: #f59e0b;">🔒 ${escapeRpHtml(c.title)}</span>
                <button type="button" class="action-btn" onclick="window.handleByocRevealClue('${c.id}')">Reveal to Party</button>
            </div>
            <div style="font-size: 0.7rem; color: #94a3b8; line-height: 1.25; font-style: italic;">${escapeRpHtml(c.content)}</div>
        </div>
    `).join("");

    listEl.innerHTML = secretBanner + (revealedHtml || "") + (hiddenHtml || "");
}

// Global window action handlers for BYOC
window.handleByocNavigateLocation = async (locId) => {
    if (!locId) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_set_active_location) {
            await window.pywebview.api.byoc_set_active_location(locId);
            ttrpgData.world_state = null;
            await renderWorldStateLedger();
        }
    } catch (e) {
        console.error("[BYOC] Navigation error:", e);
    }
};

window.handleByocChangeFactionDisp = async (facId, disp) => {
    if (!facId) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_adjust_faction) {
            await window.pywebview.api.byoc_adjust_faction(facId, 0, disp);
            ttrpgData.world_state = null;
            await renderWorldStateLedger();
        }
    } catch (e) {
        console.error("[BYOC] Faction disp error:", e);
    }
};

window.handleByocAdjustFaction = async (facId, delta) => {
    if (!facId) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_adjust_faction) {
            await window.pywebview.api.byoc_adjust_faction(facId, delta, null);
            ttrpgData.world_state = null;
            await renderWorldStateLedger();
        }
    } catch (e) {
        console.error("[BYOC] Faction adjust error:", e);
    }
};

window.handleByocCompleteObjective = async (questId, objId) => {
    if (!questId || !objId) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_complete_objective) {
            await window.pywebview.api.byoc_complete_objective(questId, objId);
            ttrpgData.world_state = null;
            await renderWorldStateLedger();
        }
    } catch (e) {
        console.error("[BYOC] Objective completion error:", e);
    }
};

window.handleByocRevealClue = async (clueId) => {
    if (!clueId) return;
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_reveal_clue) {
            await window.pywebview.api.byoc_reveal_clue(clueId);
            ttrpgData.world_state = null;
            await renderWorldStateLedger();
        }
    } catch (e) {
        console.error("[BYOC] Clue reveal error:", e);
    }
};

function initByocWorldModals() {
    initByocCreationModal();

    // Context toggle
    const toggleCtxBtn = document.getElementById("btn-toggle-context-preview");
    const ctxBody = document.getElementById("byoc-context-body");
    const ctxArrow = document.getElementById("byoc-context-arrow");
    if (toggleCtxBtn && ctxBody) {
        toggleCtxBtn.addEventListener("click", () => {
            const isHidden = ctxBody.classList.contains("hidden");
            if (isHidden) {
                ctxBody.classList.remove("hidden");
                if (ctxArrow) ctxArrow.textContent = "▲ Click to collapse";
            } else {
                ctxBody.classList.add("hidden");
                if (ctxArrow) ctxArrow.textContent = "▼ Click to expand";
            }
        });
    }

    // Modal: Add Location
    const openLocBtn = document.getElementById("btn-byoc-open-add-loc");
    const locModal = document.getElementById("modal-byoc-add-loc");
    const closeLocBtn = document.getElementById("btn-close-byoc-loc-modal");
    const cancelLocBtn = document.getElementById("btn-cancel-byoc-loc");
    const confirmLocBtn = document.getElementById("btn-confirm-byoc-loc");

    if (openLocBtn && locModal) {
        openLocBtn.addEventListener("click", () => {
            document.getElementById("modal-loc-name").value = "";
            document.getElementById("modal-loc-desc").value = "";
            document.getElementById("modal-loc-hazards").value = "";
            locModal.classList.remove("hidden");
        });
    }
    if (closeLocBtn && locModal) closeLocBtn.addEventListener("click", () => locModal.classList.add("hidden"));
    if (cancelLocBtn && locModal) cancelLocBtn.addEventListener("click", () => locModal.classList.add("hidden"));
    if (confirmLocBtn && locModal) {
        confirmLocBtn.addEventListener("click", async () => {
            const name = (document.getElementById("modal-loc-name")?.value || "").trim();
            if (!name) return;
            const type = document.getElementById("modal-loc-type")?.value || "dungeon";
            const desc = (document.getElementById("modal-loc-desc")?.value || "").trim();
            const hazards = (document.getElementById("modal-loc-hazards")?.value || "").trim();
            if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_add_location) {
                await window.pywebview.api.byoc_add_location({ name, type, description: desc, hazards });
                locModal.classList.add("hidden");
                ttrpgData.world_state = null;
                await renderWorldStateLedger();
            }
        });
    }

    // Modal: Add Faction
    const openFacBtn = document.getElementById("btn-byoc-open-add-fac");
    const facModal = document.getElementById("modal-byoc-add-fac");
    const closeFacBtn = document.getElementById("btn-close-byoc-fac-modal");
    const cancelFacBtn = document.getElementById("btn-cancel-byoc-fac");
    const confirmFacBtn = document.getElementById("btn-confirm-byoc-fac");

    if (openFacBtn && facModal) {
        openFacBtn.addEventListener("click", () => {
            document.getElementById("modal-fac-name").value = "";
            document.getElementById("modal-fac-agenda").value = "";
            document.getElementById("modal-fac-rep").value = "";
            facModal.classList.remove("hidden");
        });
    }
    if (closeFacBtn && facModal) closeFacBtn.addEventListener("click", () => facModal.classList.add("hidden"));
    if (cancelFacBtn && facModal) cancelFacBtn.addEventListener("click", () => facModal.classList.add("hidden"));
    if (confirmFacBtn && facModal) {
        confirmFacBtn.addEventListener("click", async () => {
            const name = (document.getElementById("modal-fac-name")?.value || "").trim();
            if (!name) return;
            const disposition = document.getElementById("modal-fac-disp")?.value || "neutral";
            const influence = parseInt(document.getElementById("modal-fac-influence")?.value || "50", 10);
            const agenda = (document.getElementById("modal-fac-agenda")?.value || "").trim();
            const rep = (document.getElementById("modal-fac-rep")?.value || "").trim();
            if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_add_faction) {
                await window.pywebview.api.byoc_add_faction({ name, disposition, influence_score: influence, agenda, public_reputation: rep });
                facModal.classList.add("hidden");
                ttrpgData.world_state = null;
                await renderWorldStateLedger();
            }
        });
    }

    // Modal: Add Quest
    const openQuestBtn = document.getElementById("btn-byoc-open-add-quest");
    const questModal = document.getElementById("modal-byoc-add-quest");
    const closeQuestBtn = document.getElementById("btn-close-byoc-quest-modal");
    const cancelQuestBtn = document.getElementById("btn-cancel-byoc-quest");
    const confirmQuestBtn = document.getElementById("btn-confirm-byoc-quest");

    if (openQuestBtn && questModal) {
        openQuestBtn.addEventListener("click", () => {
            document.getElementById("modal-quest-title").value = "";
            document.getElementById("modal-quest-giver").value = "";
            document.getElementById("modal-quest-summary").value = "";
            document.getElementById("modal-quest-obj").value = "";
            questModal.classList.remove("hidden");
        });
    }
    if (closeQuestBtn && questModal) closeQuestBtn.addEventListener("click", () => questModal.classList.add("hidden"));
    if (cancelQuestBtn && questModal) cancelQuestBtn.addEventListener("click", () => questModal.classList.add("hidden"));
    if (confirmQuestBtn && questModal) {
        confirmQuestBtn.addEventListener("click", async () => {
            const title = (document.getElementById("modal-quest-title")?.value || "").trim();
            if (!title) return;
            const giver = (document.getElementById("modal-quest-giver")?.value || "").trim();
            const summary = (document.getElementById("modal-quest-summary")?.value || "").trim();
            const obj = (document.getElementById("modal-quest-obj")?.value || "").trim();
            const objectives = obj ? [{ id: "obj_1", description: obj, is_completed: false, is_mandatory: true }] : [];
            if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_add_quest) {
                await window.pywebview.api.byoc_add_quest({ title, giver_npc: giver, summary, objectives });
                questModal.classList.add("hidden");
                ttrpgData.world_state = null;
                await renderWorldStateLedger();
            }
        });
    }

    // Modal: Add Clue
    const openClueBtn = document.getElementById("btn-byoc-open-add-clue");
    const clueModal = document.getElementById("modal-byoc-add-clue");
    const closeClueBtn = document.getElementById("btn-close-byoc-clue-modal");
    const cancelClueBtn = document.getElementById("btn-cancel-byoc-clue");
    const confirmClueBtn = document.getElementById("btn-confirm-byoc-clue");

    if (openClueBtn && clueModal) {
        openClueBtn.addEventListener("click", () => {
            document.getElementById("modal-clue-title").value = "";
            document.getElementById("modal-clue-content").value = "";
            document.getElementById("modal-clue-revealed").checked = false;
            clueModal.classList.remove("hidden");
        });
    }
    if (closeClueBtn && clueModal) closeClueBtn.addEventListener("click", () => clueModal.classList.add("hidden"));
    if (cancelClueBtn && clueModal) cancelClueBtn.addEventListener("click", () => clueModal.classList.add("hidden"));
    if (confirmClueBtn && clueModal) {
        confirmClueBtn.addEventListener("click", async () => {
            const title = (document.getElementById("modal-clue-title")?.value || "").trim();
            const content = (document.getElementById("modal-clue-content")?.value || "").trim();
            if (!title || !content) return;
            const isRevealed = document.getElementById("modal-clue-revealed")?.checked || false;
            if (window.pywebview && window.pywebview.api && window.pywebview.api.byoc_add_clue) {
                await window.pywebview.api.byoc_add_clue({ title, content, is_revealed: isRevealed });
                clueModal.classList.add("hidden");
                ttrpgData.world_state = null;
                await renderWorldStateLedger();
            }
        });
    }
}

// =========================================================================
// Phase 1: Vault Tabs Binding & D&D 5e SRD Compendium + Homebrew Lab UI
// =========================================================================

let activeCompendiumTab = "weapons";
let compendiumItemsCache = [];
let selectedCompendiumItem = null;
let compendiumSearchTimer = null;

function openCompendiumModal(initialTab = "weapons") {
    const modal = document.getElementById("compendium-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    setActiveCompendiumTab(initialTab || "weapons");
}

function closeCompendiumModal() {
    const modal = document.getElementById("compendium-modal");
    if (modal) modal.classList.add("hidden");
}

function setActiveCompendiumTab(tabName) {
    activeCompendiumTab = tabName || "weapons";
    const tabsBar = document.getElementById("compendium-tabs-bar");
    if (tabsBar) {
        tabsBar.querySelectorAll(".comp-tab-btn").forEach(btn => {
            btn.classList.toggle("active", btn.getAttribute("data-comp-tab") === activeCompendiumTab);
        });
    }

    const filterBar = document.getElementById("compendium-filter-bar");
    const browsePane = document.getElementById("compendium-browse-pane");
    const hbPane = document.getElementById("compendium-homebrew-pane");
    const isHb = (activeCompendiumTab === "homebrew");

    if (filterBar) filterBar.classList.toggle("hidden", isHb);
    if (browsePane) browsePane.classList.toggle("hidden", isHb);
    if (hbPane) hbPane.classList.toggle("hidden", !isHb);

    // Toggle contextual subfilter wrappers
    const wWrap = document.getElementById("comp-filter-weapon-wrap");
    const aWrap = document.getElementById("comp-filter-armor-wrap");
    const iWrap = document.getElementById("comp-filter-item-wrap");
    const sWrap = document.getElementById("comp-filter-spell-wrap");
    if (wWrap) wWrap.classList.toggle("hidden", activeCompendiumTab !== "weapons");
    if (aWrap) aWrap.classList.toggle("hidden", activeCompendiumTab !== "armor");
    if (iWrap) iWrap.classList.toggle("hidden", activeCompendiumTab !== "items");
    if (sWrap) sWrap.classList.toggle("hidden", activeCompendiumTab !== "spells");

    if (isHb) {
        refreshHomebrewList();
        syncHomebrewFormCategoryVisibility();
    } else {
        selectedCompendiumItem = null;
        renderCompendiumDetail(null);
        refreshCompendiumResults();
    }
}

function buildCompendiumFilters() {
    const filters = {};
    if (activeCompendiumTab === "weapons") {
        const wc = document.getElementById("select-comp-weapon-cat")?.value || "";
        const wm = document.getElementById("select-comp-weapon-mastery")?.value || "";
        if (wc) filters.weapon_category = wc;
        if (wm) filters.mastery = wm;
    } else if (activeCompendiumTab === "armor") {
        const ac = document.getElementById("select-comp-armor-cat")?.value || "";
        if (ac) filters.armor_category = ac;
    } else if (activeCompendiumTab === "items") {
        const ic = document.getElementById("select-comp-item-cat")?.value || "";
        const ir = document.getElementById("select-comp-item-rarity")?.value || "";
        if (ic) filters.item_category = ic;
        if (ir) filters.rarity = ir;
    } else if (activeCompendiumTab === "spells") {
        const sc = document.getElementById("select-comp-spell-class")?.value || "";
        const sl = document.getElementById("select-comp-spell-level")?.value || "";
        if (sc) filters.class_name = sc;
        if (sl !== "") filters.level = parseInt(sl, 10);
    }
    return filters;
}

async function refreshCompendiumResults() {
    const listEl = document.getElementById("compendium-results-list");
    const countEl = document.getElementById("compendium-results-count");
    if (!listEl) return;

    const query = (document.getElementById("input-compendium-search")?.value || "").trim();
    const rawEd = document.getElementById("select-compendium-edition")?.value || "all";
    const homebrewOnly = (rawEd === "homebrew");
    const edition = homebrewOnly ? "all" : rawEd;
    const filters = buildCompendiumFilters();

    listEl.innerHTML = `<div style="padding: 18px; text-align: center; color: #64748b; font-size: 0.8rem;">Loading ${escapeRpHtml(activeCompendiumTab)}...</div>`;

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.search_dnd_compendium) {
        listEl.innerHTML = `<div style="padding: 18px; color: #f87171; font-size: 0.8rem;">Compendium backend unavailable.</div>`;
        return;
    }

    try {
        const res = await window.pywebview.api.search_dnd_compendium(
            activeCompendiumTab,
            query,
            edition,
            filters,
            homebrewOnly,
            300
        );
        compendiumItemsCache = (res && Array.isArray(res.items)) ? res.items : [];
        if (countEl) countEl.textContent = `${compendiumItemsCache.length} entries`;

        if (compendiumItemsCache.length === 0) {
            listEl.innerHTML = `<div style="padding: 20px; text-align: center; color: #64748b; font-size: 0.82rem;">No matches found. Try clearing filters or creating it in Homebrew Lab!</div>`;
            return;
        }

        listEl.innerHTML = compendiumItemsCache.map((item, idx) => {
            const ctype = item.compendium_type || activeCompendiumTab;
            const editions = Array.isArray(item.editions) ? item.editions : ["2014", "2024"];
            const isSelected = selectedCompendiumItem && selectedCompendiumItem.index === item.index && selectedCompendiumItem.compendium_type === ctype;
            let subtitle = "";
            if (ctype === "weapons") {
                subtitle = `${item.category || "Weapon"} • ${item.damage || "Special"} • ${item.cost_display || "0 gp"}`;
            } else if (ctype === "armor") {
                subtitle = `${item.category || "Armor"} • AC ${item.ac_base || 10}${item.dex_bonus ? "+DEX" : ""} • ${item.cost_display || "0 gp"}`;
            } else if (ctype === "items") {
                subtitle = `${item.subcategory || item.category || "Gear"} • ${item.cost_display || "Special"}${item.weight ? ` • ${item.weight} lb` : ""}`;
            } else if (ctype === "spells") {
                const lvlLbl = item.level_label || (item.level === 0 ? "Cantrip" : `Level ${item.level}`);
                subtitle = `${lvlLbl} ${item.school || ""} • ${(item.classes || []).slice(0, 4).join(", ")}`;
            }

            const badges = [];
            if (item.is_homebrew) badges.push(`<span class="comp-pill comp-pill-hb">HOMEBREW</span>`);
            if (editions.includes("2024")) badges.push(`<span class="comp-pill comp-pill-ed">2024</span>`);
            if (editions.includes("2014")) badges.push(`<span class="comp-pill comp-pill-ed">2014</span>`);
            if (item.is_magic) badges.push(`<span class="comp-pill comp-pill-magic">${escapeRpHtml(item.rarity || "MAGIC")}</span>`);
            if (item.mastery) badges.push(`<span class="comp-pill comp-pill-mastery">⚔️ ${escapeRpHtml(item.mastery)}</span>`);

            return `
                <div class="comp-list-item ${isSelected ? "selected" : ""}" data-comp-idx="${idx}">
                    <div class="comp-list-top">
                        <span class="comp-list-name">${escapeRpHtml(item.name)}</span>
                    </div>
                    <div class="comp-list-sub">${escapeRpHtml(subtitle)}</div>
                    <div class="comp-badges-row">${badges.join("")}</div>
                </div>
            `;
        }).join("");

        listEl.querySelectorAll(".comp-list-item").forEach(row => {
            row.addEventListener("click", () => {
                const i = parseInt(row.getAttribute("data-comp-idx"), 10);
                const item = compendiumItemsCache[i];
                if (!item) return;
                listEl.querySelectorAll(".comp-list-item").forEach(el => el.classList.remove("selected"));
                row.classList.add("selected");
                selectCompendiumItem(item);
            });
        });
    } catch (e) {
        console.error("[Compendium] Search error:", e);
        listEl.innerHTML = `<div style="padding: 18px; color: #f87171; font-size: 0.8rem;">Search error: ${escapeRpHtml(e.message)}</div>`;
    }
}

async function selectCompendiumItem(item) {
    selectedCompendiumItem = item;
    const cat = item.compendium_type || activeCompendiumTab;
    const edSel = document.getElementById("select-compendium-edition")?.value || "2024";
    const ed = (edSel === "2014" || edSel === "2024") ? edSel : "2024";

    if (window.pywebview && window.pywebview.api && window.pywebview.api.get_dnd_compendium_item) {
        try {
            const res = await window.pywebview.api.get_dnd_compendium_item(cat, item.index || item.name, ed);
            if (res && res.status === "success" && res.item) {
                selectedCompendiumItem = { ...item, ...res.item, compendium_type: cat };
            }
        } catch (e) {
            console.warn("[Compendium] Detail lookup warning:", e);
        }
    }
    renderCompendiumDetail(selectedCompendiumItem);
}

function getActivePartyCharsForSelect() {
    const charsObj = (ttrpgData.state && ttrpgData.state.characters) ? ttrpgData.state.characters : {};
    return Object.values(charsObj).filter(c => c && c.name);
}

function renderCompendiumDetail(item) {
    const pane = document.getElementById("compendium-detail-pane");
    if (!pane) return;
    if (!item) {
        pane.innerHTML = `
            <div class="comp-detail-empty">
                <span style="font-size: 2rem;">📖</span>
                <p>Select any weapon, armor, item, pack, or spell on the left to inspect its 2014 &amp; 2024 rules, grant it to a party hero, or clone it as Homebrew.</p>
            </div>
        `;
        return;
    }

    const cat = item.compendium_type || activeCompendiumTab;
    const editions = Array.isArray(item.editions) ? item.editions : ["2014", "2024"];
    const tags = [];
    if (item.is_homebrew) tags.push(`<span class="comp-pill comp-pill-hb">CUSTOM HOMEBREW</span>`);
    editions.forEach(e => tags.push(`<span class="comp-pill comp-pill-ed">${escapeRpHtml(e)} SRD</span>`));
    if (item.is_magic) tags.push(`<span class="comp-pill comp-pill-magic">${escapeRpHtml(item.rarity || "Magic")}</span>`);
    if (item.requires_attunement) tags.push(`<span class="comp-pill comp-pill-hb">Requires Attunement</span>`);
    if (item.mastery) tags.push(`<span class="comp-pill comp-pill-mastery">Mastery: ${escapeRpHtml(item.mastery)}</span>`);
    if (item.concentration) tags.push(`<span class="comp-pill comp-pill-ed">Concentration</span>`);
    if (item.ritual) tags.push(`<span class="comp-pill comp-pill-ed">Ritual</span>`);

    const stats = [];
    if (cat === "weapons") {
        stats.push(["Category", item.category || "Weapon"]);
        stats.push(["Damage", item.damage || "Special"]);
        if (item.two_handed_damage) stats.push(["Two-Handed", item.two_handed_damage]);
        stats.push(["Range", item.range || "5 ft."]);
        stats.push(["Cost", item.cost_display || "0 gp"]);
        stats.push(["Weight", `${item.weight || 0} lb`]);
        if (Array.isArray(item.properties) && item.properties.length) {
            stats.push(["Properties", item.properties.join(", ")]);
        }
        if (item.mastery) {
            stats.push(["2024 Mastery", item.mastery]);
        }
    } else if (cat === "armor") {
        stats.push(["Category", item.category || "Armor"]);
        let acDisp = `${item.ac_base ?? 10}`;
        if (item.category === "Shield") acDisp = `+${2 + (item.ac_bonus || 0)}`;
        else if (item.dex_bonus) acDisp += item.max_dex_bonus != null ? ` + DEX (max ${item.max_dex_bonus})` : " + DEX";
        if (item.ac_bonus && item.category !== "Shield") acDisp += ` (+${item.ac_bonus})`;
        stats.push(["Armor Class", acDisp]);
        stats.push(["STR Minimum", item.str_minimum ? `STR ${item.str_minimum}` : "None"]);
        stats.push(["Stealth", item.stealth_disadvantage ? "Disadvantage" : "Normal"]);
        stats.push(["Cost", item.cost_display || "0 gp"]);
        stats.push(["Weight", `${item.weight || 0} lb`]);
    } else if (cat === "items") {
        stats.push(["Category", item.subcategory || item.category || "Gear"]);
        stats.push(["Cost", item.cost_display || "Varies"]);
        stats.push(["Weight", `${item.weight || 0} lb`]);
        stats.push(["Rarity", item.rarity || "Common"]);
        if (item.quantity_bundle && item.quantity_bundle > 1) {
            stats.push(["Bundle Qty", `${item.quantity_bundle} per purchase`]);
        }
    } else if (cat === "spells") {
        stats.push(["Level", item.level_label || (item.level === 0 ? "Cantrip" : `Level ${item.level}`)]);
        stats.push(["School", item.school || "Evocation"]);
        stats.push(["Casting Time", item.casting_time || "1 action"]);
        stats.push(["Range", item.range || "Self"]);
        stats.push(["Duration", item.duration || "Instantaneous"]);
        stats.push(["Components", (item.components || []).join(", ") + (item.material ? ` (${item.material})` : "")]);
        if (item.damage) stats.push(["Damage / Effect", item.damage]);
        if (item.dc_type) stats.push(["Saving Throw", item.dc_type]);
        if (Array.isArray(item.classes) && item.classes.length) {
            stats.push(["Classes", item.classes.join(", ")]);
        }
    }

    const statGridHtml = stats.map(([lbl, val]) => `
        <div class="comp-stat-cell">
            <strong>${escapeRpHtml(lbl)}</strong>
            <span>${escapeRpHtml(val)}</span>
        </div>
    `).join("");

    let bodyHtml = "";
    if (cat === "weapons" && item.mastery_desc) {
        bodyHtml += `<div class="comp-desc-box" style="border-color: rgba(249,115,22,0.35);"><strong>2024 Mastery (${escapeRpHtml(item.mastery)}):</strong> ${escapeRpHtml(item.mastery_desc)}</div>`;
    }
    const edSel = document.getElementById("select-compendium-edition")?.value || "2024";
    const mainDesc = item.active_description
        || (edSel === "2014" ? (item.description_2014 || item.description) : (item.description_2024 || item.description))
        || "";
    if (mainDesc) {
        bodyHtml += `<div class="comp-desc-box">${escapeRpHtml(mainDesc)}</div>`;
    }
    if (item.notes_2024) {
        bodyHtml += `<div class="comp-desc-box" style="border-color: rgba(56,189,248,0.35);"><strong>📘 2014 vs 2024 Change Note:</strong> ${escapeRpHtml(item.notes_2024)}</div>`;
    }
    if (item.higher_level) {
        bodyHtml += `<div class="comp-desc-box"><strong>At Higher Levels:</strong> ${escapeRpHtml(item.higher_level)}</div>`;
    }

    const packContents = item.expanded_contents || item.contents || [];
    const hasPackContents = Array.isArray(packContents) && packContents.length > 0;
    if (hasPackContents) {
        const packList = packContents.map(p => `• ${p.quantity || p.qty || 1}x ${p.name}`).join("\n");
        bodyHtml += `<div class="comp-desc-box"><strong>🎒 Pack Contents:</strong>\n${escapeRpHtml(packList)}</div>`;
    }

    const partyChars = getActivePartyCharsForSelect();
    const charOptions = partyChars.length > 0
        ? partyChars.map(c => `<option value="${escapeRpHtml(c.name)}">${escapeRpHtml(c.name)} (${escapeRpHtml(c.archetype || "Hero")})</option>`).join("")
        : `<option value="">No active party characters loaded</option>`;

    const extraControls = `
        ${(cat === "items") ? `
            <div style="display:flex; align-items:center; gap: 12px; flex-wrap:wrap; font-size:0.78rem; color:#cbd5e1;">
                <label style="display:flex; align-items:center; gap:5px;">Qty: <input type="number" id="input-comp-grant-qty" class="modal-input" min="1" value="1" style="width:65px; padding:4px 6px;" /></label>
                ${hasPackContents ? `<label style="display:flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" id="chk-comp-expand-pack" checked /> Unpack bundle into inventory items</label>` : ""}
            </div>
        ` : ""}
        ${(cat === "armor") ? `
            <label style="display:flex; align-items:center; gap:6px; font-size:0.78rem; color:#cbd5e1; cursor:pointer;">
                <input type="checkbox" id="chk-comp-equip-armor" checked /> Equip immediately &amp; recalculate AC
            </label>
        ` : ""}
        ${(cat === "spells") ? `
            <div style="display:flex; flex-direction:column; gap:5px; font-size:0.78rem;">
                <div id="comp-spell-eligibility-note" style="font-size:0.75rem; color:#94a3b8; display:flex; flex-direction:column; gap:2px;"></div>
                <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top:2px;">
                    <label style="display:flex; align-items:center; gap:6px; color:#cbd5e1; cursor:pointer;" title="If checked, marks the spell as prepared for today. If unchecked, adds to spellbook/list as unprepared.">
                        <input type="checkbox" id="chk-comp-prep-spell" checked /> Prepare spell immediately
                    </label>
                </div>
                <div id="comp-spell-override-wrap" style="display:none;">
                    <label style="display:flex; align-items:center; gap:6px; color:#fbbf24; cursor:pointer;">
                        <input type="checkbox" id="chk-comp-bypass-spell-class" /> Allow Out-of-Class grant (Magical Secrets / DM Override)
                    </label>
                </div>
            </div>
        ` : ""}
    `;

    pane.innerHTML = `
        <div class="comp-detail-card-head">
            <h4>${escapeRpHtml(item.name)}</h4>
            <div class="comp-badges-row">${tags.join("")}</div>
        </div>
        <div class="comp-stat-grid">${statGridHtml}</div>
        ${bodyHtml || '<div class="comp-desc-box" style="color:#64748b; font-style:italic;">Standard equipment entry.</div>'}

        <div class="comp-grant-box">
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                <select id="select-comp-grant-char" class="modal-input" style="flex:1; min-width:170px; padding:6px 8px; font-size:0.8rem;">
                    ${charOptions}
                </select>
                <button type="button" id="btn-comp-grant-char" class="btn-primary-sm" style="padding: 7px 14px;" ${partyChars.length === 0 ? "disabled" : ""}>🎁 Grant to Hero</button>
            </div>
            ${extraControls}
            <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; border-top: 1px solid rgba(255,255,255,0.06); padding-top:8px;">
                <div style="display:flex; gap:6px;">
                    <button type="button" id="btn-comp-clone-hb" class="cyber-btn-small">🧬 Clone as Homebrew</button>
                    ${item.is_homebrew ? `<button type="button" id="btn-comp-edit-hb" class="cyber-btn-small">✏️ Edit</button>` : ""}
                    ${item.is_homebrew ? `<button type="button" id="btn-comp-del-hb" class="cyber-btn-small" style="color:#f87171; border-color:rgba(239,68,68,0.4);">🗑️</button>` : ""}
                </div>
                <span id="comp-grant-feedback" style="font-size:0.74rem; color:#4ade80;"></span>
            </div>
        </div>
    `;

    const grantBtn = document.getElementById("btn-comp-grant-char");
    if (grantBtn) grantBtn.addEventListener("click", () => grantSelectedCompendiumItem());

    const cloneBtn = document.getElementById("btn-comp-clone-hb");
    if (cloneBtn) cloneBtn.addEventListener("click", () => cloneCompendiumItemToHomebrew(item));

    const editHbBtn = document.getElementById("btn-comp-edit-hb");
    if (editHbBtn) editHbBtn.addEventListener("click", () => editHomebrewEntry(cat, item));

    const delHbBtn = document.getElementById("btn-comp-del-hb");
    if (delHbBtn) delHbBtn.addEventListener("click", () => deleteHomebrewEntryFromUI(cat, item.index || item.id));

    const charSelect = document.getElementById("select-comp-grant-char");
    if (charSelect && cat === "spells") {
        charSelect.addEventListener("change", () => updateCompendiumSpellEligibilityUI(item));
        updateCompendiumSpellEligibilityUI(item);
    }
}

async function updateCompendiumSpellEligibilityUI(item) {
    if (!item) return;
    const cat = item.compendium_type || activeCompendiumTab;
    if (cat !== "spells") return;

    const charName = document.getElementById("select-comp-grant-char")?.value || "";
    const noteEl = document.getElementById("comp-spell-eligibility-note");
    const overrideWrap = document.getElementById("comp-spell-override-wrap");
    const chkOverride = document.getElementById("chk-comp-bypass-spell-class");
    const chkPrep = document.getElementById("chk-comp-prep-spell");
    if (!noteEl || !overrideWrap) return;

    if (!charName) {
        noteEl.innerHTML = "";
        overrideWrap.style.display = "none";
        return;
    }

    const edSel = document.getElementById("select-compendium-edition")?.value || "2024";
    const edition = (edSel === "2014" || edSel === "2024") ? edSel : "2024";

    if (window.pywebview && window.pywebview.api && window.pywebview.api.check_spell_class_eligibility) {
        try {
            const res = await window.pywebview.api.check_spell_class_eligibility(charName, item.index || item.name, edition);
            let noteHtml = "";
            if (res && res.status === "success") {
                if (res.can_learn) {
                    noteHtml = `<span style="color:#4ade80;">✅ Eligible for ${escapeRpHtml(charName)}</span> <span style="color:#64748b; font-size:0.72rem;">(${escapeRpHtml(res.matched_via || "Class Spell List")})</span>`;
                    overrideWrap.style.display = "none";
                    if (chkOverride) chkOverride.checked = false;
                } else {
                    const classesStr = (res.allowed_spell_classes && res.allowed_spell_classes.length > 0)
                        ? res.allowed_spell_classes.join(", ")
                        : "Specific class";
                    noteHtml = `<span style="color:#f87171;">⚠️ Not on ${escapeRpHtml(charName)}'s list (${escapeRpHtml(classesStr)} spell)</span>`;
                    overrideWrap.style.display = "block";
                }
            }

            // Also check character's current preparation capacity
            if (window.pywebview.api.check_character_spell_legality) {
                const leg = await window.pywebview.api.check_character_spell_legality(charName, edition);
                if (leg && leg.is_caster && leg.max_prepared > 0) {
                    const isCantrip = (item.is_cantrip !== undefined) ? Boolean(item.is_cantrip) : (item.level === 0 || String(item.level).toLowerCase().includes("cantrip"));
                    if (!isCantrip) {
                        if (leg.prepared_count >= leg.max_prepared) {
                            if (chkPrep) chkPrep.checked = false;
                            noteHtml += `<span style="color:#fbbf24; font-size:0.72rem;">⚠️ ${escapeRpHtml(charName)} is at max prepared capacity (${leg.prepared_count}/${leg.max_prepared}). Added to spellbook unprepared unless checked.</span>`;
                        } else {
                            if (chkPrep) chkPrep.checked = true;
                            noteHtml += `<span style="color:#94a3b8; font-size:0.72rem;">Capacity: ${leg.prepared_count} / ${leg.max_prepared} prepared.</span>`;
                        }
                    }
                }
            }
            noteEl.innerHTML = noteHtml;
        } catch (e) {
            console.warn("[Compendium] Spell eligibility check error:", e);
        }
    }
}

async function grantSelectedCompendiumItem() {
    if (!selectedCompendiumItem) return;
    const charName = document.getElementById("select-comp-grant-char")?.value || "";
    if (!charName) return;

    const cat = selectedCompendiumItem.compendium_type || activeCompendiumTab;
    const edSel = document.getElementById("select-compendium-edition")?.value || "2024";
    const edition = (edSel === "2014" || edSel === "2024") ? edSel : "2024";
    const expandPack = document.getElementById("chk-comp-expand-pack")?.checked ?? true;
    const equip = document.getElementById("chk-comp-equip-armor")?.checked ?? false;
    let bypassClass = document.getElementById("chk-comp-bypass-spell-class")?.checked ?? false;
    const prepSpell = document.getElementById("chk-comp-prep-spell")?.checked ?? true;
    const qty = Math.max(1, parseInt(document.getElementById("input-comp-grant-qty")?.value || "1", 10) || 1);
    const feedback = document.getElementById("comp-grant-feedback");

    if (cat === "spells" && !bypassClass && window.pywebview && window.pywebview.api && window.pywebview.api.check_spell_class_eligibility) {
        try {
            const check = await window.pywebview.api.check_spell_class_eligibility(charName, selectedCompendiumItem.index || selectedCompendiumItem.name, edition);
            if (check && !check.can_learn) {
                const classReq = (check.allowed_spell_classes && check.allowed_spell_classes.length > 0)
                    ? check.allowed_spell_classes.join(", ")
                    : "different classes";
                const heroClasses = (check.character_classes && check.character_classes.length > 0)
                    ? check.character_classes.join(", ")
                    : "Non-Caster";
                const proceed = await showAetherConfirm(
                    `'${selectedCompendiumItem.name}' is a ${classReq} spell and not on ${charName}'s class spell list (${heroClasses}).\n\nDo you want to grant this spell anyway as an Out-of-Class grant / Magical Secrets / DM Boon?`,
                    "Class Spell Restriction",
                    "Grant Anyway",
                    false
                );
                if (!proceed) {
                    if (feedback) {
                        feedback.style.color = "#f87171";
                        feedback.textContent = `Grant canceled: requires ${classReq}.`;
                    }
                    return;
                }
                bypassClass = true;
                const chkBox = document.getElementById("chk-comp-bypass-spell-class");
                if (chkBox) chkBox.checked = true;
            }
        } catch (chkErr) {
            console.warn("[Compendium] Check eligibility error:", chkErr);
        }
    }

    try {
        const res = await window.pywebview.api.add_compendium_entry_to_character(
            charName,
            cat,
            selectedCompendiumItem.index || selectedCompendiumItem.name,
            edition,
            Boolean(expandPack),
            qty,
            Boolean(equip),
            Boolean(bypassClass),
            Boolean(prepSpell)
        );
        if (res && res.status === "success") {
            if (feedback) {
                feedback.style.color = "#4ade80";
                feedback.textContent = res.message || `Granted ${selectedCompendiumItem.name}!`;
            }
            await loadRolePlayUI(true);

            // ROSTER LEGALITY CHECK: If character is over their prepared/known spell limit,
            // immediately open the Edit Character modal and show an alert explaining the required fix.
            if (cat === "spells" && res.legality && !res.legality.is_legal) {
                const compModal = document.getElementById("compendium-modal");
                if (compModal) compModal.classList.add("hidden");

                const targetCharId = res.character_id || charName;
                await handleOpenEditCharacterModal(targetCharId, "campaign");

                const curPrep = res.legality.prepared_count;
                const maxPrep = res.legality.max_prepared;
                const excess = res.legality.excess_prepared || (curPrep - maxPrep);
                await showAetherAlert(
                    `⚠️ Spell Preparation Limit Exceeded!\n\n${charName} is currently preparing ${curPrep} leveled spells, but is only allotted ${maxPrep} based on their class and level (over by ${excess}).\n\nPlease uncheck (unprepare) ${excess} leveled spell(s) or remove a spell below to make your roster legal.`,
                    "Action Required: Spell Preparation Limit"
                );
            }
        } else if (feedback) {
            feedback.style.color = "#f87171";
            feedback.textContent = res?.message || "Failed to grant entry.";
        }
    } catch (e) {
        console.error("[Compendium] Grant error:", e);
        if (feedback) {
            feedback.style.color = "#f87171";
            feedback.textContent = e.message;
        }
    }
}

// --- Homebrew Lab Logic ---
function syncHomebrewFormCategoryVisibility() {
    const cat = document.getElementById("hb-field-category")?.value || "weapons";
    const gearMeta = document.getElementById("hb-group-gear-meta");
    const grpWpn = document.getElementById("hb-group-weapons");
    const grpArm = document.getElementById("hb-group-armor");
    const grpItm = document.getElementById("hb-group-items");
    const grpSpl = document.getElementById("hb-group-spells");

    if (gearMeta) gearMeta.classList.toggle("hidden", cat === "spells");
    if (grpWpn) grpWpn.classList.toggle("hidden", cat !== "weapons");
    if (grpArm) grpArm.classList.toggle("hidden", cat !== "armor");
    if (grpItm) grpItm.classList.toggle("hidden", cat !== "items");
    if (grpSpl) grpSpl.classList.toggle("hidden", cat !== "spells");
}

function resetHomebrewForm(category = "weapons") {
    const catSel = document.getElementById("hb-field-category");
    if (catSel) catSel.value = category;
    document.getElementById("hb-field-index").value = "";
    document.getElementById("hb-field-name").value = "";
    document.getElementById("hb-ed-2014").checked = true;
    document.getElementById("hb-ed-2024").checked = true;
    document.getElementById("hb-field-cost-qty").value = "50";
    document.getElementById("hb-field-cost-unit").value = "gp";
    document.getElementById("hb-field-weight").value = "2";
    document.getElementById("hb-field-rarity").value = "Common";
    document.getElementById("hb-field-is-magic").checked = false;
    document.getElementById("hb-field-attunement").checked = false;
    document.getElementById("hb-field-description").value = "";
    document.getElementById("hb-save-status").textContent = "";

    document.getElementById("hb-field-wpn-cat").value = "Martial Melee";
    document.getElementById("hb-field-wpn-mastery").value = "";
    document.getElementById("hb-field-wpn-dice").value = "1d8";
    document.getElementById("hb-field-wpn-dmgtype").value = "Slashing";
    document.getElementById("hb-field-wpn-bonus").value = "";
    document.getElementById("hb-field-wpn-range").value = "5 ft.";
    document.getElementById("hb-field-wpn-props").value = "";

    document.getElementById("hb-field-arm-cat").value = "Medium Armor";
    document.getElementById("hb-field-arm-ac").value = "14";
    document.getElementById("hb-field-arm-bonus").value = "0";
    document.getElementById("hb-field-arm-maxdex").value = "2";
    document.getElementById("hb-field-arm-strmin").value = "0";
    document.getElementById("hb-field-arm-dex").checked = true;
    document.getElementById("hb-field-arm-stealth").checked = false;

    document.getElementById("hb-field-item-subcat").value = "Adventuring Gear";
    document.getElementById("hb-field-item-consumable").checked = false;

    document.getElementById("hb-field-sp-level").value = "1";
    document.getElementById("hb-field-sp-school").value = "Evocation";
    document.getElementById("hb-field-sp-time").value = "1 action";
    document.getElementById("hb-field-sp-range").value = "60 feet";
    document.getElementById("hb-field-sp-duration").value = "Instantaneous";
    document.getElementById("hb-field-sp-comps").value = "V, S";
    document.getElementById("hb-field-sp-dmg").value = "";
    document.getElementById("hb-field-sp-dc").value = "";
    document.getElementById("hb-field-sp-atktype").value = "";
    document.getElementById("hb-field-sp-conc").checked = false;
    document.getElementById("hb-field-sp-ritual").checked = false;
    document.querySelectorAll("#hb-spell-classes-checks input[type='checkbox']").forEach(chk => {
        chk.checked = (chk.value === "Wizard");
    });

    syncHomebrewFormCategoryVisibility();
}

function populateHomebrewForm(cat, entry, isClone = false) {
    resetHomebrewForm(cat);
    const catSel = document.getElementById("hb-field-category");
    if (catSel) catSel.value = cat;
    syncHomebrewFormCategoryVisibility();

    document.getElementById("hb-field-index").value = isClone ? "" : (entry.index || entry.id || "");
    document.getElementById("hb-field-name").value = isClone ? `Custom ${entry.name || ""}`.trim() : (entry.name || "");
    const eds = Array.isArray(entry.editions) ? entry.editions : ["2014", "2024"];
    document.getElementById("hb-ed-2014").checked = eds.includes("2014");
    document.getElementById("hb-ed-2024").checked = eds.includes("2024");
    document.getElementById("hb-field-description").value = entry.description_2024 || entry.description || "";

    if (cat !== "spells") {
        const costObj = entry.cost || {};
        document.getElementById("hb-field-cost-qty").value = costObj.quantity ?? 50;
        document.getElementById("hb-field-cost-unit").value = costObj.unit || "gp";
        document.getElementById("hb-field-weight").value = entry.weight ?? 1;
        document.getElementById("hb-field-rarity").value = entry.rarity || "Common";
        document.getElementById("hb-field-is-magic").checked = Boolean(entry.is_magic);
        document.getElementById("hb-field-attunement").checked = Boolean(entry.requires_attunement);
    }

    if (cat === "weapons") {
        document.getElementById("hb-field-wpn-cat").value = entry.category || "Martial Melee";
        document.getElementById("hb-field-wpn-mastery").value = entry.mastery || "";
        document.getElementById("hb-field-wpn-dice").value = entry.damage_dice || "1d8";
        document.getElementById("hb-field-wpn-dmgtype").value = entry.damage_type || "Slashing";
        document.getElementById("hb-field-wpn-bonus").value = entry.atk_bonus || "";
        document.getElementById("hb-field-wpn-range").value = entry.range || "5 ft.";
        document.getElementById("hb-field-wpn-props").value = Array.isArray(entry.properties) ? entry.properties.join(", ") : "";
    } else if (cat === "armor") {
        document.getElementById("hb-field-arm-cat").value = entry.category || "Medium Armor";
        document.getElementById("hb-field-arm-ac").value = entry.ac_base ?? 14;
        document.getElementById("hb-field-arm-bonus").value = entry.ac_bonus ?? 0;
        document.getElementById("hb-field-arm-maxdex").value = entry.max_dex_bonus != null ? entry.max_dex_bonus : "";
        document.getElementById("hb-field-arm-strmin").value = entry.str_minimum ?? 0;
        document.getElementById("hb-field-arm-dex").checked = entry.dex_bonus !== false;
        document.getElementById("hb-field-arm-stealth").checked = Boolean(entry.stealth_disadvantage);
    } else if (cat === "items") {
        document.getElementById("hb-field-item-subcat").value = entry.subcategory || entry.category || "Adventuring Gear";
        document.getElementById("hb-field-item-consumable").checked = Boolean(entry.consumable);
    } else if (cat === "spells") {
        document.getElementById("hb-field-sp-level").value = String(entry.level ?? 1);
        document.getElementById("hb-field-sp-school").value = entry.school || "Evocation";
        document.getElementById("hb-field-sp-time").value = entry.casting_time || "1 action";
        document.getElementById("hb-field-sp-range").value = entry.range || "60 feet";
        document.getElementById("hb-field-sp-duration").value = entry.duration || "Instantaneous";
        document.getElementById("hb-field-sp-comps").value = Array.isArray(entry.components) ? entry.components.join(", ") : (entry.components || "V, S");
        document.getElementById("hb-field-sp-dmg").value = entry.damage || "";
        document.getElementById("hb-field-sp-dc").value = entry.dc_type || "";
        document.getElementById("hb-field-sp-atktype").value = entry.attack_type || "";
        document.getElementById("hb-field-sp-conc").checked = Boolean(entry.concentration);
        document.getElementById("hb-field-sp-ritual").checked = Boolean(entry.ritual);
        const clsSet = new Set((entry.classes || []).map(c => c.toLowerCase()));
        document.querySelectorAll("#hb-spell-classes-checks input[type='checkbox']").forEach(chk => {
            chk.checked = clsSet.has(chk.value.toLowerCase());
        });
    }
}

function cloneCompendiumItemToHomebrew(item) {
    const cat = item.compendium_type || activeCompendiumTab;
    setActiveCompendiumTab("homebrew");
    populateHomebrewForm(cat, item, true);
}

function editHomebrewEntry(cat, item) {
    setActiveCompendiumTab("homebrew");
    populateHomebrewForm(cat, item, false);
}

async function refreshHomebrewList() {
    const listEl = document.getElementById("hb-entries-list");
    if (!listEl || !window.pywebview || !window.pywebview.api || !window.pywebview.api.list_homebrew_entries) return;

    const filterCat = document.getElementById("select-hb-list-filter")?.value || "all";
    try {
        const res = await window.pywebview.api.list_homebrew_entries(filterCat);
        const entries = (res && Array.isArray(res.entries)) ? res.entries : [];
        if (entries.length === 0) {
            listEl.innerHTML = `<div style="padding: 16px; text-align:center; color:#64748b; font-size:0.78rem;">No custom homebrew created yet.<br>Fill out the form on the right or click "Clone as Homebrew" on any SRD item!</div>`;
            return;
        }
        listEl.innerHTML = entries.map((e, i) => `
            <div class="comp-list-item" data-hb-idx="${i}">
                <div class="comp-list-top">
                    <span class="comp-list-name">${escapeRpHtml(e.name)}</span>
                    <div style="display:flex; gap:4px;">
                        <button type="button" class="cyber-btn-tiny btn-hb-edit" data-hb-idx="${i}" title="Edit">✏️</button>
                        <button type="button" class="cyber-btn-tiny btn-hb-del" data-hb-idx="${i}" style="color:#f87171;" title="Delete">🗑️</button>
                    </div>
                </div>
                <div class="comp-list-sub">${escapeRpHtml((e.compendium_type || "").toUpperCase())} ${e.is_magic ? `• ${escapeRpHtml(e.rarity || "Magic")}` : ""}</div>
            </div>
        `).join("");

        listEl.querySelectorAll(".btn-hb-edit").forEach(btn => {
            btn.addEventListener("click", (ev) => {
                ev.stopPropagation();
                const ent = entries[parseInt(btn.getAttribute("data-hb-idx"), 10)];
                if (ent) populateHomebrewForm(ent.compendium_type, ent, false);
            });
        });
        listEl.querySelectorAll(".comp-list-item").forEach(row => {
            row.addEventListener("click", () => {
                const ent = entries[parseInt(row.getAttribute("data-hb-idx"), 10)];
                if (ent) populateHomebrewForm(ent.compendium_type, ent, false);
            });
        });
        listEl.querySelectorAll(".btn-hb-del").forEach(btn => {
            btn.addEventListener("click", async (ev) => {
                ev.stopPropagation();
                const ent = entries[parseInt(btn.getAttribute("data-hb-idx"), 10)];
                if (ent) await deleteHomebrewEntryFromUI(ent.compendium_type, ent.index || ent.id);
            });
        });
    } catch (e) {
        console.error("[Homebrew] List error:", e);
    }
}

async function saveHomebrewForm() {
    const cat = document.getElementById("hb-field-category")?.value || "weapons";
    const name = (document.getElementById("hb-field-name")?.value || "").trim();
    const statusEl = document.getElementById("hb-save-status");
    if (!name) {
        if (statusEl) { statusEl.style.color = "#f87171"; statusEl.textContent = "Please enter a name."; }
        return;
    }

    const editions = [];
    if (document.getElementById("hb-ed-2014")?.checked) editions.push("2014");
    if (document.getElementById("hb-ed-2024")?.checked) editions.push("2024");
    if (editions.length === 0) editions.push("2024");

    const payload = {
        index: (document.getElementById("hb-field-index")?.value || "").trim(),
        name,
        editions,
        description: (document.getElementById("hb-field-description")?.value || "").trim(),
    };

    if (cat !== "spells") {
        payload.cost = {
            quantity: Math.max(0, parseFloat(document.getElementById("hb-field-cost-qty")?.value || "0") || 0),
            unit: document.getElementById("hb-field-cost-unit")?.value || "gp",
        };
        payload.weight = Math.max(0, parseFloat(document.getElementById("hb-field-weight")?.value || "0") || 0);
        payload.rarity = document.getElementById("hb-field-rarity")?.value || "Common";
        payload.is_magic = Boolean(document.getElementById("hb-field-is-magic")?.checked);
        payload.requires_attunement = Boolean(document.getElementById("hb-field-attunement")?.checked);
    }

    if (cat === "weapons") {
        const dice = (document.getElementById("hb-field-wpn-dice")?.value || "1d6").trim();
        const dmgType = document.getElementById("hb-field-wpn-dmgtype")?.value || "Slashing";
        payload.category = document.getElementById("hb-field-wpn-cat")?.value || "Martial Melee";
        payload.damage_dice = dice;
        payload.damage_type = dmgType;
        payload.damage = `${dice} ${dmgType}`.trim();
        payload.atk_bonus = document.getElementById("hb-field-wpn-bonus")?.value || "";
        payload.range = (document.getElementById("hb-field-wpn-range")?.value || "5 ft.").trim();
        payload.properties = (document.getElementById("hb-field-wpn-props")?.value || "").trim();
        payload.mastery = document.getElementById("hb-field-wpn-mastery")?.value || "";
    } else if (cat === "armor") {
        const maxDexRaw = (document.getElementById("hb-field-arm-maxdex")?.value || "").trim();
        payload.category = document.getElementById("hb-field-arm-cat")?.value || "Medium Armor";
        payload.ac_base = parseInt(document.getElementById("hb-field-arm-ac")?.value || "14", 10);
        payload.ac_bonus = parseInt(document.getElementById("hb-field-arm-bonus")?.value || "0", 10);
        payload.max_dex_bonus = maxDexRaw !== "" ? parseInt(maxDexRaw, 10) : null;
        payload.str_minimum = parseInt(document.getElementById("hb-field-arm-strmin")?.value || "0", 10);
        payload.dex_bonus = Boolean(document.getElementById("hb-field-arm-dex")?.checked);
        payload.stealth_disadvantage = Boolean(document.getElementById("hb-field-arm-stealth")?.checked);
    } else if (cat === "items") {
        payload.subcategory = (document.getElementById("hb-field-item-subcat")?.value || "Adventuring Gear").trim();
        payload.category = payload.is_magic ? "Magic Items" : payload.subcategory;
        payload.consumable = Boolean(document.getElementById("hb-field-item-consumable")?.checked);
    } else if (cat === "spells") {
        const selectedClasses = [];
        document.querySelectorAll("#hb-spell-classes-checks input[type='checkbox']:checked").forEach(chk => {
            selectedClasses.push(chk.value);
        });
        payload.level = parseInt(document.getElementById("hb-field-sp-level")?.value || "1", 10);
        payload.school = document.getElementById("hb-field-sp-school")?.value || "Evocation";
        payload.casting_time = (document.getElementById("hb-field-sp-time")?.value || "1 action").trim();
        payload.range = (document.getElementById("hb-field-sp-range")?.value || "60 feet").trim();
        payload.duration = (document.getElementById("hb-field-sp-duration")?.value || "Instantaneous").trim();
        payload.components = (document.getElementById("hb-field-sp-comps")?.value || "V, S").trim();
        payload.damage = (document.getElementById("hb-field-sp-dmg")?.value || "").trim();
        payload.dc_type = document.getElementById("hb-field-sp-dc")?.value || "";
        payload.attack_type = document.getElementById("hb-field-sp-atktype")?.value || "";
        payload.concentration = Boolean(document.getElementById("hb-field-sp-conc")?.checked);
        payload.ritual = Boolean(document.getElementById("hb-field-sp-ritual")?.checked);
        payload.classes = selectedClasses;
    }

    try {
        const res = await window.pywebview.api.save_homebrew_entry(cat, payload);
        if (res && res.status === "success") {
            if (res.entry?.index) {
                document.getElementById("hb-field-index").value = res.entry.index;
            }
            if (statusEl) {
                statusEl.style.color = "#4ade80";
                statusEl.textContent = `Saved custom ${cat.slice(0, -1)} '${name}'!`;
            }
            await refreshHomebrewList();
        } else if (statusEl) {
            statusEl.style.color = "#f87171";
            statusEl.textContent = res?.message || "Save failed.";
        }
    } catch (e) {
        console.error("[Homebrew] Save error:", e);
        if (statusEl) {
            statusEl.style.color = "#f87171";
            statusEl.textContent = e.message;
        }
    }
}

async function deleteHomebrewEntryFromUI(cat, entryId) {
    if (!entryId) return;
    if (!(await showAetherConfirm(`Delete homebrew entry '${entryId}'?`, "Delete Homebrew", "Delete", true))) return;
    try {
        const res = await window.pywebview.api.delete_homebrew_entry(cat, entryId);
        if (res && res.status === "success") {
            if (activeCompendiumTab === "homebrew") {
                resetHomebrewForm(cat);
                await refreshHomebrewList();
            } else {
                selectedCompendiumItem = null;
                renderCompendiumDetail(null);
                await refreshCompendiumResults();
            }
        }
    } catch (e) {
        console.error("[Homebrew] Delete error:", e);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    // 1. Vault System Tabs
    const vaultTabsBar = document.getElementById("vault-system-tabs-bar");
    if (vaultTabsBar) {
        vaultTabsBar.querySelectorAll(".vault-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                activeVaultTab = btn.getAttribute("data-vault-tab") || "all";
                updateVaultTabButtonsUI();
                refreshVaultModalUI();
            });
        });
    }

    // 2. Compendium Modal Controls
    const openCompBtn = document.getElementById("open-compendium-btn");
    const closeCompBtn = document.getElementById("btn-close-compendium-modal");
    const closeCompFooter = document.getElementById("btn-close-compendium-footer");
    if (openCompBtn) openCompBtn.addEventListener("click", () => openCompendiumModal("weapons"));
    if (closeCompBtn) closeCompBtn.addEventListener("click", closeCompendiumModal);
    if (closeCompFooter) closeCompFooter.addEventListener("click", closeCompendiumModal);

    const compTabsBar = document.getElementById("compendium-tabs-bar");
    if (compTabsBar) {
        compTabsBar.querySelectorAll(".comp-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => setActiveCompendiumTab(btn.getAttribute("data-comp-tab")));
        });
    }

    const searchInput = document.getElementById("input-compendium-search");
    if (searchInput) {
        searchInput.addEventListener("input", () => {
            if (compendiumSearchTimer) clearTimeout(compendiumSearchTimer);
            compendiumSearchTimer = setTimeout(() => refreshCompendiumResults(), 180);
        });
    }

    [
        "select-compendium-edition",
        "select-comp-weapon-cat",
        "select-comp-weapon-mastery",
        "select-comp-armor-cat",
        "select-comp-item-cat",
        "select-comp-item-rarity",
        "select-comp-spell-class",
        "select-comp-spell-level"
    ].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", () => refreshCompendiumResults());
    });

    // 3. Homebrew Lab Controls
    const hbCatSel = document.getElementById("hb-field-category");
    if (hbCatSel) hbCatSel.addEventListener("change", () => syncHomebrewFormCategoryVisibility());
    const hbListFilter = document.getElementById("select-hb-list-filter");
    if (hbListFilter) hbListFilter.addEventListener("change", () => refreshHomebrewList());
    const hbNewBtn = document.getElementById("btn-hb-new-entry");
    if (hbNewBtn) hbNewBtn.addEventListener("click", () => resetHomebrewForm(document.getElementById("hb-field-category")?.value || "weapons"));
    const hbResetBtn = document.getElementById("btn-hb-reset");
    if (hbResetBtn) hbResetBtn.addEventListener("click", () => resetHomebrewForm(document.getElementById("hb-field-category")?.value || "weapons"));
    const hbSaveBtn = document.getElementById("btn-hb-save");
    if (hbSaveBtn) hbSaveBtn.addEventListener("click", () => saveHomebrewForm());
});

// =========================================================================
// Phase 2: Location-Based Merchant Shop & Interactive Short / Long Rest UI
// =========================================================================

let shopMode = "buy";
let shopCatalogCache = [];
let shopSellCache = [];
let selectedShopEntry = null;
let shopSearchTimer = null;

function formatPurseFromCharacter(ch) {
    if (!ch) return "0 gp";
    const c = ch.currency || ch.native_system_data?.currency || {};
    const pp = parseInt(c.pp || 0, 10) || 0;
    const gp = parseInt(c.gp !== undefined ? c.gp : (c.gold || 0), 10) || 0;
    const ep = parseInt(c.ep || 0, 10) || 0;
    const sp = parseInt(c.sp || 0, 10) || 0;
    const cp = parseInt(c.cp || 0, 10) || 0;
    const parts = [];
    if (pp) parts.push(`${pp} pp`);
    if (gp || parts.length === 0) parts.push(`${gp} gp`);
    if (ep) parts.push(`${ep} ep`);
    if (sp) parts.push(`${sp} sp`);
    if (cp) parts.push(`${cp} cp`);
    return parts.join(", ");
}

function updateShopMultiplierBadge() {
    const slider = document.getElementById("input-shop-multiplier");
    const lbl = document.getElementById("shop-multiplier-label");
    if (!slider || !lbl) return;
    const mult = parseFloat(slider.value || "1.0") || 1.0;
    const pctDiff = Math.round((mult - 1.0) * 100);
    const activeShop = ttrpgData.state?.active_shop;
    const storyMult = activeShop?.price_multiplier ? parseFloat(activeShop.price_multiplier) : null;
    const isStory = storyMult !== null && Math.abs(mult - storyMult) < 0.01;

    let baseText = "";
    if (pctDiff === 0) {
        lbl.style.color = "#38bdf8";
        baseText = "1.00x (Standard)";
    } else if (pctDiff < 0) {
        lbl.style.color = "#4ade80";
        baseText = `${mult.toFixed(2)}x (${pctDiff}% Discount)`;
    } else {
        lbl.style.color = "#fb923c";
        baseText = `${mult.toFixed(2)}x (+${pctDiff}% Markup)`;
    }

    if (isStory) {
        lbl.innerHTML = `${baseText} <span class="comp-pill comp-pill-ed" style="font-size:0.65rem; margin-left:4px; vertical-align:middle;">🎭 Story Rate</span>`;
    } else if (storyMult !== null) {
        lbl.innerHTML = `${baseText} <button type="button" id="btn-reset-story-mult" class="cyber-btn-tiny" style="font-size:10px; margin-left:6px; padding:1px 6px;" title="Reset price multiplier to the rate set by the DM/story">↺ Story (${storyMult.toFixed(2)}x)</button>`;
        document.getElementById("btn-reset-story-mult")?.addEventListener("click", () => {
            slider.value = String(storyMult);
            updateShopMultiplierBadge();
            refreshShopList();
        });
    } else {
        lbl.textContent = baseText;
    }
}

function refreshShopHeroDropdown(preferredName = "") {
    const sel = document.getElementById("select-shop-character");
    const badge = document.getElementById("shop-char-wallet-badge");
    if (!sel) return;
    const chars = getActivePartyCharsForSelect();
    const prevVal = preferredName || sel.value || "";
    if (chars.length === 0) {
        sel.innerHTML = `<option value="">No party heroes loaded</option>`;
        if (badge) badge.textContent = "🪙 Purse: 0 gp";
        return;
    }
    sel.innerHTML = chars.map(c => `<option value="${escapeRpHtml(c.name)}">${escapeRpHtml(c.name)} (${escapeRpHtml(c.archetype || "Hero")})</option>`).join("");
    if (prevVal && chars.some(c => c.name === prevVal)) {
        sel.value = prevVal;
    }
    const chosen = chars.find(c => c.name === sel.value) || chars[0];
    if (badge) badge.textContent = `🪙 Purse: ${formatPurseFromCharacter(chosen)}`;
}

function openShopModal(shopData = null) {
    const modal = document.getElementById("shop-modal");
    if (!modal) return;

    const activeShop = shopData || ttrpgData.state?.active_shop || null;
    const titleEl = document.getElementById("shop-modal-title");
    const subEl = document.getElementById("shop-modal-subtitle");
    const flavorEl = document.getElementById("shop-banner-flavor");
    const multEl = document.getElementById("input-shop-multiplier");
    const specEl = document.getElementById("select-shop-specialty");
    const magicEl = document.getElementById("chk-shop-include-magic");
    const edEl = document.getElementById("select-shop-edition");
    const feedbackEl = document.getElementById("shop-global-feedback");
    if (feedbackEl) feedbackEl.textContent = "";

    const activeEd = ttrpgData.active_campaign_meta?.ruleset_edition || ttrpgData.state?.ruleset_edition || "2024";
    if (edEl && (activeEd === "2014" || activeEd === "2024")) edEl.value = activeEd;

    if (activeShop && typeof activeShop === "object") {
        if (titleEl && activeShop.shop_name) titleEl.textContent = activeShop.shop_name;
        if (subEl && activeShop.location_name) subEl.textContent = `📍 ${activeShop.location_name} • Rate: ${Number(activeShop.price_multiplier || 1.0).toFixed(2)}x`;
        if (multEl && activeShop.price_multiplier) multEl.value = String(activeShop.price_multiplier);
        if (specEl && activeShop.specialty) {
            const specAlias = { "smith": "weapons_armor", "alchemist": "potions", "outfitter": "gear" };
            const mappedSpec = specAlias[activeShop.specialty] || activeShop.specialty;
            const hasOpt = Array.from(specEl.options).some(o => o.value === mappedSpec);
            if (hasOpt) specEl.value = mappedSpec;
        }
        if (magicEl && activeShop.include_magic !== undefined) magicEl.checked = Boolean(activeShop.include_magic);
        if (flavorEl) {
            if (activeShop.description) {
                flavorEl.textContent = `💬 "${activeShop.description}"`;
                flavorEl.classList.remove("hidden");
            } else {
                flavorEl.classList.add("hidden");
            }
        }
    } else {
        if (titleEl) titleEl.textContent = "Merchant Shop & Quartermaster";
        if (subEl) subEl.textContent = "Regional Pricing (50%–200%) • Multi-Coin Settlement • Automatic Change";
        if (flavorEl) flavorEl.classList.add("hidden");
    }

    updateShopMultiplierBadge();
    refreshShopHeroDropdown();
    modal.classList.remove("hidden");
    setShopMode("buy");
}

function closeShopModal() {
    const modal = document.getElementById("shop-modal");
    if (modal) modal.classList.add("hidden");
}

function setShopMode(mode) {
    shopMode = mode === "sell" ? "sell" : "buy";
    const buyBtn = document.getElementById("btn-shop-mode-buy");
    const sellBtn = document.getElementById("btn-shop-mode-sell");
    if (buyBtn) buyBtn.classList.toggle("active", shopMode === "buy");
    if (sellBtn) sellBtn.classList.toggle("active", shopMode === "sell");
    selectedShopEntry = null;
    renderShopDetail(null);
    refreshShopList();
}

async function refreshShopList() {
    const listEl = document.getElementById("shop-items-list");
    const countEl = document.getElementById("shop-results-count");
    if (!listEl) return;

    if (shopMode === "sell") {
        const heroName = document.getElementById("select-shop-character")?.value || "";
        const hero = getActivePartyCharsForSelect().find(c => c.name === heroName);
        if (!hero) {
            listEl.innerHTML = `<div style="padding:18px; color:#64748b; text-align:center;">Select an active hero to sell items.</div>`;
            if (countEl) countEl.textContent = "0 owned";
            return;
        }
        const query = (document.getElementById("input-shop-search")?.value || "").trim().toLowerCase();
        const sellEntries = [];
        (hero.inventory || []).forEach(it => {
            const nm = typeof it === "string" ? it : (it.name || "");
            if (!nm) return;
            const qVal = typeof it === "object" ? (parseInt(it.qty || "1", 10) || 1) : 1;
            const cat = typeof it === "object" ? (it.category || "Gear") : "Gear";
            if (!query || nm.toLowerCase().includes(query)) {
                sellEntries.push({
                    name: nm,
                    qty: qVal,
                    category: cat,
                    source: "inventory",
                    notes: typeof it === "object" ? (it.notes || "") : "",
                    equipped: typeof it === "object" ? Boolean(it.equipped) : false,
                });
            }
        });
        (hero.weapons || []).forEach(w => {
            const nm = typeof w === "string" ? w : (w.name || "");
            if (!nm || nm.toLowerCase() === "unarmed strike" || nm.toLowerCase() === "fists") return;
            if (!query || nm.toLowerCase().includes(query)) {
                sellEntries.push({
                    name: nm,
                    qty: 1,
                    category: "Weapon",
                    source: "weapons",
                    notes: typeof w === "object" ? `${w.atk_bonus || "+0"} • ${w.damage || ""}` : "",
                    equipped: true,
                });
            }
        });
        shopSellCache = sellEntries;
        if (countEl) countEl.textContent = `${sellEntries.length} owned`;
        if (sellEntries.length === 0) {
            listEl.innerHTML = `<div style="padding:20px; text-align:center; color:#64748b; font-size:0.82rem;">No matching gear or weapons to sell.</div>`;
            return;
        }
        listEl.innerHTML = sellEntries.map((ent, i) => {
            const isSel = selectedShopEntry && selectedShopEntry._sellIdx === i;
            return `
                <div class="comp-list-item ${isSel ? "selected" : ""}" data-shop-sell-idx="${i}">
                    <div class="comp-list-top">
                        <span class="comp-list-name">${escapeRpHtml(ent.name)}</span>
                        <span class="shop-price-tag shop-price-standard">Owned: x${ent.qty}</span>
                    </div>
                    <div class="comp-list-sub">${escapeRpHtml(ent.category)}${ent.notes ? ` • ${escapeRpHtml(ent.notes)}` : ""}</div>
                </div>
            `;
        }).join("");

        listEl.querySelectorAll("[data-shop-sell-idx]").forEach(row => {
            row.addEventListener("click", () => {
                const i = parseInt(row.getAttribute("data-shop-sell-idx"), 10);
                const ent = shopSellCache[i];
                if (!ent) return;
                listEl.querySelectorAll(".comp-list-item").forEach(el => el.classList.remove("selected"));
                row.classList.add("selected");
                selectedShopEntry = { ...ent, _sellIdx: i };
                renderShopDetail(selectedShopEntry);
            });
        });
        return;
    }

    // BUY MODE
    const mult = parseFloat(document.getElementById("input-shop-multiplier")?.value || "1.0") || 1.0;
    const spec = document.getElementById("select-shop-specialty")?.value || "all";
    const query = (document.getElementById("input-shop-search")?.value || "").trim();
    const edition = document.getElementById("select-shop-edition")?.value || "2024";
    const includeMagic = Boolean(document.getElementById("chk-shop-include-magic")?.checked);

    listEl.innerHTML = `<div style="padding:18px; text-align:center; color:#64748b; font-size:0.8rem;">Stocking merchant shelves...</div>`;
    if (!window.pywebview?.api?.get_merchant_catalog) {
        listEl.innerHTML = `<div style="padding:18px; color:#f87171;">Shop backend unavailable.</div>`;
        return;
    }
    try {
        const res = await window.pywebview.api.get_merchant_catalog(spec, query, edition, mult, includeMagic);
        shopCatalogCache = (res && Array.isArray(res.items)) ? res.items : [];
        if (countEl) countEl.textContent = `${shopCatalogCache.length} wares`;
        if (shopCatalogCache.length === 0) {
            listEl.innerHTML = `<div style="padding:20px; text-align:center; color:#64748b; font-size:0.82rem;">No matching merchant goods found. Try enabling Magic Items or changing Specialty.</div>`;
            return;
        }
        listEl.innerHTML = shopCatalogCache.map((item, i) => {
            const ctype = item.compendium_type || "items";
            const isSel = selectedShopEntry && selectedShopEntry.index === item.index && selectedShopEntry.compendium_type === ctype;
            const priceCls = mult < 0.99 ? "shop-price-discount" : (mult > 1.01 ? "shop-price-markup" : "shop-price-standard");
            let sub = "";
            if (ctype === "weapons") sub = `${item.category || "Weapon"} • ${item.damage || ""}`;
            else if (ctype === "armor") sub = `${item.category || "Armor"} • AC ${item.ac_base ?? 10}${item.dex_bonus ? "+DEX" : ""}`;
            else sub = `${item.subcategory || item.category || "Gear"}${item.weight ? ` • ${item.weight} lb` : ""}`;

            return `
                <div class="comp-list-item ${isSel ? "selected" : ""}" data-shop-buy-idx="${i}">
                    <div class="comp-list-top">
                        <span class="comp-list-name">${escapeRpHtml(item.name)}</span>
                        <span class="shop-price-tag ${priceCls}">${escapeRpHtml(item.adjusted_cost_display || item.cost_display || "Free")}</span>
                    </div>
                    <div class="comp-list-sub">${escapeRpHtml(sub)}${item.is_magic ? ` • ✨ ${escapeRpHtml(item.rarity || "Magic")}` : ""}</div>
                </div>
            `;
        }).join("");

        listEl.querySelectorAll("[data-shop-buy-idx]").forEach(row => {
            row.addEventListener("click", () => {
                const i = parseInt(row.getAttribute("data-shop-buy-idx"), 10);
                const item = shopCatalogCache[i];
                if (!item) return;
                listEl.querySelectorAll(".comp-list-item").forEach(el => el.classList.remove("selected"));
                row.classList.add("selected");
                selectedShopEntry = item;
                renderShopDetail(item);
            });
        });

        if (selectedShopEntry && shopMode === "buy") {
            const refreshedSel = shopCatalogCache.find(it => it.index === selectedShopEntry.index && (it.compendium_type || "items") === (selectedShopEntry.compendium_type || "items"));
            if (refreshedSel) {
                selectedShopEntry = refreshedSel;
                renderShopDetail(refreshedSel);
            }
        }
    } catch (e) {
        console.error("[Shop] Catalog error:", e);
        listEl.innerHTML = `<div style="padding:18px; color:#f87171;">Error loading catalog: ${escapeRpHtml(e.message)}</div>`;
    }
}

function renderShopDetail(item) {
    const pane = document.getElementById("shop-detail-pane");
    if (!pane) return;
    if (!item) {
        pane.innerHTML = `
            <div class="comp-detail-empty">
                <span style="font-size: 2rem;">🏪</span>
                <p>Select any item, weapon, armor, or pack on the left to inspect its local price and complete a transaction.</p>
            </div>
        `;
        return;
    }

    const heroName = document.getElementById("select-shop-character")?.value || "";
    const mult = parseFloat(document.getElementById("input-shop-multiplier")?.value || "1.0") || 1.0;

    if (shopMode === "sell") {
        const maxQty = Math.max(1, item.qty || 1);
        pane.innerHTML = `
            <div class="comp-detail-card-head">
                <h4>Sell: ${escapeRpHtml(item.name)}</h4>
                <div class="comp-badges-row">
                    <span class="comp-pill comp-pill-ed">${escapeRpHtml(item.category || "Gear")}</span>
                    <span class="comp-pill comp-pill-magic">Owned: x${maxQty}</span>
                </div>
            </div>
            <div class="comp-desc-box">Selling back to a merchant typically yields <strong>50% of base value</strong> (${escapeRpHtml(item.notes || "Standard equipment")}).</div>
            <div class="comp-grant-box">
                <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
                    <label style="font-size:0.78rem; color:#cbd5e1;">Qty to Sell:
                        <input type="number" id="input-shop-sell-qty" class="modal-input" min="1" max="${maxQty}" value="1" style="width:72px; padding:4px 6px;" />
                    </label>
                    <label style="font-size:0.78rem; color:#cbd5e1;">Sell Rate:
                        <select id="select-shop-sell-rate" class="modal-input" style="width:130px; padding:4px 6px;">
                            <option value="0.5" selected>50% (Standard)</option>
                            <option value="0.25">25% (Desperate)</option>
                            <option value="0.75">75% (High Haggling)</option>
                            <option value="1.0">100% (Full Trade)</option>
                        </select>
                    </label>
                </div>
                <button type="button" id="btn-shop-execute-tx" class="btn-primary-sm" style="padding: 8px 16px;" ${!heroName ? "disabled" : ""}>🪙 Sell Selected Gear</button>
                <span id="shop-tx-feedback" style="font-size:0.76rem; color:#4ade80;"></span>
            </div>
        `;
        document.getElementById("btn-shop-execute-tx")?.addEventListener("click", () => executeShopSell(item));
        return;
    }

    const ctype = item.compendium_type || "items";
    const stats = [
        ["Category", item.subcategory || item.category || ctype],
        ["Local Price", item.adjusted_cost_display || "Free"],
        ["Base Value", item.base_cost_display || "0 gp"],
        ["Weight", `${item.weight || 0} lb`],
    ];
    if (ctype === "weapons") stats.push(["Damage", item.damage || "Special"]);
    if (ctype === "armor") stats.push(["AC", `${item.ac_base ?? 10}${item.dex_bonus ? "+DEX" : ""}`]);

    const packContents = item.contents || [];
    const hasPack = Array.isArray(packContents) && packContents.length > 0;
    const descParts = [];
    if (item.description) descParts.push(item.description);
    if (hasPack) descParts.push("Pack Contents:\n" + packContents.map(p => `• ${p.quantity || 1}x ${p.name}`).join("\n"));

    pane.innerHTML = `
        <div class="comp-detail-card-head">
            <h4>${escapeRpHtml(item.name)}</h4>
            <div class="comp-badges-row">
                <span class="comp-pill comp-pill-ed">${escapeRpHtml(ctype.toUpperCase())}</span>
                <span class="comp-pill comp-pill-magic">${escapeRpHtml(item.adjusted_cost_display || "Free")} (${mult.toFixed(2)}x)</span>
                ${item.mastery ? `<span class="comp-pill comp-pill-mastery">Mastery: ${escapeRpHtml(item.mastery)}</span>` : ""}
            </div>
        </div>
        <div class="comp-stat-grid">
            ${stats.map(([l, v]) => `<div class="comp-stat-cell"><strong>${escapeRpHtml(l)}</strong><span>${escapeRpHtml(String(v))}</span></div>`).join("")}
        </div>
        <div class="comp-desc-box">${escapeRpHtml(descParts.join("\n\n") || "Standard PHB / SRD equipment.")}</div>
        <div class="comp-grant-box">
            <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap; font-size:0.78rem; color:#cbd5e1;">
                <label style="display:flex; align-items:center; gap:5px;">Qty:
                    <input type="number" id="input-shop-buy-qty" class="modal-input" min="1" value="1" style="width:70px; padding:4px 6px;" />
                </label>
                ${hasPack ? `<label style="display:flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" id="chk-shop-expand-pack" checked /> Unpack into inventory items</label>` : ""}
                ${ctype === "armor" ? `<label style="display:flex; align-items:center; gap:5px; cursor:pointer;"><input type="checkbox" id="chk-shop-equip-armor" checked /> Equip immediately &amp; update AC</label>` : ""}
            </div>
            <button type="button" id="btn-shop-execute-tx" class="btn-primary-sm" style="padding: 8px 16px;" ${!heroName ? "disabled" : ""}>
                🛒 Purchase ${escapeRpHtml(item.name)} (${escapeRpHtml(item.adjusted_cost_display || "0 cp")})
            </button>
            <span id="shop-tx-feedback" style="font-size:0.76rem; color:#4ade80;"></span>
        </div>
    `;
    document.getElementById("btn-shop-execute-tx")?.addEventListener("click", () => executeShopPurchase(item));
}

async function executeShopPurchase(item) {
    const heroName = document.getElementById("select-shop-character")?.value || "";
    if (!heroName || !window.pywebview?.api?.purchase_merchant_item) return;

    const mult = parseFloat(document.getElementById("input-shop-multiplier")?.value || "1.0") || 1.0;
    const ed = document.getElementById("select-shop-edition")?.value || "2024";
    const qty = Math.max(1, parseInt(document.getElementById("input-shop-buy-qty")?.value || "1", 10) || 1);
    const expandPack = document.getElementById("chk-shop-expand-pack")?.checked ?? true;
    const equip = document.getElementById("chk-shop-equip-armor")?.checked ?? false;
    const fb = document.getElementById("shop-tx-feedback");
    const globalFb = document.getElementById("shop-global-feedback");

    try {
        const res = await window.pywebview.api.purchase_merchant_item(
            heroName,
            item.compendium_type || "items",
            item.index || item.name,
            qty,
            mult,
            Boolean(expandPack),
            ed,
            Boolean(equip)
        );
        if (res && res.status === "success") {
            if (fb) { fb.style.color = "#4ade80"; fb.textContent = res.message; }
            if (globalFb) { globalFb.style.color = "#4ade80"; globalFb.textContent = res.message; }
            await loadRolePlayUI(true);
            refreshShopHeroDropdown(heroName);
        } else {
            const msg = res?.message || "Purchase failed.";
            if (fb) { fb.style.color = "#f87171"; fb.textContent = msg; }
            if (globalFb) { globalFb.style.color = "#f87171"; globalFb.textContent = msg; }
        }
    } catch (e) {
        if (fb) { fb.style.color = "#f87171"; fb.textContent = e.message; }
    }
}

async function executeShopSell(item) {
    const heroName = document.getElementById("select-shop-character")?.value || "";
    if (!heroName || !window.pywebview?.api?.sell_merchant_item) return;

    const qty = Math.max(1, parseInt(document.getElementById("input-shop-sell-qty")?.value || "1", 10) || 1);
    const rate = parseFloat(document.getElementById("select-shop-sell-rate")?.value || "0.5") || 0.5;
    const fb = document.getElementById("shop-tx-feedback");
    const globalFb = document.getElementById("shop-global-feedback");

    try {
        const res = await window.pywebview.api.sell_merchant_item(
            heroName,
            item.name,
            rate,
            qty,
            item.source || "auto",
            null
        );
        if (res && res.status === "success") {
            if (fb) { fb.style.color = "#4ade80"; fb.textContent = res.message; }
            if (globalFb) { globalFb.style.color = "#4ade80"; globalFb.textContent = res.message; }
            await loadRolePlayUI(true);
            refreshShopHeroDropdown(heroName);
            selectedShopEntry = null;
            refreshShopList();
        } else {
            const msg = res?.message || "Sale failed.";
            if (fb) { fb.style.color = "#f87171"; fb.textContent = msg; }
            if (globalFb) { globalFb.style.color = "#f87171"; globalFb.textContent = msg; }
        }
    } catch (e) {
        if (fb) { fb.style.color = "#f87171"; fb.textContent = e.message; }
    }
}

window.handleRoleplayShopOpened = function(shopData) {
    openShopModal(shopData || null);
};

// --- Interactive Short / Long Rest Modal Controller ---

let restModalData = null;
let restDraftByChar = {};
let activeRestHeroId = null;
let activeRestType = "short";
let restSpellSearch = "";
let restSpellLevelFilter = "";

function getActiveRestHero() {
    if (!restModalData || !Array.isArray(restModalData.characters) || restModalData.characters.length === 0) return null;
    return restModalData.characters.find(c => c.id === activeRestHeroId) || restModalData.characters[0];
}

function initRestDraftState(data) {
    restDraftByChar = {};
    (data?.characters || []).forEach(ch => {
        const prepNames = [];
        (ch.spellcasting?.current_spells || []).forEach(sp => {
            if (!sp.is_cantrip && sp.prepared) prepNames.push(sp.name);
        });
        restDraftByChar[ch.id] = {
            hit_dice_to_spend: 0,
            manual_hp_gain: 0,
            consumables_used: {},
            equipped_armor: ch.armor_equipped || "",
            shield_equipped: Boolean(ch.shield_equipped),
            attuned_items: Array.isArray(ch.attuned_items) ? [...ch.attuned_items] : [],
            prepared_spell_names: prepNames,
        };
    });
}

function computeRestProjectedAC(ch, draft) {
    const dexMod = Number(ch.dex_mod || 0);
    const armName = (draft.equipped_armor || "").trim();
    const shieldAdd = draft.shield_equipped ? 2 : 0;
    if (!armName || armName.toLowerCase() === "unarmored") {
        return { ac: 10 + dexMod + shieldAdd, warnings: [] };
    }
    const armRef = (ch.owned_armor || []).find(a => a.name.toLowerCase() === armName.toLowerCase());
    if (!armRef) {
        return { ac: Number(ch.armor_class || 10), warnings: [] };
    }
    const baseAc = Number(armRef.ac_base || 10) + Number(armRef.ac_bonus || 0);
    let appliedDex = 0;
    if (armRef.dex_bonus) {
        appliedDex = armRef.max_dex_bonus != null ? Math.min(dexMod, Number(armRef.max_dex_bonus)) : dexMod;
    }
    const warnings = [];
    if (armRef.str_minimum && Number(ch.str_score || 10) < Number(armRef.str_minimum)) {
        warnings.push(`Requires STR ${armRef.str_minimum}`);
    }
    if (armRef.stealth_disadvantage) {
        warnings.push("Stealth Disadvantage");
    }
    return { ac: baseAc + appliedDex + shieldAdd, warnings };
}

function updateRestTypeButtonsUI() {
    const isLong = (activeRestType === "long");
    document.getElementById("btn-rest-type-short")?.classList.toggle("active", !isLong);
    document.getElementById("btn-rest-type-long")?.classList.toggle("active", isLong);
    const commitBtn = document.getElementById("btn-commit-rest");
    if (commitBtn) {
        commitBtn.textContent = isLong ? "🌙 Complete Long Rest" : "⛺ Complete Short Rest";
    }
}

async function openRestModal(options = {}) {
    const modal = document.getElementById("rest-modal");
    if (!modal) return;

    activeRestType = (options.rest_type === "long") ? "long" : "short";
    const edSel = document.getElementById("select-rest-edition");
    const defaultEd = options.edition || ttrpgData.active_campaign_meta?.ruleset_edition || ttrpgData.state?.ruleset_edition || "2024";
    if (edSel && (defaultEd === "2014" || defaultEd === "2024")) edSel.value = defaultEd;

    const reasonEl = document.getElementById("rest-reason-banner");
    if (reasonEl) {
        if (options.reason) {
            reasonEl.textContent = `🏕️ ${options.reason}`;
            reasonEl.classList.remove("hidden");
        } else {
            reasonEl.classList.add("hidden");
        }
    }
    const fb = document.getElementById("rest-feedback");
    if (fb) fb.textContent = "";

    updateRestTypeButtonsUI();
    modal.classList.remove("hidden");
    await reloadRestModalState();
}

function closeRestModal() {
    const modal = document.getElementById("rest-modal");
    if (modal) modal.classList.add("hidden");
}

async function reloadRestModalState() {
    const bodyEl = document.getElementById("rest-hero-body");
    const ed = document.getElementById("select-rest-edition")?.value || "2024";
    if (!window.pywebview?.api?.get_rest_modal_state) {
        if (bodyEl) bodyEl.innerHTML = `<div style="padding:20px; color:#f87171;">Rest backend unavailable.</div>`;
        return;
    }
    try {
        const res = await window.pywebview.api.get_rest_modal_state(activeRestType, ed);
        if (!res || res.status !== "success") {
            if (bodyEl) bodyEl.innerHTML = `<div style="padding:20px; color:#f87171;">${escapeRpHtml(res?.message || "Failed loading rest state.")}</div>`;
            return;
        }
        restModalData = res;
        initRestDraftState(res);
        const pill = document.getElementById("rest-dice-mode-pill");
        if (pill) {
            pill.textContent = res.dice_mode === "physical" ? "🎲 Physical Tabletop Dice" : "⚡ Digital Dice Auto-Roll";
        }
        activeRestHeroId = res.characters[0]?.id || null;
        renderRestHeroTabs();
        renderRestHeroWorkspace();
    } catch (e) {
        if (bodyEl) bodyEl.innerHTML = `<div style="padding:20px; color:#f87171;">${escapeRpHtml(e.message)}</div>`;
    }
}

function renderRestHeroTabs() {
    const tabsEl = document.getElementById("rest-hero-tabs");
    if (!tabsEl) return;
    const chars = restModalData?.characters || [];
    if (chars.length === 0) {
        tabsEl.innerHTML = "";
        return;
    }
    tabsEl.innerHTML = chars.map(ch => `
        <button type="button" class="comp-tab-btn ${ch.id === activeRestHeroId ? "active" : ""}" data-rest-hero-id="${escapeRpHtml(ch.id)}">
            ${escapeRpHtml(ch.name)} <span style="font-size:0.68rem; opacity:0.8;">(${ch.hp.current}/${ch.hp.max} HP)</span>
        </button>
    `).join("");
    tabsEl.querySelectorAll("[data-rest-hero-id]").forEach(btn => {
        btn.addEventListener("click", () => {
            activeRestHeroId = btn.getAttribute("data-rest-hero-id");
            renderRestHeroTabs();
            renderRestHeroWorkspace();
        });
    });
}

function renderRestHeroWorkspace() {
    const bodyEl = document.getElementById("rest-hero-body");
    if (!bodyEl) return;
    const ch = getActiveRestHero();
    if (!ch) {
        bodyEl.innerHTML = `<div style="padding:24px; text-align:center; color:#64748b;">No characters in the active party roster.</div>`;
        return;
    }
    const draft = restDraftByChar[ch.id] || {};
    const isLong = (activeRestType === "long");
    const isPhysical = (restModalData?.dice_mode === "physical");
    const ed = document.getElementById("select-rest-edition")?.value || "2024";

    const acInfo = computeRestProjectedAC(ch, draft);
    const conSign = ch.hit_dice.con_mod >= 0 ? `+${ch.hit_dice.con_mod}` : `${ch.hit_dice.con_mod}`;

    // 1. Hit Dice / HP Card
    const hdHtml = isLong ? `
        <div class="comp-desc-box" style="border-color:rgba(74,222,128,0.35);">
            <strong>🌙 Long Rest Recovery:</strong> Restores HP to <strong>${ch.hp.max}/${ch.hp.max}</strong>, refills all spell slots, resets short &amp; long rest features, and recovers <strong>${ed === "2024" ? "all spent Hit Dice" : `up to ${Math.max(1, Math.floor(ch.hit_dice.max / 2))} Hit Dice`}</strong>.
        </div>
    ` : `
        <div class="rest-item-row">
            <div>
                <strong>Spend Hit Dice</strong>
                <div style="font-size:0.7rem; color:#94a3b8;">Available: ${ch.hit_dice.current}/${ch.hit_dice.max} (${escapeRpHtml(ch.hit_dice.die_str)} ${conSign} CON per die)</div>
            </div>
            <div style="display:flex; align-items:center; gap:6px;">
                <input type="number" id="rest-hd-spend-input" class="modal-input" min="0" max="${ch.hit_dice.current}" value="${draft.hit_dice_to_spend || 0}" style="width:68px; padding:4px 6px;" ${ch.hit_dice.current <= 0 ? "disabled" : ""} />
            </div>
        </div>
        ${isPhysical ? `
            <div class="rest-item-row">
                <div><strong>Physical Dice HP Gained</strong><div style="font-size:0.7rem; color:#94a3b8;">Roll your physical dice + ${conSign} CON</div></div>
                <input type="number" id="rest-manual-hp-input" class="modal-input" min="0" value="${draft.manual_hp_gain || 0}" style="width:82px; padding:4px 6px;" />
            </div>
        ` : `<div style="font-size:0.72rem; color:#94a3b8;">⚡ Digital dice will automatically roll ${escapeRpHtml(ch.hit_dice.die_str)} ${conSign} per Hit Die spent.</div>`}
    `;

    // 2. Consumables Card
    const consList = ch.consumables || [];
    const consHtml = consList.length > 0 ? consList.map((c, idx) => {
        const useCnt = draft.consumables_used[c.name] || 0;
        return `
            <div class="rest-item-row">
                <div>
                    <strong>${escapeRpHtml(c.name)}</strong> <span style="color:#94a3b8;">(x${c.qty})</span>
                    ${c.heal_formula ? `<span class="comp-pill comp-pill-ed" style="margin-left:4px;">+${escapeRpHtml(c.heal_formula)} HP</span>` : ""}
                </div>
                <div style="display:flex; align-items:center; gap:5px;">
                    <span style="font-size:0.7rem; color:#94a3b8;">Use:</span>
                    <input type="number" class="modal-input rest-cons-qty-input" data-cons-idx="${idx}" min="0" max="${c.qty}" value="${useCnt}" style="width:60px; padding:3px 6px;" />
                </div>
            </div>
        `;
    }).join("") : `<div style="font-size:0.76rem; color:#64748b; font-style:italic;">No potions, rations, or consumables in inventory.</div>`;

    // 3. Equipment & Attunement Card
    const armorOptions = [`<option value="">Unarmored (10 + DEX)</option>`].concat(
        (ch.owned_armor || []).map(a => `<option value="${escapeRpHtml(a.name)}" ${(draft.equipped_armor || "").toLowerCase() === a.name.toLowerCase() ? "selected" : ""}>${escapeRpHtml(a.name)} (AC ${a.ac_base}${a.dex_bonus ? "+DEX" : ""})</option>`)
    ).join("");
    const hasShieldInBag = (ch.owned_shields || []).length > 0 || ch.shield_equipped;

    const attList = ch.attunable_items || [];
    const attHtml = attList.length > 0 ? attList.map((att, idx) => {
        const checked = (draft.attuned_items || []).some(n => n.toLowerCase() === att.name.toLowerCase());
        return `
            <label class="rest-item-row" style="cursor:pointer;">
                <span style="display:flex; align-items:center; gap:7px;">
                    <input type="checkbox" class="rest-attune-chk" data-att-idx="${idx}" ${checked ? "checked" : ""} />
                    <strong>${escapeRpHtml(att.name)}</strong>
                </span>
                <span class="comp-pill comp-pill-magic">${escapeRpHtml(att.rarity || "Magic")}</span>
            </label>
        `;
    }).join("") : `<div style="font-size:0.74rem; color:#64748b; font-style:italic;">No magic items requiring attunement in inventory.</div>`;

    // 4. Spell Preparation Card
    const sc = ch.spellcasting || {};
    let spellsSectionHtml = "";
    if (sc.is_caster) {
        const prepSet = new Set((draft.prepared_spell_names || []).map(s => s.toLowerCase()));
        const alwaysPreparedCountedOut = new Set(
            (sc.current_spells || []).filter(s => s.is_cantrip || s.is_always_prepared).map(s => s.name.toLowerCase())
        );

        // Merge current_spells + eligible_class_spells seamlessly
        const mergedSpellsMap = new Map();
        (sc.current_spells || []).forEach(s => {
            mergedSpellsMap.set(s.name.toLowerCase(), { ...s, on_sheet: true });
        });
        if (sc.can_swap_from_class_list && Array.isArray(sc.eligible_class_spells)) {
            sc.eligible_class_spells.forEach(s => {
                const k = s.name.toLowerCase();
                if (!mergedSpellsMap.has(k)) {
                    mergedSpellsMap.set(k, { ...s, is_cantrip: false, is_always_prepared: false, on_sheet: false });
                }
            });
        }
        const allCandidateSpells = Array.from(mergedSpellsMap.values()).sort((a, b) => (a.level - b.level) || a.name.localeCompare(b.name));
        const filteredSpells = allCandidateSpells.filter(s => {
            if (restSpellSearch && !s.name.toLowerCase().includes(restSpellSearch.toLowerCase())) return false;
            if (restSpellLevelFilter !== "" && Number(s.level) !== parseInt(restSpellLevelFilter, 10)) return false;
            return true;
        });

        const preparedLeveledCount = (draft.prepared_spell_names || []).filter(n => !alwaysPreparedCountedOut.has(n.toLowerCase())).length;
        const maxPrep = Number(sc.max_prepared_leveled_spells || 1);
        const capColor = preparedLeveledCount > maxPrep ? "#f87171" : "#4ade80";

        const maxSlotLvl = Math.max(1, Number(sc.max_slot_level || 1));
        const lvlOpts = [`<option value="">All Levels</option>`, `<option value="0" ${restSpellLevelFilter === "0" ? "selected" : ""}>Cantrips</option>`];
        for (let l = 1; l <= maxSlotLvl; l++) {
            lvlOpts.push(`<option value="${l}" ${restSpellLevelFilter === String(l) ? "selected" : ""}>Level ${l}</option>`);
        }

        spellsSectionHtml = `
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>✨ Spells &amp; Preparation (${escapeRpHtml(sc.primary_class || "Caster")} • ${escapeRpHtml(ed)} Rules)</span>
                    <span id="rest-spell-cap-badge" style="color:${capColor};">${preparedLeveledCount} / ${maxPrep} Prepared</span>
                </div>
                <div style="font-size:0.74rem; color:#94a3b8;">
                    ${isLong
                        ? (sc.can_swap_from_class_list ? `Your ${escapeRpHtml(sc.primary_class)} knows their full class list—check up to ${maxPrep} leveled spells to prepare today.` : `Toggle prepared leveled spells from your spellbook/sheet.`)
                        : `Short Rest: Pact slots recharge automatically. (Tip: Switch to Long Rest to re-prepare daily spells!)`}
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <input type="text" id="input-rest-spell-search" class="modal-input comp-search-input" value="${escapeRpHtml(restSpellSearch)}" placeholder="Filter spells (e.g. Bless, Cure Wounds, Fireball)..." />
                    <select id="select-rest-spell-level" class="modal-input comp-select">${lvlOpts.join("")}</select>
                </div>
                <div class="rest-spell-grid" id="rest-spell-grid">
                    ${filteredSpells.map((sp) => {
                        const isLocked = Boolean(sp.is_cantrip || sp.is_always_prepared);
                        const isChecked = isLocked || prepSet.has(sp.name.toLowerCase());
                        const lvlTxt = sp.is_cantrip ? "Cantrip" : `L${sp.level}`;
                        return `
                            <label class="rest-spell-card ${isChecked ? "prepared" : ""} ${isLocked ? "locked" : ""}">
                                <input type="checkbox" class="rest-spell-chk" data-spell-name="${escapeRpHtml(sp.name)}" ${isChecked ? "checked" : ""} ${isLocked ? "disabled" : ""} />
                                <div style="min-width:0;">
                                    <div style="font-weight:700; color:#f1f5f9;">${escapeRpHtml(sp.name)}</div>
                                    <div style="font-size:0.68rem; color:#94a3b8;">${lvlTxt} ${escapeRpHtml(sp.school || "")}${sp.is_cantrip ? " • At-Will" : (sp.is_always_prepared ? " • Always Prepared" : "")}</div>
                                </div>
                            </label>
                        `;
                    }).join("") || `<div style="color:#64748b; font-size:0.78rem; padding:10px;">No spells match filter.</div>`}
                </div>
            </div>
        `;
    }

    bodyEl.innerHTML = `
        <div class="rest-grid-2">
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>❤️ Vitals &amp; Hit Dice</span>
                    <span style="color:#f1f5f9;">${ch.hp.current} / ${ch.hp.max} HP</span>
                </div>
                ${hdHtml}
                ${(ch.expendable_meters || []).length > 0 ? `
                    <div style="margin-top:4px; font-size:0.72rem; color:#94a3b8;">
                        <strong>Class Pools:</strong>
                        ${(ch.expendable_meters || []).map(m => `${escapeRpHtml(m.label)}: ${m.current}/${m.max} (${escapeRpHtml(m.reset_on)})`).join(" • ")}
                    </div>
                ` : ""}
            </div>

            <div class="rest-section-card">
                <div class="rest-section-title"><span>🧪 Consumables &amp; Potions</span></div>
                ${consHtml}
            </div>
        </div>

        <div class="rest-grid-2">
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>🛡️ Armor &amp; Shield Swap</span>
                    <span id="rest-projected-ac-badge" class="comp-pill comp-pill-ed">Projected AC: ${acInfo.ac}</span>
                </div>
                <div>
                    <label class="modal-label">Body Armor</label>
                    <select id="select-rest-armor" class="modal-input">${armorOptions}</select>
                </div>
                <label style="display:flex; align-items:center; gap:7px; font-size:0.78rem; color:#cbd5e1; cursor:pointer;">
                    <input type="checkbox" id="chk-rest-shield" ${draft.shield_equipped ? "checked" : ""} ${!hasShieldInBag ? "disabled" : ""} />
                    Equip Shield (+2 AC)${!hasShieldInBag ? " (none in inventory)" : ""}
                </label>
                ${acInfo.warnings.length ? `<div style="font-size:0.72rem; color:#fbbf24;">⚠️ ${escapeRpHtml(acInfo.warnings.join(" • "))}</div>` : ""}
            </div>

            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>🔗 Magic Item Attunement</span>
                    <span id="rest-attune-count-badge" style="color:${(draft.attuned_items || []).length > 3 ? "#f87171" : "#38bdf8"};">${(draft.attuned_items || []).length} / 3 Attuned</span>
                </div>
                ${attHtml}
            </div>
        </div>

        ${spellsSectionHtml}
    `;

    // Wire dynamic hero controls
    document.getElementById("rest-hd-spend-input")?.addEventListener("input", (e) => {
        const val = Math.max(0, Math.min(ch.hit_dice.current, parseInt(e.target.value || "0", 10) || 0));
        draft.hit_dice_to_spend = val;
    });
    document.getElementById("rest-manual-hp-input")?.addEventListener("input", (e) => {
        draft.manual_hp_gain = Math.max(0, parseInt(e.target.value || "0", 10) || 0);
    });
    bodyEl.querySelectorAll(".rest-cons-qty-input").forEach(inp => {
        inp.addEventListener("input", () => {
            const idx = parseInt(inp.getAttribute("data-cons-idx"), 10);
            const consItem = (ch.consumables || [])[idx];
            if (!consItem) return;
            const qty = Math.max(0, Math.min(consItem.qty, parseInt(inp.value || "0", 10) || 0));
            if (qty > 0) draft.consumables_used[consItem.name] = qty;
            else delete draft.consumables_used[consItem.name];
        });
    });
    document.getElementById("select-rest-armor")?.addEventListener("change", (e) => {
        draft.equipped_armor = e.target.value || "";
        renderRestHeroWorkspace();
    });
    document.getElementById("chk-rest-shield")?.addEventListener("change", (e) => {
        draft.shield_equipped = Boolean(e.target.checked);
        renderRestHeroWorkspace();
    });
    bodyEl.querySelectorAll(".rest-attune-chk").forEach(chk => {
        chk.addEventListener("change", () => {
            const idx = parseInt(chk.getAttribute("data-att-idx"), 10);
            const attItem = (ch.attunable_items || [])[idx];
            if (!attItem) return;
            const cur = draft.attuned_items || [];
            if (chk.checked) {
                if (cur.length >= 3) {
                    chk.checked = false;
                    const fb = document.getElementById("rest-feedback");
                    if (fb) { fb.style.color = "#f87171"; fb.textContent = `${ch.name} can only attune to 3 magic items! Uncheck one first.`; }
                    return;
                }
                if (!cur.some(n => n.toLowerCase() === attItem.name.toLowerCase())) {
                    cur.push(attItem.name);
                }
            } else {
                draft.attuned_items = cur.filter(n => n.toLowerCase() !== attItem.name.toLowerCase());
            }
            renderRestHeroWorkspace();
        });
    });
    document.getElementById("input-rest-spell-search")?.addEventListener("input", (e) => {
        restSpellSearch = e.target.value || "";
        renderRestHeroWorkspace();
        const searchEl = document.getElementById("input-rest-spell-search");
        if (searchEl) {
            searchEl.focus();
            searchEl.setSelectionRange(restSpellSearch.length, restSpellSearch.length);
        }
    });
    document.getElementById("select-rest-spell-level")?.addEventListener("change", (e) => {
        restSpellLevelFilter = e.target.value || "";
        renderRestHeroWorkspace();
    });
    bodyEl.querySelectorAll(".rest-spell-chk").forEach(chk => {
        chk.addEventListener("change", () => {
            const spName = chk.getAttribute("data-spell-name");
            if (!spName) return;
            const list = draft.prepared_spell_names || [];
            if (chk.checked) {
                if (!list.some(n => n.toLowerCase() === spName.toLowerCase())) {
                    list.push(spName);
                }
            } else {
                draft.prepared_spell_names = list.filter(n => n.toLowerCase() !== spName.toLowerCase());
            }
            renderRestHeroWorkspace();
        });
    });
}

async function commitInteractiveRestFromModal() {
    const fb = document.getElementById("rest-feedback");
    if (!window.pywebview?.api?.commit_party_rest) {
        if (fb) fb.textContent = "Rest backend unavailable.";
        return;
    }

    const ed = document.getElementById("select-rest-edition")?.value || "2024";
    const charactersPayload = {};
    Object.entries(restDraftByChar).forEach(([cid, d]) => {
        const consArr = Object.entries(d.consumables_used || {})
            .filter(([, q]) => q > 0)
            .map(([name, qty]) => ({ name, qty }));
        charactersPayload[cid] = {
            hit_dice_to_spend: d.hit_dice_to_spend || 0,
            manual_hp_gain: d.manual_hp_gain || 0,
            consumables_used: consArr,
            equipped_armor: d.equipped_armor || "",
            shield_equipped: Boolean(d.shield_equipped),
            attuned_items: d.attuned_items || [],
            prepared_spell_names: d.prepared_spell_names || [],
        };
    });

    try {
        const res = await window.pywebview.api.commit_party_rest({
            rest_type: activeRestType,
            dice_mode: restModalData?.dice_mode || "digital",
            edition: ed,
            characters: charactersPayload,
        });
        if (res && res.status === "success") {
            closeRestModal();
            await loadRolePlayUI(true);
            if (typeof renderRolePlayStoryBubble === "function") {
                renderRolePlayStoryBubble({ type: "system", content: `⛺ ${res.summary}` });
            }
        } else if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = res?.message || "Failed applying rest.";
        }
    } catch (e) {
        if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = e.message;
        }
    }
}

window.handleRoleplayRestOpened = function(data) {
    openRestModal(data || {});
};

// ============================================================================
// Phase 3: Guided Level-Up & Multiclass Wizard (2014 & 2024 D&D 5e + Non-D&D)
// ============================================================================
const LEVELUP_ABIL_ORDER = ["STR", "DEX", "CON", "INT", "WIS", "CHA"];
const LEVELUP_ABIL_LABELS = {
    STR: "Strength",
    DEX: "Dexterity",
    CON: "Constitution",
    INT: "Intelligence",
    WIS: "Wisdom",
    CHA: "Charisma",
};

let levelUpModalState = null;
let levelUpPreviewData = null;

function _formatModifier(score) {
    const mod = Math.floor(((Number(score) || 10) - 10) / 2);
    return mod >= 0 ? `+${mod}` : `${mod}`;
}

function _computeProjectedWizardStats(preview) {
    const base = { ...(preview?.ability_scores || {}) };
    LEVELUP_ABIL_ORDER.forEach(ab => {
        if (base[ab] === undefined) base[ab] = 10;
    });
    const st = levelUpModalState || {};
    const asiApplied = {};
    let featObj = null;

    if (preview?.is_asi_level) {
        if (st.asiMode === "asi2") {
            const ab = st.asiMain || "STR";
            const capped = Math.min(20, (base[ab] || 10) + 2);
            const delta = capped - (base[ab] || 10);
            if (delta > 0) asiApplied[ab] = delta;
            base[ab] = capped;
        } else if (st.asiMode === "asi11") {
            const a = st.asiA || "STR";
            const b = st.asiB || "DEX";
            if (a === b) {
                const capped = Math.min(20, (base[a] || 10) + 2);
                const delta = capped - (base[a] || 10);
                if (delta > 0) asiApplied[a] = delta;
                base[a] = capped;
            } else {
                const capA = Math.min(20, (base[a] || 10) + 1);
                const capB = Math.min(20, (base[b] || 10) + 1);
                if (capA > base[a]) asiApplied[a] = capA - base[a];
                if (capB > base[b]) asiApplied[b] = capB - base[b];
                base[a] = capA;
                base[b] = capB;
            }
        } else if (st.asiMode === "feat") {
            featObj = (preview.feats_available || []).find(f => f.name === st.selectedFeat) || null;
            if (featObj) {
                const isHalfIn2014Only2024 = (
                    featObj.type === "half_feat"
                    && preview.edition === "2014"
                    && featObj.name !== "Resilient"
                );
                const bonusAmt = isHalfIn2014Only2024 ? 0 : (Number(featObj.ability_increase) || 0);
                const choices = featObj.ability_choices || [];
                const cap = (featObj.cap || (featObj.type === "epic_boon" ? 30 : 20));
                if (bonusAmt > 0 && choices.length > 0) {
                    const picked = choices.includes(st.featAbility) ? st.featAbility : choices[0];
                    const nextVal = Math.min(cap, (base[picked] || 10) + bonusAmt);
                    const delta = nextVal - (base[picked] || 10);
                    if (delta > 0) asiApplied[picked] = delta;
                    base[picked] = nextVal;
                }
            }
        }
    }

    const oldConMod = Math.floor(((preview?.ability_scores?.CON || 10) - 10) / 2);
    const newConMod = Math.floor(((base.CON || 10) - 10) / 2);
    const retroConHp = (newConMod - oldConMod) * (preview?.current_total_level || 0);
    const hitDie = preview?.selected_hit_die || 8;
    const fixedDieGain = Math.floor(hitDie / 2) + 1;
    let dieResult = fixedDieGain;
    if (st.hpMode === "rolled") {
        dieResult = Math.max(1, Math.min(hitDie, Number(st.rolledDieValue) || fixedDieGain));
    } else if (st.hpMode === "manual") {
        dieResult = Math.max(1, Math.min(hitDie, Number(st.manualDieValue) || fixedDieGain));
    }
    const levelHpGain = Math.max(1, dieResult + newConMod);

    let featHp = 0;
    if (featObj) {
        featHp += Number(featObj.hp_bonus_flat || 0);
        featHp += Number(featObj.hp_bonus_per_level || 0) * (preview?.next_total_level || 1);
    }

    return {
        projectedScores: base,
        asiApplied,
        featObj,
        oldConMod,
        newConMod,
        retroConHp,
        hitDie,
        dieResult,
        levelHpGain,
        featHp,
        totalHpGain: Math.max(1, levelHpGain + featHp) + retroConHp,
    };
}

async function openLevelUpWizard(charIdOrName, source = "campaign", opts = {}) {
    const modal = document.getElementById("levelup-wizard-modal");
    if (!modal || !charIdOrName) return;

    const fb = document.getElementById("levelup-feedback");
    if (fb) fb.textContent = "";

    const activeEd = opts.edition || ttrpgData.active_campaign_meta?.ruleset_edition || ttrpgData.state?.ruleset_edition || "2024";
    const edSel = document.getElementById("select-levelup-edition");
    if (edSel && (activeEd === "2014" || activeEd === "2024")) edSel.value = activeEd;

    const reasonEl = document.getElementById("levelup-reason-banner");
    if (reasonEl) {
        if (opts.reason) {
            reasonEl.textContent = `📜 Milestone: ${opts.reason}`;
            reasonEl.classList.remove("hidden");
        } else {
            reasonEl.classList.add("hidden");
        }
    }

    const bypassChk = document.getElementById("chk-levelup-bypass-prereqs");
    if (bypassChk) bypassChk.checked = false;

    levelUpModalState = {
        charIdOrName: String(charIdOrName),
        source: source === "vault" ? "vault" : "campaign",
        edition: edSel ? edSel.value : (activeEd === "2014" ? "2014" : "2024"),
        targetClass: opts.target_class || null,
        bypassPrereqs: false,
        hpMode: "fixed",
        rolledDieValue: null,
        manualDieValue: 5,
        subclassChoice: "",
        subclassCustom: "",
        asiMode: "asi2",
        asiMain: "STR",
        asiA: "STR",
        asiB: "DEX",
        selectedFeat: "Tough",
        featAbility: "CON",
        selectedSpells: [],
        spellSearch: "",
        spellLevelFilter: "",
    };

    modal.classList.remove("hidden");
    await loadLevelUpWizardOptions();
}

function closeLevelUpWizard() {
    const modal = document.getElementById("levelup-wizard-modal");
    if (modal) modal.classList.add("hidden");
    levelUpModalState = null;
    levelUpPreviewData = null;
}

async function loadLevelUpWizardOptions() {
    const fb = document.getElementById("levelup-feedback");
    if (!levelUpModalState || !window.pywebview?.api?.get_character_advancement_options) {
        if (fb) fb.textContent = "Advancement backend unavailable.";
        return;
    }

    const st = levelUpModalState;
    try {
        const res = await window.pywebview.api.get_character_advancement_options(
            st.charIdOrName,
            st.edition,
            st.targetClass,
            st.source,
            Boolean(st.bypassPrereqs)
        );
        if (!res || res.status !== "success") {
            if (fb) {
                fb.style.color = "#f87171";
                fb.textContent = res?.message || "Failed loading level-up options.";
            }
            return;
        }
        levelUpPreviewData = res;
        if (res.is_dnd !== false) {
            st.edition = res.edition || st.edition;
            st.targetClass = res.selected_class || st.targetClass;
            if (!st.rolledDieValue) {
                st.rolledDieValue = Math.floor((res.selected_hit_die || 8) / 2) + 1;
                st.manualDieValue = st.rolledDieValue;
            }
            if (st.spellLevelFilter === "" && res.max_new_cantrips === 0 && (res.max_new_leveled_spells || 0) > 0) {
                st.spellLevelFilter = "1";
            }
            const firstOpt = (res.subclass_options || [])[0] || "";
            if (res.needs_subclass && (!st.subclassChoice || !(res.subclass_options || []).includes(st.subclassChoice))) {
                st.subclassChoice = firstOpt;
            }
            const feats = res.feats_available || [];
            if (feats.length && !feats.some(f => f.name === st.selectedFeat)) {
                const defaultFeat = feats.find(f => f.type !== "asi") || feats[0];
                st.selectedFeat = defaultFeat.name;
            }
        }
        renderLevelUpWizard();
    } catch (e) {
        console.error("[LevelUpWizard] Error loading options:", e);
        if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = e.message || "Error loading advancement options.";
        }
    }
}

function renderLevelUpWizard() {
    const p = levelUpPreviewData;
    const st = levelUpModalState;
    if (!p || !st) return;

    const titleEl = document.getElementById("levelup-modal-title");
    const subEl = document.getElementById("levelup-modal-subtitle");
    const edSel = document.getElementById("select-levelup-edition");
    const xpBadge = document.getElementById("levelup-xp-badge");
    const xpAwardWrap = document.getElementById("levelup-xp-award-group");
    const bypassWrap = document.getElementById("levelup-bypass-wrap");
    const dndBody = document.getElementById("levelup-dnd-body");
    const nonDndBody = document.getElementById("levelup-nondnd-body");
    const commitBtn = document.getElementById("btn-commit-levelup");

    if (titleEl) titleEl.textContent = `⭐ Advance ${p.character_name || "Adventurer"}`;
    if (xpAwardWrap) xpAwardWrap.style.display = st.source === "campaign" ? "flex" : "none";

    // Handle non-D&D rulesets cleanly
    if (p.is_dnd === false) {
        if (subEl) subEl.textContent = `${p.archetype || p.ruleset_label || "Hero"} • Level ${p.current_total_level} → Level ${p.next_total_level}`;
        if (edSel) edSel.style.display = "none";
        if (bypassWrap) bypassWrap.style.display = "none";
        if (xpBadge) xpBadge.textContent = `XP: ${p.experience_points || 0}`;
        dndBody?.classList.add("hidden");
        nonDndBody?.classList.remove("hidden");

        const rsTitle = document.getElementById("levelup-nondnd-ruleset-title");
        const lvlBadge = document.getElementById("levelup-nondnd-level-badge");
        const attrSel = document.getElementById("select-nondnd-attr");
        if (rsTitle) rsTitle.textContent = `🎲 ${p.ruleset_label || p.ruleset || "TTRPG"} Progression`;
        if (lvlBadge) lvlBadge.textContent = `Lv. ${p.current_total_level} → Lv. ${p.next_total_level}`;
        if (attrSel) {
            const keys = Object.keys(p.attributes || {});
            attrSel.innerHTML = `<option value="">-- No Attribute Change --</option>` +
                keys.map(k => `<option value="${escapeRpHtml(k)}">${escapeRpHtml(k)} (${p.attributes[k]})</option>`).join("");
        }
        if (commitBtn) {
            commitBtn.disabled = false;
            commitBtn.textContent = `⬆️ Advance to Level ${p.next_total_level}`;
        }
        return;
    }

    // D&D 5e Guided Wizard UI
    if (edSel) edSel.style.display = "";
    if (bypassWrap) bypassWrap.style.display = "flex";
    dndBody?.classList.remove("hidden");
    nonDndBody?.classList.add("hidden");

    if (edSel && p.edition) edSel.value = p.edition;
    if (subEl) {
        subEl.textContent = p.can_advance
            ? `${p.current_class_summary} → ${p.next_class_summary} (Total Level ${p.next_total_level} • ${p.edition} Rules)`
            : `${p.current_class_summary} (Maximum Level 20 Reached)`;
    }
    if (xpBadge) {
        const xpReady = p.xp_ready ? " • READY!" : "";
        xpBadge.textContent = p.can_advance
            ? `XP: ${Number(p.experience_points || 0).toLocaleString()} / ${Number(p.next_level_xp || 0).toLocaleString()}${xpReady}`
            : `XP: ${Number(p.experience_points || 0).toLocaleString()} (MAX)`;
        xpBadge.style.borderColor = p.xp_ready ? "rgba(74, 222, 128, 0.6)" : "rgba(56, 189, 248, 0.35)";
        xpBadge.style.color = p.xp_ready ? "#4ade80" : "#38bdf8";
    }

    // Render Class Choice List (Left Pane)
    const classListEl = document.getElementById("levelup-class-list");
    if (classListEl) {
        const allOpts = p.class_options || [];
        const existingOpts = allOpts.filter(o => o.is_existing_class);
        const multiOpts = allOpts.filter(o => !o.is_existing_class);

        const renderCard = (opt) => {
            const isSelected = opt.name === st.targetClass;
            const locked = !opt.eligible;
            const statusText = opt.is_existing_class
                ? `Lv. ${opt.current_class_level} → Lv. ${opt.next_class_level}`
                : `Multiclass → Lv. 1`;
            const prereqHtml = opt.eligible
                ? (opt.bypassed ? `<span style="color:#fbbf24; font-size:0.68rem;">🔓 Prereq Overridden</span>` : `<span style="color:#4ade80; font-size:0.68rem;">✅ Eligible • d${opt.hit_die} (+${opt.fixed_hp_gain} HP)</span>`)
                : `<span style="color:#f87171; font-size:0.68rem;">🔒 ${escapeRpHtml((opt.missing_prereqs || []).join("; "))}</span>`;
            const tags = [];
            if (opt.needs_subclass) tags.push(`<span class="comp-pill" style="font-size:0.62rem;">Subclass</span>`);
            if (opt.is_epic_boon_level) tags.push(`<span class="comp-pill comp-pill-ed" style="font-size:0.62rem;">Epic Boon</span>`);
            else if (opt.is_asi_level) tags.push(`<span class="comp-pill" style="font-size:0.62rem;">ASI / Feat</span>`);
            return `
                <button type="button" class="levelup-class-card ${isSelected ? "selected" : ""} ${locked ? "locked" : ""}" data-lu-class="${escapeRpHtml(opt.name)}">
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; width:100%;">
                        <strong style="font-size:0.84rem; color:#f8fafc;">${escapeRpHtml(opt.name)}</strong>
                        <span style="font-size:0.72rem; color:#38bdf8; font-weight:600;">${statusText}</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:4px; flex-wrap:wrap;">
                        ${prereqHtml}
                        <span style="display:inline-flex; gap:3px;">${tags.join("")}</span>
                    </div>
                </button>
            `;
        };

        let clsHtml = `<div class="levelup-class-group-hdr">Advance Existing Class</div>`;
        clsHtml += existingOpts.map(renderCard).join("") || `<div style="font-size:0.75rem; color:#64748b; padding:4px;">None</div>`;
        clsHtml += `<div class="levelup-class-group-hdr" style="margin-top:10px;">Multiclass Into New Class</div>`;
        clsHtml += multiOpts.map(renderCard).join("");
        classListEl.innerHTML = clsHtml;

        classListEl.querySelectorAll(".levelup-class-card").forEach(btn => {
            btn.addEventListener("click", async () => {
                const chosen = btn.getAttribute("data-lu-class");
                if (!chosen || chosen === st.targetClass) return;
                st.targetClass = chosen;
                st.rolledDieValue = null;
                st.selectedSpells = [];
                st.subclassChoice = "";
                st.subclassCustom = "";
                await loadLevelUpWizardOptions();
            });
        });
    }

    // Right Pane Details
    const detailEl = document.getElementById("levelup-wizard-body");
    if (!detailEl) return;

    if (!p.can_advance) {
        detailEl.innerHTML = `<div class="rest-section-card"><div class="rest-section-title">🏆 Maximum Level 20 Reached</div><p style="margin:0; font-size:0.84rem; color:#cbd5e1;">${escapeRpHtml(p.character_name)} is already at D&amp;D 5e Level 20.</p></div>`;
        if (commitBtn) commitBtn.disabled = true;
        return;
    }

    const proj = _computeProjectedWizardStats(p);
    const prereqOk = p.selected_prereq?.eligible !== false;
    if (commitBtn) {
        commitBtn.disabled = !prereqOk;
        commitBtn.textContent = `✨ Confirm ${p.selected_class} ${p.selected_class_next_level}`;
    }

    // Prereq warning banner if ineligible
    const prereqWarningHtml = !prereqOk ? `
        <div style="padding:10px 12px; border-radius:7px; background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.4); color:#fca5a5; font-size:0.78rem;">
            <strong>⚠️ Multiclass Prerequisites Not Met:</strong> ${escapeRpHtml((p.selected_prereq?.missing || []).join("; "))}.
            Check <em>"Override Multiclass Prereqs"</em> above if your DM permits this multiclass.
        </div>
    ` : "";

    // Hit Points section
    const fixedAvg = Math.floor(proj.hitDie / 2) + 1;
    let hpExtraHtml = "";
    if (st.hpMode === "rolled") {
        hpExtraHtml = `
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:4px;">
                <button type="button" id="btn-lu-roll-hp" class="btn-secondary-sm" style="padding:5px 12px;">🎲 Roll d${proj.hitDie}</button>
                <span style="font-size:0.8rem; color:#e2e8f0;">Rolled Die: <strong style="color:#38bdf8;">${proj.dieResult}</strong> + CON (${proj.newConMod >= 0 ? "+" : ""}${proj.newConMod}) = <strong style="color:#4ade80;">+${proj.levelHpGain} HP</strong></span>
            </div>
        `;
    } else if (st.hpMode === "manual") {
        hpExtraHtml = `
            <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:4px;">
                <label class="modal-label" for="input-lu-manual-roll" style="margin:0;">Physical Die Roll (1–${proj.hitDie}):</label>
                <input type="number" id="input-lu-manual-roll" class="modal-input" min="1" max="${proj.hitDie}" value="${proj.dieResult}" style="width:78px; padding:4px 8px;" />
                <span style="font-size:0.78rem; color:#94a3b8;">+ CON (${proj.newConMod >= 0 ? "+" : ""}${proj.newConMod}) = <strong id="lu-hp-manual-gain" style="color:#4ade80;">+${proj.levelHpGain} HP</strong></span>
            </div>
        `;
    } else {
        hpExtraHtml = `
            <div style="font-size:0.78rem; color:#94a3b8; margin-top:4px;">
                Fixed Average (${fixedAvg} on d${proj.hitDie}) + CON (${proj.newConMod >= 0 ? "+" : ""}${proj.newConMod}) = <strong style="color:#4ade80;">+${proj.levelHpGain} HP</strong>
            </div>
        `;
    }
    const retroNotice = proj.retroConHp > 0
        ? `<div style="font-size:0.74rem; color:#4ade80;">+${proj.retroConHp} Retroactive CON HP (+${proj.newConMod - proj.oldConMod} CON mod × ${p.current_total_level} prior levels)</div>`
        : "";
    const featHpNotice = proj.featHp > 0
        ? `<div style="font-size:0.74rem; color:#4ade80;">+${proj.featHp} Bonus HP from Feat (${escapeRpHtml(proj.featObj?.name || "")})</div>`
        : "";

    // Subclass Section
    let subclassHtml = "";
    if (p.needs_subclass) {
        const subOpts = (p.subclass_options || []).map(sc =>
            `<option value="${escapeRpHtml(sc)}" ${st.subclassChoice === sc ? "selected" : ""}>${escapeRpHtml(sc)}</option>`
        ).join("");
        subclassHtml = `
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>🛡️ 2. Choose ${escapeRpHtml(p.subclass_title || "Subclass")}</span>
                    <span class="comp-pill comp-pill-ed">${escapeRpHtml(p.selected_class)} Lv. ${p.selected_class_next_level}</span>
                </div>
                <select id="select-lu-subclass" class="modal-input">
                    ${subOpts}
                    <option value="__custom__" ${st.subclassChoice === "__custom__" ? "selected" : ""}>Custom / Homebrew Subclass...</option>
                </select>
                <input type="text" id="input-lu-subclass-custom" class="modal-input ${st.subclassChoice === "__custom__" ? "" : "hidden"}" value="${escapeRpHtml(st.subclassCustom || "")}" placeholder="Enter custom subclass name..." />
            </div>
        `;
    }

    // ASI / Feat / Epic Boon Section
    let asiHtml = "";
    if (p.is_asi_level) {
        const abOpts = (selected, list = LEVELUP_ABIL_ORDER) => list.map(ab =>
            `<option value="${ab}" ${selected === ab ? "selected" : ""}>${ab} (${LEVELUP_ABIL_LABELS[ab]}: ${p.ability_scores?.[ab] ?? 10})</option>`
        ).join("");
        const feats = (p.feats_available || []).filter(f => f.type !== "asi");
        const featOpts = feats.map(f => {
            const tag = f.type === "epic_boon" ? " [Epic Boon L19+]" : "";
            return `<option value="${escapeRpHtml(f.name)}" ${st.selectedFeat === f.name ? "selected" : ""}>${escapeRpHtml(f.name)}${tag}</option>`;
        }).join("");
        const selFeatObj = feats.find(f => f.name === st.selectedFeat) || feats[0] || null;
        const isHalfDisabledIn2014 = selFeatObj && selFeatObj.type === "half_feat" && p.edition === "2014" && selFeatObj.name !== "Resilient";
        const featBonusAmt = selFeatObj ? (isHalfDisabledIn2014 ? 0 : (Number(selFeatObj.ability_increase) || 0)) : 0;
        const featChoices = selFeatObj?.ability_choices || [];

        let asiBodyHtml = "";
        if (st.asiMode === "asi2") {
            asiBodyHtml = `
                <div>
                    <label class="modal-label" for="select-lu-asi-main">Increase One Ability by +2 (max 20)</label>
                    <select id="select-lu-asi-main" class="modal-input">${abOpts(st.asiMain)}</select>
                </div>
            `;
        } else if (st.asiMode === "asi11") {
            asiBodyHtml = `
                <div class="rest-grid-2">
                    <div>
                        <label class="modal-label" for="select-lu-asi-a">First Ability (+1)</label>
                        <select id="select-lu-asi-a" class="modal-input">${abOpts(st.asiA)}</select>
                    </div>
                    <div>
                        <label class="modal-label" for="select-lu-asi-b">Second Ability (+1)</label>
                        <select id="select-lu-asi-b" class="modal-input">${abOpts(st.asiB)}</select>
                    </div>
                </div>
            `;
        } else {
            asiBodyHtml = `
                <div>
                    <label class="modal-label" for="select-lu-feat">Choose Feat${p.is_epic_boon_level ? " or Epic Boon (Stat Cap 30)" : ""}</label>
                    <select id="select-lu-feat" class="modal-input">${featOpts}</select>
                    ${selFeatObj ? `<div style="font-size:0.75rem; color:#94a3b8; margin-top:4px;">${escapeRpHtml(selFeatObj.description || "")}</div>` : ""}
                </div>
                ${(featBonusAmt > 0 && featChoices.length > 0) ? `
                    <div>
                        <label class="modal-label" for="select-lu-feat-ability">Bonus +${featBonusAmt} Ability${selFeatObj?.grants_save_prof ? " (+ Saving Throw Proficiency)" : ""}</label>
                        <select id="select-lu-feat-ability" class="modal-input">${abOpts(featChoices.includes(st.featAbility) ? st.featAbility : featChoices[0], featChoices)}</select>
                    </div>
                ` : ""}
            `;
        }

        const statPills = LEVELUP_ABIL_ORDER.map(ab => {
            const oldV = p.ability_scores?.[ab] ?? 10;
            const newV = proj.projectedScores[ab] ?? oldV;
            const changed = newV !== oldV;
            return `<span class="levelup-stat-pill ${changed ? "boosted" : ""}">${ab}: ${changed ? `${oldV} → <strong>${newV}</strong>` : newV} (${_formatModifier(newV)})</span>`;
        }).join("");

        asiHtml = `
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>⚡ ${p.is_epic_boon_level ? "Epic Boon / ASI / Feat (Level 19+)" : "Ability Score Improvement / Feat"}</span>
                    <span class="comp-pill comp-pill-ed">${p.edition} Rules</span>
                </div>
                <div class="shop-mode-toggle" style="width:fit-content;">
                    <button type="button" class="shop-mode-btn lu-asi-mode-btn ${st.asiMode === "asi2" ? "active" : ""}" data-asi-mode="asi2">+2 Ability</button>
                    <button type="button" class="shop-mode-btn lu-asi-mode-btn ${st.asiMode === "asi11" ? "active" : ""}" data-asi-mode="asi11">+1 / +1 Abilities</button>
                    <button type="button" class="shop-mode-btn lu-asi-mode-btn ${st.asiMode === "feat" ? "active" : ""}" data-asi-mode="feat">${p.is_epic_boon_level ? "Feat / Epic Boon" : "Choose Feat"}</button>
                </div>
                <div class="levelup-asi-box">
                    ${asiBodyHtml}
                    <div class="levelup-stat-preview-row">${statPills}</div>
                </div>
            </div>
        `;
    }

    // Class Features & Multiclass Proficiencies Section
    const gainedFeats = p.gained_features || [];
    const featuresHtml = `
        <div class="rest-section-card">
            <div class="rest-section-title">
                <span>📜 Unlocked Features &amp; Proficiencies</span>
                <span style="font-size:0.72rem; color:#94a3b8;">Prof Bonus: +${p.old_proficiency_bonus} → +${p.new_proficiency_bonus}</span>
            </div>
            ${p.selected_multiclass_proficiencies ? `<div style="font-size:0.76rem; color:#38bdf8;"><strong>Multiclass Proficiencies Gained:</strong> ${escapeRpHtml(p.selected_multiclass_proficiencies)}</div>` : ""}
            ${gainedFeats.length ? `
                <div style="display:flex; flex-wrap:wrap; gap:6px;">
                    ${gainedFeats.map(f => `<span class="comp-pill" style="color:#e2e8f0; border-color:rgba(56,189,248,0.35);">✨ ${escapeRpHtml(f)}</span>`).join("")}
                </div>
            ` : `<div style="font-size:0.76rem; color:#64748b;">Spellcasting / HP progression</div>`}
        </div>
    `;

    // Spell Slots & Learnable Spells Picker Section
    const nextSlots = p.next_spell_slots || {};
    const curSlots = p.current_spell_slots || {};
    const slotBadges = [];
    for (let lvl = 1; lvl <= 9; lvl++) {
        const key = `level_${lvl}`;
        const nMax = Number(nextSlots[key]?.max || 0);
        const cMax = Number(curSlots[key]?.max || 0);
        const pMax = Number(nextSlots[key]?.pact_max || 0);
        if (nMax > 0 || cMax > 0 || pMax > 0) {
            const pactNote = pMax > 0 ? ` + ${pMax} Pact` : "";
            const changed = nMax !== cMax;
            slotBadges.push(`<span class="levelup-stat-pill ${changed ? "boosted" : ""}">Lv.${lvl}: ${changed ? `${cMax} → <strong>${nMax}</strong>` : nMax}${pactNote}</span>`);
        }
    }
    if (p.next_pact_slots && !slotBadges.some(b => b.includes("Pact"))) {
        slotBadges.push(`<span class="levelup-stat-pill boosted">Pact Magic: ${p.next_pact_slots.count} × Lv.${p.next_pact_slots.level} (Short Rest)</span>`);
    }

    let spellsSectionHtml = "";
    const learnable = p.learnable_spells || [];
    if (slotBadges.length > 0 || learnable.length > 0) {
        const q = (st.spellSearch || "").trim().toLowerCase();
        const lf = st.spellLevelFilter;
        const filteredSpells = learnable.filter(sp => {
            if (lf !== "" && String(sp.level) !== String(lf)) return false;
            if (q && !sp.name.toLowerCase().includes(q) && !(sp.school || "").toLowerCase().includes(q)) return false;
            return true;
        });

        let lvlOpts = `<option value="">All Levels (0–${p.max_learnable_spell_level || 0})</option>`;
        for (let lv = 0; lv <= (p.max_learnable_spell_level || 0); lv++) {
            const lbl = lv === 0 ? "Cantrips" : `Level ${lv}`;
            lvlOpts += `<option value="${lv}" ${String(lf) === String(lv) ? "selected" : ""}>${lbl}</option>`;
        }

        const maxCantrips = p.max_new_cantrips ?? 0;
        const maxLeveled = p.max_new_leveled_spells ?? 0;
        const maxTotal = p.max_total_new_spells ?? (maxCantrips + maxLeveled);

        // Compute currently selected counts
        const selSpells = st.selectedSpells || [];
        let selCantripsCount = 0;
        let selLeveledCount = 0;
        selSpells.forEach(sName => {
            const sp = learnable.find(x => x.name.toLowerCase() === sName.toLowerCase());
            if (sp && sp.level === 0) selCantripsCount++;
            else selLeveledCount++;
        });

        let quotaBadgeHtml = "";
        if (maxTotal > 0) {
            const parts = [];
            if (maxLeveled > 0) parts.push(`${selLeveledCount}/${maxLeveled} Spells`);
            if (maxCantrips > 0) parts.push(`${selCantripsCount}/${maxCantrips} Cantrips`);
            const isFull = (selLeveledCount === maxLeveled) && (maxCantrips === 0 || selCantripsCount === maxCantrips);
            quotaBadgeHtml = `<span style="color:${isFull ? '#4ade80' : '#c084fc'}; font-size:0.75rem; font-weight:700;">${parts.join(" • ")} Selected</span>`;
        } else if (p.preparation_type === "prepared_full_list") {
            quotaBadgeHtml = `<span style="color:#94a3b8; font-size:0.72rem;">Prepares from full class spell list</span>`;
        } else {
            quotaBadgeHtml = `<span style="color:#94a3b8; font-size:0.72rem;">No new spells this level</span>`;
        }

        spellsSectionHtml = `
            <div class="rest-section-card">
                <div class="rest-section-title">
                    <span>🔮 Spell Slots &amp; Learn New Spells</span>
                    ${quotaBadgeHtml}
                </div>
                ${p.spell_selection_reason ? `<div style="font-size:0.73rem; color:#94a3b8; margin:-2px 0 6px 0; font-style:italic;">💡 ${escapeRpHtml(p.spell_selection_reason)}</div>` : ""}
                ${slotBadges.length ? `<div class="levelup-stat-preview-row">${slotBadges.join("")}</div>` : ""}
                <div id="lu-spells-inline-warning" style="display:none; padding:6px 10px; margin-bottom:6px; background:rgba(239, 68, 68, 0.15); border:1px solid rgba(239, 68, 68, 0.4); border-radius:6px; font-size:0.75rem; color:#fca5a5;"></div>
                ${learnable.length ? `
                    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:4px;">
                        <input type="text" id="input-lu-spell-search" class="modal-input" value="${escapeRpHtml(st.spellSearch || "")}" placeholder="Search ${escapeRpHtml(p.selected_class)} spells (Ctrl+F)..." style="flex:1; min-width:160px; padding:5px 9px; font-size:0.78rem;" />
                        <select id="select-lu-spell-level" class="modal-input comp-select" style="min-width:130px;">${lvlOpts}</select>
                    </div>
                    <div class="rest-spell-grid" style="max-height:210px;">
                        ${filteredSpells.slice(0, 120).map(sp => {
                            const checked = (st.selectedSpells || []).includes(sp.name);
                            const already = Boolean(sp.already_on_sheet);
                            const isCantrip = sp.level === 0;
                            const cantripCapReached = isCantrip && (maxCantrips === 0 || selCantripsCount >= maxCantrips);
                            const leveledCapReached = !isCantrip && (maxLeveled === 0 || selLeveledCount >= maxLeveled);
                            const disableUnchecked = !checked && (already || cantripCapReached || leveledCapReached);

                            let lockReason = "";
                            if (already) lockReason = "Already known on sheet";
                            else if (isCantrip && maxCantrips === 0) lockReason = `${p.selected_class}s do not unlock new cantrips at Level ${p.selected_class_next_level} (next at Level 4)`;
                            else if (isCantrip && cantripCapReached) lockReason = `Maximum cantrips (${maxCantrips}) reached for this level`;
                            else if (!isCantrip && maxLeveled === 0) lockReason = `No new leveled spells unlocked at this level`;
                            else if (!isCantrip && leveledCapReached) lockReason = `Maximum spells (${maxLeveled}) reached for this level`;

                            return `
                                <label class="rest-spell-card ${checked ? "prepared" : ""} ${disableUnchecked ? "locked" : ""}" title="${escapeRpHtml(lockReason || sp.description || "")}" style="${disableUnchecked ? "opacity:0.6; cursor:not-allowed;" : ""}">
                                    <input type="checkbox" class="lu-spell-chk" data-spell-name="${escapeRpHtml(sp.name)}" data-is-cantrip="${isCantrip ? "1" : "0"}" ${checked ? "checked" : ""} ${disableUnchecked ? "disabled" : ""} />
                                    <div style="min-width:0;">
                                        <div style="font-weight:600; color:#f1f5f9;">${escapeRpHtml(sp.name)}${already ? " <span style='color:#38bdf8;font-size:0.65rem;'>(Known)</span>" : ""}</div>
                                        <div style="font-size:0.68rem; color:#94a3b8;">${escapeRpHtml(sp.level_label)} • ${escapeRpHtml(sp.school)}</div>
                                    </div>
                                </label>
                            `;
                        }).join("") || `<div style="padding:10px; color:#64748b; font-size:0.78rem;">No matching spells found.</div>`}
                    </div>
                ` : ""}
            </div>
        `;
    }

    detailEl.innerHTML = `
        <div class="levelup-summary-banner">
            <div>
                <div style="font-size:0.95rem; font-weight:700; color:#f8fafc;">${escapeRpHtml(p.current_class_summary)} → <span style="color:#38bdf8;">${escapeRpHtml(p.next_class_summary)}</span></div>
                <div style="font-size:0.74rem; color:#cbd5e1;">Total Level ${p.current_total_level} → Level ${p.next_total_level} • Hit Die d${proj.hitDie} • Proficiency +${p.old_proficiency_bonus} → +${p.new_proficiency_bonus}</div>
            </div>
            <span id="lu-banner-hp-badge" class="comp-pill comp-pill-ed" style="font-size:0.8rem;">Total HP Gain: +${proj.totalHpGain}</span>
        </div>

        ${prereqWarningHtml}

        <div class="rest-section-card">
            <div class="rest-section-title">
                <span>❤️ Hit Points Increase (d${proj.hitDie})</span>
                <span id="lu-hp-total-badge" style="color:#4ade80;">+${proj.totalHpGain} Max HP</span>
            </div>
            <div class="shop-mode-toggle" style="width:fit-content;">
                <button type="button" class="shop-mode-btn lu-hp-mode-btn ${st.hpMode === "fixed" ? "active" : ""}" data-hp-mode="fixed">📊 Fixed Avg (${fixedAvg})</button>
                <button type="button" class="shop-mode-btn lu-hp-mode-btn ${st.hpMode === "rolled" ? "active" : ""}" data-hp-mode="rolled">🎲 Roll 1d${proj.hitDie}</button>
                <button type="button" class="shop-mode-btn lu-hp-mode-btn ${st.hpMode === "manual" ? "active" : ""}" data-hp-mode="manual">✍️ Manual Die</button>
            </div>
            ${hpExtraHtml}
            ${retroNotice}
            ${featHpNotice}
        </div>

        ${subclassHtml}
        ${asiHtml}
        ${featuresHtml}
        ${spellsSectionHtml}
    `;

    // Wire dynamic D&D wizard controls
    detailEl.querySelectorAll(".lu-hp-mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            st.hpMode = btn.getAttribute("data-hp-mode") || "fixed";
            if (st.hpMode === "rolled" && !st.rolledDieValue) {
                st.rolledDieValue = Math.floor(Math.random() * proj.hitDie) + 1;
            }
            renderLevelUpWizard();
        });
    });
    document.getElementById("btn-lu-roll-hp")?.addEventListener("click", () => {
        st.rolledDieValue = Math.floor(Math.random() * proj.hitDie) + 1;
        renderLevelUpWizard();
    });
    document.getElementById("input-lu-manual-roll")?.addEventListener("input", (e) => {
        const val = parseInt(e.target.value || "1", 10);
        if (!isNaN(val)) {
            st.manualDieValue = Math.max(1, Math.min(proj.hitDie, val));
            const nextProj = _computeProjectedWizardStats(p);
            const banner = document.getElementById("lu-banner-hp-badge");
            const cardBadge = document.getElementById("lu-hp-total-badge");
            const gainEl = document.getElementById("lu-hp-manual-gain");
            if (banner) banner.textContent = `Total HP Gain: +${nextProj.totalHpGain}`;
            if (cardBadge) cardBadge.textContent = `+${nextProj.totalHpGain} Max HP`;
            if (gainEl) gainEl.textContent = `+${nextProj.levelHpGain} HP`;
        }
    });
    document.getElementById("select-lu-subclass")?.addEventListener("change", (e) => {
        st.subclassChoice = e.target.value || "";
        renderLevelUpWizard();
    });
    document.getElementById("input-lu-subclass-custom")?.addEventListener("input", (e) => {
        st.subclassCustom = e.target.value || "";
    });
    detailEl.querySelectorAll(".lu-asi-mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            st.asiMode = btn.getAttribute("data-asi-mode") || "asi2";
            renderLevelUpWizard();
        });
    });
    document.getElementById("select-lu-asi-main")?.addEventListener("change", (e) => {
        st.asiMain = e.target.value;
        renderLevelUpWizard();
    });
    document.getElementById("select-lu-asi-a")?.addEventListener("change", (e) => {
        st.asiA = e.target.value;
        renderLevelUpWizard();
    });
    document.getElementById("select-lu-asi-b")?.addEventListener("change", (e) => {
        st.asiB = e.target.value;
        renderLevelUpWizard();
    });
    document.getElementById("select-lu-feat")?.addEventListener("change", (e) => {
        st.selectedFeat = e.target.value;
        const chosenF = (p.feats_available || []).find(f => f.name === st.selectedFeat);
        if (chosenF?.ability_choices?.length && !chosenF.ability_choices.includes(st.featAbility)) {
            st.featAbility = chosenF.ability_choices[0];
        }
        renderLevelUpWizard();
    });
    document.getElementById("select-lu-feat-ability")?.addEventListener("change", (e) => {
        st.featAbility = e.target.value;
        renderLevelUpWizard();
    });
    document.getElementById("input-lu-spell-search")?.addEventListener("input", (e) => {
        st.spellSearch = e.target.value || "";
        renderLevelUpWizard();
        const inp = document.getElementById("input-lu-spell-search");
        if (inp) {
            inp.focus();
            inp.setSelectionRange(st.spellSearch.length, st.spellSearch.length);
        }
    });
    document.getElementById("select-lu-spell-level")?.addEventListener("change", (e) => {
        st.spellLevelFilter = e.target.value || "";
        renderLevelUpWizard();
    });
    detailEl.querySelectorAll(".lu-spell-chk").forEach(chk => {
        chk.addEventListener("change", () => {
            const spName = chk.getAttribute("data-spell-name");
            if (!spName) return;
            const isCantrip = chk.getAttribute("data-is-cantrip") === "1";
            const warnEl = document.getElementById("lu-spells-inline-warning");

            const maxCantrips = p.max_new_cantrips ?? 0;
            const maxLeveled = p.max_new_leveled_spells ?? 0;
            const selSpells = st.selectedSpells || [];
            let curCantrips = 0;
            let curLeveled = 0;
            selSpells.forEach(sName => {
                const sp = (p.learnable_spells || []).find(x => x.name.toLowerCase() === sName.toLowerCase());
                if (sp && sp.level === 0) curCantrips++;
                else curLeveled++;
            });

            if (chk.checked) {
                if (isCantrip && maxCantrips === 0) {
                    chk.checked = false;
                    if (warnEl) {
                        warnEl.style.display = "block";
                        warnEl.textContent = `⚠️ ${p.selected_class}s do not gain new cantrips at Level ${p.selected_class_next_level}. New cantrips unlock at Level 4.`;
                    }
                    return;
                }
                if (isCantrip && curCantrips >= maxCantrips) {
                    chk.checked = false;
                    if (warnEl) {
                        warnEl.style.display = "block";
                        warnEl.textContent = `⚠️ You can only select up to ${maxCantrips} new cantrip(s) for this level.`;
                    }
                    return;
                }
                if (!isCantrip && curLeveled >= maxLeveled) {
                    chk.checked = false;
                    if (warnEl) {
                        warnEl.style.display = "block";
                        warnEl.textContent = `⚠️ You can only select up to ${maxLeveled} new spell(s) for this level.`;
                    }
                    return;
                }
                if (warnEl) warnEl.style.display = "none";
                if (!st.selectedSpells.includes(spName)) st.selectedSpells.push(spName);
            } else {
                if (warnEl) warnEl.style.display = "none";
                st.selectedSpells = st.selectedSpells.filter(n => n !== spName);
            }
            renderLevelUpWizard();
        });
    });
}

async function awardXpFromWizard() {
    const p = levelUpPreviewData;
    const st = levelUpModalState;
    const fb = document.getElementById("levelup-feedback");
    if (!p || !st || st.source !== "campaign") return;
    if (!window.pywebview?.api?.manage_ttrpg_progression) return;

    const amt = parseInt(document.getElementById("input-levelup-award-xp")?.value || "0", 10);
    if (isNaN(amt) || amt <= 0) {
        if (fb) { fb.style.color = "#f87171"; fb.textContent = "Enter a positive XP amount."; }
        return;
    }

    try {
        const res = await window.pywebview.api.manage_ttrpg_progression(p.character_name, "award_xp", amt, null);
        if (res && res.status === "success") {
            if (fb) {
                fb.style.color = "#4ade80";
                fb.textContent = res.message || `Awarded ${amt} XP.`;
            }
            await loadLevelUpWizardOptions();
            await loadRolePlayUI(true);
        } else if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = res?.message || "Failed awarding XP.";
        }
    } catch (e) {
        if (fb) { fb.style.color = "#f87171"; fb.textContent = e.message; }
    }
}

async function commitLevelUpWizard() {
    const p = levelUpPreviewData;
    const st = levelUpModalState;
    const fb = document.getElementById("levelup-feedback");
    if (!p || !st || !window.pywebview?.api) return;

    // Non-D&D submission
    if (p.is_dnd === false) {
        const attrKey = document.getElementById("select-nondnd-attr")?.value || "";
        const boostVal = parseInt(document.getElementById("input-nondnd-boost")?.value || "1", 10) || 0;
        const traitName = (document.getElementById("input-nondnd-trait-name")?.value || "").trim();
        const traitDesc = (document.getElementById("input-nondnd-trait-desc")?.value || "").trim();
        const statBoosts = (attrKey && boostVal > 0) ? { [attrKey]: boostVal } : null;
        const newTraits = traitName ? [{ name: traitName, description: traitDesc || "Granted on level up." }] : null;

        try {
            let res;
            if (st.source === "vault") {
                res = await window.pywebview.api.level_up_vault_character(p.character_id || st.charIdOrName, 1, statBoosts, newTraits);
            } else {
                res = await window.pywebview.api.manage_ttrpg_progression(p.character_name || st.charIdOrName, "level_up", 1, null);
            }
            if (res && res.status === "success") {
                closeLevelUpWizard();
                if (typeof refreshVaultModalUI === "function") await refreshVaultModalUI();
                await loadRolePlayUI(true);
                if (typeof renderRolePlayStoryBubble === "function" && res.message) {
                    renderRolePlayStoryBubble({ type: "system", content: `⭐ ${res.message}` });
                }
            } else if (fb) {
                fb.style.color = "#f87171";
                fb.textContent = res?.message || "Failed advancing character.";
            }
        } catch (e) {
            if (fb) { fb.style.color = "#f87171"; fb.textContent = e.message; }
        }
        return;
    }

    // D&D 5e Guided Wizard Payload
    if (!window.pywebview.api.apply_character_dnd_level_up) {
        if (fb) fb.textContent = "D&D advancement bridge unavailable.";
        return;
    }

    const maxCantrips = p.max_new_cantrips ?? 0;
    const maxLeveled = p.max_new_leveled_spells ?? 0;
    const selSpells = st.selectedSpells || [];
    let selCantripsCount = 0;
    let selLeveledCount = 0;
    selSpells.forEach(sName => {
        const sp = (p.learnable_spells || []).find(x => x.name.toLowerCase() === sName.toLowerCase());
        if (sp && sp.level === 0) selCantripsCount++;
        else selLeveledCount++;
    });

    if (selCantripsCount > maxCantrips) {
        if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = `Too many cantrips selected (${selCantripsCount}/${maxCantrips} allowed).`;
        }
        return;
    }
    if (selLeveledCount > maxLeveled) {
        if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = `Too many spells selected (${selLeveledCount}/${maxLeveled} allowed).`;
        }
        return;
    }

    const proj = _computeProjectedWizardStats(p);
    const payload = {
        edition: st.edition,
        target_class: st.targetClass,
        bypass_prereqs: Boolean(st.bypassPrereqs),
        hp_mode: st.hpMode === "fixed" ? "fixed" : "rolled",
        new_spells: st.selectedSpells || [],
    };
    if (st.hpMode === "rolled" || st.hpMode === "manual") {
        payload.hp_roll = proj.dieResult;
    }
    if (p.needs_subclass) {
        const subName = st.subclassChoice === "__custom__" ? (st.subclassCustom || "").trim() : (st.subclassChoice || "").trim();
        if (subName) payload.subclass = subName;
    }
    if (p.is_asi_level) {
        if (st.asiMode === "feat") {
            payload.feat = st.selectedFeat;
            if (Object.keys(proj.asiApplied).length > 0) {
                payload.ability_increases = proj.asiApplied;
            }
            if (st.selectedFeat === "Resilient" && st.featAbility) {
                payload.resilient_ability = st.featAbility;
            }
        } else {
            payload.feat = "Ability Score Improvement";
            payload.ability_increases = proj.asiApplied;
        }
    }

    try {
        const res = await window.pywebview.api.apply_character_dnd_level_up(
            st.source === "vault" ? (p.character_id || st.charIdOrName) : (p.character_name || st.charIdOrName),
            payload,
            st.source
        );
        if (res && res.status === "success") {
            closeLevelUpWizard();
            if (st.source === "vault" && typeof refreshVaultModalUI === "function") {
                await refreshVaultModalUI();
            }
            await loadRolePlayUI(true);
            if (typeof renderRolePlayStoryBubble === "function" && res.message) {
                renderRolePlayStoryBubble({ type: "system", content: `⭐ ${res.message}` });
            }
        } else if (fb) {
            fb.style.color = "#f87171";
            fb.textContent = res?.message || "Failed applying level-up.";
        }
    } catch (e) {
        if (fb) { fb.style.color = "#f87171"; fb.textContent = e.message; }
    }
}

window.handleRoleplayLevelUpOpened = function(data) {
    if (!data) return;
    openLevelUpWizard(
        data.character_name || data.character_id,
        data.source || "campaign",
        data
    );
};

window.handleRoleplayLevelUpEvent = async function(data) {
    if (!data) return;
    if (data.level_up_ready_characters && Array.isArray(data.level_up_ready_characters)) {
        for (const ch of data.level_up_ready_characters) {
            await checkAndPromptLevelUp(ch.name, ch.experience_points, ch.level);
        }
    } else if (data.character_name) {
        await checkAndPromptLevelUp(data.character_name, data.experience_points, data.level);
    } else if (typeof window.handleRoleplayLevelUpOpened === "function") {
        window.handleRoleplayLevelUpOpened(data);
    }
};

document.addEventListener("DOMContentLoaded", () => {
    // Merchant Shop Modal Bindings
    document.getElementById("open-shop-btn")?.addEventListener("click", () => openShopModal());
    document.getElementById("btn-close-shop-modal")?.addEventListener("click", closeShopModal);
    document.getElementById("btn-close-shop-footer")?.addEventListener("click", closeShopModal);
    document.getElementById("btn-shop-mode-buy")?.addEventListener("click", () => setShopMode("buy"));
    document.getElementById("btn-shop-mode-sell")?.addEventListener("click", () => setShopMode("sell"));

    const presetSel = document.getElementById("select-shop-location-preset");
    const multSlider = document.getElementById("input-shop-multiplier");
    const specSel = document.getElementById("select-shop-specialty");
    if (presetSel && multSlider) {
        presetSel.addEventListener("change", () => {
            const opt = presetSel.options[presetSel.selectedIndex];
            const m = opt?.getAttribute("data-mult");
            const s = opt?.getAttribute("data-spec");
            if (m) multSlider.value = m;
            if (s && specSel) specSel.value = s;
            updateShopMultiplierBadge();
            refreshShopList();
        });
    }
    if (multSlider) {
        multSlider.addEventListener("input", () => updateShopMultiplierBadge());
        multSlider.addEventListener("change", () => refreshShopList());
    }
    document.getElementById("select-shop-character")?.addEventListener("change", () => {
        const name = document.getElementById("select-shop-character")?.value || "";
        const badge = document.getElementById("shop-char-wallet-badge");
        const hero = getActivePartyCharsForSelect().find(c => c.name === name);
        if (badge) badge.textContent = `🪙 Purse: ${formatPurseFromCharacter(hero)}`;
        if (shopMode === "sell") refreshShopList();
        if (selectedShopEntry) renderShopDetail(selectedShopEntry);
    });
    document.getElementById("input-shop-search")?.addEventListener("input", () => {
        if (shopSearchTimer) clearTimeout(shopSearchTimer);
        shopSearchTimer = setTimeout(() => refreshShopList(), 180);
    });
    ["select-shop-specialty", "select-shop-edition", "chk-shop-include-magic"].forEach(id => {
        document.getElementById(id)?.addEventListener("change", () => refreshShopList());
    });

    // Interactive Short / Long Rest Modal Bindings
    document.getElementById("open-rest-btn")?.addEventListener("click", () => openRestModal({ rest_type: "short" }));
    document.getElementById("btn-close-rest-modal")?.addEventListener("click", closeRestModal);
    document.getElementById("btn-cancel-rest")?.addEventListener("click", closeRestModal);
    document.getElementById("btn-rest-type-short")?.addEventListener("click", () => {
        activeRestType = "short";
        updateRestTypeButtonsUI();
        renderRestHeroWorkspace();
    });
    document.getElementById("btn-rest-type-long")?.addEventListener("click", () => {
        activeRestType = "long";
        updateRestTypeButtonsUI();
        renderRestHeroWorkspace();
    });
    document.getElementById("select-rest-edition")?.addEventListener("change", () => reloadRestModalState());
    document.getElementById("btn-commit-rest")?.addEventListener("click", commitInteractiveRestFromModal);

    // Phase 3: Guided Level-Up & Multiclass Wizard Bindings
    document.getElementById("btn-close-levelup-modal")?.addEventListener("click", closeLevelUpWizard);
    document.getElementById("btn-cancel-levelup")?.addEventListener("click", closeLevelUpWizard);
    document.getElementById("select-levelup-edition")?.addEventListener("change", (e) => {
        if (levelUpModalState) {
            levelUpModalState.edition = e.target.value || "2024";
            loadLevelUpWizardOptions();
        }
    });
    document.getElementById("chk-levelup-bypass-prereqs")?.addEventListener("change", (e) => {
        if (levelUpModalState) {
            levelUpModalState.bypassPrereqs = Boolean(e.target.checked);
            loadLevelUpWizardOptions();
        }
    });
    document.getElementById("btn-levelup-award-xp")?.addEventListener("click", awardXpFromWizard);
    document.getElementById("btn-commit-levelup")?.addEventListener("click", commitLevelUpWizard);
});



