"""CLI refresh: fetch all sources, score, print a report.  Run:  python refresh.py"""
import db
import fetchers
import notify
import scoring


def main():
    db.init()
    jobs, stats = fetchers.run_all(progress=lambda m: print("  " + m))
    new_count, new_ids = db.upsert_jobs(jobs)
    scored = scoring.rescore_all(db)
    note = notify.after_refresh(new_ids)
    stats["_summary"] = {"fetched": len(jobs), "new": new_count, "new_ids": new_ids, "scored": scored,
                         "at": db.now_iso(), "notify": note}
    db.set_meta("last_refresh", stats)

    print("\n=== Refresh report ===")
    for source, s in stats.items():
        if source in ("_summary", "board_failures", "companies"):
            continue
        print(f"  {source:16} {s.get('kept', 0):5} kept" + (f"   ERROR: {s['error']}" if "error" in s else ""))
    fails = stats.get("board_failures", [])
    if fails:
        print(f"\n  {len(fails)} company boards failed:")
        for f in fails:
            print(f"    - {f}")
    print(f"\n  Total fetched: {len(jobs)}   New: {new_count}   In DB (scored): {scored}   Notify: {note}")


if __name__ == "__main__":
    main()
