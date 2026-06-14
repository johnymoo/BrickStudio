import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getLibraryPart, updateLibraryPart, type LibraryPart, type PartStatus } from "@lib/api";
import { Viewer } from "@features/viewer/Viewer";
import { StatusBadge as PartStatusBadge, SourceBadge } from "./PartBadge";

type PatchBody = { name?: string; notes?: string | null; status?: PartStatus };

function formatSpec(spec: LibraryPart["derived_spec_mm"]) {
  return JSON.stringify(spec ?? {}, null, 2);
}

export function LibraryDetail() {
  const { id } = useParams();
  const currentIdRef = useRef<string | undefined>(id);
  const mutationSeqRef = useRef(0);
  const [part, setPart] = useState<LibraryPart | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (currentIdRef.current !== id) {
    currentIdRef.current = id;
    mutationSeqRef.current += 1;
  }

  useEffect(() => {
    setSaving(false);

    if (!id) {
      setPart(null);
      setLoading(false);
      setError("缺少零件 ID");
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getLibraryPart(id, controller.signal)
      .then((row) => {
        if (controller.signal.aborted) return;
        setPart(row);
        setName(row.name);
        setNotes(row.notes ?? "");
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setPart(null);
        setError(err.message || "加载零件失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [id]);

  async function patch(patchBody: PatchBody) {
    if (!id) return;
    const patchId = id;
    const mutationSeq = ++mutationSeqRef.current;
    const savesEditableFields = "name" in patchBody || "notes" in patchBody;
    const isCurrentMutation = () => currentIdRef.current === patchId && mutationSeqRef.current === mutationSeq;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateLibraryPart(patchId, patchBody);
      if (!isCurrentMutation()) return;
      setPart(updated);
      if (savesEditableFields) {
        setName(updated.name);
        setNotes(updated.notes ?? "");
      }
    } catch (err) {
      if (!isCurrentMutation()) return;
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      if (isCurrentMutation()) setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <div className="card text-sm text-txt-secondary">加载中...</div>
      </div>
    );
  }

  if (!part) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <div className="card space-y-3">
          <p className="text-sm text-err">{error ?? "没有找到这个零件"}</p>
          <Link to="/library" className="text-sm">
            返回零件库
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Link to="/library" className="text-xs text-txt-secondary">
            返回零件库
          </Link>
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
            <h1 className="min-w-0 break-words text-xl font-semibold text-txt-primary">{part.name}</h1>
            <PartStatusBadge status={part.status} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-txt-secondary">
            <SourceBadge source={part.source_mode} />
            {part.system ? <span>{part.system}</span> : null}
            {part.kind ? <span>· {part.kind}</span> : null}
            {part.units_x != null && part.units_y != null ? (
              <span>
                · {part.units_x}×{part.units_y}
              </span>
            ) : null}
          </div>
        </div>
        <Link to={`/captures/${part.capture_id}`} className="shrink-0 text-sm">
          查看来源采集
        </Link>
      </div>

      {error ? <div className="mb-4 rounded-lg border border-err bg-err-bg p-3 text-sm text-err">{error}</div> : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <section className="min-h-[360px] overflow-hidden rounded-lg border border-border bg-card">
          {part.asset_id ? (
            <Viewer assetId={part.asset_id} className="h-[360px] sm:h-[520px]" />
          ) : (
            <div className="flex h-[360px] items-center justify-center p-6 text-center text-sm text-txt-secondary sm:h-[520px]">
              暂无可预览的 3D 模型
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <section className="card space-y-3">
            <div>
              <label htmlFor="part-name" className="text-xs font-medium text-txt-secondary">
                名称
              </label>
              <input
                id="part-name"
                data-testid="part-name-input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-1 w-full rounded-lg border border-border bg-page px-3 py-2 text-sm text-txt-primary outline-none focus:border-accent"
              />
            </div>
            <div>
              <label htmlFor="part-notes" className="text-xs font-medium text-txt-secondary">
                备注
              </label>
              <textarea
                id="part-notes"
                data-testid="part-notes-input"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={4}
                className="mt-1 w-full resize-y rounded-lg border border-border bg-page px-3 py-2 text-sm text-txt-primary outline-none focus:border-accent"
              />
            </div>
            <button
              type="button"
              data-testid="part-save-btn"
              disabled={saving}
              onClick={() => patch({ name, notes })}
              className="btn-primary w-full text-sm"
            >
              保存
            </button>
          </section>

          <section className="card space-y-3">
            <h2 className="text-sm font-medium text-txt-primary">核验状态</h2>
            <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-1">
              <button
                type="button"
                data-testid="part-verify-btn"
                disabled={saving}
                onClick={() => patch({ status: "verified" })}
                className="btn-outline text-sm"
              >
                标记已核验
              </button>
              <button
                type="button"
                data-testid="part-reject-btn"
                disabled={saving}
                onClick={() => patch({ status: "rejected" })}
                className="btn-outline text-sm"
              >
                标记已拒绝
              </button>
              <button
                type="button"
                data-testid="part-reset-btn"
                disabled={saving}
                onClick={() => patch({ status: "pending" })}
                className="btn-ghost text-sm"
              >
                重置为待核验
              </button>
            </div>
          </section>

          <section className="card space-y-3">
            <h2 className="text-sm font-medium text-txt-primary">规格</h2>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
              <dt className="text-txt-secondary">零件 ID</dt>
              <dd className="min-w-0 break-words text-txt-primary">{part.part_id}</dd>
              <dt className="text-txt-secondary">采集 ID</dt>
              <dd className="min-w-0 break-words text-txt-primary">{part.capture_id}</dd>
              <dt className="text-txt-secondary">资产 ID</dt>
              <dd className="min-w-0 break-words text-txt-primary">{part.asset_id ?? "无"}</dd>
              <dt className="text-txt-secondary">颜色</dt>
              <dd className="min-w-0 break-words text-txt-primary">{part.color ?? "未设置"}</dd>
            </dl>
            <pre className="max-h-64 overflow-auto rounded-lg border border-border bg-page p-3 text-xs text-txt-secondary">
              {formatSpec(part.derived_spec_mm)}
            </pre>
          </section>
        </aside>
      </div>
    </div>
  );
}
