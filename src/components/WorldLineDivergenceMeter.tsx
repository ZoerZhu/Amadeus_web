import {
  forwardRef,
  memo,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type { CSSProperties } from "react";
import "./WorldLineDivergenceMeter.css";

type Digit = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9";
type TubeSymbol = Digit | ".";

export type WorldLineTransition = "worldline" | "roll" | "burn" | "instant";
export type SettleDirection = "left-to-right" | "right-to-left" | "center-out";

export type WorldLineMeterHandle = {
  shiftTo: (
    nextValue: string | number,
    options?: Partial<WorldLineDivergenceMeterProps>
  ) => void;
  pulse: () => void;
};

export type WorldLineDivergenceMeterProps = {
  value?: string | number;
  integerDigits?: number;
  decimalDigits?: number;
  transition?: WorldLineTransition;
  settleDirection?: SettleDirection;
  duration?: number;
  stagger?: number;
  scrambleRate?: number;
  tubeScale?: number;
  gap?: number;
  glow?: number;
  ghostOpacity?: number;
  showLabel?: boolean;
  className?: string;
  onComplete?: (value: string) => void;
};

const DIGITS: Digit[] = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];

/**
 * 坐标系按参考图比例设计：viewBox = 180 x 460
 * 每个数字使用一组 stroke，而不是字体。
 */
const DIGIT_STROKES: Record<Digit, string[]> = {
  "0": [
    "M90 77 C52 77 31 137 31 230 C31 324 52 383 90 383 C128 383 149 324 149 230 C149 137 128 77 90 77",
  ],

  "1": [
    "M43 130 C58 104 78 84 103 75",
    "M103 75 L103 382",
    "M36 152 H137",
  ],

  "2": [
    "M43 108 C56 85 83 73 108 81 C134 89 146 116 137 145 C130 166 114 188 92 216 L45 333",
    "M39 153 H142",
    "M45 333 H139",
  ],

  "3": [
    "M45 82 H139 L93 186",
    "M45 230 H118",
    "M93 186 C130 188 148 214 143 259 C137 318 98 350 43 338",
  ],

  "4": [
    "M122 76 L42 265 H144",
    "M122 76 V382",
  ],

  "5": [
    "M139 83 H56 L48 207",
    "M48 207 C72 190 115 191 135 221 C156 253 143 319 96 340 C72 351 48 345 35 333",
    "M45 244 H137",
  ],

  "6": [
    "M125 77 C78 124 48 184 45 259 C43 318 73 363 111 350 C151 337 154 269 119 242 C86 218 47 235 43 291",
    "M45 249 H135",
  ],

  "7": [
    "M42 82 H142 L76 346",
    "M39 152 H132",
  ],

  "8": [
    "M90 78 C55 78 38 118 40 166 C42 213 63 231 90 231 C117 231 138 213 140 166 C142 118 125 78 90 78",
    "M90 231 C51 231 34 272 38 322 C42 368 60 385 90 385 C120 385 138 368 142 322 C146 272 129 231 90 231",
  ],

  "9": [
    "M90 78 C54 78 36 118 39 168 C42 214 64 234 96 226 C127 218 143 181 137 132 C133 101 116 78 90 78",
    "M123 219 L75 345",
  ],
};

const GRID_X = [
  19, 29, 39, 49, 59, 69, 79, 89, 99, 109, 119, 129, 139, 149, 159,
];

const GRID_Y = [
  55, 70, 85, 100, 115, 130, 145, 160, 175, 190, 205, 220, 235, 250,
  265, 280, 295, 310, 325, 340, 355, 370, 385,
];

function normalizeValue(
  value: string | number,
  integerDigits: number,
  decimalDigits: number
): TubeSymbol[] {
  const raw =
    typeof value === "number"
      ? value.toFixed(decimalDigits)
      : String(value).trim();

  const cleaned = raw.replace(/[^\d.]/g, "");
  const [rawInt = "0", ...rest] = cleaned.split(".");
  const rawDecimal = rest.join("");

  const intPart = rawInt.replace(/\D/g, "").padStart(integerDigits, "0").slice(-integerDigits);
  const decPart = rawDecimal.replace(/\D/g, "").padEnd(decimalDigits, "0").slice(0, decimalDigits);

  return `${intPart}.${decPart}`.split("") as TubeSymbol[];
}

function pseudoDigit(slotIndex: number, frame: number): Digit {
  const n = Math.abs(Math.sin(slotIndex * 91.73 + frame * 12.9898) * 100000) | 0;
  return DIGITS[n % 10];
}

function buildSettleOrder(slots: TubeSymbol[], direction: SettleDirection) {
  const indexes = slots
    .map((symbol, index) => (symbol === "." ? null : index))
    .filter((index): index is number => index !== null);

  if (direction === "right-to-left") {
    return [...indexes].reverse();
  }

  if (direction === "center-out") {
    const center = (slots.length - 1) / 2;
    return [...indexes].sort((a, b) => Math.abs(a - center) - Math.abs(b - center));
  }

  return indexes;
}

function TubeMesh() {
  return (
    <>
      <g className="wl-back-mesh">
        {GRID_X.map((x, index) => (
          <line
            key={`bx-${x}`}
            className={index % 5 === 0 ? "wl-mesh-major" : "wl-mesh-line"}
            x1={x}
            y1="42"
            x2={x}
            y2="398"
          />
        ))}

        {GRID_Y.map((y, index) => (
          <line
            key={`by-${y}`}
            className={index % 5 === 0 ? "wl-mesh-major" : "wl-mesh-line"}
            x1="24"
            y1={y}
            x2="156"
            y2={y}
          />
        ))}

        <path className="wl-diagonal-wire" d="M42 382 L137 78" />
        <path className="wl-diagonal-wire" d="M42 78 L140 382" />
      </g>
    </>
  );
}

type TubeProps = {
  symbol: TubeSymbol;
  previous?: TubeSymbol;
  locked: boolean;
  changing: boolean;
  visualMode: WorldLineTransition;
};

const NixieTube = memo(function NixieTube({
  symbol,
  previous,
  locked,
  changing,
  visualMode,
}: TubeProps) {
  const rawId = useId();
  const id = rawId.replace(/:/g, "");
  const glowId = `wl-glow-${id}`;
  const heavyGlowId = `wl-heavy-glow-${id}`;
  const scanId = `wl-scan-${id}`;

  const isDot = symbol === ".";
  const activeStrokes = isDot ? [] : DIGIT_STROKES[symbol];
  const previousStrokes =
    previous && previous !== "." && previous !== symbol ? DIGIT_STROKES[previous] : null;

  const showPrevious = visualMode === "burn" && previousStrokes;

  return (
    <div
      className={[
        "wl-tube",
        locked ? "is-locked" : "is-unlocked",
        changing ? "is-changing" : "",
        isDot ? "is-dot-tube" : "",
      ].join(" ")}
    >
      <svg
        className="wl-tube-svg"
        viewBox="0 0 180 460"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <defs>
          <filter
            id={glowId}
            x="-90"
            y="-100"
            width="360"
            height="660"
            filterUnits="userSpaceOnUse"
          >
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          <filter
            id={heavyGlowId}
            x="-120"
            y="-140"
            width="420"
            height="740"
            filterUnits="userSpaceOnUse"
          >
            <feGaussianBlur stdDeviation="18" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          <pattern id={scanId} width="180" height="12" patternUnits="userSpaceOnUse">
            <rect x="0" y="0" width="180" height="2" className="wl-scan-hot" />
            <rect x="0" y="7" width="180" height="1" className="wl-scan-dark" />
          </pattern>

          <linearGradient id={`wl-glass-${id}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="rgba(255,255,255,0.18)" />
            <stop offset="18%" stopColor="rgba(255,255,255,0.04)" />
            <stop offset="52%" stopColor="rgba(255,120,30,0.05)" />
            <stop offset="82%" stopColor="rgba(255,255,255,0.03)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0.16)" />
          </linearGradient>
        </defs>

        <rect className="wl-tube-bg" x="8" y="10" width="164" height="438" rx="42" />
        <rect
          className="wl-glass-fill"
          x="12"
          y="14"
          width="156"
          height="430"
          rx="40"
          fill={`url(#wl-glass-${id})`}
        />

        <ellipse className="wl-top-electrode" cx="90" cy="42" rx="46" ry="7" />
        <ellipse className="wl-bottom-electrode" cx="90" cy="412" rx="52" ry="9" />

        <g className="wl-ghost-stack">
          {DIGITS.map((digit, digitIndex) => (
            <g
              key={`ghost-${digit}`}
              opacity={0.18 + digitIndex * 0.012}
              transform={`translate(${(digitIndex - 4.5) * 0.7} ${
                ((digitIndex % 3) - 1) * 0.9
              })`}
            >
              {DIGIT_STROKES[digit].map((d, strokeIndex) => (
                <path
                  key={`ghost-${digit}-${strokeIndex}`}
                  className="wl-ghost-stroke"
                  d={d}
                />
              ))}
            </g>
          ))}
        </g>

        <TubeMesh />

        {showPrevious && (
          <g className="wl-previous-glyph">
            {previousStrokes.map((d, index) => (
              <path key={`prev-${index}`} d={d} />
            ))}
          </g>
        )}

        {!isDot && (
          <g className="wl-active-glyph">
            {activeStrokes.map((d, index) => (
              <path
                key={`active-heavy-${index}`}
                className="wl-active-heavy"
                d={d}
                filter={`url(#${heavyGlowId})`}
              />
            ))}

            {activeStrokes.map((d, index) => (
              <path
                key={`active-glow-${index}`}
                className="wl-active-glow"
                d={d}
                filter={`url(#${glowId})`}
              />
            ))}

            {activeStrokes.map((d, index) => (
              <path key={`active-body-${index}`} className="wl-active-body" d={d} />
            ))}

            {activeStrokes.map((d, index) => (
              <path key={`active-core-${index}`} className="wl-active-core" d={d} />
            ))}
          </g>
        )}

        {isDot && (
          <g className="wl-active-dot">
            <circle
              className="wl-dot-heavy"
              cx="145"
              cy="383"
              r="26"
              filter={`url(#${heavyGlowId})`}
            />
            <circle
              className="wl-dot-glow"
              cx="145"
              cy="383"
              r="18"
              filter={`url(#${glowId})`}
            />
            <circle className="wl-dot-body" cx="145" cy="383" r="12" />
            <circle className="wl-dot-core" cx="145" cy="383" r="5" />
          </g>
        )}

        <g className="wl-front-mesh">
          {GRID_X.map((x, index) => (
            <line
              key={`fx-${x}`}
              className={index % 5 === 0 ? "wl-front-major" : "wl-front-line"}
              x1={x}
              y1="42"
              x2={x}
              y2="398"
            />
          ))}

          {GRID_Y.map((y, index) => (
            <line
              key={`fy-${y}`}
              className={index % 5 === 0 ? "wl-front-major" : "wl-front-line"}
              x1="24"
              y1={y}
              x2="156"
              y2={y}
            />
          ))}
        </g>

        <rect className="wl-scan-overlay" x="12" y="18" width="156" height="420" rx="38" fill={`url(#${scanId})`} />
        <rect className="wl-vignette" x="8" y="10" width="164" height="438" rx="42" />
        <path className="wl-left-highlight" d="M34 46 C22 142 22 312 35 402" />
        <path className="wl-right-highlight" d="M150 48 C162 142 162 310 149 404" />
      </svg>
    </div>
  );
});

export const WorldLineDivergenceMeter = forwardRef<
  WorldLineMeterHandle,
  WorldLineDivergenceMeterProps
>(function WorldLineDivergenceMeter(
  {
    value = "1.048596",
    integerDigits = 1,
    decimalDigits = 6,
    transition = "worldline",
    settleDirection = "right-to-left",
    duration = 1800,
    stagger = 95,
    scrambleRate = 44,
    tubeScale = 1,
    gap = 7,
    glow = 1,
    ghostOpacity = 1,
    showLabel = true,
    className = "",
    onComplete,
  },
  ref
) {
  const initialSlots = normalizeValue(value, integerDigits, decimalDigits);

  const [slots, setSlots] = useState<TubeSymbol[]>(initialSlots);
  const [previousSlots, setPreviousSlots] = useState<TubeSymbol[]>(initialSlots);
  const [locked, setLocked] = useState<boolean[]>(initialSlots.map(() => true));
  const [phase, setPhase] = useState<"idle" | "changing" | "burst">("idle");
  const [visualMode, setVisualMode] = useState<WorldLineTransition>(transition);

  const slotsRef = useRef<TubeSymbol[]>(initialSlots);
  const rafRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(false);

  function commitSlots(nextSlots: TubeSymbol[]) {
    slotsRef.current = nextSlots;
    setSlots(nextSlots);
  }

  function stopRunningAnimation() {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }

  function pulse() {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
    }

    setPhase("burst");

    timerRef.current = setTimeout(() => {
      setPhase("idle");
    }, 760);
  }

  function shiftTo(
    nextValue: string | number,
    options: Partial<WorldLineDivergenceMeterProps> = {}
  ) {
    const nextIntegerDigits = options.integerDigits ?? integerDigits;
    const nextDecimalDigits = options.decimalDigits ?? decimalDigits;
    const nextTransition = options.transition ?? transition;
    const nextDirection = options.settleDirection ?? settleDirection;
    const nextDuration = options.duration ?? duration;
    const nextStagger = options.stagger ?? stagger;
    const nextScrambleRate = options.scrambleRate ?? scrambleRate;

    const nextSlots = normalizeValue(nextValue, nextIntegerDigits, nextDecimalDigits);
    const fromSlots = slotsRef.current;

    stopRunningAnimation();
    setPreviousSlots(fromSlots);
    setVisualMode(nextTransition);

    if (nextTransition === "instant") {
      commitSlots(nextSlots);
      setLocked(nextSlots.map(() => true));
      setPhase("idle");
      onComplete?.(nextSlots.join(""));
      return;
    }

    if (nextTransition === "burn") {
      commitSlots(nextSlots);
      setLocked(nextSlots.map(() => true));
      pulse();
      onComplete?.(nextSlots.join(""));
      return;
    }

    const order = buildSettleOrder(nextSlots, nextDirection);
    const orderMap = new Map<number, number>();
    order.forEach((slotIndex, orderIndex) => {
      orderMap.set(slotIndex, orderIndex);
    });

    const maxOrder = Math.max(0, order.length - 1);
    const lockSpan = maxOrder * nextStagger;
    const chaosDuration = Math.max(320, nextDuration - lockSpan - 260);
    const finishAt = chaosDuration + lockSpan + 260;

    setPhase("changing");
    setLocked(nextSlots.map((symbol) => symbol === "."));

    const startedAt = performance.now();

    const tick = (now: number) => {
      const elapsed = now - startedAt;
      const frame = Math.floor(elapsed / Math.max(16, nextScrambleRate));

      const currentSlots = nextSlots.map((targetSymbol, slotIndex): TubeSymbol => {
        if (targetSymbol === ".") return ".";

        const orderIndex = orderMap.get(slotIndex) ?? 0;
        const lockAt = chaosDuration + orderIndex * nextStagger;

        if (elapsed >= lockAt) {
          return targetSymbol;
        }

        if (nextTransition === "roll") {
          const n = (frame + orderIndex * 3 + slotIndex * 5) % 10;
          return DIGITS[n];
        }

        const isNearLock = lockAt - elapsed < 130;
        if (isNearLock && frame % 2 === 0) {
          return targetSymbol;
        }

        return pseudoDigit(slotIndex, frame + orderIndex * 11);
      });

      const nextLocked = nextSlots.map((targetSymbol, slotIndex) => {
        if (targetSymbol === ".") return true;

        const orderIndex = orderMap.get(slotIndex) ?? 0;
        const lockAt = chaosDuration + orderIndex * nextStagger;
        return elapsed >= lockAt;
      });

      commitSlots(currentSlots);
      setLocked(nextLocked);

      if (elapsed < finishAt) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        commitSlots(nextSlots);
        setLocked(nextSlots.map(() => true));
        pulse();
        onComplete?.(nextSlots.join(""));
      }
    };

    rafRef.current = requestAnimationFrame(tick);
  }

  useImperativeHandle(ref, () => ({
    shiftTo,
    pulse,
  }));

  useEffect(() => {
    const nextSlots = normalizeValue(value, integerDigits, decimalDigits);

    if (!mountedRef.current) {
      mountedRef.current = true;
      commitSlots(nextSlots);
      setPreviousSlots(nextSlots);
      setLocked(nextSlots.map(() => true));
      return;
    }

    shiftTo(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, integerDigits, decimalDigits]);

  useEffect(() => {
    return () => {
      stopRunningAnimation();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const labelValue = slots.join("");

  return (
    <div
      className={[
        "worldline-meter",
        phase === "changing" ? "is-changing" : "",
        phase === "burst" ? "is-burst" : "",
        className,
      ].join(" ")}
      style={
        {
          "--wl-scale": tubeScale,
          "--wl-gap": `${gap}px`,
          "--wl-glow": glow,
          "--wl-ghost": ghostOpacity,
        } as CSSProperties
      }
      role="img"
      aria-label={`World line divergence meter ${labelValue}`}
    >
      <div className="worldline-meter__deck">
        <div className="worldline-meter__tube-row">
          {slots.map((symbol, index) => (
            <NixieTube
              key={`tube-${index}`}
              symbol={symbol}
              previous={previousSlots[index]}
              locked={locked[index] ?? true}
              changing={phase === "changing"}
              visualMode={visualMode}
            />
          ))}
        </div>
      </div>

      {showLabel && (
        <div className="worldline-meter__label">
          <span>WORLD LINE</span>
          <strong>DIVERGENCE METER</strong>
          <em>{labelValue}</em>
        </div>
      )}
    </div>
  );
});
