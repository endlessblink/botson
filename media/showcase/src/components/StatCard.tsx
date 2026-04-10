import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";

export const StatCard: React.FC<{
  icon: string;
  label: string;
  value: string | number;
  sub?: string;
  delay: number;
  color?: string;
}> = ({ icon, label, value, sub, delay, color }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = spring({
    frame: frame - delay,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  const scale = interpolate(entrance, [0, 1], [0.8, 1]);
  const opacity = entrance;

  const isNumber = typeof value === "number";
  const displayValue = isNumber
    ? Math.round(
        interpolate(frame, [delay, delay + 20], [0, value as number], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      )
    : value;

  return (
    <div
      style={{
        backgroundColor: COLORS.card,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 8,
        padding: 16,
        transform: `scale(${scale})`,
        opacity,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color: COLORS.textSecondary,
        }}
      >
        <span style={{ fontSize: 14 }}>{icon}</span>
        <span>{label}</span>
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 700,
          color: color || COLORS.text,
          lineHeight: 1.2,
        }}
      >
        {displayValue}
      </div>
      {sub && (
        <div
          style={{
            fontSize: 11,
            color: COLORS.textMuted,
          }}
        >
          {sub}
        </div>
      )}
    </div>
  );
};
