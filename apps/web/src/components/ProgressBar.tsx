export function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className="relative h-2 w-full overflow-hidden rounded-full bg-slate-800"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div
        className="h-full rounded-full bg-primary-500 transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
