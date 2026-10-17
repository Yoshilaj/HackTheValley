import { useEffect, useState } from "react";

// useState that is kept in localStorage under `key` (as JSON). Falls back to
// `initial` when nothing is saved or storage is blocked.
export default function useStoredState(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? initial : JSON.parse(raw);
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage unavailable: the value just won't persist */
    }
  }, [key, value]);

  return [value, setValue];
}
