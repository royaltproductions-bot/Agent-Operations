"""Resident supervisor: keep watching after housekeeping, work and recoverable failure."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def command(args, **kwargs):
    # Git operations must never hold the resident worker indefinitely.
    if args and args[0]=="git" and "timeout" not in kwargs:
        kwargs["timeout"]=60
    return subprocess.run(args, check=True, **kwargs)


def checkpoint():
    """Persist Gambit state and its spending ledger on an isolated branch.

    Other revenue variants continuously commit receipts to main. A dedicated
    branch prevents their writes from racing Gambit's 30-second checkpoints.
    Do not allow a new inference call when state persistence fails.
    """
    files = ['ops/gambit/state.json', 'ops/gambit/openai_budget.json']
    if Path('ops/gambit/funnel.json').exists():
        files.append('ops/gambit/funnel.json')
    for attempt in range(4):
        command(['git', 'fetch', '--no-tags', 'origin', 'gambit-state'])
        originals = [
            subprocess.run(['git', 'show', 'origin/gambit-state:' + p],
                           check=not p.endswith("/funnel.json"), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=60).stdout
            for p in files
        ]
        if all(Path(p).read_bytes() == original
               for p, original in zip(files, originals)):
            return
        with tempfile.TemporaryDirectory(prefix='gambit-state-') as folder:
            directory = Path(folder) / 'checkout'
            command(['git', 'worktree', 'add', '--detach',
                     str(directory), 'origin/gambit-state'])
            try:
                for p in files:
                    dest = directory / p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, dest)
                prefix = ['git', '-C', str(directory)]
                command(prefix + ['config', 'user.name', 'gambit-worker'])
                command(prefix + ['config', 'user.email',
                                  'ari-operations@agentmail.to'])
                command(prefix + ['add', '--', *files])
                command(prefix + ['commit', '-m',
                                  'Checkpoint Gambit market and API budget'])
                pushed = subprocess.run(
                    prefix + ['push', 'origin', 'HEAD:refs/heads/gambit-state'],
                    timeout=60)
                if pushed.returncode == 0:
                    return
            finally:
                command(['git', 'worktree', 'remove', '--force', str(directory)])
        time.sleep(min(2 ** attempt, 4))
    raise RuntimeError('checkpoint_isolated_state_push_failed')


def probe(module):
    # A fresh output file for EACH call: old needs_run=true must not latch on.
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / 'output'
        command([sys.executable, '-m', module, '--probe'],
                env={**os.environ, 'GITHUB_OUTPUT': str(output)}, timeout=90)
        return dict(line.split('=', 1) for line in output.read_text().splitlines()
                    if '=' in line)


GAMBIT_RELEVANT_MAIN_PATHS = (
    ".github/workflows/gambit-bounty.yml",
    "tools/gambit_watch.py",
    "tools/gambit_worker.py",
    "tools/gambit_commercial.py",
    "tools/gambit_openai.py",
    "tools/gambit_artifacts.py",
    "tools/gambit_secondary_marketplace.py",
    "tools/gambit_credentials.py",
    "ops/gambit/bountyboard-credential.json",
)


def relevant_main_change():
    """Yield the resident slot only for changes that would launch a new Gambit run."""
    try:
        fetched=subprocess.run(
            ["git","fetch","--quiet","--no-tags","origin",
             "main:refs/remotes/origin/main"],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        if fetched.returncode != 0:
            return False
        changed=subprocess.run(
            ["git","diff","--quiet","HEAD","origin/main","--",*GAMBIT_RELEVANT_MAIN_PATHS],
            timeout=30)
        return changed.returncode == 1
    except subprocess.TimeoutExpired:
        # Never stop commercial work merely because the comparison fetch stalled.
        return False


def prepare_video_runtime():
    """Install FFmpeg only for an actual bounded MP4 job, never on empty polls."""
    from tools import gambit_artifacts
    if gambit_artifacts.video_capable():
        return
    command(["sudo", "apt-get", "update", "-qq"])
    command(["sudo", "apt-get", "install", "-y", "--no-install-recommends", "ffmpeg"])
    gambit_artifacts.video_capable.cache_clear()
    if not gambit_artifacts.video_capable():
        raise RuntimeError("mp4_runtime_unavailable")


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
    if status.get('needs_video_runtime') == 'true':
        prepare_video_runtime()
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
        # A resident cycle must fit inside the five-minute poll interval.
        # TimeoutExpired is handled by watch() as a recoverable failed cycle,
        # and the finally checkpoint below preserves retry state.
        command([sys.executable, '-m', 'tools.gambit_worker'], timeout=240)
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
        if relevant_main_change():
            # Persist the last resident-health state, then release concurrency so
            # GitHub can start the already-triggered latest-code worker.
            record_health(successes, failures)
            print(json.dumps({"watch_handoff":"relevant_main_change",
                              "successful_cycles":successes,
                              "failed_cycles":failures}), flush=True)
            return successes, failures
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
    successes, failures = watch(
        seconds=int(os.getenv('GAMBIT_WATCH_SECONDS', '13200')),
        interval=int(os.getenv('GAMBIT_WATCH_INTERVAL_SECONDS', '300')))
    record_health(successes, failures)
