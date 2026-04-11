import React from "react";
import { Composition } from "remotion";
import { Showcase, PAGE_DURATION, TRANSITION_DURATION } from "./Showcase";
import {
  TelegramOnboarding,
  TOTAL_DURATION as ONBOARDING_TOTAL,
} from "./TelegramOnboarding";

const PAGES_COUNT = 4;
const TOTAL =
  PAGES_COUNT * PAGE_DURATION - (PAGES_COUNT - 1) * TRANSITION_DURATION;

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="Showcase"
      component={Showcase}
      durationInFrames={TOTAL}
      fps={15}
      width={960}
      height={540}
    />
    <Composition
      id="TelegramOnboarding"
      component={TelegramOnboarding}
      durationInFrames={ONBOARDING_TOTAL}
      fps={15}
      width={720}
      height={720}
    />
  </>
);
