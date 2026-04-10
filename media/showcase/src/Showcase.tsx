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
import { PAGE_REGIONS, ElementRegion } from "./PageRegions";

const PAGES = [
  "home.png",
  "health.png",
  "prompts.png",
  "planner.png",
  "activity.png",
  "levels.png",
];

const FPS = 15;
export const PAGE_DURATION = Math.round(3.5 * FPS); // 52 frames (3.5s per page)
export const TRANSITION_DURATION = Math.round(0.4 * FPS); // 6 frames (0.4s transition)

// Output dimensions
const W = 960;
const H = 540;

// Sidebar is on the right ~200px; content is the left 760px
const SIDEBAR_WIDTH = 200;
const CONTENT_WIDTH = W - SIDEBAR_WIDTH;

const SNAPPY_CONFIG = { damping: 20, stiffness: 200 };
const SMOOTH_CONFIG = { damping: 200 };

const RevealElement: React.FC<{
  page: string;
  region: ElementRegion;
}> = ({ page, region }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Spring starts at frame offset by the region's stagger delay
  // Add a global delay of 3 frames so sidebar settles first
  const GLOBAL_DELAY = 3;
  const progress = spring({
    frame: frame - region.delay - GLOBAL_DELAY,
    fps,
    config: SNAPPY_CONFIG,
  });

  const opacity = progress;
  const translateY = interpolate(progress, [0, 1], [25, 0]);
  const scale = interpolate(progress, [0, 1], [0.96, 1]);

  // clipPath inset: top right bottom left
  const clipTop = region.top;
  const clipBottom = H - region.bottom;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        clipPath: `inset(${clipTop}px ${SIDEBAR_WIDTH}px ${clipBottom}px 0px)`,
        opacity,
        transform: `translateY(${translateY}px) scale(${scale})`,
        transformOrigin: "center top",
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

  // Sidebar appears first with a smooth, fast spring
  const sidebarProgress = spring({
    frame,
    fps,
    config: SMOOTH_CONFIG,
  });

  const sidebarOpacity = sidebarProgress;
  const sidebarTranslateX = interpolate(sidebarProgress, [0, 1], [30, 0]);

  const regions = PAGE_REGIONS[page] || [];

  return (
    <AbsoluteFill style={{ backgroundColor: "#09090b" }}>
      {/* Sidebar — clips to right 200px, slides in from right */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          clipPath: `inset(0 0 0 ${CONTENT_WIDTH}px)`,
          opacity: sidebarOpacity,
          transform: `translateX(${sidebarTranslateX}px)`,
          transformOrigin: "right center",
        }}
      >
        <Img
          src={staticFile(`screenshots/${page}`)}
          style={{ width: W, height: H, objectFit: "cover", display: "block" }}
        />
      </div>

      {/* Content regions — each reveals with staggered spring */}
      {regions.map((region, i) => (
        <RevealElement key={i} page={page} region={region} />
      ))}

      {/* Blur overlay sits above all image layers */}
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
                timing={linearTiming({ durationInFrames: TRANSITION_DURATION })}
              />
            )}
          </React.Fragment>
        ))}
      </TransitionSeries>
    </AbsoluteFill>
  );
};
