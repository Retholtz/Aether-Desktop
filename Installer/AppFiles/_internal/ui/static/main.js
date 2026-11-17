/**
 * Aether Desktop - UI Main Controller
 * Handles global navigation, tab switching listeners, and games tab initialization.
 */

document.addEventListener("DOMContentLoaded", () => {
    // Tab click handlers are wired once via window.aetherUI.setupTabs() in app.js.
    // Only attach fallback listeners here if a button was not already wired by setupTabs().
    const gamesBtn = document.getElementById("btn-tab-games") ||
                     document.querySelector('[data-tab="tab-games"]') ||
                     document.querySelector('[data-tab="games"]');
    if (gamesBtn && gamesBtn.dataset.tabWired !== "1") {
        gamesBtn.addEventListener("click", () => {
            if (typeof loadGamesUI === "function") {
                loadGamesUI();
            }
        });
    }

    const programsBtn = document.getElementById("btn-tab-programs") ||
                        document.querySelector('[data-tab="tab-programs"]') ||
                        document.querySelector('[data-tab="programs"]');
    if (programsBtn && programsBtn.dataset.tabWired !== "1") {
        programsBtn.addEventListener("click", () => {
            if (typeof loadProgramsUI === "function") {
                loadProgramsUI();
            }
        });
    }

    const settingsBtn = document.getElementById("btn-tab-settings") ||
                        document.querySelector('[data-tab="tab-settings"]') ||
                        document.querySelector('[data-tab="settings"]');
    if (settingsBtn && settingsBtn.dataset.tabWired !== "1") {
        settingsBtn.addEventListener("click", () => {
            if (typeof loadSettingsUI === "function") {
                loadSettingsUI();
            }
        });
    }

    const accountsBtn = document.getElementById("btn-tab-accounts") ||
                        document.querySelector('[data-tab="tab-accounts"]') ||
                        document.querySelector('[data-tab="accounts"]');
    if (accountsBtn && accountsBtn.dataset.tabWired !== "1") {
        accountsBtn.addEventListener("click", () => {
            if (typeof loadOAuthStatusUI === "function") {
                loadOAuthStatusUI();
            }
        });
    }
});

