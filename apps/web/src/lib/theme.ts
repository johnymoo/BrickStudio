const STORAGE_KEY = "brickstudio-theme";

export type Theme = "light" | "dark" | "system";

function getSystemPreference(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(resolved: "light" | "dark") {
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

export function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
  const resolved = stored === "light" || stored === "dark" ? stored : getSystemPreference();
  applyTheme(resolved);

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    const current = localStorage.getItem(STORAGE_KEY);
    if (!current || current === "system") {
      applyTheme(e.matches ? "dark" : "light");
    }
  });
}

export function setTheme(theme: Theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  const resolved = theme === "system" ? getSystemPreference() : theme;
  applyTheme(resolved);
}

export function getTheme(): Theme {
  return (localStorage.getItem(STORAGE_KEY) as Theme) ?? "system";
}
