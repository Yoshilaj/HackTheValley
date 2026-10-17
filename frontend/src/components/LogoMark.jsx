// Heart-in-hand brand mark, shared by the top bar and the history sidebar.
export default function LogoMark() {
  return (
    <span className="logo-mark" aria-hidden="true">
      <svg viewBox="0 0 48 48">
        <path
          className="lm-heart"
          d="M29 31s-12-7.2-12-15.4A6.5 6.5 0 0 1 29 12a6.5 6.5 0 0 1 12 3.6C41 23.8 29 31 29 31z"
        />
        <path className="lm-cross" d="M26.6 14.6h4.8v3.6H35v4.8h-3.6v3.6h-4.8V23H23v-4.8h3.6z" />
        <path
          className="lm-hand"
          d="M15 31.5h8.5c2.4 0 4.2 1.6 4.2 1.6l9.8-2.7c2.4-.6 3.6 2.8 1.4 3.9L28.2 40c-1.6.8-3.3 1.2-5 1.2H15z"
        />
        <rect className="lm-sleeve" x="12" y="30.5" width="3.5" height="11.5" rx="0.5" />
        <rect className="lm-cuff" x="5" y="29" width="7" height="14.5" rx="1" />
      </svg>
    </span>
  );
}
