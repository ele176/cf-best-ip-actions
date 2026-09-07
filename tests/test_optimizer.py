import importlib.util
import ipaddress
import pathlib
import sys
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).parents[1] / "scripts" / "optimizer.py"
SPEC = importlib.util.spec_from_file_location("optimizer", MODULE_PATH)
optimizer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = optimizer
SPEC.loader.exec_module(optimizer)


class OptimizerTests(unittest.TestCase):
    def test_parse_candidates_and_provider(self):
        text = """
        104.16.1.2#CMCC-IPv4_CMLiu_1
        ignored text
        172.64.9.10#CMCC-IPv4_VPS789_4
        999.999.999.999#invalid
        """
        parsed = optimizer.parse_candidates(text, "fallback", "cmcc")
        self.assertEqual(parsed[0], ("104.16.1.2", "CMLiu", 1, "cmcc"))
        self.assertEqual(parsed[1], ("172.64.9.10", "VPS789", 4, "cmcc"))
        self.assertEqual(len(parsed), 2)

    def test_cloudflare_network_filter(self):
        networks = [ipaddress.ip_network("104.16.0.0/13"), ipaddress.ip_network("172.64.0.0/13")]
        self.assertTrue(optimizer.is_cloudflare_ip("104.16.1.2", networks))
        self.assertFalse(optimizer.is_cloudflare_ip("8.8.8.8", networks))

    def test_rank_prefers_more_sources(self):
        one = optimizer.Candidate("104.16.1.1", {"a"}, {"cmcc"}, [1], 10, 200, 2)
        two = optimizer.Candidate("104.16.1.2", {"a", "b"}, {"cmcc"}, [8, 8], 50, 200, 2)
        ranked = sorted([one, two], key=lambda item: optimizer.candidate_sort_key(item, "cmcc"))
        self.assertEqual(ranked[0].ip, two.ip)

    def test_switch_immediately_when_current_fails(self):
        current = optimizer.Candidate("104.16.1.1")
        best = optimizer.Candidate("104.16.1.2", {"a"}, {"cmcc"}, [1], 20, 200, 2)
        switch, _ = optimizer.should_switch(current, best, current_age_hours=1, min_age_hours=24)
        self.assertTrue(switch)

    def test_keep_recent_healthy_current(self):
        current = optimizer.Candidate("104.16.1.1", {"a"}, {"cmcc"}, [5], 20, 200, 2)
        best = optimizer.Candidate("104.16.1.2", {"a", "b"}, {"cmcc"}, [1], 10, 200, 2)
        switch, _ = optimizer.should_switch(current, best, current_age_hours=2, min_age_hours=24)
        self.assertFalse(switch)

    def test_extra_sources_require_credential_free_https(self):
        self.assertEqual(
            optimizer.normalize_extra_source_urls("https://example.com/ips.txt\n"),
            ["https://example.com/ips.txt"],
        )
        for unsafe in ("http://example.com/ips.txt", "https://user:pass@example.com/ips.txt"):
            with self.subTest(unsafe=unsafe), self.assertRaises(RuntimeError):
                optimizer.normalize_extra_source_urls(unsafe)

    def test_plain_source_uses_line_number_as_rank(self):
        parsed = optimizer.parse_candidates("# title\n104.16.0.1\n104.16.0.2\n", "plain", "global")
        self.assertEqual(parsed[0], ("104.16.0.1", "plain", 2, "global"))
        self.assertEqual(parsed[1], ("104.16.0.2", "plain", 3, "global"))

    def test_parse_domains_rejects_non_domains_and_deduplicates(self):
        parsed = optimizer.parse_domains(
            "Best.Example.com\nbest.example.com\nhttps://bad.example.com/path\ninvalid\ncf.example.org\n"
        )
        self.assertEqual(parsed, ["best.example.com", "cf.example.org"])

    def test_official_sampling_covers_each_range(self):
        candidates = {}
        networks = [ipaddress.ip_network("104.16.0.0/24"), ipaddress.ip_network("172.64.0.0/24")]
        added = optimizer.add_official_samples(candidates, networks, 4)
        self.assertEqual(added, 8)
        self.assertEqual(len(candidates), 8)
        self.assertTrue(all("official-sample" in item.providers for item in candidates.values()))
        self.assertTrue(all(item.votes == 0 for item in candidates.values()))

    def test_probe_selection_reserves_official_coverage(self):
        sourced = [
            optimizer.Candidate(f"104.16.0.{index}", {"feed"}, {"global"}, [index])
            for index in range(1, 31)
        ]
        samples = [
            optimizer.Candidate(f"172.64.0.{index}", {"official-sample"}, {"global"}, [500])
            for index in range(1, 21)
        ]
        selected = optimizer.select_probe_candidates(sourced + samples, "all", 40, sample_reserve=8)
        self.assertEqual(len(selected), 40)
        self.assertGreaterEqual(sum(item.votes == 0 for item in selected), 8)

    def test_verify_requires_multiple_successes(self):
        candidates = [optimizer.Candidate("104.16.0.1"), optimizer.Candidate("104.16.0.2")]

        def fake_probe(candidate, **_kwargs):
            candidate.successes = 2 if candidate.ip.endswith(".2") else 1
            candidate.latency_ms = 10
            candidate.status = 200
            return candidate

        with mock.patch.object(optimizer, "probe_candidate", side_effect=fake_probe):
            verified = optimizer.verify_candidates(
                candidates,
                sni="example.com",
                path="/",
                expected_statuses=None,
                attempts=3,
                timeout=2,
                min_successes=2,
            )
        self.assertEqual([item.ip for item in verified], ["104.16.0.2"])


if __name__ == "__main__":
    unittest.main()
