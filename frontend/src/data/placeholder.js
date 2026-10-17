// Placeholder data for the dashboard UI. Swap for real API data later.

export const patient = { name: "Micky", initials: "ML" };

export const navItems = [
  { to: "/", label: "Overview" },
  { to: "/vitals", label: "Vitals" },
  { to: "/appointments", label: "Appointments" },
  { to: "/medications", label: "Medications" },
  { to: "/chat", label: "Assistant" },
];

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

// ---- Detail pages ----

export const vitals = [
  { id: "hr", label: "Heart rate", value: "72", unit: "bpm", status: "normal", range: "60-100 bpm", trend: [70, 74, 71, 69, 73, 75, 72] },
  { id: "bp", label: "Blood pressure", value: "128/84", unit: "mmHg", status: "watch", range: "Under 120/80 mmHg", trend: [122, 126, 124, 130, 127, 131, 128] },
  { id: "spo2", label: "Blood oxygen", value: "98", unit: "%", status: "normal", range: "95-100 %", trend: [97, 98, 98, 97, 99, 98, 98] },
  { id: "temp", label: "Temperature", value: "36.7", unit: "°C", status: "normal", range: "36.1-37.2 °C", trend: [36.6, 36.8, 36.7, 36.6, 36.9, 36.7, 36.7] },
  { id: "weight", label: "Weight", value: "68.4", unit: "kg", status: "normal", range: "Goal 67-70 kg", trend: [69.1, 68.9, 68.8, 68.7, 68.6, 68.5, 68.4] },
  { id: "glucose", label: "Blood glucose", value: "6.2", unit: "mmol/L", status: "watch", range: "4.0-5.6 mmol/L fasting", trend: [5.6, 5.9, 6.1, 5.8, 6.3, 6.0, 6.2] },
];

export const readings = [
  { id: 1, date: "Jul 03, 7:45 AM", vital: "Blood pressure", value: "128/84 mmHg", status: "watch" },
  { id: 2, date: "Jul 03, 7:45 AM", vital: "Heart rate", value: "72 bpm", status: "normal" },
  { id: 3, date: "Jul 02, 8:10 PM", vital: "Blood glucose", value: "6.2 mmol/L", status: "watch" },
  { id: 4, date: "Jul 02, 7:50 AM", vital: "Blood oxygen", value: "98 %", status: "normal" },
  { id: 5, date: "Jul 01, 7:40 AM", vital: "Weight", value: "68.5 kg", status: "normal" },
];

export const appointmentList = {
  upcoming: [
    { id: 1, name: "Dr. Amira Patel", specialty: "Cardiology", date: "Jul 04, 2025", time: "9:40 AM", place: "Riverside Heart Clinic", length: "30 min", status: "Confirmed", glyph: "AP", note: "Bring your blood pressure log." },
    { id: 2, name: "Dr. Lucas Chen", specialty: "Family medicine", date: "Jul 11, 2025", time: "2:15 PM", place: "Maple Family Practice", length: "20 min", status: "Confirmed", glyph: "LC", note: "Annual check-up." },
    { id: 3, name: "Blood panel", specialty: "Laboratory", date: "Jul 18, 2025", time: "8:00 AM", place: "Westside Lab", length: "15 min", status: "Pending", glyph: "LB", note: "Fasting for 8 hours beforehand." },
  ],
  past: [
    { id: 4, name: "Dr. Priya Nair", specialty: "Endocrinology", date: "May 22, 2025", time: "10:30 AM", place: "Lakeview Diabetes Centre", length: "25 min", status: "Completed", glyph: "PN", note: "Medication dose reviewed." },
    { id: 5, name: "Dr. Lucas Chen", specialty: "Family medicine", date: "Mar 14, 2025", time: "1:00 PM", place: "Maple Family Practice", length: "20 min", status: "Completed", glyph: "LC", note: "Flu shot given." },
  ],
};

export const medicationList = [
  { id: 1, name: "Lisinopril", dose: "10 mg", frequency: "Once daily", purpose: "Blood pressure", prescriber: "Dr. Amira Patel", refills: 3, times: ["8:00 AM"], glyph: "L" },
  { id: 2, name: "Metformin", dose: "500 mg", frequency: "Twice daily with meals", purpose: "Blood sugar", prescriber: "Dr. Priya Nair", refills: 1, times: ["8:00 AM", "6:00 PM"], glyph: "M" },
  { id: 3, name: "Vitamin D", dose: "2000 IU", frequency: "Once daily", purpose: "Bone health", prescriber: "Dr. Lucas Chen", refills: 5, times: ["8:00 PM"], glyph: "D" },
  { id: 4, name: "Omega-3", dose: "1000 mg", frequency: "Once daily", purpose: "Heart health", prescriber: "Dr. Amira Patel", refills: 0, times: ["8:00 PM"], glyph: "O" },
];
