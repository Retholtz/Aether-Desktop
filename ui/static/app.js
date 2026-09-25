/**
 * AETHER DESKTOP - FRONTEND CONTROLLER
 * Connects the web UI to pywebview.api backend bridge.
 */

window.aetherUI = {
  currentConfig: null,
  isAssistantRunning: false,
  agentName: "Aether",
  pttType: "hold",
  pttKey: "Space",
  pttKeyDisplay: "Space",
  pttVk: 32,
  pttModifiers: [],
  isPttActive: false,
  isRecordingKeybind: false,
  hudMode: "normal",
  hudModeKey: "Ctrl+Space",
  hudModeKeyDisplay: "Ctrl+Space",
  hudModeVk: 32,
  hudModeModifiers: ["Control"],
  isRecordingHudModeKeybind: false,
  logEntries: [],
  activeLogFilter: "all",
  edgeCatalog: null,
  storedSessions: [],
  selectedStoredSessionId: null,

  init: async function() {
    this.setupTabs();
    this.setupEventHandlers();
    await this.applyWindowsTheme();
    await this.loadAccents();
    await this.loadAudioDevices();
    await this.loadMonitors();
    await this.loadConfig();
    await this.loadVoiceProfileStatus();
    await this.loadUserName();
    await this.loadUserPreferences();
    await this.loadUserFacts();
    await this.loadDictionaryTerms();
    await this.loadRecentLogs();
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
            this.loadAudioDevices(true);
          } else if (tab.dataset.tab === "preferences") {
            targetPane.focus();
            this.loadUserName();
            this.loadUserPreferences();
            this.loadUserFacts();
            this.loadDictionaryTerms();
          } else if (tab.dataset.tab === "chats") {
            targetPane.focus();
            this.loadStoredSessions();
          }
        }
      });
    });

    // Smooth wheel scrolling for Settings, User Preferences, and Stored Chats tab panes
    ["tab-settings", "tab-preferences", "tab-chats"].forEach(paneId => {
      const pane = document.getElementById(paneId);
      if (pane) {
        pane.addEventListener("wheel", (e) => {
          if (e.target.tagName === "TEXTAREA" || e.target.closest(".facts-table-wrap") || e.target.closest(".stored-sessions-list") || e.target.closest(".stored-turns-scroll")) {
            const el = e.target.tagName === "TEXTAREA" ? e.target : (e.target.closest(".facts-table-wrap") || e.target.closest(".stored-sessions-list") || e.target.closest(".stored-turns-scroll"));
            const atTop = el.scrollTop === 0 && e.deltaY < 0;
            const atBottom = (el.scrollHeight - el.clientHeight <= el.scrollTop + 1) && e.deltaY > 0;
            if (!atTop && !atBottom) return;
          }
          pane.scrollBy({
            top: e.deltaY,
            behavior: "auto"
          });
        }, { passive: true });
      }
    });
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
      await this.loadAudioDevices(true);
      this.log("Audio devices re-enumerated (matched with System\\Sound).");
    });

    const inDevSel = document.getElementById("inputDeviceSelect");
    if (inDevSel) {
      inDevSel.addEventListener("change", () => this.updateTelemetryDeviceLabels());
    }
    const outDevSel = document.getElementById("outputDeviceSelect");
    if (outDevSel) {
      outDevSel.addEventListener("change", () => this.updateTelemetryDeviceLabels());
    }

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

    // New Task / Reset Context button
    const newSessionBtn = document.getElementById("newSessionBtn");
    if (newSessionBtn) {
      newSessionBtn.addEventListener("click", () => this.resetContext());
    }

    // Log filter buttons
    document.querySelectorAll(".logs-filter-bar button").forEach(btn => {
      btn.addEventListener("click", () => {
        this.setLogFilter(btn.dataset.filter);
      });
    });

    // Copy logs to clipboard
    const copyLogsBtn = document.getElementById("copyLogsBtn");
    if (copyLogsBtn) {
      copyLogsBtn.addEventListener("click", async () => {
        const visibleLines = this.logEntries
          .filter(item => this.shouldShowLog(item.category))
          .map(item => item.entry.formatted || item.entry.message || "")
          .join("\n");
        try {
          await navigator.clipboard.writeText(visibleLines);
          const origText = copyLogsBtn.innerText;
          copyLogsBtn.innerText = "COPIED!";
          setTimeout(() => { copyLogsBtn.innerText = origText; }, 1500);
        } catch (e) {
          console.error("Clipboard copy failed:", e);
        }
      });
    }

    // Open logs directory in Windows Explorer
    const openLogsBtn = document.getElementById("openLogsFolderBtn");
    if (openLogsBtn) {
      openLogsBtn.addEventListener("click", async () => {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.open_logs_folder) {
          await window.pywebview.api.open_logs_folder();
        }
      });
    }

    // Clear Logs button
    const clearLogsBtn = document.getElementById("clearLogsBtn");
    if (clearLogsBtn) {
      clearLogsBtn.addEventListener("click", async () => {
        const consoleEl = document.getElementById("logConsole");
        if (consoleEl) consoleEl.innerHTML = "";
        this.logEntries = [];
        if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_log_console) {
          await window.pywebview.api.clear_log_console();
        }
      });
    }

    // Audio Mode Radios (PTT vs Always-On)
    document.querySelectorAll("input[name='audioMode']").forEach(radio => {
      radio.addEventListener("change", (e) => {
        this.updateAudioModeUI(e.target.value);
        this.saveSettings(true);
      });
    });

    // PTT Behavior Radios (Hold vs Toggle)
    document.querySelectorAll("input[name='pttType']").forEach(radio => {
      radio.addEventListener("change", (e) => {
        this.pttType = e.target.value;
        this.updatePttButtonUI();
        this.saveSettings(true);
        this.log(`PTT Behavior set to: ${this.pttType === "hold" ? "Hold to Speak" : "Toggle on/off"}`);
      });
    });

    // PTT Keybind Recorder Controls
    const keybindBtn = document.getElementById("pttKeybindBtn");
    if (keybindBtn) {
      keybindBtn.addEventListener("click", () => {
        if (this.isRecordingKeybind) {
          this.stopKeybindRecording();
        } else {
          this.startKeybindRecording();
        }
      });
    }

    const resetKeybindBtn = document.getElementById("pttKeybindResetBtn");
    if (resetKeybindBtn) {
      resetKeybindBtn.addEventListener("click", () => {
        this.pttKey = "Space";
        this.pttKeyDisplay = "Space";
        this.pttVk = 32;
        this.pttModifiers = [];
        const disp = document.getElementById("pttKeybindDisplay");
        if (disp) disp.innerText = "Space";
        this.updatePttButtonUI();
        this.saveSettings(true);
        this.log("PTT Keybind reset to default (Space).");
      });
    }

    // HUD Mode Keybind Recorder Controls
    const hudModeBtn = document.getElementById("hudModeKeybindBtn");
    if (hudModeBtn) {
      hudModeBtn.addEventListener("click", () => {
        if (this.isRecordingHudModeKeybind) {
          this.stopHudModeKeybindRecording();
        } else {
          this.startHudModeKeybindRecording();
        }
      });
    }

    const resetHudModeBtn = document.getElementById("hudModeKeybindResetBtn");
    if (resetHudModeBtn) {
      resetHudModeBtn.addEventListener("click", () => {
        this.hudModeKey = "Ctrl+Space";
        this.hudModeKeyDisplay = "Ctrl+Space";
        this.hudModeVk = 32;
        this.hudModeModifiers = ["Control"];
        const disp = document.getElementById("hudModeKeybindDisplay");
        if (disp) disp.innerText = "Ctrl+Space";
        this.saveSettings(true);
        this.log("HUD Mode Keybind reset to default (Ctrl+Space).");
      });
    }

    const defaultHudModeSel = document.getElementById("defaultHudModeSelect");
    if (defaultHudModeSel) {
      defaultHudModeSel.addEventListener("change", (e) => {
        this.hudMode = e.target.value;
        this.saveSettings(true);
        if (window.pywebview && window.pywebview.api && window.pywebview.api.set_mode) {
          window.pywebview.api.set_mode(e.target.value);
        }
      });
    }

    // Push-To-Talk Button Events (Supports both Hold and Toggle interactions)
    const pttBtn = document.getElementById("pttButton");
    const startHoldPTT = (e) => {
      if (this.pttType === "hold") {
        if (e && e.type === "touchstart") e.preventDefault();
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.set_ptt(true);
        }
      }
    };
    const stopHoldPTT = (e) => {
      if (this.pttType === "hold") {
        if (e && e.type === "touchend") e.preventDefault();
        if (window.pywebview && window.pywebview.api) {
          window.pywebview.api.set_ptt(false);
        }
      }
    };

    pttBtn.addEventListener("mousedown", startHoldPTT);
    pttBtn.addEventListener("mouseup", stopHoldPTT);
    pttBtn.addEventListener("mouseleave", stopHoldPTT);
    pttBtn.addEventListener("touchstart", startHoldPTT);
    pttBtn.addEventListener("touchend", stopHoldPTT);

    pttBtn.addEventListener("click", (e) => {
      if (this.pttType === "toggle") {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.toggle_ptt) {
          window.pywebview.api.toggle_ptt();
        }
      }
    });

    // Window Key Listeners for Keybind Recording & In-Window PTT Trigger
    window.addEventListener("keydown", (e) => {
      if (this.isRecordingHudModeKeybind) {
        e.preventDefault();
        e.stopPropagation();
        this.handleRecordedHudModeKey(e);
        return;
      }
      if (this.isRecordingKeybind) {
        e.preventDefault();
        e.stopPropagation();
        this.handleRecordedKey(e);
        return;
      }
      // If PTT mode is active and user presses keybind while window is focused
      if (this.currentConfig?.audio?.mode === "ptt" && !this.isRecordingKeybind) {
        const activeTag = document.activeElement ? document.activeElement.tagName : "";
        const isTyping = (activeTag === "INPUT" || activeTag === "TEXTAREA");
        // Only suppress if typing in an input and no modifiers are required
        if (isTyping && (!this.pttModifiers || this.pttModifiers.length === 0)) {
          return;
        }
        if (this.matchesPttKey(e)) {
          e.preventDefault();
          if (this.pttType === "hold") {
            if (!this.isPttActive && window.pywebview?.api?.set_ptt) {
              window.pywebview.api.set_ptt(true);
            }
          } else if (this.pttType === "toggle") {
            if (!e.repeat && window.pywebview?.api?.toggle_ptt) {
              window.pywebview.api.toggle_ptt();
            }
          }
        }
      }
    });

    window.addEventListener("keyup", (e) => {
      if (this.isRecordingKeybind) return;
      if (this.currentConfig?.audio?.mode === "ptt" && this.pttType === "hold") {
        if (this.matchesPttKey(e)) {
          if (window.pywebview?.api?.set_ptt) {
            window.pywebview.api.set_ptt(false);
          }
        }
      }
    });

    // Notify backend when text inputs gain/lose focus to suppress single-key global hotkeys while typing
    document.addEventListener("focusin", (e) => {
      const tag = e.target ? e.target.tagName : "";
      if (tag === "INPUT" || tag === "TEXTAREA") {
        if (window.pywebview?.api?.set_input_focused) {
          window.pywebview.api.set_input_focused(true);
        }
      }
    });
    document.addEventListener("focusout", (e) => {
      const tag = e.target ? e.target.tagName : "";
      if (tag === "INPUT" || tag === "TEXTAREA") {
        if (window.pywebview?.api?.set_input_focused) {
          window.pywebview.api.set_input_focused(false);
        }
      }
    });

    // Voice Biometrics Toggle & Sensitivity Slider
    const bioCheck = document.getElementById("voiceBiometricsCheck");
    if (bioCheck) {
      bioCheck.addEventListener("change", async (e) => {
        const threshold = parseFloat(document.getElementById("bioThresholdSlider")?.value || "0.40");
        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_voice_biometrics_settings) {
          await window.pywebview.api.save_voice_biometrics_settings(e.target.checked, threshold);
          this.log(`Target speaker filter ${e.target.checked ? "ENABLED" : "DISABLED"} (Threshold: ${threshold})`);
        }
      });
    }

    const bioSlider = document.getElementById("bioThresholdSlider");
    const bioVal = document.getElementById("bioThresholdVal");
    if (bioSlider) {
      bioSlider.addEventListener("input", (e) => {
        if (bioVal) bioVal.innerText = parseFloat(e.target.value).toFixed(2);
      });
      bioSlider.addEventListener("change", async (e) => {
        const enabled = document.getElementById("voiceBiometricsCheck")?.checked || false;
        const threshold = parseFloat(e.target.value);
        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_voice_biometrics_settings) {
          await window.pywebview.api.save_voice_biometrics_settings(enabled, threshold);
          this.log(`Speaker verification sensitivity updated to ${threshold}`);
        }
      });
    }

    // Voice Calibration Wizard Buttons
    const recStep1Btn = document.getElementById("recStep1Btn");
    if (recStep1Btn) {
      recStep1Btn.addEventListener("click", () => this.recordVoiceStep(1));
    }
    const recStep2Btn = document.getElementById("recStep2Btn");
    if (recStep2Btn) {
      recStep2Btn.addEventListener("click", () => this.recordVoiceStep(2));
    }
    const recStep3Btn = document.getElementById("recStep3Btn");
    if (recStep3Btn) {
      recStep3Btn.addEventListener("click", () => this.recordVoiceStep(3));
    }
    const retrainProfileBtn = document.getElementById("retrainProfileBtn");
    if (retrainProfileBtn) {
      retrainProfileBtn.addEventListener("click", () => this.retrainVoiceProfile());
    }

    // TTS Provider & Voice Swapping Listeners
    const ttsSelect = document.getElementById("ttsSelect");
    if (ttsSelect) {
      ttsSelect.addEventListener("change", async (e) => {
        await this.loadVoices(e.target.value);
      });
    }

    const edgeRegionSelect = document.getElementById("edgeRegionSelect");
    if (edgeRegionSelect) {
      edgeRegionSelect.addEventListener("change", (e) => {
        this.populateEdgeVoicesForRegion(e.target.value);
        this.updateVoiceLabels();
      });
    }

    const voiceSelect = document.getElementById("voiceSelect");
    if (voiceSelect) {
      voiceSelect.addEventListener("change", () => {
        this.updateVoiceLabels();
      });
    }

    const voiceTextInput = document.getElementById("voiceTextInput");
    if (voiceTextInput) {
      voiceTextInput.addEventListener("input", () => {
        this.updateVoiceLabels();
      });
    }

    const voiceAccentSelect = document.getElementById("voiceAccentSelect");
    if (voiceAccentSelect) {
      voiceAccentSelect.addEventListener("change", () => {
        this.updateVoiceLabels();
      });
    }

    const speedSlider = document.getElementById("voiceSpeedSlider");
    const speedVal = document.getElementById("voiceSpeedVal");
    if (speedSlider) {
      speedSlider.addEventListener("input", (e) => {
        if (speedVal) speedVal.innerText = `${parseFloat(e.target.value).toFixed(2)}x`;
      });
    }

    this.setupPreferencesHandlers();
    this.setupStoredChatsHandlers();
  },

  updateVoiceLabels: function() {
    const eng = (document.getElementById("ttsSelect")?.value || "").toLowerCase();
    const isLocal = eng.includes("local") && !eng.includes("windows");
    const select = document.getElementById("voiceSelect");
    const textInput = document.getElementById("voiceTextInput");
    const v = isLocal ? (textInput?.value.trim() || "") : (select?.value || "");
    const selectedOpt = select?.selectedOptions ? select.selectedOptions[0] : null;
    const a = document.getElementById("voiceAccentSelect")?.value || "default";
    const accentSuffix = (a && a !== "default" && !isLocal) ? ` (${a})` : "";

    let displayVoice = v;
    if (isLocal) {
      displayVoice = v || "Default / None";
    } else if (selectedOpt && selectedOpt.dataset.cleanName) {
      const regSelect = document.getElementById("edgeRegionSelect");
      const regId = regSelect?.value || "";
      const regCode = regId.includes("-") ? regId.split("-")[1] : regId;
      displayVoice = `${selectedOpt.dataset.cleanName} (${regCode})`;
    }

    if (document.getElementById("threadVoiceLabel")) {
      document.getElementById("threadVoiceLabel").innerText = `VOICE: ${displayVoice}${accentSuffix}`;
    }
    if (document.getElementById("telVoice")) {
      document.getElementById("telVoice").innerText = `${displayVoice}${accentSuffix}`;
    }
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
    const pttGroup = document.getElementById("pttSettingsGroup");
    document.getElementById("telAudioMode").innerText = mode === "ptt" ? "PUSH-TO-TALK" : "ALWAYS-ON";
    if (mode === "ptt") {
      if (pttBtn) pttBtn.style.display = "block";
      if (pttGroup) pttGroup.style.display = "block";
    } else {
      if (pttBtn) pttBtn.style.display = "none";
      if (pttGroup) pttGroup.style.display = "none";
    }
    this.updatePttButtonUI();
  },

  updatePttButtonUI: function() {
    const pttBtn = document.getElementById("pttButton");
    const label = document.getElementById("pttButtonLabel") || pttBtn?.querySelector("span");
    if (!pttBtn || !label) return;

    const keyText = (this.pttKeyDisplay || "SPACE").toUpperCase();
    if (this.pttType === "hold") {
      if (this.isPttActive) {
        label.innerText = `🎙️ TRANSMITTING (HOLD [${keyText}])`;
        pttBtn.classList.add("active");
      } else {
        label.innerText = `🎙️ HOLD [${keyText}] TO SPEAK`;
        pttBtn.classList.remove("active");
      }
    } else if (this.pttType === "toggle") {
      if (this.isPttActive) {
        label.innerText = `🎙️ MIC LIVE (PRESS [${keyText}] TO MUTE)`;
        pttBtn.classList.add("active");
      } else {
        label.innerText = `🎙️ MIC MUTED (PRESS [${keyText}] TO SPEAK)`;
        pttBtn.classList.remove("active");
      }
    }
  },

  startKeybindRecording: function() {
    this.isRecordingKeybind = true;
    const btn = document.getElementById("pttKeybindBtn");
    const display = document.getElementById("pttKeybindDisplay");
    if (btn) btn.classList.add("recording");
    if (display) display.innerText = "PRESS ANY KEY...";
  },

  stopKeybindRecording: function() {
    this.isRecordingKeybind = false;
    const btn = document.getElementById("pttKeybindBtn");
    const display = document.getElementById("pttKeybindDisplay");
    if (btn) btn.classList.remove("recording");
    if (display) display.innerText = this.pttKeyDisplay || "Space";
  },

  handleRecordedKey: function(e) {
    // Ignore lone modifier presses while waiting for the key
    if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) {
      return;
    }

    const mods = [];
    if (e.ctrlKey) mods.push("Control");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");

    let keyName = e.code || e.key;
    if (keyName.startsWith("Key")) keyName = keyName.substring(3);
    if (keyName.startsWith("Digit")) keyName = keyName.substring(5);

    let displayKey = keyName;
    if (keyName === "Space") displayKey = "Space";
    else if (keyName === "Backquote") displayKey = "~";
    else if (keyName === "Escape") displayKey = "Esc";

    const displayParts = [];
    if (e.ctrlKey) displayParts.push("Ctrl");
    if (e.altKey) displayParts.push("Alt");
    if (e.shiftKey) displayParts.push("Shift");
    displayParts.push(displayKey.toUpperCase());

    this.pttKey = keyName;
    this.pttKeyDisplay = displayParts.join(" + ");
    this.pttVk = e.keyCode || 32;
    this.pttModifiers = mods;

    this.stopKeybindRecording();
    this.updatePttButtonUI();
    this.saveSettings(true);
    this.log(`PTT Keybind set to: ${this.pttKeyDisplay}`);
  },

  startHudModeKeybindRecording: function() {
    this.isRecordingHudModeKeybind = true;
    this.stopKeybindRecording();
    const btn = document.getElementById("hudModeKeybindBtn");
    const display = document.getElementById("hudModeKeybindDisplay");
    if (btn) btn.classList.add("recording");
    if (display) display.innerText = "PRESS ANY KEY COMBO...";
  },

  stopHudModeKeybindRecording: function() {
    this.isRecordingHudModeKeybind = false;
    const btn = document.getElementById("hudModeKeybindBtn");
    const display = document.getElementById("hudModeKeybindDisplay");
    if (btn) btn.classList.remove("recording");
    if (display) display.innerText = this.hudModeKeyDisplay || "Ctrl+Space";
  },

  handleRecordedHudModeKey: function(e) {
    if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) {
      return;
    }

    const mods = [];
    if (e.ctrlKey) mods.push("Control");
    if (e.altKey) mods.push("Alt");
    if (e.shiftKey) mods.push("Shift");

    let keyName = e.code || e.key;
    if (keyName.startsWith("Key")) keyName = keyName.substring(3);
    if (keyName.startsWith("Digit")) keyName = keyName.substring(5);

    let displayKey = keyName;
    if (keyName === "Space") displayKey = "Space";
    else if (keyName === "Backquote") displayKey = "~";
    else if (keyName === "Escape") displayKey = "Esc";

    const displayParts = [];
    if (e.ctrlKey) displayParts.push("Ctrl");
    if (e.altKey) displayParts.push("Alt");
    if (e.shiftKey) displayParts.push("Shift");
    displayParts.push(displayKey);

    this.hudModeKey = displayParts.join("+");
    this.hudModeKeyDisplay = displayParts.join("+");
    this.hudModeVk = e.keyCode || 32;
    this.hudModeModifiers = mods;

    this.stopHudModeKeybindRecording();
    this.saveSettings(true);
    this.log(`HUD Mode Keybind set to: ${this.hudModeKeyDisplay}`);
  },

  matchesPttKey: function(e) {
    if (this.pttVk && e.keyCode === this.pttVk) {
      const reqCtrl = this.pttModifiers.includes("Control");
      const reqAlt = this.pttModifiers.includes("Alt");
      const reqShift = this.pttModifiers.includes("Shift");
      return e.ctrlKey === reqCtrl && e.altKey === reqAlt && e.shiftKey === reqShift;
    }
    return false;
  },

  // =========================================================================
  // Data Loading from Python Backend
  // =========================================================================
  loadAccents: async function() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const accents = await window.pywebview.api.get_available_accents();
      const select = document.getElementById("voiceAccentSelect");
      if (!select) return;
      const prevVal = select.value || "default";
      select.innerHTML = "";
      accents.forEach(a => {
        const opt = document.createElement("option");
        opt.value = a.id;
        opt.innerText = a.label;
        select.appendChild(opt);
      });
      if (prevVal && Array.from(select.options).some(o => o.value === prevVal)) {
        select.value = prevVal;
      }
    } catch (e) {
      console.error("Failed to load accents:", e);
    }
  },

  loadEdgeCatalog: async function() {
    if (this.edgeCatalog) return this.edgeCatalog;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_edge_voice_catalog) return null;
    try {
      this.edgeCatalog = await window.pywebview.api.get_edge_voice_catalog();
      return this.edgeCatalog;
    } catch (e) {
      console.error("Failed to load Edge voice catalog:", e);
      return null;
    }
  },

  populateEdgeVoicesForRegion: function(regionId, selectedVoice) {
    if (!this.edgeCatalog || !this.edgeCatalog.voices) return;
    const voices = this.edgeCatalog.voices[regionId] || [];
    const select = document.getElementById("voiceSelect");
    if (!select) return;
    const prevVal = selectedVoice || select.value;
    select.innerHTML = "";

    voices.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.innerText = v.label;
      opt.dataset.cleanName = v.name;
      opt.dataset.gender = v.gender;
      select.appendChild(opt);
    });

    if (prevVal && Array.from(select.options).some(o => o.value === prevVal)) {
      select.value = prevVal;
    } else if (select.options.length > 0) {
      select.selectedIndex = 0;
    }
  },

  updateTtsUiVisibility: function(engine) {
    const eng = (engine || document.getElementById("ttsSelect")?.value || "").toLowerCase();
    const isEdge = eng.includes("edge");
    const isGemini = eng.startsWith("gemini") || !eng;
    const isLocal = eng.includes("local") && !eng.includes("windows");
    const isWindows = eng.includes("windows") || eng.includes("sapi");

    const edgeRegionGroup = document.getElementById("edgeRegionGroup");
    const accentGroup = document.getElementById("voiceAccentGroup");
    const localGroup = document.getElementById("localTtsGroup");
    const speedGroup = document.getElementById("voiceSpeedGroup");

    const voiceSelect = document.getElementById("voiceSelect");
    const voiceTextInput = document.getElementById("voiceTextInput");
    const voiceSelectLabel = document.getElementById("voiceSelectLabel");
    const voiceSelectHint = document.getElementById("voiceSelectHint");

    const subLeft = document.getElementById("ttsSubSettingsLeft");
    const subRight = document.getElementById("ttsSubSettingsRight");

    if (voiceSelect) {
      voiceSelect.style.display = isLocal ? "none" : "block";
    }
    if (voiceTextInput) {
      voiceTextInput.style.display = isLocal ? "block" : "none";
    }
    if (voiceSelectLabel) {
      voiceSelectLabel.innerText = isLocal ? "AI Output Voice / Model (Local TTS)" : "AI Output Voice";
    }
    if (voiceSelectHint) {
      voiceSelectHint.innerText = isLocal
        ? "Enter your Local TTS model/speaker name (leave blank to use server default)."
        : "Synthesizer voice tailored to your selected TTS engine.";
    }

    if (edgeRegionGroup) {
      edgeRegionGroup.style.display = isEdge ? "flex" : "none";
    }
    if (accentGroup) {
      accentGroup.style.display = isGemini ? "flex" : "none";
    }
    if (localGroup) {
      localGroup.style.display = isLocal ? "flex" : "none";
    }
    if (speedGroup) {
      speedGroup.style.display = isWindows ? "flex" : "none";
    }

    if (subLeft) {
      subLeft.style.display = (isWindows || isLocal) ? "flex" : "none";
    }
    if (subRight) {
      subRight.style.display = (isEdge || isGemini) ? "flex" : "none";
    }
  },

  loadVoices: async function(ttsEngine, selectedVoice) {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const engine = ttsEngine || document.getElementById("ttsSelect")?.value || "gemini-live-native";
      const isEdge = engine.toLowerCase().includes("edge");
      const isLocal = engine.toLowerCase().includes("local") && !engine.toLowerCase().includes("windows");

      if (isLocal) {
        const textInput = document.getElementById("voiceTextInput");
        if (textInput && selectedVoice !== undefined) {
          textInput.value = selectedVoice;
        }
      } else if (isEdge) {
        await this.loadEdgeCatalog();
        const regSelect = document.getElementById("edgeRegionSelect");
        if (regSelect && this.edgeCatalog && this.edgeCatalog.regions) {
          const prevReg = regSelect.value;
          regSelect.innerHTML = "";
          this.edgeCatalog.regions.forEach(r => {
            const opt = document.createElement("option");
            opt.value = r.id;
            opt.innerText = r.label;
            regSelect.appendChild(opt);
          });

          // Determine which region to select
          let targetRegion = "en-US";
          if (selectedVoice) {
            for (const [regId, vList] of Object.entries(this.edgeCatalog.voices || {})) {
              if (vList.some(v => v.id === selectedVoice)) {
                targetRegion = regId;
                break;
              }
            }
          } else if (prevReg && Array.from(regSelect.options).some(o => o.value === prevReg)) {
            targetRegion = prevReg;
          }

          regSelect.value = targetRegion;
          this.populateEdgeVoicesForRegion(targetRegion, selectedVoice);
        }
      } else {
        const voices = await window.pywebview.api.get_available_voices(engine);
        const select = document.getElementById("voiceSelect");
        if (!select) return;
        const prevVal = selectedVoice || select.value;
        select.innerHTML = "";

        voices.forEach(v => {
          const opt = document.createElement("option");
          opt.value = v.name;
          opt.innerText = `${v.name} — ${v.trait} (${v.gender})`;
          select.appendChild(opt);
        });

        if (prevVal && Array.from(select.options).some(o => o.value === prevVal)) {
          select.value = prevVal;
        } else if (select.options.length > 0) {
          select.selectedIndex = 0;
        }
      }

      this.updateTtsUiVisibility(engine);
      this.updateVoiceLabels();
    } catch (e) {
      console.error("Failed to load voices:", e);
    }
  },

  selectAudioDeviceOption: function(selectEl, targetIdx, targetName, followNewDefault = false) {
    if (!selectEl || selectEl.options.length === 0) return;

    const cleanName = (str) => {
      if (!str) return "";
      let s = str.trim();
      if (s.endsWith("kHz)") && s.lastIndexOf(" (") !== -1) {
        s = s.slice(0, s.lastIndexOf(" (")).trim();
      }
      return s.replace(/\[Default\]/gi, "").replace(/^\[\d+\]\s*/, "").trim().toLowerCase();
    };

    const targetIsDefault = followNewDefault || !targetName || /\[default\]/i.test(targetName);
    if (targetIsDefault) {
      const defOpt = Array.from(selectEl.options).find(o => o.dataset.isDefault === "true");
      if (defOpt) {
        selectEl.value = defOpt.value;
        return;
      }
    }

    const wantedName = cleanName(targetName);
    if (wantedName) {
      // Exact device name match first (handles PortAudio index shifts cleanly)
      for (const opt of selectEl.options) {
        const optBase = (opt.dataset.deviceName || cleanName(opt.innerText)).toLowerCase();
        if (optBase === wantedName) {
          selectEl.value = opt.value;
          return;
        }
      }
      // Substring match fallback
      for (const opt of selectEl.options) {
        const optBase = (opt.dataset.deviceName || cleanName(opt.innerText)).toLowerCase();
        if (optBase && (optBase.includes(wantedName) || wantedName.includes(optBase))) {
          selectEl.value = opt.value;
          return;
        }
      }
    }

    if (targetIdx !== undefined && targetIdx !== null) {
      for (const opt of selectEl.options) {
        if (parseInt(opt.value, 10) === parseInt(targetIdx, 10)) {
          selectEl.value = opt.value;
          return;
        }
      }
    }

    const defOpt = Array.from(selectEl.options).find(o => o.dataset.isDefault === "true");
    if (defOpt) {
      selectEl.value = defOpt.value;
    } else if (selectEl.options.length > 0) {
      selectEl.selectedIndex = 0;
    }
  },

  loadAudioDevices: async function(forceRefresh = false, preloadedData = null) {
    if (!preloadedData && (!window.pywebview || !window.pywebview.api)) return;
    try {
      const inSelect = document.getElementById("inputDeviceSelect");
      const outSelect = document.getElementById("outputDeviceSelect");
      if (!inSelect || !outSelect) return;

      const prevInOpt = inSelect.selectedOptions[0];
      const prevOutOpt = outSelect.selectedOptions[0];
      const prevInIdx = inSelect.value;
      const prevOutIdx = outSelect.value;
      const prevInText = prevInOpt ? prevInOpt.innerText : (this.currentConfig?.audio?.input_device_name || "");
      const prevOutText = prevOutOpt ? prevOutOpt.innerText : (this.currentConfig?.audio?.output_device_name || "");
      const prevInDefaultName = Array.from(inSelect.options).find(o => o.dataset.isDefault === "true")?.dataset.deviceName;
      const prevOutDefaultName = Array.from(outSelect.options).find(o => o.dataset.isDefault === "true")?.dataset.deviceName;

      const data = preloadedData || await window.pywebview.api.get_audio_devices(!!forceRefresh);

      inSelect.innerHTML = "";
      outSelect.innerHTML = "";

      if (data.inputs && data.inputs.length > 0) {
        data.inputs.forEach(dev => {
          const opt = document.createElement("option");
          opt.value = dev.index;
          opt.innerText = dev.label || dev.name;
          opt.dataset.deviceName = dev.name || "";
          opt.dataset.isDefault = dev.is_default ? "true" : "false";
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
          opt.dataset.deviceName = dev.name || "";
          opt.dataset.isDefault = dev.is_default ? "true" : "false";
          outSelect.appendChild(opt);
        });
      } else {
        const opt = document.createElement("option");
        opt.value = "-1";
        opt.innerText = "No available speakers detected";
        outSelect.appendChild(opt);
      }

      const newInDefaultName = data.inputs?.find(d => d.is_default)?.name;
      const newOutDefaultName = data.outputs?.find(d => d.is_default)?.name;
      const inDefaultChanged = !!(prevInDefaultName && newInDefaultName && prevInDefaultName !== newInDefaultName);
      const outDefaultChanged = !!(prevOutDefaultName && newOutDefaultName && prevOutDefaultName !== newOutDefaultName);

      const targetInIdx = data.selected_input_index !== undefined ? data.selected_input_index : prevInIdx;
      const targetInName = data.selected_input_name || prevInText;
      const targetOutIdx = data.selected_output_index !== undefined ? data.selected_output_index : prevOutIdx;
      const targetOutName = data.selected_output_name || prevOutText;

      this.selectAudioDeviceOption(inSelect, targetInIdx, targetInName, inDefaultChanged);
      this.selectAudioDeviceOption(outSelect, targetOutIdx, targetOutName, outDefaultChanged);
      this.updateTelemetryDeviceLabels();
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

      if (api.model_id) document.getElementById("modelSelect").value = api.model_id;
      const currentStt = api.stt_model_id || api.stt_endpoint;
      if (currentStt && document.getElementById("sttSelect")) {
        document.getElementById("sttSelect").value = currentStt;
      }

      // TTS Engine, Voice, Accent & Local TTS URL
      const currentTts = api.tts_model_id || api.tts_endpoint || "gemini-live-native";
      if (document.getElementById("ttsSelect")) {
        document.getElementById("ttsSelect").value = currentTts;
      }
      if (document.getElementById("localTtsUrlInput")) {
        document.getElementById("localTtsUrlInput").value = api.local_tts_url || "http://localhost:8880/v1/audio/speech";
      }
      await this.loadAccents();
      if (document.getElementById("voiceAccentSelect")) {
        document.getElementById("voiceAccentSelect").value = api.voice_accent || "default";
      }
      await this.loadVoices(currentTts, api.voice_name);
      if (api.voice_speed !== undefined) {
        const speedVal = parseFloat(api.voice_speed).toFixed(2);
        const speedSlider = document.getElementById("voiceSpeedSlider");
        if (speedSlider) speedSlider.value = api.voice_speed;
        const speedValDisplay = document.getElementById("voiceSpeedVal");
        if (speedValDisplay) speedValDisplay.innerText = `${speedVal}x`;
      }
      this.updateVoiceLabels();
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
      }
      if (audio.ptt_type) {
        this.pttType = audio.ptt_type;
        const pttRadio = document.querySelector(`input[name='pttType'][value='${audio.ptt_type}']`);
        if (pttRadio) pttRadio.checked = true;
      }
      if (audio.ptt_key) this.pttKey = audio.ptt_key;
      if (audio.ptt_key_display) {
        this.pttKeyDisplay = audio.ptt_key_display;
        const disp = document.getElementById("pttKeybindDisplay");
        if (disp) disp.innerText = audio.ptt_key_display;
      }
      if (audio.ptt_vk !== undefined) this.pttVk = audio.ptt_vk;
      if (audio.ptt_modifiers) this.pttModifiers = audio.ptt_modifiers;
      this.updateAudioModeUI(audio.mode || "always_on");
      if (audio.safe_phrase) {
        document.getElementById("safePhraseInput").value = audio.safe_phrase;
        document.getElementById("telSafePhrase").innerText = `"${audio.safe_phrase}"`;
      } else {
        document.getElementById("safePhraseInput").value = `${this.agentName} stop`;
      }

      if (audio.preferred_language && document.getElementById("preferredLanguageSelect")) {
        document.getElementById("preferredLanguageSelect").value = audio.preferred_language;
      }
      if (audio.software_gate !== undefined) {
        document.getElementById("softwareGateCheck").checked = audio.software_gate;
      }

      // Trailing Silence Pause Duration (VAD)
      const silenceVal = cfg.vad_trailing_silence_ms || audio.vad_trailing_silence_ms || 1400;
      const vadSlider = document.getElementById("vadSilenceSlider");
      if (vadSlider) vadSlider.value = silenceVal;
      const vadVal = document.getElementById("vadSilenceValue");
      if (vadVal) vadVal.innerText = `${silenceVal} ms`;

      if (audio.voice_biometrics) {
        const bio = audio.voice_biometrics;
        if (bio.enabled !== undefined && document.getElementById("voiceBiometricsCheck")) {
          document.getElementById("voiceBiometricsCheck").checked = !!bio.enabled;
        }
        if (bio.threshold !== undefined && document.getElementById("bioThresholdSlider")) {
          document.getElementById("bioThresholdSlider").value = bio.threshold;
          const valDisplay = document.getElementById("bioThresholdVal");
          if (valDisplay) valDisplay.innerText = parseFloat(bio.threshold).toFixed(2);
        }
      }
      // Audio Devices
      const inSel = document.getElementById("inputDeviceSelect");
      if (inSel) {
        this.selectAudioDeviceOption(inSel, audio.input_device_index, audio.input_device_name);
      }
      const outSel = document.getElementById("outputDeviceSelect");
      if (outSel) {
        this.selectAudioDeviceOption(outSel, audio.output_device_index, audio.output_device_name);
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

        if (cfg.ui.hud_mode) {
          this.hudMode = cfg.ui.hud_mode;
          const hudModeSel = document.getElementById("defaultHudModeSelect");
          if (hudModeSel) hudModeSel.value = cfg.ui.hud_mode;
        }
        if (cfg.ui.hud_mode_hotkey) {
          this.hudModeKey = cfg.ui.hud_mode_hotkey;
        }
        if (cfg.ui.hud_mode_key_display) {
          this.hudModeKeyDisplay = cfg.ui.hud_mode_key_display;
          const disp = document.getElementById("hudModeKeybindDisplay");
          if (disp) disp.innerText = cfg.ui.hud_mode_key_display;
        }
        if (cfg.ui.hud_mode_vk !== undefined) {
          this.hudModeVk = cfg.ui.hud_mode_vk;
        }
        if (cfg.ui.hud_mode_modifiers) {
          this.hudModeModifiers = cfg.ui.hud_mode_modifiers;
        }
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

      const ttsVal = document.getElementById("ttsSelect")?.value || "gemini-live-native";
      const isLocal = ttsVal.toLowerCase().includes("local") && !ttsVal.toLowerCase().includes("windows");
      const voiceName = isLocal
        ? (document.getElementById("voiceTextInput")?.value.trim() || "")
        : (document.getElementById("voiceSelect")?.value || "");

      const vadSilenceMs = parseInt(document.getElementById("vadSilenceSlider")?.value || "1400", 10);

      const payload = {
        vad_trailing_silence_ms: vadSilenceMs,
        api: {
          new_api_key: newKey,
          agent_name: agentName,
          voice_name: voiceName,
          voice_accent: document.getElementById("voiceAccentSelect")?.value || "default",
          voice_speed: parseFloat(document.getElementById("voiceSpeedSlider")?.value || "1.00"),
          local_tts_url: document.getElementById("localTtsUrlInput")?.value || "http://localhost:8880/v1/audio/speech",
          model_id: document.getElementById("modelSelect").value,
          pipeline_mode: document.getElementById("modelSelect").value.includes("live") ? "live" : "modular",
          stt_model_id: document.getElementById("sttSelect")?.value || "gemini-3.5-transcribe",
          tts_model_id: ttsVal,
          live_model_id: "gemini-3.1-flash-live-preview",
          stt_endpoint: document.getElementById("sttSelect")?.value || "gemini-3.5-transcribe",
          tts_endpoint: ttsVal,
          pro_model_id: document.getElementById("proModelSelect")?.value || "gemini-3.1-pro-preview",
          temperature: parseFloat(document.getElementById("temperatureSlider").value),
          system_instruction: document.getElementById("systemPromptInput").value
        },
        audio: {
          vad_trailing_silence_ms: vadSilenceMs,
          preferred_language: document.getElementById("preferredLanguageSelect")?.value || "en-US",
          voice_biometrics: {
            enabled: document.getElementById("voiceBiometricsCheck")?.checked || false,
            threshold: parseFloat(document.getElementById("bioThresholdSlider")?.value || "0.40")
          },
          mode: mode,
          ptt_type: document.querySelector("input[name='pttType']:checked")?.value || this.pttType || "hold",
          ptt_key: this.pttKey || "Space",
          ptt_key_display: this.pttKeyDisplay || "Space",
          ptt_vk: this.pttVk || 32,
          ptt_modifiers: this.pttModifiers || [],
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
          minimize_to_tray: document.getElementById("minimizeToTrayCheck")?.checked !== false,
          hud_mode: document.getElementById("defaultHudModeSelect")?.value || this.hudMode || "normal",
          hud_mode_hotkey: this.hudModeKey || "Ctrl+Space",
          hud_mode_key_display: this.hudModeKeyDisplay || "Ctrl+Space",
          hud_mode_vk: this.hudModeVk !== undefined ? this.hudModeVk : 32,
          hud_mode_modifiers: this.hudModeModifiers || ["Control"]
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
      try {
        await window.pywebview.api.stop_assistant();
      } catch (err) {
        console.warn("stop_assistant error:", err);
      }
      btn.disabled = false;
      this.isAssistantRunning = false;
      this.updateAssistantButtonState(false);
      this.updateStatus("disconnected", "Assistant stopped.");
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

  resetContext: async function() {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.reset_chat_context) {
      await window.pywebview.api.reset_chat_context();
    }
    this.appendBubble("system", "✨ AI context reset. Starting fresh task.", "SYSTEM");
    this.log("Conversation context reset by user.");
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
    } else if (type === "audio_devices_updated") {
      if (this.currentConfig && this.currentConfig.audio) {
        if (data.selected_input_index !== undefined) {
          this.currentConfig.audio.input_device_index = data.selected_input_index;
        }
        if (data.selected_input_name) {
          this.currentConfig.audio.input_device_name = data.selected_input_name;
        }
        if (data.selected_output_index !== undefined) {
          this.currentConfig.audio.output_device_index = data.selected_output_index;
        }
        if (data.selected_output_name) {
          this.currentConfig.audio.output_device_name = data.selected_output_name;
        }
      }
      this.loadAudioDevices(false, data);
      this.log(
        `Audio hardware updated -> Mic: ${data.selected_input_name || "Default"} | Speaker: ${data.selected_output_name || "Default"}`
      );
    } else if (type === "log_event") {
      this.appendLogEntry(data);
    } else if (type === "telemetry_update") {
      this.updateTelemetryMetrics(data);
    } else if (type === "voice_profile_updated") {
      this.updateVoiceProfileUI(data);
    } else if (type === "mic_status") {
      this.isPttActive = !!data.ptt_active;
      this.updatePttButtonUI();
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
    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    this.appendLogEntry({
      time: timeStr,
      level: "INFO",
      name: "HUD",
      message: msg,
      formatted: `[${timeStr}] [INFO ] [HUD] ${msg}`
    });
  },

  classifyLogEntry: function(entry) {
    const msg = (entry && entry.message) ? String(entry.message) : "";
    const lvl = (entry && entry.level) ? String(entry.level).toUpperCase() : "";
    const name = (entry && entry.name) ? String(entry.name) : "";

    if (lvl === "ERROR" || msg.includes("ERROR") || msg.includes("Exception") || msg.includes("Traceback")) {
      return { category: "error", cssClass: "error" };
    }
    if (lvl === "WARNING" || lvl === "WARN" || msg.includes("WARN")) {
      return { category: "warn", cssClass: "warn" };
    }
    if (msg.includes("[LATENCY") || name === "Latency") {
      return { category: "latency", cssClass: "latency" };
    }
    if (msg.includes("[SCRIPT") || name === "ScriptRunner") {
      return { category: "script", cssClass: "script" };
    }
    if (msg.includes("[TOOL") || name === "ToolDispatcher" || msg.includes("[SEARCH GROUNDING]")) {
      return { category: "tool", cssClass: "tool" };
    }
    if (msg.includes("[STATUS]") || lvl === "DEBUG") {
      return { category: "status", cssClass: "status" };
    }
    return { category: "info", cssClass: "info" };
  },

  shouldShowLog: function(category) {
    if (this.activeLogFilter === "all") return true;
    if (this.activeLogFilter === "script") return category === "script" || category === "tool";
    if (this.activeLogFilter === "latency") return category === "latency";
    if (this.activeLogFilter === "error") return category === "error" || category === "warn";
    return true;
  },

  appendLogEntry: function(entry) {
    const consoleEl = document.getElementById("logConsole");
    if (!consoleEl || !entry) return;

    const classification = this.classifyLogEntry(entry);
    const lineDiv = document.createElement("div");
    lineDiv.className = `log-line ${classification.cssClass}`;
    lineDiv.dataset.category = classification.category;
    lineDiv.textContent = entry.formatted || `[${entry.time || new Date().toLocaleTimeString()}] [${entry.level || 'INFO'}] [${entry.name || 'System'}] ${entry.message || ''}`;

    this.logEntries.push({
      entry: entry,
      category: classification.category,
      element: lineDiv
    });

    if (this.logEntries.length > 500) {
      const removed = this.logEntries.shift();
      if (removed && removed.element && removed.element.parentNode) {
        removed.element.parentNode.removeChild(removed.element);
      }
    }

    if (this.shouldShowLog(classification.category)) {
      lineDiv.style.display = "";
    } else {
      lineDiv.style.display = "none";
    }

    const isNearBottom = (consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight) < 80;
    consoleEl.appendChild(lineDiv);
    if (isNearBottom) {
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  },

  setLogFilter: function(filterName) {
    this.activeLogFilter = filterName;
    document.querySelectorAll(".logs-filter-bar button").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.filter === filterName);
    });
    const consoleEl = document.getElementById("logConsole");
    if (!consoleEl) return;
    for (const item of this.logEntries) {
      item.element.style.display = this.shouldShowLog(item.category) ? "" : "none";
    }
    consoleEl.scrollTop = consoleEl.scrollHeight;
  },

  loadRecentLogs: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_recent_logs) return;
    try {
      const logs = await window.pywebview.api.get_recent_logs();
      if (Array.isArray(logs)) {
        logs.forEach(entry => this.appendLogEntry(entry));
      }
    } catch (e) {
      console.warn("Could not load recent logs:", e);
    }
  },

  updateTelemetryMetrics: function(data) {
    if (!data) return;
    const telTotal = document.getElementById("telTotalLatency");
    const telStt = document.getElementById("telSttLatency");
    const telLlm = document.getElementById("telLlmlatency");
    const telTools = document.getElementById("telToolsLatency");
    const telTts = document.getElementById("telTtsLatency");

    if (telTotal && data.total_ms !== undefined) telTotal.innerText = `${data.total_ms} ms`;
    if (telStt && data.stt_ms !== undefined) telStt.innerText = `${data.stt_ms} ms`;
    if (telLlm && data.llm_ms !== undefined) telLlm.innerText = `${data.llm_ms} ms`;
    if (telTools && data.tools_ms !== undefined) telTools.innerText = `${data.tools_ms} ms`;
    if (telTts && data.tts_ms !== undefined) telTts.innerText = `${data.tts_ms} ms`;
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
  },

  appendBubble: function(type, content, sender) {
    this.renderChatBubble({
      type: type,
      content: content,
      agent_name: sender
    });
  },

  // =========================================================================
  // Voice Biometrics & Calibration Wizard
  // =========================================================================
  loadVoiceProfileStatus: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_voice_profile_status) return;
    try {
      const status = await window.pywebview.api.get_voice_profile_status();
      if (status.success) {
        this.updateVoiceProfileUI(status);
      }
    } catch (e) {
      console.warn("Could not load voice profile status:", e);
    }
  },

  updateVoiceProfileUI: function(data) {
    if (!data) return;
    const badge = document.getElementById("voiceProfileBadge");
    const check = document.getElementById("voiceBiometricsCheck");
    const slider = document.getElementById("bioThresholdSlider");
    const sliderVal = document.getElementById("bioThresholdVal");
    const feedback = document.getElementById("calibrationFeedback");
    const feedbackText = document.getElementById("calibrationFeedbackText");

    const isEnrolled = !!data.enrolled;
    if (badge) {
      if (isEnrolled) {
        badge.className = "card-tag enrolled";
        badge.innerText = data.enabled ? "ACTIVE / ENROLLED" : "ENROLLED (OFF)";
      } else {
        badge.className = "card-tag not-enrolled";
        badge.innerText = "NOT CALIBRATED";
      }
    }

    if (check && data.enabled !== undefined) {
      check.checked = data.enabled;
    }
    if (slider && data.threshold !== undefined) {
      slider.value = data.threshold;
      if (sliderVal) sliderVal.innerText = parseFloat(data.threshold).toFixed(2);
    }

    // Update wizard steps state if enrolled or in-progress
    const staged = data.staged_samples || (isEnrolled ? 3 : 0);
    for (let i = 1; i <= 3; i++) {
      const card = document.getElementById(`calibStep${i}Card`);
      const btn = document.getElementById(`recStep${i}Btn`);
      const pill = document.getElementById(`step${i}Status`);
      if (!card || !btn || !pill) continue;

      const btnText = btn.querySelector(".rec-btn-text");
      if (i <= staged) {
        card.className = "calibration-step-card completed";
        btn.disabled = false;
        if (btnText) btnText.innerText = `RE-RECORD ${i}`;
        pill.className = "step-status-pill completed";
        pill.innerText = "✓ RECORDED";
      } else if (i === staged + 1) {
        card.className = "calibration-step-card active";
        btn.disabled = false;
        if (btnText) btnText.innerText = `RECORD SAMPLE ${i}`;
        pill.className = "step-status-pill pending";
        pill.innerText = "READY";
      } else {
        card.className = "calibration-step-card";
        btn.disabled = true;
        if (btnText) btnText.innerText = `RECORD SAMPLE ${i}`;
        pill.className = "step-status-pill pending";
        pill.innerText = "PENDING";
      }
    }

    if (feedback && feedbackText) {
      if (isEnrolled) {
        feedback.className = "calibration-feedback-banner success";
        feedbackText.innerText = "✓ Voiceprint calibrated and verified! Aether only responds to your voice when filtering is enabled.";
      } else if (staged > 0) {
        feedback.className = "calibration-feedback-banner";
        feedbackText.innerText = `Recorded ${staged} of 3 samples. Please record the next prompt.`;
      } else {
        feedback.className = "calibration-feedback-banner";
        feedbackText.innerText = 'Click "Record Sample 1" and speak the prompt naturally. Each recording captures 3.5 seconds.';
      }
    }
  },

  recordVoiceStep: async function(stepNum) {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.record_calibration_sample) return;
    const btn = document.getElementById(`recStep${stepNum}Btn`);
    const pill = document.getElementById(`step${stepNum}Status`);
    const card = document.getElementById(`calibStep${stepNum}Card`);
    const feedback = document.getElementById("calibrationFeedback");
    const feedbackText = document.getElementById("calibrationFeedbackText");
    const btnText = btn?.querySelector(".rec-btn-text");

    if (btn) {
      btn.disabled = true;
      btn.classList.add("recording");
      if (btnText) btnText.innerText = "RECORDING (3.5s)...";
    }
    if (pill) {
      pill.className = "step-status-pill recording";
      pill.innerText = "RECORDING...";
    }
    if (feedbackText) {
      feedbackText.innerText = `🎙️ Listening... Please read Prompt ${stepNum} clearly now!`;
    }

    try {
      const res = await window.pywebview.api.record_calibration_sample(stepNum);
      if (btn) btn.classList.remove("recording");

      if (res && res.success) {
        this.log(`Voice sample #${stepNum} recorded successfully (SNR: ${res.snr_db || 0} dB, Energy: ${res.rms_energy || 0})`);
        if (pill) {
          pill.className = "step-status-pill completed";
          pill.innerText = "✓ RECORDED";
        }
        if (card) {
          card.className = "calibration-step-card completed";
        }
        if (btn) {
          btn.disabled = false;
          if (btnText) btnText.innerText = `RE-RECORD ${stepNum}`;
        }

        // Enable next step button if available
        if (stepNum < 3) {
          const nextBtn = document.getElementById(`recStep${stepNum + 1}Btn`);
          const nextCard = document.getElementById(`calibStep${stepNum + 1}Card`);
          const nextPill = document.getElementById(`step${stepNum + 1}Status`);
          if (nextBtn) nextBtn.disabled = false;
          if (nextCard) nextCard.className = "calibration-step-card active";
          if (nextPill) nextPill.innerText = "READY";
          if (feedbackText) feedbackText.innerText = `✓ Sample ${stepNum} captured. Proceed to Prompt ${stepNum + 1}.`;
        }

        // If 3 samples collected, finalize voiceprint!
        if (res.sample_count >= 3) {
          if (feedbackText) feedbackText.innerText = "Synthesizing centroid voiceprint and verifying...";
          const threshold = parseFloat(document.getElementById("bioThresholdSlider")?.value || "0.40");
          const finRes = await window.pywebview.api.finalize_voice_profile(threshold, true);
          if (finRes && finRes.success) {
            this.log("Voiceprint enrollment finalized and saved to profile/user_voiceprint.npy");
            this.updateVoiceProfileUI({ enrolled: true, enabled: true, threshold: threshold });
            this.appendBubble("system", "🎯 User Voice Calibration Complete! Aether is now locked to your voiceprint.", "SYSTEM");
          } else {
            if (feedbackText) feedbackText.innerText = `Finalization error: ${finRes.error || "Unknown"}`;
          }
        }
      } else {
        if (btn) {
          btn.disabled = false;
          if (btnText) btnText.innerText = `RETRY SAMPLE ${stepNum}`;
        }
        if (pill) {
          pill.className = "step-status-pill pending";
          pill.innerText = "FAILED";
        }
        if (feedbackText) feedbackText.innerText = `Recording failed: ${res?.error || "Low audio signal"}. Please try again.`;
        this.log(`[VOICE ERROR] ${res?.error || "Failed to capture sample"}`);
      }
    } catch (err) {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("recording");
        if (btnText) btnText.innerText = `RETRY SAMPLE ${stepNum}`;
      }
      if (pill) {
        pill.className = "step-status-pill pending";
        pill.innerText = "ERROR";
      }
      if (feedbackText) feedbackText.innerText = `Error: ${err}`;
      console.error("Calibration record error:", err);
    }
  },

  retrainVoiceProfile: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.delete_voice_profile) return;
    try {
      const res = await window.pywebview.api.delete_voice_profile();
      if (res && res.success) {
        this.log("Voiceprint profile reset.");
        this.updateVoiceProfileUI({ enrolled: false, enabled: false, staged_samples: 0 });
      }
    } catch (e) {
      console.error("Failed to reset voice profile:", e);
    }
  },

  // =========================================================================
  // User Preferences & Knowledge Store (Concept #3)
  // =========================================================================
  userFactsCache: [],

  setupPreferencesHandlers: function() {
    // Category Toggles
    const toggles = [
      { id: "prefDatesToggle", category: "dates" },
      { id: "prefInterestsToggle", category: "interests" },
      { id: "prefWorkToggle", category: "work" },
      { id: "prefGeneralToggle", category: "general" }
    ];

    toggles.forEach(({ id, category }) => {
      const el = document.getElementById(id);
      if (el) {
        el.addEventListener("change", async (e) => {
          const enabled = e.target.checked;
          const leadSelect = document.getElementById("prefDatesLeadTime");
          const leadTime = (category === "dates" && leadSelect) ? parseInt(leadSelect.value, 10) : null;
          await this.saveCategoryPreference(category, enabled, leadTime);
        });
      }
    });

    // Dates Lead Time Select
    const leadSelect = document.getElementById("prefDatesLeadTime");
    if (leadSelect) {
      leadSelect.addEventListener("change", async (e) => {
        const datesToggle = document.getElementById("prefDatesToggle");
        const enabled = datesToggle ? datesToggle.checked : true;
        const leadTime = parseInt(e.target.value, 10);
        await this.saveCategoryPreference("dates", enabled, leadTime);
      });
    }

    // Add Fact Button
    const addFactBtn = document.getElementById("addFactBtn");
    if (addFactBtn) {
      addFactBtn.addEventListener("click", () => this.handleAddFact());
    }

    // Search and Category Filter
    const searchInput = document.getElementById("factsSearchInput");
    if (searchInput) {
      searchInput.addEventListener("input", () => this.filterAndRenderFacts());
    }

    const catFilter = document.getElementById("factsCategoryFilter");
    if (catFilter) {
      catFilter.addEventListener("change", () => this.filterAndRenderFacts());
    }

    // User Name Save Button & Enter Key / Change
    const saveNameBtn = document.getElementById("saveUserNameBtn");
    const nameInput = document.getElementById("userNameInput");
    if (saveNameBtn) {
      saveNameBtn.addEventListener("click", () => this.handleSaveUserName());
    }
    if (nameInput) {
      nameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          this.handleSaveUserName();
        }
      });
      nameInput.addEventListener("change", () => this.handleSaveUserName());
    }

    // Callsign Usage Frequency Slider
    const freqSlider = document.getElementById("callsignFrequencySlider");
    const freqVal = document.getElementById("callsignFrequencyVal");
    const freqLabels = ["Never", "Seldom", "Often", "Always"];
    const freqKeys = ["never", "seldom", "often", "always"];
    if (freqSlider) {
      freqSlider.addEventListener("input", (e) => {
        const val = parseInt(e.target.value, 10);
        if (freqVal) freqVal.innerText = freqLabels[val] || "Often";
      });
      freqSlider.addEventListener("change", (e) => {
        const val = parseInt(e.target.value, 10);
        const freqKey = freqKeys[val] || "often";
        this.handleSaveCallsignFrequency(freqKey);
      });
    }

    // Refresh Facts Button
    const refreshBtn = document.getElementById("refreshFactsBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => this.loadUserFacts());
    }

    // Custom Dictionary Event Listeners
    const addDictBtn = document.getElementById("addDictBtn");
    if (addDictBtn) {
      addDictBtn.addEventListener("click", () => this.handleAddDictionaryTerm());
    }
    const dictTermInput = document.getElementById("newDictTerm");
    const dictPhoneticInput = document.getElementById("newDictPhonetic");
    [dictTermInput, dictPhoneticInput].forEach(inp => {
      if (inp) {
        inp.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            this.handleAddDictionaryTerm();
          }
        });
      }
    });

    const dictSearchInput = document.getElementById("dictSearchInput");
    if (dictSearchInput) {
      dictSearchInput.addEventListener("input", () => this.filterAndRenderDictionary());
    }
    const dictCatFilter = document.getElementById("dictCategoryFilter");
    if (dictCatFilter) {
      dictCatFilter.addEventListener("change", () => this.filterAndRenderDictionary());
    }

    const refreshDictBtn = document.getElementById("refreshDictBtn");
    if (refreshDictBtn) {
      refreshDictBtn.addEventListener("click", () => this.loadDictionaryTerms());
    }
  },

  loadUserName: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_user_name) return;
    try {
      const res = await window.pywebview.api.get_user_name();
      if (res && res.success && res.name) {
        const input = document.getElementById("userNameInput");
        if (input) input.value = res.name;
      }
      await this.loadCallsignFrequency();
    } catch (e) {
      console.error("Failed to load user name:", e);
    }
  },

  loadCallsignFrequency: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_callsign_frequency) return;
    try {
      const res = await window.pywebview.api.get_callsign_frequency();
      if (res && res.success) {
        const slider = document.getElementById("callsignFrequencySlider");
        const valEl = document.getElementById("callsignFrequencyVal");
        const freqLabels = ["Never", "Seldom", "Often", "Always"];
        const level = res.level !== undefined ? res.level : 2;
        if (slider) slider.value = level;
        if (valEl) valEl.innerText = freqLabels[level] || "Often";
      }
    } catch (e) {
      console.error("Failed to load callsign frequency:", e);
    }
  },

  handleSaveCallsignFrequency: async function(frequency) {
    const statusEl = document.getElementById("callsignFreqStatus");
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_callsign_frequency) return;
    try {
      const res = await window.pywebview.api.set_callsign_frequency(frequency);
      if (res && res.success) {
        if (statusEl) {
          const capitalized = frequency.charAt(0).toUpperCase() + frequency.slice(1);
          statusEl.innerText = `✓ Saved frequency: ${capitalized}`;
          statusEl.style.color = "#34c759";
          setTimeout(() => { if (statusEl) statusEl.innerText = ""; }, 2500);
        }
        this.log(`Callsign usage frequency updated: ${frequency}`);
      } else {
        if (statusEl) {
          statusEl.innerText = `Error: ${res?.error || 'Failed to save frequency'}`;
          statusEl.style.color = "var(--status-red)";
        }
      }
    } catch (e) {
      console.error("Failed to save callsign frequency:", e);
    }
  },

  handleSaveUserName: async function() {
    const input = document.getElementById("userNameInput");
    const statusEl = document.getElementById("userNameStatus");
    const name = input?.value?.trim() || "";
    if (!name) return;

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_user_name) return;
    try {
      const res = await window.pywebview.api.set_user_name(name);
      if (res && res.success) {
        if (statusEl) {
          const names = name.split(";").map(n => n.trim()).filter(Boolean);
          const nameDisplay = names.length > 1 ? names.join(", ") : name;
          statusEl.innerText = `✓ Saved callsign(s): ${nameDisplay}`;
          statusEl.style.color = "#34c759";
          setTimeout(() => { if (statusEl) statusEl.innerText = ""; }, 3000);
        }
        this.log(`Preferred user callsigns updated: ${name}`);
      } else {
        if (statusEl) {
          statusEl.innerText = `Error: ${res?.error || 'Failed to save name'}`;
          statusEl.style.color = "var(--status-red)";
        }
      }
    } catch (e) {
      console.error("Failed to save user name:", e);
    }
  },

  saveCategoryPreference: async function(category, enabled, leadTimeDays) {
    const feedback = document.getElementById("prefSaveFeedback");
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.update_user_preference) return;
    try {
      const res = await window.pywebview.api.update_user_preference(category, enabled, leadTimeDays);
      if (res && res.success && feedback) {
        feedback.innerText = `✓ Updated preference for ${category}`;
        feedback.style.opacity = "1";
        setTimeout(() => { if (feedback) feedback.style.opacity = "0"; }, 2500);
      }
    } catch (e) {
      console.error(`Failed to update preference for ${category}:`, e);
      if (feedback) {
        feedback.innerText = `Error saving preference: ${e}`;
        feedback.style.opacity = "1";
      }
    }
  },

  loadUserPreferences: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_user_preferences) return;
    try {
      const res = await window.pywebview.api.get_user_preferences();
      if (res && res.success && res.preferences) {
        res.preferences.forEach(pref => {
          const cat = pref.category;
          const enabled = Boolean(pref.enabled);
          if (cat === "dates") {
            const toggle = document.getElementById("prefDatesToggle");
            if (toggle) toggle.checked = enabled;
            const lead = document.getElementById("prefDatesLeadTime");
            if (lead && pref.lead_time_days !== undefined && pref.lead_time_days !== null) {
              lead.value = String(pref.lead_time_days);
            }
          } else if (cat === "interests") {
            const toggle = document.getElementById("prefInterestsToggle");
            if (toggle) toggle.checked = enabled;
          } else if (cat === "work") {
            const toggle = document.getElementById("prefWorkToggle");
            if (toggle) toggle.checked = enabled;
          } else if (cat === "general") {
            const toggle = document.getElementById("prefGeneralToggle");
            if (toggle) toggle.checked = enabled;
          }
        });
      }
    } catch (e) {
      console.error("Failed to load user preferences:", e);
    }
  },

  loadUserFacts: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_user_facts) return;
    try {
      const res = await window.pywebview.api.get_user_facts();
      if (res && res.success) {
        this.userFactsCache = res.facts || [];
        this.filterAndRenderFacts();
      }
    } catch (e) {
      console.error("Failed to load user facts:", e);
    }
  },

  filterAndRenderFacts: function() {
    const searchVal = (document.getElementById("factsSearchInput")?.value || "").toLowerCase().trim();
    const catVal = document.getElementById("factsCategoryFilter")?.value || "all";

    const filtered = this.userFactsCache.filter(f => {
      const matchesCategory = (catVal === "all" || f.category === catVal);
      const matchesSearch = !searchVal || 
        (f.key && f.key.toLowerCase().includes(searchVal)) || 
        (f.value && f.value.toLowerCase().includes(searchVal)) ||
        (f.category && f.category.toLowerCase().includes(searchVal));
      return matchesCategory && matchesSearch;
    });

    this.renderFactsTable(filtered);
  },

  renderFactsTable: function(facts) {
    const tbody = document.getElementById("factsTableBody");
    const emptyState = document.getElementById("factsEmptyState");
    const countBadge = document.getElementById("factsCountBadge");

    if (countBadge) {
      countBadge.innerText = `${this.userFactsCache.length} FACT${this.userFactsCache.length === 1 ? '' : 'S'}`;
    }

    if (!tbody) return;
    tbody.innerHTML = "";

    if (!facts || facts.length === 0) {
      if (emptyState) emptyState.style.display = "block";
      return;
    }

    if (emptyState) emptyState.style.display = "none";

    facts.forEach(fact => {
      const tr = document.createElement("tr");

      let dateDisplay = "";
      const ts = fact.last_updated || fact.updated_at;
      if (ts) {
        try {
          const d = new Date(ts);
          dateDisplay = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        } catch (e) {
          dateDisplay = ts.split("T")[0] || ts;
        }
      }

      tr.innerHTML = `
        <td><span class="fact-badge ${this.escapeHtml(fact.category)}">${this.escapeHtml(fact.category)}</span></td>
        <td class="fact-key">${this.escapeHtml(fact.key)}</td>
        <td class="fact-value">${this.escapeHtml(fact.value)}</td>
        <td class="fact-type">${this.escapeHtml(fact.data_type || "string")}</td>
        <td class="fact-time">${this.escapeHtml(dateDisplay)}</td>
        <td style="text-align: center;">
          <button type="button" class="fact-delete-btn" data-id="${fact.id}" title="Forget / Delete this fact">🗑️ DELETE</button>
        </td>
      `;

      const delBtn = tr.querySelector(".fact-delete-btn");
      if (delBtn) {
        delBtn.addEventListener("click", () => this.handleDeleteFact(fact.id, fact.category, fact.key));
      }

      tbody.appendChild(tr);
    });
  },

  handleAddFact: async function() {
    const catEl = document.getElementById("newFactCategory");
    const keyEl = document.getElementById("newFactKey");
    const valEl = document.getElementById("newFactValue");
    const typeEl = document.getElementById("newFactType");
    const statusEl = document.getElementById("addFactStatus");

    const category = catEl?.value || "general";
    const key = keyEl?.value?.trim();
    const value = valEl?.value?.trim();
    const dataType = typeEl?.value || "string";

    if (!key || !value) {
      if (statusEl) {
        statusEl.innerText = "⚠️ Please provide both a key and a value.";
        statusEl.style.color = "var(--status-yellow)";
      }
      return;
    }

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.add_user_fact) return;

    try {
      const res = await window.pywebview.api.add_user_fact(category, key, value, dataType);
      if (res && res.success) {
        if (keyEl) keyEl.value = "";
        if (valEl) valEl.value = "";
        if (statusEl) {
          statusEl.innerText = `✓ Remembered: [${category}] ${key}`;
          statusEl.style.color = "#34c759";
          setTimeout(() => { if (statusEl) statusEl.innerText = ""; }, 3000);
        }
        await this.loadUserFacts();
      } else {
        if (statusEl) {
          statusEl.innerText = `Error: ${res?.error || 'Failed to add fact'}`;
          statusEl.style.color = "var(--status-red)";
        }
      }
    } catch (e) {
      console.error("Failed to add user fact:", e);
      if (statusEl) {
        statusEl.innerText = `Error: ${e}`;
        statusEl.style.color = "var(--status-red)";
      }
    }
  },

  handleDeleteFact: async function(factId, category, key) {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.delete_user_fact) return;
    try {
      const res = await window.pywebview.api.delete_user_fact(factId);
      if (res && res.success) {
        this.log(`Deleted user fact: [${category}] ${key}`);
        await this.loadUserFacts();
      }
    } catch (e) {
      console.error("Failed to delete user fact:", e);
    }
  },

  // =========================================================================
  // Custom Lexicon & Phonetic Dictionary (STT/TTS Biasing)
  // =========================================================================
  dictionaryCache: [],

  loadDictionaryTerms: async function() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_dictionary_terms) return;
    try {
      const res = await window.pywebview.api.get_dictionary_terms();
      if (res && res.success) {
        this.dictionaryCache = res.terms || [];
        this.filterAndRenderDictionary();
      }
    } catch (e) {
      console.error("Failed to load dictionary terms:", e);
    }
  },

  filterAndRenderDictionary: function() {
    const searchVal = (document.getElementById("dictSearchInput")?.value || "").toLowerCase().trim();
    const catVal = document.getElementById("dictCategoryFilter")?.value || "all";

    const filtered = this.dictionaryCache.filter(item => {
      const matchesCategory = (catVal === "all" || (item.category || "").toLowerCase() === catVal.toLowerCase());
      const matchesSearch = !searchVal ||
        (item.term && item.term.toLowerCase().includes(searchVal)) ||
        (item.phonetic_guide && item.phonetic_guide.toLowerCase().includes(searchVal)) ||
        (item.category && item.category.toLowerCase().includes(searchVal));
      return matchesCategory && matchesSearch;
    });

    this.renderDictionaryTable(filtered);
  },

  renderDictionaryTable: function(terms) {
    const tbody = document.getElementById("dictTableBody");
    const emptyState = document.getElementById("dictEmptyState");
    const countBadge = document.getElementById("dictCountBadge");

    if (countBadge) {
      countBadge.innerText = `${this.dictionaryCache.length} WORD${this.dictionaryCache.length === 1 ? '' : 'S'}`;
    }

    if (!tbody) return;
    tbody.innerHTML = "";

    if (!terms || terms.length === 0) {
      if (emptyState) emptyState.style.display = "block";
      return;
    }

    if (emptyState) emptyState.style.display = "none";

    terms.forEach(item => {
      const tr = document.createElement("tr");

      let dateDisplay = "";
      const ts = item.created_at;
      if (ts) {
        try {
          const d = new Date(ts);
          dateDisplay = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        } catch (e) {
          dateDisplay = ts.split("T")[0] || ts;
        }
      }

      tr.innerHTML = `
        <td><span class="fact-badge ${this.escapeHtml(item.category || 'name')}">${this.escapeHtml(item.category || 'name')}</span></td>
        <td class="fact-key" style="font-weight: 600; color: #fff;">${this.escapeHtml(item.term)}</td>
        <td class="fact-value" style="font-style: italic; color: var(--win-accent);">${this.escapeHtml(item.phonetic_guide)}</td>
        <td class="fact-time">${this.escapeHtml(dateDisplay)}</td>
        <td style="text-align: center;">
          <button type="button" class="dict-delete-btn" data-term="${this.escapeHtml(item.term)}" title="Delete word from dictionary">✕</button>
        </td>
      `;

      const delBtn = tr.querySelector(".dict-delete-btn");
      if (delBtn) {
        delBtn.addEventListener("click", () => this.handleDeleteDictionaryTerm(item.term));
      }

      tbody.appendChild(tr);
    });
  },

  handleAddDictionaryTerm: async function() {
    const termEl = document.getElementById("newDictTerm");
    const phoneticEl = document.getElementById("newDictPhonetic");
    const catEl = document.getElementById("newDictCategory");
    const statusEl = document.getElementById("addDictStatus");

    const term = termEl?.value?.trim();
    const phonetic = phoneticEl?.value?.trim();
    const category = catEl?.value || "name";

    if (!term || !phonetic) {
      if (statusEl) {
        statusEl.innerText = "⚠️ Please provide both a term and a pronunciation hint.";
        statusEl.style.color = "var(--status-yellow)";
      }
      return;
    }

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.add_dictionary_term) return;

    try {
      const res = await window.pywebview.api.add_dictionary_term(term, phonetic, category);
      if (res && res.success) {
        if (termEl) termEl.value = "";
        if (phoneticEl) phoneticEl.value = "";
        if (statusEl) {
          statusEl.innerText = `✓ Added word: '${term}' (${phonetic})`;
          statusEl.style.color = "#34c759";
          setTimeout(() => { if (statusEl) statusEl.innerText = ""; }, 3000);
        }
        this.log(`Added dictionary term: '${term}' -> '${phonetic}' (${category})`);
        await this.loadDictionaryTerms();
      } else {
        if (statusEl) {
          statusEl.innerText = `Error: ${res?.error || 'Failed to add word'}`;
          statusEl.style.color = "var(--status-red)";
        }
      }
    } catch (e) {
      console.error("Failed to add dictionary term:", e);
      if (statusEl) {
        statusEl.innerText = `Error: ${e}`;
        statusEl.style.color = "var(--status-red)";
      }
    }
  },

  handleDeleteDictionaryTerm: async function(term) {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.remove_dictionary_term) return;
    try {
      const res = await window.pywebview.api.remove_dictionary_term(term);
      if (res && res.success) {
        this.log(`Deleted dictionary term: '${term}'`);
        await this.loadDictionaryTerms();
      }
    } catch (e) {
      console.error("Failed to delete dictionary term:", e);
    }
  },

  // =========================================================================
  // Stored Chats & Manifest Browser Methods
  // =========================================================================
  setupStoredChatsHandlers: function() {
    const searchInput = document.getElementById("storedChatsSearch");
    if (searchInput) {
      let debounceTimer = null;
      searchInput.addEventListener("input", (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          this.loadStoredSessions(e.target.value.trim());
        }, 250);
      });
    }

    const refreshBtn = document.getElementById("refreshStoredChatsBtn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => {
        const query = document.getElementById("storedChatsSearch")?.value?.trim() || "";
        this.loadStoredSessions(query);
      });
    }

    const clearAllBtn = document.getElementById("clearAllChatsBtn");
    if (clearAllBtn) {
      clearAllBtn.addEventListener("click", () => this.clearAllStoredSessions());
    }

    const deleteCurrentBtn = document.getElementById("deleteCurrentSessionBtn");
    if (deleteCurrentBtn) {
      deleteCurrentBtn.addEventListener("click", () => {
        if (this.selectedStoredSessionId) {
          this.deleteStoredSession(this.selectedStoredSessionId);
        }
      });
    }
  },

  loadStoredSessions: async function(query = "") {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_stored_sessions) {
      return;
    }

    try {
      const res = await window.pywebview.api.get_stored_sessions(query);
      if (res && res.success) {
        this.storedSessions = res.sessions || [];
        const countEl = document.getElementById("storedChatsCount");
        if (countEl) {
          countEl.innerText = `${this.storedSessions.length} SESSION${this.storedSessions.length === 1 ? '' : 'S'}`;
        }
        this.renderStoredSessionsList(this.storedSessions);
        
        if (this.selectedStoredSessionId) {
          const exists = this.storedSessions.some(s => s.session_id === this.selectedStoredSessionId);
          if (exists) {
            this.selectStoredSession(this.selectedStoredSessionId);
          } else {
            this.resetStoredSessionDetail();
          }
        }
      } else {
        console.error("Failed to load stored sessions:", res?.error);
      }
    } catch (e) {
      console.error("Error calling get_stored_sessions:", e);
    }
  },

  renderStoredSessionsList: function(sessions) {
    const listEl = document.getElementById("storedSessionsList");
    if (!listEl) return;

    if (!sessions || sessions.length === 0) {
      listEl.innerHTML = `<div class="stored-empty-state">No archived chat sessions found.</div>`;
      return;
    }

    listEl.innerHTML = sessions.map(s => {
      const isSelected = s.session_id === this.selectedStoredSessionId;
      const previewText = this.escapeHtml(s.preview || "No user input in this session.");
      const dateStr = s.date || "Unknown date";
      const timeStr = s.time || "";
      const turnCount = s.turn_count || 0;
      const durationSec = s.duration_seconds || 0;

      const topics = (s.topics || []).slice(0, 3);
      const topicChips = topics.map(t => `<span class="session-tag-chip">${this.escapeHtml(t)}</span>`).join("");

      return `
        <div class="session-card ${isSelected ? 'selected' : ''}" data-session-id="${this.escapeHtml(s.session_id)}">
          <div class="session-card-header">
            <span class="session-card-date">${this.escapeHtml(dateStr)} ${this.escapeHtml(timeStr)}</span>
            <button type="button" class="session-card-del-btn" title="Delete session transcript" data-del-id="${this.escapeHtml(s.session_id)}">✕</button>
          </div>
          <div class="session-card-preview">${previewText}</div>
          <div class="session-card-meta">
            <span>${turnCount} turn${turnCount === 1 ? '' : 's'}</span> · 
            <span>${durationSec}s</span>
          </div>
          ${topicChips ? `<div class="session-card-tags">${topicChips}</div>` : ''}
        </div>
      `;
    }).join("");

    listEl.querySelectorAll(".session-card").forEach(card => {
      const sid = card.dataset.sessionId;
      card.addEventListener("click", () => {
        this.selectStoredSession(sid);
      });
    });

    listEl.querySelectorAll(".session-card-del-btn").forEach(btn => {
      const sid = btn.dataset.delId;
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.deleteStoredSession(sid);
      });
    });
  },

  selectStoredSession: async function(sessionId) {
    if (!sessionId) return;
    this.selectedStoredSessionId = sessionId;

    const listEl = document.getElementById("storedSessionsList");
    if (listEl) {
      listEl.querySelectorAll(".session-card").forEach(card => {
        if (card.dataset.sessionId === sessionId) {
          card.classList.add("selected");
        } else {
          card.classList.remove("selected");
        }
      });
    }

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_session_transcript) {
      return;
    }

    try {
      const res = await window.pywebview.api.get_session_transcript(sessionId);
      if (res && res.success && res.session) {
        this.renderStoredSessionDetail(res.session);
      } else {
        console.error("Failed to load session transcript:", res?.error);
      }
    } catch (e) {
      console.error("Error calling get_session_transcript:", e);
    }
  },

  renderStoredSessionDetail: function(session) {
    const placeholder = document.getElementById("storedDetailPlaceholder");
    const content = document.getElementById("storedDetailContent");
    if (placeholder) placeholder.style.display = "none";
    if (content) content.style.display = "flex";

    const sidEl = document.getElementById("detailSessionId");
    if (sidEl) sidEl.innerText = session.session_id || "";

    const dateEl = document.getElementById("detailSessionDate");
    if (dateEl) dateEl.innerText = session.date || "";

    const timeEl = document.getElementById("detailSessionTime");
    if (timeEl) timeEl.innerText = session.time || "";

    const turnsCountEl = document.getElementById("detailSessionTurns");
    if (turnsCountEl) {
      const tc = session.turn_count || 0;
      turnsCountEl.innerText = `${tc} turn${tc === 1 ? '' : 's'}`;
    }

    const durEl = document.getElementById("detailSessionDuration");
    if (durEl) durEl.innerText = `${session.duration_seconds || 0}s`;

    const fpathEl = document.getElementById("detailFilePath");
    if (fpathEl) fpathEl.innerText = `data/chats/${session.session_id}.json`;

    const manifest = session.manifest;
    const badgeEl = document.getElementById("manifestStatusBadge");
    if (badgeEl) {
      if (manifest) {
        badgeEl.className = "manifest-status-badge indexed";
        badgeEl.innerText = "INDEXED";
      } else {
        badgeEl.className = "manifest-status-badge pending";
        badgeEl.innerText = "NO CARD";
      }
    }

    const renderTagCloud = (containerId, items, pillClass) => {
      const container = document.getElementById(containerId);
      if (!container) return;
      if (!items || items.length === 0) {
        container.innerHTML = `<span class="empty-tag-placeholder">None recorded</span>`;
      } else {
        container.innerHTML = items.map(it => 
          `<span class="manifest-pill ${pillClass}">${this.escapeHtml(String(it))}</span>`
        ).join("");
      }
    };

    renderTagCloud("detailTopicsList", manifest?.topics, "topic");
    renderTagCloud("detailEntitiesList", manifest?.entities, "entity");
    renderTagCloud("detailActionsList", manifest?.actions, "action");
    renderTagCloud("detailUnresolvedList", manifest?.unresolved, "unresolved");

    const turnsContainer = document.getElementById("storedTranscriptTurns");
    if (turnsContainer) {
      const turns = session.turns || [];
      if (turns.length === 0) {
        turnsContainer.innerHTML = `<div class="stored-empty-state">No recorded dialogue turns for this session.</div>`;
      } else {
        turnsContainer.innerHTML = turns.map(t => {
          const role = (t.role || "user").toLowerCase();
          const roleLabel = role === "user" ? "USER" : (this.agentName || "AETHER").toUpperCase();
          const timeStr = t.timestamp ? new Date(t.timestamp * 1000).toLocaleTimeString() : "";
          const text = this.escapeHtml(t.text || "");
          const tools = t.tools_used || [];
          const toolsBadges = tools.map(tool => 
            `<span class="stored-tool-badge">🔧 ${this.escapeHtml(tool)}</span>`
          ).join("");

          return `
            <div class="stored-turn-bubble ${role}">
              <div class="stored-turn-meta">
                <span class="stored-turn-role">${this.escapeHtml(roleLabel)}</span>
                <span class="stored-turn-time">${this.escapeHtml(timeStr)}</span>
              </div>
              <div class="stored-turn-text">${text}</div>
              ${toolsBadges ? `<div class="stored-tools-badges">${toolsBadges}</div>` : ''}
            </div>
          `;
        }).join("");
      }
    }
  },

  resetStoredSessionDetail: function() {
    this.selectedStoredSessionId = null;
    const placeholder = document.getElementById("storedDetailPlaceholder");
    const content = document.getElementById("storedDetailContent");
    if (placeholder) placeholder.style.display = "flex";
    if (content) content.style.display = "none";
  },

  deleteStoredSession: async function(sessionId) {
    if (!sessionId) return;
    if (!confirm(`Are you sure you want to permanently delete session "${sessionId}"?\nThis removes both the transcript file and semantic index entry.`)) {
      return;
    }

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.delete_stored_session) {
      return;
    }

    try {
      const res = await window.pywebview.api.delete_stored_session(sessionId);
      if (res && res.success) {
        this.log(`Deleted session "${sessionId}".`);
        if (this.selectedStoredSessionId === sessionId) {
          this.resetStoredSessionDetail();
        }
        const query = document.getElementById("storedChatsSearch")?.value?.trim() || "";
        await this.loadStoredSessions(query);
      } else {
        alert(`Failed to delete session: ${res?.error || 'Unknown error'}`);
      }
    } catch (e) {
      console.error("Error deleting session:", e);
      alert(`Error deleting session: ${e}`);
    }
  },

  clearAllStoredSessions: async function() {
    if (!confirm("Are you sure you want to permanently purge ALL archived sessions?\nThis action cannot be undone and deletes all transcript JSON files and SQLite indexes.")) {
      return;
    }

    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.clear_all_stored_sessions) {
      return;
    }

    try {
      const res = await window.pywebview.api.clear_all_stored_sessions();
      if (res && res.success) {
        this.log("Purged all stored session transcripts and SQLite index records.");
        this.resetStoredSessionDetail();
        await this.loadStoredSessions("");
      } else {
        alert(`Failed to purge sessions: ${res?.error || 'Unknown error'}`);
      }
    } catch (e) {
      console.error("Error clearing all sessions:", e);
      alert(`Error clearing all sessions: ${e}`);
    }
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

function updateSilenceDisplay(val) {
    const el = document.getElementById("vadSilenceValue");
    if (el) el.innerText = `${val} ms`;
}

async function persistSilenceSetting(val) {
    const silenceMs = parseInt(val, 10);
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.update_vad_silence) {
            await window.pywebview.api.update_vad_silence(silenceMs);
        }
    } catch (err) {
        console.error("Failed to persist VAD silence setting:", err);
    }
}

async function loadSettings() {
    if (window.pywebview && window.pywebview.api) {
        try {
            const config = await window.pywebview.api.get_config();
            if (config && config.vad_trailing_silence_ms) {
                const slider = document.getElementById("vadSilenceSlider");
                if (slider) slider.value = config.vad_trailing_silence_ms;
                const valDisp = document.getElementById("vadSilenceValue");
                if (valDisp) valDisp.innerText = `${config.vad_trailing_silence_ms} ms`;
            }
        } catch (err) {
            console.error("Failed to load VAD silence settings:", err);
        }
    }
}

window.updateSilenceDisplay = updateSilenceDisplay;
window.persistSilenceSetting = persistSilenceSetting;
window.loadSettings = loadSettings;

