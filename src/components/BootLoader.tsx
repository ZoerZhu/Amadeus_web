import { useCallback, useEffect, useRef, useState } from "react";
import { DivergenceTubeDigit } from "./DivergenceTubeDigit";
import "./BootLoader.css";

const SLOT_COUNT = 8;
const DOT_INDEX = 1;
const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const INITIAL_VALUE = "0.000000";
const DEFAULT_TARGET_VALUE = "1.048596";
const ACCELERATION_DURATION_MS = 3000;
const SETTLE_DURATION_MS = 3000;
const SECOND_MS = 1000;
const STAGE_ONE_HZ = 6;
const STAGE_TWO_HZ = 15;
const STAGE_THREE_HZ = 42;

type Digit = (typeof DIGITS)[number];
type DisplayGlyph = Digit | ".";

type BootLoaderProps = {
  ready: boolean;
  onEnter: () => void;
  targetValue?: string;
};

function randomDigit(): Digit {
  return DIGITS[Math.floor(Math.random() * DIGITS.length)];
}

function parseDisplay(value: string): DisplayGlyph[] {
  const normalized = value.includes(".") ? value : `${value.slice(0, 1) || "0"}.${value.slice(1)}`;
  const [integer = "0", decimal = ""] = normalized.split(".");
  return `${integer.slice(-1) || "0"}.${decimal.padEnd(6, "0").slice(0, 6)}`
    .split("")
    .slice(0, SLOT_COUNT) as DisplayGlyph[];
}

function createScrambleDisplay(): DisplayGlyph[] {
  return Array.from({ length: SLOT_COUNT }, (_, index) => (index === DOT_INDEX ? "." : randomDigit()));
}

function createSettlingDisplay(
  finalDisplay: DisplayGlyph[],
  lockedCount: number,
  currentDisplay: DisplayGlyph[]
) {
  return Array.from({ length: SLOT_COUNT }, (_, index) => {
    if (index === DOT_INDEX) {
      return ".";
    }
    if (index < lockedCount) {
      return finalDisplay[index];
    }

    return currentDisplay[index] === "." ? randomDigit() : currentDisplay[index];
  });
}

function accelerationHz(elapsedMs: number) {
  if (elapsedMs < SECOND_MS) {
    return (elapsedMs / SECOND_MS) * STAGE_ONE_HZ;
  }

  if (elapsedMs < SECOND_MS * 2) {
    return STAGE_ONE_HZ + ((elapsedMs - SECOND_MS) / SECOND_MS) * (STAGE_TWO_HZ - STAGE_ONE_HZ);
  }

  if (elapsedMs < ACCELERATION_DURATION_MS) {
    return STAGE_TWO_HZ + ((elapsedMs - SECOND_MS * 2) / SECOND_MS) * (STAGE_THREE_HZ - STAGE_TWO_HZ);
  }

  return STAGE_THREE_HZ;
}

function settlingHz(startHz: number, elapsedMs: number) {
  const progress = Math.min(elapsedMs / SETTLE_DURATION_MS, 1);
  return startHz * (1 - progress);
}

export function BootLoader({ ready, onEnter, targetValue = DEFAULT_TARGET_VALUE }: BootLoaderProps) {
  const accelerationElapsedMs = useRef(0);
  const settleElapsedMs = useRef(0);
  const settleStartHz = useRef(0);
  const lockedCount = useRef(0);
  const jumpPhase = useRef(0);
  const finalDisplay = useRef<DisplayGlyph[]>(parseDisplay(targetValue));
  const lastTickMs = useRef<number | null>(null);
  const [display, setDisplay] = useState<DisplayGlyph[]>(parseDisplay(INITIAL_VALUE));
  const [settling, setSettling] = useState(false);
  const [settled, setSettled] = useState(false);
  const [progress, setProgress] = useState(8);

  const restartAcceleration = useCallback(() => {
    accelerationElapsedMs.current = 0;
    settleElapsedMs.current = 0;
    lockedCount.current = 0;
    jumpPhase.current = 0;
    lastTickMs.current = null;
    setSettled(false);
    setSettling(false);
    setDisplay(createScrambleDisplay());
  }, []);

  const startSettling = useCallback(() => {
    finalDisplay.current = parseDisplay(targetValue);
    settleElapsedMs.current = 0;
    settleStartHz.current = accelerationHz(accelerationElapsedMs.current);
    lockedCount.current = 0;
    jumpPhase.current = 0;
    lastTickMs.current = null;
    setSettled(false);
    setSettling(true);
  }, [targetValue]);

  useEffect(() => {
    if (ready) {
      setProgress(100);
      startSettling();
      return;
    }

    setProgress(8);
    restartAcceleration();
  }, [ready, restartAcceleration, startSettling]);

  useEffect(() => {
    if (ready) {
      return;
    }

    const timer = window.setInterval(() => {
      setProgress((current) => {
        const next = current + (92 - current) * 0.035 + 0.22;
        return Math.min(next, 92);
      });
    }, 120);

    return () => window.clearInterval(timer);
  }, [ready]);

  useEffect(() => {
    if (settled) {
      return;
    }

    let frameId: number | undefined;
    let cancelled = false;
    lastTickMs.current = window.performance.now();

    function tick(now: number) {
      if (cancelled) {
        return;
      }

      const lastTick = lastTickMs.current ?? now;
      const deltaMs = now - lastTick;
      lastTickMs.current = now;

      let currentHz: number;

      if (settling) {
        settleElapsedMs.current += deltaMs;
        currentHz = settlingHz(settleStartHz.current, settleElapsedMs.current);
      } else {
        accelerationElapsedMs.current = Math.min(
          accelerationElapsedMs.current + deltaMs,
          ACCELERATION_DURATION_MS
        );
        currentHz = accelerationHz(accelerationElapsedMs.current);
      }

      jumpPhase.current += (currentHz * deltaMs) / SECOND_MS;
      const shouldJump = jumpPhase.current >= 1;

      if (shouldJump) {
        jumpPhase.current %= 1;
      }

      if (settling && settleElapsedMs.current >= SETTLE_DURATION_MS) {
        setDisplay(finalDisplay.current);
        accelerationElapsedMs.current = 0;
        settleElapsedMs.current = 0;
        lockedCount.current = SLOT_COUNT;
        jumpPhase.current = 0;
        setSettled(true);
        return;
      }

      if (settling) {
        const nextLockedCount = Math.min(
          SLOT_COUNT,
          Math.floor((settleElapsedMs.current / SETTLE_DURATION_MS) * SLOT_COUNT)
        );

        if (shouldJump || nextLockedCount !== lockedCount.current) {
          lockedCount.current = nextLockedCount;
          setDisplay((currentDisplay) =>
            createSettlingDisplay(
              finalDisplay.current,
              nextLockedCount,
              shouldJump ? createScrambleDisplay() : currentDisplay
            )
          );
        }
      } else if (shouldJump) {
        setDisplay(createScrambleDisplay());
      }

      frameId = window.requestAnimationFrame(tick);
    }

    frameId = window.requestAnimationFrame(tick);

    return () => {
      cancelled = true;
      if (frameId !== undefined) {
        window.cancelAnimationFrame(frameId);
      }
    };
  }, [settling, settled]);

  return (
    <section className={`loading-page ${ready ? "is-ready" : "is-loading"}`} aria-label="世界线变动侦测仪加载">
      <div className="loading-vignette" aria-hidden="true" />

      <div className="loading-stack">
        <div className="meter-stage">
          <div className="tube-row">
            {display.map((glyph, index) => (
              <DivergenceTubeDigit
                key={index}
                value={glyph}
                width="var(--loading-digit-width)"
                intensity={1}
                grid
              />
            ))}
          </div>
        </div>

        <div className={`loading-glass ${ready ? "is-confirm" : ""}`}>
          <button
            className="enter-button"
            type="button"
            aria-disabled={!ready}
            aria-label={ready ? "确认进入" : `加载进度 ${Math.round(progress)}%`}
            onBlur={() => {
              if (!ready) {
                restartAcceleration();
              }
            }}
            onFocus={() => {
              if (ready) {
                startSettling();
              }
            }}
            onMouseEnter={() => {
              if (ready) {
                startSettling();
              }
            }}
            onMouseLeave={() => {
              if (!ready) {
                restartAcceleration();
              }
            }}
            onClick={() => {
              if (ready) {
                onEnter();
              }
            }}
          >
            <span className="loading-progress" aria-hidden={ready}>
              <span className="loading-progress-fill" style={{ width: `${progress}%` }} />
            </span>
            <span className="loading-label">{ready ? "确认进入" : "加载中"}</span>
            <span className="loading-percent">{ready ? "READY" : `${Math.round(progress)}%`}</span>
          </button>
        </div>
      </div>
    </section>
  );
}
