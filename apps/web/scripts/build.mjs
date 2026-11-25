import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const name of ["index.html", "app.js", "styles.css"]) {
  await cp(resolve(root, "src", name), resolve(dist, name));
}

const assetVersion = async (name) => createHash("sha256")
  .update(await readFile(resolve(root, "src", name)))
  .digest("hex")
  .slice(0, 12);
const appVersion = await assetVersion("app.js");
const stylesVersion = await assetVersion("styles.css");
const indexPath = resolve(dist, "index.html");
const indexHtml = await readFile(indexPath, "utf8");
await writeFile(
  indexPath,
  indexHtml
    .replace('href="/styles.css"', `href="/styles.css?v=${stylesVersion}"`)
    .replace('src="/app.js"', `src="/app.js?v=${appVersion}"`),
);

function escapeHtml(value) {
  return String(value).replace(/[&<>\"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[character]));
}

function inlineMarkdown(value) {
  let html = escapeHtml(value);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
    const safeHref = /^javascript:/i.test(href) ? "#" : href;
    return `<a href="${escapeHtml(safeHref)}">${label}</a>`;
  });
  return html;
}

function renderMarkdown(markdown) {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const output = [];
  let inCode = false;
  let codeLanguage = "";
  let codeLines = [];
  let listType = null;
  let paragraphLines = [];
  let tableRows = null;

  const closeList = () => {
    if (listType) output.push(`</${listType}>`);
    listType = null;
  };
  const closeParagraph = () => {
    if (paragraphLines.length) output.push(`<p>${inlineMarkdown(paragraphLines.join(" "))}</p>`);
    paragraphLines = [];
  };
  const closeTable = () => {
    if (!tableRows?.length) return;
    const [header, ...body] = tableRows;
    output.push(`<div class="tutorial-table-wrap"><table><thead><tr>${header.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    tableRows = null;
  };
  const parseTableRow = (value) => value.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  const isTableSeparator = (value) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(value);

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.match(/^```\s*([\w-]*)\s*$/);
    if (fence) {
      if (inCode) {
        output.push(`<pre><code class="language-${escapeHtml(codeLanguage)}">${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        inCode = false;
        codeLanguage = "";
        codeLines = [];
      } else {
        closeParagraph();
        closeList();
        closeTable();
        inCode = true;
        codeLanguage = fence[1] || "text";
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    if (!line.trim()) {
      closeParagraph();
      closeList();
      closeTable();
      continue;
    }
    if (line.trim().startsWith("|") && isTableSeparator(lines[index + 1] || "")) {
      closeParagraph();
      closeList();
      closeTable();
      tableRows = [parseTableRow(line)];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        tableRows.push(parseTableRow(lines[index]));
        index += 1;
      }
      index -= 1;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeParagraph();
      closeList();
      closeTable();
      const level = heading[1].length;
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      closeParagraph();
      closeTable();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        closeList();
        output.push(`<${nextType}>`);
        listType = nextType;
      }
      output.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      closeParagraph();
      closeList();
      closeTable();
      output.push(`<blockquote>${inlineMarkdown(line.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    if (listType) closeList();
    if (tableRows) closeTable();
    paragraphLines.push(line.trim());
  }
  closeParagraph();
  if (inCode) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  closeList();
  closeTable();
  return output.join("\n");
}

const tutorial = await readFile(resolve(root, "..", "..", "TUTORIAL.md"), "utf8");
const tutorialHtml = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>OpsFusion · Concepts and implementation</title>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body>
    <main class="tutorial-page">
      <a class="tutorial-back" href="/">← Back to the operations console</a>
      <article class="tutorial-content">${renderMarkdown(tutorial)}</article>
    </main>
  </body>
</html>
`;
await writeFile(resolve(dist, "tutorial.html"), tutorialHtml);
console.log(`OpsFusion console built at ${dist}`);
