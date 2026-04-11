import React from "react";
import { TG } from "../../telegram-theme";

interface ChatBubbleProps {
  sender?: string;
  text: string;
  time: string;
  type: "incoming" | "outgoing";
  opacity: number;
  translateY: number;
  showAvatar?: boolean;
}

export const ChatBubble: React.FC<ChatBubbleProps> = ({
  sender,
  text,
  time,
  type,
  opacity,
  translateY,
  showAvatar = false,
}) => {
  const isIncoming = type === "incoming";

  return (
    <div
      style={{
        display: "flex",
        direction: "rtl",
        alignItems: "flex-start",
        gap: 8,
        alignSelf: isIncoming ? "flex-end" : "flex-start",
        maxWidth: "80%",
        opacity,
        transform: `translateY(${translateY}px)`,
        marginBottom: 8,
      }}
    >
      {/* Bot avatar (only for incoming with avatar) */}
      {showAvatar && isIncoming && (
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "50%",
            backgroundColor: TG.bubbleOut,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 16,
            flexShrink: 0,
            marginTop: 2,
          }}
        >
          🤖
        </div>
      )}

      {/* Bubble */}
      <div
        style={{
          backgroundColor: isIncoming ? TG.bubbleIn : TG.bubbleOut,
          padding: "8px 12px",
          borderRadius: 12,
          borderTopRightRadius: isIncoming ? 4 : 12,
          borderTopLeftRadius: isIncoming ? 12 : 4,
        }}
      >
        {/* Sender name */}
        {sender && (
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: TG.accent,
              marginBottom: 4,
            }}
          >
            {sender}
          </div>
        )}

        {/* Message text */}
        <div
          style={{
            fontSize: 14,
            color: TG.text,
            lineHeight: 1.45,
            whiteSpace: "pre-line",
          }}
        >
          {text}
        </div>

        {/* Timestamp */}
        <div
          style={{
            fontSize: 11,
            color: TG.textSecondary,
            marginTop: 4,
            textAlign: "left",
          }}
        >
          {time}
        </div>
      </div>
    </div>
  );
};
