import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Heebo";
import { TG } from "../telegram-theme";
import { TelegramHeader } from "../components/telegram/TelegramHeader";
import { TopicRow } from "../components/telegram/TopicRow";
import { AnnotationOverlay } from "../components/telegram/AnnotationOverlay";

const { fontFamily } = loadFont();

const TOPICS = [
  {
    emoji: "💬",
    name: "כללי",
    preview: "דניאל_צ: מישהו רוצה קפה?",
    time: "14:22",
    unread: 5,
  },
  {
    emoji: "🎯",
    name: "יום יום",
    preview: "מיכל_א: סיימתי ריצה! 💪",
    time: "13:50",
    unread: 2,
  },
  {
    emoji: "🎬",
    name: "סרטים וסדרות",
    preview: "יובל42: ראיתם את הסדרה החדשה?",
    time: "12:15",
  },
  {
    emoji: "🎮",
    name: "גיימינג",
    preview: "נועה_ש: מישהו למשחק ערב?",
    time: "11:40",
    unread: 3,
  },
  {
    emoji: "🐕",
    name: "כל מה שחמוד",
    preview: "שירה_ל: החתול שלי 😍",
    time: "10:30",
  },
  {
    emoji: "🎨",
    name: "אומנות ויצירה",
    preview: "רון_מ: ציור חדש שלי",
    time: "09:15",
  },
  {
    emoji: "💕",
    name: "מכירים",
    preview: "הצטרפו 2 חברים חדשים",
    time: "אתמול",
    unread: 1,
  },
  {
    emoji: "🤖",
    name: "AI וטכנולוגיה",
    preview: "טל_ב: ראיתם את הכלי החדש?",
    time: "אתמול",
  },
];

// Which topic indices get highlighted and when
const HIGHLIGHTS = [
  { index: 0, start: 60, end: 72 }, // כללי
  { index: 3, start: 72, end: 84 }, // גיימינג
  { index: 6, start: 84, end: 96 }, // מכירים
];

export const TopicsScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Sub-header fade in
  const subHeaderOpacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: TG.mainBg,
        fontFamily,
        direction: "rtl",
      }}
    >
      {/* Static header */}
      <TelegramHeader
        title="אלהוריים וזה"
        subtitle="81 חברים"
        emoji="💬"
      />

      {/* Topics sub-header */}
      <div
        style={{
          height: 36,
          padding: "0 20px",
          display: "flex",
          alignItems: "center",
          opacity: subHeaderOpacity,
        }}
      >
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: TG.textSecondary,
            letterSpacing: 0.5,
          }}
        >
          נושאים
        </div>
      </div>

      {/* Topic rows */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        {TOPICS.map((topic, i) => {
          const delay = 5 + i * 7;
          const rowOpacity = interpolate(frame, [delay, delay + 8], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
          });
          const rowTranslateY = interpolate(
            frame,
            [delay, delay + 10],
            [20, 0],
            {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }
          );

          // Check if this topic is highlighted
          const highlight = HIGHLIGHTS.find((h) => h.index === i);
          let isHighlighted = false;
          let highlightOpacity = 0;
          if (highlight) {
            isHighlighted =
              frame >= highlight.start && frame <= highlight.end + 5;
            highlightOpacity = interpolate(
              frame,
              [
                highlight.start,
                highlight.start + 4,
                highlight.end,
                highlight.end + 5,
              ],
              [0, 0.8, 0.8, 0],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            );
          }

          // Tap animation on כללי (index 0) in final frames
          const isPressed = i === 0 && frame >= 96;
          const pressedOpacity = isPressed
            ? interpolate(frame, [96, 99], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              })
            : 0;

          return (
            <TopicRow
              key={i}
              emoji={topic.emoji}
              name={topic.name}
              preview={topic.preview}
              time={topic.time}
              unread={topic.unread}
              opacity={rowOpacity}
              translateY={rowTranslateY}
              highlighted={isHighlighted}
              highlightOpacity={highlightOpacity}
              colorIndex={i}
              pressed={isPressed}
              pressedOpacity={pressedOpacity}
            />
          );
        })}
      </div>

      <AnnotationOverlay text="כל נושא = צ׳אט נפרד" fadeInFrame={56} />
    </AbsoluteFill>
  );
};
