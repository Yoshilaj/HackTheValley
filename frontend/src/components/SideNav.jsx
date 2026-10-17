import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { navItems } from "../data/placeholder.js";

// Main navigation, shown in the sidebar. Rows can be reordered by dragging
// (mouse), long-press then drag (touch), or Alt+Up/Down (keyboard). The order
// is remembered. Collapsed, the sidebar shows the icons only.
const ORDER_KEY = "caresync-nav-order";
const SLOP = 6; // px of movement before a mouse press becomes a drag
const HOLD_MS = 250; // touch must hold this long so swipes still scroll

const ICONS = {
  "/": <path d="M4 11l8-7 8 7v8a1 1 0 01-1 1h-4v-6h-6v6H5a1 1 0 01-1-1z" />,
  "/chat": (
    <>
      <path d="M20 12a8 8 0 01-11.6 7.1L4 20l1-4.1A8 8 0 1120 12z" />
      <path d="M12 9.2c-.8-1.2-3-1-3 .9 0 1.6 3 3.4 3 3.4s3-1.8 3-3.4c0-1.9-2.2-2.1-3-.9z" />
    </>
  ),
  "/appointments": (
    <>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
    </>
  ),
  "/medications": (
    <>
      <rect x="3" y="9" width="18" height="6" rx="3" transform="rotate(-45 12 12)" />
      <path d="M9.5 9.5l5 5" />
    </>
  ),
};

function useNavOrder() {
  const [order, setOrder] = useState(() => {
    const known = navItems.map((n) => n.to);
    let saved = [];
    try {
      saved = JSON.parse(localStorage.getItem(ORDER_KEY)) ?? [];
    } catch {
      /* storage unavailable */
    }
    // Drop items that no longer exist and append any new ones at the end.
    const kept = Array.isArray(saved) ? [...new Set(saved)].filter((to) => known.includes(to)) : [];
    return [...kept, ...known.filter((to) => !kept.includes(to))];
  });

  const save = (next) => {
    setOrder(next);
    try {
      localStorage.setItem(ORDER_KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
  };

  return [order.map((to) => navItems.find((n) => n.to === to)), save];
}

export default function SideNav({ collapsed }) {
  const [items, setOrder] = useNavOrder();
  const [drag, setDrag] = useState(null); // { from, to, dy, shift }
  const [settling, setSettling] = useState(false);
  const [announce, setAnnounce] = useState("");
  const navRef = useRef(null);
  const press = useRef(null);
  const suppressClick = useRef(false);

  // Once a touch drag starts, stop the browser from scrolling instead.
  useEffect(() => {
    const nav = navRef.current;
    const block = (e) => press.current?.active && e.preventDefault();
    nav.addEventListener("touchmove", block, { passive: false });
    return () => nav.removeEventListener("touchmove", block);
  }, []);

  const move = (from, to) => {
    if (from === to) return;
    const next = items.map((i) => i.to);
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setOrder(next);
    setAnnounce(`${items[from].label} moved to position ${to + 1} of ${items.length}`);
  };

  const end = () => {
    const p = press.current;
    if (!p) return;
    clearTimeout(p.timer);
    p.cleanup();
    press.current = null;
    // Skip the transition for the frame where rows jump into their new slots.
    setSettling(true);
    setDrag(null);
    requestAnimationFrame(() => setSettling(false));
  };

  const begin = () => {
    const p = press.current;
    const nav = navRef.current;
    const originY = nav.getBoundingClientRect().top;
    p.rects = [...nav.querySelectorAll("a")].map((a) => {
      const top = originY + a.offsetTop;
      return { top, height: a.offsetHeight, bottom: top + a.offsetHeight };
    });
    p.active = true;
    p.to = p.index;
    const gap = parseFloat(getComputedStyle(nav).rowGap) || 0;
    setDrag({ from: p.index, to: p.index, dy: 0, shift: p.rects[p.index].height + gap });
  };

  const onPointerDown = (e, index) => {
    if (e.button !== 0) return;

    const onMove = (ev) => {
      const p = press.current;
      if (!p || ev.pointerId !== p.id) return;
      const dy = ev.clientY - p.y;
      if (!p.active) {
        if (Math.hypot(ev.clientX - p.x, dy) < SLOP) return;
        if (p.touch) return end(); // moved before the hold finished: it's a scroll
        begin();
      }
      const r = p.rects;
      const i = p.index;
      const clamped = Math.max(r[0].top - r[i].top, Math.min(r[r.length - 1].bottom - r[i].bottom, dy));
      const center = r[i].top + r[i].height / 2 + clamped;
      p.to = r.filter((b, j) => j !== i && b.top + b.height / 2 < center).length;
      setDrag((d) => ({ ...d, to: p.to, dy: clamped }));
    };

    const onUp = (ev) => {
      const p = press.current;
      if (!p || ev.pointerId !== p.id) return;
      if (p.active) {
        // The link's click follows pointerup; swallow it so a drop doesn't navigate.
        suppressClick.current = true;
        setTimeout(() => (suppressClick.current = false), 0);
        move(p.index, p.to);
      }
      end();
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", end);

    press.current = {
      id: e.pointerId,
      index,
      x: e.clientX,
      y: e.clientY,
      active: false,
      touch: e.pointerType !== "mouse",
      cleanup: () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", end);
      },
    };
    if (press.current.touch) press.current.timer = setTimeout(begin, HOLD_MS);
  };

  const onKeyDown = (e, index) => {
    if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    const to = index + (e.key === "ArrowUp" ? -1 : 1);
    if (to >= 0 && to < items.length) move(index, to);
  };

  const transformFor = (j) => {
    if (!drag) return undefined;
    const { from, to, dy, shift } = drag;
    if (j === from) return `translateY(${dy}px) scale(1.03)`;
    if (from < to && j > from && j <= to) return `translateY(${-shift}px)`;
    if (to < from && j >= to && j < from) return `translateY(${shift}px)`;
    return undefined;
  };

  const navClass = ["side-nav", drag && "reordering", settling && "settling"].filter(Boolean).join(" ");

  return (
    <nav ref={navRef} className={navClass} aria-label="Main" onContextMenu={(e) => press.current && e.preventDefault()}>
      {items.map((item, j) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          draggable={false}
          title={collapsed ? item.label : undefined}
          aria-label={collapsed ? item.label : undefined}
          aria-describedby="side-nav-hint"
          className={({ isActive }) =>
            ["history-row", "side-nav-item", isActive && "active", drag?.from === j && "dragging"].filter(Boolean).join(" ")
          }
          style={{ transform: transformFor(j) }}
          onPointerDown={(e) => onPointerDown(e, j)}
          onKeyDown={(e) => onKeyDown(e, j)}
          onClick={(e) => suppressClick.current && e.preventDefault()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {ICONS[item.to] ?? <circle cx="12" cy="12" r="7" />}
          </svg>
          <span className="history-label">{item.label}</span>
        </NavLink>
      ))}
      <span id="side-nav-hint" className="sr-only">
        Drag to reorder, or press Alt with the up or down arrow.
      </span>
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
    </nav>
  );
}
