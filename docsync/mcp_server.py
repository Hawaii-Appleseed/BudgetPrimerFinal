#!/usr/bin/env python3
"""An MCP server exposing a running docsync editor's reports, READ-ONLY.

Point any MCP client at this and it can answer "what is in this report?" —
every slot's markdown, every source and whether it is cited, every placed
object's geometry, every addressable element id — without a browser and
without knowing anything about the editor's internals.

    claude mcp add primer -- python3 /path/to/docsync/mcp_server.py

It is a thin client over the dev server's own /__inventory and /__ping
(report2027/tools/serve.py); PRIMER_URL overrides the default origin. No
dependencies, by the same argument serve.py makes for urllib over a package:
two endpoints and a line protocol do not justify one, and this has to run
wherever the editor already runs.

NO OUT-OF-BAND WRITES — and why `pilot` is not one
--------------------------------------------------
The editor holds the document in MEMORY: open a report, type, and content.md
on disk is already stale. A write from this side landing on DISK would be
silently overwritten by that session's next Save — the edit would vanish, with
nothing to see and nothing to undo. That hazard is why every read here is a
read, and why there is still no tool that touches a file.

`pilot` is the answer to the question this file used to leave open ("how would
an out-of-band write coordinate with an open editor's unsaved buffer?"): it
does not write out of band at all. It POSTs the verb to /__pilot, the dev
server hands it to the editor tab holding the live stream, and the verb runs
INSIDE that editor through window.docsync.api — same pushHistory (one ⌘Z
undoes it), same render() and its validation, same document the human is
looking at. Nothing is written behind the editor's back; the edit is simply
made by the editor, on request.

So: use the read tools to decide WHAT to change, and `pilot` to make the
change. `pilot` needs an editor tab open — with none, it says so rather than
falling back to a file write.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("PRIMER_URL", "http://localhost:8010").rstrip("/")
PROTOCOL = "2025-06-18"          # echoed back to a client that asks for another
TIMEOUT = 180                    # an elements render is a full build; be patient


def _get(path: str, params: dict | None = None) -> dict:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:                                   # noqa: BLE001
            return {"ok": False, "error": f"HTTP {e.code} from {url}"}
    except urllib.error.URLError as e:
        # By far the commonest failure, and worth naming rather than leaking a
        # connection-refused traceback: the editor simply is not running.
        return {"ok": False, "error": f"no editor server at {BASE} ({e.reason}). "
                "Start it (the Budget Primer Editor app, or `make -C report2027 "
                "live`), or set PRIMER_URL if it listens elsewhere."}
    except (TimeoutError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"{BASE}{path}: {e}"}


def _post(path: str, body: dict, timeout: float = TIMEOUT) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:                                   # noqa: BLE001
            return {"ok": False, "error": f"HTTP {e.code} from {BASE}{path}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"no editor server at {BASE} ({e.reason}). "
                "Start it (the Budget Primer Editor app, or `make -C report2027 "
                "live`), or set PRIMER_URL if it listens elsewhere."}
    except (TimeoutError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"{BASE}{path}: {e}"}


# ---------------------------------------------------------------- tools

def t_list_reports(_args: dict) -> dict:
    """Every report this editor serves."""
    reg = _get("/projects.json")
    if isinstance(reg, dict) and reg.get("ok") is False:
        return reg
    return {"ok": True, "reports": [
        {"id": pid, "name": e.get("name", pid), "repo": e.get("repo"),
         "local_root": e.get("local_root")}
        for pid, e in sorted(reg.items())]}


def t_status(args: dict) -> dict:
    """Build state: version, unpushed commits, and any build error."""
    return _get("/__ping", {"project": args["project"]})


def t_style_guide(_args: dict) -> dict:
    """Templates + house style from the running editor's engine."""
    return _get("/__templates")


def t_inventory(args: dict) -> dict:
    """The whole report as data. `elements` costs a render; skip it when the
    question is only about text."""
    return _get("/__inventory", {"project": args["project"],
                                 "elements": "1" if args.get("elements", True) else "0"})


def t_get_slot(args: dict) -> dict:
    """One slot's full markdown — the unit the editor edits."""
    inv = _get("/__inventory", {"project": args["project"], "elements": "0"})
    if not inv.get("ok"):
        return inv
    key = args["key"]
    for s in inv["slots"]:
        if s["key"] == key:
            return {"ok": True, "key": key, "md": s["md"]}
    near = [s["key"] for s in inv["slots"] if key.lower() in s["key"].lower()][:8]
    return {"ok": False, "error": f"no slot '{key}'",
            "did_you_mean": near or [s["key"] for s in inv["slots"][:8]]}


def t_search(args: dict) -> dict:
    """Find where a phrase appears — across slot markdown, source citations
    and text-box contents. The headless answer to "where does the report say
    X?", which otherwise means reading every slot."""
    inv = _get("/__inventory", {"project": args["project"], "elements": "0"})
    if not inv.get("ok"):
        return inv
    q = args["query"].lower()
    hits = []
    for s in inv["slots"]:
        if q in s["md"].lower():
            i = s["md"].lower().index(q)
            hits.append({"where": "slot", "key": s["key"],
                         "excerpt": s["md"][max(0, i - 60):i + len(q) + 60].strip()})
    for s in inv["sources"]:
        if q in (s["text"] + " " + s["url"]).lower():
            hits.append({"where": "source", "key": s["id"],
                         "excerpt": s["text"], "cites": s["cites"]})
    for b in inv["placed"]["boxes"]:
        if q in str(b.get("md", "")).lower():
            hits.append({"where": "textbox", "key": "text." + str(b.get("id")),
                         "page": b.get("page"), "excerpt": str(b.get("md"))[:160]})
    return {"ok": True, "query": args["query"], "count": len(hits), "hits": hits}


def t_uncited_sources(args: dict) -> dict:
    """Sources nothing cites. They RENDER while editing but refuse to publish,
    so this is the check worth running before anyone tries to ship."""
    inv = _get("/__inventory", {"project": args["project"], "elements": "0"})
    if not inv.get("ok"):
        return inv
    bad = [s for s in inv["sources"] if not s["cites"]]
    return {"ok": True, "uncited": bad, "count": len(bad),
            "publishable": not bad,
            "note": "an uncited source blocks publish/export; it is normal "
                    "MID-EDIT (a sentence cut to be moved) and only matters "
                    "when shipping" if bad else ""}


def t_pilot(args: dict) -> dict:
    """Run one window.docsync.api verb in the open editor (see module docstring:
    this is the editor editing itself on request, not a write behind its back)."""
    verb = (args.get("verb") or "").strip()
    if not verb:
        return {"ok": False, "error": "verb is required"}
    call = args.get("args", [])
    if not isinstance(call, list):
        call = [call]
    body = {"project": args.get("project"), "verb": verb, "args": call}
    if args.get("timeout") is not None:
        body["timeout"] = args["timeout"]
    # Aims the op at ONE editor. Only matters when the same project is open in
    # two browser profiles, which each elect their own claimant — but dropping
    # it silently would send the edit to the wrong document, so it is forwarded
    # whenever given.
    if args.get("tab"):
        body["tab"] = args["tab"]
    # The relay's own wait is the real clock; give the HTTP call a little more
    # so a timeout is reported by the server (which knows WHY) rather than here.
    r = _post("/__pilot", body, timeout=float(body.get("timeout", 30)) + 15)
    if r.get("ok") is False:
        return r
    # Unwrap: the caller cares about the VERB's answer, not the envelope.
    return r.get("result", {"ok": False, "error": "the editor returned nothing"})


def t_announce_change(args: dict) -> dict:
    """Tell the editor (and the person at it) that you changed this report.

    Call this BEFORE editing: `base` defaults to the report's current commit,
    which is exactly what Undo restores to. Writing a report's files directly
    leaves nothing dirty in the editor, so without this the work simply
    appears with no author and no way back.
    """
    return _post("/__agent/change", {
        "project": args["project"],
        "summary": args.get("summary", "changed this report"),
        "by": args.get("by", "Claude"),
        "base": args.get("base", ""),
    })


def t_agent_log(args: dict) -> dict:
    """What automated changes were announced on this report — and which ones
    the person REWOUND. Check this before trusting your own last edit: a
    'rewind' event means your change was undone and the files are back to the
    commit named in `base`."""
    return _get("/__agent/log", {"project": args["project"]})


TOOLS = [
    {"name": "list_reports", "fn": t_list_reports,
     "description": "List every report the running docsync editor serves.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "status", "fn": t_status,
     "description": "Build state for one report: version, unpushed commit count, "
                    "any build error.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {
         "project": {"type": "string", "description": "report id, e.g. budget-primer"}}}},
    {"name": "style_guide", "fn": t_style_guide,
     "description": "The Hawaiʻi Appleseed report templates and house style as "
                    "data: the templates on offer (with their colour schemes), "
                    "fonts, page metrics, and every text/shape pattern — "
                    "section heading, hairline, body, callout, pull-quote, "
                    "figure caption, footer — as ready-to-use addTextBox/"
                    "addShape style dicts, plus the step-by-step recipe for "
                    "building a properly styled report from a user's text. "
                    "START HERE when asked to create or extend a report.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "inventory", "fn": t_inventory,
     "description": "The whole report as data: every slot's markdown, every source "
                    "with its citation count, the geometry of everything placed, and "
                    "(unless elements=false) every addressable element id. Geometry "
                    "is only stored for placed objects; an unmoved designed element's "
                    "position needs the editor's own docsync.api.inventory().",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {
         "project": {"type": "string"},
         "elements": {"type": "boolean",
                      "description": "include addressable ids (costs a render); "
                                     "default true"}}}},
    {"name": "get_slot", "fn": t_get_slot,
     "description": "One slot's full markdown.",
     "inputSchema": {"type": "object", "required": ["project", "key"], "properties": {
         "project": {"type": "string"},
         "key": {"type": "string", "description": "slot key, e.g. whopays.p1"}}}},
    {"name": "search", "fn": t_search,
     "description": "Find a phrase across slots, sources and text boxes.",
     "inputSchema": {"type": "object", "required": ["project", "query"], "properties": {
         "project": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "uncited_sources", "fn": t_uncited_sources,
     "description": "Sources nothing cites — these block publishing.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {
         "project": {"type": "string"}}}},
    {"name": "announce_change", "fn": t_announce_change,
     "description": "Announce that you (an AI or a script) are about to change "
                    "a report's files directly. Shows a notice in the open "
                    "editor and gives the person a one-click rewind. Call it "
                    "BEFORE editing so the recorded base commit is the state "
                    "to restore to.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {
         "project": {"type": "string", "description": "report id"},
         "summary": {"type": "string", "description": "one line, shown to the person"},
         "by": {"type": "string", "description": "who is editing; defaults to Claude"},
         "base": {"type": "string", "description": "commit to rewind to; defaults to HEAD now"}}}},
    {"name": "agent_log", "fn": t_agent_log,
     "description": "Announced changes and rewinds for a report. A 'rewind' "
                    "event means the person undid an automated change — read "
                    "this before assuming your last edit is still in place.",
     "inputSchema": {"type": "object", "required": ["project"], "properties": {
         "project": {"type": "string", "description": "report id"}}}},
    {"name": "pilot", "fn": t_pilot,
     "description": "CHANGE a report: run one window.docsync.api verb in the open "
                    "editor. Verbs: inventory, status, audit, getSlot, setSlot, "
                    "setStyle, setBoxText, place, recolor, rotate, lock, group, "
                    "ungroup, remove, duplicate, addTextBox, addShape, addPage, "
                    "addSource, addEndnotesSection, applyScheme, batch, undo, "
                    "redo, save. Geometry is in "
                    "page inches. Every verb is ONE undo step, and returns what "
                    "actually happened (a clamped box, a refusal) — so no "
                    "screenshot is needed to know the result. Needs an editor tab "
                    "open; says so if there is none. Push stays with the human.",
     "inputSchema": {"type": "object", "required": ["project", "verb"], "properties": {
         "project": {"type": "string"},
         "verb": {"type": "string", "description": "e.g. setSlot, place, audit"},
         "args": {"type": "array",
                  "description": "positional args, same order as the JS call — "
                                 "e.g. [\"whopays.p1\", \"new markdown\"]"},
         "timeout": {"type": "number", "description": "seconds to wait, default 30"},
         "tab": {"type": "string",
                 "description": "aim at ONE editor (its docsync.api.status().tab). "
                                "Only needed when the same report is open in two "
                                "browser profiles; omit otherwise."}}}},
]
BY_NAME = {t["name"]: t for t in TOOLS}

INSTRUCTIONS = (
    "Read and drive the reports served by a running docsync draft editor.\n\n"
    "Start with list_reports, then inventory(project) — that one call carries "
    "every slot's markdown, every source, and every addressable element id, "
    "so there is no need to hunt.\n\n"
    "To CHANGE a report use pilot(project, verb, args): it runs the verb inside "
    "the open editor through window.docsync.api, so the edit is one undo step "
    "and lands in the document the human is looking at. Nothing here writes a "
    "file behind the editor's back — with no editor tab open, pilot says so.\n\n"
    "Prefer batch for several related edits (one undo entry, one render), and "
    "audit() over screenshots for anything mechanical: overlaps, a placed "
    "element parked on flow content (covers-flow), off-sheet elements, print "
    "overflow, uncited sources. Every verb returns geometry in "
    "page inches, so read the RESULT rather than looking at the page. Push "
    "stays with the human."
)


# ------------------------------------------------------------ JSON-RPC

def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _result(rid, result) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code, message) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def handle(req: dict) -> None:
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") or {}
    # A notification has no id and must draw no reply at all — answering one
    # is a protocol error, not a harmless extra.
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(rid, {
            "protocolVersion": asked if isinstance(asked, str) and asked else PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "docsync-primer", "version": "1.0.0"},
            "instructions": INSTRUCTIONS,
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": [
            {k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]})
    if method == "tools/call":
        name = params.get("name")
        spec = BY_NAME.get(name)
        if spec is None:
            return _error(rid, -32602, f"unknown tool '{name}'")
        args = params.get("arguments") or {}
        missing = [k for k in spec["inputSchema"].get("required", []) if k not in args]
        if missing:
            return _error(rid, -32602, f"{name}: missing {', '.join(missing)}")
        try:
            out = spec["fn"](args)
        except Exception as e:                                  # noqa: BLE001
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        # isError marks a FAILED call the model should read and react to,
        # rather than a transport fault — the payload still travels either way.
        return _result(rid, {
            "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
            "isError": not out.get("ok", True),
        })
    if rid is not None:
        _error(rid, -32601, f"unknown method '{method}'")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error")
            continue
        try:
            handle(req)
        except Exception as e:                                  # noqa: BLE001
            _error(req.get("id"), -32603, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
