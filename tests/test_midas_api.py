"""Offline behavior checks. No MIDAS instance or external network is used."""
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlunsplit

TEST_BASE = urlunsplit(("https", "example.invalid", "/civil", "", ""))

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "midas_api.py"
SPEC = importlib.util.spec_from_file_location("skill_midas_api", MODULE_PATH)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


def response(payload):
    result = Mock()
    result.status = 200
    result.read.return_value = json.dumps(payload).encode()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "MIDAS_BASE_URL": TEST_BASE,
            "MIDAS_API_KEY": "synthetic-test-credential"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = api.MidasClient()

    def test_get_retries_and_counts_every_attempt(self):
        with patch.object(self.client._opener, "open", side_effect=[
                URLError("offline"), response({"UNIT": {"1": {"DIST": "M"}}})]) as send:
            with patch.object(api.time, "sleep"):
                result = self.client.request("GET", "/db/UNIT")
        self.assertIn("UNIT", result)
        self.assertEqual(send.call_count, 2)
        self.assertEqual([x["ok"] for x in self.client.calls], [False, True])
        self.assertNotIn("synthetic-test-credential", json.dumps(self.client.calls))

    def test_mutations_never_retry_when_outcome_is_unknown(self):
        for method, endpoint in (("POST", "/doc/anal"), ("PUT", "/db/SECF"), ("DELETE", "/db/NODE/9")):
            with self.subTest(method=method):
                with patch.object(self.client._opener, "open", side_effect=TimeoutError()) as send:
                    with self.assertRaisesRegex(api.MidasAPIError, "Outcome unknown"):
                        self.client.request(method, endpoint, {})
                self.assertEqual(send.call_count, 1)

    def test_http_200_failure_redacts_credential(self):
        payload = {"error": {"message": "rejected synthetic-test-credential"}}
        with patch.object(self.client._opener, "open", return_value=response(payload)):
            with self.assertRaises(api.MidasAPIError) as caught:
                self.client.request("POST", "/doc/anal", {})
        self.assertNotIn("synthetic-test-credential", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))
        self.assertFalse(self.client.calls[-1]["ok"])

    def test_http_auth_failure_does_not_retry(self):
        failure = HTTPError(TEST_BASE, 401, "unauthorized", {}, io.BytesIO())
        with patch.object(self.client._opener, "open", side_effect=failure) as send:
            with self.assertRaisesRegex(api.MidasAPIError, "HTTP 401"):
                self.client.request("GET", "/db/UNIT")
        self.assertEqual(send.call_count, 1)

    def test_application_error_redacts_connection_address(self):
        payload = {"error": "request rejected at " + TEST_BASE + "/db/UNIT"}
        with patch.object(self.client._opener, "open", return_value=response(payload)):
            with self.assertRaises(api.MidasAPIError) as caught:
                self.client.request("GET", "/db/UNIT")
        self.assertNotIn(TEST_BASE, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_json_output_redacts_escaped_credentials_and_connection_address(self):
        for key in ('synthetic"quoted-credential', 'synthetic\\escaped-credential'):
            with self.subTest(key=key), patch.dict(os.environ, {
                    "MIDAS_API_KEY": key, "MIDAS_BASE_URL": TEST_BASE + "/"}):
                payload = {"credential_echo": key, "address_echo": TEST_BASE + "/db/UNIT",
                           "UNIT": {"1": {"DIST": "M"}}}
                with patch("sys.stdout", new=io.StringIO()) as stdout:
                    api._output(payload, None)
                decoded = json.loads(stdout.getvalue())
                self.assertEqual(decoded["credential_echo"], "[REDACTED]")
                self.assertEqual(decoded["address_echo"], "[REDACTED]/db/UNIT")
                self.assertEqual(decoded["UNIT"], payload["UNIT"])

    def test_json_file_output_and_cli_errors_redact_configuration(self):
        payload = {"credential_echo": self.client._key, "address_echo": TEST_BASE}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "result.json"
            api._output(payload, str(target))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {
                "credential_echo": "[REDACTED]", "address_echo": "[REDACTED]"})
        failure = api.MidasAPIError(self.client._key + " " + TEST_BASE)
        with patch.object(api, "MidasClient", side_effect=failure):
            with patch("sys.stderr", new=io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as caught:
                    api.main(["probe"])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(self.client._key, stderr.getvalue())
        self.assertNotIn(TEST_BASE, stderr.getvalue())
        self.assertIn("[REDACTED]", stderr.getvalue())

    def test_quoted_credential_does_not_match_json_structure(self):
        payload = {"synthetic": {"value": 123, "items": [True, None, "ordinary"]}}
        with patch.dict(os.environ, {"MIDAS_API_KEY": 'synthetic"'}):
            with patch("sys.stdout", new=io.StringIO()) as stdout:
                api._output(payload, None)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_json_redaction_handles_nested_keys_without_mutating_input(self):
        key = self.client._key
        payload = {"nested": [{key: key}, {"base": TEST_BASE}], "count": 2}
        before = deepcopy(payload)
        with patch("sys.stdout", new=io.StringIO()) as stdout:
            api._output(payload, None)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "nested": [{"[REDACTED]": "[REDACTED]"}, {"base": "[REDACTED]"}], "count": 2})
        self.assertEqual(payload, before)

    def test_json_redaction_rejects_key_collision_without_partial_output(self):
        payload = {self.client._key: 1, "[REDACTED]": 2}
        with patch("sys.stdout", new=io.StringIO()) as stdout:
            with self.assertRaisesRegex(ValueError, "duplicate JSON keys"):
                api._output(payload, None)
        self.assertEqual(stdout.getvalue(), "")

    def test_non_json_response_is_rejected(self):
        reply = response({})
        reply.read.return_value = b"<html>not the API</html>"
        with patch.object(self.client._opener, "open", return_value=reply):
            with self.assertRaisesRegex(api.MidasAPIError, "not UTF-8 JSON"):
                self.client.request("GET", "/db/UNIT")

    def test_missing_environment_is_rejected_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(api.MidasAPIError, "environment"):
                api.MidasClient()

    def test_analysis_completion_is_separate_from_http_success(self):
        api.require_analysis_complete({"message": "MIDAS Civil NX Command Complete"})
        for payload in ({}, {"message": "queued"}, {"success": False},
                        {"message": "MIDAS Civil NX Command Complete", "result": {"error": "refused"}},
                        {"message": "MIDAS Civil NX Command Complete", "details": {"message": "Analysis not allowed"}}):
            with self.subTest(payload=payload):
                with self.assertRaises(api.MidasAPIError):
                    api.require_analysis_complete(payload)

    def test_invalid_header_credential_is_rejected_without_echo(self):
        with patch.dict(os.environ, {"MIDAS_API_KEY": "synthetic\ncredential"}):
            with self.assertRaises(api.MidasAPIError) as caught:
                api.MidasClient()
        self.assertNotIn("synthetic", str(caught.exception))


class PlanAndResultTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {"SECF": {"101": {"ITEMS": [
            {"ID": 1, "IYY_SF": 2.0, "WGT_SF": 1.0, "J5": 2.0}]}}}
        self.spec = {"girder": {"bounds": [0.5, 1.5], "targets": [
            {"table": "SECF", "id": "101", "path": ["ITEMS", 0, "IYY_SF"], "mode": "scale"}]}}

    def test_repeated_parameters_do_not_accumulate_or_change_mass(self):
        before = deepcopy(self.baseline)
        first = api.build_parameter_updates(self.baseline, self.spec, {"girder": 1.2})
        second = api.build_parameter_updates(self.baseline, self.spec, {"girder": 1.2})
        item = second["updates"]["SECF"]["Assign"]["101"]["ITEMS"][0]
        self.assertEqual(item["IYY_SF"], 2.4)
        self.assertEqual(item["WGT_SF"], 1.0)
        self.assertEqual(first, second)
        self.assertEqual(before, self.baseline)

    def test_invalid_or_undeclared_values_are_rejected(self):
        for values in ({}, {"girder": 2}, {"girder": float("nan")}, {"girder": True},
                       {"girder": 1, "unexpected": 1}):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    api.build_parameter_updates(self.baseline, self.spec, values)

    def test_missing_field_and_duplicate_targets_are_rejected(self):
        invalid = deepcopy(self.spec)
        invalid["girder"]["targets"][0]["path"][-1] = "invented_field"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            api.build_parameter_updates(self.baseline, invalid, {"girder": 1})
        duplicate = deepcopy(self.spec)
        duplicate["other"] = deepcopy(self.spec["girder"])
        with self.assertRaisesRegex(ValueError, "same field"):
            api.build_parameter_updates(self.baseline, duplicate, {"girder": 1, "other": 1})

    def test_set_mode_preserves_other_fields_and_nested_array_target(self):
        spec = deepcopy(self.spec)
        spec["girder"]["targets"][0]["mode"] = "set"
        result = api.build_parameter_updates(self.baseline, spec, {"girder": 1.1})
        item = result["updates"]["SECF"]["Assign"]["101"]["ITEMS"][0]
        self.assertEqual(item["IYY_SF"], 1.1)
        self.assertEqual(item["J5"], 2.0)

    def test_malformed_spec_has_explicit_error(self):
        for spec in ({"girder": []}, {"girder": {"bounds": [0, 2], "targets": [None]}}):
            with self.assertRaises(ValueError):
                api.build_parameter_updates(self.baseline, spec, {"girder": 1})

    def test_result_headers_preserve_index_and_reject_misalignment(self):
        result = api.table_rows({"HEAD": ["Index", "Node", "DZ"], "DATA": [["1", "27", "-4.06"]]})
        self.assertEqual(result[0]["Node"], "27")
        self.assertEqual(result[0]["DZ"], "-4.06")
        for block in ({"HEAD": ["Node", "Node"], "DATA": [[1, 2]]},
                      {"HEAD": ["Node", "DZ"], "DATA": [[1]]},
                      {"HEAD": ["Node"], "DATA": []}):
            with self.assertRaises(ValueError):
                api.table_rows(block)

    def test_plan_cli_runs_without_credentials_or_client_and_protects_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (("baseline", self.baseline), ("spec", self.spec), ("values", {"girder": 1})):
                (root / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            argv = ["plan"]
            for name in ("baseline", "spec", "values"):
                argv += ["--" + name, str(root / (name + ".json"))]
            target = root / "updates.json"
            argv += ["--output", str(target)]
            with patch.dict(os.environ, {}, clear=True), patch.object(api, "MidasClient", side_effect=AssertionError):
                self.assertEqual(api.main(argv), 0)
                before = target.read_bytes()
                with patch("sys.stderr", new=io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        api.main(argv)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
