// A thin bar showing the microphone's input loudness (0..1) from useRecorder.
export default function LevelMeter({ level }) {
  return (
    <div className="level-meter" aria-hidden="true">
      <i style={{ transform: `scaleX(${Math.max(0.02, level)})` }} />
    </div>
  );
}
