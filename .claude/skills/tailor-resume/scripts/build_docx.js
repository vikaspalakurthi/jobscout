#!/usr/bin/env node
/* Build an ATS-safe resume or cover letter DOCX from a structured markdown file,
 * reproducing the exact formatting of the reference documents this project produces
 * (extracted from the author's reference resume and cover letter, Sep 2026).
 *
 * Usage:
 *   node scripts/build_docx.js <input.md> <output.docx> [--cover] [--check-pages]
 *
 * Requires: npm install docx   (run once inside the skill folder)
 *
 * Resume markdown format (same structure as master-resume.md):
 *
 *   # Full Name
 *   Optional headline line (bold, centered)   <- present when TWO plain lines precede the first ##
 *   Contact line (centered)
 *
 *   ## SECTION HEADING                        <- 12pt bold with thin gray bottom rule
 *   Plain paragraph lines.
 *   Category: item, item                      <- in a section whose name contains SKILL,
 *                                                text before ":" renders bold
 *   ### Title | Org, Location | Dates         <- bold title, right-aligned bold dates,
 *                                                italic org line underneath
 *   - bullet line                             <- ASCII hyphen bullet, hanging indent
 *
 * Cover letter markdown format (--cover):
 *
 *   # Full Name
 *   Contact line
 *   Plain paragraphs (greeting, body, closing), one per line or blank-line separated.
 *
 * The script aborts on ANY non-ASCII character in the input, enforcing the writing
 * guide's character rules (keyboard hyphen only, no smart quotes, no em dashes).
 * --check-pages converts the DOCX to PDF with LibreOffice (if installed) and reports
 * the page count (target: exactly 2 for resumes, 1 for cover letters).
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const {
  AlignmentType, BorderStyle, Document, LevelFormat, Packer,
  Paragraph, TabStopType, TextRun,
} = require("docx");

const FONT = "Calibri";

// All measurements in twips / half-points, copied from the reference DOCX.
const RESUME = {
  page: { width: 11906, height: 16838 },            // A4 (docx library default, matches reference)
  margin: { top: 863, right: 863, bottom: 863, left: 863 },  // ~0.6 in
  name: { size: 36, after: 40 },                    // 18pt bold centered
  headline: { size: 22, after: 40 },                // 11pt bold centered
  contact: { size: 20, after: 120 },                // 10pt centered
  heading: { size: 24, before: 140, after: 60 },    // 12pt bold + bottom rule
  headingBorder: { style: BorderStyle.SINGLE, size: 6, color: "444444", space: 2 },
  body: { size: 20, after: 60 },                    // 10pt
  skill: { size: 20, after: 50 },
  role: { titleSize: 22, dateSize: 20, before: 110, after: 20, tabPos: 10512 },
  org: { size: 20, after: 40 },                     // italic
  bullet: { size: 20, after: 40, indentLeft: 360, indentHanging: 215 },
};

const COVER = {
  page: { width: 11906, height: 16838 },
  margin: { top: 1152, right: 1296, bottom: 1152, left: 1296 },  // 0.8 in / 0.9 in
  name: { size: 32, after: 60 },                    // 16pt bold centered
  contact: { size: 20, after: 240 },                // 10pt centered
  body: { size: 22, after: 160 },                   // 11pt
};

const BULLET_NUMBERING = {
  config: [{
    reference: "res-bullets",
    levels: [{
      level: 0,
      format: LevelFormat.BULLET,
      text: "-",                                    // plain ASCII hyphen
      alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: RESUME.bullet.indentLeft, hanging: RESUME.bullet.indentHanging } } },
    }],
  }],
};

function fail(msg) { console.error("ERROR: " + msg); process.exit(1); }

function checkAscii(text) {
  const bad = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    for (const ch of line) {
      const cp = ch.codePointAt(0);
      if (cp > 126 || (cp < 32 && ch !== "\t")) {
        bad.push(`  line ${i + 1}: '${ch}' (U+${cp.toString(16).toUpperCase().padStart(4, "0")}) in: ${line.trim().slice(0, 60)}`);
      }
    }
  });
  if (bad.length) fail("non-ASCII or control characters found; fix the input first:\n" + bad.slice(0, 20).join("\n"));
}

function run(text, opts = {}) {
  return new TextRun({ text, font: FONT, size: opts.size, bold: !!opts.bold, italics: !!opts.italic });
}

function buildResume(text) {
  const children = [];
  const lines = text.split(/\r?\n/);

  // Collect the plain lines that precede the first "## " to detect an optional headline.
  const preamble = [];
  let i = 0;
  let name = "";
  for (; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    if (!line.trim()) continue;
    if (line.startsWith("## ")) break;
    if (line.startsWith("# ") && !name) { name = line.slice(2).trim(); continue; }
    preamble.push(line.trim());
  }
  if (!name) fail("input must start with '# Full Name'");
  const headline = preamble.length >= 2 ? preamble[0] : null;
  const contact = preamble.length >= 2 ? preamble[1] : (preamble[0] || "");

  children.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: RESUME.name.after },
    children: [run(name, { size: RESUME.name.size, bold: true })],
  }));
  if (headline) children.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: RESUME.headline.after },
    children: [run(headline, { size: RESUME.headline.size, bold: true })],
  }));
  if (contact) children.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: RESUME.contact.after },
    children: [run(contact, { size: RESUME.contact.size })],
  }));

  let section = null;
  for (; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    if (!line.trim()) continue;

    if (line.startsWith("## ")) {
      section = line.slice(3).trim();
      children.push(new Paragraph({
        spacing: { before: RESUME.heading.before, after: RESUME.heading.after },
        border: { bottom: RESUME.headingBorder },
        children: [run(section, { size: RESUME.heading.size, bold: true })],
      }));
      continue;
    }

    if (line.startsWith("### ")) {
      const parts = line.slice(4).split("|").map(s => s.trim());
      const title = parts[0] || "";
      const org = parts.length > 2 ? parts[1] : "";
      const dates = parts.length > 2 ? parts[2] : (parts[1] || "");
      children.push(new Paragraph({
        spacing: { before: RESUME.role.before, after: RESUME.role.after },
        tabStops: [{ type: TabStopType.RIGHT, position: RESUME.role.tabPos }],
        children: [
          run(title, { size: RESUME.role.titleSize, bold: true }),
          run("\t" + dates, { size: RESUME.role.dateSize, bold: true }),
        ],
      }));
      if (org) children.push(new Paragraph({
        spacing: { after: RESUME.org.after },
        children: [run(org, { size: RESUME.org.size, italic: true })],
      }));
      continue;
    }

    if (line.trimStart().startsWith("- ")) {
      children.push(new Paragraph({
        numbering: { reference: "res-bullets", level: 0 },
        spacing: { after: RESUME.bullet.after },
        children: [run(line.trimStart().slice(2).trim(), { size: RESUME.bullet.size })],
      }));
      continue;
    }

    if (section && /SKILL/i.test(section) && line.includes(":")) {
      const idx = line.indexOf(":");
      children.push(new Paragraph({
        spacing: { after: RESUME.skill.after },
        children: [
          run(line.slice(0, idx + 1) + " ", { size: RESUME.skill.size, bold: true }),
          run(line.slice(idx + 1).trim(), { size: RESUME.skill.size }),
        ],
      }));
      continue;
    }

    children.push(new Paragraph({
      spacing: { after: RESUME.body.after },
      children: [run(line.trim(), { size: RESUME.body.size })],
    }));
  }

  return new Document({
    numbering: BULLET_NUMBERING,
    sections: [{ properties: { page: { size: RESUME.page, margin: RESUME.margin } }, children }],
  });
}

function buildCover(text) {
  const children = [];
  const lines = text.split(/\r?\n/).map(l => l.trimEnd()).filter(l => l.trim());
  let name = "", contact = "", bodyStart = 0;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].startsWith("# ") && !name) { name = lines[i].slice(2).trim(); continue; }
    contact = lines[i].trim(); bodyStart = i + 1; break;
  }
  if (!name) fail("cover input must start with '# Full Name'");
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: COVER.name.after },
    children: [run(name, { size: COVER.name.size, bold: true })],
  }));
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER, spacing: { after: COVER.contact.after },
    children: [run(contact, { size: COVER.contact.size })],
  }));
  for (let i = bodyStart; i < lines.length; i++) {
    children.push(new Paragraph({
      spacing: { after: COVER.body.after },
      children: [run(lines[i].trim(), { size: COVER.body.size })],
    }));
  }
  return new Document({
    sections: [{ properties: { page: { size: COVER.page, margin: COVER.margin } }, children }],
  });
}

function checkPages(outPath, target) {
  try {
    const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "respages-"));
    execFileSync("soffice", ["--headless", "--convert-to", "pdf", "--outdir", tmp, outPath], { timeout: 120000 });
    const pdf = path.join(tmp, path.basename(outPath).replace(/\.docx$/i, ".pdf"));
    const info = execFileSync("pdfinfo", [pdf]).toString();
    const m = info.match(/Pages:\s+(\d+)/);
    const pages = m ? parseInt(m[1], 10) : -1;
    const ok = pages === target ? "OK" : `WARNING: target is exactly ${target} page(s)`;
    console.log(`Page count: ${pages} (${ok})`);
    fs.rmSync(tmp, { recursive: true, force: true });
  } catch (e) {
    console.log("Page check skipped or failed (LibreOffice/pdfinfo needed): " + e.message.split("\n")[0]);
  }
}

async function main() {
  const argv = process.argv.slice(2);
  const cover = argv.includes("--cover");
  const doCheck = argv.includes("--check-pages");
  const args = argv.filter(a => !a.startsWith("--"));
  if (args.length !== 2) fail("usage: node scripts/build_docx.js <input.md> <output.docx> [--cover] [--check-pages]");
  const [inPath, outPath] = args;
  if (!fs.existsSync(inPath)) fail("input not found: " + inPath);
  const text = fs.readFileSync(inPath, "utf-8");
  checkAscii(text);
  const doc = cover ? buildCover(text) : buildResume(text);
  const buf = await Packer.toBuffer(doc);
  fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
  fs.writeFileSync(outPath, buf);
  console.log("Wrote " + outPath);
  if (doCheck) checkPages(outPath, cover ? 1 : 2);
}

main().catch(e => fail(e.stack || String(e)));
