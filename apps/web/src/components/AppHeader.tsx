import { Link, useLocation } from "react-router-dom";

const NAV_ITEMS: ReadonlyArray<{ path: string; label: string; testId?: string }> = [
  { path: "/", label: "我的积木" },
  { path: "/parametric", label: "建模", testId: "header-parametric-link" },
];

export function AppHeader() {
  const { pathname } = useLocation();

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-card/85 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <Link to="/" className="flex items-center gap-2 no-underline">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-accent to-accent-light text-white dark:text-page"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="4" y="4" width="6" height="6" rx="1" />
              <rect x="14" y="4" width="6" height="6" rx="1" />
              <rect x="4" y="14" width="6" height="6" rx="1" />
              <rect x="14" y="14" width="6" height="6" rx="1" />
            </svg>
          </span>
          <span className="text-base font-semibold text-txt-primary">BrickStudio</span>
        </Link>
        <nav className="flex items-center gap-4">
          {NAV_ITEMS.map((item) => {
            const active =
              item.path === "/"
                ? pathname === "/" || pathname.startsWith("/jobs")
                : pathname.startsWith(item.path);
            return (
              <Link
                key={item.path}
                to={item.path}
                aria-current={active ? "page" : undefined}
                data-testid={item.testId}
                className={`text-sm no-underline pb-0.5 ${
                  active
                    ? "font-semibold text-accent border-b-2 border-accent"
                    : "text-txt-secondary hover:text-txt-primary"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
