#!/usr/bin/env python3
"""repo-wiki MCP server: the LLM-written repo wikis, readable by agents.

WHY THIS EXISTS
---------------
repo-wiki (../../repo-wiki) spends hours of ornith time writing a wiki of each
repository in repos.txt, and until 2026-09-20 its only reader was a person with
a browser. The pages are the cheapest way for an agent to learn how this
cluster is laid out -- one 3-5k token page instead of walking the tree through
a GitHub tool -- but mkdocs serves rendered HTML and the markdown sits on an
RWO volume, so nothing could read it.

The mkdocs pod now carries a `raw` sidecar serving the markdown and the
generator's manifest.json on :8001. This server is a thin index over that:
list, read, search. It holds no state and no credential, never touches the
Kubernetes API, and cannot write anywhere.

DEPENDENCY-FREE ON PURPOSE
--------------------------
Same shape as platform-mcp, for the same reasons: pure stdlib, MCP over stdio
is newline-delimited JSON-RPC 2.0, the script is mounted from a ConfigMap onto
a stock python image. See ../platform-mcp/README.md.

There is no dollar sign in this file; Flux envsubst runs over the ConfigMap.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

WIKI_URL = os.environ.get("WIKI_URL", "http://repo-wiki.llm:8001").rstrip("/")
BROWSER_URL = os.environ.get("WIKI_BROWSER_URL", "https://wikis.ekenhome.se").rstrip("/")

MAX_HITS = 8
MAX_SNIPPETS = 3

# (repo, slug) -> (generated_at, text). A page only changes when the generator
# rewrites it, and the manifest says when that was.
_pages = {}


def _get(path, timeout=10):
    url = WIKI_URL + "/" + urllib.parse.quote(path)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _manifest():
    try:
        return json.loads(_get("manifest.json")).get("repos", {})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "the wiki has no manifest.json yet -- the generator has not completed a "
                "run since wiki-mcp was deployed (it runs nightly at 03:00)")
        raise


def _resolve(repos, repo):
    """Accept `owner/name`, a bare `name`, or nothing when there is one wiki."""
    if not repo:
        if len(repos) == 1:
            return next(iter(repos))
        raise ValueError("repo is required; one of: " + ", ".join(sorted(repos)))
    if repo in repos:
        return repo
    matches = [r for r in repos if r.split("/", 1)[-1] == repo]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"no wiki for {repo!r}; one of: " + ", ".join(sorted(repos)))


def _page_text(repo, page):
    key, stamp = (repo, page["slug"]), page.get("generated_at")
    cached = _pages.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    text = _get(f"{repo}/{page['slug']}.md")
    _pages[key] = (stamp, text)
    return text


def tool_wiki_list(repo=None):
    repos = _manifest()
    names = [_resolve(repos, repo)] if repo else sorted(repos)
    return {
        "note": "AI-generated from the repository at `source_sha`. Good for orientation; "
                "confirm anything you will act on against the repository itself.",
        "wikis": [{
            "repo": name,
            "source_sha": repos[name].get("sha"),
            "generated_at": repos[name].get("generated_at"),
            "pages": [{"page": p["slug"], "title": p["title"], "focus": p.get("focus", "")}
                      for p in repos[name].get("pages", [])],
        } for name in names],
    }


def tool_wiki_read(page, repo=None):
    repos = _manifest()
    name = _resolve(repos, repo)
    pages = {p["slug"]: p for p in repos[name].get("pages", [])}
    entry = pages.get(page)
    if entry is None:
        return {"error": f"no page {page!r} in {name}; one of: " + ", ".join(sorted(pages))}
    return {
        "repo": name,
        "page": page,
        "title": entry["title"],
        "source_sha": repos[name].get("sha"),
        "generated_at": entry.get("generated_at"),
        "built_from": entry.get("files", []),
        "browser_url": f"{BROWSER_URL}/{name}/{page}/",
        "markdown": _page_text(name, entry),
    }


def tool_wiki_search(query, repo=None):
    terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
    if not terms:
        return {"error": "empty query"}
    repos = _manifest()
    names = [_resolve(repos, repo)] if repo else sorted(repos)
    hits = []
    for name in names:
        for entry in repos[name].get("pages", []):
            try:
                lines = _page_text(name, entry).splitlines()
            except (urllib.error.URLError, OSError):
                continue
            matched = [(i, sum(line.lower().count(t) for t in terms)) for i, line in enumerate(lines)]
            matched = [(i, n) for i, n in matched if n]
            if not matched:
                continue
            body = "\n".join(lines).lower()
            covered = sum(1 for t in terms if t in body)
            best = sorted(matched, key=lambda m: -m[1])[:MAX_SNIPPETS]
            hits.append({
                "repo": name,
                "page": entry["slug"],
                "title": entry["title"],
                # Pages matching every term outrank pages repeating one of them.
                "score": covered * 1000 + sum(n for _, n in matched),
                "snippets": [{"line": i + 1, "text": "\n".join(lines[max(0, i - 1):i + 2]).strip()[:400]}
                             for i, _ in sorted(best)],
            })
    hits.sort(key=lambda h: -h["score"])
    return {"query": query, "hits": hits[:MAX_HITS],
            "next": "wiki_read the page for the full text" if hits else
                    "no match; wiki_list shows what the wiki covers"}


TOOLS = [
    {
        "name": "wiki_list",
        "description": (
            "List the AI-written wikis of the owner's git repositories (currently the "
            "home-ops Kubernetes GitOps repo) and the pages in each: architecture, "
            "networking and ingress, storage and backups, secrets, databases, the LLM "
            "platform, observability, CI and so on. START HERE when asked how this "
            "cluster or one of these repositories is set up."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string",
                         "description": "Optional. `owner/name` or bare `name`; omit for all."},
            },
        },
    },
    {
        "name": "wiki_read",
        "description": (
            "Read one wiki page as markdown (typically 3-5k tokens), with the list of source "
            "files it was written from and the commit it reflects. Page ids come from "
            "wiki_list or wiki_search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "string", "description": "Page id (slug), e.g. storage-and-backups."},
                "repo": {"type": "string",
                         "description": "Optional when there is only one wiki. `owner/name` or bare `name`."},
            },
            "required": ["page"],
        },
    },
    {
        "name": "wiki_search",
        "description": (
            "Case-insensitive keyword search across every wiki page. Returns the best pages "
            "with matching lines. Use plain keywords (`volsync restic`, `envoy gateway`), "
            "not a question."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Space-separated keywords."},
                "repo": {"type": "string", "description": "Optional. Restrict to one wiki."},
            },
            "required": ["query"],
        },
    },
]

HANDLERS = {
    "wiki_list": tool_wiki_list,
    "wiki_read": tool_wiki_read,
    "wiki_search": tool_wiki_search,
}

INSTRUCTIONS = (
    "AI-written wikis of the owner's git repositories, regenerated nightly from source. "
    "Use them to orient yourself before answering how this Kubernetes cluster or one of "
    "these repositories is put together: wiki_list, then wiki_read or wiki_search. The "
    "pages are a model's summary of the code at a given commit -- they can be stale or "
    "wrong in detail, so verify against the repository before acting on a specific value."
)


# --------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio. stdout carries protocol frames ONLY -- every
# diagnostic goes to stderr, because one stray print corrupts the stream and
# the client's failure mode is an unhelpful parse error.
# --------------------------------------------------------------------------
def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle(message):
    """Return a response dict, or None for a notification."""
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "repo-wiki", "version": "1.0.0"},
            "instructions": INSTRUCTIONS,
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        try:
            payload = handler(**arguments)
            is_error = isinstance(payload, dict) and "error" in payload
        except TypeError as exc:
            payload, is_error = {"error": f"bad arguments for {name}: {exc}"}, True
        except Exception as exc:  # never kill the loop over one bad call
            payload, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": is_error,
        })

    if method is not None and method.startswith("notifications/"):
        return None

    if request_id is None:
        return None  # unknown notification: the spec says stay silent
    return _error(request_id, -32601, f"method not found: {method}")


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            print(f"[wiki-mcp] unparseable frame: {exc}", file=sys.stderr, flush=True)
            continue
        try:
            response = _handle(message)
        except Exception as exc:
            print(f"[wiki-mcp] handler crashed: {exc!r}", file=sys.stderr, flush=True)
            response = _error(message.get("id"), -32603, "internal error")
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
