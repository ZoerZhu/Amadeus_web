import { useCallback, useEffect, useRef, useState } from "react";
import { DivergenceTubeDigit } from "./DivergenceTubeDigit";
import { pickWeightedDivergenceValue } from "./divergenceValues";
import "./BootLoader.css";

const SLOT_COUNT = 8;
const DOT_INDEX = 1;
const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const INITIAL_VALUE = "0.000000";
const ACCELERATION_DURATION_MS = 5000;
const SETTLE_DURATION_MS = 3000;
const SECOND_MS = 1000;
const MAX_ACCELERATION_SECONDS = 5;
const MAX_HZ = 5 * MAX_ACCELERATION_SECONDS ** 2;
const PROGRESS_HOLD_PERCENT = 92;
const PROGRESS_SLOW_HOLD_PERCENT = 96;
const PROGRESS_HOLD_DURATION_MS = 2400;
const PROGRESS_SLOW_HOLD_DURATION_MS = 5200;
const PROGRESS_FINALIZE_DURATION_MS = 1100;

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
  const seconds = Math.min(elapsedMs / SECOND_MS, MAX_ACCELERATION_SECONDS);
  return Math.min(5 * seconds ** 2, MAX_HZ);
}

function accelerationElapsedFromHz(hz: number) {
  const seconds = Math.sqrt(Math.max(0, Math.min(hz, MAX_HZ)) / 5);
  return Math.min(seconds * SECOND_MS, ACCELERATION_DURATION_MS);
}

function deceleratingHz(startHz: number, elapsedMs: number) {
  const progress = Math.min(elapsedMs / SETTLE_DURATION_MS, 1);
  return startHz * (1 - progress) ** 3;
}

function easeOutCubic(progress: number) {
  return 1 - (1 - progress) ** 3;
}

function loadingProgress(elapsedMs: number) {
  if (elapsedMs <= PROGRESS_HOLD_DURATION_MS) {
    return (elapsedMs / PROGRESS_HOLD_DURATION_MS) * PROGRESS_HOLD_PERCENT;
  }

  const slowElapsed = Math.min(elapsedMs - PROGRESS_HOLD_DURATION_MS, PROGRESS_SLOW_HOLD_DURATION_MS);
  return (
    PROGRESS_HOLD_PERCENT +
    (slowElapsed / PROGRESS_SLOW_HOLD_DURATION_MS) * (PROGRESS_SLOW_HOLD_PERCENT - PROGRESS_HOLD_PERCENT)
  );
}

export function BootLoader({ ready, onEnter, targetValue }: BootLoaderProps) {
  const selectedTargetValue = useRef(targetValue ?? pickWeightedDivergenceValue());
  const accelerationElapsedMs = useRef(0);
  const settleElapsedMs = useRef(0);
  const settleStartHz = useRef(0);
  const activeHz = useRef(0);
  const lockedCount = useRef(0);
  const jumpPhase = useRef(0);
  const finalDisplay = useRef<DisplayGlyph[]>(parseDisplay(selectedTargetValue.current));
  const lastTickMs = useRef<number | null>(null);
  const progressStartMs = useRef<number | null>(null);
  const progressReadyStartMs = useRef<number | null>(null);
  const progressReadyStartValue = useRef(0);
  const progressValue = useRef(0);
  const [display, setDisplay] = useState<DisplayGlyph[]>(parseDisplay(INITIAL_VALUE));
  const [settling, setSettling] = useState(false);
  const [settled, setSettled] = useState(false);
  const [progress, setProgress] = useState(0);
  const [canEnter, setCanEnter] = useState(false);

  const setProgressValue = useCallback((value: number) => {
    const nextProgress = Math.max(0, Math.min(value, 100));
    progressValue.current = nextProgress;
    setProgress(nextProgress);
  }, []);

  const restartAcceleration = useCallback(() => {
    accelerationElapsedMs.current = accelerationElapsedFromHz(activeHz.current);
    settleElapsedMs.current = 0;
    lockedCount.current = 0;
    jumpPhase.current = 0;
    lastTickMs.current = null;
    setSettled(false);
    setSettling(false);
    setDisplay(createScrambleDisplay());
  }, []);

  const startSettling = useCallback(() => {
    finalDisplay.current = parseDisplay(selectedTargetValue.current);
    settleElapsedMs.current = 0;
    settleStartHz.current = Math.max(activeHz.current, accelerationHz(accelerationElapsedMs.current));
    lockedCount.current = 0;
    jumpPhase.current = 0;
    lastTickMs.current = null;
    setSettled(false);
    setSettling(true);
  }, []);

  useEffect(() => {
    if (targetValue) {
      selectedTargetValue.current = targetValue;
      finalDisplay.current = parseDisplay(targetValue);
    }
  }, [targetValue]);

  useEffect(() => {
    progressStartMs.current = window.performance.now();
    progressReadyStartMs.current = null;
    progressReadyStartValue.current = 0;
    setProgressValue(0);
    setCanEnter(false);
    accelerationElapsedMs.current = 0;
    activeHz.current = 0;
    restartAcceleration();
  }, [restartAcceleration, setProgressValue]);

  useEffect(() => {
    let frameId: number | undefined;
    let cancelled = false;

    function tick(now: number) {
      if (cancelled) {
        return;
      }

      const startMs = progressStartMs.current ?? now;
      const baseProgress = loadingProgress(now - startMs);

      if (ready) {
        if (progressReadyStartMs.current === null) {
          progressReadyStartMs.current = now;
          progressReadyStartValue.current = Math.max(progressValue.current, baseProgress);
        }

        const finishProgress = Math.min(
          (now - progressReadyStartMs.current) / PROGRESS_FINALIZE_DURATION_MS,
          1
        );
        const nextProgress =
          progressReadyStartValue.current +
          (100 - progressReadyStartValue.current) * easeOutCubic(finishProgress);

        setProgressValue(nextProgress);
        if (finishProgress >= 1) {
          setCanEnter(true);
          return;
        }
      } else {
        progressReadyStartMs.current = null;
        progressReadyStartValue.current = 0;
        setCanEnter(false);
        setProgressValue(baseProgress);
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
  }, [ready, setProgressValue]);

  useEffect(() => {
    if (settled && settling) {
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
        currentHz = deceleratingHz(settleStartHz.current, settleElapsedMs.current);
      } else {
        accelerationElapsedMs.current = Math.min(
          accelerationElapsedMs.current + deltaMs,
          ACCELERATION_DURATION_MS
        );
        currentHz = accelerationHz(accelerationElapsedMs.current);
      }

      activeHz.current = currentHz;
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
        activeHz.current = 0;
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
    <section className={`loading-page ${canEnter ? "is-ready" : "is-loading"}`} aria-label="世界线变动侦测仪加载">
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

        <div className={`loading-glass ${canEnter ? "is-confirm" : ""}`}>
          <button
            className="enter-button"
            type="button"
            aria-disabled={!canEnter}
            aria-label={canEnter ? "确认进入" : `加载进度 ${Math.round(progress)}%`}
            onBlur={() => {
              if (canEnter) {
                restartAcceleration();
              }
            }}
            onFocus={() => {
              if (canEnter) {
                startSettling();
              }
            }}
            onMouseEnter={() => {
              if (canEnter) {
                startSettling();
              }
            }}
            onMouseLeave={() => {
              if (canEnter) {
                restartAcceleration();
              }
            }}
            onClick={() => {
              if (canEnter) {
                onEnter();
              }
            }}
          >
            <span className="loading-progress" aria-hidden={canEnter}>
              <span className="loading-progress-fill" style={{ width: `${progress}%` }} />
            </span>
            <span className="loading-label">{canEnter ? "确认进入" : "加载中"}</span>
            <span className="loading-percent">{canEnter ? "READY" : `${Math.round(progress)}%`}</span>
          </button>
        </div>
      </div>
    </section>
  );
}
