#!/usr/bin/env python3
"""Retired bounty-scan-to-social generator.

The GitHub opportunity radar can change candidate counts every hour even when
nothing interesting happened economically. Those changes previously generated
near-identical "Bounty Safari" videos. That is NOT a documentary-worthy event.

Public episodes now require an individually authored, evidence-backed,
original handoff under ops/ai-handoffs/*.json, which is processed by the
separate ai-handoff-dispatch.yml workflow. GitHub's economic research and
payment/fulfillment automations remain independent and unaffected.
"""
from pathlib import Path

READY = Path("/tmp/independent-episode-dispatch")


def run():
    READY.write_text("no\n", encoding="utf-8")
    print("SKIP: repetitive opportunity-scan videos retired. "
          "Real, distinct events require an approved editorial handoff.")
    return False


if __name__ == "__main__":
    run()
