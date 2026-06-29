"""Unit tests for the #17 slice-3 backend file-coverage delivery detector + warning builder.

Loads the module BY FILE PATH so it runs with bare ``python3 -m unittest`` — no ``open_webui``
package import, no live stack — mirroring ``test_failure_surface.py``. Covers: the pure
classifier, the Path-B handoff-delivery scan (against a real temp dir, list/stat only), the
warn gating, the privacy-safe warning payload (allow-list + fail-loud canonical kind), the
trace format, and a drift-guard vs the parent-repo ops classifier.
"""
import hashlib
import importlib.util
import os
import tempfile
import unittest

_HERE = os.path.dirname(__file__)
_MOD = os.path.normpath(os.path.join(_HERE, "..", "..", "utils", "file_coverage.py"))
_spec = importlib.util.spec_from_file_location("file_coverage", _MOD)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


def _handoff_name(fc_mod, raw_name, idx=1, nonce="deadbeef"):
    """Reproduce hermes_handoff's filename scheme: {idx:03d}-{nonce8}-{safe_name}."""
    return f"{idx:03d}-{nonce}-{fc_mod.safe_segment(os.path.basename(raw_name))}"


def _make_handoff_tree(root, user_id, chat_id, msg_id, raw_names):
    d = os.path.join(root, "user", user_id, "chat", chat_id, "message", msg_id)
    os.makedirs(d, exist_ok=True)
    for i, nm in enumerate(raw_names):
        with open(os.path.join(d, _handoff_name(fc, nm, idx=i)), "wb") as fh:
            fh.write(b"x")  # 1 byte; the scanner never reads bytes, only lists/stats
    return d


def _file_item(name, status="uploaded", error=None):
    return {"type": "file", "id": "id-" + name, "name": name, "status": status, "error": error}


class TestClassifier(unittest.TestCase):
    def _s(self, sig):
        return fc.classify_file(sig)["status"]

    def test_handed_off(self):
        self.assertEqual(self._s({"upload_status": "uploaded", "handed_off": True, "handoff_dir_present": True}), "handed_off")

    def test_missed_handoff_is_uploaded_not_in_handoff(self):
        v = fc.classify_file({"upload_status": "uploaded", "handed_off": False, "handoff_dir_present": True})
        self.assertEqual(v["status"], "uploaded")
        self.assertEqual(v["reason"], "not_in_handoff")

    def test_path_a_no_handoff_is_uploaded_low(self):
        v = fc.classify_file({"upload_status": "uploaded", "handed_off": None, "handoff_dir_present": False})
        self.assertEqual((v["status"], v["confidence"]), ("uploaded", "low"))

    def test_failed_and_skipped_buckets(self):
        self.assertEqual(self._s({"error_bucket": "upload_error"}), "failed")
        self.assertEqual(self._s({"error_bucket": "unsupported_type"}), "skipped")

    def test_failure_beats_handoff(self):
        self.assertEqual(self._s({"error_bucket": "upload_error", "handed_off": True, "handoff_dir_present": True}), "failed")


class TestDeliveryScan(unittest.TestCase):
    """End-to-end (temp filesystem) Path-B handoff-delivery coverage."""

    def test_all_delivered_no_warn(self):
        with tempfile.TemporaryDirectory() as root:
            names = ["Contract A.pdf", "Exhibit (1).docx", "report.pdf"]
            _make_handoff_tree(root, "u1", "c1", "m1", names)
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", [_file_item(n) for n in names])
            self.assertEqual((cov["present"], cov["total"], cov["used"], cov["unused"]), (True, 3, 3, 0))
            self.assertFalse(fc.should_warn(cov))

    def test_one_not_delivered_warns(self):
        with tempfile.TemporaryDirectory() as root:
            delivered = ["Contract A.pdf", "report.pdf"]
            _make_handoff_tree(root, "u1", "c1", "m1", delivered)
            items = [_file_item("Contract A.pdf"), _file_item("report.pdf"), _file_item("MISSED Exhibit.pdf")]
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", items)
            self.assertEqual((cov["total"], cov["used"], cov["unused"]), (3, 2, 1))
            self.assertTrue(fc.should_warn(cov))

    def test_duplicate_names_matched_by_count(self):
        with tempfile.TemporaryDirectory() as root:
            # two uploads share a name but only ONE copy reached handoff -> 1 delivered, 1 unused
            _make_handoff_tree(root, "u1", "c1", "m1", ["dup.pdf"])
            items = [_file_item("dup.pdf"), _file_item("dup.pdf")]
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", items)
            self.assertEqual((cov["used"], cov["unused"]), (1, 1))
            self.assertTrue(fc.should_warn(cov))

    def test_path_a_no_handoff_dir_never_warns(self):
        with tempfile.TemporaryDirectory() as root:
            # no handoff tree created -> present=False -> unmeasurable -> no warning
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", [_file_item("a.pdf")])
            self.assertFalse(cov["present"])
            self.assertFalse(fc.should_warn(cov))

    def test_no_files_never_warns(self):
        with tempfile.TemporaryDirectory() as root:
            _make_handoff_tree(root, "u1", "c1", "m1", [])
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", [])
            self.assertEqual(cov["total"], 0)
            self.assertFalse(fc.should_warn(cov))

    def test_unsupported_file_is_skipped_not_unused(self):
        with tempfile.TemporaryDirectory() as root:
            _make_handoff_tree(root, "u1", "c1", "m1", ["ok.pdf"])
            items = [_file_item("ok.pdf"), _file_item("weird.xyz", status="error", error="unsupported file type")]
            cov = fc.compute_turn_delivery_coverage(root, "u1", "c1", "m1", items)
            self.assertEqual((cov["used"], cov["skipped"], cov["unused"]), (1, 1, 0))
            # unused==0 -> per Planner gate, no warning (the skip is a separate, reported count)
            self.assertFalse(fc.should_warn(cov))

    def test_scan_error_suppresses_warning(self):
        cov = {"scan_error": True, "present": False, "total": 0, "used": 0, "unused": 0, "skipped": 0, "failed": 0}
        self.assertFalse(fc.should_warn(cov))


class TestShouldWarnMatrix(unittest.TestCase):
    def test_matrix(self):
        base = {"scan_error": False, "skipped": 0, "failed": 0}
        self.assertTrue(fc.should_warn({**base, "present": True, "total": 3, "used": 2, "unused": 1}))
        self.assertFalse(fc.should_warn({**base, "present": True, "total": 3, "used": 3, "unused": 0}))
        self.assertFalse(fc.should_warn({**base, "present": False, "total": 3, "used": 0, "unused": 3}))
        self.assertFalse(fc.should_warn({**base, "present": True, "total": 0, "used": 0, "unused": 0}))


class TestTraceId(unittest.TestCase):
    def test_format_and_truncation(self):
        self.assertEqual(fc.make_trace_id("deadbeefcafef00d", "0123456789abcdef"), "t-deadbeef-01234567")

    def test_short_or_missing(self):
        self.assertEqual(fc.make_trace_id("abc", ""), "t-abc-")
        self.assertEqual(fc.make_trace_id(None, None), "t--")


class TestWarningPayload(unittest.TestCase):
    def test_canonical_kind(self):
        self.assertEqual(fc.WARNING_KIND_PARTIAL, "partial_materials")
        self.assertIn(fc.WARNING_KIND_PARTIAL, fc.ALLOWED_WARNING_KINDS)

    def test_payload_has_only_allow_listed_keys(self):
        p = fc.build_warning_payload("deadbeefcafef00d", "0123456789abcdef", used=10, total=12, unused=2, skipped=0, failed=0)
        self.assertEqual(set(p), set(fc.WARNING_PAYLOAD_ALLOWED_KEYS))

    def test_payload_counts_kind_trace_only(self):
        p = fc.build_warning_payload("deadbeefcafef00d", "0123456789abcdef", used=10, total=12, unused=2)
        self.assertEqual(p["kind"], "partial_materials")
        self.assertEqual(p["trace_id"], "t-deadbeef-01234567")
        self.assertEqual((p["used"], p["total"], p["unused"]), (10, 12, 2))
        # content is exactly the fixed counts-banner — nothing else can be in it
        self.assertEqual(p["content"], fc.build_warning_banner(p["used"], p["total"], p["unused"]))
        self.assertIn("2 of 12", p["content"])

    def test_builder_cannot_carry_injected_raw_content(self):
        # ids are truncated to 8 and only ids/counts appear; no name/content parameter exists
        p = fc.build_warning_payload("chatWITHsecretNAME", "msgWITHsecret", used=1, total=2, unused=1)
        self.assertNotIn("secretNAME", p["content"])
        self.assertNotIn("WITHsecret", p["content"])
        self.assertNotIn("WITHsecret", p["trace_id"])

    def test_rejects_non_canonical_kind(self):
        with self.assertRaises(ValueError):
            fc.build_warning_payload("c1234567", "m1234567", used=1, total=2, unused=1, kind="RAW_CLIENT_MATTER")

    def test_rejects_negative_or_nonint_or_bool_counts(self):
        for bad in (dict(used=-1, total=2, unused=1), dict(used=1, total=2, unused="2"),
                    dict(used=True, total=2, unused=1)):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    fc.build_warning_payload("c1234567", "m1234567", **bad)

    def test_assert_rejects_extra_key(self):
        with self.assertRaises(ValueError):
            fc.assert_warning_privacy_safe({"content": fc.build_warning_banner(1, 2, 1), "kind": "partial_materials",
                                            "used": 1, "total": 2, "unused": 1, "skipped": 0, "failed": 0,
                                            "trace_id": "t-a-b", "raw": "leak"})

    def test_assert_rejects_non_canonical_kind(self):
        with self.assertRaises(ValueError):
            fc.assert_warning_privacy_safe({"content": fc.build_warning_banner(1, 2, 1), "kind": "RAW_MATTER",
                                            "used": 1, "total": 2, "unused": 1, "skipped": 0, "failed": 0,
                                            "trace_id": "t-a-b"})

    def test_assert_rejects_tampered_banner(self):
        # a content not reconstructible from counts (e.g. a raw name spliced in) → fail
        with self.assertRaises(ValueError):
            fc.assert_warning_privacy_safe({"content": "Re: Secret client matter — 2 of 12 files",
                                            "kind": "partial_materials", "used": 10, "total": 12, "unused": 2,
                                            "skipped": 0, "failed": 0, "trace_id": "t-a-b"})

    def test_valid_payload_round_trips(self):
        p = fc.build_warning_payload("c1234567", "m1234567", used=10, total=12, unused=2, skipped=1, failed=1)
        self.assertEqual(fc.assert_warning_privacy_safe(p), p)


class TestSanitizerAndVocabularyDriftGuard(unittest.TestCase):
    """Single-sourcing-against-drift: the backend classifier + status vocab + sanitizer must
    match scripts/ops/openwebui_8083_file_coverage.py (the container can't import it, so the
    check lives at test time; skipped when the parent-repo layout is unreachable)."""

    def _load_ops(self):
        ops_path = os.path.normpath(os.path.join(
            _HERE, "..", "..", "..", "..", "..", "scripts", "ops", "openwebui_8083_file_coverage.py"))
        if not os.path.exists(ops_path):
            self.skipTest("parent-repo ops file_coverage script not reachable from here")
        spec = importlib.util.spec_from_file_location("ops_file_coverage", ops_path)
        ops = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops)
        return ops

    def test_vocabulary_matches_ops(self):
        ops = self._load_ops()
        self.assertEqual(fc.FILE_STATUSES, ops.FILE_STATUSES)
        self.assertEqual(fc.SKIP_REASONS, ops.SKIP_REASONS)
        self.assertEqual(fc.FAIL_REASONS, ops.FAIL_REASONS)
        self.assertEqual(fc.ALL_REASONS, ops.ALL_REASONS)

    def test_classify_agrees_with_ops_on_all_fixtures(self):
        ops = self._load_ops()
        for name, sig in ops.FIXTURES.items():
            with self.subTest(fixture=name):
                self.assertEqual(fc.classify_file(sig), ops.classify_file(sig))

    def test_summarize_agrees_with_ops(self):
        ops = self._load_ops()
        rows = [{"verdict": fc.classify_file(s)} for s in ops.FIXTURES.values()]
        ctx = {"handoff_dir_present": True, "is_path_b": True}
        self.assertEqual(fc.summarize(rows, ctx), ops.summarize(rows, ctx))

    def test_safe_segment_matches_ops(self):
        ops = self._load_ops()
        for s in ["(With Translation) App as filed.pdf", "  a b c .docx", "中文 file.pdf",
                  "x" * 200 + ".pdf", "...", "ok.pdf", ""]:
            with self.subTest(s=s[:20]):
                self.assertEqual(fc.safe_segment(s), ops.safe_segment(s))


class TestHandoffKeyingIntegration(unittest.TestCase):
    """#17 slice-3 keying-DRIFT guard (regression for the inert-warning defect found in live
    verification, 2026-06-30). Runs the REAL ``run_hermes_handoff`` end-to-end against a temp
    handoff volume (byte-resolution monkeypatched so no Storage/Files/open_webui import) with an
    **id-stripped** ``body['messages']`` (exactly what the frontend sends), then asserts the
    finalize coverage scan resolves the SAME dir the handoff wrote — by reading the recorded
    ``metadata['handoff_message_id']`` (the fix), NOT by reconstructing ``message/<user_message.id>/``
    (the bug). If the two keyings ever drift apart again, this test fails."""

    def _load_hh(self):
        hh_path = os.path.normpath(os.path.join(_HERE, "..", "..", "skiprag", "hermes_handoff.py"))
        spec = importlib.util.spec_from_file_location("hermes_handoff_under_test", hh_path)
        hh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hh)  # stdlib-only at import; Storage/Files are lazy-imported
        return hh

    def _run_handoff(self, hh, root, beta_bytes=b""):
        import asyncio
        import types

        async def fake_resolve(file_item, filename):
            # alpha delivers real bytes → handed off; beta resolves per ``beta_bytes`` (empty →
            # the genuine 'could not resolve bytes' skip), without touching Storage.
            return beta_bytes if (filename or "").startswith("beta") else b"ALPHA-bytes"

        hh.HANDOFF_DIR = root
        hh._resolve_raw_bytes = fake_resolve  # module-global lookup at call time
        user = types.SimpleNamespace(id="user-keying-1")
        items = [
            {"type": "file", "id": "a", "name": "alpha.txt"},
            {"type": "file", "id": "b", "name": "beta.txt"},
        ]
        user_message = {"id": "umid-stable-keying", "role": "user", "files": items}
        body = {
            # NO per-message id — the real flow (Chat.svelte maps to {role,content,...}).
            "messages": [{"role": "user", "content": "here are my materials"}],
            "metadata": {"chat_id": "chat-keying-1", "user_message": user_message,
                         "user_message_id": "umid-stable-keying", "files": list(items)},
        }
        asyncio.run(hh.run_hermes_handoff(None, body, {}, user))
        return user, user_message, items, body["metadata"]

    def test_finalize_scan_resolves_the_same_dir_handoff_wrote(self):
        hh = self._load_hh()
        with tempfile.TemporaryDirectory() as root:
            user, user_message, items, md = self._run_handoff(hh, root)

            recorded = md.get("handoff_message_id")
            self.assertTrue(recorded, "run_hermes_handoff must record the dir segment it wrote")

            # THE FIX: scanning the recorded id resolves the exact dir the handoff created.
            cov = fc.compute_turn_delivery_coverage(root, user.id, "chat-keying-1", recorded, items)
            self.assertTrue(cov["present"], "recorded id must resolve the written handoff dir")
            self.assertEqual((cov["total"], cov["used"], cov["unused"]), (2, 1, 1))
            self.assertTrue(fc.should_warn(cov))

            # THE BUG (load-bearing): the OLD keying — scanning user_message.id — does NOT match,
            # because the handoff dir is keyed on the id-stripped body messages (random uuid).
            cov_old = fc.compute_turn_delivery_coverage(
                root, user.id, "chat-keying-1", user_message["id"], items
            )
            self.assertFalse(cov_old["present"], "user_message.id must NOT match (proves fix needed)")
            self.assertFalse(fc.should_warn(cov_old))

            # And the recorded id is the actual on-disk dir name (no hidden second random uuid).
            written = os.path.join(root, "user", "user-keying-1", "chat", "chat-keying-1",
                                   "message", recorded)
            self.assertTrue(os.path.isdir(written))

    def test_all_delivered_via_real_handoff_does_not_warn(self):
        # Negative: when both files resolve, the same recorded-id scan reports unused=0 → no warn.
        hh = self._load_hh()
        with tempfile.TemporaryDirectory() as root:
            user, user_message, items, md = self._run_handoff(hh, root, beta_bytes=b"BETA-bytes")
            recorded = md.get("handoff_message_id")
            cov = fc.compute_turn_delivery_coverage(root, user.id, "chat-keying-1", recorded, items)
            self.assertTrue(cov["present"])
            self.assertEqual((cov["total"], cov["used"], cov["unused"]), (2, 2, 0))
            self.assertFalse(fc.should_warn(cov))


if __name__ == "__main__":
    unittest.main()
