import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState
} from "react";

declare global {
  interface Window {
    PIXI?: any;
  }
}

export interface Live2DStageHandle {
  playEmotion: (emotion: string) => void;
}

export interface Live2DStageProps {
  speaking: boolean;
  onReady?: () => void;
  onError?: (message: string) => void;
}

const LIVE2D_SCRIPTS = [
  "/utils/live2d/pixi.min.js",
  "/utils/live2d/live2dcubismcore.min.js",
  "/utils/live2d/live2d.min.js",
  "/utils/live2d/index.min.js"
];

const MODEL_URL = "/live2dmodels/steinsGateKurisuNew/kurisu.model3.json";
const RANDOM_MOTIONS = ["neutral", "random1", "random2", "random3", "random4", "random5"];
const EMOTION_ALIASES: Record<string, string> = {
  smile: "smile1"
};

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

function normalizeEmotion(emotion: string): string {
  return EMOTION_ALIASES[emotion] ?? emotion;
}

export const Live2DStage = forwardRef<Live2DStageHandle, Live2DStageProps>(
  function Live2DStage({ speaking, onReady, onError }, ref) {
    const hostRef = useRef<HTMLDivElement | null>(null);
    const appRef = useRef<any>(null);
    const modelRef = useRef<any>(null);
    const onReadyRef = useRef(onReady);
    const onErrorRef = useRef(onError);
    const [status, setStatus] = useState("loading");

    useEffect(() => {
      onReadyRef.current = onReady;
      onErrorRef.current = onError;
    }, [onReady, onError]);

    useImperativeHandle(ref, () => ({
      playEmotion(emotion: string) {
        const model = modelRef.current;
        const pixi = window.PIXI;
        if (!model || !pixi?.live2d) {
          return;
        }
        const normalized = normalizeEmotion(emotion);
        try {
          model.motion(normalized, 0, pixi.live2d.MotionPriority.NORMAL);
          model.expression(normalized);
        } catch {
          return;
        }
      }
    }));

    useEffect(() => {
      let disposed = false;
      let resizeObserver: ResizeObserver | null = null;
      let idleTimer = 0;

      async function boot() {
        setStatus("loading");
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

        const model = await pixi.live2d.Live2DModel.from(MODEL_URL, {
          autoInteract: true,
          motionPreload: pixi.live2d.MotionPreloadStrategy.NONE
        });
        if (disposed) {
          model.destroy?.();
          return;
        }
        modelRef.current = model;
        app.stage.addChild(model);

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
            model.position.set(nextWidth * 0.5, nextHeight * 0.52);
          } else {
            const bounds = model.getLocalBounds();
            model.pivot.set(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
            model.position.set(nextWidth * 0.5, nextHeight * 0.52);
          }
          model.scale.set(scale);
        };

        fitModel();
        resizeObserver = new ResizeObserver(fitModel);
        resizeObserver.observe(host);
        idleTimer = window.setInterval(() => {
          const group = RANDOM_MOTIONS[Math.floor(Math.random() * RANDOM_MOTIONS.length)];
          try {
            model.motion(group, 0, pixi.live2d.MotionPriority.IDLE);
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
        if (appRef.current) {
          try {
            appRef.current.destroy(true, { children: true });
          } catch {
            appRef.current.destroy?.();
          }
          appRef.current = null;
        }
      };
    }, []);

    return (
      <div className={`live2d-stage ${speaking ? "is-speaking" : ""}`} ref={hostRef}>
        {status !== "ready" && <div className="live2d-status">{status}</div>}
      </div>
    );
  }
);
