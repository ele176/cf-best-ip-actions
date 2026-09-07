#!/usr/bin/env python3
"""Aggregate public Cloudflare candidates, validate them, and update one DNS A record."""

from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime as dt
import ipaddress
import json
import os
import re
import socket
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable


CF_API = "https://api.cloudflare.com/client/v4"
CF_IPV4_URL = "https://www.cloudflare.com/ips-v4"
BESTCF_BASE = "https://raw.githubusercontent.com/DustinWin/BestCF/bestcf"
BESTCF_DOMAIN_URL = f"{BESTCF_BASE}/bestcf-domain.txt"
SOURCE_FILES = {
    "cmcc": "cmcc-ip.txt",
    "cucc": "cucc-ip.txt",
    "ctcc": "ctcc-ip.txt",
    "global": "bestcf-ip.txt",
}
REDUNDANT_SOURCES = (
    (
        "ipdb-official",
        "https://ipdb.api.030101.xyz/?type=bestcf",
        "global",
    ),
    (
        "ipdb-proxy",
        "https://ipdb.api.030101.xyz/?type=bestproxy",
        "global",
    ),
    (
        "lancelot-global",
        "https://raw.githubusercontent.com/LancelotRar/best-cf-ips/main/best-cf-ipv4.txt",
        "global",
    ),
)
IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
DOMAIN_RE = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)
USER_AGENT = "cf-best-ip-actions/1.0"
MAX_RESPONSE_BYTES = 1_048_576


@dataclasses.dataclass
class Candidate:
    ip: str
    providers: set[str] = dataclasses.field(default_factory=set)
    carriers: set[str] = dataclasses.field(default_factory=set)
    positions: list[int] = dataclasses.field(default_factory=list)
    latency_ms: float = float("inf")
    status: int | None = None
    successes: int = 0
    recommendations: int = 0

    @property
    def average_position(self) -> float:
        return statistics.mean(self.positions) if self.positions else 999.0

    @property
    def votes(self) -> int:
        return len(self.providers - {"official-sample"})


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict | None = None,
    timeout: int = 20,
) -> tuple[int, bytes]:
    body = None if data is None else json.dumps(data).encode("utf-8")
    merged_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if body is not None:
        merged_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=merged_headers, method=method)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"response exceeded {MAX_RESPONSE_BYTES} bytes: {url}")
                return response.status, body
        except urllib.error.HTTPError as exc:
            # Preserve API error bodies so callers can report Cloudflare's real error.
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"request failed after retries: {url}: {last_error}")


def fetch_text(url: str) -> str:
    status, body = http_request(url)
    if status != 200:
        raise RuntimeError(f"unexpected HTTP {status} from {url}")
    return body.decode("utf-8", errors="replace")


def normalize_extra_source_urls(value: str) -> list[str]:
    urls: list[str] = []
    for line in value.splitlines():
        url = line.strip()
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("EXTRA_SOURCE_URLS entries must be credential-free HTTPS URLs")
        urls.append(url)
    return urls


def parse_provider(comment: str, fallback: str) -> tuple[str, int]:
    comment = comment.strip()
    position_match = re.search(r"_(\d+)\s*$", comment)
    position = int(position_match.group(1)) if position_match else 999
    known = ("CMLiu", "VPS789", "CFYes", "WeTest", "CFSpeedTest", "IPDB")
    provider = next((name for name in known if name.lower() in comment.lower()), fallback)
    return provider, position


def parse_candidates(text: str, source: str, carrier: str) -> list[tuple[str, str, int, str]]:
    parsed: list[tuple[str, str, int, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        match = IPV4_RE.search(raw_line)
        if not match:
            continue
        ip = match.group(0)
        try:
            if not isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address):
                continue
        except ValueError:
            continue
        comment = raw_line.split("#", 1)[1] if "#" in raw_line else ""
        provider, position = parse_provider(comment, source)
        if position == 999:
            position = line_number
        parsed.append((ip, provider, position, carrier))
    return parsed


def parse_domains(text: str, limit: int = 40) -> list[str]:
    domains: list[str] = []
    for raw_line in text.splitlines():
        domain = raw_line.strip().lower().rstrip(".")
        if not domain or domain.startswith("#") or not DOMAIN_RE.fullmatch(domain):
            continue
        if domain not in domains:
            domains.append(domain)
        if len(domains) >= limit:
            break
    return domains


def resolve_domain(domain: str) -> tuple[str, list[str]]:
    addresses: set[str] = set()
    try:
        answers = socket.getaddrinfo(domain, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
        for answer in answers:
            addresses.add(answer[4][0])
    except OSError:
        pass
    return domain, sorted(addresses)


def collect_domain_candidates(candidates: dict[str, Candidate], providers_seen: set[str]) -> int:
    try:
        domains = parse_domains(fetch_text(BESTCF_DOMAIN_URL))
    except Exception as exc:
        print(f"warning: unable to read bestcf domains: {exc}")
        return 0

    resolved = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(domains)))) as pool:
        for domain, addresses in pool.map(resolve_domain, domains):
            if not addresses:
                continue
            provider = f"dns:{domain}"
            providers_seen.add(provider)
            for position, ip in enumerate(addresses, 1):
                candidate = candidates.setdefault(ip, Candidate(ip=ip))
                candidate.providers.add(provider)
                candidate.positions.append(position)
                candidate.carriers.add("global")
                candidate.recommendations += 1
                resolved += 1
    print(f"source bestcf-domains: {resolved} resolved IPv4 candidates from {len(domains)} domains")
    return resolved


def add_official_samples(
    candidates: dict[str, Candidate],
    networks: Iterable[ipaddress.IPv4Network],
    per_network: int,
) -> int:
    """Add deterministic coverage samples; these are fallbacks, not China-speed votes."""
    added = 0
    per_network = max(0, min(32, per_network))
    if not per_network:
        return 0
    for network in networks:
        usable = max(0, network.num_addresses - 2)
        for index in range(1, per_network + 1):
            offset = 1 + (usable * index) // (per_network + 1)
            ip = str(network.network_address + min(offset, network.num_addresses - 2))
            candidate = candidates.setdefault(ip, Candidate(ip=ip))
            candidate.providers.add("official-sample")
            candidate.positions.append(500 + index)
            candidate.carriers.add("global")
            added += 1
    return added


def load_cloudflare_networks() -> list[ipaddress.IPv4Network]:
    text = fetch_text(CF_IPV4_URL)
    networks: list[ipaddress.IPv4Network] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            network = ipaddress.ip_network(line)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            networks.append(network)
    if not networks:
        raise RuntimeError("Cloudflare official IPv4 list was empty")
    return networks


def is_cloudflare_ip(ip: str, networks: Iterable[ipaddress.IPv4Network]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def collect_candidates(isp: str, extra_urls: list[str]) -> tuple[dict[str, Candidate], set[str]]:
    carriers = ["cmcc", "cucc", "ctcc"] if isp == "all" else [isp]
    sources: list[tuple[str, str, str]] = [
        (f"bestcf-{carrier}", f"{BESTCF_BASE}/{SOURCE_FILES[carrier]}", carrier)
        for carrier in carriers
    ]
    sources.append(("bestcf-global", f"{BESTCF_BASE}/{SOURCE_FILES['global']}", "global"))
    sources.extend(REDUNDANT_SOURCES)
    sources.extend((f"extra-{index}", url, "extra") for index, url in enumerate(extra_urls, 1))

    candidates: dict[str, Candidate] = {}
    providers_seen: set[str] = set()
    successful_sources = 0
    for source_name, url, carrier in sources:
        try:
            text = fetch_text(url)
            parsed = parse_candidates(text, source_name, carrier)
            if not parsed:
                print(f"warning: source returned no IPv4 candidates: {source_name}")
                continue
            successful_sources += 1
            for ip, provider, position, parsed_carrier in parsed:
                candidate = candidates.setdefault(ip, Candidate(ip=ip))
                candidate.providers.add(provider)
                candidate.positions.append(position)
                candidate.carriers.add(parsed_carrier)
                candidate.recommendations += 1
                providers_seen.add(provider)
            print(f"source {source_name}: {len(parsed)} candidates")
        except Exception as exc:  # A broken third-party source must not abort the run.
            print(f"warning: unable to read {source_name}: {exc}")

    if successful_sources == 0:
        raise RuntimeError("all candidate sources failed")
    collect_domain_candidates(candidates, providers_seen)
    return candidates, providers_seen


def parse_expected_statuses(value: str) -> set[int] | None:
    if not value.strip():
        return None
    statuses: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            statuses.add(int(item))
    return statuses


def probe_once(ip: str, sni: str, path: str, timeout: float) -> tuple[float, int]:
    if not path.startswith("/"):
        path = "/" + path
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    started = time.monotonic()
    with socket.create_connection((ip, 443), timeout=timeout) as raw_socket:
        raw_socket.settimeout(timeout)
        with context.wrap_socket(raw_socket, server_hostname=sni) as tls_socket:
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {sni}\r\n"
                f"User-Agent: {USER_AGENT}\r\n"
                "Range: bytes=0-0\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            )
            tls_socket.sendall(request.encode("ascii"))
            response = tls_socket.recv(4096)
    elapsed_ms = (time.monotonic() - started) * 1000
    status_match = re.match(rb"HTTP/\d(?:\.\d)?\s+(\d{3})", response)
    if not status_match:
        raise RuntimeError("invalid HTTP response")
    return elapsed_ms, int(status_match.group(1))


def probe_candidate(
    candidate: Candidate,
    *,
    sni: str,
    path: str,
    expected_statuses: set[int] | None,
    attempts: int,
    timeout: float,
) -> Candidate:
    timings: list[float] = []
    last_status: int | None = None
    for _ in range(attempts):
        try:
            elapsed_ms, status = probe_once(candidate.ip, sni, path, timeout)
            status_ok = status in expected_statuses if expected_statuses else 200 <= status < 500
            if status_ok:
                timings.append(elapsed_ms)
                last_status = status
        except (OSError, ssl.SSLError, RuntimeError):
            continue
    candidate.successes = len(timings)
    candidate.status = last_status
    if timings:
        candidate.latency_ms = statistics.median(timings)
    return candidate


def candidate_sort_key(candidate: Candidate, isp: str) -> tuple:
    carrier_match = isp == "all" or isp in candidate.carriers
    return (
        -candidate.votes,
        -int(carrier_match),
        -len(candidate.carriers),
        -candidate.recommendations,
        candidate.average_position,
        -candidate.successes,
        candidate.latency_ms,
        candidate.ip,
    )


def select_probe_candidates(
    candidates: Iterable[Candidate], isp: str, limit: int, sample_reserve: int = 80
) -> list[Candidate]:
    """Keep a fallback slice of official coverage samples even with very large feeds."""
    ranked = sorted(candidates, key=lambda item: candidate_sort_key(item, isp))
    sourced = [item for item in ranked if item.votes > 0]
    sample_only = [item for item in ranked if item.votes == 0 and "official-sample" in item.providers]
    reserve = min(sample_reserve, limit // 4, len(sample_only))
    selected = sourced[: max(0, limit - reserve)]
    selected.extend(sample_only[:reserve])
    if len(selected) < limit:
        selected_ids = {id(item) for item in selected}
        selected.extend(item for item in ranked if id(item) not in selected_ids)
    return selected[:limit]


def verify_candidates(
    candidates: list[Candidate],
    *,
    sni: str,
    path: str,
    expected_statuses: set[int] | None,
    attempts: int,
    timeout: float,
    min_successes: int = 1,
) -> list[Candidate]:
    verified: list[Candidate] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(candidates)))) as pool:
        futures = [
            pool.submit(
                probe_candidate,
                candidate,
                sni=sni,
                path=path,
                expected_statuses=expected_statuses,
                attempts=attempts,
                timeout=timeout,
            )
            for candidate in candidates
        ]
        for future in concurrent.futures.as_completed(futures):
            candidate = future.result()
            if candidate.successes >= min_successes:
                verified.append(candidate)
    return verified


class CloudflareDNS:
    def __init__(self, token: str, zone_id: str):
        self.token = token
        self.zone_id = zone_id

    def request(self, path: str, *, method: str = "GET", data: dict | None = None) -> dict:
        status, body = http_request(
            f"{CF_API}{path}",
            method=method,
            headers={"Authorization": f"Bearer {self.token}"},
            data=data,
        )
        payload = json.loads(body.decode("utf-8"))
        if status >= 400 or not payload.get("success"):
            raise RuntimeError(f"Cloudflare API error: {payload.get('errors', payload)}")
        return payload

    def find_a_record(self, name: str) -> dict | None:
        query = urllib.parse.urlencode({"type": "A", "name": name, "per_page": 100})
        payload = self.request(f"/zones/{self.zone_id}/dns_records?{query}")
        records = payload.get("result", [])
        if len(records) > 1:
            raise RuntimeError(f"multiple A records found for {name}; keep exactly one")
        return records[0] if records else None

    def upsert_a_record(self, name: str, ip: str, record: dict | None) -> dict:
        data = {
            "type": "A",
            "name": name,
            "content": ip,
            "ttl": 300,
            "proxied": False,
            "comment": "Managed by cf-best-ip-actions; keep DNS-only",
        }
        if record:
            return self.request(
                f"/zones/{self.zone_id}/dns_records/{record['id']}", method="PATCH", data=data
            )["result"]
        return self.request(f"/zones/{self.zone_id}/dns_records", method="POST", data=data)["result"]


def parse_modified_age_hours(record: dict | None) -> float:
    if not record or not record.get("modified_on"):
        return float("inf")
    value = record["modified_on"].replace("Z", "+00:00")
    modified = dt.datetime.fromisoformat(value)
    now = dt.datetime.now(dt.timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 3600)


def should_switch(
    current: Candidate | None,
    best: Candidate,
    *,
    current_age_hours: float,
    min_age_hours: float,
) -> tuple[bool, str]:
    if current is None or not current.successes:
        return True, "current DNS IP is missing or failed validation"
    if current.ip == best.ip:
        return False, "current DNS IP is already the best candidate"
    if current_age_hours < min_age_hours:
        return False, f"current DNS IP is healthy and younger than {min_age_hours:g} hours"
    if best.votes >= current.votes + 1:
        return True, "new candidate has support from more independent sources"
    if best.votes == current.votes and best.average_position + 2 <= current.average_position:
        return True, "new candidate has a materially better upstream rank"
    if not current.providers:
        return True, "healthy current IP is no longer present in fresh candidate sources"
    return False, "current DNS IP remains healthy without a materially better candidate"


def append_summary(lines: list[str]) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def main() -> int:
    token = os.getenv("CF_API_TOKEN", "").strip()
    zone_id = os.getenv("CF_ZONE_ID", "").strip()
    dns_record = os.getenv("DNS_RECORD", "").strip().rstrip(".")
    sni = os.getenv("SNI_DOMAIN", "").strip().rstrip(".")
    isp = os.getenv("ISP", "all").strip().lower()
    path = os.getenv("VERIFY_PATH", "/").strip() or "/"
    expected_statuses = parse_expected_statuses(os.getenv("EXPECTED_STATUSES", ""))
    min_age_hours = float(os.getenv("MIN_SWITCH_AGE_HOURS", "24"))
    attempts = max(2, min(5, int(os.getenv("PROBE_ATTEMPTS", "3"))))
    min_successes = max(1, min(attempts, int(os.getenv("MIN_PROBE_SUCCESSES", "2"))))
    probe_limit = max(40, min(500, int(os.getenv("PROBE_LIMIT", "400"))))
    samples_per_range = max(0, min(32, int(os.getenv("OFFICIAL_SAMPLE_PER_RANGE", "8"))))
    timeout = max(2.0, min(15.0, float(os.getenv("PROBE_TIMEOUT", "6"))))
    dry_run = env_bool("DRY_RUN", False)
    extra_urls = normalize_extra_source_urls(os.getenv("EXTRA_SOURCE_URLS", ""))

    if not token or not zone_id:
        raise RuntimeError("CF_API_TOKEN and CF_ZONE_ID secrets are required")
    if not dns_record or not sni:
        raise RuntimeError("DNS_RECORD and SNI_DOMAIN variables are required")
    if isp not in {"cmcc", "cucc", "ctcc", "all"}:
        raise RuntimeError("ISP must be one of: cmcc, cucc, ctcc, all")
    if dns_record == sni:
        raise RuntimeError("DNS_RECORD must be the grey-cloud lookup name, not the orange-cloud SNI name")

    print(f"target DNS: {dns_record}")
    print(f"validation SNI: {sni}{path}")
    print(f"ISP profile: {isp}; dry-run: {dry_run}")

    cf_dns = CloudflareDNS(token, zone_id)
    record = cf_dns.find_a_record(dns_record)
    current_ip = record.get("content") if record else None
    if record and record.get("proxied"):
        raise RuntimeError(f"{dns_record} is proxied; turn it to DNS-only/grey-cloud before running")

    candidates_by_ip, providers_seen = collect_candidates(isp, extra_urls)
    if len(providers_seen) < 2:
        raise RuntimeError("fewer than two independent candidate providers were available; keeping current DNS")

    networks = load_cloudflare_networks()
    sampled = add_official_samples(candidates_by_ip, networks, samples_per_range)
    official_count = sum(is_cloudflare_ip(ip, networks) for ip in candidates_by_ip)
    proxy_count = len(candidates_by_ip) - official_count
    print(
        f"candidate pool: {len(candidates_by_ip)}; official: {official_count}; "
        f"TLS-gated proxy/BYOIP: {proxy_count}; official samples added: {sampled}"
    )
    if not candidates_by_ip:
        raise RuntimeError("candidate pool was empty")

    ranked_for_probe = select_probe_candidates(candidates_by_ip.values(), isp, probe_limit)
    verified = verify_candidates(
        ranked_for_probe,
        sni=sni,
        path=path,
        expected_statuses=expected_statuses,
        attempts=attempts,
        timeout=timeout,
        min_successes=min_successes,
    )
    verified.sort(key=lambda item: candidate_sort_key(item, isp))
    if not verified:
        raise RuntimeError("no candidate passed TLS/HTTPS validation; keeping current DNS")

    best = verified[0]
    print("top validated candidates:")
    for candidate in verified[:10]:
        print(
            f"  {candidate.ip} sources={candidate.votes} "
            f"recommendations={candidate.recommendations} rank={candidate.average_position:.1f} "
            f"tls_http={candidate.latency_ms:.0f}ms successes={candidate.successes}/{attempts} "
            f"status={candidate.status} official={is_cloudflare_ip(candidate.ip, networks)}"
        )

    current_candidate: Candidate | None = None
    if current_ip:
        if current_ip in candidates_by_ip:
            current_candidate = candidates_by_ip[current_ip]
        else:
            current_candidate = Candidate(ip=current_ip)
        probe_candidate(
            current_candidate,
            sni=sni,
            path=path,
            expected_statuses=expected_statuses,
            attempts=attempts,
            timeout=timeout,
        )

    age_hours = parse_modified_age_hours(record)
    switch, reason = should_switch(
        current_candidate,
        best,
        current_age_hours=age_hours,
        min_age_hours=min_age_hours,
    )
    selected_ip = best.ip if switch else current_ip
    action = "would update" if dry_run and switch else "updated" if switch else "kept"
    print(f"decision: {action} {dns_record} -> {selected_ip}; reason: {reason}")

    if switch and not dry_run:
        result = cf_dns.upsert_a_record(dns_record, best.ip, record)
        if result.get("content") != best.ip or result.get("proxied"):
            raise RuntimeError("Cloudflare returned an unexpected DNS record after update")

    summary_lines = [
            "## Cloudflare 优选 IP 结果",
            "",
            f"- 目标记录：`{dns_record}`",
            f"- 验证域名：`{sni}`",
            f"- 运营商配置：`{isp}`",
            f"- 独立来源：**{len(providers_seen)}**",
            f"- 总候选：**{len(candidates_by_ip)}**（官方网段 {official_count}，反代/BYOIP {proxy_count}）",
            f"- 本轮探测：**{len(ranked_for_probe)}**；连续通过：**{len(verified)}**（至少 {min_successes}/{attempts} 轮）",
            f"- 当前 IP：`{current_ip or '不存在'}`",
            f"- 最佳候选：`{best.ip}`（{best.votes} 个来源，{best.recommendations} 次推荐，HTTP {best.status}）",
            f"- 最终决定：**{action}** `{'%s' % selected_ip}`",
            f"- 原因：{reason}",
            f"- 模式：`{'dry-run' if dry_run else 'live'}`",
            "",
            "### 前 10 个连续验证通过的候选",
            "",
            "| # | IP | 类型 | 来源数 | 推荐次数 | 成功轮次 | GitHub RTT | HTTP |",
            "|---:|---|---|---:|---:|---:|---:|---:|",
        ]
    for index, candidate in enumerate(verified[:10], 1):
        ip_type = "官方" if is_cloudflare_ip(candidate.ip, networks) else "反代/BYOIP"
        summary_lines.append(
            f"| {index} | `{candidate.ip}` | {ip_type} | {candidate.votes} | "
            f"{candidate.recommendations} | {candidate.successes}/{attempts} | "
            f"{candidate.latency_ms:.0f} ms | {candidate.status} |"
        )
    summary_lines.extend(
        [
            "",
            "> GitHub RTT 仅用于可用性和同级候选排序，不代表中国本地宽带延迟。",
            "> 反代/BYOIP 候选不靠网段名称放行，必须通过目标 SNI 的系统证书校验和多轮 HTTP 探测。",
        ]
    )
    append_summary(summary_lines)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        append_summary(["## Cloudflare 优选 IP 失败", "", f"- `{type(error).__name__}: {error}`", "- DNS 未修改。"])
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
