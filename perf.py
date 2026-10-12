"""Response-time check for the endpoints the UI hits on every click/poll.

    python perf.py                 # against http://localhost:8765
    python perf.py 8766            # another port

Prints one line per endpoint and exits 1 when any call is slower than SLOW_MS. Run it
whenever the app "feels slow" - both 2026-10-03 regressions (the 2 s localhost/IPv6 floor,
the 3 s YAML re-parse in /api/resume/status) show up here in seconds.
"""
import sys
import time

import requests

SLOW_MS = 500
PATHS = [
    "/api/nothing-here",                   # 404: measures the per-request floor (should be ~5 ms)
    "/api/status", "/api/facets", "/api/resume/status", "/api/companies",
    "/api/jobs?tab=inbox", "/api/jobs?tab=applied", "/api/jobs?tab=viewed", "/api/jobs?new=1",
    "/api/jobs?tab=inbox&role=SRE&loc=austin,texas,remote_us&source=direct&days=30",
]


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "8765"
    bad = 0
    for host in ("localhost", "127.0.0.1"):     # both must be fast: the browser uses localhost
        base = f"http://{host}:{port}"
        print(f"\n{base}")
        for p in PATHS:
            best, size, code = None, 0, "ERR"
            for _ in range(2):
                try:
                    t = time.perf_counter()
                    r = requests.get(base + p, timeout=30)
                    ms = (time.perf_counter() - t) * 1000
                    best = ms if best is None else min(best, ms)
                    size, code = len(r.content), r.status_code
                except requests.RequestException as e:
                    code = type(e).__name__
            flag = "" if best is not None and best < SLOW_MS else "   <-- SLOW"
            bad += bool(flag)
            print(f"  {best or 0:7.0f} ms {size/1024:7.0f} KB  {code}  {p}{flag}")
        if host == "localhost" and best and best > 1500:
            print("  localhost is slow but 127.0.0.1 may not be: the server must listen on ::1 too (server.serve)")
    print("\nOK" if not bad else f"\n{bad} slow call(s)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
