// Placeholder data for the dashboard UI. Swap for real API data later.

export const patient = { name: "Micky", initials: "ML" };

export const navItems = ["Overview", "Vitals", "Appointments", "Medications"];

export const wellness = {
  score: "86",
  note: "+4 points since last month. Keep it up!",
};

export const stats = [
  { id: "heart", label: "Heart rate", amount: "72", unit: "bpm", note: "Resting average this week", badge: "Normal", trend: "normal" },
  { id: "bp", label: "Blood pressure", amount: "128/84", unit: "mmHg", note: "Slightly above your goal", badge: "Watch", trend: "watch" },
];

export const activity = [
  { day: "Mon", value: "4.1K", height: 76 },
  { day: "Tue", value: "2.2K", height: 100 },
  { day: "Wed", value: "3.5K", height: 62 },
  { day: "Thu", value: "1.2K", height: 52 },
  { day: "Fri", value: "2.4K", height: 92, active: true, tip: { amount: "8,420 steps", change: "+16%" } },
];

export const focusAreas = {
  total: "86%",
  items: [
    { name: "Sleep", pct: 30, color: "#4f46e5" },
    { name: "Activity", pct: 20, color: "#0e7490" },
    { name: "Nutrition", pct: 20, color: "#15803d" },
    { name: "Hydration", pct: 10, color: "#0284c7" },
    { name: "Mindfulness", pct: 10, color: "#a21caf" },
    { name: "Recovery", pct: 10, color: "#b45309" },
  ],
};

export const appointments = {
  count: 4,
  rows: [
    { id: 1, name: "Dr. Amira Patel", status: "Confirmed", date: "Jul 04, 9:40 AM", place: "Cardiology", amount: "30 min", color: "#cffafe", ink: "#155e75", glyph: "AP" },
    { id: 2, name: "Dr. Lucas Chen", status: "Confirmed", date: "Jul 11, 2:15 PM", place: "Family medicine", amount: "20 min", color: "#dcfce7", ink: "#166534", glyph: "LC" },
    { id: 3, name: "Lab: blood panel", status: "Pending", date: "Jul 18, 8:00 AM", place: "Westside Lab", amount: "15 min", color: "#fef3c7", ink: "#92400e", glyph: "LB" },
  ],
};

export const coverage = {
  count: 3,
  front: { number: "4156 6727 1439 6902", holder: "Micky Larson", expiry: "07/25" },
  stack: [
    { id: "a", brand: "", last4: "326R" },
    { id: "b", brand: "Vision", last4: "4352" },
    { id: "c", brand: "Dental", last4: "6902" },
  ],
};

export const medications = {
  count: 4,
  icons: [
    { id: "lisinopril", bg: "#cffafe", ink: "#155e75", glyph: "L" },
    { id: "metformin", bg: "#dcfce7", ink: "#166534", glyph: "M" },
    { id: "vitd", bg: "#fef3c7", ink: "#92400e", glyph: "D" },
    { id: "omega", bg: "#e0e7ff", ink: "#3730a3", glyph: "O" },
  ],
  list: [
    { id: 1, name: "Lisinopril", next: "Next dose 8:00 AM", dose: "10", unit: "mg", glyph: "L" },
    { id: 2, name: "Metformin", next: "Next dose 1:00 PM", dose: "500", unit: "mg", glyph: "M" },
    { id: 3, name: "Vitamin D", next: "Next dose 8:00 PM", dose: "2000", unit: "IU", glyph: "D" },
    { id: 4, name: "Omega-3", next: "Next dose 8:00 PM", dose: "1000", unit: "mg", glyph: "O" },
  ],
};
