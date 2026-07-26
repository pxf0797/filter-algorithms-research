/* ============================================================================
 * Plotly cross-subplot crosshair helper
 *
 * Shared by two independent rendering paths:
 *   1. Streamlit embed:  filter/browse/charts.py _render_plotly()
 *      Injects __DIV_ID__ and __FIGURE_JSON__ placeholders.
 *      DIV ID format: chart-{random suffix} (auto-generated per render).
 *   2. Standalone HTML:  docs/backtesting/回测结果可视化.html
 *      Loaded via view_backtest.py with window.BACKTEST_DATA pre-injected.
 *      DIV ID format: chart-dash-v{N}, chart-heatmap, chart-overview,
 *                     chart-signals, chart-pnl, chart-multi-pnl,
 *                     chart-trades, chart-filtered-overview.
 *      Global vars: window.BACKTEST_DATA, window.BACKTEST_STATS,
 *                   window.BACKTEST_METADATA, window._DIV_ID_.
 *
 * Injected arguments (replaced by Python renderer):
 *   __DIV_ID__      — unique DOM element id
 *   __FIGURE_JSON__ — serialized Plotly figure (data + layout)
 *   __HEIGHT__      — chart height in px (used by Python renderer, not JS)
 *
 * Payload optimisation (M6):
 *   - Layout properties matching _SHARED_LAYOUT_TEMPLATE are stripped from
 *     the per-chart JSON; they are restored from SHARED_LAYOUT below.
 *   - Traces whose `x` array is identical to data[0].x have `x` stripped;
 *     it is restored below before rendering.
 *   - Plotly is resolved from window.parent.Plotly when not loaded in this
 *     iframe (supports include_plotlyjs=False rendering in sibling iframes).
 * ============================================================================ */

/* ── Shared layout defaults ──────────────────────────────────────────────
 * Kept in sync with _SHARED_LAYOUT_TEMPLATE in charts.py.
 * Any property present here is omitted from the per-chart payload.
 * ──────────────────────────────────────────────────────────────────────── */
var SHARED_LAYOUT = {
    template: "plotly_dark",
    margin: {l: 10, r: 10, t: 25, b: 10},
    hovermode: "x unified",
    legend: {
        orientation: "h", yanchor: "bottom", y: 1.02,
        xanchor: "right", x: 1, font: {size: 9}
    }
};

(function() {
    var _fallbackEl = document.getElementById('plotly-fallback-__DIV_ID__');

    /* ── Resolve Plotly: local window, then parent window ── */
    function _getPlotly() {
        if (typeof Plotly !== 'undefined') return Plotly;
        if (window.parent && window.parent.Plotly) return window.parent.Plotly;
        return null;
    }

    /* ── Main render ── */
    function _render() {
        var P = _getPlotly();
        if (!P) {
            _fallbackEl.style.display = 'block';
            document.getElementById('__DIV_ID__').style.display = 'none';
            return;
        }
        /* If parent loaded Plotly, clear CDN-failed flag */
        if (typeof Plotly === 'undefined' && window._plotlyCdnFailed) {
            delete window._plotlyCdnFailed;
        }

        var figure = __FIGURE_JSON__;

        /* ── Restore shared layout defaults ── */
        var layout = figure.layout || {};
        for (var key in SHARED_LAYOUT) {
            if (!(key in layout)) {
                layout[key] = SHARED_LAYOUT[key];
            }
        }
        figure.layout = layout;

        /* ── Restore missing x arrays from data[0].x ── */
        var data = figure.data;
        if (data.length > 0 && data[0].x) {
            var _sharedX = data[0].x;
            for (var i = 1; i < data.length; i++) {
                if (!data[i].x) {
                    data[i].x = _sharedX;
                }
            }
        }

        var config = {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ['lasso2d', 'select2d']
        };

        P.newPlot('__DIV_ID__', data, layout, config).then(function (gd) {
            var _lastXv = -Infinity, _pending = false, _pendingXv = null, _THROTTLE_MS = 45;
            function _nearestIdx(arr, xv) {
                var lo = 0, hi = arr.length - 1;
                if (xv <= arr[lo]) return lo;
                if (xv >= arr[hi]) return hi;
                while (lo < hi - 1) { var mid = (lo + hi) >> 1; if (arr[mid] <= xv) lo = mid; else hi = mid; }
                return (xv - arr[lo] <= arr[hi] - xv) ? lo : hi;
            }
            var _shapeKeys = [];
            var shapes = gd.layout.shapes || [];
            for (var i = 0; i < shapes.length; i++) {
                if (shapes[i].yref === 'paper' || shapes[i].yref === 'y domain') {
                    _shapeKeys.push({ x0: 'shapes[' + i + '].x0', x1: 'shapes[' + i + '].x1', vis: 'shapes[' + i + '].visible' });
                }
            }
            var _xArr0 = gd.data[0].x;
            var _dates = gd.layout._dates;
            var _hasDates = _dates && _dates.length > 0;
            var _tip = document.getElementById('date-tip-__DIV_ID__');
            var _dateCache = '';

            function _apply(xv) {
                _pending = false;
                var u = {};
                for (var i = 0; i < _shapeKeys.length; i++) { var k = _shapeKeys[i]; u[k.x0] = xv; u[k.x1] = xv; u[k.vis] = true; }
                P.relayout(gd, u);
                _lastXv = xv;
                if (_hasDates && _xArr0 && _xArr0.length > 0) {
                    var idx = _nearestIdx(_xArr0, xv);
                    _dateCache = (idx < _dates.length) ? _dates[idx] : '';
                    if (_dateCache) {
                        _tip.textContent = _dateCache;
                        _tip.style.display = 'block';
                    } else {
                        _tip.style.display = 'none';
                    }
                }
            }

            gd.on('plotly_hover', function (evt) {
                if (!evt.points || evt.points.length === 0) return;
                var xv = evt.points[0].x;
                if (xv === _lastXv) return;
                if (_pending) { _pendingXv = xv; }
                else {
                    _pending = true; _pendingXv = null;
                    _apply(xv);
                    setTimeout(function() {
                        if (_pendingXv !== null && _pendingXv !== _lastXv) _apply(_pendingXv);
                        else _pending = false;
                    }, _THROTTLE_MS);
                }
            });

            gd.on('plotly_unhover', function () {
                _pending = false; _pendingXv = null; _lastXv = -Infinity;
                var u = {};
                for (var i = 0; i < _shapeKeys.length; i++) { u[_shapeKeys[i].vis] = false; }
                P.relayout(gd, u);
                _tip.style.display = 'none';
                _dateCache = '';
            });

            document.getElementById('__DIV_ID__').addEventListener('mousemove', function (e) {
                if (_tip.style.display === 'block') {
                    var tx = e.clientX + 16;
                    var tw = _tip.offsetWidth || 100;
                    if (tx + tw > window.innerWidth - 10) tx = e.clientX - tw - 16;
                    if (tx < 5) tx = 5;
                    _tip.style.left = tx + 'px';
                    _tip.style.top = (e.clientY - 28) + 'px';
                }
            });
        });

        /* Safety check: if Plotly still not loaded after 5s, show fallback */
        setTimeout(function() {
            if (!_getPlotly()) {
                _fallbackEl.style.display = 'block';
                document.getElementById('__DIV_ID__').style.display = 'none';
            }
        }, 5000);
    }

    /* ── Entry: immediate or polled ── */
    if (_getPlotly()) {
        _render();
    } else {
        /* Poll for parent.Plotly (loaded by a prior sibling iframe).
         * Timeout after 5 seconds → show fallback. */
        var _tries = 0;
        var _timer = setInterval(function() {
            if (_getPlotly()) {
                clearInterval(_timer);
                _render();
            } else if (++_tries > 50) {
                clearInterval(_timer);
                _render();  /* will show fallback */
            }
        }, 100);
    }
})();
