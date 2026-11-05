/**
 * ui/static/edit_character_modal.js - Spell Preparation Enforcement
 */

let activeMaxPreparedSpells = 3;

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

function isKnownCasterClass(clsStr) {
  const c = String(clsStr || window.activeCharacterClass || document.getElementById('input-rp-char-archetype')?.value || '').toLowerCase();
  return c.includes('warlock') || c.includes('sorcerer') || c.includes('bard');
}

function setupSpellPreparationHandlers(maxAllowed, classStr) {
  activeMaxPreparedSpells = (typeof maxAllowed === 'number' && maxAllowed >= 0) ? maxAllowed : 3;
  if (typeof classStr === 'string' && classStr.trim()) {
    window.activeCharacterClass = classStr.trim();
  }
  updatePreparedBadge();

  // Remove existing listeners and delegate
  document.removeEventListener('change', handleSpellCheckboxChange);
  document.addEventListener('change', handleSpellCheckboxChange);
}

function handleSpellCheckboxChange(e) {
  if (!e.target || !e.target.classList || !e.target.classList.contains('spell-prep-checkbox')) return;

  const row = e.target.closest('.spell-item-row') || e.target.closest('tr');
  const isCantrip = row && row.getAttribute('data-is-cantrip') === 'true';
  const isAlwaysPrepared = row && row.getAttribute('data-is-always-prepared') === 'true';

  // Cantrips and Subclass Oath/Circle spells cannot be unprepared
  if (isCantrip || isAlwaysPrepared) {
    e.target.checked = true;
    return;
  }

  const idx = parseInt(e.target.getAttribute('data-idx'), 10);

  // IF UNCHECKING: Always allow without obstruction!
  if (!e.target.checked) {
    if (!isNaN(idx) && typeof window.stagedCharacterSpells !== "undefined" && window.stagedCharacterSpells[idx]) {
      window.stagedCharacterSpells[idx].prepared = false;
    }
    updatePreparedBadge();
    return;
  }

  // IF CHECKING: Calculate currently prepared leveled count
  if (!isNaN(idx) && typeof window.stagedCharacterSpells !== "undefined" && Array.isArray(window.stagedCharacterSpells)) {
    const currentlyChecked = window.stagedCharacterSpells.filter((s, sIdx) => {
      if (sIdx === idx || !s) return false;
      const lvl = typeof s === "object" ? String(s.level || "") : "";
      const cant = (s && s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (lvl.toLowerCase().includes("cantrip") || lvl === "0");
      const alwaysPrep = Boolean(s && s.is_always_prepared);
      return !cant && !alwaysPrep && s.prepared === true;
    }).length;

    if (currentlyChecked >= activeMaxPreparedSpells) {
      e.preventDefault();
      e.target.checked = false;
      if (window.stagedCharacterSpells[idx]) {
        window.stagedCharacterSpells[idx].prepared = false;
      }
      alert(`Preparation limit reached! This character can only prepare ${activeMaxPreparedSpells} leveled spells. Oath and Domain spells do not count against this limit.`);
      updatePreparedBadge();
      return;
    }

    if (window.stagedCharacterSpells[idx]) {
      window.stagedCharacterSpells[idx].prepared = true;
    }
    updatePreparedBadge();
    return;
  }

  const checkedLeveled = getCheckedLeveledCount();

  if (checkedLeveled > activeMaxPreparedSpells) {
    e.preventDefault();
    e.target.checked = false;
    alert(`Preparation limit reached! This character can only prepare ${activeMaxPreparedSpells} leveled spells. Oath and Domain spells do not count against this limit.`);
    return;
  }

  updatePreparedBadge();
}

function getCheckedLeveledCount() {
  // If staged array is available, treat as single source of truth
  if (typeof window.stagedCharacterSpells !== "undefined" && Array.isArray(window.stagedCharacterSpells)) {
    return window.stagedCharacterSpells.filter(s => {
      if (!s) return false;
      const lvl = typeof s === "object" ? String(s.level || "") : "";
      const cant = (s && s.is_cantrip !== undefined) ? Boolean(s.is_cantrip) : (lvl.toLowerCase().includes("cantrip") || lvl === "0");
      const alwaysPrep = Boolean(s && s.is_always_prepared);
      return !cant && !alwaysPrep && s.prepared === true;
    }).length;
  }

  // Fallback: Query DOM
  const container = getActiveSpellsContainer() || document.getElementById("dnd-spells-chips-container") || document;
  const rows = container.querySelectorAll('.spell-item-row, .spell-row');
  let count = 0;
  rows.forEach(r => {
    const isCantrip = r.getAttribute('data-is-cantrip') === 'true';
    const isAlwaysPrep = r.getAttribute('data-is-always-prepared') === 'true';
    const cb = r.querySelector('.spell-prep-checkbox');

    // ONLY tally user-prepared leveled spells; exclude cantrips and oath/circle spells
    if (!isCantrip && !isAlwaysPrep && cb && cb.checked) {
      count++;
    }
  });
  return count;
}

function updatePreparedBadge() {
  const pill = document.querySelector('.prep-counter-pill');
  const labelSpan = pill ? pill.querySelector('span') : null;
  const badge = document.getElementById('prep-spells-tally') || document.querySelector('.prep-counter-pill strong');
  if (badge) {
    const count = getCheckedLeveledCount();
    if (isKnownCasterClass()) {
      if (labelSpan) labelSpan.innerText = 'Spells Known: ';
      badge.innerText = `${count}`;
      badge.style.color = '#2ecc71';
    } else {
      if (labelSpan) labelSpan.innerText = 'Prepared Leveled Spells: ';
      badge.innerText = `${count} / ${activeMaxPreparedSpells}`;
      badge.style.color = (count === activeMaxPreparedSpells) ? '#2ecc71' : '#00F3FF';
    }
  }
}

// Global exports
window.setupSpellPreparationHandlers = setupSpellPreparationHandlers;
window.updatePreparedBadge = updatePreparedBadge;
window.getCheckedLeveledCount = getCheckedLeveledCount;
window.handleSpellCheckboxChange = handleSpellCheckboxChange;
window.getActiveSpellsContainer = getActiveSpellsContainer;
window.isKnownCasterClass = isKnownCasterClass;

/**
 * Dynamic 1-9 Spell Slot Handlers
 */
function getOrdinalSuffix(num) {
  const j = num % 10, k = num % 100;
  if (j === 1 && k !== 11) return `${num}st`;
  if (j === 2 && k !== 12) return `${num}nd`;
  if (j === 3 && k !== 13) return `${num}rd`;
  return `${num}th`;
}

function renderSpellSlotInputs(spellSlots = {}) {
  const container = document.getElementById('spell-slots-grid-container');
  if (!container) return;
  container.innerHTML = '';

  let hasAnySlots = false;

  for (let lvl = 1; lvl <= 9; lvl++) {
    const key = `level_${lvl}`;
    const slotData = spellSlots[key] || { current: 0, max: 0 };

    // Render if character possesses maximum slots > 0 or has slots configured
    if (slotData.max > 0 || slotData.current > 0) {
      hasAnySlots = true;
      container.insertAdjacentHTML('beforeend', `
        <div class="slot-chip" data-slot-level="${lvl}">
          <span class="slot-chip-label">${getOrdinalSuffix(lvl)}:</span>
          <input type="number" id="slot-${lvl}-cur" class="slot-spin-input" 
                 value="${slotData.current}" min="0" max="9" />
          <span class="slot-divider">/</span>
          <input type="number" id="slot-${lvl}-max" class="slot-spin-input" 
                 value="${slotData.max}" min="0" max="9" />
        </div>
      `);
    }
  }

  if (!hasAnySlots) {
    container.innerHTML = `<span style="font-size: 12px; color: #5f748a;">No spell slots unlocked for current level.</span>`;
  }
}

function collectDynamicSpellSlots() {
  const spellSlots = {};
  for (let lvl = 1; lvl <= 9; lvl++) {
    const curElem = document.getElementById(`slot-${lvl}-cur`);
    const maxElem = document.getElementById(`slot-${lvl}-max`);
    if (curElem && maxElem) {
      spellSlots[`level_${lvl}`] = {
        current: Math.max(0, parseInt(curElem.value, 10) || 0),
        max: Math.max(0, parseInt(maxElem.value, 10) || 0)
      };
    } else {
      spellSlots[`level_${lvl}`] = { current: 0, max: 0 };
    }
  }
  return spellSlots;
}

window.getOrdinalSuffix = getOrdinalSuffix;
window.renderSpellSlotInputs = renderSpellSlotInputs;
window.collectDynamicSpellSlots = collectDynamicSpellSlots;
