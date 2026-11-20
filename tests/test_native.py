import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autorouter.native import command, main


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.package = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("autorouter.integrations.native_flags", return_value=[]))
        self.enterContext(patch("autorouter.learning.recommendations", return_value=[]))


    def test_native_arguments_are_unchanged(self):
        args = ['resume', '--last', '-C', '/tmp/path with spaces', '-m', 'custom']
        with patch("autorouter.native.code_mode_marker") as marker:
            marker.return_value.is_file.return_value = False
            result = command(args)
        self.assertTrue(result[0].endswith('/.local/lib/autorouter/codex'))
        self.assertEqual(result[1:4], ['--no-daemon', '--enable', 'step_model_switching'])
        self.assertEqual(result[4:], args)

    def test_exec_replaces_process_and_preserves_manual_override(self):
        with patch('sys.argv', ['autorouter', '-m', 'custom']), patch('pathlib.Path.is_file', side_effect=[True, False, False]), patch('os.execv') as execute, patch('autorouter.native.package', return_value=self.package), patch.dict('os.environ', {}, clear=True):
            main()
            import os
            self.assertEqual(os.environ['AUTOROUTER_ENABLED'], '1')
            self.assertEqual(os.environ['AUTOROUTER_PIN_MODEL'], '1')
            self.assertEqual(execute.call_args.args[1][-2:], ['-m', 'custom'])

    def test_spaced_and_attached_config_overrides_are_pinned(self):
        import os
        for args in [['-c', 'model = "custom"', '--config=model_reasoning_effort = "high"'],
                     ['-cmodel=custom', '-cmodel_reasoning_effort=high']]:
            with self.subTest(args=args), patch('sys.argv', ['autorouter', *args]), patch('pathlib.Path.is_file', side_effect=[True, False, False]), patch('os.execv'), patch('autorouter.native.package', return_value=self.package), patch.dict('os.environ', {}, clear=True):
                main()
                self.assertEqual(os.environ['AUTOROUTER_PIN_MODEL'], '1')
                self.assertEqual(os.environ['AUTOROUTER_PIN_EFFORT'], '1')

    def test_code_mode_uses_the_native_tool_registry(self):
        with patch("autorouter.native.code_mode_marker") as marker:
            marker.return_value.is_file.return_value = True
            self.assertEqual(command(["resume"])[4:], [
                "--enable", "code_mode", "--enable", "code_mode_tool_search", "resume"
            ])
