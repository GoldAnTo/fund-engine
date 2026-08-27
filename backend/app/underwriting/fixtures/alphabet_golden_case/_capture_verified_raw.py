"""Capture already-audited official bytes into deterministic gzip sidecars.

This maintenance script is not imported by the application.  It fails before
writing when a provider response differs from the independently audited length
or SHA-256.
"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent
RAW_ROOT = ROOT / "raw"
CAPTURES = (
    (
        "nasdaq_googl_2026-08-25.json.gz",
        "https://api.nasdaq.com/api/quote/GOOGL/historical?assetclass=stocks&fromdate=2026-08-24&todate=2026-08-25&limit=10",
        493,
        "c75b9b645e16992c0d5a778517900173ace83d1d3cb76400e68d890a6ba23a6c",
        "nasdaq",
    ),
    (
        "nasdaq_goog_2026-08-25.json.gz",
        "https://api.nasdaq.com/api/quote/GOOG/historical?assetclass=stocks&fromdate=2026-08-24&todate=2026-08-25&limit=10",
        492,
        "29e981036b84e13b09724c2d839e1253459c0535c0c9770eb9cb3b5f2a24fe4a",
        "nasdaq",
    ),
    (
        "federal_reserve_h10_usd_cny.csv.gz",
        "https://www.federalreserve.gov/datadownload/Output.aspx?filetype=csv&from=&label=include&lastobs=10&layout=seriescolumn&rel=H10&series=60f32914ab61dfab590e0e470153e3ae&to=&type=package",
        3672,
        "0377197ee8f124a336bbad0cb3b0a3ee08db0671232db438a452aa030dc13682",
        "federal_reserve",
    ),
    (
        "alphabet_q2_2026_exhibit_99_1.html.gz",
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000066/googexhibit991q22026.htm",
        371494,
        "a01f6bd87c7fa0dcb562493dda7348a1a37d017b4a4b5edb39b915b45688237e",
        "sec",
    ),
    (
        "alphabet_q2_2026_10q.html.gz",
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm",
        2464133,
        "fecfbc2683f630380b17937278ce3745eca150eb90e21a945fd6b78fe19728c7",
        "sec",
    ),
    (
        "alphabet_2025_10k.html.gz",
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm",
        2616499,
        "c2f6301004f35411a20611c14ff01d80a85c0bcbab6053c80d8cc7f6fc747161",
        "sec",
    ),
)


def _headers(provider: str) -> dict[str, str]:
    common = {
        "Accept-Encoding": "identity",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
    }
    if provider == "nasdaq":
        common.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            }
        )
    elif provider == "sec":
        common["User-Agent"] = "fund-engine-research/1.0 research@example.com"
    return common


def main() -> None:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    captured: list[tuple[Path, bytes]] = []
    for name, url, expected_size, expected_hash, provider in CAPTURES:
        command = [
            "curl", "--http1.1", "--fail", "--silent", "--show-error",
            "--retry", "4", "--retry-all-errors", "--max-time", "60",
        ]
        for key, value in _headers(provider).items():
            command.extend(("--header", f"{key}: {value}"))
        raw = subprocess.check_output((*command, url))
        actual_hash = hashlib.sha256(raw).hexdigest()
        if len(raw) != expected_size or actual_hash != expected_hash:
            raise RuntimeError(
                f"{name} response drift: size={len(raw)} sha256={actual_hash}"
            )
        captured.append((RAW_ROOT / name, gzip.compress(raw, compresslevel=9, mtime=0)))
    for path, compressed in captured:
        path.write_bytes(compressed)
        print(
            f"{path.name} compressed_sha256={hashlib.sha256(compressed).hexdigest()}"
        )


if __name__ == "__main__":
    main()
