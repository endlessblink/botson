import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { TG } from "../../telegram-theme";

interface AnnotationOverlayProps {
  text: string;
  fadeInFrame: number;
}

export const AnnotationOverlay: React.FC<AnnotationOverlayProps> = ({
  text,
  fadeInFrame,
}) => {
  const frame = useCurrentFrame();

  const opacity = interpolate(frame, [fadeInFrame, fadeInFrame + 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div
      style={{
        position: "absolute",
        bottom: 16,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        opacity,
        direction: "rtl",
      }}
    >
      <div
        style={{
          fontSize: 15,
          fontWeight: 600,
          color: TG.accent,
          backgroundColor: "rgba(14, 22, 33, 0.85)",
          borderRadius: 8,
          padding: "6px 14px",
        }}
      >
        {text}
      </div>
    </div>
  );
};
