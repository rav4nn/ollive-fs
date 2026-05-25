"use client";

type Props = {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  /** Optional formatter for the most-recent value (top-right label). */
  formatLatest?: (v: number) => string;
  label?: string;
};

export default function Sparkline({
  values,
  width = 360,
  height = 72,
  color = "var(--accent)",
  formatLatest,
  label,
}: Props) {
  if (values.length === 0) {
    return (
      <div style={{ color: "var(--muted)", fontSize: 12 }}>{label} (no data yet)</div>
    );
  }

  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);

  const padX = 4;
  const padY = 6;
  const w = width - padX * 2;
  const h = height - padY * 2;
  const step = values.length > 1 ? w / (values.length - 1) : 0;

  const points = values
    .map((v, i) => {
      const x = padX + i * step;
      const y = padY + (1 - (v - min) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  // Area path for the fill underneath the line.
  const area =
    `M ${padX},${padY + h} L ` +
    points.replace(/ /g, " L ") +
    ` L ${padX + (values.length - 1) * step},${padY + h} Z`;

  const latest = values[values.length - 1];
  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: 4,
        }}
      >
        <span style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5 }}>
          {label}
        </span>
        <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
          {formatLatest ? formatLatest(latest) : latest.toLocaleString()}
        </span>
      </div>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
        <path d={area} fill={color} opacity={0.15} />
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}
