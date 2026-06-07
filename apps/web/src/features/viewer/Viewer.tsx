import { Suspense, useEffect, useRef, useState, type CSSProperties } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, Bounds, useGLTF, Center, Html } from "@react-three/drei";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Box3, Vector3, type Group, type Mesh, type MeshStandardMaterial, type MeshBasicMaterial, type Camera } from "three";
import clsx from "clsx";
import { getAssetUrl } from "@lib/api";

type ViewerProps = {
  /** Asset id whose `.glb` URL we should resolve via the backend. */
  assetId: string;
  className?: string;
  style?: CSSProperties;
  /** Optional initial background override. Defaults to the theme camera bg. */
  background?: string;
};

/** Resolve a CSS `var(--name)` reference to its computed color, or pass through. */
function resolveCssColor(value: string): string {
  const match = value.match(/^var\((--[a-z0-9-]+)\)$/i);
  const varName = match?.[1];
  if (!varName) return value;
  if (typeof window === "undefined") return value;
  const resolved = getComputedStyle(document.documentElement).getPropertyValue(varName);
  const trimmed = (resolved ?? "").trim();
  return trimmed || value;
}

type DisplayMode = "material" | "wireframe";

/**
 * 3D GLB viewer backed by react-three-fiber.
 *
 * - Fetches the asset URL through the API (which may 302-redirect to MinIO)
 * - Centres & fits the camera via `<Bounds fit>` + `<Center>`
 * - Exposes reset / background / wireframe controls
 * - Shows a spinner during load and an error banner on failure
 */
export function Viewer({ assetId, className, style, background = "var(--camera-bg)" }: ViewerProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("material");
  const [bg, setBg] = useState<string>(background);
  // Three.js Color doesn't understand CSS var() — resolve to a real color and
  // re-resolve when the theme (light/dark class on <html>) changes.
  const [resolvedBg, setResolvedBg] = useState<string>(() => resolveCssColor(background));
  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setUrl(null);
    setError(null);
    getAssetUrl(assetId)
      .then((info) => {
        if (cancelled) return;
        setUrl(info.url);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [assetId]);

  useEffect(() => {
    if (!bg.startsWith("var(")) {
      setResolvedBg(bg);
      return;
    }
    setResolvedBg(resolveCssColor(bg));
    if (typeof window === "undefined") return;
    const observer = new MutationObserver(() => {
      setResolvedBg(resolveCssColor(bg));
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, [bg]);

  return (
    <div className={clsx("relative h-full w-full", className)} style={style} data-testid="viewer">
      {error ? (
        <ErrorBanner message={error} />
      ) : (
        <Canvas
          key={resetKey}
          camera={{ position: [2.5, 2.5, 2.5], fov: 50 }}
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true }}
          data-testid="viewer-canvas"
        >
          <color attach="background" args={[resolvedBg]} />
          <ambientLight intensity={0.5} />
          <directionalLight position={[5, 10, 5]} intensity={1.1} castShadow={false} />
          <Suspense fallback={<Html center><Spinner /></Html>}>
            {url ? (
              <FitOnMount>
                <GLTFModel url={url} displayMode={displayMode} />
              </FitOnMount>
            ) : (
              <Html center>
                <Spinner />
              </Html>
            )}
          </Suspense>
          <OrbitControls makeDefault enableDamping />
        </Canvas>
      )}

      <Controls
        displayMode={displayMode}
        onDisplayModeChange={setDisplayMode}
        bg={bg}
        onBgChange={setBg}
        onReset={() => setResetKey((k) => k + 1)}
      />
    </div>
  );
}

/**
 * Wraps its children in `<Bounds fit>` + `<Center>`. After the model mounts
 * we additionally re-position the camera to frame the geometry.
 */
function FitOnMount({ children }: { children: React.ReactNode }) {
  const groupRef = useRef<Group | null>(null);
  const { camera } = useThree();
  useEffect(() => {
    const handle = window.setTimeout(() => {
      const g = groupRef.current;
      if (!g) return;
      const box = computeGroupBoundingBox(g);
      if (!box) return;
      const size = box.getSize(new Vector3());
      const center = box.getCenter(new Vector3());
      const maxDim = Math.max(size.x, size.y, size.z) || 1;
      const fov = (camera as Camera & { fov: number }).fov ?? 50;
      const dist = (maxDim / 2) / Math.tan(((fov / 2) * Math.PI) / 180);
      camera.position.set(center.x + dist, center.y + dist, center.z + dist);
      camera.lookAt(center);
      camera.updateProjectionMatrix();
    }, 50);
    return () => window.clearTimeout(handle);
  }, [camera, children]);
  return (
    <Bounds fit clip observe margin={1.2}>
      <Center>
        <group ref={groupRef}>{children}</group>
      </Center>
    </Bounds>
  );
}

function computeGroupBoundingBox(group: Group): Box3 | null {
  const box = new Box3();
  let found = false;
  group.traverse((obj) => {
    const mesh = obj as Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    if (!found) {
      box.setFromObject(mesh);
      found = true;
    } else {
      box.expandByObject(mesh);
    }
  });
  return found ? box : null;
}

function GLTFModel({ url, displayMode }: { url: string; displayMode: DisplayMode }) {
  const groupRef = useRef<Group | null>(null);
  // drei's useGLTF preloads and caches; we read its scene to mount into R3F.
  const gltf = useGLTF(url) as unknown as GLTF;
  useEffect(() => {
    if (!groupRef.current) return;
    groupRef.current.traverse((obj) => {
      const mesh = obj as Mesh;
      if (!mesh.isMesh) return;
      const mat = mesh.material as MeshStandardMaterial | MeshBasicMaterial | undefined;
      if (!mat) return;
      mat.wireframe = displayMode === "wireframe";
    });
  }, [gltf, displayMode]);
  return (
    <group ref={groupRef}>
      <primitive object={gltf.scene} />
    </group>
  );
}

function Controls({
  displayMode,
  onDisplayModeChange,
  bg,
  onBgChange,
  onReset,
}: {
  displayMode: DisplayMode;
  onDisplayModeChange: (m: DisplayMode) => void;
  bg: string;
  onBgChange: (c: string) => void;
  onReset: () => void;
}) {
  return (
    <div className="absolute right-2 top-2 flex flex-col gap-1 rounded-md bg-card/70 p-1 backdrop-blur">
      <button
        type="button"
        onClick={onReset}
        className="btn-ghost px-2 py-1 text-xs"
        data-testid="viewer-reset"
        title="重置视角"
      >
        重置
      </button>
      <button
        type="button"
        onClick={() => onDisplayModeChange(displayMode === "material" ? "wireframe" : "material")}
        className="btn-ghost px-2 py-1 text-xs"
        data-testid="viewer-mode"
        title="切换显示模式"
      >
        {displayMode === "material" ? "线框" : "材质"}
      </button>
      <div className="flex items-center gap-1 px-1">
        <span className="text-[10px] text-txt-secondary">底色</span>
        {(["var(--camera-bg)", "#ffffff", "#1e293b", "#000000"] as const).map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onBgChange(c)}
            aria-label={`背景色 ${c}`}
            data-testid={`bg-${c}`}
            className={clsx(
              "h-3 w-3 rounded-full border",
              bg === c ? "border-accent" : "border-border",
            )}
            style={{ background: c.startsWith("var(") ? resolveCssColor(c) : c }}
          />
        ))}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <div
      role="status"
      className="flex items-center gap-2 text-xs text-txt-secondary"
      data-testid="viewer-spinner"
    >
      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      <span>加载模型…</span>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="grid h-full w-full place-items-center bg-err-bg p-4 text-center text-sm text-err"
      data-testid="viewer-error"
    >
      <div>
        <p className="font-medium">模型加载失败</p>
        <p className="mt-1 text-xs text-err">{message}</p>
      </div>
    </div>
  );
}
