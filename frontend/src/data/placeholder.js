// Placeholder data for the dashboard UI. Swap for real API data later.

export const patient = {
  name: "Micky",
  fullName: "Micky Larson",
  initials: "ML",
  dateOfBirth: "1974-03-03",
  healthCardLast4: "6902",
  allergies: ["Penicillin"],
  conditions: ["High blood pressure (stage 1)", "Prediabetes"],
  emergencyContact: { name: "Jordan Larson", relation: "Partner", phone: "(416) 555-0187" },
};

// The user's own doctors (shown in the health profile and "Contact care team").
// Phone numbers use the 555-01xx range reserved for fiction.
export const careTeam = [
  { id: "chen", name: "Dr. Lucas Chen", role: "Family doctor", clinic: "Maple Family Practice", phone: "(416) 555-0142", email: "office@maplefamily.example", primary: true },
  { id: "patel", name: "Dr. Amira Patel", role: "Cardiologist", clinic: "Riverside Heart Clinic", phone: "(416) 555-0178", email: "cardiology@riversideheart.example" },
  { id: "nair", name: "Dr. Priya Nair", role: "Endocrinologist", clinic: "Lakeview Diabetes Centre", phone: "(416) 555-0133", email: "clinic@lakeviewdiabetes.example" },
];

// Pharmacies a refill request can go to.
export const pharmacies = [
  { id: "maple", name: "Maple Pharmacy", address: "410 Maple St", hours: "Open until 9 PM" },
  { id: "riverside", name: "Riverside Drugs", address: "12 Riverside Dr", hours: "Open 24 hours" },
];

export const navItems = [
  { to: "/", label: "Overview" },
  { to: "/chat", label: "CareSync" },
  { to: "/checkin", label: "Check-in" },
  { to: "/vitals", label: "Vitals" },
  { to: "/assessments", label: "Assessments" },
  { to: "/appointments", label: "Appointments" },
  { to: "/medications", label: "Medications" },
];

export const notifications = [
  { id: 2, kind: "medication", title: "Omega-3 is out of refills", body: "Ask Dr. Patel for a new prescription.", time: "3h ago", to: "/medications", unread: true },
];

// Overall risk index 0-100, higher = more risk. It combines the condition
// estimates below, weighted by how serious each condition is. The backend
// derives everything only from the user's chat history and doctors'
// diagnoses, checked against clinical guidelines. Change score here to
// preview the card colours.
export const riskAssessment = {
  score: 14,
  previous: { score: 16 }, // last month's assessment, for the trend line
  updatedAt: Date.now() - 3 * 60 * 60 * 1000, // placeholder: three hours ago
  basis: { diagnoses: 3 }, // chats are counted live from the chat history
  note: "Based on your CareSync chats and doctors' diagnoses.",
  // Per-condition estimates, shown as bars beside the overall index:
  //   risk     - % chance of the outcome within `horizon` years
  //   typical  - the same for a healthy person of the user's age
  //   previous - last month's risk, for the trend
  //   bands    - [moderate from %, high from %] per the method's guidance
  //   method   - the published risk tool the estimate is modelled on
  // `details` explains the estimate from three kinds of evidence: the user's
  // chats, past doctors' diagnoses, and medical sources retrieved by the
  // backend (RAG). Placeholder values; the backend will fill these in.
  conditions: [
    {
      id: "alzheimers", name: "Alzheimer's disease", outcome: "develop dementia, most often Alzheimer's disease",
      risk: 1.9, horizon: 20, typical: 1.0, previous: 1.9, bands: [4, 7], method: "CAIDE-based estimate",
      details: {
        summary: "Your risk is low. You haven't mentioned any memory or thinking problems, and your doctors haven't noted any. Blood sugar slightly above target and short sleep raise it a little.",
        chats: [
          { quote: "I've only been sleeping about 5 hours a night lately.", date: "Jul 1", why: "Ongoing short sleep is linked to poorer brain health over time." },
        ],
        diagnoses: [
          { finding: "Routine check-up. No memory or thinking concerns noted.", doctor: "Dr. Lucas Chen", specialty: "Family medicine", date: "Mar 14, 2025" },
          { finding: "Blood sugar slightly above target. Metformin continued.", doctor: "Dr. Priya Nair", specialty: "Endocrinology", date: "May 22, 2025" },
        ],
        sources: [
          { name: "World Health Organization", title: "Dementia: key facts", excerpt: "Risk factors include physical inactivity, diabetes, high blood pressure, smoking and social isolation.", url: "https://www.who.int" },
          { name: "Alzheimer's Association", title: "Brain health and sleep", excerpt: "Regular, good-quality sleep supports memory and overall brain health.", url: "https://www.alz.org" },
        ],
        tips: ["Aim for 7 to 9 hours of sleep.", "Keep your blood sugar on target with your current plan."],
      },
    },
    {
      id: "cardio", name: "Stroke / heart disease", outcome: "have a heart attack or stroke",
      risk: 11.4, horizon: 10, typical: 5.1, previous: 10.8, bands: [10, 20], method: "QRISK3-based estimate",
      details: {
        summary: "Your risk is moderate, mainly because your blood pressure has stayed above your goal of 120/80. It is being treated, and taking Lisinopril every day keeps it from rising.",
        chats: [
          { quote: "My blood pressure was 131/86 this morning, is that bad?", date: "Jul 2", why: "A reading in the stage 1 high blood pressure range." },
          { quote: "I get headaches some afternoons.", date: "Jun 28", why: "Can go with raised blood pressure; worth mentioning to your doctor." },
        ],
        diagnoses: [
          { finding: "Stage 1 high blood pressure. Started Lisinopril 10 mg daily.", doctor: "Dr. Amira Patel", specialty: "Cardiology", date: "Apr 10, 2025" },
          { finding: "Blood sugar slightly above target, which adds to heart risk.", doctor: "Dr. Priya Nair", specialty: "Endocrinology", date: "May 22, 2025" },
        ],
        sources: [
          { name: "American Heart Association", title: "Understanding blood pressure readings", excerpt: "Readings of 130-139 / 80-89 mmHg are classed as stage 1 hypertension.", url: "https://www.heart.org" },
          { name: "NICE", title: "Hypertension in adults", excerpt: "Lowering blood pressure reduces the risk of stroke and heart attack.", url: "https://www.nice.org.uk" },
        ],
        tips: ["Take Lisinopril at the same time every day.", "Cut back on salty foods.", "Aim for 150 minutes of activity a week."],
      },
    },
    {
      id: "parkinsons", name: "Parkinson's disease", outcome: "develop Parkinson's disease",
      risk: 0.4, horizon: 10, typical: 0.4, previous: 0.4, bands: [1, 2], method: "PREDICT-PD-based estimate",
      details: {
        summary: "Your risk is very low. Nothing in your chats or your doctors' notes points to the early signs of Parkinson's.",
        chats: [],
        diagnoses: [
          { finding: "No tremor, stiffness or balance problems on exam.", doctor: "Dr. Lucas Chen", specialty: "Family medicine", date: "Mar 14, 2025" },
        ],
        sources: [
          { name: "Parkinson's Foundation", title: "Early signs of Parkinson's", excerpt: "Early signs can include a tremor, smaller handwriting, loss of smell, trouble sleeping and stiffness.", url: "https://www.parkinson.org" },
        ],
        tips: ["Tell CareSync or your doctor if you notice a tremor or stiffness."],
      },
    },
  ],
  // What CareSync thinks is driving the score. At 85+ it holds a visit with a
  // matching specialist (see CareAlertContext).
  suspected: {
    condition: "High blood pressure (hypertension)",
    specialty: "Cardiology",
    why: "Your blood pressure was above your goal in 5 of the last 7 readings, and you mentioned dizziness and headaches in chat.",
    bring: "Photo ID, health card, your blood pressure log and a list of current medications",
  },
  details: {
    about: "Your overall risk index combines the condition estimates beside it, weighted by how serious each condition is. It runs from 0 to 100, and a higher number means more risk. CareSync works it out only from your chats and your doctors' diagnoses, checked against clinical guidelines.",
    tips: ["Tell CareSync about new symptoms so your assessment stays current.", "This is not a diagnosis. Talk to your doctor about any concerns."],
  },
};

// Placeholder charts under the overall risk card, until the backend sends them.
// trend: monthly overall scores, oldest first (the last bar shows the live score).
// factors: what makes up the score, as shares adding to 100.
export const riskCharts = {
  trend: [
    { month: "May", score: 22 },
    { month: "Jun", score: 20 },
    { month: "Jul", score: 19 },
    { month: "Aug", score: 18 },
    { month: "Sep", score: 16 },
    { month: "Oct", score: 14 },
  ],
  factors: [
    { name: "Blood pressure", share: 40 },
    { name: "Cholesterol", share: 25 },
    { name: "Age", share: 20 },
    { name: "Activity", share: 15 },
  ],
};

// Specialists suggested from chat history. The backend will search for real
// doctors and clinics near the user; these are placeholders.
export const recommendations = [
  { id: "r1", name: "Dr. Hannah Okafor", specialty: "Cardiology", clinic: "Riverside Heart Clinic", address: "220 Riverside Dr, Suite 4", distance: "1.2 km", distanceKm: 1.2, nextSlot: "Thu, Jul 10 · 11:00 AM", reason: "You've mentioned blood pressure readings above your goal.", glyph: "HO",
    slots: ["2025-07-10T11:00", "2025-07-11T08:30", "2025-07-15T16:30", "2025-07-19T10:00"] },
  { id: "r2", name: "Dr. Priya Nair", specialty: "Endocrinology", clinic: "Lakeview Diabetes Centre", address: "88 Lakeview Ave, 2nd floor", distance: "3.4 km", distanceKm: 3.4, nextSlot: "Mon, Jul 14 · 9:30 AM", reason: "Your recent blood sugar questions suggest a follow-up.", glyph: "PN",
    slots: ["2025-07-14T09:30", "2025-07-16T13:00", "2025-07-17T18:00"] },
  { id: "r3", name: "Northside Sleep Clinic", specialty: "Sleep medicine", clinic: "Northside Medical Building", address: "1450 North St, Building B", distance: "4.8 km", distanceKm: 4.8, nextSlot: "Wed, Jul 16 · 2:15 PM", reason: "You told CareSync you haven't been sleeping well.", glyph: "SC",
    slots: ["2025-07-16T14:15", "2025-07-18T19:00", "2025-07-21T09:00"] },
];

export const stats = [
  {
    id: "heart", label: "Heart rate", amount: "72", unit: "bpm", note: "Resting average this week", badge: "Normal", trend: "normal",
    details: {
      about: "Resting heart rate is how many times your heart beats per minute while you're relaxed. A lower resting rate usually means a more efficient heart.",
      rows: [["Healthy range", "60-100 bpm"], ["Lowest this week", "69 bpm"], ["Highest this week", "75 bpm"], ["Last reading", "Jul 03, 7:45 AM"]],
      tips: ["Measure at the same time each morning, before coffee.", "Tell your care team if it stays above 100 at rest."],
    },
  },
  {
    id: "bp", label: "Blood pressure", amount: "128/84", unit: "mmHg", note: "Slightly above your goal", badge: "Watch", trend: "watch",
    details: {
      about: "The top number (systolic) is the pressure when your heart beats; the bottom (diastolic) is the pressure between beats. Yours is a little above your goal of 120/80.",
      rows: [["Your goal", "Under 120/80 mmHg"], ["7-day average", "127/83 mmHg"], ["Medication", "Lisinopril 10 mg daily"], ["Last reading", "Jul 03, 7:45 AM"]],
      tips: ["Sit quietly for 5 minutes before measuring.", "Cut back on salty foods.", "Bring your log to Dr. Patel on Jul 04."],
    },
  },
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
  { id: "hr", label: "Heart rate", value: "72", unit: "bpm", status: "normal", range: "60-100 bpm", band: [60, 100], trend: [70, 74, 71, 69, 73, 75, 72],
    about: "How many times your heart beats each minute while you rest. A lower resting rate usually means a more efficient heart.",
    tips: ["Measure at the same time each morning, before coffee.", "Tell your doctor if it stays above 100 at rest."] },
  { id: "bp", label: "Blood pressure", value: "128/84", unit: "mmHg", status: "watch", range: "Under 120/80 mmHg", band: [90, 120], trend: [122, 126, 124, 130, 127, 131, 128],
    about: "The top number is the pressure when your heart beats; the bottom is the pressure between beats. Yours has been a little above your goal of 120/80.",
    tips: ["Sit quietly for 5 minutes before measuring, feet flat on the floor.", "Take two readings a minute apart and log the second.", "Cutting back on salt can lower the top number."] },
  { id: "spo2", label: "Blood oxygen", value: "98", unit: "%", status: "normal", range: "95-100 %", band: [95, 100], trend: [97, 98, 98, 97, 99, 98, 98],
    about: "How much oxygen your red blood cells are carrying. Healthy lungs usually keep it at 95% or higher.",
    tips: ["Warm your hands first; cold fingers can give a low reading.", "Remove nail polish from the finger you measure."] },
  { id: "temp", label: "Temperature", value: "36.7", unit: "°C", status: "normal", range: "36.1-37.2 °C", band: [36.1, 37.2], trend: [36.6, 36.8, 36.7, 36.6, 36.9, 36.7, 36.7],
    about: "Your body temperature. 38 °C or higher usually means a fever.",
    tips: ["Wait 15 minutes after eating or drinking before measuring by mouth."] },
  { id: "weight", label: "Weight", value: "68.4", unit: "kg", status: "normal", range: "Goal 67-70 kg", band: [67, 70], trend: [69.1, 68.9, 68.8, 68.7, 68.6, 68.5, 68.4],
    about: "Your weight against the goal range you set with Dr. Chen.",
    tips: ["Weigh yourself at the same time of day, ideally in the morning."] },
  { id: "glucose", label: "Blood glucose", value: "6.2", unit: "mmol/L", status: "watch", range: "4.0-5.6 mmol/L fasting", band: [4, 5.6], trend: [5.6, 5.9, 6.1, 5.8, 6.3, 6.0, 6.2],
    about: "The sugar in your blood after not eating for at least 8 hours. Yours has been slightly above the healthy range, in the prediabetes zone.",
    tips: ["Measure before breakfast for a true fasting reading.", "Take Metformin with meals as prescribed."] },
];

export const readings = [
  { id: 1, date: "Jul 03, 7:45 AM", vital: "Blood pressure", value: "128/84 mmHg", status: "watch" },
  { id: 2, date: "Jul 03, 7:45 AM", vital: "Heart rate", value: "72 bpm", status: "normal" },
  { id: 3, date: "Jul 02, 8:10 PM", vital: "Blood glucose", value: "6.2 mmol/L", status: "watch" },
  { id: 4, date: "Jul 02, 7:50 AM", vital: "Blood oxygen", value: "98 %", status: "normal" },
  { id: 5, date: "Jul 01, 7:40 AM", vital: "Weight", value: "68.5 kg", status: "normal" },
];

// Upcoming starts empty; booked visits are added by AppointmentsContext.
export const appointmentList = {
  upcoming: [],
  past: [
    { id: 4, name: "Dr. Priya Nair", specialty: "Endocrinology", date: "May 22, 2025", time: "10:30 AM", place: "Lakeview Diabetes Centre", length: "25 min", status: "Completed", glyph: "PN", note: "Medication dose reviewed." },
    { id: 5, name: "Dr. Lucas Chen", specialty: "Family medicine", date: "Mar 14, 2025", time: "1:00 PM", place: "Maple Family Practice", length: "20 min", status: "Completed", glyph: "LC", note: "Flu shot given." },
  ],
};

// Doctors, clinics and hospitals for the user to book with, filled in by the
// backend (GET /api/providers, see getProviders in api/client.js for the shape).
// Empty until then; the Appointments page shows an empty state.
export const providers = [];

export const medicationList = [
  { id: 1, name: "Lisinopril", dose: "10 mg", frequency: "Once daily", purpose: "Blood pressure", prescriber: "Dr. Amira Patel", refills: 3, times: ["8:00 AM"], glyph: "L", daysLeft: 41,
    takeWith: "With or without food, at the same time each day.",
    sideEffects: ["Dry cough", "Dizziness, especially when standing up", "Headache"],
    interactions: ["Potassium supplements or salt substitutes", "Anti-inflammatory painkillers such as ibuprofen"] },
  { id: 2, name: "Metformin", dose: "500 mg", frequency: "Twice daily with meals", purpose: "Blood sugar", prescriber: "Dr. Priya Nair", refills: 1, times: ["8:00 AM", "6:00 PM"], glyph: "M", daysLeft: 9,
    takeWith: "With breakfast and dinner, to avoid an upset stomach.",
    sideEffects: ["Upset stomach or diarrhea", "Metallic taste", "Less appetite"],
    interactions: ["Heavy drinking", "Contrast dye for some scans (stop beforehand if told to)"] },
  { id: 3, name: "Vitamin D", dose: "2000 IU", frequency: "Once daily", purpose: "Bone health", prescriber: "Dr. Lucas Chen", refills: 5, times: ["8:00 PM"], glyph: "D", daysLeft: 63,
    takeWith: "With a meal that has some fat, which helps it absorb.",
    sideEffects: ["Rare at this dose"],
    interactions: ["Other supplements that contain vitamin D or calcium"] },
  { id: 4, name: "Omega-3", dose: "1000 mg", frequency: "Once daily", purpose: "Heart health", prescriber: "Dr. Amira Patel", refills: 0, times: ["8:00 PM"], glyph: "O", daysLeft: 4,
    takeWith: "With food, to reduce fishy aftertaste.",
    sideEffects: ["Fishy aftertaste", "Mild upset stomach"],
    interactions: ["Blood thinners such as warfarin (can raise bleeding risk)"] },
];
