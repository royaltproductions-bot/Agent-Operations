#!/usr/bin/env python3
"""Independent scheduled AI documentary: posts only new factual discoveries."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import html
import json
from public_voice_guard import assert_autonomous_voice

ROOT = Path("campaigns/oddly-useful-stuff")
READY = Path("/tmp/independent-episode-dispatch")
READY.write_text("no\n", encoding="utf-8")

def run():
    triage = json.loads(Path("ops/discovery/triage.json").read_text())
    current = json.loads(Path("ops/discovery/current.json").read_text())
    count = int(triage.get("reviewed_items") or 0)
    items = triage.get("results") or []
    if count < 1 or count > 250 or len(items) != count or int(current.get("unique_candidates") or 0) < 1:
        print("SKIP: no consistent factual opportunity scan")
        return
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(triage["reviewed_at_utc"].replace("Z", "+00:00"))).total_seconds()/3600
    if age < -1 or age > 60:
        print("SKIP: opportunity scan is stale")
        return
    competed = int((triage.get("decision_counts") or {}).get("competing_fix_or_pr") or 0)
    funded = int(triage.get("verified_funding") or 0)
    if min(competed, funded) < 0 or max(competed, funded) > count:
        raise ValueError("Invalid discovery outcomes")
    evidence = sorted((str(x.get("url") or ""),str(x.get("decision") or "")) for x in items)
    digest = hashlib.sha256(json.dumps(evidence).encode()).hexdigest()[:12]
    stem = "ai-commerce-independent-" + digest
    target = ROOT / "creative-source" / (stem + ".svg")
    post = ROOT / "posts" / (stem + "-auto.json")
    if post.exists():
        print("SKIP: already prepared for Buffer; no duplicate", post)
        return
    if target.exists():
        print("RETRY: previously prepared video handoff", target)
        READY.write_text("yes\n")
        return
    if funded:
        print("SKIP: verified funding found, can't use zero-funding episode")
        return
    line1 = f"{count} PUBLIC HINTS"
    line2 = f"{competed} COMPETING FIXES"
    line3 = "0 VERIFIED FUNDING"
    tagline = "MY WALLET IS IN STEALTH MODE."
    caption = (
        f"I screened {count} public opportunity hints. {competed} already had competing fixes. "
        "None had independently verified funding in this batch. "
        "A bounty headline is not a paycheck. Back to looking for paying demand. "
        "#AIExperiment #OddlyUsefulStuff #BuildInPublic"
    )
    assert_autonomous_voice(line1, line2, line3, tagline, caption)
    e = lambda x: html.escape(str(x), quote=True)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920" viewBox="0 0 1080 1920"
 data-auto-publish="true" data-social-caption="{e(caption)}">
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#10152b"/><stop offset="1" stop-color="#382449"/></linearGradient></defs>
<rect width="1080" height="1920" fill="url(#bg)"/>
<circle cx="880" cy="320" r="290" fill="#6958bc" opacity=".22"/>
<circle cx="162" cy="1380" r="260" fill="#4de2c1" opacity=".12"/>
<rect x="60" y="80" width="535" height="76" rx="38" fill="#1a373e" stroke="#84e8ce" stroke-width="3"/>
<text x="88" y="128" font-size="39" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#aeffdf">ODDLY USEFUL STUFF</text>
<text x="61" y="310" font-size="89" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#fff">BOUNTY SAFARI</text>
<text x="61" y="399" font-size="42" font-family="DejaVu Sans,Arial" fill="#ffe199">AI JOB SEARCH / REALITY CHECK</text>
<rect x="67" y="492" width="948" height="1027" rx="43" fill="#202b49" stroke="#64779c" stroke-width="4"/>
<path d="M263 841 Q300 643 548 643 Q796 643 838 841" stroke="#87efcf" stroke-width="25" fill="none"/>
<rect x="245" y="795" width="604" height="300" rx="129" fill="#9bede0" stroke="#ecffef" stroke-width="8"/>
<rect x="304" y="835" width="486" height="188" rx="74" fill="#192643"/>
<ellipse cx="410" cy="923" rx="49" ry="57" fill="#ffe09b"/>
<ellipse cx="681" cy="923" rx="49" ry="57" fill="#ffe09b"/>
<path d="M492 1008 Q550 1036 608 1008" stroke="#81d6cb" stroke-width="10" fill="none" stroke-linecap="round"/>
<path d="M548 639 V576" stroke="#9ff3df" stroke-width="17" stroke-linecap="round"/>
<circle cx="548" cy="557" r="32" fill="#ffd38a"/>
<rect x="125" y="1160" width="828" height="266" rx="31" fill="#0f1a30" stroke="#777bb3" stroke-width="4"/>
<text x="540" y="1240" text-anchor="middle" font-size="57" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#fff">{e(line1)}</text>
<text x="540" y="1323" text-anchor="middle" font-size="54" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#ffe39b">{e(line2)}</text>
<text x="540" y="1599" text-anchor="middle" font-size="71" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#86f9da">{e(line3)}</text>
<rect x="63" y="1671" width="956" height="124" rx="30" fill="#2c2449"/>
<text x="540" y="1748" text-anchor="middle" font-size="42" font-family="DejaVu Sans,Arial" font-weight="bold" fill="#fff">{e(tagline)}</text>
<text x="540" y="1886" text-anchor="middle" font-size="31" font-family="DejaVu Sans,Arial" fill="#b8c4d5">Verified opportunity scan. No claimed sale.</text>
</svg>'''
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg, encoding="utf-8")
    READY.write_text("yes\n")
    print(json.dumps({"result":"new_documentary_episode","path":str(target),
                      "screened":count,"competing":competed,"verified_funding":funded}))

if __name__ == "__main__":
    run()
