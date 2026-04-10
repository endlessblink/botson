import React from "react";

interface BlurRegion {
  x: number;
  y: number;
  width: number;
  height: number;
}

const blurRegions: Record<string, BlurRegion[]> = {
  "home.png": [
    // Stat cards — names area
    { x: 225, y: 105, width: 562, height: 90 },
    // Leaderboard — names column
    { x: 397, y: 225, width: 390, height: 225 },
  ],
  "activity.png": [
    // Log entries with member names
    { x: 37, y: 112, width: 675, height: 428 },
  ],
  "levels.png": [
    // Member name column
    { x: 375, y: 195, width: 420, height: 345 },
  ],
};

export const BlurOverlay: React.FC<{ page: string }> = ({ page }) => {
  const regions = blurRegions[page];
  if (!regions) return null;

  return (
    <>
      {regions.map((region, i) => (
        <div
          key={i}
          style={{
            position: "absolute",
            left: region.x,
            top: region.y,
            width: region.width,
            height: region.height,
            backdropFilter: "blur(15px)",
            WebkitBackdropFilter: "blur(15px)",
            backgroundColor: "rgba(24, 24, 27, 0.3)",
            borderRadius: 6,
            zIndex: 10,
          }}
        />
      ))}
    </>
  );
};
