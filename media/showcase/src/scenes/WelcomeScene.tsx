import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  spring,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/Heebo";
import { TG } from "../telegram-theme";
import { TelegramHeader } from "../components/telegram/TelegramHeader";
import { ChatBubble } from "../components/telegram/ChatBubble";
import { TopicSidePanel } from "../components/telegram/TopicSidePanel";

const { fontFamily } = loadFont();

const SIDE_TOPICS = [
  { emoji: "📋", name: "כל ההודעות", colorIndex: 0 },
  { emoji: "💬", name: "כללי", colorIndex: 0 },
  { emoji: "🎯", name: "יום יום", colorIndex: 1 },
  { emoji: "🎬", name: "סרטים וסדרות", colorIndex: 2 },
  { emoji: "🎮", name: "גיימינג", colorIndex: 3 },
  { emoji: "🐕", name: "כל מה שחמוד", colorIndex: 4 },
  { emoji: "🎨", name: "אומנות ויצירה", colorIndex: 5 },
  { emoji: "💕", name: "מכירים", colorIndex: 0 },
  { emoji: "🤖", name: "AI וטכנולוגיה", colorIndex: 1 },
];

export const WelcomeScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Welcome bubble entrance
  const bubbleOpacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });
  const bubbleY = interpolate(
    spring({ frame, fps, config: { damping: 12 } }),
    [0, 1],
    [15, 0]
  );

  // CTA fade in
  const ctaOpacity = interpolate(frame, [25, 38], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Subtle CTA pulse after appearing
  const ctaPulse =
    frame > 38
      ? interpolate(Math.sin((frame - 38) * 0.3), [-1, 1], [0.7, 1])
      : ctaOpacity;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: TG.mainBg,
        fontFamily,
        direction: "rtl",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Topic header — welcome channel */}
      <TelegramHeader
        title="מצטרפים חדשים"
        subtitle="אלהוריים וזה"
        emoji="🏠"
      />

      {/* Main area: side panel + chat */}
      <div style={{ flex: 1, display: "flex", direction: "rtl", overflow: "hidden" }}>
        {/* Side panel with topic icons */}
        <TopicSidePanel topics={SIDE_TOPICS} activeIndex={-1} />

        {/* Chat area */}
        <div
          style={{
            flex: 1,
            padding: "24px 12px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 24,
          }}
        >
          {/* Welcome bot bubble */}
          <ChatBubble
            sender="Botson 🤖"
            text={
              "ברוכים הבאים! 🎉\n\nתבחרו נושא שמעניין אתכם\nוהצטרפו לשיחה"
            }
            time="עכשיו"
            type="incoming"
            opacity={bubbleOpacity}
            translateY={bubbleY}
            showAvatar
          />

          {/* CTA */}
          <div
            style={{
              fontSize: 18,
              color: TG.accent,
              opacity: ctaPulse,
              textAlign: "center",
            }}
          >
            👇 בחרו נושא והצטרפו
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
