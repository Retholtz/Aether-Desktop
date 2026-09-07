/**
 * AETHER DESKTOP - FRONTEND CONTROLLER
 * Connects the web UI to pywebview.api backend bridge.
 */

window.aetherUI = {
  currentConfig: null,
  isAssistantRunning: false,
  agentName: "Aether",

  init: async function() {
    this.setupTabs();
    this.setupEventHandlers();
    await this.applyWindowsTheme();
    await this.loadVoices();
    await this.loadAudioDevices();
    await this.loadMonitors();
    await this.loadConfig();
    this.startTelemetryLoop();
    this.log("Aether Desktop initialized and ready.");
  },

  // =========================================================================
  // Windows Theme Integration
  // =========================================================================
  applyWindowsTheme: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const theme = await window.pywebview.api.get_windows_theme();
      if (theme && theme.accent_color) {
        document.documentElement.style.setProperty("--win-accent", theme.accent_color);
      }
    } catch (e) {
      console.warn("Could not query Windows theme:", e);
    }
  },

  // =========================================================================
  // Tab Switching
  // =========================================================================
  setupTabs: function() {
    const tabs = document.querySelectorAll(".hud-tab");
    tabs.forEach(tab => {
      tab.addEventListener("click", () => {
        tabs.forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

        tab.classList.add("active");
        const targetPane = document.getElementById("tab-" + tab.dataset.tab);
        if (targetPane) {
          targetPane.classList.add("active");
          if (tab.dataset.tab === "settings") {
            targetPane.focus();
          }
        }
      });
    });

    // Smooth wheel scrolling for Settings tab pane
    const settingsPane = document.getElementById("tab-settings");
    if (settingsPane) {
      settingsPane.addEventListener("wheel", (e) => {
        if (e.target.tagName === "TEXTAREA") {
          const ta = e.target;
          const atTop = ta.scrollTop === 0 && e.deltaY < 0;
          const atBottom = (ta.scrollHeight - ta.clientHeight <= ta.scrollTop + 1) && e.deltaY > 0;
          if (!atTop && !atBottom) return;
        }
        settingsPane.scrollBy({
          top: e.deltaY,
          behavior: "auto"
        });
      }, { passive: true });
    }
  },

  // =========================================================================
  // DOM & User Event Handlers
  // =========================================================================
  setupEventHandlers: function() {
    // Temperature slider display sync
    const tempSlider = document.getElementById("temperatureSlider");
    const tempDisplay = document.getElementById("tempValDisplay");
    tempSlider.addEventListener("input", (e) => {
      const val = parseFloat(e.target.value).toFixed(2);
      tempDisplay.innerText = val;
      document.getElementById("telTemp").innerText = val;
    });

    // Agent name real-time sync
    const nameInput = document.getElementById("agentNameInput");
    nameInput.addEventListener("input", (e) => {
      const name = e.target.value.trim() || "Aether";
      this.agentName = name;
      this.updateAgentNameUI(name);

      // Also automatically update Kill Phrase input if it contains stop
      const killInput = document.getElementById("safePhraseInput");
      if (killInput && (!killInput.value || killInput.value.toLowerCase().includes("stop"))) {
        killInput.value = `${name} stop`;
        const telSafe = document.getElementById("telSafePhrase");
        if (telSafe) telSafe.innerText = `"${name} stop"`;
      }
    });

    // Toggle API Key visibility & handle stored asterisks placeholder
    const keyInput = document.getElementById("apiKeyInput");
    const toggleKeyBtn = document.getElementById("toggleKeyVisibilityBtn");

    keyInput.addEventListener("focus", () => {
      if (keyInput.dataset.stored === "true" && keyInput.value.includes("***")) {
        keyInput.value = "";
        keyInput.dataset.stored = "false";
      }
    });

    toggleKeyBtn.addEventListener("click", () => {
      if (keyInput.type === "password") {
        keyInput.type = "text";
        toggleKeyBtn.innerText = "HIDE";
        if (keyInput.dataset.stored === "true" && this.currentConfig?.api?.api_key_display) {
          keyInput.value = this.currentConfig.api.api_key_display;
        }
      } else {
        keyInput.type = "password";
        toggleKeyBtn.innerText = "SHOW";
        if (keyInput.dataset.stored === "true") {
          keyInput.value = "********************************";
        }
      }
    });

    // Save Settings buttons (both top and bottom)
    document.getElementById("saveSettingsBtn").addEventListener("click", () => this.saveSettings());
    const topSaveBtn = document.getElementById("topSaveSettingsBtn");
    if (topSaveBtn) {
      topSaveBtn.addEventListener("click", () => this.saveSettings());
    }

    // Refresh Audio Hardware
    document.getElementById("refreshDevicesBtn").addEventListener("click", async () => {
      await this.loadAudioDevices();
      this.log("Audio devices re-enumerated.");
    });

    // Start / Stop Assistant button
    document.getElementById("toggleAssistantBtn").addEventListener("click", () => this.toggleAssistant());

    // Voice Playback Kill button
    document.getElementById("killAudioBtn").addEventListener("click", () => this.killAudio());

    // Chat text form submit
    document.getElementById("chatForm").addEventListener("submit", (e) => {
      e.preventDefault();
      this.sendChatMessage();
    });

    // Clear Chat button
    document.getElementById("clearChatBtn").addEventListener("click", () => {
      document.getElementById("chatHistory").innerHTML = "";
    });

    // Clear Logs button
    document.getElementById("clearLogsBtn").addEventListener("click", () => {
      document.getElementById("logConsole").innerText = "";
    });

    // Audio Mode Radios (PTT vs Always-On)
    document.querySelectorAll("input[name='audioMode']").forEach(radio => {
      radio.addEventListener("change", (e) => {
        this.updateAudioModeUI(e.target.value);
      });
    });

    // Push-To-Talk Button Events
    const pttBtn = document.getElementById("pttButton");
    const startPTT = () => {
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.set_ptt(true);
        pttBtn.classList.add("active");
      }
    };
    const stopPTT = () => {
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.set_ptt(false);
        pttBtn.classList.remove("active");
      }
    };

    pttBtn.addEventListener("mousedown", startPTT);
    pttBtn.addEventListener("mouseup", stopPTT);
    pttBtn.addEventListener("mouseleave", stopPTT);
    pttBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startPTT(); });
    pttBtn.addEventListener("touchend", (e) => { e.preventDefault(); stopPTT(); });
  },

  updateAgentNameUI: function(name) {
    document.getElementById("hudAgentTitle").innerText = `${name.toUpperCase()} DESKTOP`;
    document.getElementById("telAgentName").innerText = name;
    if (!this.isAssistantRunning) {
      document.getElementById("fabButtonText").innerText = `START ${name.toUpperCase()}`;
    }
  },

  updateAudioModeUI: function(mode) {
    const pttBtn = document.getElementById("pttButton");
    document.getElementById("telAudioMode").innerText = mode === "ptt" ? "PUSH-TO-TALK" : "ALWAYS-ON";
    if (mode === "ptt") {
      pttBtn.style.display = "block";
    } else {
      pttBtn.style.display = "none";
    }
  },

  // =========================================================================
  // Data Loading from Python Backend
  // =========================================================================
  loadVoices: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const voices = await window.pywebview.api.get_available_voices();
      // Alphabetize by voice name
      voices.sort((a, b) => a.name.localeCompare(b.name));

      const select = document.getElementById("voiceSelect");
      select.innerHTML = "";
      voices.forEach(v => {
        const opt = document.createElement("option");
        opt.value = v.name;
        opt.innerText = `${v.name} — ${v.trait} (${v.gender})`;
        select.appendChild(opt);
      });
    } catch (e) {
      console.error("Failed to load voices:", e);
    }
  },

  loadAudioDevices: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const inSelect = document.getElementById("inputDeviceSelect");
      const outSelect = document.getElementById("outputDeviceSelect");
      if (!inSelect || !outSelect) return;

      const prevInVal = inSelect.value;
      const prevOutVal = outSelect.value;

      const data = await window.pywebview.api.get_audio_devices();
      
      inSelect.innerHTML = "";
      outSelect.innerHTML = "";

      if (data.inputs && data.inputs.length > 0) {
        data.inputs.forEach(dev => {
          const opt = document.createElement("option");
          opt.value = dev.index;
          opt.innerText = dev.label || dev.name;
          inSelect.appendChild(opt);
        });
      } else {
        const opt = document.createElement("option");
        opt.value = "-1";
        opt.innerText = "No available microphones detected";
        inSelect.appendChild(opt);
      }

      if (data.outputs && data.outputs.length > 0) {
        data.outputs.forEach(dev => {
          const opt = document.createElement("option");
          opt.value = dev.index;
          opt.innerText = dev.label || dev.name;
          outSelect.appendChild(opt);
        });
      } else {
        const opt = document.createElement("option");
        opt.value = "-1";
        opt.innerText = "No available speakers detected";
        outSelect.appendChild(opt);
      }

      // Preserve previously selected option if still present in available list
      if (prevInVal && inSelect.querySelector(`option[value="${prevInVal}"]`)) {
        inSelect.value = prevInVal;
      }
      if (prevOutVal && outSelect.querySelector(`option[value="${prevOutVal}"]`)) {
        outSelect.value = prevOutVal;
      }
    } catch (e) {
      console.error("Failed to load audio devices:", e);
    }
  },

  loadMonitors: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const monitors = await window.pywebview.api.get_monitors();
      const select = document.getElementById("monitorSelect");
      if (!select || !monitors) return;
      select.innerHTML = "";
      monitors.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.innerText = `${m.name} (${m.details})`;
        select.appendChild(opt);
      });
    } catch (e) {
      console.error("Failed to load monitors:", e);
    }
  },

  loadConfig: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const cfg = await window.pywebview.api.get_config();
      this.currentConfig = cfg;

      const api = cfg.api || {};
      const audio = cfg.audio || {};
      const vision = cfg.vision || {};
      const security = cfg.security || {};

      // Agent Name
      if (api.agent_name) {
        this.agentName = api.agent_name;
        document.getElementById("agentNameInput").value = api.agent_name;
        this.updateAgentNameUI(api.agent_name);
      }

      // Gemini & Voice
      const keyInput = document.getElementById("apiKeyInput");
      if (api.has_key) {
        keyInput.value = "********************************";
        keyInput.dataset.stored = "true";
        document.getElementById("apiKeyHint").innerText = `Active Key: ${api.api_key_display} (DPAPI Encrypted)`;
      } else {
        keyInput.value = "";
        keyInput.dataset.stored = "false";
        document.getElementById("apiKeyHint").innerText = "No key saved. Enter your Gemini API key above.";
      }

      if (api.voice_name) {
        document.getElementById("voiceSelect").value = api.voice_name;
        document.getElementById("threadVoiceLabel").innerText = `VOICE: ${api.voice_name}`;
        document.getElementById("telVoice").innerText = api.voice_name;
      }
      if (api.model_id) document.getElementById("modelSelect").value = api.model_id;
      const currentStt = api.stt_model_id || api.stt_endpoint;
      if (currentStt && document.getElementById("sttSelect")) {
        document.getElementById("sttSelect").value = currentStt;
      }
      const currentTts = api.tts_model_id || api.tts_endpoint;
      if (currentTts && document.getElementById("ttsSelect")) {
        document.getElementById("ttsSelect").value = currentTts;
      }
      if (api.pro_model_id && document.getElementById("proModelSelect")) {
        document.getElementById("proModelSelect").value = api.pro_model_id;
      }
      if (vision.endpoint && document.getElementById("visionEndpointSelect")) {
        document.getElementById("visionEndpointSelect").value = vision.endpoint;
      }
      if (api.temperature !== undefined) {
        const tempVal = parseFloat(api.temperature).toFixed(2);
        document.getElementById("temperatureSlider").value = api.temperature;
        document.getElementById("tempValDisplay").innerText = tempVal;
        document.getElementById("telTemp").innerText = tempVal;
      }
      if (api.system_instruction) {
        document.getElementById("systemPromptInput").value = api.system_instruction;
      }

      // Audio & Mode
      if (audio.mode) {
        const radio = document.querySelector(`input[name='audioMode'][value='${audio.mode}']`);
        if (radio) radio.checked = true;
        this.updateAudioModeUI(audio.mode);
      }
      if (audio.safe_phrase) {
        document.getElementById("safePhraseInput").value = audio.safe_phrase;
        document.getElementById("telSafePhrase").innerText = `"${audio.safe_phrase}"`;
      } else {
        document.getElementById("safePhraseInput").value = `${this.agentName} stop`;
      }

      if (audio.software_gate !== undefined) {
        document.getElementById("softwareGateCheck").checked = audio.software_gate;
      }
      // Audio Devices
      if (audio.input_device_index !== undefined) {
        const inSel = document.getElementById("inputDeviceSelect");
        let matched = false;
        for (let opt of inSel.options) {
          if (parseInt(opt.value, 10) === audio.input_device_index) {
            inSel.value = opt.value;
            matched = true;
            break;
          }
        }
        // Fallback: match by device name if device index shifted
        if (!matched && audio.input_device_name) {
          const rawName = audio.input_device_name.replace(/^\[\d+\]\s*/, "").split("(")[0].trim().toLowerCase();
          if (rawName) {
            for (let opt of inSel.options) {
              const optName = opt.innerText.replace(/^\[\d+\]\s*/, "").split("(")[0].trim().toLowerCase();
              if (optName.includes(rawName) || rawName.includes(optName)) {
                inSel.value = opt.value;
                matched = true;
                break;
              }
            }
          }
        }
      }

      if (audio.output_device_index !== undefined) {
        const outSel = document.getElementById("outputDeviceSelect");
        let matched = false;
        for (let opt of outSel.options) {
          if (parseInt(opt.value, 10) === audio.output_device_index) {
            outSel.value = opt.value;
            matched = true;
            break;
          }
        }
        // Fallback: match by device name if device index shifted
        if (!matched && audio.output_device_name) {
          const rawName = audio.output_device_name.replace(/^\[\d+\]\s*/, "").split("(")[0].trim().toLowerCase();
          if (rawName) {
            for (let opt of outSel.options) {
              const optName = opt.innerText.replace(/^\[\d+\]\s*/, "").split("(")[0].trim().toLowerCase();
              if (optName.includes(rawName) || rawName.includes(optName)) {
                outSel.value = opt.value;
                matched = true;
                break;
              }
            }
          }
        }
      }


      // Vision
      if (vision.enabled !== undefined) {
        document.getElementById("visionEnabledCheck").checked = vision.enabled;
      }
      if (vision.fps !== undefined && vision.fps !== null) {
        document.getElementById("visionFps").value = parseFloat(vision.fps).toFixed(1);
      }
      if (vision.monitor && document.getElementById("monitorSelect")) {
        document.getElementById("monitorSelect").value = vision.monitor;
      }

      // Security
      if (security.app_whitelist) {
        document.getElementById("whitelistInput").value = security.app_whitelist.join(", ");
      }

      // Floating HUD Overlay & System Tray
      if (cfg.ui) {
        const overlayMode = cfg.ui.floating_overlay || "on_minimize";
        const overlaySel = document.getElementById("floatingOverlayMode");
        if (overlaySel) overlaySel.value = overlayMode;

        const trayCheck = document.getElementById("minimizeToTrayCheck");
        if (trayCheck) trayCheck.checked = (cfg.ui.minimize_to_tray !== false);
      }

      this.updateTelemetryDeviceLabels();

    } catch (e) {
      console.error("Failed to load config:", e);
    }
  },

  updateTelemetryDeviceLabels: function() {
    const inSel = document.getElementById("inputDeviceSelect");
    const outSel = document.getElementById("outputDeviceSelect");
    if (inSel.selectedOptions.length > 0) {
      document.getElementById("telMicName").innerText = inSel.selectedOptions[0].text;
    }
    if (outSel.selectedOptions.length > 0) {
      document.getElementById("telSpeakerName").innerText = outSel.selectedOptions[0].text;
    }
  },

  // =========================================================================
  // Saving Settings
  // =========================================================================
  saveSettings: async function(silent = false) {
    if (!window.pywebview || !window.pywebview.api) return false;
    const saveMsg = document.getElementById("saveStatusMsg");
    if (!silent && saveMsg) saveMsg.innerText = "Encrypting & saving...";

    try {
      const mode = document.querySelector("input[name='audioMode']:checked")?.value || "always_on";
      const inSel = document.getElementById("inputDeviceSelect");
      const outSel = document.getElementById("outputDeviceSelect");

      const rawKey = document.getElementById("apiKeyInput").value.trim();
      const newKey = (rawKey.includes("***") || rawKey.includes("••••")) ? "" : rawKey;
      const agentName = document.getElementById("agentNameInput").value.trim() || "Aether";
      const killPhrase = document.getElementById("safePhraseInput").value.trim() || `${agentName} stop`;

      const whitelistRaw = document.getElementById("whitelistInput").value;
      const whitelist = whitelistRaw.split(",").map(s => s.trim()).filter(Boolean);

      const payload = {
        api: {
          new_api_key: newKey,
          agent_name: agentName,
          voice_name: document.getElementById("voiceSelect").value,
          model_id: document.getElementById("modelSelect").value,
          pipeline_mode: document.getElementById("modelSelect").value.includes("live") ? "live" : "modular",
          stt_model_id: document.getElementById("sttSelect")?.value || "gemini-3.5-transcribe",
          tts_model_id: document.getElementById("ttsSelect")?.value || "edge-tts",
          live_model_id: "gemini-3.1-flash-live-preview",
          stt_endpoint: document.getElementById("sttSelect")?.value || "gemini-3.5-transcribe",
          tts_endpoint: document.getElementById("ttsSelect")?.value || "edge-tts",
          pro_model_id: document.getElementById("proModelSelect")?.value || "gemini-3.1-pro-preview",
          temperature: parseFloat(document.getElementById("temperatureSlider").value),
          system_instruction: document.getElementById("systemPromptInput").value
        },
        audio: {
          mode: mode,
          safe_phrase: killPhrase,
          software_gate: document.getElementById("softwareGateCheck").checked,
          input_device_index: parseInt(inSel.value, 10),
          input_device_name: inSel.selectedOptions[0]?.text || "",
          output_device_index: parseInt(outSel.value, 10),
          output_device_name: outSel.selectedOptions[0]?.text || "",
          input_sample_rate: 16000,
          output_sample_rate: 24000
        },
        vision: {
          enabled: document.getElementById("visionEnabledCheck").checked,
          fps: parseFloat(document.getElementById("visionFps").value),
          monitor: document.getElementById("monitorSelect")?.value || "auto",
          endpoint: document.getElementById("visionEndpointSelect")?.value || "live-stream",
          resolution: [768, 768],
          jpeg_quality: 70
        },
        security: {
          app_whitelist: whitelist,
          require_verbal_confirmation: true
        },
        ui: {
          floating_overlay: document.getElementById("floatingOverlayMode")?.value || "on_minimize",
          minimize_to_tray: document.getElementById("minimizeToTrayCheck")?.checked !== false
        }
      };

      const res = await window.pywebview.api.save_config(payload);
      if (res.success) {
        if (!silent && saveMsg) {
          saveMsg.innerText = "✓ Settings Saved (DPAPI Encrypted)";
          setTimeout(() => { saveMsg.innerText = ""; }, 4000);
        }
        document.getElementById("apiKeyInput").value = "";
        await this.loadConfig();

        // Dynamically reflect floating overlay setting change
        const overlayMode = payload.ui.floating_overlay;
        if (overlayMode === "always") {
          window.pywebview.api.show_overlay();
        } else if (overlayMode === "disabled") {
          window.pywebview.api.hide_overlay();
        }

        return true;
      } else {
        if (!silent && saveMsg) saveMsg.innerText = "Error: " + res.error;
        this.renderChatBubble({
          type: "error",
          content: `Settings Error: ${res.error}`
        });
        return false;
      }
    } catch (e) {
      if (!silent && saveMsg) saveMsg.innerText = "Failed to save: " + e;
      this.renderChatBubble({
        type: "error",
        content: `Failed to save settings: ${e}`
      });
      return false;
    }
  },

  // =========================================================================
  // Assistant Lifecycle & Controls
  // =========================================================================
  toggleAssistant: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    const btn = document.getElementById("toggleAssistantBtn");
    const label = document.getElementById("fabButtonText");

    if (!this.isAssistantRunning) {
      btn.disabled = true;
      label.innerText = "STARTING...";

      // Auto-save current form settings so any edits are immediately persisted!
      await this.saveSettings(true);

      label.innerText = "CONNECTING...";
      this.log("Starting assistant...");
      
      const res = await window.pywebview.api.start_assistant();
      btn.disabled = false;
      if (res.success) {
        this.isAssistantRunning = true;
        this.updateAssistantButtonState(true);
      } else {
        this.updateAssistantButtonState(false);
        this.renderChatBubble({
          type: "error",
          content: `Failed to start assistant: ${res.error || "Unknown error"}\n\nPlease verify your API key and audio devices in Settings.`
        });
      }
    } else {
      btn.disabled = true;
      label.innerText = "STOPPING...";
      await window.pywebview.api.stop_assistant();
      btn.disabled = false;
      this.isAssistantRunning = false;
      this.updateAssistantButtonState(false);
    }
  },

  updateAssistantButtonState: function(running) {
    const btn = document.getElementById("toggleAssistantBtn");
    const label = document.getElementById("fabButtonText");
    const name = (this.agentName || "Aether").toUpperCase();

    if (running) {
      btn.className = "hud-fab-assistant stop";
      btn.querySelector(".fab-icon").innerText = "■";
      label.innerText = `STOP ${name}`;
    } else {
      btn.className = "hud-fab-assistant start";
      btn.querySelector(".fab-icon").innerText = "▶";
      label.innerText = `START ${name}`;
    }
  },

  killAudio: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    await window.pywebview.api.kill_audio();
    this.log("Voice Playback Kill executed.");
  },

  sendChatMessage: async function() {
    const input = document.getElementById("chatTextInput");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";

    if (!window.pywebview || !window.pywebview.api) return;
    await window.pywebview.api.send_text_message(text);
  },

  // =========================================================================
  // Backend Event Dispatcher Receiver
  // =========================================================================
  handleEvent: function(event) {
    if (!event) return;
    const type = event.type;
    const data = event.data || {};

    if (type === "status") {
      this.updateStatus(data.state, data.message);
    } else if (type === "user_speech_stream") {
      this.updateLiveUserBubble(data.text);
    } else if (type === "chat_event") {
      this.renderChatBubble(data);
    } else if (type === "config_updated") {
      if (data && data.security && Array.isArray(data.security.app_whitelist)) {
        const input = document.getElementById("whitelistInput");
        if (input) {
          input.value = data.security.app_whitelist.join(", ");
        }
        if (this.currentConfig && this.currentConfig.security) {
          this.currentConfig.security.app_whitelist = data.security.app_whitelist;
        }
      }
    }
  },

  updateLiveUserBubble: function(text) {
    if (!text) return;
    const container = document.getElementById("chatHistory");
    let bubble = document.getElementById("activeLiveUserBubble");

    if (!bubble) {
      bubble = document.createElement("div");
      bubble.id = "activeLiveUserBubble";
      bubble.className = "chat-bubble user live";
      const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      bubble.innerHTML = `
        <div class="bubble-meta">
          <span class="sender-tag user">USER [VOICE]</span>
          <span class="timestamp">${timeStr}</span>
        </div>
        <div class="bubble-body"></div>
      `;
      container.appendChild(bubble);
    }

    const body = bubble.querySelector(".bubble-body");
    if (body) {
      body.innerText = text;
    }
    container.scrollTop = container.scrollHeight;
  },

  updateStatus: function(state, message) {
    const badge = document.getElementById("statusBadge");
    const text = document.getElementById("statusText");
    const telCore = document.getElementById("telCoreState");

    badge.className = "status-pill " + state;
    text.innerText = state.toUpperCase();
    telCore.innerText = state.toUpperCase();

    if (state === "connected" || state === "listening" || state === "speaking") {
      this.isAssistantRunning = true;
      this.updateAssistantButtonState(true);
    } else if (state === "disconnected" || state === "offline") {
      this.isAssistantRunning = false;
      this.updateAssistantButtonState(false);
    } else if (state === "error") {
      this.isAssistantRunning = false;
      this.updateAssistantButtonState(false);
      // Directly render error in Chat Window for immediate user visibility!
      if (message) {
        this.renderChatBubble({
          type: "error",
          content: `⚠️ ${message}`
        });
      }
    }

    if (message) this.log(`[STATUS] ${message}`);
  },

  renderChatBubble: function(item) {
    // If an active live speech bubble exists, handle consolidation
    const liveBubble = document.getElementById("activeLiveUserBubble");
    if (item.type === "user" && item.source === "voice") {
      if (liveBubble) {
        liveBubble.removeAttribute("id");
        liveBubble.classList.remove("live");
        const body = liveBubble.querySelector(".bubble-body");
        if (body && item.content) {
          body.innerText = item.content;
        }
        return;
      }
    } else if (liveBubble) {
      // Finalize live bubble before rendering assistant response or error
      liveBubble.removeAttribute("id");
      liveBubble.classList.remove("live");
    }

    const container = document.getElementById("chatHistory");
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble " + item.type;

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

    let senderLabel = "SYSTEM";
    const agentLabel = (item.agent_name || this.agentName || "AETHER").toUpperCase();

    if (item.type === "user") {
      senderLabel = item.source === "voice" ? "USER [VOICE]" : "USER [TEXT]";
    } else if (item.type === "assistant") {
      senderLabel = agentLabel;
    } else if (item.type === "tool") {
      senderLabel = item.name || "TOOL";
    } else if (item.type === "error") {
      senderLabel = "ERROR / DIAGNOSTIC";
    }

    bubble.innerHTML = `
      <div class="bubble-meta">
        <span class="sender-tag ${item.type}">${senderLabel}</span>
        <span class="timestamp">${timeStr}</span>
      </div>
      <div class="bubble-body">${this.escapeHtml(item.content)}</div>
    `;

    container.appendChild(bubble);
    container.scrollTop = container.scrollHeight;
  },

  escapeHtml: function(text) {
    const div = document.createElement("div");
    div.innerText = text;
    return div.innerHTML;
  },

  log: function(msg) {
    const consoleEl = document.getElementById("logConsole");
    if (!consoleEl) return;
    const time = new Date().toLocaleTimeString();
    consoleEl.innerText += `[${time}] ${msg}\n`;
    consoleEl.scrollTop = consoleEl.scrollHeight;
  },

  // =========================================================================
  // Telemetry Polling Loop (Mic Level Meter & State Sync)
  // =========================================================================
  startTelemetryLoop: function() {
    const meterFill = document.getElementById("micMeterFill");
    setInterval(async () => {
      if (!window.pywebview || !window.pywebview.api) return;
      try {
        const tel = await window.pywebview.api.get_telemetry();
        if (tel) {
          const pct = Math.min(100, Math.round(tel.mic_level * 100));
          meterFill.style.width = pct + "%";
        }
      } catch (e) {
        // Suppress polling error
      }
    }, 100);
  }
};

// Bootstrap on pywebview ready or window load
if (window.pywebview) {
  window.aetherUI.init();
} else {
  window.addEventListener("pywebviewready", () => {
    window.aetherUI.init();
  });
}
