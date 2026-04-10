import React from "react";
import { AbsoluteFill } from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { HomePage } from "./pages/HomePage";
import { HealthPage } from "./pages/HealthPage";
import { PromptsPage } from "./pages/PromptsPage";
import { PlannerPage } from "./pages/PlannerPage";

const FPS = 15;
export const PAGE_DURATION = Math.round(3.5 * FPS); // 52 frames
export const TRANSITION_DURATION = Math.round(0.5 * FPS); // 7 frames

const PAGES = [HomePage, HealthPage, PromptsPage, PlannerPage];

export const Showcase: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: "#09090b" }}>
    <TransitionSeries>
      {PAGES.map((Page, i) => (
        <React.Fragment key={i}>
          <TransitionSeries.Sequence durationInFrames={PAGE_DURATION}>
            <Page />
          </TransitionSeries.Sequence>
          {i < PAGES.length - 1 && (
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
