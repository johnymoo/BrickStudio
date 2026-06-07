export function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className="relative h-1 w-full overflow-hidden rounded-full"
      style={{ backgroundColor: "var(--progress-track)" }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div
        className="h-full rounded-full transition-all duration-300"
        style={{
          width: `${pct}%`,
          background: `linear-gradient(90deg, var(--accent), var(--accent-light))`,
        }}
      />
    </div>
  );
}
