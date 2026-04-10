import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";

export const FeatureToggle: React.FC<{
  name: string;
  active: boolean;
  delay: number;
}> = ({ name, active, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const rowEntrance = spring({
    frame: frame - delay,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  const toggleProgress = active
    ? spring({
        frame: frame - delay - 5,
        fps,
        config: { damping: 14, stiffness: 180 },
      })
    : 0;

  const knobX = interpolate(toggleProgress, [0, 1], [2, 18]);

  const trackR = Math.round(interpolate(toggleProgress, [0, 1], [63, 52]));
  const trackG = Math.round(interpolate(toggleProgress, [0, 1], [63, 211]));
  const trackB = Math.round(interpolate(toggleProgress, [0, 1], [70, 153]));
  const trackColor = `rgb(${trackR}, ${trackG}, ${trackB})`;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 0",
        opacity: rowEntrance,
        direction: "rtl",
      }}
    >
      <span style={{ color: COLORS.text, fontSize: 13 }}>{name}</span>

      {/* Toggle track */}
      <div
        style={{
          width: 36,
          height: 20,
          borderRadius: 10,
          backgroundColor: trackColor,
          position: "relative",
          flexShrink: 0,
        }}
      >
        {/* Knob */}
        <div
          style={{
            width: 16,
            height: 16,
            borderRadius: "50%",
            backgroundColor: "#fff",
            position: "absolute",
            top: 2,
            right: knobX,
            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
          }}
        />
      </div>
    </div>
  );
};
