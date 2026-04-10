import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";

export const TableRow: React.FC<{
  rank: number;
  name: string;
  points: number;
  emoji: string;
  delay: number;
}> = ({ rank, name, points, emoji, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = spring({
    frame: frame - delay,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  const translateX = interpolate(entrance, [0, 1], [40, 0]);
  const opacity = entrance;

  const isOdd = rank % 2 === 1;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 14px",
        borderBottom: `1px solid ${COLORS.border}`,
        backgroundColor: isOdd ? COLORS.hover : "transparent",
        transform: `translateX(${translateX}px)`,
        opacity,
        direction: "rtl",
      }}
    >
      {/* Right side (RTL): rank + name */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span
          style={{
            color: COLORS.textMuted,
            fontSize: 13,
            fontWeight: 600,
            minWidth: 18,
            textAlign: "center",
          }}
        >
          {rank}
        </span>
        <span style={{ color: COLORS.text, fontSize: 14, fontWeight: 500 }}>
          {name}
        </span>
      </div>

      {/* Left side: points + emoji */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: COLORS.textSecondary, fontSize: 13 }}>
          {points} נק׳
        </span>
        <span style={{ fontSize: 16 }}>{emoji}</span>
      </div>
    </div>
  );
};
