import asyncio
import threading

import pandas as pd

from src.data.price_fetcher import PriceFetcher


def test_history_fetch_runs_blocking_provider_off_event_loop(monkeypatch):
    fetcher = PriceFetcher()
    main_thread = threading.get_ident()
    worker_threads = []

    def fake_sync(symbol, start_date, end_date, point_in_time_safe=False):
        worker_threads.append(threading.get_ident())
        return pd.DataFrame(
            {"close": [10.0]},
            index=pd.to_datetime(["2025-01-02"]),
        )

    monkeypatch.setattr(fetcher, "_fetch_history_frame_sync", fake_sync)
    result = asyncio.run(fetcher.fetch_history_frame(
        "000001", "2025-01-01", "2025-01-03", point_in_time_safe=True,
    ))

    assert not result.empty
    assert worker_threads and worker_threads[0] != main_thread
