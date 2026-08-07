/**
 * Bundle Monaco locally instead of fetching it from a CDN.
 *
 * `@monaco-editor/react` defaults to pulling the editor off jsdelivr at
 * runtime. This app runs in Docker, often on a machine with no outbound
 * internet, and the editor would simply never appear. Configuring the loader
 * with a locally imported Monaco makes the editor part of the build.
 *
 * Importing this module pulls in Monaco, so it must only ever be imported from
 * a lazily-loaded chunk — see EditorPanel.
 */
import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';

const environment: monaco.Environment = {
  getWorker(_workerId: string, label: string) {
    if (label === 'json') return new jsonWorker();
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker();
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker();
    if (label === 'typescript' || label === 'javascript') return new tsWorker();
    return new editorWorker();
  },
};

self.MonacoEnvironment = environment;

loader.config({ monaco });
