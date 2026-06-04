import React, { CSSProperties, useId } from "react";
import "./DivergenceTubeDigit.css";

type DigitGlyph = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9";
type TubeGlyph = DigitGlyph | ".";

type CrossSpec = {
  /** 横线位置。竖线统一固定为上中心到下中心。 */
  y: number;
  left?: number;
  right?: number;
};

export type DivergenceTubeDigitProps = {
  value: TubeGlyph | number;
  glass?: boolean;
  grid?: boolean;
  width?: number | string;
  className?: string;
  intensity?: number;
};

const X_L = 28;
const X_R = 152;
const X_C = 90;

const Y_TOP = 78;
const Y_UPPER = 140;
const Y_LOWER = 256;
const Y_BOTTOM = 324;

/**
 * 修复点：
 * 无论是上十字还是下十字，竖线都统一为数字绘制区的中心竖线：
 * x = 90, y = 78 -> 324。
 * 上 / 下十字只影响横线的 y 坐标。
 */
const CROSS_X = X_C;
const CROSS_TOP = Y_TOP;
const CROSS_BOTTOM = Y_BOTTOM;

const UPPER_CIRCLE_TOP_ARC = `
  M28 140
  C28 106 56 78 90 78
  C124 78 152 106 152 140
`;

const UPPER_CIRCLE_FULL = `
  M90 78
  C56 78 28 106 28 140
  C28 174 56 202 90 202
  C124 202 152 174 152 140
  C152 106 124 78 90 78
`;

const LOWER_ROUND_THREE_QUARTER = `
  C124 188 152 218 152 256
  C152 294 124 324 90 324
  C56 324 28 294 28 256
`;

const LOWER_ROUND_FIVE_ARC = `
  C72 186 152 202 152 256
  C152 294 124 324 90 324
  C56 324 28 294 28 256
`;

const LOWER_CIRCLE_FULL = `
  M90 188
  C124 188 152 218 152 256
  C152 294 124 324 90 324
  C56 324 28 294 28 256
  C28 218 56 188 90 188
`;

const EIGHT_UPPER_OUTER_ARC = `
  M78 196
  C27 163 26 119 48 95
  C72 68 108 68 132 95
  C154 119 153 163 102 196
`;

const EIGHT_LOWER_ELLIPSE_OUTER_ARC = `
  M102 196
  C160 209 160 267 137 302
  C112 334 68 334 43 302
  C20 267 20 209 78 196
`;

const upper = (): CrossSpec => ({
  y: Y_UPPER,
  left: X_L,
  right: X_R,
});

const lower = (): CrossSpec => ({
  y: Y_LOWER,
  left: X_L,
  right: X_R,
});

/**
 * 所有数字都在同一个 180 × 460 坐标系中绘制。
 * 亮线主高度统一在 78 ~ 324。
 */
const DIGIT_PATHS: Record<DigitGlyph, string[]> = {
  "0": [
    `
      M90 78
      C50 78 29 122 29 201
      C29 280 50 324 90 324
      C130 324 151 280 151 201
      C151 122 130 78 90 78
      Z
    `,
  ],

  /**
   * 1：主竖线从上中心到下中心。
   * 这样 1 的竖线与十字竖线完全重合。
   */
  "1": [
    `
      M56 102
      C63 94 70 87 78 82
      M90 80
      L90 324
    `,
  ],

  "2": [
    `
      ${UPPER_CIRCLE_TOP_ARC}
      L39 324
      H149
    `,
  ],

  "3": [
    `
      M35 82
      H147
      L90 188
      ${LOWER_ROUND_THREE_QUARTER}
    `,
  ],

  "4": [
    `
      M109 80
      L27 256
      H151
      M109 80
      V324
    `,
  ],

  "5": [
    `
      M147 82
      H48
      L39 198
      ${LOWER_ROUND_FIVE_ARC}
    `,
  ],

  /**
   * 6：起点改为上方中心 M90 80。
   * 修复之前 6 的起点与十字竖线不对齐的问题。
   */
  "6": [
    `
      M90 80
      C58 118 37 177 28 256
    `,
    LOWER_CIRCLE_FULL,
  ],

  "7": [
    `
      M36 82
      H151
      L67 324
    `,
  ],

  "8": [
    EIGHT_UPPER_OUTER_ARC,
    EIGHT_LOWER_ELLIPSE_OUTER_ARC,
  ],

  /**
   * 9：下落尾巴终点改为下方中心 90 324。
   * 修复之前 9 的终点与十字竖线不对齐的问题。
   */
  "9": [
    `
      ${UPPER_CIRCLE_FULL}
      M148 142
      C139 205 116 270 90 324
    `,
  ],
};

/**
 * 细十字线配置：
 * 0 上十字
 * 1 上十字
 * 2 上十字
 * 3 下十字
 * 4 下十字
 * 5 下十字
 * 6 下十字
 * 7 上十字
 * 8 上下十字
 * 9 上十字
 *
 * 注意：竖线不再由单个数字决定，统一绘制为中心贯穿竖线。
 */
const CROSSES: Record<DigitGlyph, CrossSpec[]> = {
  "0": [upper()],
  "1": [upper()],
  "2": [upper()],
  "3": [lower()],
  "4": [lower()],
  "5": [lower()],
  "6": [lower()],
  "7": [upper()],
  "8": [upper(), lower()],
  "9": [upper()],
};

const GRID_COLUMNS = 10;
const GRID_LEFT = 25;
const GRID_RIGHT = 153;
const GRID_BOTTOM = 366;
const GRID_V = Array.from(
  { length: GRID_COLUMNS + 1 },
  (_, i) => GRID_LEFT + ((GRID_RIGHT - GRID_LEFT) / GRID_COLUMNS) * i,
);
const GRID_CELL = (GRID_RIGHT - GRID_LEFT) / GRID_COLUMNS;
const GRID_ROWS = Math.floor((GRID_BOTTOM - 58) / GRID_CELL);
const GRID_TOP = GRID_BOTTOM - GRID_ROWS * GRID_CELL;
const GRID_H = Array.from(
  { length: GRID_ROWS + 1 },
  (_, i) => GRID_TOP + GRID_CELL * i,
);

function normalizeGlyph(value: TubeGlyph | number): TubeGlyph {
  const text = String(value).trim();
  const first = text[0] as TubeGlyph;

  if (first === ".") return ".";
  if (/^[0-9]$/.test(first)) return first as DigitGlyph;

  return "0";
}

function StrokeLayer({ layer, paths }: { layer: string; paths: string[] }) {
  return (
    <g className={`sg-layer sg-layer-digit sg-layer-${layer}`}>
      {paths.map((d, i) => (
        <path key={`${layer}-${i}`} d={d} />
      ))}
    </g>
  );
}

function CrossLayer({
  layer,
  crosses,
}: {
  layer: "outer" | "mid" | "core" | "hot";
  crosses: CrossSpec[];
}) {
  if (!crosses.length) return null;

  return (
    <g className={`sg-layer sg-layer-cross sg-cross-${layer}`}>
      {crosses.map((c, i) => {
        const left = c.left ?? X_L;
        const right = c.right ?? X_R;

        return (
          <line
            key={`cross-${layer}-h-${i}`}
            x1={left}
            y1={c.y}
            x2={right}
            y2={c.y}
          />
        );
      })}

      <line
        key={`cross-${layer}-v`}
        x1={CROSS_X}
        y1={CROSS_TOP}
        x2={CROSS_X}
        y2={CROSS_BOTTOM}
      />
    </g>
  );
}

function CoilLayers({
  paths,
  crosses,
}: {
  paths: string[];
  crosses: CrossSpec[];
}) {
  return (
    <>
      <StrokeLayer layer="outer" paths={paths} />
      <CrossLayer layer="outer" crosses={crosses} />

      <StrokeLayer layer="mid" paths={paths} />
      <CrossLayer layer="mid" crosses={crosses} />

      <StrokeLayer layer="core" paths={paths} />
      <CrossLayer layer="core" crosses={crosses} />

      <StrokeLayer layer="hot" paths={paths} />
      <CrossLayer layer="hot" crosses={crosses} />
    </>
  );
}

function DotLayers() {
  return (
    <g className="sg-dot">
      <circle className="sg-dot-outer" cx="146" cy="346" r="14" />
      <circle className="sg-dot-mid" cx="146" cy="346" r="9" />
      <circle className="sg-dot-core" cx="146" cy="346" r="6" />
      <circle className="sg-dot-hot" cx="146" cy="346" r="3" />
    </g>
  );
}

function GridLines({
  className,
  maskId,
}: {
  className: string;
  maskId?: string;
}) {
  return (
    <g className={className} mask={maskId ? `url(#${maskId})` : undefined}>
      {GRID_V.map((x) => (
        <line key={`v-${x}`} x1={x} y1="54" x2={x} y2="366" />
      ))}

      {GRID_H.map((y) => (
        <line key={`h-${y}`} x1="20" y1={y} x2="160" y2={y} />
      ))}

    </g>
  );
}

function GridLayers({ maskId }: { maskId: string }) {
  return (
    <>
      <GridLines className="sg-grid-lines sg-grid-lines--soft" />
      <GridLines className="sg-grid-lines sg-grid-lines--strong" maskId={maskId} />
    </>
  );
}

export function DivergenceTubeDigit({
  value,
  glass = true,
  grid = true,
  width = 180,
  intensity = 1,
  className = "",
}: DivergenceTubeDigitProps) {
  const rawId = useId().replace(/:/g, "");
  const glyph = normalizeGlyph(value);

  const style = {
    "--sg-tube-width": typeof width === "number" ? `${width}px` : width,
    "--sg-intensity": intensity,
  } as CSSProperties;

  const isDot = glyph === ".";
  const digit = glyph as DigitGlyph;
  const gridMaskId = `${rawId}-grid-strength-mask`;

  return (
    <div
      className={[
        "sg-tube",
        glass ? "sg-tube--glass-on" : "sg-tube--glass-off",
        className,
      ].join(" ")}
      style={style}
      data-value={glyph}
    >
      <svg
        className="sg-svg"
        viewBox="0 0 180 460"
        role="img"
        aria-label={`divergence meter glyph ${glyph}`}
      >
        <defs>
          <clipPath id={`${rawId}-tube-window`}>
            <rect x="18" y="48" width="144" height="322" rx="8" />
          </clipPath>

          <radialGradient id={`${rawId}-amber`} cx="50%" cy="45%" r="60%">
            <stop offset="0%" stopColor="#fff1a3" />
            <stop offset="38%" stopColor="#ffb12b" />
            <stop offset="72%" stopColor="#ff5a10" />
            <stop offset="100%" stopColor="#b31700" />
          </radialGradient>

          {grid && (
            <mask id={gridMaskId} maskUnits="userSpaceOnUse">
              <rect x="0" y="0" width="180" height="460" fill="white" />

              {!isDot && (
                <>
                  <g
                    fill="none"
                    stroke="#6e6e6eff"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="28"
                  >
                    {DIGIT_PATHS[digit].map((d, i) => (
                      <path key={`grid-mask-digit-${i}`} d={d} />
                    ))}
                  </g>

                  <g
                    fill="none"
                    stroke="#6e6e6eff"
                    strokeLinecap="round"
                    strokeWidth="18"
                  >
                    {CROSSES[digit].map((c, i) => {
                      const left = c.left ?? X_L;
                      const right = c.right ?? X_R;

                      return (
                        <line
                          key={`grid-mask-cross-h-${i}`}
                          x1={left}
                          y1={c.y}
                          x2={right}
                          y2={c.y}
                        />
                      );
                    })}

                    <line
                      key="grid-mask-cross-v"
                      x1={CROSS_X}
                      y1={CROSS_TOP}
                      x2={CROSS_X}
                      y2={CROSS_BOTTOM}
                    />
                  </g>
                </>
              )}

              {isDot && <circle cx="146" cy="346" r="16" fill="#5a5a5a" />}
            </mask>
          )}
        </defs>

        <g className="sg-back">
          <ellipse className="sg-back-oval" cx="90" cy="199" rx="61" ry="147" />
          <ellipse
            className="sg-back-oval sg-back-oval-inner"
            cx="90"
            cy="199"
            rx="48"
            ry="127"
          />

          <path className="sg-back-wire" d="M54 74 L28 332" />
          <path className="sg-back-wire" d="M83 74 L57 332" />
          <path className="sg-back-wire" d="M112 74 L86 332" />
          <path className="sg-back-wire" d="M141 74 L115 332" />

          <line
            className="sg-back-wire sg-back-horizontal"
            x1="26"
            y1="140"
            x2="154"
            y2="140"
          />
          <line
            className="sg-back-wire sg-back-horizontal"
            x1="26"
            y1="256"
            x2="154"
            y2="256"
          />
        </g>

        <g clipPath={`url(#${rawId}-tube-window)`}>
          {!isDot && (
            <CoilLayers paths={DIGIT_PATHS[digit]} crosses={CROSSES[digit]} />
          )}

          {isDot && <DotLayers />}

          {grid && <GridLayers maskId={gridMaskId} />}
        </g>

        <g className="sg-caps">
          <ellipse cx="90" cy="36" rx="44" ry="8" />
          <ellipse cx="90" cy="417" rx="60" ry="10" />
        </g>
      </svg>

      <span className="sg-glass" aria-hidden="true" />
    </div>
  );
}

export default DivergenceTubeDigit;
