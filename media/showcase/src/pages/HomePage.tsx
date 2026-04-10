import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { spring, interpolate } from "remotion";
import { DashboardShell } from "../components/DashboardShell";
import { StatCard } from "../components/StatCard";
import { TableRow } from "../components/TableRow";
import { MiniChart } from "../components/MiniChart";
import {
  LEADERBOARD,
  STAT_CARDS_HOME,
  CHART_DATA,
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

export const HomePage: React.FC = () => {
  return (
    <DashboardShell activePage="home" title="סקירה כללית" subtitle="מבט על הקהילה">
      {/* Stat Cards Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 16 }}>
        {STAT_CARDS_HOME.map((card, i) => (
          <StatCard
            key={card.label}
            icon={card.icon}
            label={card.label}
            value={card.value}
            sub={card.sub}
            delay={5 + i * 3}
          />
        ))}
      </div>

      {/* Leaderboard + Events */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <Panel title="טבלת רמות" delay={17}>
          {LEADERBOARD.map((entry, i) => (
            <TableRow
              key={entry.rank}
              rank={entry.rank}
              name={entry.name}
              points={entry.points}
              emoji={entry.emoji}
              delay={20 + i * 3}
            />
          ))}
        </Panel>

        <Panel title="אירועים קרובים" delay={17}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: 80,
              color: COLORS.textMuted,
              fontSize: 11,
              direction: "rtl",
            }}
          >
            אין אירועים קרובים
          </div>
        </Panel>
      </div>

      {/* Charts */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Panel title="פעילות ב-14 ימים" delay={25}>
          <MiniChart data={CHART_DATA} color={COLORS.emerald} delay={27} width={300} height={60} />
        </Panel>

        <Panel title="סוגי פעילות" delay={25}>
          <MiniChart data={[35, 25, 20, 15, 5]} color={COLORS.sky} delay={27} width={300} height={60} />
        </Panel>
      </div>
    </DashboardShell>
  );
};
