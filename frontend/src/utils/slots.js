// Appointment slots are local ISO times ("2025-07-10T11:00").
export function describeSlot(iso) {
  const d = new Date(iso);
  return {
    day: d.toLocaleDateString("en-US", { weekday: "short" }),
    date: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    time: d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }),
    hour: d.getHours(),
  };
}

export function slotLabel(iso) {
  const s = describeSlot(iso);
  return `${s.day}, ${s.date} · ${s.time}`;
}
