import { useEffect, useRef, useState } from "react";

/** Live viewers by label, so a test (or a curious user in the console) can ask Mol* what it actually loaded. */
function registry(): Record<string, unknown> {
  const w = window as unknown as { __benchViewers?: Record<string, unknown> };
  w.__benchViewers ??= {};
  return w.__benchViewers;
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * A Mol* canvas showing one MolViewSpec state. The state is built by the API (smeltery.gui.scenes.pose_scene) or,
 * for previews, by the caller; this component only draws it. Mol* is loaded on first use.
 */
export function MolstarViewer({ state, label, height = 520 }: { state: unknown; label: string; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const plugin = useRef<import("molstar/lib/mol-plugin/context").PluginContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // Create the plugin once per mount.
  useEffect(() => {
    let disposed = false;
    (async () => {
      const [{ PluginContext }, { DefaultPluginSpec, PluginSpec }, { MolViewSpec }, { Color }] = await Promise.all([
        import("molstar/lib/mol-plugin/context"),
        import("molstar/lib/mol-plugin/spec"),
        import("molstar/lib/extensions/mvs/behavior"),
        import("molstar/lib/mol-util/color"),
      ]);
      if (disposed || !canvas.current || !host.current) return;
      const base = DefaultPluginSpec();
      const p = new PluginContext({ ...base, behaviors: [...base.behaviors, PluginSpec.Behavior(MolViewSpec)] });
      await p.init();
      if (!(await p.initViewerAsync(canvas.current, host.current))) throw new Error("WebGL is not available");
      p.canvas3d?.setProps({ renderer: { backgroundColor: Color.fromHexStyle(token("--panel")) } });
      if (disposed) {
        p.dispose();
        return;
      }
      plugin.current = p;
      registry()[label] = p;
      setReady(true);
    })().catch((e: unknown) => setError(String(e)));
    return () => {
      disposed = true;
      plugin.current?.dispose();
      plugin.current = null;
      delete registry()[label];
    };
  }, [label]);

  // Load the state whenever it changes.
  useEffect(() => {
    const p = plugin.current;
    if (!ready || !p || state === undefined) return;
    (async () => {
      const [{ loadMVS }, { MVSData }] = await Promise.all([
        import("molstar/lib/extensions/mvs/load"),
        import("molstar/lib/extensions/mvs/mvs-data"),
      ]);
      await loadMVS(p, MVSData.fromMVSJ(JSON.stringify(state)), { sanityChecks: false });
      // The scenes carry no camera, so frame what was loaded; otherwise Mol* keeps its empty default view.
      p.canvas3d?.requestResize();
      p.managers.camera.reset();
      host.current?.setAttribute("data-loaded", String(p.managers.structure.hierarchy.current.structures.length));
    })().catch((e: unknown) => setError(String(e)));
  }, [ready, state]);

  return (
    <div className="viewer" style={{ height }}>
      <div ref={host} className="viewer-host" data-viewer={label} role="img" aria-label={label}>
        <canvas ref={canvas} />
      </div>
      {error ? (
        <div className="banner" role="alert">
          The 3D viewer could not draw this scene: {error}
        </div>
      ) : null}
    </div>
  );
}
