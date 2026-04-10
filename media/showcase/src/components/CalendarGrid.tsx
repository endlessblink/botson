import React from "react";
import { useCurrentFrame, useVideoConfig, spring, interpolate } from "remotion";
import { COLORS } from "../data";
import type { CALENDAR_WEEK } from "../data";

export const CalendarGrid: React.FC<{
  data: typeof CALENDAR_WEEK;
  delay: number;
}> = ({ data, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  return (
    <div
      style={{
        display: "flex",
        gap: 6,
        direction: "rtl",
      }}
    >
      {data.map((day, colIndex) => {
        const colEntrance = spring({
          frame: frame - delay - colIndex * 2,
          fps,
          config: { damping: 18, stiffness: 200 },
        });

        const colOpacity = colEntrance;
        const colY = interpolate(colEntrance, [0, 1], [10, 0]);

        return (
          <div
            key={day.day}
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 4,
              opacity: colOpacity,
              transform: `translateY(${colY}px)`,
              padding: "8px 4px",
              borderRadius: 6,
              border: day.isToday
                ? `1px solid ${COLORS.emerald}40`
                : "1px solid transparent",
              backgroundColor: day.isToday ? `${COLORS.emerald}10` : "transparent",
            }}
          >
            {/* Day name */}
            <span
              style={{
                color: COLORS.textMuted,
                fontSize: 10,
                fontWeight: 500,
              }}
            >
              {day.day}
            </span>

            {/* Date number */}
            <span
              style={{
                color: day.isToday ? COLORS.emerald : COLORS.text,
                fontSize: 16,
                fontWeight: 600,
              }}
            >
              {day.date}
            </span>

            {/* Event dots */}
            <div
              style={{
                display: "flex",
                gap: 3,
                marginTop: 2,
                minHeight: 6,
              }}
            >
              {day.events.map((event, dotIndex) => {
                const dotEntrance = spring({
                  frame: frame - delay - colIndex * 2 - 6 - dotIndex * 2,
                  fps,
                  config: { damping: 8, stiffness: 200 },
                });

                return (
                  <div
                    key={dotIndex}
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      backgroundColor: event.color,
                      transform: `scale(${dotEntrance})`,
                    }}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
};
