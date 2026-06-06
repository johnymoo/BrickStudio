/**
 * Step 3 of the parametric-block wizard.
 *
 * Renders 5 number inputs (1A / 1B / 2 / 3 / 4) alongside an embedded SVG
 * that shows the caliper placement. The SVG is bundled as a static asset
 * in the page (cheap, no extra HTTP request) — it mirrors
 * `docs/measure-block-annotated.svg` so the user sees the exact same
 * reference diagram the docs ship.
 *
 * The 5 fields are required regardless of system/kind — the caliper is
 * friendly to all five measurements (`docs/measure-block-annotated.svg`).
 * Sub-step layout:
 *
 *   | left col  | right col |
 *   | 5 inputs  |  svg pic  |
 *
 * On small screens the columns stack. We also surface a real-time
 * cross-check (warns if stud_diameter disagrees with the algebraic
 * `(outer_pitch - inner_pitch) / 2` by more than 0.5 mm).
 */
import { useCallback, useMemo } from "react";
import clsx from "clsx";
import { MEASUREMENT_FIELDS, SYSTEM_UNIT_MM, crossCheckMeasurements } from "./schema";
import type { BrickSystem, BrickKind } from "./api";
import type { Measurements } from "./schema";

interface MeasurementsStepProps {
  system: BrickSystem;
  kind: BrickKind;
  values: Measurements;
  onChange: (next: Measurements) => void;
  onBack: () => void;
  onNext: () => void;
}

export function MeasurementsStep({ system, kind, values, onChange, onBack, onNext }: MeasurementsStepProps) {
  const allValid = useMemo(() => {
    return MEASUREMENT_FIELDS.every((f) => Number.isFinite(values[f.key]) && values[f.key] > 0);
  }, [values]);

  const crossCheckWarning = useMemo(() => crossCheckMeasurements(values), [values]);

  const setField = useCallback(
    (key: keyof Measurements, raw: string) => {
      // Allow empty input — we keep NaN to mark "not yet filled", and the
      // submit button stays disabled until all 5 are finite and > 0.
      const trimmed = raw.trim();
      const v = trimmed === "" ? NaN : Number(trimmed);
      onChange({ ...values, [key]: v });
    },
    [values, onChange],
  );

  return (
    <div className="card" data-testid="step-measurements">
      <h2 className="mb-2 text-sm font-medium text-slate-300">步骤 3/4 — 输入 5 个 caliper 测量</h2>
      <p className="mb-3 text-xs text-slate-500">
        5 个数字都是 mm, 卡尺直接卡的位置 (见右图)。所有体系 ({system} · {kind}) 都用这 5 个, 推导在服务端做。
      </p>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        {/* 5 input fields */}
        <ol className="space-y-2.5" data-testid="measurement-list">
          {MEASUREMENT_FIELDS.map((f) => {
            const v = values[f.key];
            const valid = Number.isFinite(v) && v > 0;
            return (
              <li
                key={f.key}
                className="flex items-start gap-3 rounded-md border border-slate-700 bg-slate-900/50 p-2.5"
                data-testid={`measurement-${f.key}`}
                data-valid={valid ? "true" : "false"}
              >
                <span
                  aria-hidden
                  className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary-500/20 text-[11px] font-bold text-primary-100 ring-1 ring-primary-500/40"
                >
                  {f.code}
                </span>
                <div className="flex-1">
                  <label htmlFor={`m-${f.key}`} className="block text-xs font-medium text-slate-200">
                    {f.label}
                  </label>
                  <p className="mt-0.5 text-[10px] leading-snug text-slate-500">{f.hint}</p>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <input
                      id={`m-${f.key}`}
                      type="number"
                      inputMode="decimal"
                      min={0}
                      step={0.05}
                      value={Number.isFinite(v) ? v : ""}
                      onChange={(e) => setField(f.key, e.target.value)}
                      placeholder="mm"
                      className={clsx(
                        "h-8 w-28 rounded border bg-slate-950 px-2 text-sm text-slate-100 focus:outline-none",
                        valid
                          ? "border-slate-700 focus:border-primary-500"
                          : "border-rose-700/60 focus:border-rose-500",
                      )}
                      data-testid={`measurement-input-${f.key}`}
                    />
                    <span className="text-xs text-slate-500">{f.unit}</span>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>

        {/* SVG diagram */}
        <div className="rounded-md border border-slate-700 bg-white p-2" data-testid="measurement-svg-wrap">
          <MeasurementDiagram />
        </div>
      </div>

      {crossCheckWarning ? (
        <p
          className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300"
          data-testid="cross-check-warning"
        >
          ⚠ {crossCheckWarning}
        </p>
      ) : null}

      <div className="mt-4 flex items-center justify-between">
        <p className={clsx("text-xs", allValid ? "text-emerald-400" : "text-slate-500")} data-testid="measurements-validity">
          {allValid ? "✓ 5 个数字均已填, 可生成" : "请填满 5 个正数 (mm)"}
        </p>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-ghost text-sm" onClick={onBack} data-testid="step3-back">
            ← 上一步
          </button>
          <button
            type="button"
            className="btn-primary text-sm"
            onClick={onNext}
            disabled={!allValid}
            data-testid="step3-next"
          >
            下一步: 提交 + 预览 →
          </button>
        </div>
      </div>

      {/* Public-system cheat sheet (purely a UX nicety; the real derivation
          is server-side). Hidden on small screens to keep the page short. */}
      <p className="mt-3 hidden text-[10px] text-slate-500 lg:block" data-testid="public-spec-hint">
        公开规格参考 ({system}) · unit = {SYSTEM_UNIT_MM[system]} mm ·{" "}
        {system === "duplo"
          ? "brick 17.0 mm, knob Ø16.0 × 7.0"
          : system === "lego"
            ? "brick 9.6 mm, knob Ø4.8 × 1.7"
            : system === "feile"
              ? "brick 19.2 mm, knob Ø9.4 × 5.4"
              : "未知 — 比例由测量反算"}
      </p>
    </div>
  );
}

/**
 * Inline SVG showing where the caliper goes for each of the 5 measurements.
 * Sourced from `docs/measure-block-annotated.svg` (the top-view + front-view
 * pair is enough — the iso view is redundant for a 2D form).
 */
function MeasurementDiagram() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 600 460"
      className="h-auto w-full"
      role="img"
      aria-label="卡尺测量示意"
      data-testid="measurement-diagram"
    >
      <text x="300" y="22" textAnchor="middle" fontSize="14" fontWeight="700" fill="#0f172a">
        卡尺 5 个测量点 (mm)
      </text>

      {/* Top view: brick (190..390 x 180..360) with 4 studs */}
      <rect x="20" y="50" width="560" height="190" rx="6" fill="#fafafa" stroke="#cbd5e1" />
      <text x="300" y="70" textAnchor="middle" fontSize="11" fontWeight="700" fill="#0f172a">
        顶视
      </text>
      <rect x="200" y="100" width="200" height="120" fill="#dbeafe" stroke="#1e3a8a" strokeWidth="1.5" />
      <circle cx="240" cy="140" r="24" fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />
      <circle cx="360" cy="140" r="24" fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />
      <circle cx="240" cy="200" r="24" fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />
      <circle cx="360" cy="200" r="24" fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />

      {/* 1A arrow */}
      <line x1="208" y1="138" x2="392" y2="138" stroke="#0f172a" strokeWidth="1.2" />
      <text x="300" y="132" textAnchor="middle" fontSize="10" fill="#0f172a">1A outer_pitch</text>
      <circle cx="50" cy="138" r="11" fill="#3b82f6" stroke="#1e3a8a" strokeWidth="1.5" />
      <text x="50" y="142" textAnchor="middle" fontSize="10" fontWeight="700" fill="#fff">1A</text>

      {/* 1B arrow */}
      <line x1="265" y1="160" x2="335" y2="160" stroke="#0f172a" strokeWidth="1.2" />
      <text x="300" y="178" textAnchor="middle" fontSize="10" fill="#0f172a">1B inner_pitch</text>
      <circle cx="50" cy="195" r="11" fill="#3b82f6" stroke="#1e3a8a" strokeWidth="1.5" />
      <text x="50" y="199" textAnchor="middle" fontSize="10" fontWeight="700" fill="#fff">1B</text>

      {/* 3 stud diameter (across stud 4) */}
      <line x1="335" y1="200" x2="385" y2="200" stroke="#0f172a" strokeWidth="1.2" />
      <text x="395" y="195" textAnchor="start" fontSize="10" fill="#0f172a">3 stud_diameter</text>
      <circle cx="560" cy="200" r="11" fill="#3b82f6" stroke="#1e3a8a" strokeWidth="1.5" />
      <text x="560" y="204" textAnchor="middle" fontSize="10" fontWeight="700" fill="#fff">3</text>

      {/* Front view: 2×2 brick with two studs on top */}
      <rect x="20" y="250" width="560" height="190" rx="6" fill="#fafafa" stroke="#cbd5e1" />
      <text x="300" y="270" textAnchor="middle" fontSize="11" fontWeight="700" fill="#0f172a">
        前视 / 侧视
      </text>
      <rect x="200" y="350" width="200" height="60" fill="#dbeafe" stroke="#1e3a8a" strokeWidth="1.5" />
      <path d="M 250 280 A 30 30 0 0 1 310 280 L 220 280 A 30 30 0 0 1 250 280 Z"
            fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />
      <path d="M 350 280 A 30 30 0 0 1 410 280 L 320 280 A 30 30 0 0 1 350 280 Z"
            fill="#fef3c7" stroke="#92400e" strokeWidth="1.2" />

      {/* 2 brick_height_net (底 → 砖顶) */}
      <line x1="170" y1="350" x2="170" y2="410" stroke="#0f172a" strokeWidth="1.2" />
      <text x="120" y="385" textAnchor="middle" fontSize="10" fill="#0f172a">2 brick_height_net</text>
      <circle cx="50" cy="385" r="11" fill="#3b82f6" stroke="#1e3a8a" strokeWidth="1.5" />
      <text x="50" y="389" textAnchor="middle" fontSize="10" fontWeight="700" fill="#fff">2</text>

      {/* 4 brick_height_total (底 → 凸点顶) */}
      <line x1="430" y1="350" x2="430" y2="280" stroke="#0f172a" strokeWidth="1.2" />
      <text x="500" y="320" textAnchor="middle" fontSize="10" fill="#0f172a">4 brick_height_total</text>
      <circle cx="560" cy="320" r="11" fill="#3b82f6" stroke="#1e3a8a" strokeWidth="1.5" />
      <text x="560" y="324" textAnchor="middle" fontSize="10" fontWeight="700" fill="#fff">4</text>
    </svg>
  );
}
