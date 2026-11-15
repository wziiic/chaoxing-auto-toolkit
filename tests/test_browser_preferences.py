import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import browser_preferences as browsers
import lxt
from panel import app as panel


class BrowserPreferencesTests(unittest.TestCase):
    def test_default_uses_system_browser(self):
        with patch.object(browsers.webbrowser, 'open', return_value=True) as opened:
            browsers.open_panel('http://127.0.0.1:8765/', {'choice': 'system'})
        opened.assert_called_once_with('http://127.0.0.1:8765/')

    def test_edge_launch_preserves_url_and_spaces_without_shell(self):
        path = r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
        with patch.object(browsers, 'executable', return_value=path), patch.object(browsers.subprocess, 'Popen') as launch:
            browsers.open_panel('http://127.0.0.1:8765/?token=fixture&x=1', {'choice': 'edge'})
        self.assertEqual(launch.call_args.args[0], [path, 'http://127.0.0.1:8765/?token=fixture&x=1'])
        self.assertNotIn('shell', launch.call_args.kwargs)

    def test_custom_path_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / 'Other Browser.exe'
            program.touch()
            self.assertEqual(browsers.normalize({'choice': 'custom', 'custom_path': f'"{program}"'})['custom_path'], str(program))
            for path in ('relative.exe', str(program) + ' --flag', str(Path(directory) / 'missing.exe')):
                with self.assertRaises(ValueError):
                    browsers.normalize({'choice': 'custom', 'custom_path': path})

    def test_missing_selected_browser_is_rejected(self):
        with patch.object(browsers, 'executable', return_value=None):
            with self.assertRaises(ValueError):
                browsers.normalize({'choice': 'edge'})

    def test_saved_browser_survives_and_preserves_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'llm': {'api_key': 'fixture-secret'}}), encoding='utf-8')
            with patch.object(panel, 'CONFIG_PATH', str(config)), patch.object(browsers, 'CONFIG_PATH', config), patch.object(browsers, 'executable', return_value='fixture.exe'):
                panel.set_browser_settings(panel.BrowserReq(choice='edge'))
                self.assertEqual(browsers.read_preferences()['choice'], 'edge')
                self.assertEqual(panel.load_config()['llm']['api_key'], 'fixture-secret')
                self.assertEqual(panel.get_browser_settings()['preferences']['choice'], 'edge')
                with self.assertRaises(panel.HTTPException):
                    panel.set_browser_settings(panel.BrowserReq(choice='invalid'))
                self.assertEqual(browsers.read_preferences()['choice'], 'edge')

    def test_explicit_edge_is_used_by_course_browser(self):
        runtime = Mock()
        with patch.object(browsers, 'read_preferences', return_value={'choice': 'edge'}):
            lxt.launch_chromium(runtime, headless=True, standard_automation=True)
        runtime.chromium.launch.assert_called_once_with(headless=True, args=[], channel='msedge')

    def test_explicit_choice_does_not_silently_switch(self):
        runtime = Mock()
        runtime.chromium.launch.side_effect = RuntimeError('unavailable')
        with patch.object(browsers, 'read_preferences', return_value={'choice': 'chrome'}):
            with self.assertRaises(RuntimeError):
                lxt.launch_chromium(runtime)
        self.assertEqual(runtime.chromium.launch.call_count, 1)

    def test_other_panel_browser_keeps_course_fallback(self):
        with patch.object(browsers, 'read_preferences', return_value={'choice': 'custom'}):
            self.assertEqual(browsers.course_channels(), ('chrome', 'msedge', None))

    def test_direct_applies_only_to_course_automation(self):
        runtime = Mock()
        with patch.object(browsers, 'read_preferences', return_value={'choice': 'edge', 'course_network': 'direct'}):
            lxt.launch_chromium(runtime, standard_automation=True)
            self.assertEqual(runtime.chromium.launch.call_args.kwargs['proxy'], {'server': 'direct://'})
            lxt.launch_chromium(runtime, standard_automation=False)
            self.assertNotIn('proxy', runtime.chromium.launch.call_args.kwargs)

    def test_network_choice_validation_and_default(self):
        self.assertEqual(browsers.normalize({'choice': 'system'})['course_network'], 'system')
        self.assertEqual(browsers.normalize({'choice': 'system', 'course_network': 'direct'})['course_network'], 'direct')
        with self.assertRaises(ValueError):
            browsers.normalize({'choice': 'system', 'course_network': 'invalid'})


if __name__ == '__main__':
    unittest.main()
