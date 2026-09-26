import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy as np

from keyboard_ui import KeyboardUI, Cancel, clipped, keyboard
from yield_tui import Dashboard


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sample.csv'
        x = np.linspace(0, .03, 301)
        y = np.where(x <= .005, 200000*x, 1000+1000*(x-.005))
        np.savetxt(self.path, np.c_[x, y], delimiter=',', header='X,Y', comments='')
        self.app = Dashboard([self.path], str(Path(self.tmp.name)/'out'), False, preferences_path=None)

    def ui(self, keys):
        iterator = iter(keys)
        ui = KeyboardUI(self.app, lambda: next(iterator))
        ui.screen = lambda *args, **kwargs: None
        return ui

    def test_full_workflow_only_arrows_and_enter(self):
        keys = [
            'down', 'enter', 'enter', 'down', 'enter',
            'enter', 'down', 'enter', *(['down']*4), 'enter',
            'down', 'enter', 'enter', 'enter', 'right', 'right', 'enter',
            'down', 'enter', 'enter', *(['right']*6), 'enter',
            *(['down']*3), 'enter', 'enter',
            'down', 'down', 'enter', 'down', 'down', 'enter',
        ]
        self.ui(keys).loop()
        self.assertEqual(self.app.c.xmode, 'strain')
        self.assertEqual(self.app.c.yunit, 'MPa')
        self.assertEqual(self.app.c.fit_rows, (2, 32))
        self.assertEqual(self.app.c.yield_row, 62)
        files = list(Path(self.tmp.name).rglob('*.json'))
        self.assertEqual(len(files), 1)
        result = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertAlmostEqual(result['offset']['x'], 1395/199000, 12)

    def test_cancel_second_endpoint_preserves_fit(self):
        self.app.command('fit 4 40')
        with self.assertRaises(Cancel):
            self.ui(['enter', 'down', 'enter', 'right', 'escape']).fit()
        self.assertEqual(self.app.c.fit_rows, (4, 40))

    def test_invalid_fit_preserves_previous_value(self):
        self.app.command('fit 4 40')
        with self.assertRaises(ValueError):
            self.ui(['enter', 'end', 'enter', 'home', 'enter']).fit()
        self.assertEqual(self.app.c.fit_rows, (4, 40))

    def test_row_picker_uses_valid_rows(self):
        self.app.d.rows[1:] += 100
        self.assertEqual(self.ui(['down', 'enter']).pick_row('row'), 103)
        self.assertEqual(self.ui(['left', 'enter']).pick_row('row'), 2)

    def test_number_arrows_and_reject_invalid_input(self):
        self.assertEqual(self.ui(['right', 'up', 'enter']).number('n', 5, 1, 100, True), 15)
        self.assertEqual(self.ui(['0', 'enter', 'backspace', '2', '.', '5', 'enter',
                                 'backspace', 'backspace', 'enter']).number('n', 1, 1, 100, True), 2)

    def test_file_switch_retains_per_file_settings(self):
        second = Path(self.tmp.name)/'second.csv'
        second.write_bytes(self.path.read_bytes())
        self.app.paths.append(second)
        self.app.command('x strain')
        self.ui(['enter', 'down', 'enter']).source()
        self.assertEqual(self.app.index, 1)
        self.assertEqual(self.app.c.xmode, 'strain')
        self.app.command('x percent')
        self.ui(['enter', 'up', 'enter']).source()
        self.assertEqual(self.app.c.xmode, 'strain')

    def test_screen_fits_small_terminal(self):
        ui = KeyboardUI(self.app)
        output = io.StringIO()
        with patch('keyboard_ui.shutil.get_terminal_size', return_value=os.terminal_size((40, 15))), redirect_stdout(output):
            ui.screen('中文測試', ['資料'*100]*50)
        self.assertLess(len(output.getvalue().splitlines()), 15)
        self.assertEqual(clipped('中文abcd', 5), '中文a')

    def test_units_survive_restart_without_copying_analysis(self):
        prefs = Path(self.tmp.name)/'units.json'
        first = Dashboard([self.path], preferences_path=prefs)
        for command in ('x tensile 50', 'y custom 0.25 MPa', 'fit 4 40', 'zero 2'):
            first.command(command)
        restarted = Dashboard([self.path], preferences_path=prefs)
        self.assertEqual((restarted.c.xmode, restarted.c.xfactor), ('tensile', .02))
        self.assertEqual((restarted.c.yunit, restarted.c.yfactor), ('MPa', .25))
        self.assertIsNone(restarted.c.fit_rows)
        self.assertIsNone(restarted.c.zero_row)
        restarted.command('x unknown')
        self.assertEqual(Dashboard([self.path], preferences_path=prefs).c.xmode, 'unknown')

    def test_sheet_and_columns_inherit_units(self):
        self.app.command('x percent')
        self.app.command('y N')
        self.app.command('fit 4 40')
        self.app.command('sheet 1')
        self.assertEqual((self.app.c.xmode, self.app.c.xfactor), ('percent', .01))
        self.assertEqual(self.app.c.yunit, 'N')
        self.assertIsNone(self.app.c.fit_rows)
        self.app.command('cols 1 2')
        self.assertEqual(self.app.c.xmode, 'percent')

    def test_invalid_unit_memory_falls_back(self):
        prefs = Path(self.tmp.name)/'units.json'
        for content in ('invalid json', '{"xmode":"percent","xfactor":1,"yunit":"N","yfactor":1}',
                        '{"xmode":"custom","xfactor":-1,"yunit":"N","yfactor":1}'):
            prefs.write_text(content, encoding='utf-8')
            app = Dashboard([self.path], preferences_path=prefs)
            self.assertEqual(app.c.xmode, 'unknown')
            self.assertIn('重新選擇', app.message)

    def test_failed_save_keeps_units_in_session(self):
        prefs = Path(self.tmp.name)/'missing'/'units.json'
        app = Dashboard([self.path], preferences_path=prefs)
        app.command('x custom 0.001')
        self.assertEqual(app.unit_defaults['xfactor'], .001)
        self.assertIn('無法儲存', app.message)

    @unittest.skipUnless(os.name == 'nt', 'Windows key decoding')
    def test_windows_arrow_decoding(self):
        with patch('msvcrt.getwch', side_effect=['\xe0', 'H', '\xe0', 'P', '\x00', 'K', '\xe0', 'M', '\r']):
            with keyboard() as read:
                self.assertEqual([read() for _ in range(5)], ['up', 'down', 'left', 'right', 'enter'])


if __name__ == '__main__':
    unittest.main()
