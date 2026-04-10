import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";

export const StatusDot: React.FC<{
  color: string;
  delay: number;
  pulse?: boolean;
}> = ({ color, delay, pulse = false }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const entrance = spring({
    frame: frame - delay,
    fps,
    config: { damping: 8, stiffness: 200 },
  });

  const baseScale = interpolate(entrance, [0, 1], [0, 1]);

  const pulseScale = pulse
    ? 1 + 0.15 * Math.sin(((frame - delay) / fps) * Math.PI * 2)
    : 1;

  const scale = baseScale * pulseScale;

  return (
    <div
      style={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        backgroundColor: color,
        transform: `scale(${scale})`,
        flexShrink: 0,
      }}
    />
  );
};
