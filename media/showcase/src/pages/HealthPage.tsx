import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { spring, interpolate } from "remotion";
import { DashboardShell } from "../components/DashboardShell";
import { StatCard } from "../components/StatCard";
import { FeatureToggle } from "../components/FeatureToggle";
import { StatusDot } from "../components/StatusDot";
import {
  STAT_CARDS_HEALTH,
  FEATURES,
  SCHEDULE_TODAY,
  COLORS,
} from "../data";

const Panel: React.FC<{
  title: string;
  delay: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ title, delay, children, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({ frame: frame - delay, fps, config: { damping: 18, stiffness: 180 } });

  return (
    <div
      style={{
        background: COLORS.card,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 8,
        opacity: progress,
        transform: `translateY(${interpolate(progress, [0, 1], [12, 0])}px)`,
        overflow: "hidden",
        ...style,
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          borderBottom: `1px solid ${COLORS.border}`,
          fontSize: 11,
          fontWeight: 600,
          color: COLORS.text,
          direction: "rtl",
        }}
      >
        {title}
      </div>
      <div style={{ padding: 10 }}>{children}</div>
    </div>
  );
};

const ScheduleRow: React.FC<{
  time: string;
  label: string;
  done: boolean;
  delay: number;
}> = ({ time, label, done, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({ frame: frame - delay, fps, config: { damping: 18, stiffness: 180 } });

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 0",
        opacity: progress,
        transform: `translateX(${interpolate(progress, [0, 1], [-8, 0])}px)`,
        direction: "rtl",
      }}
    >
      <StatusDot color={done ? COLORS.emerald : COLORS.textMuted} delay={delay} pulse={done} />
      <span style={{ fontSize: 10, color: COLORS.textSecondary, fontFamily: "monospace", minWidth: 36 }}>
        {time}
      </span>
      <span style={{ fontSize: 11, color: COLORS.text }}>{label}</span>
    </div>
  );
};

export const HealthPage: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Warning banner animation
  const bannerProgress = spring({ frame: frame - 3, fps, config: { damping: 18, stiffness: 180 } });

  return (
    <DashboardShell activePage="health" title="מצב הבוט" subtitle="מעקב אחרי הבוט בזמן אמת">
      {/* Warning Banner */}
      <div
        style={{
          background: "rgba(251, 191, 36, 0.1)",
          border: "1px solid rgba(251, 191, 36, 0.3)",
          borderRadius: 8,
          padding: 12,
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          direction: "rtl",
          opacity: bannerProgress,
          transform: `translateY(${interpolate(bannerProgress, [0, 1], [-10, 0])}px)`,
        }}
      >
        <span style={{ fontSize: 10, color: COLORS.amber }}>
          הבוט מריץ גרסה ישנה (f9e218f) — הקוד הנוכחי הוא b104a0d
        </span>
        <div
          style={{
            background: "rgba(251, 191, 36, 0.2)",
            border: "1px solid rgba(251, 191, 36, 0.4)",
            borderRadius: 4,
            padding: "3px 10px",
            fontSize: 9,
            color: COLORS.amber,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          הפעל מחדש
        </div>
      </div>

      {/* Stat Cards Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 16 }}>
        {STAT_CARDS_HEALTH.map((card, i) => (
          <StatCard
            key={card.label}
            icon={card.icon}
            label={card.label}
            value={card.value}
            sub={card.sub}
            delay={6 + i * 3}
            color={card.color}
          />
        ))}
      </div>

      {/* Features + Schedule */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="מצב תכונות" delay={17}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {FEATURES.map((feat, i) => (
              <FeatureToggle
                key={feat.name}
                name={feat.name}
                active={feat.active}
                delay={18 + i * 2}
              />
            ))}
          </div>
        </Panel>

        <Panel title="לוח זמנים היום" delay={17}>
          {SCHEDULE_TODAY.map((item, i) => (
            <ScheduleRow
              key={item.label}
              time={item.time}
              label={item.label}
              done={item.done}
              delay={20 + i * 3}
            />
          ))}
        </Panel>
      </div>
    </DashboardShell>
  );
};
