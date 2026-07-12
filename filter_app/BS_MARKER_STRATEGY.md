# BS Marker Strategy

## Data Source

BS markers = 同向性判断 data, sourced from the higher TF's `trade_records` aligned to the current TF via `_align_pnl_to_current_tf`.

The `higher_pnl` structure already contains this aligned data. No need to recompute — the aligned markers are passed directly to `compute_bs_markers` through the `aligned_markers` parameter.

## Marker Computation Paths

`compute_bs_markers` selects the computation strategy based on context:

1. **Operating TF with `aligned_markers`** — Primary path. Uses `_compute_own_from_alignment` to generate BS markers from aligned higher-TF trade data (同向性判断).

2. **Operating TF with `trade_records`** — Fallback. Uses `_compute_own_from_trades` to generate markers from local trade records (top-level TF with no higher reference).

3. **Operating TF with only `all_pairs`** — Last resort. Uses `_compute_own_from_pairs` to generate markers from Schmitt signal pairs (no strategy trading data available).

4. **Lower TF with `higher_bs`** — Uses `_compute_cascade_markers` to propagate markers down from the higher TF.

5. **Higher TF or no data** — Returns empty markers.

## Marker Color Rules

| Direction | Entry | Exit |
|-----------|-------|------|
| Long      | Green B | Green S |
| Short     | Red S   | Red B   |
