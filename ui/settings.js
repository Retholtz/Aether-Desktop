// ui/settings.js - Dynamic Model Discovery & Settings Integration

// Reading startup settings to UI:
function loadStartupSettings(config) {
  config = config || {};
  const bootEl = document.getElementById('toggle-boot-startup');
  if (bootEl && config.boot_on_startup !== undefined) {
    bootEl.checked = Boolean(config.boot_on_startup);
  }
  const minEl = document.getElementById('toggle-start-minimized');
  if (minEl && config.start_minimized !== undefined) {
    minEl.checked = Boolean(config.start_minimized);
  }
}

// Function to sort models chronologically with newest models at the top
function sortModelsChronologically(models) {
  if (!Array.isArray(models)) return [];
  const list = [...models];

  function getModelKey(model) {
    const id = ((model && model.id) || String(model || '')).toLowerCase();

    // 1. Semantic version e.g. 3.8, 3.1, 2.5, 2.0, 1.5, 1.0
    const verMatch = id.match(/(\d+)\.(\d+)(?:\.(\d+))?/);
    let major = 0, minor = 0, patch = 0;
    if (verMatch) {
      major = parseInt(verMatch[1], 10);
      minor = parseInt(verMatch[2], 10);
      patch = verMatch[3] ? parseInt(verMatch[3], 10) : 0;
    } else if (id.includes('gemini-exp')) {
      major = 1;
      minor = 99;
    } else {
      const singleVer = id.match(/gemini-(\d+)/);
      if (singleVer) major = parseInt(singleVer[1], 10);
    }

    // 2. Date score (e.g. 2025-02-05, 02-05, 1206)
    let dateScore = 0;
    const fullDate = id.match(/(202[4-9])[-_]?(\d{2})[-_]?(\d{2})/);
    if (fullDate) {
      dateScore = parseInt(fullDate[1] + fullDate[2] + fullDate[3], 10);
    } else {
      const mdMatch = id.match(/[-_](\d{2})[-_](\d{2})\b/);
      if (mdMatch) {
        dateScore = parseInt(mdMatch[1] + mdMatch[2], 10);
      } else {
        const num4 = id.match(/[-_](0[1-9]|1[0-2])([0-3][0-9])\b/);
        if (num4) {
          dateScore = parseInt(num4[1] + num4[2], 10);
        }
      }
    }

    // 3. Checkpoint / revision / alias score
    let checkpointScore = 0;
    const revMatch = id.match(/[-_](00\d)\b/);
    if (id.includes('latest')) {
      checkpointScore = 9999;
    } else if (revMatch) {
      checkpointScore = parseInt(revMatch[1], 10);
    } else if (id.includes('preview') || id.includes('exp')) {
      checkpointScore = 500;
    }

    return { major, minor, patch, dateScore, checkpointScore, id };
  }

  return list.sort((a, b) => {
    const ka = getModelKey(a);
    const kb = getModelKey(b);
    if (ka.major !== kb.major) return kb.major - ka.major;
    if (ka.minor !== kb.minor) return kb.minor - ka.minor;
    if (ka.patch !== kb.patch) return kb.patch - ka.patch;
    if (ka.dateScore !== kb.dateScore) return kb.dateScore - ka.dateScore;
    if (ka.checkpointScore !== kb.checkpointScore) return kb.checkpointScore - ka.checkpointScore;
    return kb.id.localeCompare(ka.id);
  });
}

// Base default options for conversational and heavy models
const DEFAULT_CHAT_MODELS = [
  { id: "gemini-3.8-flash", display_name: "Gemini 3.8 Flash (High Speed)" },
  { id: "gemini-3.7-flash", display_name: "Gemini 3.7 Flash" },
  { id: "gemini-3.6-flash", display_name: "Gemini 3.6 Flash" },
  { id: "gemini-3.5-flash", display_name: "Gemini 3.5 Flash" },
  { id: "gemini-3.5-flash-lite", display_name: "Gemini 3.5 Flash Lite" },
  { id: "gemini-3.1-pro-preview", display_name: "Gemini 3.1 Pro Preview (Heavy Reasoning)" }
];

const DEFAULT_HEAVY_MODELS = [
  { id: "gemini-3.1-pro-preview", display_name: "Gemini 3.1 Pro Preview (Heavy Reasoning)" },
  { id: "gemini-3.8-flash", display_name: "Gemini 3.8 Flash (High Speed)" },
  { id: "gemini-3.7-flash", display_name: "Gemini 3.7 Flash" },
  { id: "gemini-3.6-flash", display_name: "Gemini 3.6 Flash" },
  { id: "gemini-3.5-flash", display_name: "Gemini 3.5 Flash" }
];

// Base fixed options for TTS (offline/native engines that are not Google API endpoints)
const STATIC_TTS_ENGINES = [
  { id: "gemini_live", display_name: "Gemini Live Multimodal Voice Stream (~0.5s Realtime WebSocket - Recommended)" },
  { id: "edge_neural", display_name: "Edge Neural TTS (300+ Regional & Accent Voices)" },
  { id: "sapi5", display_name: "Windows Native SAPI5 / Local Voices (Instantaneous Offline)" },
  { id: "local_server", display_name: "Local TTS Server (Kokoro / OpenAI / FastTTS)" }
];

// Base fixed options for STT
const STATIC_STT_ENGINES = [
  { 
    id: "gemini_live_audio", 
    display_name: "Gemini Live Native Audio Stream (Real-Time Bidirectional)" 
  },
  { 
    id: "primary_flash_stt", 
    display_name: "Gemini Flash Multimodal Audio (One-Shot REST Transcription)" 
  }
];

function populateDropdown(el, items, currentValue) {
  if (!el) return;
  el.innerHTML = '';

  const list = Array.isArray(items) ? items : [];
  list.forEach(item => {
    const opt = document.createElement('option');
    opt.value = item.id;
    opt.textContent = item.display_name || item.label || item.id;
    if (item.id === currentValue) {
      opt.selected = true;
    }
    el.appendChild(opt);
  });

  // Keep active setting if not matched in list
  if (currentValue && ![...el.options].some(o => o.value === currentValue)) {
    const opt = new Option(`${currentValue} (Active)`, currentValue, true, true);
    el.prepend(opt);
  }
}

function updateSettingsModelDropdowns(categorizedData, currentConfig) {
  currentConfig = currentConfig || {};
  categorizedData = categorizedData || {};

  // 1. Primary Model and Tier 2 Heavy Model
  const primaryEl = document.getElementById('select-primary-model') || 
                    document.getElementById('modelSelect') || 
                    document.querySelector('select[name="primary_model_endpoint"]');
  const heavyEl = document.getElementById('select-heavy-model') || 
                  document.getElementById('proModelSelect') || 
                  document.querySelector('select[name="tier2_heavy_model"]');

  const rawChat = categorizedData.chat_models || categorizedData.tier1_options;
  const chatModels = (Array.isArray(rawChat) && rawChat.length > 0)
    ? rawChat
    : ((currentConfig.models && Array.isArray(currentConfig.models.tier1_options) && currentConfig.models.tier1_options.length > 0)
        ? currentConfig.models.tier1_options
        : DEFAULT_CHAT_MODELS);

  const rawHeavy = categorizedData.tier2_options || categorizedData.chat_models;
  const heavyModels = (Array.isArray(rawHeavy) && rawHeavy.length > 0)
    ? rawHeavy
    : ((currentConfig.models && Array.isArray(currentConfig.models.tier2_options) && currentConfig.models.tier2_options.length > 0)
        ? currentConfig.models.tier2_options
        : DEFAULT_HEAVY_MODELS);

  if (primaryEl) {
    populateDropdown(
      primaryEl, 
      chatModels, 
      currentConfig.primary_model_endpoint || (currentConfig.api && currentConfig.api.model_id) || currentConfig.tier1_fast_model || 'gemini-3.8-flash'
    );
  }
  if (heavyEl) {
    populateDropdown(
      heavyEl, 
      heavyModels, 
      currentConfig.tier2_heavy_model || (currentConfig.api && currentConfig.api.pro_model_id) || 'gemini-3.1-pro-preview'
    );
  }

  // 2. Text to Speech (TTS) Dropdown (under ASSISTANT IDENTITY)
  const ttsEl = document.getElementById('tts_endpoint') || 
                document.querySelector('select[name="tts_endpoint"]') ||
                document.getElementById('select-tts-endpoint') ||
                document.getElementById('select-tts-model') ||
                document.getElementById('ttsSelect');

  if (ttsEl) {
    // Merge native engines + discovered cloud TTS models
    const cloudTts = categorizedData.tts_models || categorizedData.tts_options || [];
    const combinedTTS = [
      ...STATIC_TTS_ENGINES,
      ...cloudTts
    ];
    populateDropdown(
      ttsEl, 
      combinedTTS, 
      currentConfig.tts_endpoint || currentConfig.tts_model_endpoint || (currentConfig.api && (currentConfig.api.tts_model_id || currentConfig.api.tts_endpoint)) || 'gemini_live'
    );
  }

  // 3. Speech to Text (STT) Dropdown (under Gemini API Key)
  const sttEl = document.getElementById('stt_endpoint') || 
                document.querySelector('select[name="stt_endpoint"]') ||
                document.getElementById('sttSelect') ||
                document.getElementById('select-stt-model');

  if (sttEl) {
    // Merge discovered cloud transcribe models + native stream
    const cloudStt = categorizedData.stt_models || categorizedData.stt_options || [];
    const combinedSTT = [
      ...cloudStt,
      ...STATIC_STT_ENGINES
    ];
    populateDropdown(
      sttEl, 
      combinedSTT, 
      currentConfig.stt_endpoint || currentConfig.stt_model_endpoint || (currentConfig.api && (currentConfig.api.stt_model_id || currentConfig.api.stt_endpoint)) || 'primary_flash_stt'
    );
  }
}

// Backwards compatibility aliases
const updateAllModelDropdowns = updateSettingsModelDropdowns;
const populateModelDropdowns = updateSettingsModelDropdowns;
const populateSelectOptions = populateDropdown;

// Voices Loading routine for Settings:
async function loadVoicesUI(config) {
  config = config || {};
  const ttsEl = document.getElementById('select-tts-endpoint') || 
                document.getElementById('tts_endpoint') || 
                document.getElementById('select-tts-model') || 
                document.getElementById('ttsSelect');
  const engine = (ttsEl && ttsEl.value) || config.tts_endpoint || config.tts_model_endpoint || (config.api && config.api.tts_model_id) || 'gemini_live';
  const voice = config.tts_voice || (config.api && config.api.voice_name) || 'Sulafat';

  if (window.aetherUI && typeof window.aetherUI.loadVoices === 'function') {
    await window.aetherUI.loadVoices(engine, voice);
    return;
  }

  if (typeof window !== "undefined" && window.pywebview && window.pywebview.api && window.pywebview.api.get_available_voices) {
    try {
      const voices = await window.pywebview.api.get_available_voices(engine);
      const voiceEl = document.getElementById('select-output-voice') || document.getElementById('voiceSelect');
      if (voiceEl && Array.isArray(voices) && voices.length > 0) {
        voiceEl.innerHTML = '';
        voices.forEach(v => {
          const opt = document.createElement('option');
          opt.value = v.name;
          opt.innerText = `${v.name} — ${v.trait} (${v.gender})`;
          if (v.name === voice) opt.selected = true;
          voiceEl.appendChild(opt);
        });
        if (!voiceEl.value && voiceEl.options.length > 0) {
          voiceEl.selectedIndex = 0;
        }
      }
    } catch (e) {
      console.warn("Could not load voices in settings.js:", e);
    }
  }
}

// Initialization routine inside Settings load:
async function loadSettingsUI() {
  let config = {};
  let modelData = null;
  if (typeof window !== "undefined" && window.pywebview && window.pywebview.api) {
    try {
      if (window.pywebview.api.get_config) {
        config = await window.pywebview.api.get_config();
      }
      if (window.pywebview.api.get_discovered_models) {
        modelData = await window.pywebview.api.get_discovered_models();
      }
    } catch (err) {
      console.warn("Could not load settings UI via pywebview api:", err);
    }
  }
  loadStartupSettings(config);
  updateSettingsModelDropdowns(modelData, config);
  await loadVoicesUI(config);
}

// Refresh button event listener wiring:
function wireModelRefreshButton() {
  const btn = document.getElementById('btn-refresh-models');
  if (!btn || btn.dataset.wired === "true") return;
  btn.dataset.wired = "true";

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Syncing endpoints...';

    try {
      const res = await window.pywebview.api.refresh_discovered_models();
      if (res && res.success) {
        const config = await window.pywebview.api.get_config();
        const catData = res.categorized_models || res.models;
        updateSettingsModelDropdowns(catData, config);
        alert('Model categories successfully refreshed.');
      } else {
        alert(`Refresh failed: ${res ? res.error : 'Unknown error'}`);
      }
    } catch (err) {
      alert(`Error communicating with backend: ${err}`);
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="icon">🔄</span> Refresh Available Models';
    }
  });
}

function wireTtsChangeListener() {
  const ttsEl = document.getElementById('select-tts-endpoint') || 
                document.getElementById('tts_endpoint') || 
                document.getElementById('select-tts-model') || 
                document.getElementById('ttsSelect');
  if (ttsEl && ttsEl.dataset.wiredVoices !== "true") {
    ttsEl.dataset.wiredVoices = "true";
    ttsEl.addEventListener("change", async (e) => {
      await loadVoicesUI({ tts_endpoint: e.target.value });
    });
  }
}

// Attach listener when document is ready
if (typeof document !== "undefined") {
  const initSettingsOnReady = () => {
    wireModelRefreshButton();
    wireTtsChangeListener();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettingsOnReady);
  } else {
    initSettingsOnReady();
  }

  window.addEventListener("pywebviewready", () => {
    loadSettingsUI();
  });
}

// Packing settings for Save:
function collectSettingsPayload() {
  const bootEl = document.getElementById('toggle-boot-startup');
  const minEl = document.getElementById('toggle-start-minimized');
  const primaryEl = document.getElementById('select-primary-model') || 
                    document.getElementById('modelSelect') || 
                    document.querySelector('select[name="primary_model_endpoint"]');
  const heavyEl = document.getElementById('select-heavy-model') || 
                  document.getElementById('proModelSelect') || 
                  document.querySelector('select[name="tier2_heavy_model"]');
  const sttEl = document.getElementById('stt_endpoint') || 
                document.querySelector('select[name="stt_endpoint"]') || 
                document.getElementById('sttSelect') || 
                document.getElementById('select-stt-model');
  const ttsEl = document.getElementById('tts_endpoint') || 
                document.querySelector('select[name="tts_endpoint"]') || 
                document.getElementById('select-tts-endpoint') || 
                document.getElementById('select-tts-model') || 
                document.getElementById('ttsSelect');

  return {
    boot_on_startup: bootEl ? bootEl.checked : false,
    start_minimized: minEl ? minEl.checked : false,
    primary_model_endpoint: primaryEl ? primaryEl.value : 'gemini-3.8-flash',
    tier1_fast_model: primaryEl ? primaryEl.value : 'gemini-3.8-flash',
    tier2_heavy_model: heavyEl ? heavyEl.value : 'gemini-3.1-pro-preview',
    stt_model_endpoint: sttEl ? sttEl.value : 'primary_flash_stt',
    stt_endpoint: sttEl ? sttEl.value : 'primary_flash_stt',
    tts_model_endpoint: ttsEl ? ttsEl.value : 'gemini_live',
    tts_endpoint: ttsEl ? ttsEl.value : 'gemini_live'
  };
}

if (typeof window !== "undefined") {
  window.DEFAULT_CHAT_MODELS = DEFAULT_CHAT_MODELS;
  window.DEFAULT_HEAVY_MODELS = DEFAULT_HEAVY_MODELS;
  window.STATIC_TTS_ENGINES = STATIC_TTS_ENGINES;
  window.STATIC_STT_ENGINES = STATIC_STT_ENGINES;
  window.populateDropdown = populateDropdown;
  window.updateSettingsModelDropdowns = updateSettingsModelDropdowns;
  window.updateAllModelDropdowns = updateAllModelDropdowns;
  window.populateModelDropdowns = populateModelDropdowns;
  window.populateSelectOptions = populateSelectOptions;
  window.loadStartupSettings = loadStartupSettings;
  window.sortModelsChronologically = sortModelsChronologically;
  window.loadSettingsUI = loadSettingsUI;
  window.loadVoicesUI = loadVoicesUI;
  window.wireModelRefreshButton = wireModelRefreshButton;
  window.collectSettingsPayload = collectSettingsPayload;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DEFAULT_CHAT_MODELS,
    DEFAULT_HEAVY_MODELS,
    STATIC_TTS_ENGINES,
    STATIC_STT_ENGINES,
    populateDropdown,
    updateSettingsModelDropdowns,
    updateAllModelDropdowns,
    populateModelDropdowns,
    populateSelectOptions,
    loadStartupSettings,
    sortModelsChronologically,
    loadSettingsUI,
    loadVoicesUI,
    wireModelRefreshButton,
    collectSettingsPayload
  };
}
