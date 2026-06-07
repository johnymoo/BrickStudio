/**
 * Step 2 of the parametric-block wizard.
 *
 * Lets the user pick the brick system (DUPLO / LEGO / FEILE / generic),
 * the part kind (brick / plate / tile / slope), and the stud-grid size
 * (units_x × units_y). The kind options follow the same set across
 * systems (per `block_generator.py` `_KIND_HAS_KNOBS`); for the moment
 * all systems support all 4 kinds. Size is an integer 1-16 per axis.
 */
import { useCallback, useMemo } from "react";
import clsx from "clsx";
import { BRICK_KINDS, BRICK_SYSTEMS, MAX_UNITS, MIN_UNITS, SYSTEM_KIND_MATRIX, SYSTEM_UNIT_MM } from "./schema";
import type { BrickSystem, BrickKind } from "./api";

interface KindSelectStepProps {
  system: BrickSystem;
  kind: BrickKind;
  unitsX: number;
  unitsY: number;
  onChange: (next: { system?: BrickSystem; kind?: BrickKind; unitsX?: number; unitsY?: number }) => void;
  onBack: () => void;
  onNext: () => void;
}

export function KindSelectStep({
  system,
  kind,
  unitsX,
  unitsY,
  onChange,
  onBack,
  onNext,
}: KindSelectStepProps) {
  const kindOptions = useMemo(() => {
    const allowed = new Set<BrickKind>(SYSTEM_KIND_MATRIX[system] ?? []);
    return BRICK_KINDS.filter((k) => allowed.has(k.value));
  }, [system]);

  // If the user switched systems and the previously-selected kind is no
  // longer available, fall back to the first option. Effects are awkward
  // for this; we just trust the caller to keep them in sync, and the UI
  // will visually highlight the active kind either way.
  const effectiveKind = useMemo(() => {
    return kindOptions.find((k) => k.value === kind) ? kind : (kindOptions[0]?.value ?? "brick");
  }, [kindOptions, kind]);

  const setSystem = useCallback((next: BrickSystem) => {
    const allowed = SYSTEM_KIND_MATRIX[next] ?? [];
    // If the current kind isn't allowed in the new system, also clear it.
    const patch: { system: BrickSystem; kind?: BrickKind } = { system: next };
    if (!allowed.includes(kind)) patch.kind = allowed[0] ?? "brick";
    onChange(patch);
  }, [kind, onChange]);

  const adjust = useCallback(
    (axis: "x" | "y", delta: number) => {
      const current = axis === "x" ? unitsX : unitsY;
      const next = Math.max(MIN_UNITS, Math.min(MAX_UNITS, current + delta));
      onChange(axis === "x" ? { unitsX: next } : { unitsY: next });
    },
    [unitsX, unitsY, onChange],
  );

  return (
    <div className="card" data-testid="step-kind">
      <h2 className="mb-2 text-sm font-medium text-txt-primary">步骤 2/4 — 选择规格</h2>
      <p className="mb-3 text-xs text-txt-tertiary">
        选体系 (公制) → 选类型 → 选尺寸。最终尺寸由步骤 3 的 5 个 caliper 数字反算, 这里只标 stud 排数。
      </p>

      {/* system picker */}
      <fieldset className="mb-4" data-testid="system-fieldset">
        <legend className="mb-1.5 text-xs text-txt-secondary">体系 (system)</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label="体系">
          {BRICK_SYSTEMS.map((s) => {
            const active = s.value === system;
            return (
              <button
                key={s.value}
                type="button"
                role="radio"
                aria-checked={active}
                data-testid={`system-${s.value}`}
                data-active={active ? "true" : "false"}
                onClick={() => setSystem(s.value)}
                className={clsx(
                  "rounded-md border px-2 py-2 text-left text-xs transition",
                  active
                    ? "border-accent bg-accent-bg text-accent font-medium"
                    : "border-border bg-card text-txt-secondary hover:border-accent",
                )}
              >
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="mt-0.5 block text-[10px] text-txt-tertiary">{s.hint}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      {/* kind picker */}
      <fieldset className="mb-4">
        <legend className="mb-1.5 text-xs text-txt-secondary">类型 (kind)</legend>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label="类型">
          {kindOptions.map((k) => {
            const active = k.value === effectiveKind;
            return (
              <button
                key={k.value}
                type="button"
                role="radio"
                aria-checked={active}
                data-testid={`kind-${k.value}`}
                data-active={active ? "true" : "false"}
                onClick={() => onChange({ kind: k.value })}
                className={clsx(
                  "rounded-md border px-2 py-2 text-center text-xs transition",
                  active
                    ? "border-accent bg-accent-bg text-accent font-medium"
                    : "border-border bg-card text-txt-secondary hover:border-accent",
                )}
              >
                <span aria-hidden className="block text-xl">
                  {k.icon}
                </span>
                <span className="mt-1 block text-xs font-medium">{k.label}</span>
                <span className="mt-0.5 block text-[10px] text-txt-tertiary">{k.hint}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      {/* size (units_x × units_y) */}
      <fieldset className="mb-4">
        <legend className="mb-1.5 text-xs text-txt-secondary">
          尺寸 (units) · 1 unit ≈ {SYSTEM_UNIT_MM[system]} mm
        </legend>
        <div className="grid grid-cols-2 gap-3">
          <NumberStepper
            label="长 X (units_x)"
            value={unitsX}
            min={MIN_UNITS}
            max={MAX_UNITS}
            onChange={(v) => onChange({ unitsX: v })}
            onAdjust={(d) => adjust("x", d)}
            testid="units-x"
          />
          <NumberStepper
            label="宽 Y (units_y)"
            value={unitsY}
            min={MIN_UNITS}
            max={MAX_UNITS}
            onChange={(v) => onChange({ unitsY: v })}
            onAdjust={(d) => adjust("y", d)}
            testid="units-y"
          />
        </div>
        <p className="mt-2 text-[11px] text-txt-tertiary">
          提示: 公制 X × Y = 总长 × 总宽 (mm)。2×2 = {(2 * SYSTEM_UNIT_MM[system]).toFixed(1)} mm 方块, 4 个 stud 在顶面。
        </p>
      </fieldset>

      <div className="mt-2 flex items-center justify-between">
        <button type="button" className="btn-outline text-sm" onClick={onBack} data-testid="step2-back">
          ← 上一步
        </button>
        <button type="button" className="btn-primary rounded-full text-sm" onClick={onNext} data-testid="step2-next">
          下一步: 输入 5 个测量 →
        </button>
      </div>
    </div>
  );
}

interface NumberStepperProps {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
  onAdjust: (delta: number) => void;
  testid: string;
}

function NumberStepper({ label, value, min, max, onChange, onAdjust, testid }: NumberStepperProps) {
  return (
    <div className="rounded-md border border-border bg-card p-3" data-testid={testid}>
      <label className="mb-1.5 block text-xs text-txt-secondary">{label}</label>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => onAdjust(-1)}
          disabled={value <= min}
          className="grid h-8 w-8 place-items-center rounded border border-border text-txt-secondary hover:border-accent disabled:opacity-40"
          aria-label="减小"
          data-testid={`${testid}-dec`}
        >
          −
        </button>
        <input
          type="number"
          min={min}
          max={max}
          step={1}
          value={value}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) onChange(Math.max(min, Math.min(max, Math.round(v))));
          }}
          className="h-8 w-16 rounded border border-border bg-page text-center text-sm text-txt-primary focus:border-accent focus:outline-none"
          data-testid={`${testid}-input`}
          aria-label={label}
        />
        <button
          type="button"
          onClick={() => onAdjust(1)}
          disabled={value >= max}
          className="grid h-8 w-8 place-items-center rounded border border-border text-txt-secondary hover:border-accent disabled:opacity-40"
          aria-label="增大"
          data-testid={`${testid}-inc`}
        >
          +
        </button>
      </div>
    </div>
  );
}
