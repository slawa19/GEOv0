"""Second external reviewer: Gemini Argon through OpenRouter, one frozen range (040).

Selective by design: AGENTS.md section 15 names the three triggers, runbook section 5
(`docs/external-review-runbook.md`) the mechanics and the prompt template, and
`docs/reviewer-usefulness-ledger.md` the record every call leaves.

WHAT IT SENDS. One chat-completions request: the frozen prompt (read from disk, the one
file that is not a git object), the commit list of BASE..HEAD, the diff BASE..HEAD and
every touched text file in full as the blob recorded at HEAD (`cat-file blob <oid>`).
The working tree is never read, so uncommitted edits cannot reach the reviewer. Binary
and non-UTF-8 files are named in `meta.json`.

HOW GIT IS READ (`git_bytes`). Plumbing only (`rev-parse`, `diff-tree`, `rev-list`,
`cat-file`), never `log` or `show`. Every git child gets an environment WITHOUT the key
and without inherited `GIT_*` variables, with system and global config off
(`GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL=os.devnull`), replacement objects off
(`GIT_NO_REPLACE_OBJECTS`, `--no-replace-objects`: a `refs/replace/` entry cannot swap a
frozen blob), lazy fetch off, no pager, `core.fsmonitor=false`, `log.showSignature=false`,
and diffs with `--no-ext-diff --no-textconv`. The repository's own `.git/config` is still
read - git cannot be told to skip it - so the boundary is: no helper that config can name
is reached by these reads (signature, external diff, textconv, fsmonitor, pager, lazy
fetch), and if one were, it would not see the key. Hooks do not run on reads.

WHAT IT REFUSES (exit 3, nothing sent): no `OPENROUTER_API_KEY` (live mode) or one that is
not header-safe (a trailing CR/LF; the value is never echoed), a SHA that is not a commit,
a prompt that declares no verdict values, an empty range (no diff, also after `--paths`),
a gitlink or any tree entry that is not a blob, a touched path that looks like a secret
(`SECRET_PATTERNS`, except the versioned templates `SECRET_EXCEPTIONS` - by NAME; their
content is not inspected), a payload that contains the key, a payload over
`MAX_PAYLOAD_CHARS`, an `--out` directory that is not empty (a stale `final.md` would read
as this run's answer). Nothing is ever truncated: a reviewer that silently saw half the
diff is a false green.

WHAT COUNTS AS A REVIEW (exit 0): HTTP 200 with a body under `MAX_RESPONSE_BYTES`, a
response model from `ACCEPTED_RESPONSE_MODELS` (the requested id, or its canonical ids
established in the catalog; never a prefix family), `finish_reason == "stop"`, non-empty
content, and complete marker lines: every `VERDICT-<TAG>: <VALUE>` with a value the prompt
declared, every `CLASS-1-COUNT: <digits>`, every `REVIEW-COMPLETE: YES`, each at least once
and nothing else on those lines. Anything else is UNVERIFIED (exit 2), never "no findings".

WHAT IT DOES NOT SEE. Whether the findings are true, and whether the model read all it
was sent: the markers prove a complete answer, not a careful one; the orchestrator
reproduces every P1/P2 (runbook section 4). The model runs no tests and reads no files.
`--timeout` bounds each socket operation, not the whole call. There is no retry on
purpose: a retry is a second paid call, so a failed run is rerun by hand and recorded.
Redirects are not followed: urllib would carry the `Authorization` header to wherever a
3xx points, so a redirect is an HTTP error, UNVERIFIED, and the key stays with the API.
Proxy discovery is urllib's default (environment, and the registry on Windows); a CONNECT
proxy sees the host, not the header.
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
#: canonical id `google/gemini-4-argon-20261013`, context 1 048 576 tokens, max completion
#: 65 536.
ARGON_MODEL = "google/gemini-4-argon"
#: The response models accepted for a requested id: the id itself and the canonical ids
#: the catalog established for it. Any other requested model is accepted only verbatim.
ACCEPTED_RESPONSE_MODELS = {ARGON_MODEL: (ARGON_MODEL, "google/gemini-4-argon-20261013")}
KEY_VARIABLE = "OPENROUTER_API_KEY"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
#: About 400k tokens: inside the 1M context and a bound on the price of one call.
MAX_PAYLOAD_CHARS = 1_500_000
#: A complete answer of 65 536 tokens is well under 1 MB; more is not a review.
MAX_RESPONSE_BYTES = 8_000_000
DEFAULT_TIMEOUT_SECONDS = 900
#: Matched case-insensitively against every component of every touched path, including
#: deleted ones and both sides of a rename. Wider than the spec's `secrets*` on purpose
#: (`*secret*` also names `client_secret.json`).
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "*secret*")
#: The one exception, for the LAST component only: the versioned templates that
#: `.gitignore` itself lets in (`!.env*.example`) - `.env.example`,
#: `admin-ui/.env.local.example`, `simulator-ui/v2/.env.example`. A real `.env` or
#: `.env.local`, and an `.example` under a secret-looking directory, are still refused.
#: Allowed by name: what such a file contains is not inspected.
SECRET_EXCEPTIONS = (".env*.example",)
#: Tree entry modes that are blobs: regular, executable, symlink (its text is the target).
BLOB_MODES = ("100644", "100755", "120000")
ABSENT_MODE = "000000"
GITLINK_MODE = "160000"
#: A line of the prompt that declares the verdict values, e.g.
#: `VERDICT-<TAG>: SOUND | WEAK | BROKEN`.
VERDICT_DECLARATION = re.compile(
    r"VERDICT-\S+:\s*([A-Z][A-Z0-9-]*(?:\s*\|\s*[A-Z][A-Z0-9-]*)*)"
)
#: Complete marker lines of the answer: (line prefix, the full form the line must have).
MARKER_LINES = (
    ("VERDICT-", re.compile(r"VERDICT-[A-Z0-9-]+: ([A-Z][A-Z0-9-]*)")),
    ("CLASS-1-COUNT", re.compile(r"CLASS-1-COUNT: \d+")),
    ("REVIEW-COMPLETE", re.compile(r"REVIEW-COMPLETE: YES")),
)
#: What the git children run with on top of the subcommand (see the module docstring).
GIT_OPTIONS = (
    "--no-pager",
    "--no-replace-objects",
    "-c", "core.fsmonitor=false",
    "-c", "log.showSignature=false",
)
DIFF_OPTIONS = ("--no-ext-diff", "--no-textconv", "--no-color")
EXIT_OK, EXIT_UNVERIFIED, EXIT_REFUSED = 0, 2, 3


class Refused(Exception):
    """A check before sending failed; nothing was sent."""


class ResponseTooLarge(Exception):
    """The body is longer than `MAX_RESPONSE_BYTES`; it was not read further."""


class Run:
    """The output directory of one run. Every file goes through `write`, which redacts."""

    def __init__(self, out: Path, key: str | None) -> None:
        self.out, self.key = out, key
        out.mkdir(parents=True, exist_ok=True)

    def redact(self, text: str) -> str:
        # The stripped form too: an exception may show a key's CR/LF escaped (main refuses such keys first).
        for secret in {self.key, self.key.strip()} if self.key else ():
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def write(self, name: str, text: str) -> None:
        (self.out / name).write_text(self.redact(text), encoding="utf-8")

    def write_json(self, name: str, data: object) -> None:
        self.write(name, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with (self.out / "progress.log").open("a", encoding="utf-8") as handle:
            handle.write(self.redact(f"{stamp} {message}") + "\n")


def git_env() -> dict[str, str]:
    """The environment of every git child: no key, no inherited `GIT_*`, no outside config."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_") and name.upper() != KEY_VARIABLE
    }
    env.update(
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_LAZY_FETCH="1",
        GIT_OPTIONAL_LOCKS="0",
    )
    return env


def git_bytes(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *GIT_OPTIONS, *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=git_env(),
        check=False,
    )
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


def declared_verdicts(prompt: str) -> set[str]:
    """The verdict values the prompt declares; the answer may use only these."""
    values: set[str] = set()
    for line in prompt.splitlines():
        match = VERDICT_DECLARATION.fullmatch(line.strip())
        if match:
            values.update(value.strip() for value in match.group(1).split("|"))
    return values


def changed_entries(base: str, head: str, spec: list[str]) -> list[tuple[str, str, str, str]]:
    """(old mode, new mode, new oid, path) of every touched path, renames as delete + add."""
    out = git("diff-tree", "-r", "-z", "--raw", "--no-renames", *DIFF_OPTIONS, base, head, *spec)
    fields = [field for field in out.split("\0") if field]
    entries = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        old_mode, new_mode, _old_oid, new_oid, _status = meta.lstrip(":").split(" ")
        entries.append((old_mode, new_mode, new_oid, path))
    return entries


def build_payload(base: str, head: str, prompt: str, paths: list[str]) -> tuple[str, dict]:
    """The text sent to the reviewer and what the meta records. Committed blobs only."""
    spec = ["--", *paths] if paths else []
    entries = changed_entries(base, head, spec)
    gitlinks = sorted(p for old, new, _, p in entries if GITLINK_MODE in (old, new))
    if gitlinks:
        raise Refused(f"gitlink (submodule) entries are not sent: {gitlinks}; narrow --paths")
    allowed = (*BLOB_MODES, ABSENT_MODE)
    others = sorted(p for old, new, _, p in entries if old not in allowed or new not in allowed)
    if others:
        raise Refused(f"tree entries that are not blobs are not sent: {others}")
    secrets = sorted(p for _, _, _, p in entries if is_secret_path(p))
    if secrets:
        raise Refused(f"touched paths look like secrets: {secrets}; narrow --paths")
    files, skipped = [], []
    for _, new_mode, new_oid, path in entries:
        if new_mode == ABSENT_MODE:  # deleted at HEAD: the diff shows it
            continue
        blob = git_bytes("cat-file", "blob", new_oid)
        try:
            if b"\0" in blob:
                raise UnicodeDecodeError("utf-8", blob, 0, 1, "binary")
            files.append((path, blob.decode("utf-8")))
        except UnicodeDecodeError:
            skipped.append(path)
    commits = git("rev-list", "--no-commit-header", "--format=%H %s", f"{base}..{head}")
    diff = git("diff-tree", "-r", "-p", "--find-renames", *DIFF_OPTIONS, base, head, *spec)
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


def read_capped(stream: object) -> str:
    data = stream.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
    if len(data) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge(f"response body over MAX_RESPONSE_BYTES={MAX_RESPONSE_BYTES}")
    return data.decode("utf-8", "replace")


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
            return response.status, read_capped(response)
    except urllib.error.HTTPError as error:
        return error.code, read_capped(error)


def marker_problems(final: str, verdicts: set[str]) -> str | None:
    """Why the marker lines do not prove a complete answer, or None."""
    lines = [line.rstrip() for line in final.splitlines()]
    missing, malformed = [], []
    for prefix, form in MARKER_LINES:
        found = [line for line in lines if line.startswith(prefix)]
        if not found:
            missing.append(prefix)
        for line in found:
            match = form.fullmatch(line)
            if not match or (prefix == "VERDICT-" and match.group(1) not in verdicts):
                malformed.append(line)
    if missing:
        return f"markers missing from final.md: {missing}"
    if malformed:
        return f"malformed marker lines (declared verdicts {sorted(verdicts)}): {malformed}"
    return None


def verify(status: int, raw: str, requested: str, verdicts: set[str]) -> tuple[dict, str | None, str]:
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
    accepted = ACCEPTED_RESPONSE_MODELS.get(requested, (requested,))
    if fields["response_model"] not in accepted:
        model = fields["response_model"]
        return fields, f"response model {model!r} is not the requested {requested!r}", final
    if fields["finish_reason"] != "stop":
        return fields, f"finish_reason is {fields['finish_reason']!r}, not 'stop'", final
    if not final.strip():
        return fields, "empty content", final
    problem = marker_problems(final, verdicts)
    if problem:
        return fields, problem, final
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
    key = os.environ.get(KEY_VARIABLE) or None
    if key and not re.fullmatch(r"[\x21-\x7e]+", key):
        # Before anything else, so no later message can carry it; the value is not shown.
        print(
            f"REFUSED: {KEY_VARIABLE} is not header-safe (whitespace or a control character,"
            " e.g. a trailing newline); fix the variable",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    try:
        if live and not key:
            raise Refused(f"{KEY_VARIABLE} is not set")
        base, head = resolve_commit(args.base), resolve_commit(args.head)
        prompt = args.prompt.read_text(encoding="utf-8")
        verdicts = declared_verdicts(prompt)
        if not verdicts:
            raise Refused(
                "the prompt declares no verdict values (a line like"
                " `VERDICT-<TAG>: SOUND | WEAK | BROKEN`, runbook section 5.1)"
            )
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
        "declared_verdicts": sorted(verdicts),
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
        except (OSError, http.client.HTTPException, ResponseTooLarge) as error:  # URLError, timeouts
            status, raw = 0, json.dumps({"transport_error": f"{type(error).__name__}: {error}"})
        except Exception as error:  # noqa: BLE001 - any other failure is UNVERIFIED, named, redacted
            status, raw = 0, json.dumps({"unexpected_error": f"{type(error).__name__}: {error}"})
    else:
        status, raw = 200, saved
    meta["wall_seconds"] = round(time.monotonic() - started, 1)
    run.write("response.json", raw)
    fields, reason, final = verify(status, raw, args.model, verdicts)
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
