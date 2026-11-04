import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { StreamLanguage } from "@codemirror/language";
import { toml } from "@codemirror/legacy-modes/mode/toml";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { useEffect, useRef } from "react";

/**
 * The pipeline as TOML. Typing commits after a pause; the parent sends it to the API and passes back the normalised
 * TOML. Text arriving from elsewhere (a visual edit) replaces the document, but never while this pane has focus.
 */
export function TomlEditor({ value, onCommit, label }: { value: string; onCommit: (text: string) => void; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const commit = useRef(onCommit);
  commit.current = onCommit;

  useEffect(() => {
    if (!host.current) return;
    let timer: number | undefined;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          StreamLanguage.define(toml),
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((u) => {
            if (!u.docChanged || !u.transactions.some((t) => t.isUserEvent("input") || t.isUserEvent("delete"))) return;
            window.clearTimeout(timer);
            timer = window.setTimeout(() => commit.current(u.state.doc.toString()), 600);
          }),
        ],
      }),
    });
    view.current = v;
    return () => {
      window.clearTimeout(timer);
      v.destroy();
      view.current = null;
    };
    // Created once; later values arrive through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (!v || v.hasFocus || v.state.doc.toString() === value) return;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  return <div ref={host} className="toml-editor" data-testid="toml-pane" />;
}
