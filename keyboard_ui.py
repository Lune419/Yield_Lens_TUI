"""Arrow-key interface; no additional dependencies, Windows and POSIX terminals."""
from contextlib import contextmanager
import math
import os
import shutil
import sys
import unicodedata

import numpy as np

from yield_tui import analyze, transformed, terminal_chart, fmt, row_index


@contextmanager
def keyboard():
    if os.name == 'nt':
        import msvcrt
        def read():
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                return {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right',
                        'G': 'home', 'O': 'end'}.get(msvcrt.getwch(), '')
            if ch == '\x03':
                raise KeyboardInterrupt
            return {'\r': 'enter', '\x1b': 'escape', '\x08': 'backspace'}.get(ch, ch)
        yield read
    else:
        import termios
        import tty
        import select
        fd = sys.stdin.fileno()
        previous = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            # Use unbuffered reads consistently for escape sequences.
            def raw_read():
                ch = os.read(fd, 1).decode('ascii', errors='ignore')
                if not ch:
                    raise EOFError
                if ch == '\x1b':
                    sequence = ''
                    while select.select([fd], [], [], .04)[0]:
                        sequence += os.read(fd, 1).decode('ascii', errors='ignore')
                        if sequence[-1:] in 'ABCDHF~':
                            break
                    return {'[A': 'up', '[B': 'down', '[C': 'right', '[D': 'left',
                            'OA': 'up', 'OB': 'down', 'OC': 'right', 'OD': 'left',
                            '[H': 'home', '[F': 'end'}.get(sequence, 'escape')
                return {'\r': 'enter', '\n': 'enter', '\x7f': 'backspace'}.get(ch, ch)
            yield raw_read
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def clipped(text, width):
    result, used = '', 0
    for ch in str(text).replace('\t', '    '):
        if unicodedata.category(ch).startswith('C'):
            continue
        size = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
        if used + size > width:
            break
        result += ch
        used += size
    return result


class Cancel(Exception):
    pass


class KeyboardUI:
    def __init__(self, app, read=None):
        self.app = app
        self.read = read

    def screen(self, title, lines, footer='↑↓ 選擇  Enter 確認  ← 返回'):
        width, height = shutil.get_terminal_size((110, 42))
        content = [f'YIELD LENS  |  {self.app.d.path.name}', title, '']
        content += list(lines)[:max(1, height - 6)]
        content += ['', footer]
        sys.stdout.write('\033[H\033[2J' + '\n'.join(clipped(s, max(1, width-1)) for s in content))
        sys.stdout.flush()

    def menu(self, title, options, selected=0):
        while True:
            height = shutil.get_terminal_size((110, 42)).lines
            count = max(1, height - 7)
            start = max(0, min(selected-count//2, len(options)-count))
            self.screen(title, [f'{">" if i == selected else " "} {i+1}. {options[i]}'
                               for i in range(start, min(len(options), start+count))])
            key = self.read()
            if key == 'up': selected = (selected-1) % len(options)
            elif key == 'down': selected = (selected+1) % len(options)
            elif key in ('enter', 'right'): return selected
            elif key in ('escape', 'left'): raise Cancel

    def notice(self, title, text):
        lines = str(text).splitlines()
        offset = 0
        while True:
            self.screen(title, lines[offset:], '↑↓ 捲動  Enter / ← 返回')
            key = self.read()
            if key == 'down': offset = min(max(0, len(lines)-1), offset+1)
            elif key == 'up': offset = max(0, offset-1)
            elif key in ('enter', 'left', 'escape'): return

    def number(self, title, value, minimum=0, maximum=1e12, integer=False):
        step = 1 if integer else 10 ** (math.floor(math.log10(abs(value))) - 1) if value else .01
        buffer = ''
        error = ''
        while True:
            self.screen(title, [f'數值：{buffer or fmt(value)}', f'調整步長：{step:g}',
                               '↑↓ 增減數值；←→ 縮小／放大步長', '也可直接輸入數字；尚未確認的修改不會套用。', error],
                        'Enter 確認  Esc 取消')
            key = self.read()
            if key in ('escape',): raise Cancel
            if key == 'enter':
                try: candidate = float(buffer) if buffer else value
                except ValueError:
                    error = '請輸入有效數字。'
                    continue
                if math.isfinite(candidate) and minimum <= candidate <= maximum and (not integer or float(candidate).is_integer()):
                    return int(candidate) if integer else candidate
                error = f'請輸入 {minimum:g} 至 {maximum:g} 的'+('整數。' if integer else '有限數值。')
            elif key in ('up', 'down'):
                buffer = ''
                value = min(maximum, max(minimum, round(value + step*(1 if key == 'up' else -1), 12)))
            elif key == 'left': step = max(1 if integer else 1e-12, step/10)
            elif key == 'right': step = min(1e12, step*10)
            elif key == 'backspace': buffer = buffer[:-1]
            elif key in '0123456789.eE+-' and key: buffer += key

    def pick_row(self, title, initial=None):
        d, c = self.app.d, self.app.c
        index = row_index(d, initial) if initial is not None else 0
        x, y = transformed(d, c)
        try: base_result = analyze(d, c)
        except ValueError: base_result = {}
        while True:
            result = dict(base_result, manual_yield={'x': x[index], 'y': y[index]})
            width, height = shutil.get_terminal_size((110, 42))
            chart = terminal_chart(d, c, result, max(8, min(width-15, 100)), max(3, min(height-13, 16)), None, False)
            self.screen(title, [f'原始列 {d.rows[index]}  ({index+1}/{len(d.rows)})',
                               f'X={fmt(x[index])}  Y={fmt(y[index])}  圖上 Y = 目前游標', *chart.splitlines()],
                        '↑↓ 移動 1 筆  ←→ 移動 10 筆  Enter 選取  Esc 取消')
            key = self.read()
            if key == 'enter': return int(d.rows[index])
            if key == 'escape': raise Cancel
            if key == 'home': index = 0
            elif key == 'end': index = len(d.rows)-1
            else: index = max(0, min(len(d.rows)-1, index + {'up': -1, 'down': 1, 'left': -10, 'right': 10}.get(key, 0)))

    def pair(self, title, initial):
        first = self.pick_row(title+'：起列', initial[0])
        last = self.pick_row(title+'：末列', initial[1])
        return first, last

    def execute(self, command):
        self.app.command(command)

    def units(self):
        axis = self.menu('單位設定', ['X 單位／轉換', 'Y 單位／轉換'])
        if axis == 0:
            modes = ['unknown', 'strain', 'percent', 'micro', 'tensile', 'custom']
            choice = self.menu('X 原始資料單位', ['未知', '應變 mm/mm', '百分比 %', '微應變', '拉伸位移 mm（輸入標距）', '自訂係數'])
            mode = modes[choice]
            command = 'x '+mode
            if mode in ('tensile', 'custom'):
                value = self.number('標距 mm' if mode == 'tensile' else 'X 轉換係數', 50 if mode == 'tensile' else self.app.c.xfactor, 1e-12)
                command += f' {value}'
        else:
            units = ['unknown', 'N', 'kN', 'kgf', 'MPa']
            choice = self.menu('Y 單位', units + ['自訂換算係數'])
            if choice == 5:
                unit = units[self.menu('換算後單位', units)]
                value = self.number('Y 轉換係數', self.app.c.yfactor, 1e-12)
                command = f'y custom {value} {unit}'
            else: command = 'y '+units[choice]
        self.execute(command)

    def fit(self):
        choice = self.menu('線性擬合', ['從圖上選取起列／末列', '預覽 10–30% 建議區間', '清除擬合'])
        if choice == 2:
            self.execute('fit off'); return
        d, c = self.app.d, self.app.c
        initial = c.fit_rows or (int(d.rows[0]), int(d.rows[min(10, len(d.rows)-1)]))
        if choice == 1:
            _, y = transformed(d, c)
            start = row_index(d, c.start_row) if c.start_row else 0
            end = row_index(d, c.end_row) if c.end_row else start+int(np.argmax(y[start:]))
            segment = y[start:end+1]
            initial = tuple(int(d.rows[start+np.flatnonzero(segment >= segment[0]+p*(segment.max()-segment[0]))[0]]) for p in (.1, .3))
            self.notice('建議僅供起點參考', f'建議原始列：{initial[0]}–{initial[1]}\n接下來可調整兩端；確認此區確實呈直線後才套用。')
        a, b = self.pair('線性擬合', initial)
        self.execute(f'fit {a} {b}')

    def source(self):
        choice = self.menu('資料來源', ['切換檔案', '切換工作表（重設分析設定）', '選擇 X/Y 欄（重設分析設定）'])
        if choice == 0:
            index = self.menu('檔案', [str(p) for p in self.app.paths], self.app.index)
            self.app.load(index)
        elif choice == 1:
            path = self.app.d.path
            if path.suffix.lower() == '.xls':
                import xlrd
                book = xlrd.open_workbook(str(path), on_demand=True)
                try: names = book.sheet_names()
                finally: book.release_resources()
            elif path.suffix.lower() == '.xlsx':
                import openpyxl
                book = openpyxl.load_workbook(path, read_only=True)
                try: names = book.sheetnames
                finally: book.close()
            else:
                self.notice('工作表', 'CSV / TSV 只有一張工作表。'); return
            self.execute(f'sheet {self.menu("工作表", names)+1}')
        else:
            a = self.number('X 欄號（從 1 開始）', self.app.d.columns[0], 1, integer=True)
            b = self.number('Y 欄號（從 1 開始）', self.app.d.columns[1], 1, integer=True)
            self.execute(f'cols {a} {b}')

    def settings(self):
        c, d = self.app.c, self.app.d
        choice = self.menu('分析設定', ['X/Y 方向', '歸零', '分析區間', '偏離候選點門檻'])
        if choice == 0:
            pairs = [(1, 1), (-1, 1), (1, -1), (-1, -1)]
            a, b = pairs[self.menu('方向', ['X + / Y +', 'X − / Y +', 'X + / Y −', 'X − / Y −'], pairs.index((c.sx, c.sy)))]
            self.execute(f'sign {a} {b}')
        elif choice == 1:
            if self.menu('歸零', ['選取歸零點', '取消歸零']) == 1: self.execute('zero off')
            else: self.execute(f'zero {self.pick_row("歸零點", c.zero_row)}')
        elif choice == 2:
            if self.menu('分析區間', ['手動選取', '自動（至第一個最大值）']) == 1: self.execute('branch auto')
            else:
                a, b = self.pair('分析區間', (c.start_row or int(d.rows[0]), c.end_row or int(d.rows[-1])))
                self.execute(f'branch {a} {b}')
        else:
            tol = self.number('偏離比例（0–1）', c.tolerance, 1e-12, .999999)
            count = self.number('連續點數', c.consecutive, 1, len(d.rows), True)
            self.execute(f'tol {tol} {count}')

    def view(self):
        choice = self.menu('圖表／資料', ['開啟互動圖表視窗（框選放大／平移）', '選取終端縮放範圍', '恢復終端全圖', '瀏覽原始資料', '查看終端圖表'])
        if choice == 0:
            self.execute('plot')
            self.notice('互動圖表', self.app.message+'\n在瀏覽器使用「框選放大」、滾輪與「拖曳平移」。\n更新 TUI 設定後，再開啟一次即可取得最新圖表。')
            return
        if choice == 1:
            a, b = self.pair('縮放', (int(self.app.d.rows[0]), int(self.app.d.rows[-1])))
            x, _ = transformed(self.app.d, self.app.c)
            left, right = sorted((x[row_index(self.app.d, a)], x[row_index(self.app.d, b)]))
            self.execute(f'view {left} {right}')
        elif choice == 2: self.execute('view all')
        elif choice == 3:
            row = self.pick_row('瀏覽資料')
            self.execute(f'rows {row} {row+10}')
            self.notice('原始／轉換資料', self.app.message)
        if choice != 3:
            try: result = analyze(self.app.d, self.app.c)
            except ValueError as exc:
                self.notice('設定尚未完成', str(exc)); return
            width, height = shutil.get_terminal_size((110, 42))
            chart = terminal_chart(self.app.d, self.app.c, result, max(8, min(width-15, 100)), max(3, min(height-15, 18)), self.app.view, False)
            lines = [chart]
            if result.get('fit'):
                f = result['fit']; lines.append(f'斜率={fmt(f["slope"])}  R²={f["r_squared"]:.6f}  RMSE={fmt(f["rmse"])}')
            for name in ('offset', 'candidate', 'manual_yield'):
                point = result.get(name)
                lines.append(f'{name}: '+(f'X={fmt(point["x"])} Y={fmt(point["y"])}' if point else '未取得'))
            lines += result['warnings']
            self.notice('分析圖表（O offset / C 偏離候選 / Y 手動降伏）', '\n'.join(lines))

    def loop(self):
        selected = 0
        while True:
            try:
                c = self.app.c
                options = ['資料來源／檔案／工作表／欄位', f'單位設定  X={c.xmode}  Y={c.yunit}',
                           f'線性擬合  {c.fit_rows or "尚未設定"}', f'手動降伏點  {c.yield_row or "尚未設定"}',
                           '圖表／縮放／瀏覽資料', '分析設定／方向／歸零／門檻', '匯出 JSON / CSV / SVG', '操作說明', '離開']
                selected = self.menu('↑↓ 導覽主要工作；Enter 開啟', options, selected)
                if selected == 0: self.source()
                elif selected == 1: self.units()
                elif selected == 2: self.fit()
                elif selected == 3:
                    if self.menu('手動降伏點', ['選取資料點', '清除標記']) == 1: self.execute('yield off')
                    else: self.execute(f'yield {self.pick_row("手動降伏點", c.yield_row)}')
                elif selected == 4: self.view()
                elif selected == 5: self.settings()
                elif selected == 6:
                    self.execute('export'); self.notice('匯出完成', self.app.message)
                elif selected == 7:
                    self.notice('操作說明', '建議順序：單位 → 線性擬合 → 查看圖表 → 匯出\n選單：↑↓ 選擇，Enter / → 開啟，← 返回。\n選點：↑↓ 一筆，←→ 十筆，Enter 確認，Esc 取消。\n數值：↑↓ 增減，←→ 改步長，Enter 確認。也可直接打數字。\n子操作在最後一次 Enter 後才套用；Esc 取消。\nO：0.2% offset；C：偏離候選，不等同冶金降伏點。\n離開前匯出可保存結果；下次啟動不自動還原設定。\n傳統指令模式：python yield_tui.py --commands')
                elif selected == 8:
                    action = self.menu('離開前保存分析？', ['返回繼續工作', '匯出並離開', '直接離開'])
                    if action == 1:
                        self.execute('export'); self.notice('匯出完成', self.app.message)
                    if action: return
            except Cancel:
                continue
            except (ValueError, OSError, IndexError, ImportError) as exc:
                self.notice('無法完成操作；請調整設定', str(exc))

    def run(self):
        try:
            sys.stdout.write('\033[?1049h\033[?25l')
            with keyboard() as read:
                self.read = read
                self.loop()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            sys.stdout.write('\033[?25h\033[?1049l')
            sys.stdout.flush()
