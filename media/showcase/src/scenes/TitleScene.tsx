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
import { AnnotationOverlay } from "../components/telegram/AnnotationOverlay";

const { fontFamily } = loadFont();

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Header slides down from top
  const headerY = interpolate(
    spring({ frame, fps, config: { damping: 14 } }),
    [0, 1],
    [-72, 0]
  );
  const headerOpacity = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Avatar scales in
  const avatarScale = spring({
    frame: frame - 5,
    fps,
    config: { damping: 12 },
  });

  // Group name fades in
  const nameOpacity = interpolate(frame, [10, 20], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Tagline fades in
  const taglineOpacity = interpolate(frame, [18, 28], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Member count fades in
  const memberOpacity = interpolate(frame, [24, 34], [0, 1], {
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
      {/* Telegram header */}
      <TelegramHeader
        title="אלהוריים וזה"
        subtitle="81 חברים"
        emoji="💬"
        opacity={headerOpacity}
        translateY={headerY}
      />

      {/* Center content */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
        }}
      >
        {/* Group avatar */}
        <div
          style={{
            width: 80,
            height: 80,
            borderRadius: "50%",
            backgroundColor: TG.bubbleOut,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 40,
            transform: `scale(${avatarScale})`,
          }}
        >
          💬
        </div>

        {/* Group name */}
        <div
          style={{
            fontSize: 30,
            fontWeight: 700,
            color: TG.text,
            opacity: nameOpacity,
            marginTop: 8,
          }}
        >
          אלהוריים וזה
        </div>

        {/* Tagline */}
        <div
          style={{
            fontSize: 16,
            color: TG.textSecondary,
            opacity: taglineOpacity,
          }}
        >
          קהילת צ׳ילדפרי 🇮🇱
        </div>

        {/* Member count pill */}
        <div
          style={{
            fontSize: 13,
            color: TG.textSecondary,
            backgroundColor: TG.headerBg,
            border: `1px solid ${TG.bubbleOut}`,
            borderRadius: 12,
            padding: "4px 14px",
            opacity: memberOpacity,
            marginTop: 4,
          }}
        >
          👥 81 חברים
        </div>
      </div>

      <AnnotationOverlay text="הקבוצה שלנו בטלגרם 👋" fadeInFrame={30} />
    </AbsoluteFill>
  );
};
