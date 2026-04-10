import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";

export const MiniChart: React.FC<{
  data: number[];
  color: string;
  delay: number;
  width: number;
  height: number;
}> = ({ data, color, delay, width, height }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (data.length < 2) return null;

  const padding = 4;
  const chartW = width - padding * 2;
  const chartH = height - padding * 2;

  const maxVal = Math.max(...data);
  const minVal = Math.min(...data);
  const range = maxVal - minVal || 1;

  const points = data.map((v, i) => {
    const x = padding + (i / (data.length - 1)) * chartW;
    const y = padding + chartH - ((v - minVal) / range) * chartH;
    return { x, y };
  });

  // Build SVG path for the line
  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
    .join(" ");

  // Build fill path (line + close along bottom)
  const fillPath =
    linePath +
    ` L ${points[points.length - 1].x.toFixed(1)} ${height} L ${points[0].x.toFixed(1)} ${height} Z`;

  // Approximate path length for dash animation
  let pathLength = 0;
  for (let i = 1; i < points.length; i++) {
    const dx = points[i].x - points[i - 1].x;
    const dy = points[i].y - points[i - 1].y;
    pathLength += Math.sqrt(dx * dx + dy * dy);
  }

  const drawProgress = spring({
    frame: frame - delay,
    fps,
    config: { damping: 30, stiffness: 100 },
  });

  const dashOffset = interpolate(drawProgress, [0, 1], [pathLength, 0]);

  const fillOpacity = spring({
    frame: frame - delay - 10,
    fps,
    config: { damping: 20, stiffness: 150 },
  });

  const gradientId = `chart-grad-${delay}`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: "block" }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>

      {/* Fill area */}
      <path
        d={fillPath}
        fill={`url(#${gradientId})`}
        opacity={fillOpacity * 0.6}
      />

      {/* Line */}
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray={pathLength}
        strokeDashoffset={dashOffset}
      />
    </svg>
  );
};
