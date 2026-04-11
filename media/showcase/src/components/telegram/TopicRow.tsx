import React from "react";
import { TG } from "../../telegram-theme";

// Telegram's 6 topic icon fallback colors
const TOPIC_COLORS = [
  "#6FB9F0", // sky blue
  "#FFD67E", // warm yellow
  "#CB86DB", // purple
  "#8EEE98", // mint green
  "#FF93B2", // pink
  "#FB6F5F", // coral
];

interface TopicRowProps {
  emoji: string;
  name: string;
  preview: string;
  time: string;
  unread?: number;
  opacity: number;
  translateY: number;
  highlighted?: boolean;
  highlightOpacity?: number;
  colorIndex?: number;
  pressed?: boolean;
  pressedOpacity?: number;
}

export const TopicRow: React.FC<TopicRowProps> = ({
  emoji,
  name,
  preview,
  time,
  unread,
  opacity,
  translateY,
  highlighted = false,
  highlightOpacity = 0,
  colorIndex = 0,
  pressed = false,
  pressedOpacity = 0,
}) => (
  <div
    style={{
      width: "100%",
      height: 68,
      padding: "0 20px",
      display: "flex",
      alignItems: "center",
      direction: "rtl",
      borderBottom: `1px solid ${TG.separator}`,
      opacity,
      transform: `translateY(${translateY}px)`,
      backgroundColor: pressed
        ? `rgba(30, 45, 61, ${pressedOpacity})`
        : "transparent",
      boxShadow: highlighted
        ? `inset 0 0 0 1.5px rgba(100, 181, 239, ${highlightOpacity})`
        : "none",
      borderRadius: highlighted || pressed ? 4 : 0,
    }}
  >
    {/* Topic icon tile — squircle with colored background */}
    <div
      style={{
        width: 42,
        height: 42,
        borderRadius: 10,
        backgroundColor: TOPIC_COLORS[colorIndex % TOPIC_COLORS.length],
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 22,
        flexShrink: 0,
      }}
    >
      {emoji}
    </div>

    {/* Spacer */}
    <div style={{ width: 12, flexShrink: 0 }} />

    {/* Text column */}
    <div style={{ flex: 1, minWidth: 0 }}>
      <div
        style={{
          fontSize: 15,
          fontWeight: 600,
          color: TG.text,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {name}
      </div>
      <div
        style={{
          fontSize: 13,
          color: TG.textSecondary,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          marginTop: 2,
        }}
      >
        {preview}
      </div>
    </div>

    {/* Right column: time + unread */}
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 4,
        paddingRight: 12,
        flexShrink: 0,
      }}
    >
      <div style={{ fontSize: 12, color: TG.textSecondary }}>{time}</div>
      {unread != null && unread > 0 && (
        <div
          style={{
            minWidth: 20,
            height: 20,
            borderRadius: 10,
            backgroundColor: TG.unreadBadge,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "0 6px",
          }}
        >
          <span style={{ fontSize: 11, fontWeight: 700, color: "#fff" }}>
            {unread}
          </span>
        </div>
      )}
    </div>
  </div>
);
