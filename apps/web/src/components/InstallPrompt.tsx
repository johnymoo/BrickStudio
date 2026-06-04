import { useEffect, useState } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/**
 * Listens for the browser's `beforeinstallprompt` event and surfaces a
 * small banner that lets the user install the PWA.
 *
 * No-ops in browsers that don't support the API or once installed.
 */
export function InstallPrompt() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const onBefore = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onBefore);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBefore);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed || dismissed || !deferred) return null;

  return (
    <div
      className="fixed bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-full border border-slate-700 bg-slate-900/95 px-4 py-2 text-sm shadow-lg"
      data-testid="install-prompt"
    >
      <span className="text-slate-200">将积木工具添加到主屏幕</span>
      <button
        type="button"
        className="btn-primary px-3 py-1 text-xs"
        onClick={async () => {
          await deferred.prompt();
          const { outcome } = await deferred.userChoice;
          if (outcome === "accepted") setInstalled(true);
          setDeferred(null);
        }}
      >
        安装
      </button>
      <button
        type="button"
        className="text-xs text-slate-400 hover:text-slate-200"
        onClick={() => setDismissed(true)}
      >
        稍后
      </button>
    </div>
  );
}
