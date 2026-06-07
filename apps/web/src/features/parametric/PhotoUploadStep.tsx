/**
 * Step 1 of the parametric-block wizard.
 *
 * Photos are optional in the parametric path — the GLB comes entirely from
 * the 5 caliper measurements entered in step 3. Photos here serve only a
 * visual cross-check after generation (the user can compare the real part
 * with the rendered GLB in step 4). We follow the same UX as `CapturePage`
 * — multi-file input, 0-20 photos, drag-drop fallback — but skip the live
 * camera (caliper users are at a desk, not at a phone).
 */
import { useCallback, useRef } from "react";
import clsx from "clsx";
import { MAX_PHOTOS } from "./schema";

interface PhotoUploadStepProps {
  photos: File[];
  onChange: (next: File[]) => void;
  onNext: () => void;
}

export function PhotoUploadStep({ photos, onChange, onNext }: PhotoUploadStepProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const remaining = MAX_PHOTOS - photos.length;

  const addFiles = useCallback(
    (files: FileList | File[] | null) => {
      if (!files) return;
      const list = Array.from(files)
        .filter((f) => f.type.startsWith("image/"))
        .slice(0, remaining);
      if (list.length === 0) return;
      onChange([...photos, ...list]);
    },
    [photos, remaining, onChange],
  );

  const removePhoto = useCallback(
    (idx: number) => {
      onChange(photos.filter((_, i) => i !== idx));
    },
    [photos, onChange],
  );

  return (
    <div className="card" data-testid="step-photos">
      <h2 className="mb-2 text-sm font-medium text-txt-primary">步骤 1/4 — 上传对比照片 (可选)</h2>
      <p className="mb-3 text-xs text-txt-tertiary">
        照片不参与建模, 仅用于步骤 4 跟生成 GLB 并排对比 (实物校准)。 0 张也能直接进入下一步。
      </p>

      <label
        className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-card px-4 py-8 text-center text-sm text-txt-secondary transition hover:border-accent hover:text-txt-primary"
        data-testid="photo-dropzone"
      >
        <span aria-hidden className="text-2xl">📷</span>
        <span>点击或拖拽图片到此处</span>
        <span className="text-[11px] text-txt-tertiary">最多 {MAX_PHOTOS} 张 · 已选 {photos.length}</span>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          data-testid="photo-input"
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </label>

      {photos.length > 0 ? (
        <ul
          className="mt-3 grid grid-cols-4 gap-2 sm:grid-cols-6"
          data-testid="photo-grid"
          data-count={photos.length}
        >
          {photos.map((p, i) => {
            const url = URL.createObjectURL(p);
            return (
              <li
                key={`${p.name}-${i}`}
                className="relative aspect-square overflow-hidden rounded-md border border-border"
                data-testid={`photo-${i + 1}`}
              >
                {/* The url is fine for the session; revoking on unmount is the
                    parent component's job (parent owns the photos array). */}
                <img src={url} alt={p.name} className="h-full w-full object-cover" />
                <button
                  type="button"
                  onClick={() => removePhoto(i)}
                  className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-black/70 text-white ring-1 ring-white/30 hover:bg-err"
                  aria-label={`删除第 ${i + 1} 张`}
                  data-testid={`photo-remove-${i + 1}`}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden
                  >
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      <div className="mt-4 flex items-center justify-between">
        <p className={clsx("text-xs", photos.length === 0 ? "text-txt-tertiary" : "text-ok")}>
          {photos.length === 0 ? "跳过 → 直接进入测量" : `已选 ${photos.length} 张`}
        </p>
        <button
          type="button"
          className="btn-primary rounded-full text-sm"
          data-testid="step1-next"
          onClick={onNext}
        >
          下一步: 选择规格 →
        </button>
      </div>
    </div>
  );
}
