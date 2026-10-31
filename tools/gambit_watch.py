"""Resident supervisor: keep watching after housekeeping, work and recoverable failure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def checkpoint():
    """Publish worker state and the conservative API budget ledger together."""
    paths = ['ops/gambit/state.json', 'ops/gambit/openai_budget.json']
    if subprocess.run(['git', 'diff', '--quiet', '--', *paths]).returncode == 0:
        return
    command(['git', 'config', 'user.name', 'gambit-worker'])
    command(['git', 'config', 'user.email', 'ari-operations@agentmail.to'])
    command(['git', 'add', *paths])
    command(['git', 'commit', '-m', 'Record Gambit market and delivery checkpoint'])
    for attempt in range(6):
        if subprocess.run(['git', 'push', 'origin', 'HEAD:main']).returncode == 0:
            return
        command(['git', 'fetch', 'origin', 'main'])
        command(['git', 'rebase', 'origin/main'])
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError('checkpoint_push_failed')


def probe(module):
    # A fresh output file for EACH call: old needs_run=true must not latch on.
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / 'output'
        command([sys.executable, '-m', module, '--probe'],
                env={**os.environ, 'GITHUB_OUTPUT': str(output)})
        return dict(line.split('=', 1) for line in output.read_text().splitlines()
                    if '=' in line)


def prepare_models(production=False):
    from tools import gambit_openai
    if gambit_openai.provider() == "openai":
        # Paid provider is already online; do not download or boot Qwen/Ollama.
        return
    command(['bash', 'tools/gambit_models.sh', 'production' if production else 'triage'])


def cycle():
    status = probe('tools.gambit_early_scan')
    checkpoint()  # retain newly discovered IDs even if the full probe fails
    if status.get('needs_run') != 'true':
        return
    status = probe('tools.gambit_worker')
    checkpoint()  # encrypted terms before inference
    if status.get('needs_run') != 'true':
        return
    if status.get('needs_model') == 'true':
        # Keep marketplace discovery free and running, but do not allow Qwen
        # to make commercial decisions once OpenAI-only mode is selected.
        if (os.getenv("GAMBIT_INFERENCE_PROVIDER", "ollama").lower() == "openai"
                and not os.getenv("OPENAI_API_KEY", "").strip()):
            print(json.dumps({"market_scan": "complete",
                              "inference": "awaiting_openai_key"}), flush=True)
            return
        prepare_models(status.get('needs_production_model') == 'true')
    try:
        command([sys.executable, '-m', 'tools.gambit_worker'])
    finally:
        checkpoint()  # failed jobs retain retries and upload receipts


def record_health(successes, failures):
    path=Path('ops/gambit/state.json')
    state=json.loads(path.read_text())
    from datetime import datetime, timezone
    state['watch_health']={'at':datetime.now(timezone.utc).isoformat(),
                           'successful_cycles':successes, 'failed_cycles':failures,
                           'run_id':os.getenv('GITHUB_RUN_ID','local')}
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,indent=2,sort_keys=True)+'\n')
    temporary.replace(path)
    checkpoint()


def watch(seconds=240, interval=30, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + seconds
    successes = failures = 0
    last_health = clock()
    while clock() < deadline:
        started = clock()
        try:
            cycle()
            successes += 1
        except Exception as exc:
            failures += 1
            # Exceptions can contain buyer payloads; publish the class only.
            print(json.dumps({'watch_cycle': 'failed', 'error': type(exc).__name__}), flush=True)
            # If this checkpoint fails, stop rather than execute unpersisted writes.
            checkpoint()
        print(json.dumps({'watch_cycle': 'finished', 'successes': successes,
                          'failures': failures, 'elapsed_seconds': round(clock()-started, 2)}), flush=True)
        if clock()-last_health >= 300:
            record_health(successes, failures)
            last_health=clock()
        delay = min(max(0, interval - (clock()-started)), max(0, deadline-clock()))
        if delay:
            sleep(delay)
    if not successes:
        raise RuntimeError('no_successful_market_cycles')
    return successes, failures


if __name__ == '__main__':
    successes, failures = watch(seconds=int(os.getenv('GAMBIT_WATCH_SECONDS', '240')))
    record_health(successes, failures)
