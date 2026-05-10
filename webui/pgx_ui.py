#!/usr/bin/env python3
"""Local web UI for running 23andMe-compatible ClawBio skills.

Run:
    python webui/pgx_ui.py
    # Open http://127.0.0.1:5120
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAWBIO_PY = PROJECT_ROOT / "clawbio.py"
OUTPUT_ROOT = PROJECT_ROOT / "output"
UPLOAD_ROOT = OUTPUT_ROOT / "webui_uploads"
RUN_ROOT = OUTPUT_ROOT / "webui_runs"
ASSET_ROOT = PROJECT_ROOT / "webui" / "assets"

TRAITS = {
    "type2_diabetes": {
        "label": "Type 2 diabetes",
        "pgs_id": "PGS000013",
    },
    "atrial_fibrillation": {
        "label": "Atrial fibrillation",
        "pgs_id": "PGS000011",
    },
    "coronary_artery_disease": {
        "label": "Coronary artery disease",
        "pgs_id": "PGS000004",
    },
    "breast_cancer": {
        "label": "Breast cancer",
        "pgs_id": "PGS000001",
    },
    "prostate_cancer": {
        "label": "Prostate cancer",
        "pgs_id": "PGS000057",
    },
    "bmi": {
        "label": "Body mass index / BMI",
        "pgs_id": "PGS000039",
    },
    "all_available": {
        "label": "All available traits",
        "pgs_id": "ALL",
    },
}

SKILLS = {
    "pharmgx": {
        "label": "Pharmacogenomics report",
        "slug": "pharmgx",
    },
    "drugphoto": {
        "label": "Medicine Check",
        "slug": "drugphoto",
    },
    "nutrigx": {
        "label": "Nutrigenomics advice",
        "slug": "nutrigx",
    },
    "prs": {
        "label": "Polygenic risk score",
        "slug": "prs",
    },
}

PRS_DISCLAIMER = (
    "This is a research and educational tool. It is not a medical advice and "
    "does not provide clinical diagnoses. Consult a healthcare professional "
    "before making any medical decisions."
)

PRS_ALL_DISCLAIMER = (
    "Research and educational use only. This tool does not provide clinical "
    "diagnoses, treatment recommendations, or personalised medical advice"
)

PRS_REPORT_CSS = """\
:root {
  --cb-green-900: #17265f;
  --cb-green-700: #17265f;
  --cb-green-500: #3478c8;
  --cb-green-100: #eef2ff;
  --cb-green-50: #f7f9ff;
  --cb-bg: #f4f1ed;
  --cb-surface: #ffffff;
  --cb-text: #172033;
  --cb-text-secondary: #5f6675;
  --cb-border: #d9dde7;
}
*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.6;
  color: var(--cb-text);
  background: var(--cb-bg);
  margin: 0 auto;
  padding: 32px 20px;
  max-width: 1080px;
}
.report-header {
  background: #17265f;
  color: #ffffff;
  padding: 24px 32px;
  border-radius: 10px;
  margin: 0 0 32px;
  box-shadow: 0 18px 50px rgba(23, 38, 95, 0.16);
}
.report-header h1 {
  margin: 0;
  font-size: 1.8em;
  font-weight: 700;
}
.report-header .subtitle {
  margin: 4px 0 0;
  opacity: 0.9;
}
h2,
h3 {
  color: #17265f;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 0.9em;
}
th {
  background: #eef2ff;
  color: #17265f;
  text-align: left;
  padding: 10px 12px;
  border-bottom: 2px solid #c6d4f5;
}
td {
  padding: 8px 12px;
  border-bottom: 1px solid #d9dde7;
  vertical-align: top;
}
tr:nth-child(even) {
  background: #f5f6f9;
}
tr:hover {
  background: #eef2ff;
}
.table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 16px 0;
  border-radius: 10px;
  border: 1px solid #d9dde7;
  background: #ffffff;
}
.table-wrap table {
  margin: 0;
  border: none;
}
a {
  color: #3478c8;
  font-weight: 700;
}
.disclaimer {
  background: #ffffff;
  border: 1px solid #d9dde7;
  border-left: 4px solid #3478c8;
  border-radius: 10px;
  color: #172033;
  padding: 10px 14px;
  margin: 24px 0;
  box-shadow: 0 10px 28px rgba(23, 38, 95, 0.08);
}
.disclaimer strong {
  color: #17265f;
}
.report-footer {
  border-top: 1px solid #d9dde7;
  color: #5f6675;
  font-size: 0.9em;
  margin-top: 32px;
  padding-top: 16px;
}
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Genomic Insights v0.1</title>
  <style>
    :root {
      color-scheme: light;
      --navy: #17265f;
      --navy-2: #22336f;
      --navy-3: #30427f;
      --navy-soft: #eef2ff;
      --ink: #1f2933;
      --muted: #667085;
      --line: #dcdfe5;
      --surface: #ffffff;
      --field: #f7f7f8;
      --bg: #ffffff;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    main {
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(420px, 1fr) minmax(360px, 1fr);
    }
    section {
      padding: clamp(28px, 5vw, 64px);
    }
    .form-panel {
      background: var(--surface);
      display: flex;
      justify-content: flex-end;
    }
    .form-inner {
      width: min(100%, 620px);
    }
    .result-panel {
      background: var(--navy);
      color: white;
      display: flex;
      justify-content: flex-start;
      position: relative;
      overflow: hidden;
    }
    .result-panel::before {
      content: "";
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(90deg, rgba(7,18,55,0.99) 0%, rgba(7,18,55,0.94) 58%, rgba(7,18,55,0.48) 100%),
        url("/assets/report-dna-bg-selected.png");
      background-size: cover;
      background-position: center right;
      opacity: 0;
      transition: opacity 180ms ease;
    }
    .result-panel.has-selection::before {
      opacity: 1;
    }
    .result-inner {
      width: min(100%, 650px);
      padding-top: 0;
      position: relative;
      z-index: 1;
    }
    .empty-visual {
      position: absolute;
      inset: 0;
      display: none;
      background: var(--navy);
    }
    .empty-visual.is-visible {
      display: block;
    }
    .result-panel.has-selection .empty-visual {
      display: none !important;
    }
    .empty-visual img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center center;
      display: block;
    }
    .report-card {
      display: none;
      background: rgba(13, 29, 76, 0.9);
      border: 1px solid rgba(160, 190, 255, 0.36);
      border-radius: 14px;
      padding: clamp(24px, 4vw, 40px);
      box-shadow: 0 18px 60px rgba(0,0,0,0.22);
      backdrop-filter: blur(10px);
    }
    .report-card.is-visible {
      display: block;
    }
    .eyebrow {
      margin: 0 0 10px;
      color: var(--muted);
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0 0 12px;
      color: #101828;
      font-size: clamp(28px, 3vw, 40px);
      line-height: 1.12;
      letter-spacing: 0;
    }
    .subtitle {
      margin: 0 0 34px;
      color: #787878;
      font-size: 17px;
    }
    h2 { margin: 0 0 18px; font-size: 18px; color: #101828; }
    .result-panel h2 { color: white; }
    label { display: block; font-weight: 650; margin: 14px 0 6px; }
    input, select, button {
      width: 100%;
      min-height: 50px;
      border: 1px solid #dadde3;
      border-radius: 6px;
      padding: 12px 14px;
      font: inherit;
      background: var(--field);
      color: var(--ink);
    }
    input:focus, select:focus {
      outline: none;
      border-color: #3b82f6;
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.18);
      background: white;
    }
    input[type="file"] { padding: 8px; }
    button {
      margin-top: 24px;
      min-height: 56px;
      border-color: var(--navy);
      background: var(--navy);
      color: white;
      font-weight: 700;
      cursor: pointer;
      font-size: 17px;
    }
    button:hover { background: var(--navy-2); }
    button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
      background: #9298aa;
      border-color: #9298aa;
    }
    .hint { color: var(--muted); font-size: 13px; margin-top: 5px; }
    .conditional { display: none; }
    .conditional.is-visible { display: block; }
    .report-copy {
      min-height: 240px;
      color: rgba(255,255,255,0.9);
      font-size: 16px;
      line-height: 1.55;
    }
    .report-copy h3 {
      margin: 0;
      color: white;
      font-size: clamp(26px, 3vw, 34px);
      line-height: 1.18;
    }
    .report-copy p {
      margin: 0 0 16px;
    }
    .preview-card {
      display: grid;
      gap: 22px;
    }
    .preview-head {
      display: flex;
      align-items: center;
      gap: 18px;
    }
    .preview-head.stacked {
      align-items: flex-start;
      flex-direction: column;
      gap: 16px;
    }
    .preview-title-accent {
      display: inline-block;
      width: 54px;
      height: 3px;
      margin-top: 12px;
      border-radius: 99px;
      background: linear-gradient(90deg, #8b7cf6, #83e7df);
    }
    .preview-icon,
    .row-icon,
    .check-icon,
    .ready-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      border-radius: 999px;
      color: white;
      background: rgba(91, 104, 232, 0.28);
      border: 1px solid rgba(126, 231, 225, 0.38);
      box-shadow: inset 0 0 18px rgba(126, 231, 225, 0.08);
    }
    .preview-icon {
      width: 66px;
      height: 66px;
      color: #9cf2e8;
    }
    .preview-icon.large {
      width: 84px;
      height: 84px;
      color: white;
      background: linear-gradient(135deg, rgba(120, 91, 231, 0.45), rgba(44, 149, 213, 0.28));
      border-color: rgba(147, 128, 255, 0.42);
    }
    .preview-icon svg,
    .row-icon svg,
    .check-icon svg,
    .ready-icon svg {
      width: 54%;
      height: 54%;
      stroke: currentColor;
      stroke-width: 2;
      fill: none;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .preview-intro {
      display: grid;
      gap: 12px;
      color: rgba(255,255,255,0.88);
    }
    .preview-intro p,
    .preview-list p,
    .preview-ready p {
      margin: 0;
    }
    .preview-divider {
      height: 1px;
      background: rgba(190, 210, 255, 0.22);
    }
    .preview-list {
      display: grid;
      gap: 16px;
    }
    .preview-subtitle {
      color: white;
      font-weight: 800;
    }
    .preview-row,
    .preview-ready {
      display: grid;
      grid-template-columns: 42px 1fr;
      align-items: center;
      gap: 14px;
    }
    .preview-row.with-rule {
      padding-bottom: 16px;
      border-bottom: 1px solid rgba(190, 210, 255, 0.14);
    }
    .row-icon,
    .check-icon,
    .ready-icon {
      width: 36px;
      height: 36px;
    }
    .row-icon {
      color: #b4c4ff;
      border-color: rgba(126, 231, 225, 0.34);
    }
    .check-icon {
      color: #9cf2e8;
      background: rgba(37, 93, 162, 0.32);
    }
    .ready-icon {
      color: #cbd5ff;
      background: rgba(91, 104, 232, 0.24);
      border-color: rgba(147, 128, 255, 0.35);
    }
    .preview-card.nutrigx .preview-icon,
    .preview-card.nutrigx .row-icon {
      color: #d8c8ff;
      background: rgba(116, 83, 215, 0.34);
      border-color: rgba(154, 132, 255, 0.42);
    }
    .preview-card.drugphoto .preview-icon,
    .preview-card.drugphoto .row-icon,
    .preview-card.pharmgx .preview-icon,
    .preview-card.pharmgx .row-icon {
      color: #8cf0e6;
    }
    .preview-card.pharmgx .preview-head {
      align-items: center;
    }
    .report-copy ul {
      margin: 0 0 18px;
      padding-left: 20px;
    }
    .report-copy li {
      margin: 7px 0;
    }
    .report-copy .ready-message,
    .report-copy .analysing-message {
      color: white;
      font-size: 24px;
      font-weight: 800;
    }
    .result-actions {
      display: none;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }
    .result-actions.is-visible { display: grid; }
    .link-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.28);
      color: white;
      background: rgba(255,255,255,0.08);
      text-decoration: none;
      font-weight: 700;
    }
    .link-button:hover { background: rgba(255,255,255,0.14); }
    .error { color: #ffd7d3; }
    .disclaimer-box {
      margin-top: 22px;
      padding: 14px 16px;
      border: 1px solid #c9ced8;
      border-left: 4px solid var(--navy);
      border-radius: 6px;
      background: #f4f6fb;
      color: #26324a;
      font-size: 13px;
      line-height: 1.5;
      font-weight: 600;
    }
    .disclaimer-box p {
      margin: 0 0 10px;
    }
    .disclaimer-item {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin: 8px 0;
      font-weight: 500;
    }
    .disclaimer-item input {
      width: 18px;
      min-height: 18px;
      height: 18px;
      flex: 0 0 18px;
      margin: 2px 0 0;
      padding: 0;
      accent-color: var(--navy);
    }
    .fineprint {
      margin-top: 28px;
      color: var(--muted);
      font-size: 12px;
      padding-top: 22px;
      border-top: 1px solid var(--line);
    }
    code {
      background: #f0f2f5;
      border-radius: 4px;
      padding: 1px 4px;
    }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
      section { padding: 24px 16px; }
      .form-panel, .result-panel { justify-content: center; }
      .result-panel { min-height: 420px; }
      .result-inner { padding-top: 0; }
      .result-actions { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <section class="form-panel">
      <div class="form-inner">
      <p class="eyebrow">Local genomics</p>
      <h1>Genomic Insights v0.1</h1>
      <p class="subtitle">Upload your raw genotype file in .txt format, choose the report you’d like to generate, and we’ll do the CPIC-aligned analysis locally on this device.</p>
      <h2>Get started</h2>
      <form id="runForm">
        <label for="genotype">Upload your genotype file</label>
        <input id="genotype" name="genotype" type="file" accept=".txt,.gz,text/plain" required>
        <div class="hint">The file is processed locally by this server.</div>

        <label for="skill">Choose your report</label>
        <select id="skill" name="skill" required>
          <option value="" selected></option>
          <option value="pharmgx">Pharmacogenomics report</option>
          <option value="drugphoto">Medicine Check</option>
          <option value="nutrigx">Nutrigenomics advice</option>
          <option value="prs">Polygenic risk score</option>
        </select>

        <div id="drugPanel" class="conditional">
          <label for="drug">Medicine name as shown on the package</label>
          <input id="drug" name="drug" type="text" placeholder="e.g. warfarin, Plavix, codeine">
          <label for="dose">Dose shown on your medicine package (optional)</label>
          <input id="dose" name="dose" type="text" placeholder="e.g. 5mg, 75mg">
        </div>

        <div id="prsPanel" class="conditional">
          <label for="trait">Choose a health trait to analyse</label>
          <select id="trait" name="trait">
            <option value="all_available">All available traits</option>
            <option value="type2_diabetes">Type 2 diabetes</option>
            <option value="atrial_fibrillation">Atrial fibrillation</option>
            <option value="coronary_artery_disease">Coronary artery disease</option>
            <option value="breast_cancer">Breast cancer</option>
            <option value="prostate_cancer">Prostate cancer</option>
            <option value="bmi">Body mass index / BMI</option>
          </select>
        </div>

        <div class="disclaimer-box">
          <p>By continuing, I confirm that I understand:</p>
          <label class="disclaimer-item">
            <input class="disclaimer-check" type="checkbox" required>
            <span>this is a research and educational prototype only;</span>
          </label>
          <label class="disclaimer-item">
            <input class="disclaimer-check" type="checkbox" required>
            <span>this tool does not provide clinical diagnoses, treatment recommendations, or personalised medical advice;</span>
          </label>
          <label class="disclaimer-item">
            <input class="disclaimer-check" type="checkbox" required>
            <span>I agree to consult a qualified healthcare professional before making any medical decisions based on the information provided.</span>
          </label>
        </div>
        <button id="runButton" type="submit" disabled>Generate report</button>
      </form>
      <p class="fineprint">Reports are generated under <code>output/webui_runs</code>. Genetic data is not sent to external services by this UI.</p>
      </div>
    </section>

    <section class="result-panel">
      <div id="emptyVisual" class="empty-visual" aria-hidden="true">
        <img src="/assets/genomic-insights-empty-navy.png" alt="">
      </div>
      <div class="result-inner">
      <div id="reportCard" class="report-card">
        <div id="status" class="report-copy"></div>
        <div id="resultActions" class="result-actions">
          <a id="viewReport" class="link-button" href="#" target="_blank" rel="noopener">View Report</a>
          <a id="downloadReport" class="link-button" href="#">Download Report</a>
        </div>
      </div>
      </div>
    </section>
  </main>

  <script>
    const form = document.getElementById('runForm');
    const skill = document.getElementById('skill');
    const drugPanel = document.getElementById('drugPanel');
    const prsPanel = document.getElementById('prsPanel');
    const drug = document.getElementById('drug');
    const statusBox = document.getElementById('status');
    const reportCard = document.getElementById('reportCard');
    const emptyVisual = document.getElementById('emptyVisual');
    const runButton = document.getElementById('runButton');
    const actions = document.getElementById('resultActions');
    const viewReport = document.getElementById('viewReport');
    const downloadReport = document.getElementById('downloadReport');
    const disclaimerChecks = Array.from(document.querySelectorAll('.disclaimer-check'));
    const resultPanel = document.querySelector('.result-panel');
    const icons = {
      dna: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3c5 3 5 6 10 9M17 3c-5 3-5 6-10 9M7 21c5-3 5-6 10-9M17 21c-5-3-5-6-10-9M8 6h8M8 18h8M9 10h6M9 14h6"/></svg>',
      shieldPill: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3z"/><path d="M9 14l6-6"/><path d="M8.4 10.6l5 5"/></svg>',
      bottle: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6M10 3v4l-3 3v10h10V10l-3-3V3"/><path d="M10 14h4M12 12v4"/></svg>',
      apple: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8c2-3 5-2 6 1 1.5 4-2 10-6 10S4.5 13 6 9c1-3 4-4 6-1z"/><path d="M12 8c0-2 1-4 3-5"/><path d="M14 4c2 0 3 1 4 2"/></svg>',
      users: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 11a4 4 0 1 0-8 0"/><path d="M3 20c1-4 5-6 9-6s8 2 9 6"/><path d="M17 7a3 3 0 0 1 0 6"/><path d="M5 7a3 3 0 0 0 0 6"/></svg>',
      shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
      file: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/></svg>',
      book: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5c3-1.5 5-1.5 8 0v15c-3-1.5-5-1.5-8 0z"/><path d="M12 5c3-1.5 5-1.5 8 0v15c-3-1.5-5-1.5-8 0z"/></svg>',
      chart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h16M7 17V9M12 17V5M17 17v-6"/><path d="M6 13l3-3 3 2 4-5 2 2"/></svg>',
      leaf: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4c-8 0-13 5-13 13"/><path d="M20 4c0 8-5 13-13 13"/><path d="M7 17c3-1 6-3 9-7"/></svg>',
      bars: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h16M7 17v-5M12 17V7M17 17v-8"/><path d="M7 9l3 2 3-5 4 2"/></svg>',
      chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v10H9l-4 4z"/><path d="M8 9h.01M12 9h.01M16 9h.01"/></svg>',
      check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l4 4 10-10"/></svg>',
      document: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l4 4v14H7z"/><path d="M14 3v5h5"/><path d="M10 13h4M10 17h6"/></svg>',
      alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l9 16H3z"/><path d="M12 9v4M12 17h.01"/></svg>'
    };
    const reportDescriptions = {
      pharmgx: `
        <div class="preview-card pharmgx">
          <div class="preview-head">
            <span class="preview-icon large">${icons.shieldPill}</span>
            <div>
              <h3>What you’ll get</h3>
              <span class="preview-title-accent"></span>
            </div>
          </div>
          <div class="preview-intro">
            <p>Your medication report will show how your genes may affect the way some medicines work for you.</p>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-list">
            <div class="preview-row with-rule"><span class="row-icon">${icons.users}</span><p>Medicines where extra caution may be worth discussing with a healthcare professional</p></div>
            <div class="preview-row with-rule"><span class="row-icon">${icons.shield}</span><p>Medicines where standard guidance may apply</p></div>
            <div class="preview-row with-rule"><span class="row-icon">${icons.file}</span><p>Areas where your uploaded file may not contain enough information</p></div>
            <div class="preview-row"><span class="row-icon">${icons.book}</span><p>Evidence sources used to support each result</p></div>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-ready"><span class="ready-icon">${icons.chart}</span><p>Your report will appear here once it’s ready.</p></div>
        </div>
      `,
      drugphoto: `
        <div class="preview-card drugphoto">
          <div class="preview-head stacked">
            <span class="preview-icon large">${icons.bottle}</span>
            <h3>What you’ll get</h3>
          </div>
          <div class="preview-intro">
            <p>Check one medicine against your genetic data.</p>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-intro">
            <p>This report can help you see whether your genes may affect how this medicine works for you, including any relevant PGx insights, limitations, and points to discuss with a healthcare professional.</p>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-list">
            <div class="preview-row"><span class="row-icon">${icons.bars}</span><p>Personalised PGx insights for the medicine you choose</p></div>
            <div class="preview-row"><span class="row-icon">${icons.alert}</span><p>Key considerations and important limitations</p></div>
            <div class="preview-row"><span class="row-icon">${icons.chat}</span><p>Discussion points for your healthcare professional</p></div>
            <div class="preview-row"><span class="row-icon">${icons.book}</span><p>Evidence sources supporting each result</p></div>
          </div>
          <div class="preview-divider"></div>
          <p>Your result will appear here once it’s ready.</p>
        </div>
      `,
      nutrigx: `
        <div class="preview-card nutrigx">
          <div class="preview-head stacked">
            <span class="preview-icon large">${icons.apple}</span>
            <h3>What you’ll get</h3>
          </div>
          <div class="preview-intro">
            <p>Explore how your genetic data may relate to nutrition, vitamins, caffeine, fat metabolism and other dietary factors.</p>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-list">
            <div class="preview-row"><span class="row-icon">${icons.leaf}</span><p>Nutrient areas where your genes may suggest higher or lower sensitivity</p></div>
            <div class="preview-row"><span class="row-icon">${icons.bars}</span><p>A simple risk score for each nutrition domain</p></div>
            <div class="preview-row"><span class="row-icon">${icons.dna}</span><p>Genetic variants found in your uploaded file</p></div>
            <div class="preview-row"><span class="row-icon">${icons.chat}</span><p>Notes on supplements and nutrients to discuss with a qualified healthcare professional</p></div>
          </div>
          <div class="preview-divider"></div>
          <p>Your report will appear here once it’s ready.</p>
        </div>
      `,
      prs: `
        <div class="preview-card prs">
          <div class="preview-head">
            <span class="preview-icon">${icons.dna}</span>
            <h3>What you’ll get</h3>
          </div>
          <div class="preview-intro">
            <p>Explore how your genetic data may relate to selected health traits.</p>
            <p>You can choose one trait to analyse, or run a report for all available traits.</p>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-list">
            <p class="preview-subtitle">This report includes:</p>
            <div class="preview-row"><span class="check-icon">${icons.check}</span><p>A polygenic risk score for each selected trait</p></div>
            <div class="preview-row"><span class="check-icon">${icons.check}</span><p>Your estimated percentile and risk category</p></div>
            <div class="preview-row"><span class="check-icon">${icons.check}</span><p>How many relevant genetic variants were found in your file</p></div>
            <div class="preview-row"><span class="check-icon">${icons.check}</span><p>Key variants that contributed to the score</p></div>
            <div class="preview-row"><span class="check-icon">${icons.check}</span><p>Important limitations to help you interpret the result carefully</p></div>
          </div>
          <div class="preview-divider"></div>
          <div class="preview-ready"><span class="ready-icon">${icons.document}</span><p>Your report will appear here once it’s ready.</p></div>
        </div>
      `,
    };
    let isRunning = false;
    let hasResult = false;

    function allDisclaimersAccepted() {
      return disclaimerChecks.every((checkbox) => checkbox.checked);
    }

    function updateRunButtonState() {
      runButton.disabled = isRunning || !skill.value || !allDisclaimersAccepted();
    }

    function updateConditionalFields() {
      const selected = skill.value;
      drugPanel.classList.toggle('is-visible', selected === 'drugphoto');
      prsPanel.classList.toggle('is-visible', selected === 'prs');
      drug.required = selected === 'drugphoto';
      if (!isRunning) {
        hasResult = false;
        actions.classList.remove('is-visible');
        showReportDescription();
      }
    }

    function showReportDescription() {
      const hasDescription = Boolean(skill.value);
      resultPanel.classList.toggle('has-selection', hasDescription);
      reportCard.classList.toggle('is-visible', hasDescription);
      emptyVisual.classList.toggle('is-visible', !hasDescription);
      statusBox.innerHTML = hasDescription ? (reportDescriptions[skill.value] || '') : '';
    }

    function showAnalysing() {
      resultPanel.classList.add('has-selection');
      emptyVisual.classList.remove('is-visible');
      reportCard.classList.add('is-visible');
      statusBox.innerHTML = '<p class="analysing-message">Analysing...</p>';
    }

    function showReady() {
      resultPanel.classList.add('has-selection');
      emptyVisual.classList.remove('is-visible');
      reportCard.classList.add('is-visible');
      statusBox.innerHTML = '<p class="ready-message">Your report is ready</p>';
    }

    skill.addEventListener('change', updateConditionalFields);
    disclaimerChecks.forEach((checkbox) => checkbox.addEventListener('change', updateRunButtonState));
    updateConditionalFields();
    updateRunButtonState();
    showReportDescription();

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!allDisclaimersAccepted()) {
        updateRunButtonState();
        return;
      }
      isRunning = true;
      hasResult = false;
      actions.classList.remove('is-visible');
      showAnalysing();
      runButton.disabled = true;

      try {
        const response = await fetch('/api/run', {
          method: 'POST',
          body: new FormData(form),
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) {
          statusBox.innerHTML = '<span class="error">Failed.</span>\\n' + (payload.error || 'Unknown error') + '\\n\\n' + (payload.stderr || '');
          return;
        }
        hasResult = true;
        showReady();
        viewReport.href = payload.report_url;
        downloadReport.href = payload.download_url;
        actions.classList.add('is-visible');
      } catch (error) {
        statusBox.innerHTML = '<span class="error">Failed.</span>\\n' + error;
      } finally {
        isRunning = false;
        updateRunButtonState();
      }
    });
  </script>
</body>
</html>
"""


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    data = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _html_response(handler: BaseHTTPRequestHandler, content: str) -> None:
    data = content.encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return cleaned or "genotype.txt"


def _parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    content_type = handler.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise ValueError("Expected multipart/form-data")
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length)
    raw = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=default).parsebytes(raw)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if not disposition:
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_param("filename", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (filename, payload)
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields, files


def _drugphoto_html(stdout: str, output_dir: Path) -> Path:
    safe_stdout = html.escape(stdout or "No output was returned.")
    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Drug Photo PGx Result</title>
  <style>
    body {{ font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7faf8; color: #1f2933; }}
    main {{ max-width: 900px; margin: 0 auto; padding: 24px; }}
    header {{ background: #17265f; color: white; padding: 18px 24px; }}
    h1 {{ margin: 0; font-size: 22px; }}
    pre {{ white-space: pre-wrap; background: white; border: 1px solid #d9e2dc; border-radius: 8px; padding: 18px; overflow-wrap: anywhere; }}
    .disclaimer {{ background: #fff3e0; border: 1px solid #ffcc80; border-radius: 6px; padding: 10px 14px; color: #8a4b00; }}
  </style>
</head>
<body>
  <header><h1>Drug Photo / Single-Drug PGx Result</h1></header>
  <main>
    <pre>{safe_stdout}</pre>
    <p class="disclaimer">Research and educational use only. This is not medical advice.</p>
  </main>
</body>
</html>
"""
    report_path = output_dir / "report.html"
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _prs_all_html(results: list[dict], output_dir: Path) -> Path:
    rows = []
    for item in results:
        result = item["result"]
        coverage = result["overlap_fraction"] * 100
        percentile = result.get("percentile")
        percentile_text = "N/A" if percentile is None else f"{percentile:.1f}"
        z_score = result.get("z_score")
        z_text = "N/A" if z_score is None else f"{z_score:.3f}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(result['trait'])}</td>"
            f"<td>{html.escape(result['pgs_id'])}</td>"
            f"<td>{html.escape(str(result['risk_category']))}</td>"
            f"<td>{percentile_text}</td>"
            f"<td>{z_text}</td>"
            f"<td>{result['variants_used']}/{result['variants_total']} ({coverage:.1f}%)</td>"
            f'<td><a href="{html.escape(item["relative_report"])}">Open detailed report</a></td>'
            "</tr>"
        )

    content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polygenic Risk Score Report</title>
  <style>
{PRS_REPORT_CSS}
  </style>
</head>
<body>
  <div class="disclaimer"><strong>Disclaimer:</strong> {html.escape(PRS_ALL_DISCLAIMER)}</div>
  <header class="report-header">
    <h1>Polygenic Risk Score Report</h1>
    <p class="subtitle">Explore genetic risk scores from your DNA data</p>
  </header>
  <h2>All available traits</h2>
  <p>This report summarises all six curated PRS traits available in this prototype.</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Trait</th>
          <th>PGS ID</th>
          <th>Risk category</th>
          <th>Percentile</th>
          <th>Z-score</th>
          <th>Variant coverage</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </div>
  <div class="disclaimer"><strong>Disclaimer:</strong> {html.escape(PRS_ALL_DISCLAIMER)}</div>
  <div class="report-footer">
    <p>Generated by ClawBio · gwas-prs</p>
    <p>Genetic data processed locally. No data was transmitted to external servers.</p>
  </div>
</body>
</html>
"""
    report_path = output_dir / "report.html"
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _run_all_prs(upload_path: Path, output_dir: Path) -> dict:
    results = []
    stdout_parts = []
    stderr_parts = []
    start = time.time()

    for trait_key, trait in TRAITS.items():
        if trait_key == "all_available":
            continue
        pgs_id = trait["pgs_id"]
        trait_dir = output_dir / pgs_id
        trait_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(CLAWBIO_PY),
            "run",
            "prs",
            "--input",
            str(upload_path),
            "--output",
            str(trait_dir),
            "--pgs-id",
            pgs_id,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        stdout_parts.append(proc.stdout)
        stderr_parts.append(proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or f"PRS run failed for {pgs_id}.")

        json_path = trait_dir / "prs_results.json"
        if not json_path.exists():
            raise RuntimeError(f"PRS result JSON was not created for {pgs_id}.")
        parsed = json.loads(json_path.read_text(encoding="utf-8"))
        if parsed:
            results.append({
                "result": parsed[0],
                "relative_report": f"{pgs_id}/report.html",
            })

    if not results:
        raise RuntimeError("No PRS results were produced.")

    _prs_all_html(results, output_dir)
    return {
        "duration_seconds": round(time.time() - start, 2),
        "stdout": "\n".join(stdout_parts),
        "stderr": "\n".join(stderr_parts),
    }


def _run_skill(fields: dict[str, str], upload_path: Path) -> dict:
    skill_key = fields.get("skill", "").strip()
    if skill_key not in SKILLS:
        raise ValueError("Choose a supported skill.")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"{timestamp}_{skill_key}"
    output_dir = RUN_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if skill_key == "drugphoto":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "skills" / "pharmgx-reporter" / "pharmgx_reporter.py"),
            "--input",
            str(upload_path),
            "--output",
            str(output_dir),
        ]
        drug = fields.get("drug", "").strip()
        if not drug:
            raise ValueError("Drug name is required for drugphoto.")
        cmd.extend(["--drug", drug])
        dose = fields.get("dose", "").strip()
        if dose:
            cmd.extend(["--dose", dose])
    else:
        cmd = [
            sys.executable,
            str(CLAWBIO_PY),
            "run",
            SKILLS[skill_key]["slug"],
            "--input",
            str(upload_path),
            "--output",
            str(output_dir),
        ]
        if skill_key == "prs":
            trait_key = fields.get("trait", "").strip()
            if trait_key not in TRAITS:
                raise ValueError("Choose a supported PRS trait.")
            if trait_key == "all_available":
                result = _run_all_prs(upload_path, output_dir)
                return {
                    "run_id": run_id,
                    "output_dir": str(output_dir),
                    "duration_seconds": result["duration_seconds"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                }
            cmd.extend(["--pgs-id", TRAITS[trait_key]["pgs_id"]])

    start = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    duration = round(time.time() - start, 2)

    report_path = output_dir / "report.html"
    if skill_key == "drugphoto" and not report_path.exists():
        report_path = _drugphoto_html(proc.stdout, output_dir)

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "Skill run failed.")
    if not report_path.exists():
        candidates = sorted(output_dir.glob("*.html"))
        if candidates:
            shutil.copyfile(candidates[0], report_path)
        else:
            raise RuntimeError("Skill completed but did not create an HTML report.")

    return {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "duration_seconds": duration,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


class PGxUIHandler(BaseHTTPRequestHandler):
    server_version = "ClawBioPGxUI/0.1"

    def log_message(self, format: str, *args) -> None:
        print(f"[pgx-ui] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            _html_response(self, INDEX_HTML)
            return
        if parsed.path == "/api/traits":
            _json_response(self, HTTPStatus.OK, {"traits": TRAITS})
            return
        if parsed.path.startswith("/assets/"):
            self._serve_asset(parsed.path)
            return
        if parsed.path.startswith("/reports/"):
            self._serve_report(parsed.path, parse_qs(parsed.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/reports/"):
            self._serve_report(
                parsed.path,
                parse_qs(parsed.query),
                include_body=False,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            fields, files = _parse_multipart(self)
            if "genotype" not in files:
                raise ValueError("Upload a 23andMe .txt file.")
            filename, payload = files["genotype"]
            safe_name = _safe_filename(filename)
            if not (safe_name.endswith(".txt") or safe_name.endswith(".txt.gz")):
                raise ValueError("Upload a .txt or .txt.gz 23andMe raw data file.")
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            upload_path = UPLOAD_ROOT / f"{int(time.time())}_{safe_name}"
            upload_path.write_bytes(payload)

            result = _run_skill(fields, upload_path)
            report_url = f"/reports/{result['run_id']}/report.html"
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "success": True,
                    "message": "Analysis complete.",
                    "output_dir": result["output_dir"],
                    "duration_seconds": result["duration_seconds"],
                    "report_url": report_url,
                    "download_url": report_url + "?download=1",
                },
            )
        except Exception as exc:
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {
                    "success": False,
                    "error": str(exc),
                },
            )

    def _serve_asset(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "assets":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        name = parts[1]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        asset_path = ASSET_ROOT / name
        if not asset_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = asset_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        if asset_path.suffix.lower() == ".png":
            content_type = "image/png"
        else:
            content_type = "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_report(
        self,
        path: str,
        query: dict[str, list[str]],
        include_body: bool = True,
    ) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[0] != "reports":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        run_id = parts[1]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        rel_parts = parts[2:]
        if any(part in ("", ".", "..") for part in rel_parts):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in rel_parts):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        report_path = RUN_ROOT / run_id / Path(*rel_parts)
        if not report_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = report_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        if report_path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif report_path.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        elif report_path.suffix == ".csv":
            content_type = "text/csv; charset=utf-8"
        else:
            content_type = "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if query.get("download") == ["1"]:
            self.send_header("Content-Disposition", f'attachment; filename="{run_id}_{report_path.name}"')
        self.end_headers()
        if include_body:
            self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="ClawBio 23andMe web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5120)
    args = parser.parse_args()

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), PGxUIHandler)
    print(f"ClawBio 23andMe UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ClawBio 23andMe UI")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
