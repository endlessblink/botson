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

interface SidePanelTopic {
  emoji: string;
  name: string;
  colorIndex: number;
}

interface TopicSidePanelProps {
  topics: SidePanelTopic[];
  activeIndex?: number;
  opacity?: number;
}

export const TopicSidePanel: React.FC<TopicSidePanelProps> = ({
  topics,
  activeIndex = 0,
  opacity = 1,
}) => (
  <div
    style={{
      width: 180,
      backgroundColor: TG.headerBg,
      borderLeft: `1px solid ${TG.separator}`,
      display: "flex",
      flexDirection: "column",
      paddingTop: 6,
      gap: 2,
      opacity,
      flexShrink: 0,
      overflow: "hidden",
    }}
  >
    {topics.map((topic, i) => {
      const isActive = i === activeIndex;
      return (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            direction: "rtl",
            backgroundColor: isActive
              ? "rgba(100, 181, 239, 0.15)"
              : "transparent",
            borderRadius: 6,
            margin: "0 4px",
          }}
        >
          {/* Squircle icon */}
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              backgroundColor:
                TOPIC_COLORS[topic.colorIndex % TOPIC_COLORS.length],
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 16,
              flexShrink: 0,
              opacity: isActive ? 1 : 0.6,
            }}
          >
            {topic.emoji}
          </div>

          {/* Topic name */}
          <div
            style={{
              fontSize: 12,
              fontWeight: isActive ? 600 : 400,
              color: isActive ? TG.text : TG.textSecondary,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              flex: 1,
              minWidth: 0,
            }}
          >
            {topic.name}
          </div>
        </div>
      );
    })}
  </div>
);
