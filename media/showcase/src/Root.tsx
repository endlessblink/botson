import React from "react";
import { Composition } from "remotion";
import { Showcase, PAGE_DURATION, TRANSITION_DURATION } from "./Showcase";

const FPS = 15;
const NUM_PAGES = 6;

// TransitionSeries total: each transition overlaps with adjacent sequences
// Total = N * PAGE_DURATION - (N-1) * TRANSITION_DURATION
// PAGE_DURATION=52, TRANSITION_DURATION=6 → 6*52 - 5*6 = 312 - 30 = 282 frames (~18.8s)
const TOTAL_FRAMES =
  NUM_PAGES * PAGE_DURATION - (NUM_PAGES - 1) * TRANSITION_DURATION;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Showcase"
        component={Showcase}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={960}
        height={540}
      />
    </>
  );
};
