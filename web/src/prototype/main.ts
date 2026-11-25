import { mount } from "svelte";
import "../lib/theme.css";
import App from "./App.svelte";
import snapshot from "./snapshot.json";
import type { Snapshot } from "../lib/types";

mount(App, {
  target: document.getElementById("app")!,
  props: { snapshot: snapshot as unknown as Snapshot },
});
