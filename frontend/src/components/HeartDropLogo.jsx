import LogoMark from "./LogoMark.jsx";

// The logo with its looping heart drop: the heart falls softly into the still
// hand and a puff of little hearts bursts out as it lands. Styles in
// landing.css; it animates while it has the "playing" class or sits inside a
// scroll-revealed block (.revealed).
//
// Where each little heart flies, as [x, y] in its own widths (up and out to
// both sides) and a tilt in degrees.
const BURST = [
  [-2.4, -1.4, -20],
  [-1, -2.6, -8],
  [1, -2.6, 8],
  [2.4, -1.4, 20],
  [-3, 0.4, -30],
  [3, 0.4, 30],
];

export default function HeartDropLogo({ className = "" }) {
  return (
    <span className={`heart-drop ${className}`} aria-hidden="true">
      <LogoMark />
      {BURST.map(([x, y, r]) => (
        <i key={`${x},${y}`} className="burst" style={{ "--x": x, "--y": y, "--r": `${r}deg` }}>
          <svg viewBox="0 0 24 24">
            <path d="M12 21s-8-5.2-8-11a4.5 4.5 0 018-2.8A4.5 4.5 0 0120 10c0 5.8-8 11-8 11z" />
          </svg>
        </i>
      ))}
    </span>
  );
}
