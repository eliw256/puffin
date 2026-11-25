<script lang="ts">
  type Props = { title: string; text: string };
  let { title, text }: Props = $props();

  type Line = { kind: "add" | "del" | "hunk" | "file" | "ctx"; text: string };

  const lines = $derived(
    text
      .split("\n")
      .filter((line, i, all) => line !== "" || i < all.length - 1)
      .map((line): Line => {
        if (line.startsWith("+++") || line.startsWith("---")) return { kind: "file", text: line };
        if (line.startsWith("@@")) return { kind: "hunk", text: line };
        if (line.startsWith("+")) return { kind: "add", text: line };
        if (line.startsWith("-")) return { kind: "del", text: line };
        return { kind: "ctx", text: line };
      })
      .filter((line) => line.kind !== "file"),
  );
</script>

<figure class="diff">
  <figcaption>{title}</figcaption>
  <pre>{#each lines as line, i (i)}<span class={line.kind}>{line.text.replaceAll("\t", "  ")}
</span>{/each}</pre>
</figure>

<style>
  .diff {
    margin: 0 0 14px;
  }
  figcaption {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 4px;
    font-family: var(--font-mono);
  }
  pre {
    margin: 0;
    padding: 8px 0;
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.5;
    overflow-x: auto;
  }
  span {
    display: block;
    padding: 0 10px;
    color: var(--bone);
  }
  .add {
    background: var(--diff-add);
  }
  .del {
    background: var(--diff-del);
  }
  .hunk {
    color: var(--muted);
  }
</style>
