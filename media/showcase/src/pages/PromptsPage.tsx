import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { spring, interpolate } from "remotion";
import { DashboardShell } from "../components/DashboardShell";
import { FeatureToggle } from "../components/FeatureToggle";
import { TimelineItem } from "../components/TimelineItem";
import { TIMELINE_ITEMS, COLORS } from "../data";

export const PromptsPage: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Toggle row animation
  const toggleProgress = spring({ frame: frame - 3, fps, config: { damping: 18, stiffness: 180 } });

  // Section header animation
  const sectionProgress = spring({ frame: frame - 5, fps, config: { damping: 18, stiffness: 180 } });

  // Tab bar animation
  const tabProgress = spring({ frame: frame - 28, fps, config: { damping: 18, stiffness: 180 } });

  const tabs = ["לוח זמנים", "הודעות בוקר/ערב", "שאלות לדיון"];

  return (
    <DashboardShell activePage="prompts" title="הודעות ושאלות" subtitle="ניהול הודעות בוקר/ערב ושאלות לדיון">
      {/* Toggle Row */}
      <div
        style={{
          display: "flex",
          gap: 16,
          marginBottom: 16,
          direction: "rtl",
          opacity: toggleProgress,
          transform: `translateY(${interpolate(toggleProgress, [0, 1], [8, 0])}px)`,
        }}
      >
        <FeatureToggle name="דיונים" active={true} delay={3} />
        <FeatureToggle name="סיכום" active={false} delay={4} />
      </div>

      {/* Section Header */}
      <div
        style={{
          borderBottom: `1px solid ${COLORS.border}`,
          paddingBottom: 8,
          marginBottom: 12,
          direction: "rtl",
          opacity: sectionProgress,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: COLORS.text }}>
          לוח זמנים יומי
        </span>
      </div>

      {/* Timeline Items */}
      <div style={{ marginBottom: 16 }}>
        {TIMELINE_ITEMS.map((item, i) => (
          <TimelineItem
            key={item.time}
            time={item.time}
            label={item.label}
            badge={item.badge}
            badgeColor={item.badgeColor}
            content={item.content}
            delay={8 + i * 5}
            isLast={i === TIMELINE_ITEMS.length - 1}
          />
        ))}
      </div>

      {/* Tab Bar */}
      <div
        style={{
          display: "flex",
          gap: 0,
          direction: "rtl",
          borderTop: `1px solid ${COLORS.border}`,
          opacity: tabProgress,
          transform: `translateY(${interpolate(tabProgress, [0, 1], [8, 0])}px)`,
        }}
      >
        {tabs.map((tab, i) => (
          <div
            key={tab}
            style={{
              padding: "8px 16px",
              fontSize: 10,
              fontWeight: i === 0 ? 600 : 400,
              color: i === 0 ? COLORS.text : COLORS.textMuted,
              borderBottom: i === 0 ? `2px solid ${COLORS.text}` : "2px solid transparent",
              cursor: "pointer",
            }}
          >
            {tab}
          </div>
        ))}
      </div>
    </DashboardShell>
  );
};
