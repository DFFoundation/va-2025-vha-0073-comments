# VA-2025-VHA-0073 comment browser

Classifications are produced by an AI-assisted reading of each filing, each tied to a
verbatim excerpt verified against the filing's text. **Human-level audit of the
classifications is not yet complete**; check counts and labels against the filings before
relying on them.

A static, single-page browser for public comments on VA's "Reproductive Health Services"
rule (docket VA-2025-VHA-0073, RIN 2900-AS31). It has two views:

* **By comment** (`#comments`): every filing in the set, with filters on the left (commenter
  type, position, organization type, filing type, evidence, attributes, arguments, status),
  search over names, titles and comment text, and CSV export of the current results.
* **By argument** (`#arguments`, `#argument/<code>`): arguments grouped under the headings of
  VA's response to comments in the final rule (90 FR 61310), with filing counts and
  position and filer-type splits that follow the filters. Each argument page shows VA's
  response passage beside the filings that raised the argument and their quotations.
  Arguments with no heading in VA's outline are grouped separately.

Every count is a count of filings (docket records), not people. Counts are computed in the
browser from `data/comments.json`, so they follow the filters.

The page is one `index.html` with inline CSS and JS, adapted from the OMB-2026-0034 browser.
Filing text is fetched from `data/text/<Document ID>.json` only when a document is opened.

## Rebuild the data

The token is not in the shell environment Claude Code uses, so source it explicitly:

    . ~/.airtable_env && python3 scripts/build_data.py

This rewrites `data/comments.json`, `data/arguments.json` and `data/text/`.

## Preview

The page fetches its data, so it must be served over HTTP, not opened as a file:

    python3 -m http.server

then open <http://localhost:8000/>.
