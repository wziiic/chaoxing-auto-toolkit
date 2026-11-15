from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import subprocess
from windows_process import background_options
import bootstrap
from tools import make_shortcut


class HiddenHelperTests(unittest.TestCase):
    def test_windows_options_prevent_console_creation_and_hide_startup(self):
        if os.name!='nt':self.skipTest('Windows only')
        options=background_options()
        self.assertEqual(options['creationflags'],subprocess.CREATE_NO_WINDOW)
        self.assertTrue(options['startupinfo'].dwFlags & subprocess.STARTF_USESHOWWINDOW)
        self.assertEqual(options['startupinfo'].wShowWindow,subprocess.SW_HIDE)

    def test_non_windows_options_are_empty(self):
        with patch('windows_process.os.name','posix'):
            self.assertEqual(background_options(),{})

    def test_desktop_query_stays_hidden_and_preserves_output(self):
        with patch.object(make_shortcut.subprocess,'run',return_value=Mock(stdout='C:\\Desktop\n')) as run:
            self.assertEqual(make_shortcut.desktop_dir(),'C:\\Desktop')
        self.assertIn('Hidden',run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
        self.assertEqual(run.call_args.kwargs['startupinfo'].wShowWindow,subprocess.SW_HIDE)

    def test_bootstrap_panel_child_does_not_create_another_console(self):
        with patch.object(bootstrap.subprocess,'call',return_value=17) as launch:
            self.assertEqual(bootstrap.launch_panel(),17)
        self.assertEqual(launch.call_args.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
        self.assertEqual(launch.call_args.kwargs['startupinfo'].wShowWindow,subprocess.SW_HIDE)

    def test_panel_job_keeps_return_code_and_both_output_streams(self):
        from panel import app
        with patch.object(app.subprocess,'run',return_value=Mock(returncode=7,stdout='output',stderr='error')) as run:
            self.assertEqual(app.run_script(['fixture.py'],timeout=12),{'code':7,'out':'outputerror'})
        self.assertEqual(run.call_args.kwargs['timeout'],12)
        self.assertEqual(run.call_args.kwargs['startupinfo'].wShowWindow,subprocess.SW_HIDE)


if __name__=='__main__':unittest.main()
