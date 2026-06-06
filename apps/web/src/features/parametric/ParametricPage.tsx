/**
 * 建模板块 — 4-step wizard.
 *
 *   1. PhotoUploadStep    (optional photo cross-check)
 *   2. KindSelectStep     (system, kind, units_x × units_y)
 *   3. MeasurementsStep   (5 caliper numbers + embedded SVG)
 *   4. PreviewStep        (SSE-driven GLB viewer)
 *
 * The whole form lives in local state (a `ParametricFormState` blob). We
 * keep it that way — adding zustand would force us to also reason about
 * rehydration, partial submits, and stale jobs. A wizard is a short-lived
 * throwaway flow; if the user navigates away we re-create from scratch.
 *
 * Submit is a single call to `createParametricBlock`. On success the
 * resulting `capture_id` + `job_id` thread into `PreviewStep` which owns
 * the SSE subscription.
 */
import { useCallback, useState } from "react";
import clsx from "clsx";
import { createParametricBlock, ApiClientError, type ParametricBlockResponse } from "./api";
import {
  DEFAULT_FORM_STATE,
  isMeasurementsComplete,
  MEASUREMENT_FIELDS,
  type ParametricFormState,
  type WizardStep,
} from "./schema";
import { PhotoUploadStep } from "./PhotoUploadStep";
import { KindSelectStep } from "./KindSelectStep";
import { MeasurementsStep } from "./MeasurementsStep";
import { PreviewStep } from "./PreviewStep";
import { newPartId } from "@lib/api";

const STEP_LABELS: Record<WizardStep, string> = {
  1: "照片",
  2: "规格",
  3: "测量",
  4: "预览",
};

export function ParametricPage() {
  const [form, setForm] = useState<ParametricFormState>(() => ({
    ...DEFAULT_FORM_STATE,
    partId: newPartId(), // keep a stable per-session id
  }));
  const [step, setStep] = useState<WizardStep>(1);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [block, setBlock] = useState<ParametricBlockResponse | null>(null);

  // ---- step navigation ----------------------------------------------------
  const goTo = useCallback((s: WizardStep) => {
    setStep(s);
    // Clear stale server state when stepping back before submitting.
    if (s < 4) {
      setBlock(null);
      setSubmitError(null);
    }
  }, []);

  // ---- step 1: photos -----------------------------------------------------
  const setPhotos = useCallback((next: File[]) => {
    setForm((f) => ({ ...f, photos: next }));
  }, []);

  // ---- step 2: kind -------------------------------------------------------
  const setSpec = useCallback(
    (patch: Partial<Pick<ParametricFormState, "system" | "kind" | "unitsX" | "unitsY">>) => {
      setForm((f) => ({ ...f, ...patch }));
    },
    [],
  );

  // ---- step 3: measurements -----------------------------------------------
  const setMeasurements = useCallback((next: ParametricFormState["measurements"]) => {
    setForm((f) => ({ ...f, measurements: next }));
  }, []);

  // ---- step 4: submit -----------------------------------------------------
  const submit = useCallback(async () => {
    if (!isMeasurementsComplete(form.measurements)) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await createParametricBlock({
        part_id: form.partId,
        system: form.system,
        kind: form.kind,
        units_x: form.unitsX,
        units_y: form.unitsY,
        raw_measurements_mm: form.measurements,
        photos: form.photos.length > 0 ? form.photos : undefined,
      });
      setBlock(res);
      setForm((f) => ({ ...f, jobId: res.job_id, captureId: res.capture_id }));
      setStep(4);
    } catch (err: unknown) {
      // The backend follows the design.md §9 error envelope
      // ``{ error: { code, message, details? } }``. Surface the inner
      // message when present; otherwise fall back to the throw message.
      let detail: string | null = null;
      if (err instanceof ApiClientError) {
        const body = err.body as { error?: { message?: string } } | undefined;
        detail = body?.error?.message ?? null;
      } else if (err instanceof Error) {
        detail = err.message;
      }
      setSubmitError(detail ?? "提交失败");
    } finally {
      setSubmitting(false);
    }
  }, [form]);

  const reset = useCallback(() => {
    setForm({ ...DEFAULT_FORM_STATE, partId: newPartId() });
    setBlock(null);
    setSubmitError(null);
    setStep(1);
  }, []);

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">建模板块</h1>
        <span className="text-xs text-slate-500">拍照 / 测量 / 参数化生成</span>
      </div>

      <Stepper step={step} onSelect={(s) => s < step && goTo(s)} />

      {submitError ? (
        <div
          className="mt-4 rounded-md border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300"
          data-testid="submit-error-banner"
          role="alert"
        >
          <p className="font-medium">提交失败</p>
          <p className="mt-0.5 text-xs text-rose-400">{submitError}</p>
        </div>
      ) : null}

      <div className="mt-4 space-y-4">
        {step === 1 ? (
          <PhotoUploadStep
            photos={form.photos}
            onChange={setPhotos}
            onNext={() => goTo(2)}
          />
        ) : null}
        {step === 2 ? (
          <KindSelectStep
            system={form.system}
            kind={form.kind}
            unitsX={form.unitsX}
            unitsY={form.unitsY}
            onChange={setSpec}
            onBack={() => goTo(1)}
            onNext={() => goTo(3)}
          />
        ) : null}
        {step === 3 ? (
          <MeasurementsStep
            system={form.system}
            kind={form.kind}
            values={form.measurements}
            onChange={setMeasurements}
            onBack={() => goTo(2)}
            onNext={submit}
          />
        ) : null}
        {step === 4 ? (
          <PreviewStep
            system={form.system}
            kind={form.kind}
            unitsX={form.unitsX}
            unitsY={form.unitsY}
            partId={form.partId}
            block={block}
            submitError={submitError}
            submitting={submitting}
            onBack={() => goTo(3)}
            onRetry={submit}
            onReset={reset}
          />
        ) : null}
      </div>

      <DebugFooter form={form} />
    </div>
  );
}

interface StepperProps {
  step: WizardStep;
  onSelect: (s: WizardStep) => void;
}

function Stepper({ step, onSelect }: StepperProps) {
  return (
    <ol className="flex items-center gap-2" data-testid="wizard-stepper" data-active-step={step}>
      {([1, 2, 3, 4] as WizardStep[]).map((s, i) => {
        const reached = s <= step;
        const active = s === step;
        return (
          <li key={s} className="flex flex-1 items-center gap-2" data-testid={`stepper-${s}`}>
            <button
              type="button"
              onClick={() => onSelect(s)}
              disabled={s > step}
              data-active={active ? "true" : "false"}
              data-reached={reached ? "true" : "false"}
              className={clsx(
                "grid h-8 w-8 shrink-0 place-items-center rounded-full text-xs font-semibold transition",
                active
                  ? "bg-primary-500 text-white ring-2 ring-primary-400/40"
                  : reached
                    ? "bg-emerald-500/20 text-emerald-300"
                    : "bg-slate-800 text-slate-500",
                s > step ? "cursor-not-allowed" : "cursor-pointer hover:ring-1 hover:ring-slate-500",
              )}
              aria-current={active ? "step" : undefined}
            >
              {reached && !active ? "✓" : s}
            </button>
            <span className={clsx("text-xs", active ? "text-slate-100" : "text-slate-500")}>
              {STEP_LABELS[s]}
            </span>
            {i < 3 ? <span className="ml-1 h-px flex-1 bg-slate-800" aria-hidden /> : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Exposes a non-visible summary of the current form state for tests. Without
 * this the unit tests would have to traverse many sub-components to assert
 * that state updates worked.
 */
function DebugFooter({ form }: { form: ParametricFormState }) {
  return (
    <div className="mt-6 hidden" data-testid="form-state">
      <p data-testid="form-system">{form.system}</p>
      <p data-testid="form-kind">{form.kind}</p>
      <p data-testid="form-units-x">{form.unitsX}</p>
      <p data-testid="form-units-y">{form.unitsY}</p>
      <p data-testid="form-photo-count">{form.photos.length}</p>
      {MEASUREMENT_FIELDS.map((f) => {
        const v = form.measurements[f.key];
        return (
          <p key={f.key} data-testid={`form-${f.key}`}>
            {Number.isFinite(v) ? v : "NaN"}
          </p>
        );
      })}
    </div>
  );
}
