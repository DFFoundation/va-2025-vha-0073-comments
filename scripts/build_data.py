#!/usr/bin/env python3
"""
build_data.py — data for the VA-2025-VHA-0073 dashboard (two views: by comment,
by argument).

    . ~/.airtable_env && python3 scripts/build_data.py

Classifications come from the Phase 8 outputs (deepclass_2026-10-01/outputs_*), gated as
below. The eval (eval_2026-10-02) is run but its human-level audit is not yet complete; the
page's methodology says so.

* Gate: the same quote check as scripts/apply_deepclass.py (its own sq()). A record with any
  quote that cannot be found in its own source text shows as "classification pending",
  with no labels. If a record has several answers (a re-run round), the first one that
  passes is used, re-runs first.
* Airtable is read only (never written): current Commenter Type (triage, or Julia's
  decision, wins over Phase 8), Organization (from filing), Template family size,
  Posted Date, Title, Attachment Files.
* VA's response to each argument is cut from rule_text/final_2025-24061.txt between the
  outline heading's line and the next outline heading, with its 90 FR page.
* Per-record text goes to data/text/<Document ID>.json, keyed by ID and never by position
  (RUNBOOK Phase 11 trap).
Scope: the browser rule, read live from Airtable (rule_scope); no hand-kept list.
"""
import glob, importlib.util, json, os, re, sys, time, urllib.parse

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.expanduser("~/Claude/VA-2025-VHA-0073")
DC = os.path.join(PROJ, "deepclass_2026-10-01")
sys.path.insert(0, os.path.join(PROJ, "scripts"))
from import_docket import API, F, token, call  # noqa: E402

spec = importlib.util.spec_from_file_location("apply_deepclass", os.path.join(PROJ, "scripts", "apply_deepclass.py"))
ad = importlib.util.module_from_spec(spec); spec.loader.exec_module(ad)
sq = ad.sq

URL = "https://www.regulations.gov/comment/"
FR_URL = "https://www.federalregister.gov/documents/2025/12/31/2025-24061/reproductive-health-services"
AT_FIELDS = ["Document ID", "Commenter Type", "Organization (from filing)", "Template family size",
             "Posted Date", "Title", "Attachment Files", "Exclude", "VA responds to", "Commenter Sub-type", "Commenter Attributes"]


def pull(ids):
    tok, out, ids = token(), {}, sorted(ids)
    fids = [F[f] for f in AT_FIELDS]
    for i in range(0, len(ids), 40):
        f = "OR(" + ",".join('{Document ID}="%s"' % d for d in ids[i:i + 40]) + ")"
        off = None
        while True:
            q = ("?pageSize=100&returnFieldsByFieldId=true" + "".join("&fields%5B%5D=" + x for x in fids) +
                 "&filterByFormula=" + urllib.parse.quote(f) + ("&offset=" + off if off else ""))
            d = call(API + q, tok)
            for r in d["records"]:
                fl = r["fields"]
                out[fl.get(F["Document ID"], "").strip()] = {n: fl.get(F[n]) for n in AT_FIELDS}
            off = d.get("offset")
            if not off:
                break
            time.sleep(0.22)
    return out


def gate(ans, hay):
    """Return the list of failed checks, mirroring apply_deepclass.py."""
    fails = []
    found = lambda q: bool(ad.sqk(q)) and ad.sqk(q) in hay
    pos = (ans.get("position") or "").strip()
    if pos and pos not in ad.SKIP and not found(ans.get("evidence")):
        fails.append("position evidence")
    qs = ans.get("quotes") or {}
    for fld, key in (("arguments", "Arguments"), ("evidence_offered", "Evidence Offered"),
                     ("commenter_attributes", "Commenter Attributes")):
        for lab in ans.get(fld) or []:
            if not found((qs.get(fld) or {}).get(lab)):
                fails.append("%s: %s" % (key, lab))
    if ans.get("identity_quote") and not found(ans["identity_quote"]):
        fails.append("identity quote")
    if ans.get("argument_status") in ("Stance only", "No position") and ans.get("arguments"):
        fails.append("contradiction")
    return fails


def va_passages(vocab):
    lines = open(os.path.join(PROJ, "rule_text", "final_2025-24061.txt")).read().split("\n")
    outline = json.load(open(os.path.join(PROJ, "rule_text", "final_rule_comment_outline.json")))["outline"]
    starts = sorted(o["line"] for o in outline)
    # the response section ends where the next top-level part of the preamble begins
    end_all = next((i + 1 for i, l in enumerate(lines) if i + 1 > starts[-1] and
                    re.match(r"^(Executive Order|Regulatory Flexibility|Paperwork Reduction|Unfunded Mandates)", l.strip())),
                   len(lines))
    page_at, cur = {}, 61310
    for i, l in enumerate(lines, 1):
        m = re.match(r"\[\[Page (\d+)\]\]", l.strip())
        if m:
            cur = int(m.group(1))
        page_at[i] = cur

    def cut(a, b):
        paras, buf = [], []
        for l in lines[a - 1:b - 1]:
            if re.match(r"\[\[Page \d+\]\]", l.strip()):
                continue
            if not l.strip() or l.startswith("    "):
                if buf:
                    paras.append(" ".join(buf)); buf = []
            if l.strip():
                buf.append(l.strip())
        if buf:
            paras.append(" ".join(buf))
        paras = [re.sub(r"(\w)- (\w)", r"\1-\2", p) for p in paras]
        merged = []
        for p in paras:   # rejoin sentences split at a Federal Register page or column break
            if merged and not re.search(r"[.:;?!\")\]]$", merged[-1]) and re.match(r"^[a-z(]", p):
                merged[-1] += " " + p
            else:
                merged.append(p)
        return merged

    out = {}
    for o in vocab:
        if not o.get("va_line"):
            continue
        a = o["va_line"]
        if "." in o["code"]:
            b = next((s for s in starts if s > a), end_all)
        else:   # a section-level theme ("IV CHAMPVA (general)"): VA's whole section
            b = next((x["line"] for x in outline if "." not in x["code"] and x["line"] > a), end_all)
        paras = cut(a, b)
        # the first paragraph is VA's own heading, shown separately as va_title
        head = paras.pop(0) if paras and re.match(r"^([IVX]+|[A-Z]|\d+)\. ", paras[0]) else ""
        out[o["code"]] = {"page": page_at[a], "paras": paras, "heading": head}
    return out


RULE = ("AND(NOT({Template role}='Inherited'),NOT({Template role}='No text'),{Exclude}='',"
        "OR({Attachment Files}!='',{Commenter Type}='Organization',{VA responds to}!=''))")
LETTER_COPIES = ("AND({Template role}='Inherited',{Exclude}='',"
                 "OR({Attachment Files}!='',{Commenter Type}='Organization'))")


def ids_where(formula, extra=()):
    """Document IDs (and any extra fields) of every record matching an Airtable formula."""
    tok, out, off = token(), {}, None
    fids = [F["Document ID"]] + [F[x] for x in extra]
    while True:
        q = ("?pageSize=100&returnFieldsByFieldId=true" + "".join("&fields%5B%5D=" + x for x in fids) +
             "&filterByFormula=" + urllib.parse.quote(formula) + ("&offset=" + off if off else ""))
        d = call(API + q, tok)
        for r in d["records"]:
            out[r["fields"].get(F["Document ID"], "").strip()] = {x: r["fields"].get(F[x]) for x in extra}
        off = d.get("offset")
        if not off:
            return out
        time.sleep(0.22)


def rule_scope():
    """The browser rule (Julia, 2026-10-02), read live from Airtable:
    1. Template role is not Inherited / No text (each letter once)
    2. Exclude is empty
    3. Attachment Files is not empty, OR Commenter Type is Organization (from the filing, never the
       filer-entered Organization Name), OR `VA responds to` is set (VA answered this comment
       specifically; Julia, 2026-10-02)
    4. plus the Exemplar of any template family whose comment-letter copy was set aside as Inherited,
       so every family with a comment letter in it has its one copy here."""
    core = set(ids_where(RULE))
    copies = ids_where(LETTER_COPIES, ["Template family"])
    fams = sorted({c["Template family"] for c in copies.values() if c["Template family"]})
    added = set()
    for fam in fams:
        ex = ids_where("AND({Template family}='%s',{Template role}='Exemplar',{Exclude}='')" % fam)
        added |= set(ex) - core
    print("rule scope: %d by filters 1-3 + %d template exemplar(s) for set-aside comment-letter copies %s"
          % (len(core), len(added), sorted(added)))
    return core | added


def load_leads(in_browser):
    """Lead statements from strongest_2026-10-02/review.csv (gated by check_pilot.py). Only versions
    whose gate status is ok, whose lead is still in the browser, and that Julia has not marked
    `drop`. `reviewed` is true only for call == keep. The page states in its methodology that
    selections have not yet been reviewed by a person."""
    import csv, collections
    path = os.path.join(PROJ, "strongest_2026-10-02", "review.csv")
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    if not os.path.exists(path):
        return {}
    rows = list(csv.DictReader(open(path)))
    if rows and "rank" not in rows[0]:
        return {}            # v1 sheet (single winner): not this format
    for r in rows:
        call = (r.get("call") or "").strip().lower()
        if r["status"] != "ok" or call == "drop" or r["lead"] not in in_browser:
            continue
        out[r["argument"]][r["side"]].append({
            "rank": int(r["rank"] or 0), "version": r["version"], "lead": r["lead"], "excerpt": r["excerpt"],
            "also": [d for d in r["also"].split() if d in in_browser], "beats_next": r["beats_next"],
            "va_quote": r["va_counterpoint"] if r["va_gate"] == "ok" else "", "va_answers": r["va_answers"].startswith("yes"),
            "va_note": r["va_note"], "reviewed": call == "keep"})
    for sides in out.values():
        for v in sides.values():
            v.sort(key=lambda x: x["rank"])
    return {k: dict(v) for k, v in out.items()}


def docket_totals():
    """Docket-level figures for the header. VA's count is from the final rule's own text; the posted
    count matched the regulations.gov API on 2026-10-01; template figures come from Phase 7's
    near-duplicate clustering (dedupe_2026-10-01/read_plan.json), computed here, never typed."""
    import collections
    plan = json.load(open(os.path.join(PROJ, "dedupe_2026-10-01", "read_plan.json")))
    fam = collections.Counter(p["cluster"] for p in plan.values() if p.get("cluster"))
    rule = open(os.path.join(PROJ, "rule_text", "final_2025-24061.txt")).read()
    m = re.search(r"([\d,]+)\s+document\s+submissions,\s+which\s+included\s+approximately\s+([\d,]+)\s+total\s+comments", rule)
    if not m:
        sys.exit("VA's comment count sentence not found in the final rule text")
    # manual exclusions, read live; VA-specific matching, read from the confirmed review sheet
    excluded = len(ids_where("{Exclude}!=''"))
    vs = os.path.join(PROJ, "va_specific_2026-10-02")
    desc = json.load(open(os.path.join(vs, "descriptions.json")))
    import csv as _csv
    conf = [r for r in _csv.DictReader(open(os.path.join(vs, "review_matches.csv"))) if r["document_id"] and r["call"] == "confirm"]
    return {"excluded": excluded, "va_specific_total": len(desc), "va_specific_headings": len({d["heading"] for d in desc}),
            "va_specific_matched": len({r["desc"] for r in conf}),
            "empty": sum(p["route"] == "empty" for p in plan.values()),
            "va_comments": int(m.group(2).replace(",", "")), "va_submissions": int(m.group(1).replace(",", "")),
            "posted": len(plan), "posted_as_of": "1 October 2026",
            "families": len(fam), "in_families": sum(fam.values()), "largest_family": max(fam.values()),
            "copies_set_aside": sum(p["route"] == "inherit" for p in plan.values())}


def main():
    scope = rule_scope()
    vocab = json.load(open(os.path.join(PROJ, "rule_text", "arguments_vocabulary.json")))["options"]
    by_label = {o["label"]: o for o in vocab}

    src = {}
    for fp in glob.glob(os.path.join(DC, "shards_*", "shard_*.json")):
        for r in json.load(open(fp)):
            src[r["document_id"]] = r
    answers = {}
    # re-run rounds first, so a passing re-run answer wins over a quarantined original
    fps = sorted(glob.glob(os.path.join(DC, "outputs_*", "shard_*.json")), key=lambda p: ("rerun" not in p, p))
    for fp in fps:
        for r in json.load(open(fp)):
            answers.setdefault(r["document_id"], []).append(r)

    at = pull(scope)
    missing = sorted(scope - set(at))
    if missing:
        sys.exit("not in Airtable: %s" % missing[:5])
    excluded = sorted(d for d in scope if at[d].get("Exclude"))   # Airtable `Exclude`, read live
    scope -= set(excluded)
    for d in excluded:
        try: os.remove(os.path.join(SITE, "data", "text", d + ".json"))
        except FileNotFoundError: pass
    print("excluded (Airtable Exclude set): %s" % (", ".join(excluded) or "none"))

    os.makedirs(os.path.join(SITE, "data", "text"), exist_ok=True)
    recs, n_pending, n_unanswered = [], 0, 0
    for did in sorted(scope, key=lambda d: int(d.rsplit("-", 1)[1])):
        s, a = src.get(did), at[did]
        hay = ad.sqk((s or {}).get("comment_box", "") + "\n" + ((s or {}).get("text") or ""))
        ans, status = None, "unclassified"
        for cand in answers.get(did, []):
            if not gate(cand, hay):
                ans, status = cand, "classified"; break
            status = "pending"
        n_pending += status == "pending"; n_unanswered += status == "unclassified"
        ctype = a["Commenter Type"] or (ans or {}).get("commenter_type")
        ctype = ctype if ctype in ("Individual", "Organization") else ""
        is_org = ctype == "Organization"
        att = a["Attachment Files"] or ""
        rec = {
            "id": did, "url": URL + did,
            "org": (a["Organization (from filing)"] or "").strip(),
            # Individuals' names are not shown (Julia, 2026-10-01): regulations.gov titles are
            # "Comment from <name>", so the title is kept for organisations only.
            "title": (a["Title"] or "") if ctype == "Organization" else "",
            "date": a["Posted Date"] or "",
            "comment": ((s or {}).get("comment_box") or "")[:600],
            "has_attachment": bool(att.strip()),
            "family_size": a["Template family size"],
            "kind": ctype, "status": status,
            "va_responds": [{"code": l.split(":", 1)[0].strip(), "sentence": l.split(":", 1)[1].strip()}
                            for l in (a.get("VA responds to") or "").split("\n") if ":" in l],
        }
        if ans:
            qa = (ans.get("quotes") or {}).get("arguments") or {}
            codes = [by_label[l]["code"] for l in ans.get("arguments") or [] if l in by_label]
            pos = ans.get("position")
            rec.update({
                "position": pos if pos not in ad.SKIP else "",
                "position_quote": ans.get("evidence") if pos not in ad.SKIP else "",
                "argument_status": ans.get("argument_status") or "",
                "arguments": codes,
                "argument_quotes": {by_label[l]["code"]: q for l, q in qa.items() if l in by_label},
                # Airtable wins (Julia's corrections, e.g. trade associations -> Nonprofit, 2026-10-02)
                "subtype": a.get("Commenter Sub-type") or (ans.get("commenter_subtype") if ans.get("commenter_subtype") not in ad.SKIP else ""),
                "filing": ans.get("filing_type") or [],
                "evidence": ans.get("evidence_offered") or [],
                # Airtable wins (Julia's additions, e.g. "D25 partner", 2026-10-02)
                "attributes": ((a.get("Commenter Attributes") or ans.get("commenter_attributes") or []) if is_org else []),
                "scope": (ans.get("geographic_scope") if ans.get("geographic_scope") not in ad.SKIP else "") if is_org else "",
                "state": (ans.get("state") or "") if is_org else "",
            })
        if is_org and not rec.get("attributes") and a.get("Commenter Attributes"):
            # person-set attributes (e.g. "D25 partner") show even while the AI labels are held for review
            rec["attributes"] = a["Commenter Attributes"]
        recs.append(rec)
        if s:
            json.dump({"comment_box": s.get("comment_box") or "", "text": s.get("text") or "",
                       "is_ocr": s.get("is_ocr", False), "truncated": s.get("truncated") or False},
                      open(os.path.join(SITE, "data", "text", did + ".json"), "w"), ensure_ascii=False)

    # the browser's letters, for apply_deepclass.py --ids (so Airtable gets exactly what is shown)
    json.dump(sorted(r["id"] for r in recs), open(os.path.join(DC, "scopes", "browser_scope.json"), "w"), indent=1)

    # text files of records no longer in scope (dropped by the rule or excluded) are removed
    keep = {r["id"] + ".json" for r in recs}
    stale = [f for f in os.listdir(os.path.join(SITE, "data", "text")) if f.endswith(".json") and f not in keep]
    for f in stale:
        os.remove(os.path.join(SITE, "data", "text", f))
    if stale:
        print("removed %d stale text file(s): %s" % (len(stale), ", ".join(sorted(x[:-5] for x in stale))))

    # full-text search: one lazy-loaded file, keyed by Document ID. Exhibit bodies are not in
    # the shard text (decided dispositions), so they are not searchable either.
    search = {}
    for r in recs:
        s = src.get(r["id"]) or {}
        search[r["id"]] = re.sub(r"\s+", " ", ((s.get("comment_box") or "") + " " + (s.get("text") or "")).lower()).strip()
    json.dump(search, open(os.path.join(SITE, "data", "search.json"), "w"), ensure_ascii=False)

    # argument index, in VA's outline order, then the gap labels
    va = va_passages(vocab)
    outline = json.load(open(os.path.join(PROJ, "rule_text", "final_rule_comment_outline.json")))["outline"]
    sections = {o["code"]: o["title"] for o in outline if "." not in o["code"]}
    args = []
    for o in vocab:
        code = o["code"]
        sec = code.split(".")[0] if o.get("va_line") else "GAP"
        raised = [r for r in recs if code in r.get("arguments", [])]
        args.append({
            "code": code, "label": o["label"], "section": sec,
            "va_title": o.get("va_title", ""), "in_outline": bool(o.get("va_line")),
            "va_page": va.get(code, {}).get("page"), "va_text": va.get(code, {}).get("paras", []),
            "va_heading": va.get(code, {}).get("heading", ""),
            "filings": len(raised),
            "by_position": {p: sum(r.get("position") == p for r in raised) for p in ("Oppose", "Support", "Mixed", "Neutral or technical")},
            "by_kind": {k: sum(r["kind"] == k for r in raised) for k in ("Organization", "Individual")},
        })
    leads = load_leads({r["id"] for r in recs})
    for a in args:
        a["leads"] = leads.get(a["code"], {})
    print("lead statements: %d versions on %d arguments (%d reviewed 'keep')" % (
        sum(len(v) for x in leads.values() for v in x.values()), len(leads),
        sum(l["reviewed"] for x in leads.values() for v in x.values() for l in v)))
    sec_list = [{"code": c, "title": t} for c, t in sections.items()] + \
               [{"code": "GAP", "title": "Arguments not in VA's response outline"}]

    meta = {
        "docket": "VA-2025-VHA-0073", "rin": "2900-AS31", "title": "Reproductive Health Services",
        "subtitle": "Organizations and comments with attachments",
        "final_rule_url": FR_URL, "final_rule_cite": "90 FR 61310 (Dec. 31, 2025)",
        "va_not_addressed_note": json.load(open(os.path.join(PROJ, "rule_text", "final_rule_comment_outline.json")))["not_addressed_by_design"],
        "built": time.strftime("%Y-%m-%d %H:%M"),
        "totals": docket_totals(),
        "count": len(recs), "classified": sum(r["status"] == "classified" for r in recs),
        "pending": n_pending, "unclassified": n_unanswered,
    }
    json.dump({**meta, "records": recs}, open(os.path.join(SITE, "data", "comments.json"), "w"), ensure_ascii=False, indent=0)
    json.dump({"sections": sec_list, "arguments": args}, open(os.path.join(SITE, "data", "arguments.json"), "w"),
              ensure_ascii=False, indent=0)
    print("records %d: classified %d, pending (quote gate) %d, not yet answered %d" %
          (len(recs), meta["classified"], n_pending, n_unanswered))
    print("arguments %d (%d with a VA passage); raised at least once: %d" %
          (len(args), sum(bool(a["va_text"]) for a in args), sum(a["filings"] > 0 for a in args)))


if __name__ == "__main__":
    main()
