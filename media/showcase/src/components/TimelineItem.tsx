import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";

export const TimelineItem: React.FC<{
  time: string;
  label: string;
  badge: string;
  badgeColor: string;
  content: string;
  delay: number;
  isLast?: boolean;
}> = ({ time, badge, badgeColor, content, delay, isLast = false }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const dotEntrance = spring({
    frame: frame - delay,
    fps,
    config: { damping: 8, stiffness: 200 },
  });

  const dotScale = interpolate(dotEntrance, [0, 1], [0, 1]);

  const lineHeight = interpolate(
    spring({
      frame: frame - delay - 3,
      fps,
      config: { damping: 20, stiffness: 150 },
    }),
    [0, 1],
    [0, 40]
  );

  const contentEntrance = spring({
    frame: frame - delay - 4,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  const contentY = interpolate(contentEntrance, [0, 1], [8, 0]);

  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        direction: "rtl",
        minHeight: 56,
      }}
    >
      {/* Time + dot + line column */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          width: 48,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            color: COLORS.textMuted,
            fontSize: 11,
            fontFamily: "monospace",
            marginBottom: 4,
            opacity: contentEntrance,
          }}
        >
          {time}
        </span>
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            backgroundColor: badgeColor,
            transform: `scale(${dotScale})`,
            flexShrink: 0,
          }}
        />
        {!isLast && (
          <div
            style={{
              width: 1,
              height: lineHeight,
              backgroundColor: COLORS.border,
              marginTop: 4,
            }}
          />
        )}
      </div>

      {/* Content column */}
      <div
        style={{
          flex: 1,
          opacity: contentEntrance,
          transform: `translateY(${contentY}px)`,
          paddingBottom: 12,
        }}
      >
        <span
          style={{
            display: "inline-block",
            backgroundColor: badgeColor,
            color: "#000",
            fontSize: 10,
            fontWeight: 600,
            padding: "2px 8px",
            borderRadius: 10,
            marginBottom: 4,
          }}
        >
          {badge}
        </span>
        <div
          style={{
            color: COLORS.text,
            fontSize: 12,
            lineHeight: 1.5,
            marginTop: 4,
          }}
        >
          {content}
        </div>
      </div>
    </div>
  );
};
