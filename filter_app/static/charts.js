/* ============================================================================
 * Plotly cross-subplot crosshair helper
 *
 * Injected arguments (replaced by Python renderer):
 *   __DIV_ID__      — unique DOM element id
 *   __FIGURE_JSON__ — serialized Plotly figure (data + layout)
 *   __HEIGHT__      — chart height in px (used by Python renderer, not JS)
 * ============================================================================ */

(function() {
    var _fallbackEl = document.getElementById('plotly-fallback-__DIV_ID__');
    if (typeof Plotly === 'undefined') {
        _fallbackEl.style.display = 'block';
        document.getElementById('__DIV_ID__').style.display = 'none';
        return;
    } else if (window._plotlyCdnFailed) {
        // CDNJS fallback succeeded, clear flag
        delete window._plotlyCdnFailed;
    }
    var figure = __FIGURE_JSON__;
    var config = {
        responsive: true,
        displayModeBar: true,
        displaylogo: false,
        modeBarButtonsToRemove: ['lasso2d', 'select2d']
    };
    Plotly.newPlot('__DIV_ID__', figure.data, figure.layout, config).then(function (gd) {
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
            Plotly.relayout(gd, u);
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
            Plotly.relayout(gd, u);
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
    // Safety check: if Plotly still not loaded after 5s, show fallback
    setTimeout(function() {
        if (typeof Plotly === 'undefined') {
            _fallbackEl.style.display = 'block';
            document.getElementById('__DIV_ID__').style.display = 'none';
        }
    }, 5000);
})();
