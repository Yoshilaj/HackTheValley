// Shared bits for the assessment tasks (GET /api/tasks/history items).
export const TASK_NAMES = {
  finger_tap: "Finger tapping",
  sustained_vowel: "Sustained vowel",
  story_recall: "Story recall",
  word_recall: "Word recall",
  free_speech: "Voice message",
};

// A task's headline number, as shown in lists ("142 / 138 taps", "26 dB", "61 / 100").
export function taskSummary(t) {
  const s = t.summary ?? {};
  if (t.task_type === "finger_tap" && (s.left_tap_count != null || s.right_tap_count != null))
    return `${Math.round(s.left_tap_count ?? 0)} / ${Math.round(s.right_tap_count ?? 0)} taps`;
  if (t.task_type === "sustained_vowel" && typeof s.snr_db === "number") return `${Math.round(s.snr_db)} dB`;
  if (t.task_type === "story_recall" && typeof (s.score ?? t.score) === "number") return `${Math.round(s.score ?? t.score)} / 100`;
  return null;
}

// The most recent completed, valid task of a type (items are newest first).
export const lastTask = (items, type) => (items ?? []).find((t) => t.task_type === type && t.valid && t.status !== "in_progress") ?? null;
