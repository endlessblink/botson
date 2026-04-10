export const COLORS = {
  bg: "#09090b",
  card: "#0a0a0a",
  border: "#1f1f1f",
  hover: "#111111",
  text: "#fafafa",
  textSecondary: "#a1a1aa",
  textMuted: "#71717a",
  emerald: "#34d399",
  amber: "#fbbf24",
  sky: "#38bdf8",
  indigo: "#818cf8",
  violet: "#a78bfa",
  red: "#f87171",
};

export const LEADERBOARD = [
  { rank: 1, name: "דני כ.", points: 42, level: "⭐ פעיל/ה", emoji: "⭐" },
  { rank: 2, name: "מיכל ר.", points: 38, level: "⭐ פעיל/ה", emoji: "⭐" },
  { rank: 3, name: "אורי ש.", points: 25, level: "🌱 חדש/ה", emoji: "🌱" },
  { rank: 4, name: "נועה ל.", points: 18, level: "🌱 חדש/ה", emoji: "🌱" },
  { rank: 5, name: "יוסי מ.", points: 12, level: "🌱 חדש/ה", emoji: "🌱" },
];

export const STAT_CARDS_HOME = [
  { label: "רמה גבוהה ביותר", value: "דני כ.", sub: "⭐ פעיל/ה", icon: "📊" },
  { label: "רצף הכי ארוך", value: "מיכל ר.", sub: "3 ימים", icon: "🔥" },
  { label: "אירועים קרובים", value: 2, sub: "", icon: "📅" },
  { label: "טופ טריוויה", value: "אורי ש.", sub: "15 נק׳", icon: "🧠" },
];

export const STAT_CARDS_HEALTH = [
  { label: "סטטוס", value: "פעיל", sub: "f9e218f", icon: "status", color: "#34d399" },
  { label: "חברים רשומים", value: 13, sub: "", icon: "👥" },
  { label: "פעולות היום", value: 2, sub: "", icon: "⚡" },
  { label: "ספאם שנחסם היום", value: 0, sub: "", icon: "🛡️" },
];

export const FEATURES = [
  { name: "הודעת בוקר", active: true },
  { name: "הודעת ערב", active: true },
  { name: "שאלות לדיון", active: false },
  { name: "זיהוי ספאם", active: true },
  { name: "ברוכים הבאים", active: false },
  { name: "רמות", active: true },
  { name: "אירועים", active: false },
  { name: "טריוויה", active: false },
  { name: "סיכום שבועי", active: false },
];

export const TIMELINE_ITEMS = [
  { time: "09:00", label: "הודעת בוקר", badge: "בוקר", badgeColor: "#fbbf24", content: "בוקר טוב 🌸 מה 3 הדברים שאתם רוצים לסמן כ-Done היום?" },
  { time: "14:00", label: "אירוע", badge: "אירוע", badgeColor: "#a78bfa", content: "יצירת אירוע Codenames למוצ״ש 18:00 עם RSVP 🎲" },
  { time: "18:00", label: "שאלה לדיון", badge: "דיון", badgeColor: "#38bdf8", content: "אם הייתם יכולים לגור בכל מקום בעולם, איפה הייתם בוחרים ולמה?" },
  { time: "21:00", label: "הודעת ערב", badge: "ערב", badgeColor: "#818cf8", content: "ערב טוב 🌙 איך היה היום? ספרו דבר אחד טוב שקרה" },
];

export const SCHEDULE_TODAY = [
  { time: "09:00", label: "הודעת בוקר", done: true },
  { time: "21:00", label: "הודעת ערב", done: false },
  { time: "??:??", label: "discussion_prompt", done: false },
];

export const CALENDAR_WEEK = [
  { day: "א׳", date: "06", events: [{ color: "#fbbf24" }, { color: "#38bdf8" }] },
  { day: "ב׳", date: "07", events: [{ color: "#fbbf24" }, { color: "#818cf8" }] },
  { day: "ג׳", date: "08", events: [{ color: "#fbbf24" }, { color: "#a78bfa" }, { color: "#38bdf8" }], isToday: true },
  { day: "ד׳", date: "09", events: [{ color: "#fbbf24" }] },
  { day: "ה׳", date: "10", events: [{ color: "#fbbf24" }, { color: "#38bdf8" }] },
  { day: "ו׳", date: "11", events: [{ color: "#a78bfa" }] },
  { day: "ש׳", date: "12", events: [] as { color: string }[] },
];

export const CHART_DATA = [8, 12, 15, 10, 22, 28, 35, 30, 25, 33, 38, 36, 40, 42];

export const PENDING_ITEMS = [
  { text: "לשלוח הודעת בוקר — יום יום", done: true },
  { text: "לתזמן שאלת דיון — סרטים וסדרות", done: false },
  { text: "לפרסם אירוע Codenames — גיימינג", done: false },
];
