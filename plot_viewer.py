"""Self-contained, offline interactive chart snapshots."""
from datetime import datetime
import html
from pathlib import Path
import webbrowser

from yield_tui import analyze, chart_series, labels, limits, transformed, fmt


CHART_CONFIG = dict(
    scrollZoom=True, displaylogo=False, displayModeBar=True, responsive=True,
    doubleClick='reset',
    modeBarButtonsToRemove=['select2d', 'lasso2d'],
    toImageButtonOptions=dict(format='png', filename='yield-lens', scale=2),
)


def build_figure(d, c, view=None):
    import plotly.graph_objects as go
    try:
        result = analyze(d, c)
    except ValueError as exc:
        result = dict(warnings=[str(exc)], fit=None, offset=None,
                      candidate=None, manual_yield=None)
    x, y = transformed(d, c)
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=x.tolist(), y=y.tolist(), mode='lines', name='量測曲線',
        line=dict(color='#2563eb', width=2.5),
        customdata=d.rows.tolist(),
        hovertemplate='原始列 %{customdata}<br>X %{x:.7g}<br>Y %{y:.7g}<extra>量測曲線</extra>',
    ))
    for name, xs, ys, _ in chart_series(d, c, result)[1:]:
        figure.add_trace(go.Scatter(
            x=xs.tolist(), y=ys.tolist(), mode='lines',
            name='線性擬合' if name == 'linear fit' else '0.2% offset',
            line=dict(color='#d97706' if name == 'linear fit' else '#e11d48',
                      width=1.8, dash='dash'),
            hovertemplate='X %{x:.7g}<br>Y %{y:.7g}<extra>%{fullData.name}</extra>',
        ))
    for key, name, color, symbol in [
        ('offset', '0.2% offset 交點', '#e11d48', 'circle'),
        ('candidate', '偏離候選點（非降伏定義）', '#7c3aed', 'diamond'),
        ('manual_yield', '手動降伏點', '#059669', 'star'),
    ]:
        point = result.get(key)
        if point:
            figure.add_trace(go.Scatter(
                x=[point['x']], y=[point['y']], name=name, mode='markers',
                marker=dict(color=color, size=12, symbol=symbol,
                            line=dict(color='white', width=2)),
                hovertemplate='X %{x:.7g}<br>Y %{y:.7g}<extra>%{fullData.name}</extra>',
            ))
    left, right, bottom, top = limits(d, c, view)
    xlabel, ylabel = labels(c)
    figure.update_layout(
        template='plotly_white', paper_bgcolor='white', plot_bgcolor='white',
        font=dict(family='Segoe UI, Microsoft JhengHei, sans-serif', size=13, color='#475569'),
        margin=dict(l=80, r=35, t=60, b=70), dragmode='zoom', hovermode='closest',
        legend=dict(orientation='h', y=1.13, x=0, font=dict(size=12)),
        xaxis=dict(title=xlabel, range=[left, right], gridcolor='#edf1f7',
                   zerolinecolor='#cbd5e1', showspikes=True, spikemode='across', spikesnap='cursor'),
        yaxis=dict(title=ylabel, range=[bottom, top], gridcolor='#edf1f7',
                   zerolinecolor='#cbd5e1', fixedrange=False),
    )
    return figure, result


def write_preview(d, c, destination, view=None):
    figure, result = build_figure(d, c, view)
    # Inline JavaScript keeps every chart usable without a server or CDN.
    graph = figure.to_html(full_html=False, include_plotlyjs=True,
                           div_id='yield-chart', config=CHART_CONFIG)
    esc = html.escape
    fit = result.get('fit') or {}
    offset = result.get('offset') or {}
    cards = [('有效資料', f'{len(d.rows):,}', '原始資料點全部保留'),
             ('線性擬合 R²', f'{fit["r_squared"]:.6f}' if fit else '—',
              f'原始列 {c.fit_rows}' if c.fit_rows else '尚未選取線性區間'),
             ('0.2% OFFSET · Y', fmt(offset['y']) if offset else '—', labels(c)[1])]
    card_html = ''.join(f'<section class="metric"><small>{esc(title)}</small><strong>{esc(value)}</strong><span>{esc(note)}</span></section>' for title, value, note in cards)
    warnings = ''.join(f'<li>{esc(warning)}</li>' for warning in result['warnings'])
    page = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yield Lens · 互動圖表</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#17243b;font:15px 'Segoe UI','Microsoft JhengHei',sans-serif}
main{max-width:1500px;margin:auto;padding:32px 36px}header{display:flex;justify-content:space-between;align-items:center;gap:24px}
.brand{font-size:12px;letter-spacing:3px;color:#2563eb;font-weight:700}h1{margin:10px 0;font-size:28px;font-weight:650;overflow-wrap:anywhere}
.sub,small{color:#64748b;font-size:12px}.badge{border:1px solid #cbd5e1;border-radius:30px;padding:8px 13px;white-space:nowrap;font-size:12px}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}.metric{background:white;border:1px solid #e2e8f0;border-radius:14px;padding:18px 22px}
.metric strong{display:block;font-size:27px;font-weight:600;margin:8px 0}.metric span{font-size:12px;color:#64748b}
.panel{background:white;border:1px solid #e2e8f0;border-radius:16px;overflow:hidden;box-shadow:0 10px 40px #23365506}
.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:16px 22px;border-bottom:1px solid #edf1f7}
button{border:1px solid #dbe3ee;border-radius:8px;background:white;color:#334155;padding:9px 15px;cursor:pointer;font:inherit;font-size:13px}
button:hover,button[aria-pressed="true"]{background:#eff6ff;color:#1d4ed8;border-color:#93c5fd}button:focus-visible{outline:2px solid #2563eb;outline-offset:2px}
.hint{color:#64748b;font-size:12px;margin-left:auto}#yield-chart{height:64vh!important;min-height:400px}
.notes{font-size:12px;color:#64748b;line-height:1.8;margin-top:18px}li{margin:4px 0}
@media(max-width:700px){main{padding:16px}.metrics{grid-template-columns:1fr}.hint{width:100%;margin:6px 0}header{align-items:flex-start}h1{font-size:22px}}
</style></head><body><main>
'''
    page += f'<header><div><div class="brand">YIELD LENS / ANALYSIS</div><h1>{esc(d.path.name)}</h1><div class="sub">{esc(d.sheet)} · X/Y 欄 {d.columns[0]}/{d.columns[1]} · {datetime.now():%Y-%m-%d %H:%M:%S}</div></div><span class="badge">本機 · 離線預覽</span></header>'
    page += f'<div class="metrics">{card_html}</div>'
    page += '''<section class="panel"><nav class="toolbar" aria-label="圖表操作">
<button id="zoom" aria-pressed="true">框選放大</button><button id="pan" aria-pressed="false">拖曳平移</button>
<button id="reset">恢復全圖</button><button id="download">下載 PNG</button>
<span class="hint">滾輪縮放 · 滑鼠讀值 · 點圖例切換曲線</span></nav>'''
    page += graph + '</section>'
    page += f'<div class="notes">這是目前分析的快照。更改 TUI 設定後，請再次開啟預覽。縮放不會修改分析設定。<ul>{warnings}</ul></div>'
    page += '''</main><script>
const chart = document.getElementById('yield-chart');
const initialX = chart.layout.xaxis.range.slice();
const initialY = chart.layout.yaxis.range.slice();
function mode(value) {
  Plotly.relayout(chart, {dragmode:value});
  document.getElementById('zoom').setAttribute('aria-pressed', String(value==='zoom'));
  document.getElementById('pan').setAttribute('aria-pressed', String(value==='pan'));
}
document.getElementById('zoom').onclick=()=>mode('zoom');
document.getElementById('pan').onclick=()=>mode('pan');
document.getElementById('reset').onclick=()=>Plotly.relayout(chart, {
  'xaxis.range':initialX, 'yaxis.range':initialY, 'xaxis.autorange':false, 'yaxis.autorange':false});
document.getElementById('download').onclick=()=>Plotly.downloadImage(chart, {format:'png',filename:'yield-lens',scale:2});
</script></body></html>'''
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding='utf-8')
    return destination.resolve()


def open_preview(app):
    destination = Path(app.output)/'previews'/f'yield_{datetime.now():%Y%m%d_%H%M%S_%f}.html'
    # Start with the full curve; the independent window owns its zoom state.
    path = write_preview(app.d, app.c, destination)
    try:
        opened = webbrowser.open(path.as_uri(), new=1)
    except (OSError, webbrowser.Error):
        opened = False
    return path, opened
