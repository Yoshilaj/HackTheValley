// "Add to calendar": builds a standard .ics file (opens in Apple Calendar,
// Google Calendar, Outlook) for one booked appointment and downloads it.
// Times are written as local "floating" times, matching how slots are stored.

const pad = (n) => String(n).padStart(2, "0");
const localStamp = (d) =>
  `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
const utcStamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
const escapeText = (s) => String(s).replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/([,;])/g, "\\$1");

// Lines longer than 75 characters must be folded onto continuation lines.
const fold = (line) => line.match(/.{1,73}/g).join("\r\n ");

export function appointmentIcs(a) {
  const start = new Date(a.slot);
  const minutes = parseInt(a.length, 10) || 30;
  const end = new Date(start.getTime() + minutes * 60 * 1000);
  const where = [a.place, a.address].filter(Boolean).join(", ");

  return [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//CareSync//Appointments//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${a.id.replace(/[^\w.-]/g, "-")}@caresync`,
    `DTSTAMP:${utcStamp(new Date())}`,
    `DTSTART:${localStamp(start)}`,
    `DTEND:${localStamp(end)}`,
    `SUMMARY:${escapeText(a.specialty ? `${a.name} (${a.specialty})` : a.name)}`,
    where && `LOCATION:${escapeText(where)}`,
    a.note && `DESCRIPTION:${escapeText(a.note)}`,
    // Reminder an hour before
    "BEGIN:VALARM",
    "TRIGGER:-PT1H",
    "ACTION:DISPLAY",
    `DESCRIPTION:${escapeText(`Appointment with ${a.name} in 1 hour`)}`,
    "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
  ]
    .filter(Boolean)
    .map(fold)
    .join("\r\n");
}

export function downloadIcs(a) {
  const blob = new Blob([appointmentIcs(a)], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${a.name.replace(/[^\w]+/g, "-").replace(/^-|-$/g, "").toLowerCase()}-appointment.ics`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
