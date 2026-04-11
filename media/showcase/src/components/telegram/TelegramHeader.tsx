import React from "react";
import { TG } from "../../telegram-theme";

interface TelegramHeaderProps {
  title: string;
  subtitle: string;
  emoji: string;
  opacity?: number;
  translateY?: number;
}

export const TelegramHeader: React.FC<TelegramHeaderProps> = ({
  title,
  subtitle,
  emoji,
  opacity = 1,
  translateY = 0,
}) => (
  <div
    style={{
      width: "100%",
      height: 72,
      backgroundColor: TG.headerBg,
      padding: "0 20px",
      display: "flex",
      alignItems: "center",
      direction: "rtl",
      gap: 12,
      opacity,
      transform: `translateY(${translateY}px)`,
      borderBottom: `1px solid ${TG.separator}`,
      flexShrink: 0,
    }}
  >
    {/* Back arrow */}
    <div
      style={{
        fontSize: 22,
        color: TG.accent,
        fontWeight: 300,
      }}
    >
      ›
    </div>

    {/* Group avatar */}
    <div
      style={{
        width: 40,
        height: 40,
        borderRadius: "50%",
        backgroundColor: TG.bubbleOut,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 20,
        flexShrink: 0,
      }}
    >
      {emoji}
    </div>

    {/* Text column */}
    <div style={{ flex: 1, minWidth: 0 }}>
      <div
        style={{
          fontSize: 17,
          fontWeight: 600,
          color: TG.text,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {title}
      </div>
      <div
        style={{
          fontSize: 13,
          color: TG.textSecondary,
        }}
      >
        {subtitle}
      </div>
    </div>

    {/* Three-dot menu */}
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        paddingLeft: 8,
      }}
    >
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          style={{
            width: 4,
            height: 4,
            borderRadius: "50%",
            backgroundColor: TG.textSecondary,
          }}
        />
      ))}
    </div>
  </div>
);
