"""Writes 'Also remember' (global) answers into resume/skills-inventory.md.

JobScout - not the skill - owns inventory writes, so several tailoring workers can run at
once without two claude runs editing the same file. Writes are serialized with a lock and
preceded by a timestamped backup (resume/_backups/, last 20 kept).

Entry format is the one documented at the top of the inventory. An entry whose heading
matches (case-insensitive) is replaced in place; new ones are appended under a
"## Confirmed via JobScout" category at the end (the user can move them later).
"""
import os
import re
import shutil
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INVENTORY = os.path.join(BASE_DIR, "resume", "skills-inventory.md")
BACKUPS = os.path.join(BASE_DIR, "resume", "_backups")
CATEGORY = "## Confirmed via JobScout"
_lock = threading.Lock()

STATUS = {"have": "have", "familiar": "familiar", "dont-have": "dont-have"}


def backup():
    if not os.path.exists(INVENTORY):
        return
    os.makedirs(BACKUPS, exist_ok=True)
    shutil.copy2(INVENTORY, os.path.join(
        BACKUPS, f"skills-inventory_{datetime.now():%Y-%m-%d_%H%M%S}.md"))
    for old in sorted(os.listdir(BACKUPS))[:-20]:
        os.remove(os.path.join(BACKUPS, old))


def _entry(skill, answer, context):
    ctx = (context or "").strip() or "confirmed via JobScout"
    if answer == "dont-have":
        return [f"### {skill}", "- status: dont-have", f"- depth: none ({ctx})",
                f"- overclaim-guard: never claim {skill}"]
    return [f"### {skill}", f"- status: {STATUS[answer]}", f"- depth: {ctx}",
            "- years: unconfirmed", f"- where: {ctx}", f"- context: {ctx}",
            "- overclaim-guard: " + ("familiarity/concepts-level mention only"
                                      if answer == "familiar" else "none stated")]


def apply_answers(answers):
    """answers: iterable of dicts with skill, answer (have|familiar|dont-have), context.
    Returns the number of entries written. No-op when the inventory file is missing."""
    answers = [a for a in answers if a.get("skill") and a.get("answer") in STATUS]
    if not answers or not os.path.exists(INVENTORY):
        return 0
    with _lock:
        backup()
        lines = open(INVENTORY, encoding="utf-8").read().split("\n")
        for a in answers:
            new = _entry(a["skill"].strip(), a["answer"], a.get("context"))
            head = new[0].lower()
            idx = next((i for i, ln in enumerate(lines) if ln.strip().lower() == head), None)
            if idx is not None:                       # replace the existing entry body
                j = idx + 1
                while j < len(lines) and not lines[j].startswith("#"):
                    j += 1
                while j > idx + 1 and not lines[j - 1].strip():
                    j -= 1
                lines[idx:j] = new
            else:                                     # append under our category
                if CATEGORY not in lines:
                    while lines and not lines[-1].strip():
                        lines.pop()
                    lines += ["", CATEGORY, ""]
                while lines and not lines[-1].strip():
                    lines.pop()
                lines += ["", *new]
        text = "\n".join(lines).rstrip("\n") + "\n"
        text = re.sub(r"[^\x00-\x7f]", "-", text)      # inventory must stay ASCII
        with open(INVENTORY, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return len(answers)
