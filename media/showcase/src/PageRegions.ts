export interface ElementRegion {
  top: number;
  bottom: number;
  delay: number; // stagger delay in frames
}

export const PAGE_REGIONS: Record<string, ElementRegion[]> = {
  "home.png": [
    { top: 30, bottom: 75, delay: 0 },    // Header "סקירה כללית"
    { top: 75, bottom: 165, delay: 4 },    // Stat cards row
    { top: 165, bottom: 370, delay: 8 },   // Leaderboard + Events panels
    { top: 370, bottom: 540, delay: 12 },  // Charts (activity + types)
  ],
  "health.png": [
    { top: 0, bottom: 90, delay: 0 },      // Header + action buttons
    { top: 90, bottom: 190, delay: 4 },     // Status cards row
    { top: 190, bottom: 380, delay: 8 },    // Feature status + Schedule panels
    { top: 380, bottom: 540, delay: 12 },   // Activity log + Bot logs
  ],
  "prompts.png": [
    { top: 0, bottom: 60, delay: 0 },      // Header
    { top: 60, bottom: 280, delay: 4 },     // Daily timeline
    { top: 280, bottom: 320, delay: 8 },    // Tab bar
    { top: 320, bottom: 540, delay: 12 },   // Schedule settings
  ],
  "planner.png": [
    { top: 0, bottom: 60, delay: 0 },      // Header
    { top: 60, bottom: 200, delay: 4 },     // Today's pending section
    { top: 200, bottom: 540, delay: 8 },    // Weekly calendar
  ],
  "activity.png": [
    { top: 0, bottom: 80, delay: 0 },      // Header
    { top: 80, bottom: 540, delay: 4 },     // Activity table
  ],
  "levels.png": [
    { top: 0, bottom: 60, delay: 0 },      // Header + toggle
    { top: 60, bottom: 190, delay: 4 },     // Level-up notifications
    { top: 190, bottom: 540, delay: 8 },    // Score + table
  ],
};
