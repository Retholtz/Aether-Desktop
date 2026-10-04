/**
 * Aether Desktop - UI Main Controller
 * Handles global navigation, tab switching listeners, and games tab initialization.
 */

document.addEventListener("DOMContentLoaded", () => {
    const gamesBtn = document.getElementById("btn-tab-games") ||
                     document.querySelector('[data-tab="tab-games"]') ||
                     document.querySelector('[data-tab="games"]');
    if (gamesBtn) {
        gamesBtn.addEventListener("click", () => {
            if (typeof loadGamesUI === "function") {
                loadGamesUI();
            }
        });
    }
});
