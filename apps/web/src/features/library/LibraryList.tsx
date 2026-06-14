import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { listLibraryParts, type LibraryPart, type PartStatus } from "@lib/api";
import { formatTimeAgo } from "@lib/format";
import { StatusBadge as PartStatusBadge, SourceBadge } from "./PartBadge";

// Each tab filters by exactly one status (server-side). Default = pending, so
// rejected parts are hidden until the user opens the 已拒绝 tab.
const TABS: ReadonlyArray<{ id: PartStatus; label: string }> = [
  { id: "pending", label: "待核验" },
  { id: "verified", label: "已核验" },
  { id: "rejected", label: "已拒绝" },
];

export function LibraryList() {
  const [tab, setTab] = useState<PartStatus>("pending");
  const [parts, setParts] = useState<LibraryPart[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    listLibraryParts(tab, 50, controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setParts(rows);
      })
      .catch(() => {
        if (!controller.signal.aborted) setParts([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [tab]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-txt-primary">零件库</h1>
        <span className="text-xs text-txt-secondary">{parts.length} 个零件</span>
      </div>

      <div className="mb-4 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid={`library-tab-${t.id}`}
            aria-pressed={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-full px-3 py-1 text-xs ${
              tab === t.id ? "bg-accent text-white dark:text-page" : "bg-card text-txt-secondary hover:text-txt-primary"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="card text-sm text-txt-secondary">加载中...</div>
      ) : parts.length === 0 ? (
        <div className="card flex flex-col items-center gap-2 py-16 text-center">
          <h2 className="text-base font-medium text-txt-primary">这里还没有零件</h2>
          <p className="text-sm text-txt-secondary">完成一次采集或建模后，零件会自动出现在这里</p>
        </div>
      ) : (
        <ul className="space-y-2.5">
          {parts.map((p) => (
            <li key={p.part_id}>
              <Link to={`/library/${p.part_id}`} className="card group block no-underline">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 break-words text-sm font-medium text-txt-primary">{p.name}</div>
                  <span className="shrink-0">
                    <PartStatusBadge status={p.status} />
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-txt-secondary">
                  <SourceBadge source={p.source_mode} />
                  {p.system ? <span>{p.system}</span> : null}
                  {p.kind ? <span>· {p.kind}</span> : null}
                  {p.units_x != null && p.units_y != null ? (
                    <span>
                      · {p.units_x}×{p.units_y}
                    </span>
                  ) : null}
                  <span>· {formatTimeAgo(p.created_at)}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
