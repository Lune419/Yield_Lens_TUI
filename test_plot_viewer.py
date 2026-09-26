import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from plot_viewer import build_figure, open_preview, write_preview
from yield_tui import Dashboard, analyze


class PlotViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'sample.csv'
        x = np.linspace(0, .03, 301)
        y = np.where(x <= .005, 200000*x, 1000+1000*(x-.005))
        np.savetxt(self.path, np.c_[x, y], delimiter=',', header='X,Y', comments='')
        self.app = Dashboard([self.path], str(Path(self.tmp.name)/'out'), preferences_path=None)
        for command in ('x strain', 'y MPa', 'fit 4 40', 'yield 60'):
            self.app.command(command)

    def test_chart_preserves_samples_and_analysis(self):
        figure, result = build_figure(self.app.d, self.app.c)
        self.assertEqual(len(figure.data[0].x), 301)
        self.assertEqual(list(figure.data[0].customdata), list(self.app.d.rows))
        self.assertEqual(result, analyze(self.app.d, self.app.c))
        self.assertEqual(len(figure.data), 6)
        self.assertEqual(figure.layout.dragmode, 'zoom')
        self.assertFalse(figure.layout.yaxis.fixedrange)
        self.assertLess(figure.layout.yaxis.range[1], 1200)

    def test_invalid_analysis_still_shows_raw_curve(self):
        self.app.c.fit_rows = (4, 5)
        figure, result = build_figure(self.app.d, self.app.c)
        self.assertEqual(len(figure.data), 1)
        self.assertTrue(result['warnings'])

    def test_html_is_offline_and_escapes_source_labels(self):
        self.app.d.sheet = '<script>alert(1)</script>'
        target = Path(self.tmp.name)/'chart.html'
        write_preview(self.app.d, self.app.c, target)
        content = target.read_text(encoding='utf-8')
        self.assertIn('plotly.js', content)
        self.assertIn('scrollZoom', content)
        self.assertIn('Plotly.downloadImage', content)
        self.assertNotIn('<script src=', content)
        self.assertNotIn('<script>alert(1)</script>', content)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', content)

    def test_command_opens_file_uri_and_reports_fallback(self):
        with patch('plot_viewer.webbrowser.open', return_value=False) as launch:
            self.app.command('plot')
        launch.assert_called_once()
        self.assertTrue(launch.call_args.args[0].startswith('file:///'))
        self.assertEqual(launch.call_args.kwargs['new'], 1)
        self.assertIn('手動', self.app.message)
        self.assertEqual(len(list(Path(self.tmp.name).rglob('*.html'))), 1)


if __name__ == '__main__':
    unittest.main()
