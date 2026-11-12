"""Command line. `run --dry-run` never reads an API key and never calls a model."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from canary_leak_bench.agent import LiveAgent
from canary_leak_bench.config import BenchConfig, load_config, parse_cap
from canary_leak_bench.errors import BenchError, ConfigError
from canary_leak_bench.guard import GuardProcess, require_binary
from canary_leak_bench.ledger import Ledger, money
from canary_leak_bench.prompts import CONFIGS
from canary_leak_bench.provider import AnthropicProvider, key_from_env
from canary_leak_bench.recorder import Recorder
from canary_leak_bench.report import ReportMeta, render, spent
from canary_leak_bench.runner import run_scripted, run_trials
from canary_leak_bench.scenarios import Scenario, load_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="canary-leak-bench")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the scenarios under the three configs")
    run.add_argument("--dry-run", action="store_true", help="scripted agent, no model API")
    run.add_argument("--cap-usd", help="live cost cap in USD; required unless --dry-run")
    run.add_argument("--config", type=Path, help="live model config JSON")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument(
        "--scenarios",
        type=Path,
        default=Path("scenarios/catalog.json"),
    )
    run.add_argument("--agent-guard-bin", type=Path)
    run.add_argument("--only", action="append", choices=CONFIGS)
    args = parser.parse_args(argv)
    try:
        text = _execute(args)
    except BenchError as exc:
        print(f"canary-leak-bench: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("canary-leak-bench: failed", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


def _execute(args: argparse.Namespace) -> str:
    configs = tuple(args.only) if args.only else CONFIGS
    scenarios = load_catalog(args.scenarios)
    if args.dry_run:
        if args.cap_usd is not None or args.config is not None:
            raise ConfigError("dry-run takes neither --cap-usd nor --config")
        trials = run_scripted(scenarios, configs)
        meta = ReportMeta(
            simulated=True,
            model_id="SIMULATED scripted agent",
            cap_usd=None,
            spent_usd="0",
            gate="simulated mirror on the agent-guard config; no proxy on the others",
        )
        return render(trials, meta)
    if args.cap_usd is None or args.config is None:
        raise ConfigError("a live run requires --cap-usd and --config")
    spec = load_config(args.config)
    cap = parse_cap(args.cap_usd)
    return _live(scenarios, configs, spec, cap, args.agent_guard_bin)


def _live(
    scenarios: Sequence[Scenario],
    configs: Sequence[str],
    spec: BenchConfig,
    cap: Decimal,
    binary: Path | None,
) -> str:
    guard_bin = require_binary(binary) if "agent-guard" in configs else None
    key = key_from_env(spec.api_key_env)
    ledger = Ledger(cap)
    guard: GuardProcess | None = None
    recorder: Recorder | None = None
    tmp = tempfile.TemporaryDirectory(prefix="canary-leak-")
    try:
        proxy_url = ""
        if guard_bin is not None:
            recorder = Recorder(spec.base_url, spec.timeout_s)
            recorder.start()
            guard = GuardProcess(guard_bin, recorder.base_url(), Path(tmp.name))
            proxy_url = guard.start()

        def make(config: str) -> LiveAgent:
            base = proxy_url if config == "agent-guard" else spec.base_url
            provider = AnthropicProvider(key, base)
            sense = recorder if config == "agent-guard" else None
            return LiveAgent(config, provider, ledger, spec, sense)

        trials = run_trials(scenarios, configs, make, simulated=False)
    finally:
        if guard is not None:
            guard.stop()
        if recorder is not None:
            recorder.stop()
        tmp.cleanup()
    meta = ReportMeta(
        simulated=False,
        model_id=spec.model_id,
        cap_usd=money(cap),
        spent_usd=spent(trials),
        gate="agent-guard binary in front of the agent-guard config; direct API otherwise",
    )
    return render(trials, meta)
