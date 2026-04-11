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
import { AnnotationOverlay } from "../components/telegram/AnnotationOverlay";

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

const MESSAGES = [
  {
    sender: "Botson 🤖",
    text: "⭐ מיכל_א קיבלה 15 נקודות!\nדרגה חדשה: כוכב/ת 🌟",
    time: "14:25",
    type: "incoming" as const,
    showAvatar: true,
    start: 5,
  },
  {
    text: "אייי מגניב! 🔥",
    time: "14:25",
    type: "outgoing" as const,
    showAvatar: false,
    start: 28,
  },
  {
    sender: "Botson 🤖",
    text: "🧠 שאלת טריוויה!\nמה הסרט הכי מרוויח בהיסטוריה?",
    time: "14:30",
    type: "incoming" as const,
    showAvatar: true,
    start: 42,
  },
  {
    sender: "Botson 🤖",
    text: "🎉 אירוע חדש!\nמפגש ערב משחקים\n📅 יום שישי, 20:00\n✅ 8 אישרו הגעה",
    time: "14:35",
    type: "incoming" as const,
    showAvatar: true,
    start: 65,
  },
];

export const BotFeaturesScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Side panel slides in
  const panelOpacity = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp",
  });

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
      {/* Topic header — we're inside "כללי" */}
      <TelegramHeader title="כללי" subtitle="אלהוריים וזה" emoji="💬" />

      {/* Main area: side panel + chat */}
      <div style={{ flex: 1, display: "flex", direction: "rtl", overflow: "hidden" }}>
        {/* Side panel with topic icons */}
        <TopicSidePanel
          topics={SIDE_TOPICS}
          activeIndex={1}
          opacity={panelOpacity}
        />

        {/* Chat area */}
        <div
          style={{
            flex: 1,
            padding: "16px 12px 8px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "flex-start",
            overflow: "hidden",
          }}
        >
          {MESSAGES.map((msg, i) => {
            const bubbleOpacity = interpolate(
              frame,
              [msg.start, msg.start + 8],
              [0, 1],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
            );
            const bubbleY = interpolate(
              spring({
                frame: frame - msg.start,
                fps,
                config: { damping: 14 },
              }),
              [0, 1],
              [15, 0]
            );

            return (
              <ChatBubble
                key={i}
                sender={msg.sender}
                text={msg.text}
                time={msg.time}
                type={msg.type}
                opacity={bubbleOpacity}
                translateY={bubbleY}
                showAvatar={msg.showAvatar}
              />
            );
          })}
        </div>
      </div>

      {/* Fake message input bar */}
      <div
        style={{
          height: 48,
          backgroundColor: TG.headerBg,
          display: "flex",
          alignItems: "center",
          padding: "0 12px",
          gap: 10,
          direction: "rtl",
          flexShrink: 0,
        }}
      >
        <div style={{ fontSize: 18, color: TG.textSecondary }}>📎</div>
        <div
          style={{
            flex: 1,
            height: 36,
            borderRadius: 20,
            backgroundColor: TG.mainBg,
            display: "flex",
            alignItems: "center",
            padding: "0 16px",
          }}
        >
          <span style={{ fontSize: 14, color: TG.textSecondary }}>הודעה</span>
        </div>
        <div style={{ fontSize: 18, color: TG.textSecondary }}>🎤</div>
      </div>

      <AnnotationOverlay
        text="הבוט מנהל נקודות, טריוויה ואירועים"
        fadeInFrame={15}
      />
    </AbsoluteFill>
  );
};
