import React from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { TransitionSeries, linearTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { BlurOverlay } from "./BlurOverlay";
import { PAGE_REGIONS } from "./PageRegions";

const PAGES = [
  "home.png",
  "health.png",
  "prompts.png",
  "planner.png",
  "activity.png",
  "levels.png",
];

const FPS = 15;
export const PAGE_DURATION = Math.round(3.5 * FPS);
export const TRANSITION_DURATION = Math.round(0.5 * FPS);

const W = 960;
const H = 540;
const SIDEBAR_WIDTH = 200;

/**
 * Renders one region of the page screenshot.
 *
 * KEY: The <Img> is NEVER transformed (no scale, no translate).
 * Only the clipPath animates (expands from a shrunken inset to full region)
 * and opacity fades in. This prevents any pixel shifting artifacts.
 */
const RevealElement: React.FC<{
  page: string;
  top: number;
  bottom: number;
  delay: number;
}> = ({ page, top, bottom, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const GLOBAL_DELAY = 3;
  const progress = spring({
    frame: frame - delay - GLOBAL_DELAY,
    fps,
    config: { damping: 20, stiffness: 200 },
  });

  // Clip inset shrinks inward by PAD pixels, then expands to exact region
  const PAD = 10;
  const clipTop = interpolate(progress, [0, 1], [top + PAD, top]);
  const clipRight = interpolate(progress, [0, 1], [SIDEBAR_WIDTH + PAD, SIDEBAR_WIDTH]);
  const clipBottom = interpolate(progress, [0, 1], [H - bottom + PAD, H - bottom]);
  const clipLeft = interpolate(progress, [0, 1], [PAD, 0]);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        clipPath: `inset(${clipTop}px ${clipRight}px ${clipBottom}px ${clipLeft}px round 6px)`,
        opacity: progress,
      }}
    >
      <Img
        src={staticFile(`screenshots/${page}`)}
        style={{ width: W, height: H, objectFit: "cover", display: "block" }}
      />
    </div>
  );
};

const PageSlide: React.FC<{ page: string }> = ({ page }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Sidebar fades in immediately
  const sidebarProgress = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
  });

  const regions = PAGE_REGIONS[page] || [];

  return (
    <AbsoluteFill style={{ backgroundColor: "#09090b" }}>
      {/* Sidebar — always at correct position, no transform, just opacity */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          clipPath: `inset(0 0 0 ${W - SIDEBAR_WIDTH}px)`,
          opacity: sidebarProgress,
        }}
      >
        <Img
          src={staticFile(`screenshots/${page}`)}
          style={{ width: W, height: H, objectFit: "cover", display: "block" }}
        />
      </div>

      {/* Content regions — staggered expanding reveal */}
      {regions.map((region, i) => (
        <RevealElement
          key={i}
          page={page}
          top={region.top}
          bottom={region.bottom}
          delay={region.delay}
        />
      ))}

      {/* Blur overlay — always on top */}
      <BlurOverlay page={page} />
    </AbsoluteFill>
  );
};

export const Showcase: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: "#09090b" }}>
      <TransitionSeries>
        {PAGES.map((page, i) => (
          <React.Fragment key={page}>
            <TransitionSeries.Sequence durationInFrames={PAGE_DURATION}>
              <PageSlide page={page} />
            </TransitionSeries.Sequence>
            {i < PAGES.length - 1 && (
              <TransitionSeries.Transition
                presentation={fade()}
                timing={linearTiming({
                  durationInFrames: TRANSITION_DURATION,
                })}
              />
            )}
          </React.Fragment>
        ))}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
