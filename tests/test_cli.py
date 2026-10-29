"""Smoke tests for the concord entrypoints."""

import contextlib
import io
import subprocess
import sys
import unittest

from concord.cli import build_parser, main


class CliTest(unittest.TestCase):
    def test_scores_base_uses_normalized_snapshots(self):
        from pathlib import Path
        from unittest import mock

        snapshot = {'functions': {}, 'data': [], 'data_format': 'matched-ranges-v1'}
        with mock.patch('concord.cli.scores.normalized', return_value=(snapshot, dict(snapshot), {})) as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['scores', '--harvest', '/root', '--base', 'base']), 0)
            run.assert_called_once_with(Path('/root'), 'base')
            self.assertIn('0 worse', output.getvalue())
            self.assertNotIn('configuration:', output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['scores', '--harvest', '/root', '--base', 'base', '--against', 'old.json'])

    def test_scores_base_fails_when_configuration_lowers_a_function(self):
        from unittest import mock

        source = {
            'functions': {'256': {'name': 'f', 'fuzzy': 98.75, 'matched': False}},
            'data': [[4096, 5]], 'data_format': 'matched-ranges-v1',
        }
        configuration_baseline = {
            'functions': {'256': {'name': 'f', 'fuzzy': 100.0, 'matched': True}},
            'data': [[4096, 5]], 'data_format': 'matched-ranges-v1',
        }
        with mock.patch(
            'concord.cli.scores.normalized',
            return_value=(source, source, {'configuration_baseline': configuration_baseline}),
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(['scores', '--harvest', '/root', '--base', 'base'])

        self.assertEqual(result, 1)
        self.assertIn('0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0', output.getvalue())
        self.assertIn('configuration: 1 functions changed: 0 newly matched, 1 worse; data bytes matched: +0 -0', output.getvalue())

    def test_symbols_check_reports_suspects_and_returns_failure(self):
        from unittest import mock

        report = {'findings': [{'status': 'suspect', 'address': 16, 'symbol': 'f', 'reasons': ['return mismatch']}]}
        with mock.patch('concord.cli.symbol_audit.run', return_value=report) as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['symbols-check', '--harvest', '/root', '--evidence', 'manual']), 1)
            self.assertIn('return mismatch', output.getvalue())
            self.assertEqual(run.call_args.args[1], ['manual'])

    def test_causal_census_routes_selection_and_output(self):
        from pathlib import Path
        from unittest import mock

        report = {'constructs': [], 'analyzed_functions': 0, 'selected_functions': 0, 'failures': []}
        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.census.run', return_value=report) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['causal-census', '--harvest', '/root', '--maximum', '20', '--save', '/tmp/result.json']), 0)
            run.assert_called_once_with(harvest.return_value, Path('/tmp/result.json'), 97, 20, None)

    def test_causal_trial_can_measure_a_registered_authored_header(self):
        from pathlib import Path
        from unittest import mock

        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.census.counterfactual', return_value={'candidates': []}) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['causal-trial', '--harvest', '/root', '--header', 'src/h.h', '--authored', 'u.cpp', 'symbol', '--save', '/tmp/trial.json']), 0)
            run.assert_called_once_with(harvest.return_value, 'src/h.h', None, Path('/tmp/trial.json'), None, authored=('u.cpp', 'symbol'), variant=None)
            run.reset_mock()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['causal-trial', '--harvest', '/root', '--header', 'src/h.h', '--authored', 'u.cpp', 'symbol', '--variant', 'pointer-source', '--save', '/tmp/trial.json']), 0)
            self.assertEqual(run.call_args.kwargs['variant'], 'pointer-source')

    def test_causal_reuses_dumps_and_saves_structured_findings(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        report = {'schema': 'concord.causal.v1', 'unit': 'u.cpp', 'symbol': 'f', 'findings': []}
        with tempfile.TemporaryDirectory() as tmp:
            save = Path(tmp) / 'findings.json'
            with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.causal.analyze', return_value=report) as analyze, mock.patch('concord.cli.causal.collect') as collect:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['causal', '--harvest', tmp, '--unit', 'u.cpp', 'f', '--dumps', tmp, '--save', str(save)]), 0)
                collect.assert_not_called()
                analyze.assert_called_once_with(harvest.return_value, 'u.cpp', 'f', Path(tmp), limit=8, offsets=None)
                self.assertEqual(json.loads(save.read_text()), report)

    def test_causal_rejects_invalid_search_before_collecting_dumps(self):
        from unittest import mock

        with mock.patch('concord.cli.causal.collect') as collect:
            with self.assertRaisesRegex(ValueError, '--apply requires --search'):
                main(['causal', '--harvest', '/root', '--unit', 'u.cpp', 'f', '--apply'])
            with self.assertRaisesRegex(ValueError, 'budget must be positive'):
                main(['causal', '--harvest', '/root', '--unit', 'u.cpp', 'f', '--search', '--budget', '0'])
            collect.assert_not_called()

    def test_causal_search_uses_the_existing_report_and_author_option(self):
        from unittest import mock

        report = {'schema': 'concord.causal.v1', 'unit': 'u.cpp', 'symbol': 'f', 'findings': []}
        searched = {'candidates': [], 'chosen': None, 'applied': False}
        with (mock.patch('concord.cli.Harvest') as harvest,
              mock.patch('concord.cli.causal.analyze', return_value=report),
              mock.patch('concord.cli.causal.search', return_value=searched) as search):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['causal', '--harvest', '/root', '--unit', 'u.cpp', 'f', '--dumps', '/dumps', '--search', '--budget', '12', '--apply']), 0)
            search.assert_called_once_with(harvest.return_value, report, budget=12, apply=True)
            self.assertIn('0 overlays', output.getvalue())

    def test_refresh_requires_explicit_methods_and_preserves_other_bodies(self):
        from unittest import mock

        from concord.blockers import Stub

        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.blockers.stubs', return_value=[Stub('u.cpp', 'A::f')]) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['refresh', '--harvest', '/root', '--unit', 'u.cpp', 'A::f', '--apply']), 0)
            run.assert_called_once_with(harvest.return_value, ['u.cpp'], apply=True, only={'A::f'}, refresh=True, companion_first=False)

    def test_refresh_header_compiles_the_selected_including_unit(self):
        from unittest import mock

        from concord.blockers import Stub
        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.blockers.refresh_header', return_value=[Stub('u.cpp', 'A')]) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['refresh', '--harvest', '/root', '--unit', 'u.cpp', '--header', 'A.h', 'A', '--apply']), 0)
            run.assert_called_once_with(harvest.return_value, 'u.cpp', 'A.h', {'A'}, apply=True)

    def test_blockers_passes_selected_methods(self):
        from unittest import mock

        with mock.patch('concord.cli.Harvest') as harvest, mock.patch('concord.cli.blockers.stubs', return_value=[]) as stubs:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['blockers', '--harvest', '/tmp/root', 'a.cpp', '--apply', '--only', 'A::f', '--only', 'A::g']), 0)
            stubs.assert_called_once_with(harvest.return_value, ['a.cpp'], apply=True, only={'A::f', 'A::g'})

    def test_destructors_reports_overlays_without_an_apply_flag(self):
        from unittest import mock

        report = {"variants": {"baseline": {"exact": 3, "worse": {}, "error": "", "destructor_sizes": {}}}}
        with mock.patch("concord.cli.Harvest") as harvest, mock.patch("concord.cli.destructors.diagnose", return_value=report) as diagnose:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["destructors", "--harvest", "/tmp/root", "--unit", "a.cpp", "--header", "a.h", "--class", "A"]), 0)
            diagnose.assert_called_once_with(harvest.return_value, "a.cpp", "a.h", "A")
            self.assertIn("3 exact, 0 worse", output.getvalue())

    def test_explicit_permute_target_respects_skip_logged(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "runs.jsonl"
            log.write_text(json.dumps({"command": "permute", "unit": "u.cpp", "symbol": "symbol", "budget": 128, "batch": 32, "depth": 3, "seed": 0}) + "\n")
            with mock.patch("concord.cli.Harvest", return_value=SimpleNamespace(source=lambda unit: unit)), mock.patch("concord.cli._permute_one") as run:
                self.assertEqual(main(["permute", "--harvest", tmp, "--unit", "u.cpp", "symbol", "--budget", "128", "--skip-logged", "--results", str(log)]), 0)
            run.assert_not_called()

    def test_near_miss_sweep_skips_a_function_whose_baseline_the_oracle_cannot_check(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        from concord import oracle

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "runs.jsonl"
            targets = [("a.cpp", "slow", 99.5), ("b.cpp", "fine", 99.4)]
            stuck = oracle.OracleBaselineError("oracle failed on the baseline for slow: oracle check timed out after 60 seconds")
            with mock.patch("concord.cli.Harvest", return_value=SimpleNamespace(source=lambda unit: unit, root=Path(tmp))), \
                    mock.patch("concord.cli.near_misses", return_value=targets), \
                    mock.patch("concord.cli.results.commit", return_value="abc"), \
                    mock.patch("concord.cli._permute_one", side_effect=[stuck, False]) as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["permute", "--harvest", tmp, "--near-miss", "99", "--results", str(log)]), 0)
            self.assertEqual(run.call_count, 2)
            self.assertIn("skipped slow", output.getvalue())
            record = json.loads(log.read_text().splitlines()[0])
            self.assertEqual((record["symbol"], record["reason"]), ("slow", "oracle-baseline"))

    def test_near_miss_sweep_keeps_only_the_named_unit(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            targets = [("a.cpp", "one", 99.5), ("b.cpp", "two", 99.4), ("a.cpp", "three", 99.1)]
            with mock.patch("concord.cli.Harvest", return_value=SimpleNamespace(source=lambda unit: unit, root=Path(tmp))), \
                    mock.patch("concord.cli.near_misses", return_value=targets), \
                    mock.patch("concord.cli._permute_one", return_value=False) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["permute", "--harvest", tmp, "--near-miss", "99", "--unit", "a.cpp"]), 0)
            self.assertEqual([call.args[1:3] for call in run.call_args_list], [("a.cpp", "one"), ("a.cpp", "three")])

    def test_permute_keeps_no_inexact_candidate_with_a_nan_sense_step(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock

        def diff(score, exact=False):
            return SimpleNamespace(exact=exact, fuzzy=score, score=score, findings=[])

        with tempfile.TemporaryDirectory() as tmp:
            unit = Path(tmp) / "src" / "a.cpp"
            unit.parent.mkdir()
            unit.write_bytes(b"while (d > 0) {}")
            found = SimpleNamespace(
                baseline=diff(95.0), best=diff(95.8), source=b"while (!(d <= 0)) {}",
                steps=["nan-sense line 1: d > 0 as !(d <= 0)"], reason="budget spent", tried=8,
            )
            with mock.patch("concord.cli.Harvest", return_value=SimpleNamespace(source=lambda unit: unit, root=Path(tmp))), \
                    mock.patch("concord.cli.permute", return_value=found), \
                    mock.patch("concord.cli.repair", return_value=(found.source, [])), \
                    mock.patch("concord.cli.finish", return_value=found.source), \
                    mock.patch("concord.cli.results.commit", return_value="abc"), \
                    mock.patch("concord.cli.results.save_patch", return_value=None), \
                    mock.patch("concord.cli.oracle.gate") as gate, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = main(["permute", "--harvest", tmp, "--unit", "a.cpp", "f", "--apply-improved",
                             "--results", str(Path(tmp) / "runs.jsonl")])
            self.assertEqual(code, 0)
            gate.assert_not_called()
            self.assertIn("unproven", output.getvalue())
            self.assertEqual(unit.read_bytes(), b"while (d > 0) {}")

    def test_rename_gives_helpers_plain_names(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = root / "src" / "d" / "A.cpp"
            unit.parent.mkdir(parents=True)
            unit.write_text("inline int _concord_helper(int n)\n{\n\treturn n;\n}\nint f() { return _concord_helper(1); }\n")
            self.assertEqual(main(["rename", "--harvest", tmp, "d/A.cpp"]), 0)
            text = unit.read_text()
            self.assertIn("inline int helper(int n)", text)
            self.assertIn("return helper(1);", text)
            self.assertNotIn("_concord_", text)

    def test_no_args_prints_help_and_succeeds(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertIn("Matching recompilation", out.getvalue())

    def test_place_bss_runs_the_sweep_and_saves_placements(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        placements = [{"unit": "daisy/A.cpp", "address": 0x2000}]
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / "placements.json"
            with mock.patch("concord.cli.Harvest") as harvest, mock.patch(
                "concord.cli.place.place_bss", return_value=placements
            ) as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["place-bss", "--harvest", directory, "--save", str(save)]), 0
                )
            run.assert_called_once_with(harvest.return_value)
            self.assertEqual(json.loads(save.read_text()), placements)

    def test_parser_lists_the_commands(self):
        help_text = build_parser().format_help()
        for command in (
            "seed", "match", "permute", "diff", "phase", "scores", "bench", "unguard", "comparison-flips",
            "enum-switch", "residual",
        ):
            self.assertIn(command, help_text)

    def test_enum_switch_passes_filters_and_apply_to_the_capability(self):
        from unittest import mock

        with mock.patch("concord.cli.Harvest") as harvest, mock.patch("concord.cli.enumswitch.run", return_value=[]) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main([
                        "enum-switch", "--harvest", "/root", "--unit", "u.cpp",
                        "--function", "symbol", "--apply",
                    ]),
                    0,
                )
            run.assert_called_once_with(harvest.return_value, unit="u.cpp", symbol="symbol", write=True)

    def test_residual_passes_the_harvest_and_saves_a_report(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        report = {
            "summary": {
                "functions": 0,
                "unit_occurrences": 0,
                "bytes": 0,
                "groups": {
                    "no-source-change": {"functions": 0, "bytes": 0},
                    "source-pointing": {"functions": 0, "bytes": 0},
                    "other": {"functions": 0, "bytes": 0},
                },
                "kinds": {},
            },
            "top_source_pointing": [],
            "value_leads": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / "residual.json"
            with mock.patch("concord.cli.Harvest") as harvest, mock.patch(
                "concord.cli.residual.report", return_value=report
            ) as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["residual", "--harvest", directory, "--save", str(save)]), 0)
            run.assert_called_once_with(harvest.return_value)
            self.assertEqual(json.loads(save.read_text()), report)

    def test_residual_prints_progress_reconciliation_and_missing_functions(self):
        from unittest import mock

        report = {
            "summary": {
                "functions": 0,
                "unit_occurrences": 0,
                "bytes": 0,
                "inexact_code_bytes": 4,
                "residual_analyzed_functions": 0,
                "residual_analyzed_bytes": 0,
                "groups": {
                    "no-source-change": {"functions": 0, "bytes": 0},
                    "source-pointing": {"functions": 0, "bytes": 0},
                    "other": {"functions": 0, "bytes": 0},
                },
                "kinds": {},
            },
            "top_source_pointing": [],
            "value_leads": [],
            "progress_reconciliation": {
                "summary": {
                    "progress_matched_functions": 1,
                    "progress_functions": 2,
                    "progress_inexact_functions": 1,
                    "progress_inexact_function_bytes": 4,
                    "unclaimed_code_bytes": 0,
                    "inexact_code_bytes": 4,
                    "residual_progress_inexact_functions": 0,
                    "residual_progress_inexact_bytes": 0,
                    "residual_progress_exact_functions": 0,
                    "residual_progress_exact_bytes": 0,
                    "missing_inexact_functions": 1,
                    "missing_inexact_function_bytes": 4,
                    "reconciled_code_bytes": 4,
                    "untracked_residual_functions": 0,
                    "untracked_residual_bytes": 0,
                },
                "groups": {
                    "not-compiled": {
                        "functions": [
                            {
                                "address": "0x1000",
                                "symbol": "missing_symbol",
                                "size": 4,
                                "evidence": ["absent from base objects"],
                                "candidate_units": [],
                            }
                        ],
                        "bytes": 4,
                        "why": "The target symbol is absent from every compiled base object.",
                        "recommendation": "Restore the source definition.",
                    }
                },
                "exact_overlaps": [],
            },
        }
        output = io.StringIO()
        with mock.patch("concord.cli.Harvest"), mock.patch(
            "concord.cli.residual.report", return_value=report
        ), contextlib.redirect_stdout(output):
            self.assertEqual(main(["residual", "--harvest", "/root"]), 0)

        self.assertIn("Progress reconciliation:", output.getvalue())
        self.assertIn("0 aligned + 4 missing + 0 unclaimed = 4 bytes", output.getvalue())
        self.assertIn("0x1000 missing_symbol size 4 bytes", output.getvalue())

    def test_module_entrypoint_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "concord", "--version"], capture_output=True, text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("concord", result.stdout)


if __name__ == "__main__":
    unittest.main()
