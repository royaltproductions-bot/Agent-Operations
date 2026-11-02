"""Fail-closed recurring public agent card drift review.

A code change affecting declared capabilities requires human review before
changing public claims. Never edits the live TryBounty profile or accesses
customer conversations, secrets, model APIs or marketplace messages.
"""
import json
from pathlib import Path
import subprocess
from tools import gambit_agent_card

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "ops/gambit/public-agent-card-review.json"


def check():
    gambit_agent_card.card()
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    if expected.get("schema_version") != 1 or not isinstance(
            expected.get("source_git_blobs"), dict):
        raise ValueError("invalid_agent_card_review_baseline")
    for path, before in expected["source_git_blobs"].items():
        if (not path.startswith("tools/gambit_") or
                not path.endswith(".py") or ".." in path or
                not isinstance(before, str) or len(before) != 40):
            raise ValueError("unsafe_agent_card_review_baseline")
        result = subprocess.run(
            ["git", "hash-object", path], cwd=ROOT,
            check=True, capture_output=True, text=True)
        if result.stdout.strip() != before:
            raise ValueError("agent_card_review_required: capability_sources_changed")
    if (ROOT / "ops/gambit/trybounty-agent-card.md").read_text(
            encoding="utf-8") != gambit_agent_card.markdown():
        raise ValueError("agent_card_review_required: public_card_out_of_sync")
    print("public_agent_card_current_against_reviewed_code")


if __name__ == "__main__":
    check()
