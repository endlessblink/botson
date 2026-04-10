import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { spring, interpolate } from "remotion";
import { DashboardShell } from "../components/DashboardShell";
import { CalendarGrid } from "../components/CalendarGrid";
import { PENDING_ITEMS, CALENDAR_WEEK, COLORS } from "../data";

const PendingItem: React.FC<{
  text: string;
  done: boolean;
  delay: number;
}> = ({ text, done, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({ frame: frame - delay, fps, config: { damping: 18, stiffness: 180 } });

  // Checkbox animation: circle appears, then checkmark for done items
  const checkProgress = done
    ? spring({ frame: frame - delay - 3, fps, config: { damping: 14, stiffness: 200 } })
    : 0;

  const circleSize = 14;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "7px 0",
        direction: "rtl",
        opacity: progress,
        transform: `translateX(${interpolate(progress, [0, 1], [-10, 0])}px)`,
      }}
    >
      {/* Checkbox */}
      <div
        style={{
          width: circleSize,
          height: circleSize,
          borderRadius: "50%",
          border: done ? "none" : `1.5px solid ${COLORS.textMuted}`,
          background: done ? COLORS.emerald : "transparent",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          transform: `scale(${interpolate(progress, [0, 1], [0.5, 1])})`,
        }}
      >
        {done && (
          <svg
            width={8}
            height={8}
            viewBox="0 0 10 10"
            style={{
              opacity: checkProgress,
              transform: `scale(${checkProgress})`,
            }}
          >
            <path
              d="M2 5 L4.5 7.5 L8 3"
              stroke="white"
              strokeWidth={2}
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeDasharray={10}
              strokeDashoffset={interpolate(checkProgress, [0, 1], [10, 0])}
            />
          </svg>
        )}
      </div>

      {/* Text */}
      <span
        style={{
          fontSize: 11,
          color: done ? COLORS.textMuted : COLORS.text,
          textDecoration: done ? "line-through" : "none",
        }}
      >
        {text}
      </span>
    </div>
  );
};

export const PlannerPage: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const sectionProgress = spring({ frame: frame - 3, fps, config: { damping: 18, stiffness: 180 } });

  return (
    <DashboardShell activePage="planner" title="תכנון שבועי" subtitle="תכנון ומעקב שבועי">
      {/* Section Header: Pending Actions */}
      <div
        style={{
          borderBottom: `1px solid ${COLORS.border}`,
          paddingBottom: 8,
          marginBottom: 8,
          direction: "rtl",
          opacity: sectionProgress,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: COLORS.text }}>
          פעולות ממתינות
        </span>
      </div>

      {/* Pending Items */}
      <div style={{ marginBottom: 20 }}>
        {PENDING_ITEMS.map((item, i) => (
          <PendingItem
            key={item.text}
            text={item.text}
            done={item.done}
            delay={6 + i * 3}
          />
        ))}
      </div>

      {/* Calendar Grid */}
      <CalendarGrid data={CALENDAR_WEEK} delay={15} />
    </DashboardShell>
  );
};
