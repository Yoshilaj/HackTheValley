// How each vital is entered on "Log a reading", judged against the healthy
// range shown on its tile ("normal" or "watch"), and explained in its sheet.
// The min/max limits catch typos; they are not medical thresholds.
// `metric` is the backend metric type the reading is saved as (POST /api/metrics);
// blood pressure has its own endpoint (POST /api/metrics/bp).
export const VITAL_INPUTS = {
  bp: {
    label: "Blood pressure",
    unit: "mmHg",
    range: "Under 120/80 mmHg",
    band: [90, 120],
    about:
      "The top number is the pressure when your heart beats; the bottom is the pressure between beats. Home readings are taken in a morning and an evening session.",
    tips: [
      "Sit quietly for 5 minutes before measuring, feet flat on the floor.",
      "Take two readings a minute apart and log the second.",
      "Cutting back on salt can lower the top number.",
    ],
    fields: [
      { key: "sys", label: "Systolic (top number)", min: 60, max: 260 },
      { key: "dia", label: "Diastolic (bottom number)", min: 30, max: 160 },
      // The cuff's pulse reading; the backend stores it with the reading.
      { key: "pulse", label: "Pulse", min: 25, max: 250, unit: "bpm", optional: true },
    ],
    check: ({ sys, dia }) => (sys < 120 && dia < 80 ? "normal" : "watch"),
    text: ({ sys, dia }) => `${sys}/${dia}`,
    trend: ({ sys }) => sys, // the tile's sparkline follows the top number
  },
  hr: {
    label: "Heart rate",
    unit: "bpm",
    metric: "resting_hr",
    range: "60-100 bpm",
    band: [60, 100],
    about: "How many times your heart beats each minute while you rest. A lower resting rate usually means a more efficient heart.",
    tips: ["Measure at the same time each morning, before coffee.", "Tell your doctor if it stays above 100 at rest."],
    fields: [{ key: "value", label: "Resting heart rate", min: 25, max: 250 }],
    check: ({ value }) => (value >= 60 && value <= 100 ? "normal" : "watch"),
  },
  glucose: {
    label: "Blood glucose",
    unit: "mmol/L",
    metric: "blood_glucose_mmol_l",
    range: "4.0-5.6 mmol/L fasting",
    band: [4, 5.6],
    about: "The sugar in your blood after not eating for at least 8 hours. Above the healthy range is the prediabetes zone.",
    tips: ["Measure before breakfast for a true fasting reading."],
    fields: [{ key: "value", label: "Blood glucose", min: 1, max: 35, decimals: 1 }],
    check: ({ value }) => (value >= 4 && value <= 5.6 ? "normal" : "watch"),
  },
  weight: {
    label: "Weight",
    unit: "kg",
    metric: "weight_kg",
    range: "Steady week to week",
    band: null,
    about: "Your weight over time. A steady trend matters more than any one reading.",
    tips: ["Weigh yourself at the same time of day, ideally in the morning."],
    fields: [{ key: "value", label: "Weight", min: 20, max: 350, decimals: 1 }],
    check: () => "normal",
  },
  temp: {
    label: "Temperature",
    unit: "°C",
    metric: "body_temp_c",
    range: "36.1-37.2 °C",
    band: [36.1, 37.2],
    about: "Your body temperature. 38 °C or higher usually means a fever.",
    tips: ["Wait 15 minutes after eating or drinking before measuring by mouth."],
    fields: [{ key: "value", label: "Temperature", min: 30, max: 45, decimals: 1 }],
    check: ({ value }) => (value >= 36.1 && value <= 37.2 ? "normal" : "watch"),
  },
  spo2: {
    label: "Blood oxygen",
    unit: "%",
    metric: "spo2_pct",
    range: "95-100 %",
    band: [95, 100],
    about: "How much oxygen your red blood cells are carrying. Healthy lungs usually keep it at 95% or higher.",
    tips: ["Warm your hands first; cold fingers can give a low reading.", "Remove nail polish from the finger you measure."],
    fields: [{ key: "value", label: "Oxygen saturation", min: 50, max: 100 }],
    check: ({ value }) => (value >= 95 ? "normal" : "watch"),
  },
};

// Order of the choices in the form and the tiles, most often logged first.
export const VITAL_ORDER = ["bp", "hr", "glucose", "weight", "temp", "spo2"];

// Home blood pressure is read in two sessions a day (spec §2): morning
// (04:00-11:59) and evening (17:00-23:59); anything else is "other". The form
// suggests the session from the time taken; the user can change it.
export const BP_SESSIONS = [
  { id: "morning", label: "Morning", hint: "Within 1 h of waking, before breakfast" },
  { id: "evening", label: "Evening", hint: "Before bed" },
  { id: "other", label: "Other time" },
];

export function bpSessionFor(date) {
  const h = new Date(date).getHours();
  if (h >= 4 && h < 12) return "morning";
  if (h >= 17) return "evening";
  return "other";
}

// "128/84", "6.2", "72": a reading's value as shown on tiles and in lists.
export function vitalText(id, values) {
  const input = VITAL_INPUTS[id];
  if (input.text) return input.text(values);
  const { decimals } = input.fields[0];
  return decimals ? Number(values.value).toFixed(decimals) : String(Math.round(values.value));
}

export const vitalTrendValue = (id, values) => (VITAL_INPUTS[id].trend ?? (({ value }) => value))(values);
