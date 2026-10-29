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
    } else if (id.includes('argon')) {
      checkpointScore = 9000;
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
  { id: "gemini-4-argon", display_name: "Gemini 4 Argon (Balanced Reasoning)" }
];

const DEFAULT_HEAVY_MODELS = [
  { id: "gemini-4-argon", display_name: "Gemini 4 Argon (High Reasoning)" },
  { id: "gemini-3.8-flash-extended", display_name: "Gemini 3.8 Extended (Deep Reasoning)" }
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
    display_name: "Gemini Live Native Audio Stream (Real-Time Bidirectional - Recommended)" 
  },
  { 
    id: "primary_flash_stt", 
    display_name: "Gemini Flash Multimodal Audio (REST One-Shot Fallback)" 
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

  const rawHeavy = categorizedData.heavy_models || categorizedData.tier2_options;
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
      currentConfig.tier2_heavy_model || (currentConfig.api && currentConfig.api.pro_model_id) || 'gemini-4-argon'
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
    let rawStt = currentConfig.stt_endpoint || currentConfig.stt_model_endpoint || (currentConfig.api && (currentConfig.api.stt_model_id || currentConfig.api.stt_endpoint)) || 'gemini_live_audio';
    if (!['gemini_live_audio', 'primary_flash_stt'].includes(rawStt)) {
      rawStt = 'gemini_live_audio';
    }
    populateDropdown(
      sttEl, 
      STATIC_STT_ENGINES, 
      rawStt
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


// Audio Ducking UI Integration
async function loadAudioDuckingUI() {
  const toggle = document.getElementById('ducking-toggle');
  const slider = document.getElementById('ducking-slider');
  const numInput = document.getElementById('ducking-input');
  const valDisplay = document.getElementById('ducking-val-display');
  const hintDisplay = document.getElementById('ducking-hint');

  function syncDuckingUI(val) {
    if (slider) slider.value = val;
    if (numInput) numInput.value = val;
    if (valDisplay) valDisplay.textContent = `-${val}%`;
    if (hintDisplay) hintDisplay.textContent = `-${val}% background volume during speech. Dynamically lowers game and media audio while keeping Aether speech at 100%.`;
  }

  function saveDuckingSettings() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.update_audio_ducking_settings) {
      const isEnabled = toggle ? toggle.checked : true;
      const pct = slider ? parseInt(slider.value, 10) : 60;
      window.pywebview.api.update_audio_ducking_settings(isEnabled, pct);
    }
  }

  if (slider && !slider.dataset.wiredDucking) {
    slider.dataset.wiredDucking = "1";
    slider.addEventListener('input', (e) => syncDuckingUI(e.target.value));
    slider.addEventListener('change', saveDuckingSettings);
  }

  if (numInput && !numInput.dataset.wiredDucking) {
    numInput.dataset.wiredDucking = "1";
    numInput.addEventListener('input', (e) => syncDuckingUI(e.target.value));
    numInput.addEventListener('change', (e) => {
      syncDuckingUI(e.target.value);
      saveDuckingSettings();
    });
  }

  if (toggle && !toggle.dataset.wiredDucking) {
    toggle.dataset.wiredDucking = "1";
    toggle.addEventListener('change', saveDuckingSettings);
  }

  if (typeof window !== "undefined" && window.pywebview && window.pywebview.api && window.pywebview.api.get_audio_ducking_settings) {
    try {
      const cfg = await window.pywebview.api.get_audio_ducking_settings();
      if (cfg) {
        if (toggle && cfg.enabled !== undefined) {
          toggle.checked = Boolean(cfg.enabled);
        }
        if (cfg.duck_percentage !== undefined) {
          syncDuckingUI(cfg.duck_percentage);
        }
      }
    } catch (e) {
      console.warn("Could not load audio ducking settings:", e);
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
  await loadOAuthStatusUI();
  await loadAudioDuckingUI();
}

function showOAuthFeedback(msg, isError = false) {
  const banner = document.getElementById('oauthFeedbackBanner');
  if (!banner) return;
  banner.style.display = msg ? 'block' : 'none';
  banner.style.color = isError ? '#f87171' : '#38bdf8';
  banner.textContent = msg || '';
}

function renderOAuthStatus(statusMap) {
  if (!statusMap || typeof statusMap !== 'object') return;

  const updateProviderRow = (provKey, prefix) => {
    const info = statusMap[provKey] || {};
    const connected = Boolean(info.connected);
    const badge = document.getElementById(`oauth${prefix}StatusBadge`);
    const label = document.getElementById(`oauth${prefix}AccountLabel`);
    const disBtn = document.getElementById(`btnDisconnect${prefix === 'Imap' ? 'Imap' : prefix + 'OAuth'}`);

    if (badge) {
      badge.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
      badge.className = `step-status-pill ${connected ? 'done' : 'pending'}`;
    }
    if (label) {
      if (connected) {
        const acct = info.account_email || info.account_name || 'Connected Account';
        label.textContent = `Connected as ${acct}`;
        label.style.color = '#34d399';
      } else {
        label.textContent = 'Not connected';
        label.style.color = '#94a3b8';
      }
    }
    if (disBtn) {
      disBtn.style.display = connected ? 'inline-block' : 'none';
    }
  };

  updateProviderRow('google', 'Google');
  updateProviderRow('microsoft', 'Microsoft');
  updateProviderRow('imap', 'Imap');

  const gInfo = statusMap.google || {};
  const gCidInput = document.getElementById('oauthGoogleClientId');
  if (gCidInput && gInfo.client_id && !gCidInput.value) {
    gCidInput.value = gInfo.client_id;
  }

  const mInfo = statusMap.microsoft || {};
  const mCidInput = document.getElementById('oauthMicrosoftClientId');
  if (mCidInput && mInfo.client_id && !mCidInput.value) {
    mCidInput.value = mInfo.client_id;
  }
  const mTenantInput = document.getElementById('oauthMicrosoftTenantId');
  if (mTenantInput && mInfo.tenant_id) {
    mTenantInput.value = mInfo.tenant_id;
  }

  const iInfo = statusMap.imap || {};
  const iEmail = document.getElementById('oauthImapEmail');
  const iHost = document.getElementById('oauthImapHost');
  const sHost = document.getElementById('oauthSmtpHost');
  if (iEmail && iInfo.account_email && !iEmail.value) iEmail.value = iInfo.account_email;
  if (iHost && iInfo.imap_host && !iHost.value) iHost.value = iInfo.imap_host;
  if (sHost && iInfo.smtp_host && !sHost.value) sHost.value = iInfo.smtp_host;

  // Steam (Gaming & Presence) Status
  const steamInfo = statusMap.steam || {};
  const steamConnected = Boolean(steamInfo.connected);
  const steamBadge = document.getElementById('oauthSteamStatusBadge');
  const steamLabel = document.getElementById('oauthSteamAccountLabel');
  const steamConnectBtn = document.getElementById('btnConnectSteam');
  const steamDisconnectBtn = document.getElementById('btnDisconnectSteam');
  const steamKeyInput = document.getElementById('steamApiKeyInput');
  const steamIdInput = document.getElementById('steamId64Input');
  const steamAutoDetect = document.getElementById('steamAutoDetectAppId');

  if (steamBadge) {
    steamBadge.textContent = steamConnected ? 'CONNECTED' : 'DISCONNECTED';
    steamBadge.className = `step-status-pill ${steamConnected ? 'done' : 'pending'}`;
  }
  if (steamLabel) {
    if (steamConnected) {
      const name = steamInfo.persona_name
        ? `${steamInfo.persona_name} (SteamID: ${steamInfo.steam_id64 || ''})`
        : (steamInfo.steam_id64 ? `SteamID: ${steamInfo.steam_id64}` : 'Connected Account');
      steamLabel.textContent = `Connected as ${name}`;
      steamLabel.style.color = '#34d399';
    } else {
      steamLabel.textContent = 'Not connected';
      steamLabel.style.color = '#94a3b8';
    }
  }
  if (steamConnectBtn) {
    steamConnectBtn.textContent = steamConnected ? 'Configure Steam' : 'Sign into Steam';
  }
  if (steamDisconnectBtn) {
    steamDisconnectBtn.style.display = steamConnected ? 'inline-block' : 'none';
  }
  if (steamKeyInput && steamInfo.has_api_key && (!steamKeyInput.value || steamKeyInput.value === '••••••••')) {
    steamKeyInput.value = steamInfo.api_key_masked || '••••••••';
  }
  if (steamIdInput && steamInfo.steam_id64 && !steamIdInput.value) {
    steamIdInput.value = steamInfo.steam_id64;
  }
  if (steamAutoDetect && steamInfo.auto_detect_active_appid !== undefined) {
    steamAutoDetect.checked = Boolean(steamInfo.auto_detect_active_appid);
  }

  const pollRadio = document.getElementById('steamFriendPollRadio');
  const pollInterval = document.getElementById('steamFriendPollInterval');
  if (pollRadio) {
    pollRadio.checked = Boolean(steamInfo.poll_friends_status);
    pollRadio.disabled = !steamConnected;
  }
  if (pollInterval) {
    if (steamInfo.poll_interval_minutes) {
      pollInterval.value = String(steamInfo.poll_interval_minutes);
    }
    pollInterval.disabled = !steamConnected;
  }

  // Desktop Notifications Status (Discord, Slack, Teams, etc.)
  const notifInfo = statusMap.desktop_notifications || {};
  const notifBadge = document.getElementById('discordNotifStatusBadge');
  const notifLabel = document.getElementById('discordNotifStatusLabel');
  const notifEnabledChk = document.getElementById('chkDiscordNotifEnabled');
  const notifVerbalChk = document.getElementById('chkDiscordNotifVerbal');
  const notifPollInterval = document.getElementById('discordNotifPollInterval');
  const notifTargetApps = document.getElementById('discordNotifTargetAppsInput');

  if (notifBadge && notifInfo.enabled !== undefined) {
    if (!notifInfo.supported) {
      notifBadge.textContent = 'UNAVAILABLE';
      notifBadge.className = 'step-status-pill pending';
    } else if (notifInfo.enabled) {
      notifBadge.textContent = notifInfo.running ? 'LISTENING' : 'ACTIVE';
      notifBadge.className = 'step-status-pill done';
    } else {
      notifBadge.textContent = 'DISABLED';
      notifBadge.className = 'step-status-pill pending';
    }
  }
  if (notifLabel && notifInfo.status_label) {
    notifLabel.textContent = notifInfo.status_label;
    notifLabel.style.color = (notifInfo.enabled && notifInfo.supported) ? '#38bdf8' : '#94a3b8';
  }
  if (notifEnabledChk && notifInfo.enabled !== undefined) {
    notifEnabledChk.checked = Boolean(notifInfo.enabled);
  }
  if (notifVerbalChk && notifInfo.verbal_announcement !== undefined) {
    notifVerbalChk.checked = Boolean(notifInfo.verbal_announcement);
  }
  if (notifPollInterval && notifInfo.poll_interval_seconds !== undefined) {
    notifPollInterval.value = String(notifInfo.poll_interval_seconds);
  }
  if (notifTargetApps && Array.isArray(notifInfo.target_apps) && notifInfo.target_apps.length > 0) {
    notifTargetApps.value = notifInfo.target_apps.join(', ');
  }

  const notifIncludeWl = document.getElementById('chkDiscordIncludeWhitelist');
  const notifIncludeWlDrawer = document.getElementById('chkDiscordIncludeWhitelistDrawer');
  if (notifInfo.include_whitelist_apps !== undefined) {
    if (notifIncludeWl) notifIncludeWl.checked = Boolean(notifInfo.include_whitelist_apps);
    if (notifIncludeWlDrawer) notifIncludeWlDrawer.checked = Boolean(notifInfo.include_whitelist_apps);
  }

  const wlBadgeList = document.getElementById('discordWhitelistBadgeList');
  if (wlBadgeList) {
    const apps = notifInfo.whitelist_apps || [];
    if (!apps.length) {
      wlBadgeList.innerHTML = '<span style="color: #64748b; font-size: 0.75rem; font-style: italic;">No programs added to whitelist yet.</span>';
    } else {
      wlBadgeList.innerHTML = apps.map(app => `
        <span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.25); color: #38bdf8; font-size: 0.74rem;">
          🛡️ ${app}
        </span>
      `).join('');
    }
  }

  // Live Streaming & Broadcaster Watchlist Status
  const streamInfo = statusMap.streaming || {};
  const streamBadge = document.getElementById('streamingStatusBadge');
  const streamLabel = document.getElementById('streamingStatusLabel');
  const streamEnabledChk = document.getElementById('chkStreamingEnabled');
  const streamVerbalChk = document.getElementById('chkStreamingVerbal');
  const streamPollInterval = document.getElementById('streamingPollInterval');
  const twIdInput = document.getElementById('twitchClientIdInput');
  const twSecInput = document.getElementById('twitchClientSecretInput');
  const ytKeyInput = document.getElementById('youtubeApiKeyInput');
  const chipsList = document.getElementById('streamingChipsList');
  const countBadge = document.getElementById('streamingWatchlistCount');

  if (streamBadge && streamInfo.enabled !== undefined) {
    if (streamInfo.enabled) {
      streamBadge.textContent = streamInfo.running ? 'LISTENING' : 'ACTIVE';
      streamBadge.className = 'step-status-pill done';
    } else {
      streamBadge.textContent = 'DISABLED';
      streamBadge.className = 'step-status-pill pending';
    }
  }

  const twChannels = streamInfo.twitch_channels || [];
  const ytChannels = streamInfo.youtube_channels || [];
  const totalChannels = twChannels.length + ytChannels.length;

  if (streamLabel) {
    const parts = [];
    if (streamInfo.twitch_connected) parts.push('Twitch (Connected)');
    else parts.push('Twitch');
    if (streamInfo.youtube_connected) parts.push('YouTube (Connected)');
    else parts.push('YouTube');
    streamLabel.textContent = `Monitoring ${totalChannels} creators across ${parts.join(' & ')}`;
    streamLabel.style.color = streamInfo.enabled ? '#38bdf8' : '#94a3b8';
  }

  if (countBadge) {
    countBadge.textContent = `${totalChannels} CHANNEL${totalChannels === 1 ? '' : 'S'}`;
  }

  if (streamEnabledChk && streamInfo.enabled !== undefined) {
    streamEnabledChk.checked = Boolean(streamInfo.enabled);
  }
  if (streamVerbalChk && streamInfo.verbal_alerts !== undefined) {
    streamVerbalChk.checked = Boolean(streamInfo.verbal_alerts);
  }
  if (streamPollInterval && streamInfo.poll_interval_seconds !== undefined) {
    streamPollInterval.value = String(streamInfo.poll_interval_seconds);
  }

  if (twIdInput && streamInfo.has_twitch_id && (!twIdInput.value || twIdInput.value === '••••••••')) {
    twIdInput.value = streamInfo.twitch_client_id_masked || '••••••••';
  }
  if (twSecInput && streamInfo.has_twitch_secret && (!twSecInput.value || twSecInput.value === '••••••••')) {
    twSecInput.value = '••••••••';
  }
  if (ytKeyInput && streamInfo.has_youtube_key && (!ytKeyInput.value || ytKeyInput.value === '••••••••')) {
    ytKeyInput.value = streamInfo.youtube_api_key_masked || '••••••••';
  }

  // Update Twitch OAuth Pill & Button
  const twOAuthPill = document.getElementById('twitchOAuthStatusPill');
  const twConnectBtn = document.getElementById('btnConnectTwitchOAuth');
  const twDisconnectBtn = document.getElementById('btnDisconnectTwitchOAuth');
  const twConnected = streamInfo.twitch_oauth_connected || (statusMap.twitch && statusMap.twitch.connected);
  const twUser = streamInfo.twitch_account_name || (statusMap.twitch && statusMap.twitch.account_name) || '';

  if (twOAuthPill) {
    if (twConnected) {
      twOAuthPill.textContent = twUser ? `CONNECTED (${twUser})` : 'CONNECTED';
      twOAuthPill.className = 'step-status-pill done';
      if (twConnectBtn) twConnectBtn.style.display = 'none';
      if (twDisconnectBtn) twDisconnectBtn.style.display = 'inline-block';
    } else {
      twOAuthPill.textContent = 'DISCONNECTED';
      twOAuthPill.className = 'step-status-pill pending';
      if (twConnectBtn) twConnectBtn.style.display = 'inline-block';
      if (twDisconnectBtn) twDisconnectBtn.style.display = 'none';
    }
  }

  // Update YouTube OAuth Pill & Button
  const ytOAuthPill = document.getElementById('youtubeOAuthStatusPill');
  const ytConnectBtn = document.getElementById('btnConnectYouTubeOAuth');
  const ytDisconnectBtn = document.getElementById('btnDisconnectYouTubeOAuth');
  const ytOauthConnected = streamInfo.youtube_oauth_connected || (statusMap.google && statusMap.google.connected && (statusMap.google.scopes || []).some(s => s.includes('youtube')));

  if (ytOAuthPill) {
    if (ytOauthConnected) {
      ytOAuthPill.textContent = 'GOOGLE LINKED';
      ytOAuthPill.className = 'step-status-pill done';
      if (ytConnectBtn) ytConnectBtn.style.display = 'none';
      if (ytDisconnectBtn) ytDisconnectBtn.style.display = 'inline-block';
    } else if (streamInfo.youtube_connected) {
      ytOAuthPill.textContent = 'API KEY ACTIVE';
      ytOAuthPill.className = 'step-status-pill done';
      if (ytConnectBtn) ytConnectBtn.style.display = 'inline-block';
      if (ytDisconnectBtn) ytDisconnectBtn.style.display = 'none';
    } else {
      ytOAuthPill.textContent = 'DISCONNECTED';
      ytOAuthPill.className = 'step-status-pill pending';
      if (ytConnectBtn) ytConnectBtn.style.display = 'inline-block';
      if (ytDisconnectBtn) ytDisconnectBtn.style.display = 'none';
    }
  }

  // Render Creator Chips
  if (chipsList) {
    const esc = (t) => {
      const d = document.createElement('div');
      d.innerText = String(t || '');
      return d.innerHTML;
    };

    if (!totalChannels) {
      chipsList.innerHTML = '<span style="color: #64748b; font-size: 0.8rem; font-style: italic;">No creators monitored yet. Add a username above or ask Aether via voice.</span>';
    } else {
      const twHtml = twChannels.map(ch => `
        <span style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.35); color: #c084fc; font-size: 0.8rem;">
          <span style="font-weight: 700; font-size: 0.68rem; background: #9333ea; color: #fff; padding: 1px 5px; border-radius: 3px;">TWITCH</span>
          <strong>${esc(ch)}</strong>
          <span class="remove-stream-chip" data-platform="twitch" data-channel="${esc(ch)}" style="cursor: pointer; opacity: 0.7; font-weight: bold; margin-left: 4px; font-size: 0.85rem;" title="Remove ${esc(ch)}">✕</span>
        </span>
      `).join('');

      const ytHtml = ytChannels.map(entry => {
        const name = typeof entry === 'object' ? (entry.name || entry.channel_id) : entry;
        const cid = typeof entry === 'object' ? entry.channel_id : entry;
        return `
          <span style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.35); color: #f87171; font-size: 0.8rem;">
            <span style="font-weight: 700; font-size: 0.68rem; background: #dc2626; color: #fff; padding: 1px 5px; border-radius: 3px;">YOUTUBE</span>
            <strong>${esc(name)}</strong>
            <span class="remove-stream-chip" data-platform="youtube" data-channel="${esc(cid)}" style="cursor: pointer; opacity: 0.7; font-weight: bold; margin-left: 4px; font-size: 0.85rem;" title="Remove ${esc(name)}">✕</span>
          </span>
        `;
      }).join('');

      chipsList.innerHTML = twHtml + ytHtml;

      chipsList.querySelectorAll('.remove-stream-chip').forEach(el => {
        el.addEventListener('click', async (e) => {
          e.stopPropagation();
          const plat = el.dataset.platform;
          const chan = el.dataset.channel;
          if (!window.pywebview?.api?.remove_stream_channel) return;
          try {
            const res = await window.pywebview.api.remove_stream_channel(plat, chan);
            if (res && res.status) {
              renderOAuthStatus({ streaming: res.status });
              const banner = document.getElementById('streamingFeedbackBanner');
              if (banner) {
                banner.style.display = 'block';
                banner.style.color = '#38bdf8';
                banner.textContent = `Removed ${chan} from ${plat} watchlist.`;
                setTimeout(() => { banner.style.display = 'none'; }, 3500);
              }
            }
          } catch (err) {
            console.error('Failed removing stream channel:', err);
          }
        });
      });
    }
  }

  // Render Instant Replay Capture Status
  const rInfo = statusMap.replay_capture;
  if (rInfo) {
    const chk = document.getElementById('chkReplayCaptureEnabled');
    if (chk && rInfo.enabled !== undefined) chk.checked = Boolean(rInfo.enabled);
    const sel = document.getElementById('selReplayCaptureBackend');
    if (sel && rInfo.backend) sel.value = rInfo.backend;
    const txtCue = document.getElementById('txtReplaySpokenConfirmation');
    if (txtCue && rInfo.spoken_confirmation) txtCue.value = rInfo.spoken_confirmation;
    const obsDrawer = document.getElementById('replayObsConfigDrawer');
    if (obsDrawer) obsDrawer.style.display = rInfo.backend === 'obs_websocket' ? 'block' : 'none';
    const badge = document.getElementById('replayCaptureStatusBadge');
    if (badge) {
      badge.textContent = rInfo.enabled ? 'ENABLED' : 'DISABLED';
      badge.className = `step-status-pill ${rInfo.enabled ? 'done' : 'pending'}`;
    }
    const lbl = document.getElementById('replayCaptureBackendLabel');
    if (lbl) {
      if (rInfo.backend === 'shadowplay') lbl.textContent = 'NVIDIA ShadowPlay (Alt + F10)';
      else if (rInfo.backend === 'xbox_game_bar') lbl.textContent = 'Xbox Game Bar (Win + Alt + G)';
      else if (rInfo.backend === 'obs_websocket') lbl.textContent = `OBS Studio WebSocket (${rInfo.obs?.host || 'localhost'}:${rInfo.obs?.port || 4455})`;
    }
    if (rInfo.obs) {
      const h = document.getElementById('txtReplayObsHost');
      if (h && rInfo.obs.host) h.value = rInfo.obs.host;
      const p = document.getElementById('numReplayObsPort');
      if (p && rInfo.obs.port) p.value = rInfo.obs.port;
    }
  }
}

async function loadOAuthStatusUI() {
  if (typeof window !== 'undefined' && window.pywebview && window.pywebview.api && window.pywebview.api.get_oauth_status) {
    try {
      const status = await window.pywebview.api.get_oauth_status();
      renderOAuthStatus(status);
    } catch (e) {
      console.warn('Could not load OAuth status:', e);
    }
  }
}

function wireOAuthAccountButtons() {
  const bindOnce = (id, handler) => {
    const el = document.getElementById(id);
    if (!el || el.dataset.wiredOAuth === 'true') return;
    el.dataset.wiredOAuth = 'true';
    el.addEventListener('click', handler);
  };

  bindOnce('btnSaveGoogleOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const cid = (document.getElementById('oauthGoogleClientId')?.value || '').trim();
    const sec = (document.getElementById('oauthGoogleClientSecret')?.value || '').trim();
    const payload = { client_id: cid };
    if (sec) payload.client_secret = sec;
    const res = await window.pywebview.api.save_oauth_client_config('google', payload);
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback('Google OAuth Client ID saved to DPAPI vault.');
    } else {
      showOAuthFeedback(res?.error || 'Failed saving Google Client ID.', true);
    }
  });

  bindOnce('btnConnectGoogleOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const cid = (document.getElementById('oauthGoogleClientId')?.value || '').trim();
    const sec = (document.getElementById('oauthGoogleClientSecret')?.value || '').trim();
    if (cid) {
      const payload = { client_id: cid };
      if (sec) payload.client_secret = sec;
      await window.pywebview.api.save_oauth_client_config('google', payload);
    }
    const res = await window.pywebview.api.start_oauth_flow('google');
    if (res && res.success) {
      showOAuthFeedback(res.message || 'Complete Google sign-in in your browser...');
    } else {
      const drawer = document.getElementById('oauthDeveloperSetupDrawer');
      if (drawer && (res?.error || '').toLowerCase().includes('client id')) {
        drawer.open = true;
        document.getElementById('oauthGoogleClientId')?.focus();
      }
      showOAuthFeedback(res?.error || 'Could not start Google OAuth flow.', true);
    }
  });

  bindOnce('btnDisconnectGoogleOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const res = await window.pywebview.api.disconnect_oauth_provider('google');
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback('Disconnected Google account.');
    }
  });

  bindOnce('btnSaveMicrosoftOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const cid = (document.getElementById('oauthMicrosoftClientId')?.value || '').trim();
    const tenant = (document.getElementById('oauthMicrosoftTenantId')?.value || 'common').trim() || 'common';
    const res = await window.pywebview.api.save_oauth_client_config('microsoft', { client_id: cid, tenant_id: tenant });
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback('Microsoft Application Client ID saved to DPAPI vault.');
    } else {
      showOAuthFeedback(res?.error || 'Failed saving Microsoft Client ID.', true);
    }
  });

  bindOnce('btnConnectMicrosoftOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const cid = (document.getElementById('oauthMicrosoftClientId')?.value || '').trim();
    const tenant = (document.getElementById('oauthMicrosoftTenantId')?.value || 'common').trim() || 'common';
    if (cid) {
      await window.pywebview.api.save_oauth_client_config('microsoft', { client_id: cid, tenant_id: tenant });
    }
    const res = await window.pywebview.api.start_oauth_flow('microsoft');
    if (res && res.success) {
      showOAuthFeedback(res.message || 'Complete Microsoft sign-in in your browser...');
    } else {
      const drawer = document.getElementById('oauthDeveloperSetupDrawer');
      if (drawer && (res?.error || '').toLowerCase().includes('client id')) {
        drawer.open = true;
        document.getElementById('oauthMicrosoftClientId')?.focus();
      }
      showOAuthFeedback(res?.error || 'Could not start Microsoft OAuth flow.', true);
    }
  });

  bindOnce('btnDisconnectMicrosoftOAuth', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const res = await window.pywebview.api.disconnect_oauth_provider('microsoft');
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback('Disconnected Microsoft account.');
    }
  });

  bindOnce('btnConnectImap', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const email_address = (document.getElementById('oauthImapEmail')?.value || '').trim();
    const app_password = (document.getElementById('oauthImapPassword')?.value || '').trim();
    const imap_host = (document.getElementById('oauthImapHost')?.value || '').trim();
    const smtp_host = (document.getElementById('oauthSmtpHost')?.value || '').trim();
    showOAuthFeedback('Verifying IMAP connection...');
    const res = await window.pywebview.api.save_oauth_client_config('imap', {
      email_address,
      app_password,
      imap_host,
      smtp_host,
    });
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback(`Connected IMAP account (${email_address}).`);
    } else {
      showOAuthFeedback(res?.error || 'IMAP connection failed.', true);
    }
  });

  bindOnce('btnDisconnectImap', async () => {
    if (!window.pywebview || !window.pywebview.api) return;
    const res = await window.pywebview.api.disconnect_oauth_provider('imap');
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showOAuthFeedback('Disconnected IMAP account.');
    }
  });

  // Steam (Gaming & Presence) Event Handlers
  const showSteamFeedback = (msg, isErr = false) => {
    const el = document.getElementById('steamFeedbackBanner');
    if (!el) return;
    el.style.display = 'block';
    el.style.color = isErr ? '#f87171' : '#38bdf8';
    el.textContent = msg;
    setTimeout(() => { if (el) el.style.display = 'none'; }, 6000);
  };

  bindOnce('btnConnectSteam', () => {
    const drawer = document.getElementById('steamConfigDrawer');
    if (!drawer) return;
    const isClosed = drawer.style.display === 'none' || !drawer.style.display;
    drawer.style.display = isClosed ? 'block' : 'none';
    if (isClosed) {
      const keyInput = document.getElementById('steamApiKeyInput');
      if (keyInput && (!keyInput.value || keyInput.value.includes('•'))) {
        keyInput.focus();
      }
    }
  });

  bindOnce('btnCancelSteamConfig', () => {
    const drawer = document.getElementById('steamConfigDrawer');
    if (drawer) drawer.style.display = 'none';
  });

  bindOnce('btnSaveSteamConfig', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_steam_config) return;
    const apiKey = (document.getElementById('steamApiKeyInput')?.value || '').trim();
    const steamId = (document.getElementById('steamId64Input')?.value || '').trim();
    const autoDetect = Boolean(document.getElementById('steamAutoDetectAppId')?.checked);

    if (!apiKey && !steamId) {
      showSteamFeedback('Please provide a Steam Web API Key and SteamID64.', true);
      return;
    }

    const saveBtn = document.getElementById('btnSaveSteamConfig');
    if (saveBtn) saveBtn.textContent = 'Connecting...';

    try {
      const pollFriends = Boolean(document.getElementById('steamFriendPollRadio')?.checked);
      const pollInterval = parseInt(document.getElementById('steamFriendPollInterval')?.value || '5', 10) || 5;
      const res = await window.pywebview.api.save_steam_config(apiKey, steamId, autoDetect, pollFriends, pollInterval);
      if (res && res.connected) {
        renderOAuthStatus({ steam: res });
        const drawer = document.getElementById('steamConfigDrawer');
        if (drawer) drawer.style.display = 'none';
        const msg = res.persona_name
          ? `Connected as ${res.persona_name} (SteamID: ${res.steam_id64})!`
          : 'Steam credentials saved and connected successfully!';
        showSteamFeedback(msg);
      } else {
        showSteamFeedback(res?.error || 'Could not verify Steam credentials.', true);
      }
    } catch (e) {
      showSteamFeedback(`Error saving Steam config: ${e}`, true);
    } finally {
      if (saveBtn) saveBtn.textContent = 'Save & Connect Steam';
    }
  });

  const onPollSettingChanged = async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_steam_friend_polling) return;
    const pollRadio = document.getElementById('steamFriendPollRadio');
    const pollInterval = document.getElementById('steamFriendPollInterval');
    const enabled = Boolean(pollRadio && pollRadio.checked);
    const intervalMins = parseInt(pollInterval ? pollInterval.value : '5', 10) || 5;

    try {
      const res = await window.pywebview.api.set_steam_friend_polling(enabled, intervalMins);
      if (res && res.success) {
        showSteamFeedback(enabled ? `Friend status polling enabled (every ${intervalMins}m).` : 'Friend status polling disabled.');
      }
    } catch (e) {
      console.error('Failed to update Steam friend polling setting:', e);
      showSteamFeedback(`Failed to update friend polling: ${e.message || e}`, true);
    }
  };

  bindOnce('steamFriendPollRadio', () => {
    onPollSettingChanged();
  });

  const pollIntervalEl = document.getElementById('steamFriendPollInterval');
  if (pollIntervalEl && !pollIntervalEl.dataset.wired) {
    pollIntervalEl.dataset.wired = '1';
    pollIntervalEl.addEventListener('change', onPollSettingChanged);
  }

  bindOnce('btnDisconnectSteam', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.disconnect_steam) return;
    try {
      const res = await window.pywebview.api.disconnect_steam();
      renderOAuthStatus({ steam: res });
      const keyInput = document.getElementById('steamApiKeyInput');
      const idInput = document.getElementById('steamId64Input');
      if (keyInput) keyInput.value = '';
      if (idInput) idInput.value = '';
      showSteamFeedback('Disconnected Steam account.');
    } catch (e) {
      showSteamFeedback(`Error disconnecting Steam: ${e}`, true);
    }
  });

  // Discord & Desktop Notifications Event Handlers
  const showDiscordNotifFeedback = (msg, isErr = false) => {
    const el = document.getElementById('discordNotifFeedbackBanner');
    if (!el) return;
    el.textContent = msg;
    el.style.color = isErr ? '#f87171' : '#38bdf8';
    el.style.display = 'block';
    setTimeout(() => { if (el) el.style.display = 'none'; }, 4000);
  };

  bindOnce('btnConfigureDiscordNotif', () => {
    const drawer = document.getElementById('discordNotifConfigDrawer');
    if (drawer) {
      drawer.style.display = (drawer.style.display === 'none' || !drawer.style.display) ? 'block' : 'none';
    }
  });

  bindOnce('btnCancelDiscordNotifConfig', () => {
    const drawer = document.getElementById('discordNotifConfigDrawer');
    if (drawer) drawer.style.display = 'none';
  });

  const saveDiscordNotifSettings = async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_desktop_notifications_config) return;
    const enabled = Boolean(document.getElementById('chkDiscordNotifEnabled')?.checked);
    const verbal = Boolean(document.getElementById('chkDiscordNotifVerbal')?.checked);
    const includeWl = Boolean(document.getElementById('chkDiscordIncludeWhitelist')?.checked ?? document.getElementById('chkDiscordIncludeWhitelistDrawer')?.checked ?? true);
    const pollInterval = parseFloat(document.getElementById('discordNotifPollInterval')?.value || '1.5') || 1.5;
    const targetAppsStr = document.getElementById('discordNotifTargetAppsInput')?.value || 'Discord, Slack, Microsoft Teams, WhatsApp, Telegram';
    const targetApps = targetAppsStr.split(',').map(s => s.trim()).filter(Boolean);

    try {
      const res = await window.pywebview.api.save_desktop_notifications_config(enabled, verbal, pollInterval, targetApps, includeWl);
      renderOAuthStatus({ desktop_notifications: res });
      showDiscordNotifFeedback('Desktop notification settings saved.');
      const drawer = document.getElementById('discordNotifConfigDrawer');
      if (drawer) drawer.style.display = 'none';
    } catch (e) {
      showDiscordNotifFeedback(`Error saving settings: ${e.message || e}`, true);
    }
  };

  bindOnce('btnSaveDiscordNotifConfig', saveDiscordNotifSettings);

  const chkNotifEn = document.getElementById('chkDiscordNotifEnabled');
  if (chkNotifEn && !chkNotifEn.dataset.wired) {
    chkNotifEn.dataset.wired = '1';
    chkNotifEn.addEventListener('change', saveDiscordNotifSettings);
  }

  const chkNotifVerb = document.getElementById('chkDiscordNotifVerbal');
  if (chkNotifVerb && !chkNotifVerb.dataset.wired) {
    chkNotifVerb.dataset.wired = '1';
    chkNotifVerb.addEventListener('change', saveDiscordNotifSettings);
  }

  const pollIntervalSel = document.getElementById('discordNotifPollInterval');
  if (pollIntervalSel && !pollIntervalSel.dataset.wired) {
    pollIntervalSel.dataset.wired = '1';
    pollIntervalSel.addEventListener('change', saveDiscordNotifSettings);
  }

  const syncWlCheckboxes = (sourceId, targetId) => {
    const src = document.getElementById(sourceId);
    const tgt = document.getElementById(targetId);
    if (src && !src.dataset.wired) {
      src.dataset.wired = '1';
      src.addEventListener('change', () => {
        if (tgt) tgt.checked = src.checked;
        saveDiscordNotifSettings();
      });
    }
  };
  syncWlCheckboxes('chkDiscordIncludeWhitelist', 'chkDiscordIncludeWhitelistDrawer');
  syncWlCheckboxes('chkDiscordIncludeWhitelistDrawer', 'chkDiscordIncludeWhitelist');

  bindOnce('btnCheckRecentDiscordNotifs', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_recent_desktop_notifications) return;
    const container = document.getElementById('discordRecentNotifsContainer');
    const listEl = document.getElementById('discordRecentNotifsList');
    if (!container || !listEl) return;

    try {
      const res = await window.pywebview.api.get_recent_desktop_notifications(null, 10);
      container.style.display = 'block';
      const items = res.notifications || [];
      if (!items.length) {
        listEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.8rem; font-style: italic;">No recent notifications recorded.</div>';
      } else {
        listEl.innerHTML = items.map(n => `
          <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(56, 189, 248, 0.15); border-radius: 6px; padding: 8px 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
              <span style="color: #38bdf8; font-weight: 600; font-size: 0.8rem;">[${n.app_name}] ${n.title || ''}</span>
              <span style="color: #64748b; font-size: 0.72rem;">${n.timestamp ? new Date(n.timestamp).toLocaleTimeString() : ''}</span>
            </div>
            <div style="color: #cbd5e1; font-size: 0.78rem; word-break: break-word;">${n.body || ''}</div>
          </div>
        `).join('');
      }
    } catch (e) {
      showDiscordNotifFeedback(`Could not fetch notifications: ${e.message || e}`, true);
    }
  });

  bindOnce('btnClearDiscordNotifs', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.clear_desktop_notifications) return;
    try {
      await window.pywebview.api.clear_desktop_notifications();
      const listEl = document.getElementById('discordRecentNotifsList');
      if (listEl) {
        listEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.8rem; font-style: italic;">Notification log cleared.</div>';
      }
      showDiscordNotifFeedback('Cleared recent notification history.');
    } catch (e) {
      showDiscordNotifFeedback(`Error clearing log: ${e.message || e}`, true);
    }
  });

  // Live Streaming & Broadcaster Event Handlers
  const showStreamingFeedback = (msg, isErr = false) => {
    const banner = document.getElementById('streamingFeedbackBanner');
    if (!banner) return;
    banner.style.display = msg ? 'block' : 'none';
    banner.style.color = isErr ? '#f87171' : '#38bdf8';
    banner.textContent = msg || '';
    if (msg && !isErr) {
      setTimeout(() => {
        if (banner.textContent === msg) banner.style.display = 'none';
      }, 4000);
    }
  };

  bindOnce('btnConfigureStreaming', () => {
    const drawer = document.getElementById('streamingConfigDrawer');
    if (drawer) {
      drawer.style.display = drawer.style.display === 'none' ? 'block' : 'none';
    }
  });

  bindOnce('btnCancelStreamingConfig', () => {
    const drawer = document.getElementById('streamingConfigDrawer');
    if (drawer) drawer.style.display = 'none';
  });

  bindOnce('btnSaveStreamingConfig', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_streaming_config) return;
    const twId = (document.getElementById('twitchClientIdInput')?.value || '').trim();
    const twSec = (document.getElementById('twitchClientSecretInput')?.value || '').trim();
    const ytKey = (document.getElementById('youtubeApiKeyInput')?.value || '').trim();

    try {
      const res = await window.pywebview.api.save_streaming_config(
        null, null, null, twId, twSec, ytKey
      );
      renderOAuthStatus({ streaming: res });
      showStreamingFeedback('Live streaming API credentials saved.');
      const drawer = document.getElementById('streamingConfigDrawer');
      if (drawer) drawer.style.display = 'none';
    } catch (e) {
      showStreamingFeedback(`Error saving streaming credentials: ${e.message || e}`, true);
    }
  });

  const saveStreamingToggleSettings = async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_streaming_config) return;
    const enabled = Boolean(document.getElementById('chkStreamingEnabled')?.checked);
    const verbal = Boolean(document.getElementById('chkStreamingVerbal')?.checked);
    const pollInterval = parseInt(document.getElementById('streamingPollInterval')?.value || '120', 10);

    try {
      const res = await window.pywebview.api.save_streaming_config(enabled, pollInterval, verbal);
      renderOAuthStatus({ streaming: res });
      showStreamingFeedback('Updated live streaming preferences.');
    } catch (e) {
      showStreamingFeedback(`Error updating settings: ${e.message || e}`, true);
    }
  };

  const chkStreamEn = document.getElementById('chkStreamingEnabled');
  if (chkStreamEn && !chkStreamEn.dataset.wired) {
    chkStreamEn.dataset.wired = '1';
    chkStreamEn.addEventListener('change', saveStreamingToggleSettings);
  }

  const chkStreamVerb = document.getElementById('chkStreamingVerbal');
  if (chkStreamVerb && !chkStreamVerb.dataset.wired) {
    chkStreamVerb.dataset.wired = '1';
    chkStreamVerb.addEventListener('change', saveStreamingToggleSettings);
  }

  const streamPollSel = document.getElementById('streamingPollInterval');
  if (streamPollSel && !streamPollSel.dataset.wired) {
    streamPollSel.dataset.wired = '1';
    streamPollSel.addEventListener('change', saveStreamingToggleSettings);
  }

  bindOnce('btnAddStreamChannel', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.add_stream_channel) return;
    const plat = (document.getElementById('newStreamPlatform')?.value || 'twitch').trim();
    const chanInput = document.getElementById('newStreamChannelInput');
    const dispInput = document.getElementById('newStreamDisplayInput');
    const chan = (chanInput?.value || '').trim();
    const disp = (dispInput?.value || '').trim();

    if (!chan) {
      showStreamingFeedback('Please enter a creator username or channel ID.', true);
      return;
    }

    try {
      const res = await window.pywebview.api.add_stream_channel(plat, chan, disp);
      if (res && res.success) {
        renderOAuthStatus({ streaming: res.status });
        showStreamingFeedback(`Added ${disp || chan} to your ${plat} watchlist!`);
        if (chanInput) chanInput.value = '';
        if (dispInput) dispInput.value = '';
      } else {
        showStreamingFeedback(res?.error || 'Failed adding channel.', true);
      }
    } catch (e) {
      showStreamingFeedback(`Error adding channel: ${e.message || e}`, true);
    }
  });

  bindOnce('btnCheckStreamingNow', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.check_live_streams_now) return;
    const container = document.getElementById('streamingLiveResultsContainer');
    const listEl = document.getElementById('streamingLiveResultsList');
    if (!container || !listEl) return;

    listEl.innerHTML = '<div style="color: #38bdf8; font-size: 0.8rem; font-style: italic;">Checking live broadcasts across Twitch &amp; YouTube...</div>';
    container.style.display = 'block';

    try {
      const res = await window.pywebview.api.check_live_streams_now();
      const streams = res.streams || [];
      if (!streams.length) {
        listEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.8rem; font-style: italic;">No watchlist channels returned broadcast data.</div>';
      } else {
        listEl.innerHTML = streams.map(s => {
          const isLive = Boolean(s.is_live);
          const platBadge = s.platform === 'twitch'
            ? '<span style="background: #9333ea; color: #fff; font-size: 0.68rem; font-weight: 700; padding: 1px 5px; border-radius: 3px;">TWITCH</span>'
            : '<span style="background: #dc2626; color: #fff; font-size: 0.68rem; font-weight: 700; padding: 1px 5px; border-radius: 3px;">YOUTUBE</span>';
          const livePill = isLive
            ? '<span style="color: #34d399; font-weight: 700; font-size: 0.75rem; display: inline-flex; align-items: center; gap: 4px;">● LIVE</span>'
            : '<span style="color: #64748b; font-size: 0.75rem;">OFFLINE</span>';

          const viewers = isLive && s.viewer_count ? ` • ${s.viewer_count.toLocaleString()} viewers` : '';
          const category = s.game_or_category ? ` • ${s.game_or_category}` : '';
          const title = s.title ? `<div style="color: #cbd5e1; font-size: 0.78rem; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${s.title}</div>` : '';
          const link = s.url ? `<a href="${s.url}" target="_blank" style="color: #38bdf8; font-size: 0.74rem; text-decoration: none; margin-left: auto;">Open Stream ↗</a>` : '';

          return `
            <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(56, 189, 248, 0.15); border-radius: 6px; padding: 8px 10px;">
              <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                ${platBadge}
                <strong style="color: #e2e8f0; font-size: 0.84rem;">${s.channel_name}</strong>
                ${livePill}
                <span style="color: #94a3b8; font-size: 0.75rem;">${category}${viewers}</span>
                ${link}
              </div>
              ${title}
            </div>
          `;
        }).join('');
      }
    } catch (e) {
      listEl.innerHTML = `<div style="color: #f87171; font-size: 0.8rem;">Could not query live streams: ${e.message || e}</div>`;
    }
  });

  bindOnce('btnConnectTwitchOAuth', async () => {
    if (!window.pywebview?.api?.start_oauth_flow) return;
    const cid = (document.getElementById('twitchClientIdInput')?.value || '').trim();
    const sec = (document.getElementById('twitchClientSecretInput')?.value || '').trim();
    if (cid && !cid.includes('••••')) {
      const payload = { client_id: cid };
      if (sec && !sec.includes('••••')) payload.client_secret = sec;
      await window.pywebview.api.save_oauth_client_config('twitch', payload);
    }
    const res = await window.pywebview.api.start_oauth_flow('twitch');
    if (res && res.success) {
      showStreamingFeedback(res.message || 'Complete Twitch sign-in in your browser...');
    } else {
      showStreamingFeedback(res?.error || 'Could not start Twitch OAuth flow.', true);
    }
  });

  bindOnce('btnDisconnectTwitchOAuth', async () => {
    if (!window.pywebview?.api?.disconnect_oauth_provider) return;
    const res = await window.pywebview.api.disconnect_oauth_provider('twitch');
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showStreamingFeedback('Disconnected Twitch account.');
    }
  });

  bindOnce('btnConnectYouTubeOAuth', async () => {
    if (!window.pywebview?.api?.start_oauth_flow) return;
    const res = await window.pywebview.api.start_oauth_flow('google');
    if (res && res.success) {
      showStreamingFeedback(res.message || 'Complete Google sign-in in your browser...');
    } else {
      showStreamingFeedback(res?.error || 'Could not start Google OAuth flow.', true);
    }
  });

  bindOnce('btnDisconnectYouTubeOAuth', async () => {
    if (!window.pywebview?.api?.disconnect_oauth_provider) return;
    const res = await window.pywebview.api.disconnect_oauth_provider('google');
    if (res && res.success) {
      renderOAuthStatus(res.status);
      showStreamingFeedback('Disconnected Google account.');
    }
  });

  bindOnce('btnSyncTwitchFollows', async () => {
    if (!window.pywebview?.api?.sync_twitch_follows) return;
    showStreamingFeedback('Fetching followed creators from Twitch...');
    try {
      const res = await window.pywebview.api.sync_twitch_follows();
      if (res && res.success) {
        renderOAuthStatus({ streaming: res.status });
        showStreamingFeedback(`Synced ${res.added_count} new followed creator(s) from Twitch! (Total: ${res.total_count})`);
      } else {
        showStreamingFeedback(res?.error || res?.message || 'Could not sync Twitch follows.', true);
      }
    } catch (err) {
      showStreamingFeedback(`Error syncing Twitch follows: ${err.message || err}`, true);
    }
  });

  bindOnce('btnSyncYouTubeSubscriptions', async () => {
    if (!window.pywebview?.api?.sync_youtube_subscriptions) return;
    showStreamingFeedback('Fetching subscriptions from YouTube Data API...');
    try {
      const res = await window.pywebview.api.sync_youtube_subscriptions();
      if (res && res.success) {
        renderOAuthStatus({ streaming: res.status });
        showStreamingFeedback(`Synced ${res.added_count} new subscribed channel(s) from YouTube! (Total: ${res.total_count})`);
      } else {
        showStreamingFeedback(res?.error || res?.message || 'Could not sync YouTube subscriptions.', true);
      }
    } catch (err) {
      showStreamingFeedback(`Error syncing YouTube subscriptions: ${err.message || err}`, true);
    }
  });

  bindOnce('btnCloseStreamingResults', () => {
    const container = document.getElementById('streamingLiveResultsContainer');
    if (container) container.style.display = 'none';
  });

  // Instant Replay Capture Event Handlers
  const selBackend = document.getElementById('selReplayCaptureBackend');
  const obsDrawer = document.getElementById('replayObsConfigDrawer');
  if (selBackend && !selBackend.dataset.wiredReplay) {
    selBackend.dataset.wiredReplay = '1';
    selBackend.addEventListener('change', () => {
      if (obsDrawer) {
        obsDrawer.style.display = selBackend.value === 'obs_websocket' ? 'block' : 'none';
      }
    });
  }

  const showReplayFeedback = (msg, isErr = false) => {
    const banner = document.getElementById('replayFeedbackBanner');
    if (!banner) return;
    banner.style.display = 'block';
    banner.style.color = isErr ? '#f87171' : '#38bdf8';
    banner.textContent = msg;
    setTimeout(() => { banner.style.display = 'none'; }, 4000);
  };

  bindOnce('btnSaveReplayConfig', async () => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_replay_capture_config) return;
    const enabled = Boolean(document.getElementById('chkReplayCaptureEnabled')?.checked);
    const backend = document.getElementById('selReplayCaptureBackend')?.value || 'shadowplay';
    const spoken = (document.getElementById('txtReplaySpokenConfirmation')?.value || 'Captured.').trim();
    const obsHost = (document.getElementById('txtReplayObsHost')?.value || 'localhost').trim();
    const obsPort = parseInt(document.getElementById('numReplayObsPort')?.value || '4455', 10);
    const obsPassword = document.getElementById('txtReplayObsPassword')?.value || '';

    try {
      const res = await window.pywebview.api.save_replay_capture_config(
        enabled, backend, spoken, obsHost, obsPort, obsPassword
      );
      renderOAuthStatus({ replay_capture: res });
      showReplayFeedback(`Replay capture preferences saved (${backend}).`);
    } catch (e) {
      showReplayFeedback(`Error saving replay settings: ${e.message || e}`, true);
    }
  });

  const chkReplayEn = document.getElementById('chkReplayCaptureEnabled');
  if (chkReplayEn && !chkReplayEn.dataset.wired) {
    chkReplayEn.dataset.wired = '1';
    chkReplayEn.addEventListener('change', async () => {
      if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.save_replay_capture_config) return;
      const enabled = Boolean(chkReplayEn.checked);
      try {
        const res = await window.pywebview.api.save_replay_capture_config(enabled);
        renderOAuthStatus({ replay_capture: res });
        showReplayFeedback(enabled ? 'Instant replay capture enabled.' : 'Instant replay capture disabled.');
      } catch (e) {
        showReplayFeedback(`Error toggling replay: ${e.message || e}`, true);
      }
    });
  }
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
    wireOAuthAccountButtons();
    loadAudioDuckingUI();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettingsOnReady);
  } else {
    initSettingsOnReady();
  }

  window.addEventListener("pywebviewready", () => {
    wireOAuthAccountButtons();
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
    tier2_heavy_model: heavyEl ? heavyEl.value : 'gemini-4-argon',
    stt_model_endpoint: sttEl ? sttEl.value : 'gemini_live_audio',
    stt_endpoint: sttEl ? sttEl.value : 'gemini_live_audio',
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
  window.loadOAuthStatusUI = loadOAuthStatusUI;
  window.renderOAuthStatus = renderOAuthStatus;
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
