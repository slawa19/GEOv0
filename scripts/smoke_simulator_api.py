"""Minimal backend smoke-check for simulator cookie/admin flows.

Runs against local backend at http://127.0.0.1:18000.

Notes:
- Uses stdlib urllib + CookieJar to validate geo_sim_sid cookie flow.
- Exercises: health, session/ensure, scenarios, runs create/active/stop, admin/runs.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar


BASE = "http://127.0.0.1:18000"
ADMIN_TOKEN = "dev-admin-token-change-me"
ORIGIN = "http://localhost:5176"


def _req(opener, method: str, path: str, *, body=None, headers=None):
    url = BASE + path
    data = None
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with opener.open(request, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "") or ""
            text = raw.decode("utf-8") if raw else ""
            if "application/json" in ctype and text:
                return resp.status, json.loads(text)
            return resp.status, text
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8") if raw else ""
        try:
            payload = json.loads(text) if text else None
        except Exception:
            payload = text
        return e.code, payload


def main() -> int:
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def pr(label: str, result):
        print(f"\n== {label} ==")
        print(result)

    pr("health", _req(opener, "GET", "/api/v1/health"))

    pr("ensure session", _req(opener, "POST", "/api/v1/simulator/session/ensure"))
    print("cookies:", [c.name for c in cj])

    status, scenarios = _req(opener, "GET", "/api/v1/simulator/scenarios")
    pr("list scenarios", (status, scenarios))
    if status != 200:
        return 2
    items = (scenarios or {}).get("items")
    if not items:
        print("No scenarios returned")
        return 3

    scenario_id = items[0]["scenario_id"]
    print("using scenario_id:", scenario_id)

    status, run_resp = _req(
        opener,
        "POST",
        "/api/v1/simulator/runs",
        body={"scenario_id": scenario_id, "mode": "fixtures", "intensity_percent": 10},
        headers={"Origin": ORIGIN},
    )
    pr("start run", (status, run_resp))
    if status != 200:
        return 4

    run_id = run_resp["run_id"]
    pr("get active run", _req(opener, "GET", "/api/v1/simulator/runs/active"))

    pr(
        "admin list runs",
        _req(opener, "GET", "/api/v1/simulator/admin/runs", headers={"X-Admin-Token": ADMIN_TOKEN}),
    )

    pr(
        "stop run",
        _req(
            opener,
            "POST",
            f"/api/v1/simulator/runs/{run_id}/stop?source=smoke&reason=smoke",
            headers={"Origin": ORIGIN},
        ),
    )

    pr("active run after stop", _req(opener, "GET", "/api/v1/simulator/runs/active"))

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
