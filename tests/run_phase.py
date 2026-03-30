"""Run all queued Phase 1 cycles back to back for arclane.cloud dogfood test.

Triggers on-demand cycles sequentially, waits for each to complete,
tracks per-cycle cost from the sandbox proxy, and logs a full summary.

Usage:
    python tests/run_phase.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "http://localhost:8012"
PROXY = "http://localhost:8099"
SLUG = "arclane"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmb3VuZGVyQGFyY2xhbmUuY2xvdWQiLCJlbWFpbCI6ImZvdW5kZXJAYXJjbGFuZS5jbG91ZCIsImlhdCI6MTc3Mzc1MzY1OSwiZXhwIjoxNzc2MzQ1NjU5fQ.5i6x3rmjpZiyGKhSNFpLZJg-wY5PbXnq3uZMRdS-P1Y"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Max cycles to run in one session (safety cap)
MAX_CYCLES = 30
POLL_INTERVAL_S = 15
CYCLE_TIMEOUT_S = 600


def proxy_stats() -> dict:
    try:
        r = httpx.get(f"{PROXY}/stats", timeout=5)
        return r.json()
    except Exception:
        return {}


def proxy_tokens() -> int:
    return proxy_stats().get("total_tokens_approx", 0)


def proxy_requests() -> int:
    return proxy_stats().get("requests", 0)


def estimate_cost(tokens: int) -> float:
    """Sonnet pricing: $3/MTok input (~40%), $15/MTok output (~60%)."""
    input_cost = (tokens * 0.40 / 1_000_000) * 3.00
    output_cost = (tokens * 0.60 / 1_000_000) * 15.00
    return round(input_cost + output_cost, 4)


def get_roadmap() -> dict:
    try:
        r = httpx.get(f"{BASE}/api/businesses/{SLUG}/roadmap", headers=HEADERS, timeout=10)
        return r.json()
    except Exception:
        return {}


def get_content() -> list:
    try:
        r = httpx.get(f"{BASE}/api/businesses/{SLUG}/content", headers=HEADERS, timeout=10)
        return r.json() if isinstance(r.json(), list) else []
    except Exception:
        return []


def get_cycles() -> list:
    try:
        r = httpx.get(f"{BASE}/api/businesses/{SLUG}/cycles", headers=HEADERS, timeout=10)
        return r.json() if isinstance(r.json(), list) else []
    except Exception:
        return []


def advance_day() -> int | None:
    """Increment roadmap_day by 1 to simulate nightly scheduler progression."""
    try:
        r = httpx.post(
            f"{BASE}/api/businesses/{SLUG}/cycles/advance-day",
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("roadmap_day")
        print(f"  [ADVANCE-DAY FAILED] HTTP {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [ADVANCE-DAY ERROR] {e}")
        return None


def trigger_cycle() -> dict | None:
    try:
        r = httpx.post(
            f"{BASE}/api/businesses/{SLUG}/cycles",
            headers=HEADERS,
            json={},
            timeout=15,
        )
        if r.status_code == 201:
            return r.json()
        print(f"  [TRIGGER FAILED] HTTP {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [TRIGGER ERROR] {e}")
        return None


def wait_for_cycle(cycle_id: int) -> dict | None:
    """Poll until cycle completes or times out. Returns final cycle state."""
    deadline = time.time() + CYCLE_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        cycles = get_cycles()
        for c in cycles:
            if c["id"] == cycle_id:
                if c["status"] in ("completed", "failed"):
                    return c
                break
    return None  # timeout


def print_banner(text: str):
    print(f"\n{'='*65}")
    print(f"  {text}")
    print(f"{'='*65}")


def main():
    print_banner(f"ARCLANE PHASE RUN — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Business: {SLUG}")
    print(f"  Base URL: {BASE}")
    print(f"  Max cycles: {MAX_CYCLES}")

    # Snapshot starting state
    roadmap = get_roadmap()
    start_phase = roadmap.get("current_phase", "?")
    start_day = roadmap.get("roadmap_day", "?")
    start_milestones = sum(
        p.get("milestones_completed", 0)
        for p in roadmap.get("phases", [])
    )
    start_tokens = proxy_tokens()
    start_requests = proxy_requests()
    start_content_count = len(get_content())

    print(f"\n  Starting state:")
    print(f"    Phase: {start_phase} | Day: {start_day}")
    print(f"    Milestones completed: {start_milestones}")
    print(f"    Existing deliverables: {start_content_count}")
    print(f"    Proxy baseline: {start_requests} reqs / {start_tokens:,} tokens")

    session_log = []
    total_failed = 0
    content_before = start_content_count

    for i in range(1, MAX_CYCLES + 1):
        print_banner(f"CYCLE {i}/{MAX_CYCLES}")

        # Check roadmap phase before triggering
        roadmap = get_roadmap()
        current_phase = roadmap.get("current_phase", 1)
        milestones_done = sum(
            p.get("milestones_completed", 0)
            for p in roadmap.get("phases", [])
        )
        milestones_total = sum(
            p.get("milestones_total", 0)
            for p in roadmap.get("phases", [])
        )
        wd_remaining = roadmap.get("working_days_remaining", "?")

        print(f"  Phase {current_phase} | Milestones: {milestones_done}/{milestones_total}")

        # Advance roadmap_day to simulate nightly progression
        new_day = advance_day()
        if new_day is not None:
            print(f"  Roadmap day -> {new_day}")
        else:
            print("  [WARN] Could not advance roadmap_day")

        # Snapshot tokens before this cycle
        tokens_before = proxy_tokens()
        reqs_before = proxy_requests()
        t_start = time.time()

        print(f"  Triggering cycle...", end="", flush=True)
        cycle = trigger_cycle()
        if not cycle:
            print(" FAILED — stopping run")
            break

        cycle_id = cycle["id"]
        print(f" Cycle #{cycle_id} queued")
        print(f"  Waiting for completion", end="", flush=True)

        while True:
            time.sleep(POLL_INTERVAL_S)
            print(".", end="", flush=True)
            cycles = get_cycles()
            final = next((c for c in cycles if c["id"] == cycle_id), None)
            if final and final["status"] in ("completed", "failed"):
                break
            if time.time() - t_start > CYCLE_TIMEOUT_S:
                final = {"status": "timeout", "total_tasks": 0, "failed_tasks": 0}
                break

        elapsed = time.time() - t_start
        print(f" done ({elapsed:.0f}s)")

        # Cost delta
        tokens_after = proxy_tokens()
        reqs_after = proxy_requests()
        delta_tokens = tokens_after - tokens_before
        delta_reqs = reqs_after - reqs_before
        cycle_cost = estimate_cost(delta_tokens)

        status = final.get("status", "?")
        total_tasks = final.get("total_tasks") or 0
        failed_tasks = final.get("failed_tasks") or 0

        if status == "failed" or failed_tasks > 0:
            total_failed += 1

        # New deliverables
        content_after = len(get_content())
        new_deliverables = content_after - content_before
        content_before = content_after

        print(f"\n  Result:        {status.upper()}")
        print(f"  Tasks:         {total_tasks - failed_tasks}/{total_tasks} succeeded")
        print(f"  New content:   {new_deliverables} deliverable(s)")
        print(f"  LLM calls:     {delta_reqs} | ~{delta_tokens:,} tokens")
        print(f"  Cycle cost:    ${cycle_cost:.4f}")
        print(f"  Elapsed:       {elapsed:.0f}s")

        entry = {
            "cycle_num": i,
            "cycle_id": cycle_id,
            "status": status,
            "tasks_ok": total_tasks - failed_tasks,
            "tasks_total": total_tasks,
            "new_deliverables": new_deliverables,
            "llm_calls": delta_reqs,
            "tokens": delta_tokens,
            "cost": cycle_cost,
            "elapsed_s": round(elapsed, 1),
        }
        session_log.append(entry)

        # Check for phase completion / graduation
        roadmap = get_roadmap()
        new_phase = roadmap.get("current_phase", 1)
        new_milestones = sum(
            p.get("milestones_completed", 0)
            for p in roadmap.get("phases", [])
        )
        if new_milestones > milestones_done:
            print(f"\n  [+] Milestones: {milestones_done} -> {new_milestones}")

        if status == "failed" and total_tasks == 0:
            print("\n  [STOP] No tasks produced — queue may be empty or exhausted")
            break

        # Short pause between cycles to avoid hammering the API
        if i < MAX_CYCLES:
            print(f"\n  Pausing 5s before next cycle...")
            time.sleep(5)

    # ── SESSION SUMMARY ──────────────────────────────────────────────────────
    print_banner("SESSION COMPLETE")

    total_cycles = len(session_log)
    total_ok = sum(1 for e in session_log if e["status"] == "completed")
    total_deliverables = sum(e["new_deliverables"] for e in session_log)
    total_tokens = proxy_tokens() - start_tokens
    total_cost = estimate_cost(total_tokens)
    total_llm = proxy_requests() - start_requests

    # Final roadmap state
    roadmap = get_roadmap()
    final_phase = roadmap.get("current_phase", "?")
    final_milestones = sum(
        p.get("milestones_completed", 0)
        for p in roadmap.get("phases", [])
    )

    print(f"\n  Cycles run:        {total_cycles} ({total_ok} succeeded, {total_failed} failed)")
    print(f"  Deliverables:      {start_content_count} -> {start_content_count + total_deliverables} (+{total_deliverables})")
    print(f"  Phase progress:    Phase {start_phase} D{start_day} -> Phase {final_phase}")
    print(f"  Milestones:        {start_milestones} -> {final_milestones}")
    print(f"  Total LLM calls:   {total_llm}")
    print(f"  Total tokens:      ~{total_tokens:,}")
    print(f"  Total cost (est):  ${total_cost:.4f}")
    print(f"  Avg cost/cycle:    ${total_cost/max(total_cycles,1):.4f}")

    print(f"\n  Per-cycle log:")
    print(f"  {'#':>3}  {'Cycle':>6}  {'Status':10}  {'Tasks':>5}  {'New':>4}  {'Calls':>5}  {'Tokens':>7}  {'Cost':>8}  {'Time':>6}")
    print(f"  {'-'*3}  {'-'*6}  {'-'*10}  {'-'*5}  {'-'*4}  {'-'*5}  {'-'*7}  {'-'*8}  {'-'*6}")
    for e in session_log:
        print(
            f"  {e['cycle_num']:>3}  #{e['cycle_id']:<5}  {e['status']:10}  "
            f"{e['tasks_ok']}/{e['tasks_total']:>2}  {e['new_deliverables']:>4}  "
            f"{e['llm_calls']:>5}  {e['tokens']:>7,}  ${e['cost']:>7.4f}  {e['elapsed_s']:>5.0f}s"
        )

    # Save summary JSON
    out = Path(__file__).parent / "phase_run_results.json"
    summary = {
        "run_date": datetime.now(timezone.utc).isoformat(),
        "slug": SLUG,
        "cycles": session_log,
        "totals": {
            "cycles_run": total_cycles,
            "cycles_ok": total_ok,
            "deliverables_added": total_deliverables,
            "llm_calls": total_llm,
            "tokens_approx": total_tokens,
            "cost_usd": total_cost,
        },
    }
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n  Results saved: {out}")


if __name__ == "__main__":
    main()
