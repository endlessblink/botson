import React from "react";
import { AbsoluteFill } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { TitleScene } from "./scenes/TitleScene";
import { TopicsScene } from "./scenes/TopicsScene";
import { BotFeaturesScene } from "./scenes/BotFeaturesScene";
import { WelcomeScene } from "./scenes/WelcomeScene";

const FPS = 15;

// Scene durations in frames (sum=321, minus 3×7 transitions = 300 total = 20s)
const TITLE_DURATION = 51;
const TOPICS_DURATION = 105;
const BOT_DURATION = 105;
const WELCOME_DURATION = 60;

export const TRANSITION_DURATION = Math.round(0.5 * FPS); // 7

const SCENES = [
  { Component: TitleScene, duration: TITLE_DURATION },
  { Component: TopicsScene, duration: TOPICS_DURATION },
  { Component: BotFeaturesScene, duration: BOT_DURATION },
  { Component: WelcomeScene, duration: WELCOME_DURATION },
];

// Total: sum of durations - overlapping transitions
export const TOTAL_DURATION =
  SCENES.reduce((sum, s) => sum + s.duration, 0) -
  (SCENES.length - 1) * TRANSITION_DURATION;

export const TelegramOnboarding: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: "#0E1621" }}>
    <TransitionSeries>
      {SCENES.map(({ Component, duration }, i) => (
        <React.Fragment key={i}>
          <TransitionSeries.Sequence durationInFrames={duration}>
            <Component />
          </TransitionSeries.Sequence>
          {i < SCENES.length - 1 && (
            <TransitionSeries.Transition
              presentation={fade()}
              timing={linearTiming({ durationInFrames: TRANSITION_DURATION })}
            />
          )}
        </React.Fragment>
      ))}
    </TransitionSeries>
  </AbsoluteFill>
);
