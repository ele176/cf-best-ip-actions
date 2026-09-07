#!/usr/bin/env python3
"""Public, credential-free health check for scheduled repository maintenance."""

from __future__ import annotations

import importlib.util
import pathlib
import sys


MODULE_PATH = pathlib.Path(__file__).with_name("optimizer.py")
SPEC = importlib.util.spec_from_file_location("optimizer_maintenance", MODULE_PATH)
optimizer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = optimizer
SPEC.loader.exec_module(optimizer)


def main() -> int:
    networks = optimizer.load_cloudflare_networks()
    candidates, providers = optimizer.collect_candidates("all", [])
    samples = optimizer.add_official_samples(candidates, networks, 8)
    valid = [ip for ip in candidates if optimizer.is_cloudflare_ip(ip, networks)]
    proxy = [ip for ip in candidates if not optimizer.is_cloudflare_ip(ip, networks)]

    problems: list[str] = []
    if len(networks) < 10:
        problems.append(f"Cloudflare IPv4 network list unexpectedly small: {len(networks)}")
    if len(providers) < 4:
        problems.append(f"fewer than four candidate providers available: {sorted(providers)}")
    if len(candidates) < 100:
        problems.append(f"candidate pool unexpectedly small: {len(candidates)}")
    if len(valid) < 10:
        problems.append(f"fewer than ten candidates belong to official Cloudflare ranges: {len(valid)}")

    print(f"Cloudflare ranges: {len(networks)}")
    print(f"Providers: {', '.join(sorted(providers))}")
    if not proxy:
        problems.append("proxy/BYOIP candidate pool was empty")

    print(
        f"Candidates: {len(candidates)}; official-range candidates: {len(valid)}; "
        f"proxy/BYOIP candidates: {len(proxy)}; deterministic samples: {samples}"
    )

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print("Maintenance health check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
