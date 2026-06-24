export type Live2DRuntime = "cubism2" | "cubism3";
export type Live2DModelSource = "default" | "indexeddb";

export interface Live2DControlOption {
  id: string;
  name: string;
  label: string;
  file?: string;
  group?: string;
  index?: number;
}

export interface Live2DModelTransform {
  offsetX: number;
  offsetY: number;
  scale: number;
}

export interface Live2DModelRecord {
  id: string;
  name: string;
  source: Live2DModelSource;
  runtime: Live2DRuntime;
  createdAt: string;
  updatedAt: string;
  rootPath: string;
  modelFile: string;
  modelBaseDir: string;
  fileCount: number;
  textureCount: number;
  expressions: Live2DControlOption[];
  motions: Live2DControlOption[];
  idleMotionGroups: string[];
  warnings: string[];
  transform: Live2DModelTransform;
  modelUrl: string;
  manifest?: unknown;
  objectUrls?: string[];
}

export interface Live2DImportResult {
  record: Live2DModelRecord;
  filesPersisted: number;
  diagnostics: string[];
}

type PersistedLive2DModelRecord = Omit<Live2DModelRecord, "modelUrl" | "objectUrls">;

interface Live2DFileEntry {
  path: string;
  blob: Blob;
  type: string;
  size: number;
  lastModified: number;
}

interface StoredLive2DFile extends Live2DFileEntry {
  key: string;
  modelId: string;
}

interface FileIndex {
  entries: Live2DFileEntry[];
  byPath: Map<string, Live2DFileEntry>;
  byLowerPath: Map<string, Live2DFileEntry>;
}

interface ModelCandidate {
  path: string;
  baseDir: string;
  runtime: Live2DRuntime;
  manifest: Record<string, unknown>;
  score: number;
}

interface AnalyzedLive2DModel {
  name: string;
  runtime: Live2DRuntime;
  rootPath: string;
  modelFile: string;
  modelBaseDir: string;
  fileCount: number;
  textureCount: number;
  expressions: Live2DControlOption[];
  motions: Live2DControlOption[];
  idleMotionGroups: string[];
  warnings: string[];
  manifest: unknown;
}

const DB_NAME = "amadeus-live2d-import-agent-v1";
const DB_VERSION = 1;
const MODEL_STORE = "models";
const FILE_STORE = "files";
const DEFAULT_CREATED_AT = "2026-06-01T00:00:00.000Z";
const DEFAULT_MODEL_URL = "/live2dmodels/steinsGateKurisuNew/kurisu.model3.json";
export const DEFAULT_LIVE2D_TRANSFORM: Live2DModelTransform = {
  offsetX: 0,
  offsetY: 0,
  scale: 1
};

const DEFAULT_EXPRESSIONS = [
  "neutral",
  "anger",
  "joy",
  "sadness",
  "shy",
  "shy2",
  "smile1",
  "smile2",
  "surprise",
  "unhappy"
];
const DEFAULT_MOTION_GROUPS = [
  "neutral",
  "anger",
  "joy",
  "sadness",
  "shy",
  "shy2",
  "smile1",
  "smile2",
  "surprise",
  "unhappy",
  "random1",
  "random2",
  "random3",
  "random4",
  "random5"
];

export const DEFAULT_LIVE2D_MODEL: Live2DModelRecord = {
  id: "default-kurisu",
  name: "牧濑红莉栖",
  source: "default",
  runtime: "cubism3",
  createdAt: DEFAULT_CREATED_AT,
  updatedAt: DEFAULT_CREATED_AT,
  rootPath: "public/live2dmodels/steinsGateKurisuNew",
  modelFile: "kurisu.model3.json",
  modelBaseDir: "",
  fileCount: 31,
  textureCount: 1,
  expressions: DEFAULT_EXPRESSIONS.map((name, index) => createExpressionOption(name, `${name}.exp3.json`, index)),
  motions: DEFAULT_MOTION_GROUPS.map((group, index) => createMotionOption(group, 0, `${group}.motion3.json`, index)),
  idleMotionGroups: ["neutral", "random1", "random2", "random3", "random4", "random5"],
  warnings: [],
  transform: DEFAULT_LIVE2D_TRANSFORM,
  modelUrl: DEFAULT_MODEL_URL
};

export async function importLive2DModelFromFiles(files: File[]): Promise<Live2DImportResult> {
  const entries = normalizeInputFiles(files);
  if (entries.length === 0) {
    throw new Error("没有读取到 Live2D 文件。");
  }

  const analysis = await analyzeLive2DFiles(entries);
  const fingerprint = await createImportFingerprint(analysis, entries);
  const id = `live2d-${fingerprint.slice(0, 18)}`;
  const now = new Date().toISOString();
  const previous = await getStoredModel(id);
  const record: Live2DModelRecord = {
    id,
    source: "indexeddb",
    createdAt: previous?.createdAt ?? now,
    updatedAt: now,
    transform: previous?.transform ? normalizeLive2DTransform(previous.transform) : DEFAULT_LIVE2D_TRANSFORM,
    modelUrl: "",
    ...analysis
  };

  await deleteStoredFiles(id);
  await saveStoredModel(record, entries);
  return {
    record: await activateLive2DModel(record),
    filesPersisted: entries.length,
    diagnostics: analysis.warnings
  };
}

export async function listLive2DModelHistory(): Promise<Live2DModelRecord[]> {
  const imported = await listStoredModels();
  imported.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  return [
    DEFAULT_LIVE2D_MODEL,
    ...imported.map((record) => ({
      ...record,
      transform: normalizeLive2DTransform(record.transform),
      modelUrl: ""
    }))
  ];
}

export async function activateLive2DModel(record: Live2DModelRecord): Promise<Live2DModelRecord> {
  if (record.source === "default") {
    return { ...DEFAULT_LIVE2D_MODEL };
  }
  if (!record.manifest) {
    throw new Error(`Live2D 历史记录缺少 manifest：${record.name}`);
  }

  const files = await listStoredFiles(record.id);
  if (files.length === 0) {
    throw new Error(`Live2D 历史文件已丢失：${record.name}`);
  }

  const index = createFileIndex(files);
  const objectUrls: string[] = [];
  const assetUrls = await createDataUrlMap(files);
  const createAssetUrl = (path: string): string => {
    const normalized = normalizePath(path).toLowerCase();
    const url = assetUrls.get(normalized);
    if (!url) {
      throw new Error(`Live2D 资源缺失：${path}`);
    }
    return url;
  };

  const runtimeManifest =
    record.runtime === "cubism3"
      ? rewriteCubism3Manifest(record, index, createAssetUrl)
      : rewriteCubism2Manifest(record, index, createAssetUrl);
  const manifestUrl = URL.createObjectURL(new Blob([JSON.stringify(runtimeManifest)], { type: "application/json" }));
  objectUrls.push(manifestUrl);

  return {
    ...record,
    transform: normalizeLive2DTransform(record.transform),
    modelUrl: manifestUrl,
    objectUrls
  };
}

export async function updateLive2DModelTransform(id: string, transform: Live2DModelTransform): Promise<Live2DModelRecord | null> {
  if (id === DEFAULT_LIVE2D_MODEL.id) {
    return null;
  }
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(MODEL_STORE, "readwrite");
    const store = tx.objectStore(MODEL_STORE);
    const current = await requestResult<PersistedLive2DModelRecord | undefined>(store.get(id));
    if (!current) {
      await transactionDone(tx);
      return null;
    }
    const next: PersistedLive2DModelRecord = {
      ...current,
      transform: normalizeLive2DTransform(transform),
      updatedAt: new Date().toISOString()
    };
    store.put(next);
    await transactionDone(tx);
    return {
      ...next,
      modelUrl: ""
    };
  } finally {
    db.close();
  }
}

export async function deleteImportedLive2DModel(id: string): Promise<void> {
  if (id === DEFAULT_LIVE2D_MODEL.id) {
    return;
  }
  await deleteStoredFiles(id);
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(MODEL_STORE, "readwrite");
    tx.objectStore(MODEL_STORE).delete(id);
    await transactionDone(tx);
  } finally {
    db.close();
  }
}

export function releaseLive2DModelRuntime(record?: Live2DModelRecord | null): void {
  record?.objectUrls?.forEach((url) => URL.revokeObjectURL(url));
}

function normalizeInputFiles(files: File[]): Live2DFileEntry[] {
  const seen = new Set<string>();
  return files
    .map((file) => {
      const path = normalizePath(file.webkitRelativePath || file.name);
      return {
        path,
        blob: file,
        type: file.type || guessMimeType(path),
        size: file.size,
        lastModified: file.lastModified || 0
      };
    })
    .filter((entry) => {
      if (!entry.path || seen.has(entry.path.toLowerCase())) {
        return false;
      }
      seen.add(entry.path.toLowerCase());
      return true;
    });
}

async function analyzeLive2DFiles(entries: Live2DFileEntry[]): Promise<AnalyzedLive2DModel> {
  const index = createFileIndex(entries);
  const candidates = await findModelCandidates(entries, index);
  if (candidates.length > 0) {
    const best = candidates.sort((a, b) => b.score - a.score)[0];
    return best.runtime === "cubism3" ? analyzeCubism3Candidate(best, entries, index) : analyzeCubism2Candidate(best, entries, index);
  }
  return synthesizeLooseModel(entries, index);
}

async function findModelCandidates(entries: Live2DFileEntry[], index: FileIndex): Promise<ModelCandidate[]> {
  const preferredJson = entries.filter((entry) => /\.model3?\.json$/i.test(entry.path) || /\.model\.json$/i.test(entry.path));
  const jsonEntries = preferredJson.length > 0 ? preferredJson : entries.filter((entry) => /\.json$/i.test(entry.path));
  const candidates: ModelCandidate[] = [];

  for (const entry of jsonEntries) {
    try {
      const manifest = JSON.parse(await entry.blob.text()) as unknown;
      if (!isRecord(manifest)) {
        continue;
      }
      const runtime = detectRuntime(entry.path, manifest);
      if (!runtime) {
        continue;
      }
      candidates.push({
        path: entry.path,
        baseDir: dirname(entry.path),
        runtime,
        manifest,
        score: scoreCandidate(entry.path, runtime, manifest, index)
      });
    } catch {
      continue;
    }
  }
  return candidates;
}

function detectRuntime(path: string, manifest: Record<string, unknown>): Live2DRuntime | null {
  const fileReferences = getRecord(manifest.FileReferences);
  if (/\.model3\.json$/i.test(path) || asString(fileReferences?.Moc) || Number(manifest.Version) >= 3) {
    return "cubism3";
  }
  if (/\.model\.json$/i.test(path) || asString(manifest.model) || Array.isArray(manifest.textures)) {
    return "cubism2";
  }
  return null;
}

function scoreCandidate(path: string, runtime: Live2DRuntime, manifest: Record<string, unknown>, index: FileIndex): number {
  const baseDir = dirname(path);
  let score = runtime === "cubism3" ? 1000 : 900;
  score -= path.split("/").length * 2;
  if (/backup|old|unused/i.test(path)) {
    score -= 120;
  }

  if (runtime === "cubism3") {
    const refs = getRecord(manifest.FileReferences);
    if (resolveReference(asString(refs?.Moc), baseDir, index)) {
      score += 120;
    }
    score += asArray(refs?.Textures).filter((ref) => resolveReference(asString(ref), baseDir, index)).length * 20;
    score += asArray(refs?.Expressions).length * 3;
    const motions = getRecord(refs?.Motions);
    score += motions ? Object.keys(motions).length * 5 : 0;
    return score;
  }

  if (resolveReference(asString(manifest.model), baseDir, index)) {
    score += 120;
  }
  score += asArray(manifest.textures).filter((ref) => resolveReference(asString(ref), baseDir, index)).length * 20;
  score += asArray(manifest.expressions).length * 3;
  const motions = getRecord(manifest.motions);
  score += motions ? Object.keys(motions).length * 5 : 0;
  return score;
}

function analyzeCubism3Candidate(
  candidate: ModelCandidate,
  entries: Live2DFileEntry[],
  index: FileIndex
): AnalyzedLive2DModel {
  const warnings: string[] = [];
  const original = cloneJson(candidate.manifest);
  const fileReferences = getRecord(original.FileReferences) ?? {};
  const mocPath = resolveReference(asString(fileReferences.Moc), candidate.baseDir, index);
  if (!mocPath) {
    throw new Error(`未找到 moc3 文件：${candidate.path}`);
  }

  const texturePaths = resolveManyReferences(asArray(fileReferences.Textures), candidate.baseDir, index, warnings, "texture");
  if (texturePaths.length === 0) {
    texturePaths.push(...findTexturePaths(entries, candidate.baseDir));
  }
  if (texturePaths.length === 0) {
    throw new Error(`未找到贴图文件：${candidate.path}`);
  }

  const expressionDrafts = normalizeCubism3Expressions(fileReferences.Expressions, candidate.baseDir, index, warnings);
  appendScannedExpressions(expressionDrafts, entries, candidate.baseDir, ".exp3.json");

  const motionDrafts = normalizeCubism3Motions(fileReferences.Motions, candidate.baseDir, index, warnings);
  appendScannedMotions(motionDrafts, entries, candidate.baseDir, ".motion3.json");

  const physicsPath = resolveOptionalReference(asString(fileReferences.Physics), candidate.baseDir, index, warnings, "physics");
  const displayInfoPath = resolveOptionalReference(asString(fileReferences.DisplayInfo), candidate.baseDir, index, warnings, "display info");
  const expressions = expressionDrafts.map((item, itemIndex) => createExpressionOption(item.name, item.file, itemIndex));
  const motions = motionDrafts.map((item, itemIndex) => createMotionOption(item.group, item.index, item.file, itemIndex));
  const manifest: Record<string, unknown> = {
    ...original,
    Version: Number(original.Version) || 3,
    FileReferences: {
      ...fileReferences,
      Moc: toManifestRef(mocPath, candidate.baseDir),
      Textures: texturePaths.map((path) => toManifestRef(path, candidate.baseDir)),
      Expressions: expressionDrafts.map((item) => ({
        Name: item.name,
        File: toManifestRef(item.file, candidate.baseDir)
      })),
      Motions: buildCubism3MotionManifest(motionDrafts, candidate.baseDir)
    }
  };
  const nextRefs = getRecord(manifest.FileReferences);
  if (nextRefs) {
    if (physicsPath) {
      nextRefs.Physics = toManifestRef(physicsPath, candidate.baseDir);
    } else {
      delete nextRefs.Physics;
    }
    if (displayInfoPath) {
      nextRefs.DisplayInfo = toManifestRef(displayInfoPath, candidate.baseDir);
    } else {
      delete nextRefs.DisplayInfo;
    }
  }

  return {
    name: inferModelName(candidate.path, entries),
    runtime: "cubism3",
    rootPath: inferRootPath(entries),
    modelFile: candidate.path,
    modelBaseDir: candidate.baseDir,
    fileCount: entries.length,
    textureCount: texturePaths.length,
    expressions,
    motions,
    idleMotionGroups: inferIdleMotionGroups(motions),
    warnings,
    manifest
  };
}

function analyzeCubism2Candidate(
  candidate: ModelCandidate,
  entries: Live2DFileEntry[],
  index: FileIndex
): AnalyzedLive2DModel {
  const warnings: string[] = [];
  const original = cloneJson(candidate.manifest);
  const mocPath = resolveReference(asString(original.model), candidate.baseDir, index);
  if (!mocPath) {
    throw new Error(`未找到 moc 文件：${candidate.path}`);
  }

  const texturePaths = resolveManyReferences(asArray(original.textures), candidate.baseDir, index, warnings, "texture");
  if (texturePaths.length === 0) {
    texturePaths.push(...findTexturePaths(entries, candidate.baseDir));
  }
  if (texturePaths.length === 0) {
    throw new Error(`未找到贴图文件：${candidate.path}`);
  }

  const expressionDrafts = normalizeCubism2Expressions(original.expressions, candidate.baseDir, index, warnings);
  appendScannedExpressions(expressionDrafts, entries, candidate.baseDir, ".exp.json");

  const motionDrafts = normalizeCubism2Motions(original.motions, candidate.baseDir, index, warnings);
  appendScannedMotions(motionDrafts, entries, candidate.baseDir, ".mtn");

  const physicsPath = resolveOptionalReference(asString(original.physics), candidate.baseDir, index, warnings, "physics");
  const posePath = resolveOptionalReference(asString(original.pose), candidate.baseDir, index, warnings, "pose");
  const expressions = expressionDrafts.map((item, itemIndex) => createExpressionOption(item.name, item.file, itemIndex));
  const motions = motionDrafts.map((item, itemIndex) => createMotionOption(item.group, item.index, item.file, itemIndex));
  const manifest: Record<string, unknown> = {
    ...original,
    model: toManifestRef(mocPath, candidate.baseDir),
    textures: texturePaths.map((path) => toManifestRef(path, candidate.baseDir)),
    expressions: expressionDrafts.map((item) => ({
      name: item.name,
      file: toManifestRef(item.file, candidate.baseDir)
    })),
    motions: buildCubism2MotionManifest(motionDrafts, candidate.baseDir)
  };
  if (physicsPath) {
    manifest.physics = toManifestRef(physicsPath, candidate.baseDir);
  } else {
    delete manifest.physics;
  }
  if (posePath) {
    manifest.pose = toManifestRef(posePath, candidate.baseDir);
  } else {
    delete manifest.pose;
  }

  return {
    name: inferModelName(candidate.path, entries),
    runtime: "cubism2",
    rootPath: inferRootPath(entries),
    modelFile: candidate.path,
    modelBaseDir: candidate.baseDir,
    fileCount: entries.length,
    textureCount: texturePaths.length,
    expressions,
    motions,
    idleMotionGroups: inferIdleMotionGroups(motions),
    warnings,
    manifest
  };
}

function synthesizeLooseModel(entries: Live2DFileEntry[], _index: FileIndex): AnalyzedLive2DModel {
  const warnings = ["未找到 model.json/model3.json，已按目录内 moc/moc3、贴图、表情和动作文件生成临时 manifest。"];
  const moc3 = entries.find((entry) => /\.moc3$/i.test(entry.path));
  const moc2 = entries.find((entry) => /\.moc$/i.test(entry.path));
  const moc = moc3 ?? moc2;
  if (!moc) {
    throw new Error("未找到 .moc3 或 .moc 文件。");
  }
  const runtime: Live2DRuntime = moc3 ? "cubism3" : "cubism2";
  const baseDir = dirname(moc.path);
  const texturePaths = findTexturePaths(entries, baseDir);
  if (texturePaths.length === 0) {
    throw new Error("未找到 Live2D 贴图文件。");
  }

  const expressionDrafts: ExpressionDraft[] = [];
  appendScannedExpressions(expressionDrafts, entries, baseDir, runtime === "cubism3" ? ".exp3.json" : ".exp.json");
  const motionDrafts: MotionDraft[] = [];
  appendScannedMotions(motionDrafts, entries, baseDir, runtime === "cubism3" ? ".motion3.json" : ".mtn");

  const expressions = expressionDrafts.map((item, itemIndex) => createExpressionOption(item.name, item.file, itemIndex));
  const motions = motionDrafts.map((item, itemIndex) => createMotionOption(item.group, item.index, item.file, itemIndex));
  const manifest =
    runtime === "cubism3"
      ? {
          Version: 3,
          FileReferences: {
            Moc: toManifestRef(moc.path, baseDir),
            Textures: texturePaths.map((path) => toManifestRef(path, baseDir)),
            Expressions: expressionDrafts.map((item) => ({
              Name: item.name,
              File: toManifestRef(item.file, baseDir)
            })),
            Motions: buildCubism3MotionManifest(motionDrafts, baseDir)
          }
        }
      : {
          version: "Live2DViewerEX Config 1.0",
          model: toManifestRef(moc.path, baseDir),
          textures: texturePaths.map((path) => toManifestRef(path, baseDir)),
          expressions: expressionDrafts.map((item) => ({
            name: item.name,
            file: toManifestRef(item.file, baseDir)
          })),
          motions: buildCubism2MotionManifest(motionDrafts, baseDir)
        };

  return {
    name: inferModelName(moc.path, entries),
    runtime,
    rootPath: inferRootPath(entries),
    modelFile: runtime === "cubism3" ? "generated.model3.json" : "generated.model.json",
    modelBaseDir: baseDir,
    fileCount: entries.length,
    textureCount: texturePaths.length,
    expressions,
    motions,
    idleMotionGroups: inferIdleMotionGroups(motions),
    warnings,
    manifest
  };
}

interface ExpressionDraft {
  name: string;
  file: string;
}

interface MotionDraft {
  group: string;
  index: number;
  file: string;
}

function normalizeCubism3Expressions(
  value: unknown,
  baseDir: string,
  index: FileIndex,
  warnings: string[]
): ExpressionDraft[] {
  const expressions: ExpressionDraft[] = [];
  for (const [itemIndex, item] of asArray(value).entries()) {
    const record = getRecord(item);
    const file = resolveOptionalReference(asString(record?.File), baseDir, index, warnings, "expression");
    if (!file) {
      continue;
    }
    expressions.push({
      name: asString(record?.Name) || stripLive2DExtension(basename(file)) || `expression-${itemIndex + 1}`,
      file
    });
  }
  return expressions;
}

function normalizeCubism2Expressions(
  value: unknown,
  baseDir: string,
  index: FileIndex,
  warnings: string[]
): ExpressionDraft[] {
  const expressions: ExpressionDraft[] = [];
  for (const [itemIndex, item] of asArray(value).entries()) {
    const record = getRecord(item);
    const file = resolveOptionalReference(asString(record?.file), baseDir, index, warnings, "expression");
    if (!file) {
      continue;
    }
    expressions.push({
      name: asString(record?.name) || basename(file) || `expression-${itemIndex + 1}`,
      file
    });
  }
  return expressions;
}

function normalizeCubism3Motions(value: unknown, baseDir: string, index: FileIndex, warnings: string[]): MotionDraft[] {
  const motions: MotionDraft[] = [];
  const groups = getRecord(value);
  if (!groups) {
    return motions;
  }
  for (const [group, items] of Object.entries(groups)) {
    for (const [itemIndex, item] of asArray(items).entries()) {
      const record = getRecord(item);
      const file = resolveOptionalReference(asString(record?.File), baseDir, index, warnings, "motion");
      if (!file) {
        continue;
      }
      motions.push({ group, index: itemIndex, file });
    }
  }
  return motions;
}

function normalizeCubism2Motions(value: unknown, baseDir: string, index: FileIndex, warnings: string[]): MotionDraft[] {
  const motions: MotionDraft[] = [];
  const groups = getRecord(value);
  if (!groups) {
    return motions;
  }
  for (const [group, items] of Object.entries(groups)) {
    for (const [itemIndex, item] of asArray(items).entries()) {
      const record = getRecord(item);
      const file = resolveOptionalReference(asString(record?.file), baseDir, index, warnings, "motion");
      if (!file) {
        continue;
      }
      motions.push({ group, index: itemIndex, file });
    }
  }
  return motions;
}

function appendScannedExpressions(expressions: ExpressionDraft[], entries: Live2DFileEntry[], baseDir: string, extension: string) {
  const seenFiles = new Set(expressions.map((item) => item.file.toLowerCase()));
  const extensionLower = extension.toLowerCase();
  const scanned = entries
    .filter((entry) => entry.path.toLowerCase().endsWith(extensionLower))
    .sort((a, b) => naturalCompare(a.path, b.path));

  for (const entry of scanned) {
    if (seenFiles.has(entry.path.toLowerCase())) {
      continue;
    }
    expressions.push({
      name: stripLive2DExtension(basename(entry.path)) || basename(entry.path),
      file: entry.path
    });
    seenFiles.add(entry.path.toLowerCase());
  }
}

function appendScannedMotions(motions: MotionDraft[], entries: Live2DFileEntry[], baseDir: string, extension: string) {
  const seenFiles = new Set(motions.map((item) => item.file.toLowerCase()));
  const counts = new Map<string, number>();
  for (const motion of motions) {
    counts.set(motion.group, Math.max(counts.get(motion.group) ?? 0, motion.index + 1));
  }

  const extensionLower = extension.toLowerCase();
  const scanned = entries
    .filter((entry) => entry.path.toLowerCase().endsWith(extensionLower))
    .sort((a, b) => naturalCompare(a.path, b.path));

  for (const entry of scanned) {
    if (seenFiles.has(entry.path.toLowerCase())) {
      continue;
    }
    const group = inferMotionGroup(entry.path, baseDir);
    const nextIndex = counts.get(group) ?? 0;
    motions.push({
      group,
      index: nextIndex,
      file: entry.path
    });
    counts.set(group, nextIndex + 1);
    seenFiles.add(entry.path.toLowerCase());
  }
}

function buildCubism3MotionManifest(motions: MotionDraft[], baseDir: string): Record<string, Array<Record<string, string>>> {
  const grouped: Record<string, Array<Record<string, string>>> = {};
  for (const motion of motions) {
    if (!grouped[motion.group]) {
      grouped[motion.group] = [];
    }
    grouped[motion.group][motion.index] = {
      File: toManifestRef(motion.file, baseDir)
    };
  }
  return compactMotionGroups(grouped);
}

function buildCubism2MotionManifest(motions: MotionDraft[], baseDir: string): Record<string, Array<Record<string, string>>> {
  const grouped: Record<string, Array<Record<string, string>>> = {};
  for (const motion of motions) {
    if (!grouped[motion.group]) {
      grouped[motion.group] = [];
    }
    grouped[motion.group][motion.index] = {
      file: toManifestRef(motion.file, baseDir)
    };
  }
  return compactMotionGroups(grouped);
}

function compactMotionGroups(
  groups: Record<string, Array<Record<string, string> | undefined>>
): Record<string, Array<Record<string, string>>> {
  return Object.fromEntries(
    Object.entries(groups).map(([group, items]) => [group, items.filter((item): item is Record<string, string> => Boolean(item))])
  );
}

function rewriteCubism3Manifest(
  record: Live2DModelRecord,
  index: FileIndex,
  createAssetUrl: (path: string) => string
): Record<string, unknown> {
  const manifest = cloneJson(record.manifest) as Record<string, unknown>;
  const refs = getRecord(manifest.FileReferences);
  if (!refs) {
    throw new Error("Cubism3 manifest 缺少 FileReferences。");
  }
  refs.Moc = rewriteRequiredRef(asString(refs.Moc), record.modelBaseDir, index, createAssetUrl);
  refs.Textures = asArray(refs.Textures).map((ref) => rewriteRequiredRef(asString(ref), record.modelBaseDir, index, createAssetUrl));
  if (asArray(refs.Expressions).length > 0) {
    refs.Expressions = asArray(refs.Expressions).map((item) => {
      const expression = { ...(getRecord(item) ?? {}) };
      expression.File = rewriteRequiredRef(asString(expression.File), record.modelBaseDir, index, createAssetUrl);
      return expression;
    });
  }
  const motionGroups = getRecord(refs.Motions);
  if (motionGroups) {
    refs.Motions = Object.fromEntries(
      Object.entries(motionGroups).map(([group, items]) => [
        group,
        asArray(items).map((item) => {
          const motion = { ...(getRecord(item) ?? {}) };
          motion.File = rewriteRequiredRef(asString(motion.File), record.modelBaseDir, index, createAssetUrl);
          const sound = resolveReference(asString(motion.Sound), record.modelBaseDir, index);
          if (sound) {
            motion.Sound = createAssetUrl(sound);
          } else {
            delete motion.Sound;
          }
          return motion;
        })
      ])
    );
  }
  const physics = resolveReference(asString(refs.Physics), record.modelBaseDir, index);
  if (physics) {
    refs.Physics = createAssetUrl(physics);
  } else {
    delete refs.Physics;
  }
  const displayInfo = resolveReference(asString(refs.DisplayInfo), record.modelBaseDir, index);
  if (displayInfo) {
    refs.DisplayInfo = createAssetUrl(displayInfo);
  } else {
    delete refs.DisplayInfo;
  }
  return manifest;
}

function rewriteCubism2Manifest(
  record: Live2DModelRecord,
  index: FileIndex,
  createAssetUrl: (path: string) => string
): Record<string, unknown> {
  const manifest = cloneJson(record.manifest) as Record<string, unknown>;
  manifest.model = rewriteRequiredRef(asString(manifest.model), record.modelBaseDir, index, createAssetUrl);
  manifest.textures = asArray(manifest.textures).map((ref) =>
    rewriteRequiredRef(asString(ref), record.modelBaseDir, index, createAssetUrl)
  );
  if (asArray(manifest.expressions).length > 0) {
    manifest.expressions = asArray(manifest.expressions).map((item) => {
      const expression = { ...(getRecord(item) ?? {}) };
      expression.file = rewriteRequiredRef(asString(expression.file), record.modelBaseDir, index, createAssetUrl);
      return expression;
    });
  }
  const motionGroups = getRecord(manifest.motions);
  if (motionGroups) {
    manifest.motions = Object.fromEntries(
      Object.entries(motionGroups).map(([group, items]) => [
        group,
        asArray(items).map((item) => {
          const motion = { ...(getRecord(item) ?? {}) };
          motion.file = rewriteRequiredRef(asString(motion.file), record.modelBaseDir, index, createAssetUrl);
          const sound = resolveReference(asString(motion.sound), record.modelBaseDir, index);
          if (sound) {
            motion.sound = createAssetUrl(sound);
          } else {
            delete motion.sound;
          }
          return motion;
        })
      ])
    );
  }
  const physics = resolveReference(asString(manifest.physics), record.modelBaseDir, index);
  if (physics) {
    manifest.physics = createAssetUrl(physics);
  } else {
    delete manifest.physics;
  }
  const pose = resolveReference(asString(manifest.pose), record.modelBaseDir, index);
  if (pose) {
    manifest.pose = createAssetUrl(pose);
  } else {
    delete manifest.pose;
  }
  return manifest;
}

function rewriteRequiredRef(
  ref: string,
  baseDir: string,
  index: FileIndex,
  createAssetUrl: (path: string) => string
): string {
  const resolved = resolveReference(ref, baseDir, index);
  if (!resolved) {
    throw new Error(`Live2D manifest 引用了缺失文件：${ref}`);
  }
  return createAssetUrl(resolved);
}

function resolveManyReferences(
  refs: unknown[],
  baseDir: string,
  index: FileIndex,
  warnings: string[],
  label: string
): string[] {
  return refs
    .map((ref) => resolveOptionalReference(asString(ref), baseDir, index, warnings, label))
    .filter((path): path is string => Boolean(path));
}

function resolveOptionalReference(ref: string, baseDir: string, index: FileIndex, warnings: string[], label: string): string {
  if (!ref) {
    return "";
  }
  const resolved = resolveReference(ref, baseDir, index);
  if (!resolved) {
    warnings.push(`跳过缺失的 ${label}：${ref}`);
  }
  return resolved;
}

function resolveReference(ref: string, baseDir: string, index: FileIndex): string {
  if (!ref) {
    return "";
  }
  const decoded = safeDecode(ref);
  const candidates = [
    joinPath(baseDir, ref),
    joinPath(baseDir, decoded),
    normalizePath(ref),
    normalizePath(decoded),
    normalizePath(ref.replace(/^\.\//, "")),
    normalizePath(decoded.replace(/^\.\//, ""))
  ];

  for (const candidate of candidates) {
    const exact = index.byLowerPath.get(candidate.toLowerCase());
    if (exact) {
      return exact.path;
    }
  }

  const wantedName = safeDecode(basename(ref)).toLowerCase();
  const sameDir = index.entries.find(
    (entry) => dirname(entry.path).toLowerCase() === baseDir.toLowerCase() && safeDecode(basename(entry.path)).toLowerCase() === wantedName
  );
  if (sameDir) {
    return sameDir.path;
  }
  const global = index.entries.find((entry) => safeDecode(basename(entry.path)).toLowerCase() === wantedName);
  return global?.path ?? "";
}

function findTexturePaths(entries: Live2DFileEntry[], _baseDir: string): string[] {
  const images = entries
    .filter((entry) => /\.(?:png|jpe?g|webp)$/i.test(entry.path))
    .sort((a, b) => naturalCompare(a.path, b.path));
  const preferred = images.filter((entry) => /(?:^|\/)(?:textures?|tex|.*\.\d{3,4})(?:\/|$)/i.test(entry.path) || /texture/i.test(entry.path));
  const selected = preferred.length > 0 ? preferred : images.filter((entry) => !/thumb|preview|icon/i.test(entry.path));
  return (selected.length > 0 ? selected : images).map((entry) => entry.path);
}

function inferMotionGroup(path: string, baseDir: string): string {
  const dir = dirname(path);
  const parent = basename(dir);
  if (dir && dir !== baseDir && !/motions?/i.test(parent)) {
    return parent || stripLive2DExtension(basename(path)) || "motion";
  }
  return stripLive2DExtension(basename(path)) || "motion";
}

function inferIdleMotionGroups(motions: Live2DControlOption[]): string[] {
  const groups = unique(motions.map((motion) => motion.group || motion.name).filter(Boolean));
  const preferred = groups.filter((group) => /idle|random|tap|neutral|wait|home|loop/i.test(group));
  return preferred.length > 0 ? preferred : groups.slice(0, 6);
}

function inferModelName(modelPath: string, entries: Live2DFileEntry[]): string {
  const root = inferRootPath(entries);
  const stem = stripLive2DExtension(basename(modelPath));
  const rootName = basename(root);
  const name = /^(model|models|live2d)$/i.test(stem) ? rootName || stem : stem;
  return prettifyLabel(safeDecode(name || rootName || "Live2D"));
}

function inferRootPath(entries: Live2DFileEntry[]): string {
  if (entries.length === 0) {
    return "";
  }
  const segments = entries.map((entry) => entry.path.split("/").filter(Boolean));
  const root: string[] = [];
  const limit = Math.min(...segments.map((item) => item.length));
  for (let index = 0; index < limit; index += 1) {
    const segment = segments[0][index];
    if (segments.every((item) => item[index] === segment)) {
      root.push(segment);
    } else {
      break;
    }
  }
  return root.join("/");
}

function createExpressionOption(name: string, file: string, index: number): Live2DControlOption {
  return {
    id: `expression-${slugify(name)}-${index}`,
    name,
    label: prettifyLabel(stripLive2DExtension(name) || name),
    file
  };
}

function createMotionOption(group: string, index: number, file: string, optionIndex: number): Live2DControlOption {
  const label = `${prettifyLabel(group)}${index > 0 ? ` ${index + 1}` : ""}`;
  return {
    id: `motion-${slugify(group)}-${index}-${optionIndex}`,
    name: group,
    label,
    group,
    index,
    file
  };
}

function toManifestRef(path: string, baseDir: string): string {
  const normalized = normalizePath(path);
  const normalizedBase = normalizePath(baseDir);
  if (normalizedBase && normalized.toLowerCase().startsWith(`${normalizedBase.toLowerCase()}/`)) {
    return normalized.slice(normalizedBase.length + 1);
  }
  return normalized;
}

function createFileIndex(entries: Live2DFileEntry[]): FileIndex {
  return {
    entries,
    byPath: new Map(entries.map((entry) => [entry.path, entry])),
    byLowerPath: new Map(entries.map((entry) => [entry.path.toLowerCase(), entry]))
  };
}

function normalizePath(path: string): string {
  const parts: string[] = [];
  for (const part of path.replace(/\\/g, "/").replace(/^\/+/, "").split("/")) {
    if (!part || part === ".") {
      continue;
    }
    if (part === "..") {
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  return parts.join("/");
}

function joinPath(baseDir: string, ref: string): string {
  const cleanRef = ref.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!baseDir) {
    return normalizePath(cleanRef);
  }
  return normalizePath(`${baseDir}/${cleanRef}`);
}

function dirname(path: string): string {
  const normalized = normalizePath(path);
  const index = normalized.lastIndexOf("/");
  return index >= 0 ? normalized.slice(0, index) : "";
}

function basename(path: string): string {
  const normalized = normalizePath(path);
  return normalized.slice(normalized.lastIndexOf("/") + 1);
}

function stripLive2DExtension(name: string): string {
  return name
    .replace(/\.model3?\.json$/i, "")
    .replace(/\.model\.json$/i, "")
    .replace(/\.motion3\.json$/i, "")
    .replace(/\.exp3\.json$/i, "")
    .replace(/\.exp\.json$/i, "")
    .replace(/\.mtn$/i, "")
    .replace(/\.moc3?$/i, "")
    .replace(/\.[^.]+$/i, "");
}

function prettifyLabel(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || "Live2D";
}

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9\u3400-\u9fff]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "item"
  );
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function guessMimeType(path: string): string {
  if (/\.json$/i.test(path)) {
    return "application/json";
  }
  if (/\.png$/i.test(path)) {
    return "image/png";
  }
  if (/\.jpe?g$/i.test(path)) {
    return "image/jpeg";
  }
  if (/\.webp$/i.test(path)) {
    return "image/webp";
  }
  return "application/octet-stream";
}

function normalizeLive2DTransform(value?: Partial<Live2DModelTransform> | null): Live2DModelTransform {
  return {
    offsetX: clampNumber(value?.offsetX, -0.8, 0.8, DEFAULT_LIVE2D_TRANSFORM.offsetX),
    offsetY: clampNumber(value?.offsetY, -0.8, 0.8, DEFAULT_LIVE2D_TRANSFORM.offsetY),
    scale: clampNumber(value?.scale, 0.35, 2.6, DEFAULT_LIVE2D_TRANSFORM.scale)
  };
}

function clampNumber(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
}

async function createDataUrlMap(entries: Live2DFileEntry[]): Promise<Map<string, string>> {
  const pairs = await Promise.all(
    entries.map(async (entry) => [entry.path.toLowerCase(), await blobToDataUrl(entry.blob, entry.type || guessMimeType(entry.path))] as const)
  );
  return new Map(pairs);
}

function blobToDataUrl(blob: Blob, mimeType: string): Promise<string> {
  const typedBlob = blob.type || !mimeType ? blob : blob.slice(0, blob.size, mimeType);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Live2D 资源转码失败。"));
    reader.readAsDataURL(typedBlob);
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function createImportFingerprint(analysis: AnalyzedLive2DModel, entries: Live2DFileEntry[]): Promise<string> {
  const text = [
    analysis.runtime,
    analysis.modelFile,
    analysis.textureCount,
    ...entries
      .map((entry) => `${entry.path}\t${entry.size}\t${entry.lastModified}`)
      .sort((a, b) => naturalCompare(a, b))
  ].join("\n");

  if (crypto.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

async function openLive2DDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(MODEL_STORE)) {
        db.createObjectStore(MODEL_STORE, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(FILE_STORE)) {
        const store = db.createObjectStore(FILE_STORE, { keyPath: "key" });
        store.createIndex("modelId", "modelId", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Live2D IndexedDB 打开失败。"));
  });
}

async function getStoredModel(id: string): Promise<PersistedLive2DModelRecord | null> {
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(MODEL_STORE, "readonly");
    const result = await requestResult<PersistedLive2DModelRecord | undefined>(tx.objectStore(MODEL_STORE).get(id));
    await transactionDone(tx);
    return result ?? null;
  } finally {
    db.close();
  }
}

async function listStoredModels(): Promise<Live2DModelRecord[]> {
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(MODEL_STORE, "readonly");
    const result = await requestResult<PersistedLive2DModelRecord[]>(tx.objectStore(MODEL_STORE).getAll());
    await transactionDone(tx);
    return result.map((record) => ({
      ...record,
      transform: normalizeLive2DTransform(record.transform),
      modelUrl: ""
    }));
  } finally {
    db.close();
  }
}

async function listStoredFiles(modelId: string): Promise<Live2DFileEntry[]> {
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(FILE_STORE, "readonly");
    const index = tx.objectStore(FILE_STORE).index("modelId");
    const result = await requestResult<StoredLive2DFile[]>(index.getAll(IDBKeyRange.only(modelId)));
    await transactionDone(tx);
    return result.map(({ path, blob, type, size, lastModified }) => ({
      path,
      blob,
      type,
      size,
      lastModified
    }));
  } finally {
    db.close();
  }
}

async function saveStoredModel(record: Live2DModelRecord, entries: Live2DFileEntry[]): Promise<void> {
  const db = await openLive2DDB();
  try {
    const tx = db.transaction([MODEL_STORE, FILE_STORE], "readwrite");
    const modelStore = tx.objectStore(MODEL_STORE);
    const fileStore = tx.objectStore(FILE_STORE);
    modelStore.put(stripRuntimeFields(record));
    for (const entry of entries) {
      const stored: StoredLive2DFile = {
        ...entry,
        key: `${record.id}:${entry.path}`,
        modelId: record.id
      };
      fileStore.put(stored);
    }
    await transactionDone(tx);
  } finally {
    db.close();
  }
}

async function deleteStoredFiles(modelId: string): Promise<void> {
  const db = await openLive2DDB();
  try {
    const tx = db.transaction(FILE_STORE, "readwrite");
    const store = tx.objectStore(FILE_STORE);
    const index = store.index("modelId");
    await new Promise<void>((resolve, reject) => {
      const request = index.openKeyCursor(IDBKeyRange.only(modelId));
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) {
          resolve();
          return;
        }
        store.delete(cursor.primaryKey);
        cursor.continue();
      };
      request.onerror = () => reject(request.error ?? new Error("Live2D 文件删除失败。"));
    });
    await transactionDone(tx);
  } finally {
    db.close();
  }
}

function stripRuntimeFields(record: Live2DModelRecord): PersistedLive2DModelRecord {
  const { modelUrl, objectUrls, ...persisted } = record;
  void modelUrl;
  void objectUrls;
  return persisted;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed."));
  });
}

function transactionDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted."));
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed."));
  });
}
