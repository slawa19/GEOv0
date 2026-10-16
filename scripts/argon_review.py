"""Second external reviewer: Gemini Argon through OpenRouter, one frozen range (040).

Selective by design: AGENTS.md section 15 names the three triggers, runbook section 5
(`docs/external-review-runbook.md`) the mechanics and the prompt template, and
`docs/reviewer-usefulness-ledger.md` the record every call leaves.

WHAT IT SENDS. One chat-completions request: the frozen prompt, the commit list of
BASE..HEAD, `git diff BASE..HEAD` and every touched text file in full as committed at
HEAD (`git show <HEAD>:<path>`). The working tree is never read, so uncommitted edits
cannot reach the reviewer. Binary and non-UTF-8 files are named in `meta.json`.

WHAT IT REFUSES (exit 3, nothing sent): no `OPENROUTER_API_KEY` (live mode), a SHA that
is not a commit, an empty range (no diff, also after `--paths`), a touched path that looks
like a secret (`SECRET_PATTERNS`, except the versioned templates `SECRET_EXCEPTIONS`), a
payload that contains the key, a payload over `MAX_PAYLOAD_CHARS`, an `--out` directory
that is not empty (a stale `final.md` would read as this run's answer). Nothing is ever
truncated: a reviewer that silently saw half the diff is a false green.

WHAT COUNTS AS A REVIEW (exit 0): HTTP 200, a response model that is the requested id or
its dated canonical form (`<id>-<suffix>`), `finish_reason == "stop"`, non-empty content
and every regex of `MARKERS` on a line of its own. Anything else is UNVERIFIED (exit 2),
never "no findings".

WHAT IT DOES NOT SEE. Whether the findings are true, and whether the model read all it
was sent: the markers prove a complete answer, not a careful one; the orchestrator
reproduces every P1/P2 (runbook section 4). The model runs no tests and reads no files.
`--timeout` bounds each socket operation, not the whole call. There is no retry on
purpose: a retry is a second paid call, so a failed run is rerun by hand and recorded.
Redirects are not followed: urllib would carry the `Authorization` header to wherever a
3xx points, so a redirect is an HTTP error, UNVERIFIED, and the key stays with the API.
"""

from __future__ import annotations

import argparse
import fnmatch
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

#: The owner's `openrouter/google/gemini-4-argon`; in the API the id has no `openrouter/`
#: prefix. Confirmed in the free catalog `GET /api/v1/models` on 2026-10-16 (040 T4001):
#: context 1 048 576 tokens, max completion 65 536.
ARGON_MODEL = "google/gemini-4-argon"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
#: About 400k tokens: inside the 1M context and a bound on the price of one call.
MAX_PAYLOAD_CHARS = 1_500_000
DEFAULT_TIMEOUT_SECONDS = 900
#: Matched case-insensitively against every component of every touched path, including
#: deleted ones and both sides of a rename. Wider than the spec's `secrets*` on purpose
#: (`*secret*` also names `client_secret.json`).
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "*secret*")
#: The one exception, for the LAST component only: the versioned templates that
#: `.gitignore` itself lets in (`!.env*.example`) - `.env.example`,
#: `admin-ui/.env.local.example`, `simulator-ui/v2/.env.example`. A real `.env` or
#: `.env.local`, and an `.example` under a secret-looking directory, are still refused.
SECRET_EXCEPTIONS = (".env*.example",)
MARKERS = (r"^VERDICT-[A-Z0-9-]+: ", r"^CLASS-1-COUNT: \d+", r"^REVIEW-COMPLETE: YES")
EXIT_OK, EXIT_UNVERIFIED, EXIT_REFUSED = 0, 2, 3


class Refused(Exception):
    """A check before sending failed; nothing was sent."""


class Run:
    """The output directory of one run. Every file goes through `write`, which redacts."""

    def __init__(self, out: Path, key: str | None) -> None:
        self.out, self.key = out, key
        out.mkdir(parents=True, exist_ok=True)

    def redact(self, text: str) -> str:
        return text.replace(self.key, "<redacted>") if self.key else text

    def write(self, name: str, text: str) -> None:
        (self.out / name).write_text(self.redact(text), encoding="utf-8")

    def write_json(self, name: str, data: object) -> None:
        self.write(name, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with (self.out / "progress.log").open("a", encoding="utf-8") as handle:
            handle.write(self.redact(f"{stamp} {message}") + "\n")


def git_bytes(*args: str) -> bytes:
    result = subprocess.run(["git", *args], capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise Refused(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def git(*args: str) -> str:
    return git_bytes(*args).decode("utf-8")


def resolve_commit(sha: str) -> str:
    try:
        return git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").strip()
    except Refused:
        raise Refused(f"{sha!r} does not resolve to a commit") from None


def is_secret_path(path: str) -> bool:
    *dirs, name = path.lower().split("/")

    def matches(part: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatchcase(part, pat) for pat in patterns)

    if any(matches(part, SECRET_PATTERNS) for part in dirs):
        return True
    return matches(name, SECRET_PATTERNS) and not matches(name, SECRET_EXCEPTIONS)


def build_payload(base: str, head: str, prompt: str, paths: list[str]) -> tuple[str, dict]:
    """The text sent to the reviewer and what the meta records. Committed objects only."""
    spec = ["--", *paths] if paths else []
    # No external diff driver, no textconv, no colour: the text is git's own, of blobs only.
    plain = ("--no-ext-diff", "--no-textconv", "--no-color")

    def changed(*options: str) -> list[str]:
        out = git("diff", *plain, "-z", "--no-renames", *options, base, head, *spec)
        return [name for name in out.split("\0") if name]

    secrets = [p for p in changed("--name-only") if is_secret_path(p)]
    if secrets:
        raise Refused(f"touched paths look like secrets: {secrets}; narrow --paths")
    binaries = {row[4:] for row in changed("--numstat") if row.startswith("-\t-\t")}
    files, skipped = [], sorted(binaries)
    for path in changed("--name-only", "--diff-filter=d"):  # alive at HEAD
        if path in binaries:
            continue
        try:
            files.append((path, git_bytes("show", f"{head}:{path}").decode("utf-8")))
        except UnicodeDecodeError:
            skipped.append(path)
    commits = git("log", "--format=%H %s", f"{base}..{head}")
    diff = git("diff", *plain, "--find-renames", f"{base}..{head}", *spec)
    if not diff.strip():
        raise Refused(f"the range {base[:8]}..{head[:8]} has no diff (check --base, --head, --paths)")
    parts = [
        prompt.rstrip("\n"),
        f"\n\n## Commits {base}..{head}\n\n{commits}",
        f"\n## Diff {base}..{head}\n\n```diff\n{diff}```\n",
        f"\n## Touched files in full at {head}\n",
    ]
    parts += [f"\n### {path}\n\n```\n{text}\n```\n" for path, text in files]
    if skipped:
        listed = "".join(f"- {p}\n" for p in sorted(skipped))
        parts.append(f"\n## Binary or non-UTF-8 files, not embedded\n\n{listed}")
    meta = {"files_included": [p for p, _ in files], "skipped_binaries": sorted(skipped)}
    return "".join(parts), meta


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx becomes an `HTTPError`: the key is never re-sent to another URL."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def post(body: dict, key: str, timeout: float) -> tuple[int, str]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "GEOv0 argon_review",
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def verify(status: int, raw: str, requested: str) -> tuple[dict, str | None, str]:
    """(fields for the meta, the reason it is UNVERIFIED or None, the final text)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    data = data if isinstance(data, dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    cost = usage.get("cost")
    fields = {
        "http_status": status,
        "response_model": data.get("model"),
        "generation_id": data.get("id"),
        "finish_reason": choice.get("finish_reason"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        # A number only when the API reported one; never an estimate (040 ledger rule).
        "cost": cost
        if isinstance(cost, (int, float)) and not isinstance(cost, bool)
        else "unknown",
    }
    final = content if isinstance(content, str) else ""
    if status == 0:
        return fields, "no HTTP response (transport error, see response.json)", final
    if status != 200:
        return fields, f"HTTP {status}", final
    if not data:
        return fields, "response is not a JSON object", final
    model = fields["response_model"]
    if not isinstance(model, str) or not (model == requested or model.startswith(f"{requested}-")):
        return fields, f"response model {model!r} is not the requested {requested!r}", final
    if fields["finish_reason"] != "stop":
        return fields, f"finish_reason is {fields['finish_reason']!r}, not 'stop'", final
    if not final.strip():
        return fields, "empty content", final
    missing = [m for m in MARKERS if not re.search(m, final, re.MULTILINE)]
    if missing:
        return fields, f"markers missing from final.md: {missing}", final
    return fields, None, final


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One frozen-range review by Gemini Argon (runbook section 5)."
    )
    parser.add_argument("--base", required=True, help="a committed SHA")
    parser.add_argument("--head", required=True, help="a committed SHA")
    parser.add_argument("--prompt", required=True, type=Path, help="frozen prompt file")
    parser.add_argument("--out", type=Path, help="default .local-run/argon-review/<id>/")
    parser.add_argument("--model", default=ARGON_MODEL)
    parser.add_argument("--paths", nargs="+", default=[], help="limit the diff to paths")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="build and write; no call")
    mode.add_argument("--verify-response", type=Path, help="a saved response; no call")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    live = not args.dry_run and args.verify_response is None
    key = os.environ.get("OPENROUTER_API_KEY") or None
    try:
        if live and not key:
            raise Refused("OPENROUTER_API_KEY is not set")
        base, head = resolve_commit(args.base), resolve_commit(args.head)
        prompt = args.prompt.read_text(encoding="utf-8")
        saved = args.verify_response.read_text(encoding="utf-8") if args.verify_response else ""
        text, files_meta = build_payload(base, head, prompt, args.paths)
        if key and key in text:
            raise Refused("the payload contains the API key")
        if len(text) > MAX_PAYLOAD_CHARS:
            raise Refused(
                f"payload is {len(text)} chars, over MAX_PAYLOAD_CHARS={MAX_PAYLOAD_CHARS};"
                " narrow the range or pass --paths (nothing is truncated)"
            )
        top = Path(git("rev-parse", "--show-toplevel").strip())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = args.out or top / ".local-run" / "argon-review" / f"{stamp}-{base[:7]}-{head[:7]}"
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise Refused(f"--out {out} exists and is not an empty directory; one run, one directory")
    except (Refused, OSError, UnicodeDecodeError) as error:
        message = str(error).replace(key, "<redacted>") if key else str(error)
        print(f"REFUSED: {message}", file=sys.stderr)
        return EXIT_REFUSED

    run = Run(out, key)
    mode = "live" if live else ("dry-run" if args.dry_run else "verify-response")
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": text}],
        "temperature": 0,
        "usage": {"include": True},
    }
    meta: dict = {
        "mode": mode,
        "requested_model": args.model,
        "base": base,
        "head": head,
        "paths": args.paths,
        "payload_chars": len(text),
        "max_payload_chars": MAX_PAYLOAD_CHARS,
        **files_meta,
        "started_utc": stamp,
    }
    run.write("prompt.txt", prompt)
    run.write_json("payload.json", body)
    run.log(f"mode {mode}; payload {len(text)} chars, {len(files_meta['files_included'])} files")
    if args.dry_run:
        meta.update(status="DRY-RUN", exit_status=EXIT_OK)
        run.write_json("meta.json", meta)
        print(f"DRY-RUN: nothing sent; {run.out}")
        return EXIT_OK

    started = time.monotonic()
    if live:
        run.log(f"POST {API_URL} model {args.model} timeout {args.timeout}s")
        try:
            status, raw = post(body, str(key), args.timeout)
        except (OSError, http.client.HTTPException) as error:  # URLError, timeouts
            status, raw = 0, json.dumps({"transport_error": f"{type(error).__name__}: {error}"})
    else:
        status, raw = 200, saved
    meta["wall_seconds"] = round(time.monotonic() - started, 1)
    run.write("response.json", raw)
    fields, reason, final = verify(status, raw, args.model)
    run.write("final.md", final)
    meta.update(fields, status="UNVERIFIED" if reason else "VERIFIED", reason=reason)
    meta["exit_status"] = EXIT_UNVERIFIED if reason else EXIT_OK
    run.write_json("meta.json", meta)
    run.log(f"status {meta['status']}" + (f": {reason}" if reason else ""))
    if reason:
        print(run.redact(f"UNVERIFIED: {reason}; {run.out}"), file=sys.stderr)
        return EXIT_UNVERIFIED
    print(f"VERIFIED: {run.out}")
    print("Ledger stub for docs/reviewer-usefulness-ledger.md (fill each ? after checking):")
    print(
        f"| {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} | ? | `{base[:8]}..{head[:8]}` | ? "
        f"| {args.model} / {meta['response_model']} | ? / ? / ? / ? | ? "
        f"| {meta['wall_seconds']} s | {meta['cost']} "
        f"| {meta['prompt_tokens']} / {meta['completion_tokens']} |"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
