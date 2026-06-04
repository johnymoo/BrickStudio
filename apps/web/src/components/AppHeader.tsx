import { Link, useLocation } from "react-router-dom";

export function AppHeader() {
  const { pathname } = useLocation();
  const isCapture = pathname.startsWith("/capture");
  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/85 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <Link to="/" className="flex items-center gap-2 text-slate-100 no-underline">
          <span aria-hidden className="grid h-7 w-7 place-items-center rounded-md bg-primary-500 text-white">
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="4" y="4" width="6" height="6" rx="1" />
              <rect x="14" y="4" width="6" height="6" rx="1" />
              <rect x="4" y="14" width="6" height="6" rx="1" />
              <rect x="14" y="14" width="6" height="6" rx="1" />
            </svg>
          </span>
          <span className="text-base font-semibold">积木工具</span>
        </Link>
        {!isCapture && (
          <Link to="/capture" className="btn-primary text-sm">
            开始新的采集
          </Link>
        )}
      </div>
    </header>
  );
}
