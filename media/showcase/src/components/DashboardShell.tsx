import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";

const NAV_ITEMS = [
  "סקירה כללית",
  "הודעות ושאלות",
  "רמות",
  "אירועים",
  "טריוויה",
  "ספאם",
  "משתמשים חסומים",
  "חברים",
  "לוג פעילות",
  "תכנון שבועי",
  "מצב הבוט",
];

const BOTTOM_ITEMS = ["הגדרות", "התנתק"];

const SIDEBAR_WIDTH = 200;

export const DashboardShell: React.FC<{
  activePage: string;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}> = ({ activePage, title, subtitle, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const sidebarSpring = spring({
    frame,
    fps,
    config: { damping: 20, stiffness: 180 },
  });

  const sidebarX = interpolate(sidebarSpring, [0, 1], [SIDEBAR_WIDTH, 0]);

  const contentOpacity = spring({
    frame: frame - 4,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  return (
    <div
      style={{
        width: 960,
        height: 540,
        backgroundColor: COLORS.bg,
        direction: "rtl",
        display: "flex",
        fontFamily: "system-ui, -apple-system, sans-serif",
        overflow: "hidden",
        position: "relative",
      }}
    >
      {/* Sidebar (right side in RTL) */}
      <div
        style={{
          width: SIDEBAR_WIDTH,
          minWidth: SIDEBAR_WIDTH,
          backgroundColor: COLORS.card,
          borderLeft: `1px solid ${COLORS.border}`,
          display: "flex",
          flexDirection: "column",
          padding: "16px 0",
          transform: `translateX(${sidebarX}px)`,
          boxSizing: "border-box",
        }}
      >
        {/* Logo */}
        <div
          style={{
            padding: "0 16px 16px",
            borderBottom: `1px solid ${COLORS.border}`,
            marginBottom: 8,
          }}
        >
          <span
            style={{
              color: COLORS.text,
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: "0.02em",
            }}
          >
            Botson
          </span>
        </div>

        {/* Nav items */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 1 }}>
          {NAV_ITEMS.map((item) => {
            const isActive = item === activePage;
            return (
              <div
                key={item}
                style={{
                  padding: "7px 16px",
                  fontSize: 12,
                  color: isActive ? COLORS.text : COLORS.textSecondary,
                  backgroundColor: isActive ? COLORS.hover : "transparent",
                  borderRight: isActive ? `2px solid ${COLORS.emerald}` : "2px solid transparent",
                  cursor: "default",
                }}
              >
                {item}
              </div>
            );
          })}
        </div>

        {/* Bottom items */}
        <div
          style={{
            borderTop: `1px solid ${COLORS.border}`,
            paddingTop: 8,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          {BOTTOM_ITEMS.map((item) => (
            <div
              key={item}
              style={{
                padding: "7px 16px",
                fontSize: 12,
                color: COLORS.textMuted,
              }}
            >
              {item}
            </div>
          ))}
        </div>
      </div>

      {/* Content area */}
      <div
        style={{
          flex: 1,
          padding: 24,
          opacity: contentOpacity,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Page header */}
        <div style={{ marginBottom: 20 }}>
          <h1
            style={{
              color: COLORS.text,
              fontSize: 22,
              fontWeight: 700,
              margin: 0,
              lineHeight: 1.3,
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p
              style={{
                color: COLORS.textSecondary,
                fontSize: 13,
                margin: "4px 0 0",
              }}
            >
              {subtitle}
            </p>
          )}
        </div>

        {/* Children */}
        <div style={{ flex: 1, overflow: "hidden" }}>{children}</div>
      </div>
    </div>
  );
};
