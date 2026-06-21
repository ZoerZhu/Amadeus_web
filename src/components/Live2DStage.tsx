import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState
} from "react";
import type { Live2DControlOption, Live2DModelRecord, Live2DModelTransform } from "../agents/live2dImportAgent";

declare global {
  interface Window {
    PIXI?: any;
  }
}

export interface Live2DStageHandle {
  playEmotion: (emotion: string) => void;
  playExpression: (expression: string) => void;
  playMotion: (group: string, index?: number) => void;
  resetMouth: () => void;
  setMouthOpen: (value: number) => void;
}

export interface Live2DStageProps {
  model: Live2DModelRecord;
  transformLocked: boolean;
  speaking: boolean;
  onReady?: () => void;
  onError?: (message: string) => void;
  onTransformChange?: (transform: Live2DModelTransform) => void;
}

const LIVE2D_SCRIPTS = [
  "/utils/live2d/pixi.min.js",
  "/utils/live2d/live2dcubismcore.min.js",
  "/utils/live2d/live2d.min.js",
  "/utils/live2d/index.min.js"
];

const EMOTION_CANDIDATES: Record<string, string[]> = {
  anger: ["anger", "angry"],
  joy: ["joy", "happy", "smile", "smile1", "smile2"],
  neutral: ["neutral", "idle", "normal"],
  sadness: ["sadness", "sad", "down"],
  shy: ["shy", "shy1", "embarrassed"],
  smile: ["smile", "smile1", "smile2", "joy", "happy"],
  smile1: ["smile1", "smile", "joy"],
  smile2: ["smile2", "smile", "joy"],
  surprise: ["surprise", "surprised"],
  unhappy: ["unhappy", "sadness", "sad"]
};
const MIN_MODEL_SCALE = 0.35;
const MAX_MODEL_SCALE = 2.6;
const MIN_MODEL_OFFSET = -0.8;
const MAX_MODEL_OFFSET = 0.8;
const MOUTH_OPEN_PARAMETER_IDS = ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y"];

let scriptLoadPromise: Promise<void> | null = null;

function loadLive2DScripts(): Promise<void> {
  if (scriptLoadPromise) {
    return scriptLoadPromise;
  }
  scriptLoadPromise = LIVE2D_SCRIPTS.reduce<Promise<void>>(
    (chain, src) => chain.then(() => loadScript(src)),
    Promise.resolve()
  );
  return scriptLoadPromise;
}

function loadScript(src: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
  if (existing?.dataset.loaded === "true") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const script = existing ?? document.createElement("script");
    script.src = src;
    script.async = false;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`Live2D runtime failed: ${src}`));
    if (!existing) {
      document.body.appendChild(script);
    }
  });
}

function normalizeKey(value: string): string {
  return value.trim().toLowerCase();
}

function stripLive2DExtension(value: string): string {
  return value
    .replace(/\.motion3\.json$/i, "")
    .replace(/\.exp3\.json$/i, "")
    .replace(/\.exp\.json$/i, "")
    .replace(/\.mtn$/i, "")
    .replace(/\.[^.]+$/i, "");
}

function getCandidateNames(value: string): string[] {
  const key = normalizeKey(value);
  return Array.from(new Set([key, ...(EMOTION_CANDIDATES[key] ?? [])].filter(Boolean)));
}

function optionMatches(option: Live2DControlOption, candidates: string[]): boolean {
  const values = [option.name, option.label, option.group ?? "", option.file ? stripLive2DExtension(option.file.split("/").pop() ?? "") : ""]
    .map(normalizeKey)
    .filter(Boolean);
  return candidates.some((candidate) => values.includes(candidate));
}

function findExpressionOption(model: Live2DModelRecord, value: string): Live2DControlOption | null {
  const candidates = getCandidateNames(value);
  return model.expressions.find((option) => optionMatches(option, candidates)) ?? null;
}

function findMotionOption(model: Live2DModelRecord, value: string, index?: number): Live2DControlOption | null {
  const candidates = getCandidateNames(value);
  const matching = model.motions.filter((option) => optionMatches(option, candidates));
  if (typeof index === "number") {
    return matching.find((option) => (option.index ?? 0) === index) ?? null;
  }
  return matching[0] ?? null;
}

function pickIdleMotion(model: Live2DModelRecord): Live2DControlOption | null {
  const groups = new Set(model.idleMotionGroups.map(normalizeKey));
  const preferred = model.motions.filter((motion) => groups.has(normalizeKey(motion.group ?? motion.name)));
  const pool = preferred.length > 0 ? preferred : model.motions;
  if (pool.length === 0) {
    return null;
  }
  return pool[Math.floor(Math.random() * pool.length)];
}

function playMotionOption(pixi: any, model: any, option: Live2DControlOption, priority: number): void {
  model.motion(option.group ?? option.name, option.index ?? 0, priority);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function setModelParameter(model: any, ids: string[], value: number): boolean {
  const targets = [
    model?.internalModel?.coreModel,
    model?.internalModel,
    model?.coreModel,
    model
  ].filter(Boolean);

  for (const target of targets) {
    for (const id of ids) {
      try {
        if (typeof target.setParameterValueById === "function") {
          target.setParameterValueById(id, value);
          return true;
        }
        if (typeof target.setParamFloat === "function") {
          target.setParamFloat(id, value);
          return true;
        }
      } catch {
        // Imported models can expose only one runtime-specific parameter API.
      }
    }
  }
  return false;
}

function normalizeTransform(transform: Live2DModelTransform): Live2DModelTransform {
  return {
    offsetX: clamp(transform.offsetX, MIN_MODEL_OFFSET, MAX_MODEL_OFFSET),
    offsetY: clamp(transform.offsetY, MIN_MODEL_OFFSET, MAX_MODEL_OFFSET),
    scale: clamp(transform.scale, MIN_MODEL_SCALE, MAX_MODEL_SCALE)
  };
}

export const Live2DStage = forwardRef<Live2DStageHandle, Live2DStageProps>(
  function Live2DStage({ model: modelRecord, transformLocked, speaking, onReady, onError, onTransformChange }, ref) {
    const hostRef = useRef<HTMLDivElement | null>(null);
    const appRef = useRef<any>(null);
    const modelRef = useRef<any>(null);
    const baseFitRef = useRef<{ x: number; y: number; scale: number; width: number; height: number } | null>(null);
    const transformRef = useRef<Live2DModelTransform>(normalizeTransform(modelRecord.transform));
    const mouthOpenRef = useRef(0);
    const mouthOverrideActiveRef = useRef(false);
    const mouthTickerRef = useRef<(() => void) | null>(null);
    const dragRef = useRef<{
      pointerId: number;
      startX: number;
      startY: number;
      transform: Live2DModelTransform;
    } | null>(null);
    const modelRecordRef = useRef(modelRecord);
    const onReadyRef = useRef(onReady);
    const onErrorRef = useRef(onError);
    const onTransformChangeRef = useRef(onTransformChange);
    const transformCommitTimerRef = useRef<number | null>(null);
    const [status, setStatus] = useState("loading");
    const modelId = modelRecord.id;
    const modelUrl = modelRecord.modelUrl;
    const modelTransform = modelRecord.transform;

    modelRecordRef.current = modelRecord;

    useEffect(() => {
      onReadyRef.current = onReady;
      onErrorRef.current = onError;
      onTransformChangeRef.current = onTransformChange;
    }, [onReady, onError, onTransformChange]);

    function applyModelTransform() {
      const live2dModel = modelRef.current;
      const base = baseFitRef.current;
      if (!live2dModel || !base) {
        return;
      }
      const transform = normalizeTransform(transformRef.current);
      live2dModel.scale.set(base.scale * transform.scale);
      live2dModel.position.set(base.x + base.width * transform.offsetX, base.y + base.height * transform.offsetY);
    }

    function applyMouthOpen(value = mouthOpenRef.current) {
      const live2dModel = modelRef.current;
      if (!live2dModel) {
        return;
      }
      setModelParameter(live2dModel, MOUTH_OPEN_PARAMETER_IDS, clamp(value, 0, 1));
    }

    function emitTransformChange(transform: Live2DModelTransform, commitDelay = 0) {
      const next = normalizeTransform(transform);
      transformRef.current = next;
      applyModelTransform();
      if (transformCommitTimerRef.current) {
        window.clearTimeout(transformCommitTimerRef.current);
        transformCommitTimerRef.current = null;
      }
      if (commitDelay > 0) {
        transformCommitTimerRef.current = window.setTimeout(() => {
          onTransformChangeRef.current?.(next);
          transformCommitTimerRef.current = null;
        }, commitDelay);
        return;
      }
      onTransformChangeRef.current?.(next);
    }

    useEffect(() => {
      transformRef.current = normalizeTransform(modelTransform);
      applyModelTransform();
    }, [modelTransform]);

    useImperativeHandle(ref, () => ({
      playEmotion(emotion: string) {
        const live2dModel = modelRef.current;
        const pixi = window.PIXI;
        if (!live2dModel || !pixi?.live2d) {
          return;
        }
        const motion = findMotionOption(modelRecord, emotion);
        if (motion) {
          try {
            playMotionOption(pixi, live2dModel, motion, pixi.live2d.MotionPriority.NORMAL);
          } catch {
            // Expressions can still be available even if a motion group fails.
          }
        }
        const expression = findExpressionOption(modelRecord, emotion);
        if (expression) {
          try {
            live2dModel.expression(expression.name);
          } catch {
            return;
          }
        }
      },
      playExpression(expressionName: string) {
        const live2dModel = modelRef.current;
        const pixi = window.PIXI;
        if (!live2dModel || !pixi?.live2d) {
          return;
        }
        const expression = findExpressionOption(modelRecord, expressionName);
        if (!expression) {
          return;
        }
        try {
          live2dModel.expression(expression.name);
        } catch {
          return;
        }
      },
      playMotion(group: string, index?: number) {
        const live2dModel = modelRef.current;
        const pixi = window.PIXI;
        if (!live2dModel || !pixi?.live2d) {
          return;
        }
        const motion = findMotionOption(modelRecord, group, index);
        if (!motion) {
          return;
        }
        try {
          playMotionOption(pixi, live2dModel, motion, pixi.live2d.MotionPriority.NORMAL);
        } catch {
          return;
        }
      },
      resetMouth() {
        mouthOpenRef.current = 0;
        mouthOverrideActiveRef.current = false;
        applyMouthOpen(0);
      },
      setMouthOpen(value: number) {
        mouthOpenRef.current = clamp(value, 0, 1);
        mouthOverrideActiveRef.current = true;
        applyMouthOpen();
      }
    }), [modelRecord]);

    useEffect(() => {
      let disposed = false;
      let resizeObserver: ResizeObserver | null = null;
      let idleTimer = 0;

      async function boot() {
        setStatus("loading");
        if (!modelUrl) {
          throw new Error("Live2D model URL is empty.");
        }
        await loadLive2DScripts();
        if (disposed || !hostRef.current) {
          return;
        }
        const pixi = window.PIXI;
        if (!pixi?.live2d?.Live2DModel) {
          throw new Error("Live2D runtime is not available.");
        }

        pixi.live2d.config.sound = false;
        pixi.live2d.config.logLevel = pixi.live2d.config.LOG_LEVEL_ERROR;

        const host = hostRef.current;
        const width = Math.max(host.clientWidth, 320);
        const height = Math.max(host.clientHeight, 420);
        const app = new pixi.Application({
          backgroundAlpha: 0,
          antialias: true,
          autoStart: true,
          width,
          height
        });
        app.view.className = "live2d-canvas";
        host.appendChild(app.view);
        appRef.current = app;

        const model = await pixi.live2d.Live2DModel.from(modelUrl, {
          autoInteract: true,
          motionPreload: pixi.live2d.MotionPreloadStrategy.NONE
        });
        if (disposed) {
          model.destroy?.();
          return;
        }
        modelRef.current = model;
        app.stage.addChild(model);
        const applyMouthOnTick = () => {
          if (mouthOverrideActiveRef.current) {
            applyMouthOpen();
          }
        };
        mouthTickerRef.current = applyMouthOnTick;
        app.ticker.add(applyMouthOnTick);

        const fitModel = () => {
          if (!hostRef.current || !appRef.current || !modelRef.current) {
            return;
          }
          const nextWidth = Math.max(hostRef.current.clientWidth, 320);
          const nextHeight = Math.max(hostRef.current.clientHeight, 420);
          appRef.current.renderer.resize(nextWidth, nextHeight);
          const sourceWidth = Math.max(model.width / Math.max(model.scale.x, 0.0001), 1);
          const sourceHeight = Math.max(model.height / Math.max(model.scale.y, 0.0001), 1);
          const scale = Math.min((nextWidth / sourceWidth) * 0.88, (nextHeight / sourceHeight) * 0.94);

          if (model.anchor) {
            model.anchor.set(0.5, 0.5);
          } else {
            const bounds = model.getLocalBounds();
            model.pivot.set(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
          }
          baseFitRef.current = {
            x: nextWidth * 0.5,
            y: nextHeight * 0.52,
            scale,
            width: nextWidth,
            height: nextHeight
          };
          applyModelTransform();
        };

        fitModel();
        resizeObserver = new ResizeObserver(fitModel);
        resizeObserver.observe(host);
        idleTimer = window.setInterval(() => {
          const motion = pickIdleMotion(modelRecordRef.current);
          if (!motion) {
            return;
          }
          try {
            playMotionOption(pixi, model, motion, pixi.live2d.MotionPriority.IDLE);
          } catch {
            return;
          }
        }, 9000);
        setStatus("ready");
        onReadyRef.current?.();
      }

      boot().catch((error) => {
        if (!disposed) {
          const message = error instanceof Error ? error.message : "Live2D failed";
          setStatus(message);
          onErrorRef.current?.(message);
        }
      });

      return () => {
        disposed = true;
        if (idleTimer) {
          window.clearInterval(idleTimer);
        }
        resizeObserver?.disconnect();
        modelRef.current = null;
        baseFitRef.current = null;
        dragRef.current = null;
        mouthOpenRef.current = 0;
        mouthOverrideActiveRef.current = false;
        if (transformCommitTimerRef.current) {
          window.clearTimeout(transformCommitTimerRef.current);
          transformCommitTimerRef.current = null;
        }
        if (appRef.current) {
          if (mouthTickerRef.current) {
            appRef.current.ticker?.remove?.(mouthTickerRef.current);
            mouthTickerRef.current = null;
          }
          try {
            appRef.current.destroy(true, { children: true });
          } catch {
            appRef.current.destroy?.();
          }
          appRef.current = null;
        }
      };
    }, [modelId, modelUrl]);

    function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
      if (transformLocked || event.button !== 0 || !modelRef.current || !baseFitRef.current) {
        return;
      }
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        transform: transformRef.current
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    }

    function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
      const drag = dragRef.current;
      const base = baseFitRef.current;
      if (transformLocked || !drag || drag.pointerId !== event.pointerId || !base) {
        return;
      }
      emitTransformChange(
        {
          ...drag.transform,
          offsetX: drag.transform.offsetX + (event.clientX - drag.startX) / base.width,
          offsetY: drag.transform.offsetY + (event.clientY - drag.startY) / base.height
        },
        160
      );
    }

    function handlePointerEnd(event: React.PointerEvent<HTMLDivElement>) {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) {
        return;
      }
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      emitTransformChange(transformRef.current);
    }

    function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
      if (transformLocked || !modelRef.current || !baseFitRef.current) {
        return;
      }
      const factor = Math.exp(-event.deltaY * 0.0012);
      emitTransformChange(
        {
          ...transformRef.current,
          scale: transformRef.current.scale * factor
        },
        260
      );
    }

    return (
      <div
        className={`live2d-stage ${speaking ? "is-speaking" : ""} ${transformLocked ? "is-transform-locked" : ""}`}
        ref={hostRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onWheel={handleWheel}
      >
        {status !== "ready" && <div className="live2d-status">{status}</div>}
      </div>
    );
  }
);
