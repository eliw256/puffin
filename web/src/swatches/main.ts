import { mount } from "svelte";
import "../lib/theme.css";
import Swatches from "./Swatches.svelte";

mount(Swatches, { target: document.getElementById("app")! });
