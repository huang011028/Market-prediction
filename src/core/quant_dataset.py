"""Build point-in-time quant datasets from historical technical snapshots."""

from __future__ import annotations

import time
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from src.core.prediction_target import (
    default_target_spec,
    direction_from_return,
    target_spec_for_volatility,
)
from src.core.return_residualizer import estimate_market_beta
from src.core.experiment_manifest import detect_experiment_source, write_experiment_manifest
from src.data.price_fetcher import PriceFetcher
from src.data.investable_universe import InvestableUniverseStore
from src.data.quant_pit_enrichment import QuantPitEnrichmentStore
from src.data.quant_price_cache import QuantPriceCache
from src.data.quant_feature_store import (
    QuantFeatureRow,
    QuantFeatureStore,
    FEATURE_SCHEMA_VERSION,
    extract_technical_features,
)
from src.data.symbol_resolver import resolve_symbol


@dataclass
class QuantDatasetBuildConfig:
    targets: list[str]
    start_date: str
    end_date: str
    timeframe: str = "短期(1周)"
    interval_days: int = 7
    lookback_days: int = 180
    max_samples: int = 0
    export_parquet: bool = True
    use_universe: bool = False
    universe_market: str = "A"
    universe_limit: int = 0
    min_listing_days: int = 120
    min_price: float = 1.0
    min_avg_traded_value: float = 0.0
    industry_neutralization: bool = False
    universe_sample_seed: str = "quant-v3.1-a-share"
    universe_stratify: bool = True
    replace_partition: bool = True
    use_pit_enrichment: bool = True
    fundamental_max_age_days: int = 550
    announcement_lookback_days: int = 90
    industry_standard: str = QuantPitEnrichmentStore.DEFAULT_INDUSTRY_STANDARD
    use_price_cache: bool = True
    history_fetch_concurrency: int = 3


@dataclass
class QuantDatasetBuildReport:
    generated_at: str
    config: dict
    saved: int
    skipped: list[dict] = field(default_factory=list)
    feature_store_status: dict = field(default_factory=dict)
    parquet_paths: list[str] = field(default_factory=list)
    industry_neutralization: dict = field(default_factory=dict)
    deleted_existing: int = 0
    feature_coverage: dict = field(default_factory=dict)
    enrichment_status: dict = field(default_factory=dict)
    price_cache_status: dict = field(default_factory=dict)
    data_version: str = "research_data.v2"
    feature_version: str = FEATURE_SCHEMA_VERSION
    derived_feature_counts: dict = field(default_factory=dict)
    manifest_path: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class QuantHistoricalDatasetBuilder:
    """Generate V3 labels from data that is truncated at each historical date."""

    def __init__(
        self,
        store: Optional[QuantFeatureStore] = None,
        price_fetcher: Optional[PriceFetcher] = None,
        universe_store: Optional[InvestableUniverseStore] = None,
        enrichment_store: Optional[QuantPitEnrichmentStore] = None,
        price_cache: Optional[QuantPriceCache] = None,
    ):
        self.store = store or QuantFeatureStore()
        self.price_fetcher = price_fetcher or PriceFetcher()
        self.universe_store = universe_store or InvestableUniverseStore()
        self.enrichment_store = enrichment_store or QuantPitEnrichmentStore()
        self.price_cache = price_cache or QuantPriceCache()

    async def run(
        self,
        config: QuantDatasetBuildConfig,
        output_dir: Optional[str | Path] = None,
    ) -> QuantDatasetBuildReport:
        started = time.monotonic()
        saved = 0
        skipped: list[dict] = []
        coverage_counts = {
            "technical": 0,
            "fundamental": 0,
            "fundamental_high_quality": 0,
            "performance": 0,
            "surprise": 0,
            "news": 0,
            "industry": 0,
            "valuation": 0,
        }
        dates = self._dates(config.start_date, config.end_date, config.interval_days)
        target_spec = default_target_spec(config.timeframe, market=config.universe_market)
        deleted_existing = 0
        if config.replace_partition:
            deleted_existing = self.store.delete_partition(
                market=config.universe_market,
                horizon=target_spec.horizon,
                target_version=target_spec.target_version,
                start_date=config.start_date,
                end_date=config.end_date,
            )

        if config.use_universe:
            work_items = []
            for as_of in dates:
                members = self.universe_store.eligible_on(
                    as_of,
                    market=config.universe_market,
                    min_listing_days=config.min_listing_days,
                    limit=config.universe_limit,
                    sample_seed=config.universe_sample_seed,
                    stratify=config.universe_stratify,
                )
                work_items.extend((member["symbol"], as_of, member) for member in members)
            if not work_items:
                raise ValueError("PIT 股票池没有符合条件的历史成员，请先刷新股票池")
        else:
            work_items = [
                (target, as_of, None)
                for target in config.targets
                for as_of in dates
            ]

        frame_cache: dict[str, pd.DataFrame] = {}
        frame_errors: dict[str, str] = {}
        broad_start = datetime.fromisoformat(config.start_date[:10]) - timedelta(
            days=max(config.lookback_days, 260)
        )
        broad_end = datetime.fromisoformat(config.end_date[:10]) + timedelta(days=120)

        if config.use_price_cache:
            preload_symbols = {target for target, _, _ in work_items}
            if target_spec.benchmark_symbol:
                preload_symbols.add(target_spec.benchmark_symbol)
            semaphore = asyncio.Semaphore(max(1, int(config.history_fetch_concurrency)))

            async def preload(symbol: str) -> None:
                async with semaphore:
                    try:
                        await self._history_frame(
                            symbol,
                            frame_cache,
                            frame_errors,
                            broad_start,
                            broad_end,
                            use_price_cache=True,
                        )
                    except Exception:
                        return

            await asyncio.gather(*(preload(symbol) for symbol in sorted(preload_symbols)))

        for target, as_of, universe_row in work_items:
            if config.max_samples and saved >= config.max_samples:
                break
            info = resolve_symbol(target)
            try:
                coverage = await self._build_one(
                    info,
                    as_of,
                    config,
                    universe_row=universe_row,
                    frame_cache=frame_cache,
                    frame_errors=frame_errors,
                    broad_start=broad_start,
                    broad_end=broad_end,
                )
                saved += 1
                for family, available in coverage.items():
                    coverage_counts[family] += int(bool(available))
            except Exception as exc:
                skipped.append({
                    "target": target,
                    "as_of": as_of.date().isoformat(),
                    "reason": str(exc),
                })

        derived_feature_counts = self._apply_research_v2_features(
            market=config.universe_market,
            horizon=target_spec.horizon,
            target_version=target_spec.target_version,
            start_date=config.start_date,
            end_date=config.end_date,
        ) if saved else {}

        industry_neutralization = {}
        if config.use_universe and config.industry_neutralization and saved:
            industry_neutralization = self.store.apply_industry_neutralization(
                market=config.universe_market,
                horizon=target_spec.horizon,
                target_version=target_spec.target_version,
                start_date=config.start_date,
                end_date=config.end_date,
            )

        parquet_paths: list[str] = []
        if config.export_parquet and output_dir:
            try:
                parquet_paths = self.store.export_parquet(
                    Path(output_dir) / "features",
                    feature_version=FEATURE_SCHEMA_VERSION,
                )
            except Exception as exc:
                skipped.append({"target": "parquet_export", "as_of": "", "reason": str(exc)})

        manifest_path = ""
        if output_dir:
            manifest_path = write_experiment_manifest(
                output_dir,
                experiment_id=Path(output_dir).name,
                kind="quant_dataset",
                source_type=detect_experiment_source(),
                config=asdict(config),
                status="completed",
                artifacts={"parquet_paths": parquet_paths},
                metrics={
                    "saved": saved,
                    "skipped": len(skipped),
                    "feature_version": FEATURE_SCHEMA_VERSION,
                },
                project_root=Path(__file__).resolve().parents[2],
            )

        return QuantDatasetBuildReport(
            generated_at=datetime.now().isoformat(),
            config=asdict(config),
            saved=saved,
            skipped=skipped,
            feature_store_status=self.store.status(),
            parquet_paths=parquet_paths,
            industry_neutralization=industry_neutralization,
            deleted_existing=deleted_existing,
            feature_coverage={
                family: {
                    "rows": count,
                    "coverage": round(count / max(1, saved), 4),
                }
                for family, count in coverage_counts.items()
            },
            enrichment_status=self.enrichment_store.status(),
            price_cache_status=self.price_cache.status(),
            derived_feature_counts=derived_feature_counts,
            manifest_path=manifest_path,
            elapsed_seconds=time.monotonic() - started,
        )

    async def _build_one(
        self,
        info,
        as_of: datetime,
        config: QuantDatasetBuildConfig,
        *,
        universe_row: Optional[dict] = None,
        frame_cache: dict[str, pd.DataFrame],
        frame_errors: dict[str, str],
        broad_start: datetime,
        broad_end: datetime,
    ) -> dict[str, bool]:
        full_frame = await self._history_frame(
            info.symbol,
            frame_cache,
            frame_errors,
            broad_start,
            broad_end,
            use_price_cache=config.use_price_cache,
        )
        as_of_ts = pd.Timestamp(as_of).normalize()
        start_ts = as_of_ts - pd.Timedelta(days=config.lookback_days)
        historical_frame = full_frame[
            (full_frame.index >= start_ts) & (full_frame.index <= as_of_ts)
        ]
        if historical_frame.empty:
            raise ValueError("as_of 之前没有历史行情")
        price_data = await self.price_fetcher._build_price_data(
            info.symbol,
            info.market,
            historical_frame,
            as_of=as_of_ts,
            include_weekly_context=False,
            include_intraday_context=False,
            name=info.name,
        )
        if price_data.trading_days < 60:
            raise ValueError(f"历史 K 线不足 60 个交易日: {price_data.trading_days}")

        data = price_data.to_agent_dict()
        if float(price_data.price_current) < float(config.min_price):
            raise ValueError(f"价格低于可投资阈值: {price_data.price_current} < {config.min_price}")
        traded_values = [
            float(item.get("close", 0.0) or 0.0) * float(item.get("volume", 0.0) or 0.0)
            for item in (price_data.recent_trend or [])[-20:]
            if float(item.get("close", 0.0) or 0.0) > 0
        ]
        avg_traded_value = sum(traded_values) / len(traded_values) if traded_values else 0.0
        if config.min_avg_traded_value and avg_traded_value < config.min_avg_traded_value:
            raise ValueError(
                f"20日平均成交额不足: {avg_traded_value:.0f} < {config.min_avg_traded_value:.0f}"
            )
        volatility = (
            (data.get("technical_snapshot") or {})
            .get("volatility_signals", {})
            .get("daily_volatility_20d_pct")
        )
        target_spec = target_spec_for_volatility(
            default_target_spec(config.timeframe, target=info.symbol, market=info.market),
            volatility,
        )
        benchmark_frame = None
        if target_spec.benchmark_symbol:
            try:
                benchmark_frame = await self._history_frame(
                    target_spec.benchmark_symbol,
                    frame_cache,
                    frame_errors,
                    broad_start,
                    broad_end,
                    use_price_cache=config.use_price_cache,
                )
                asset_history = full_frame[full_frame.index <= as_of_ts]["close"].tail(
                    target_spec.beta_lookback_days + 5
                )
                benchmark_history = benchmark_frame[
                    benchmark_frame.index <= as_of_ts
                ]["close"].tail(target_spec.beta_lookback_days + 5)
                target_spec.market_beta = estimate_market_beta(
                    asset_history,
                    benchmark_history,
                    min_observations=target_spec.beta_min_observations,
                )
            except Exception:
                target_spec.market_beta = None
        outcome = self._frame_outcome(
            full_frame,
            benchmark_frame,
            as_of_ts,
            float(price_data.price_current),
            target_spec,
        )

        features = extract_technical_features(data)
        features.update({
            "meta__market": info.market,
            "meta__horizon_days": target_spec.horizon_trading_days,
            "meta__daily_volatility_pct": float(volatility or 0.0),
            "meta__price": float(price_data.price_current),
            "meta__avg_traded_value_20d": round(avg_traded_value, 2),
        })
        if universe_row:
            list_date = str(universe_row.get("list_date") or "")[:10]
            listing_age = (
                as_of.date() - datetime.fromisoformat(list_date).date()
            ).days if list_date else 0
            industry_source_date = str(universe_row.get("source_timestamp") or "")[:10]
            industry_pit_verified = bool(
                universe_row.get("industry")
                and industry_source_date
                and industry_source_date <= as_of.date().isoformat()
            )
            features.update({
                "meta__exchange": universe_row.get("exchange") or "",
                "meta__board": universe_row.get("board") or "",
                "meta__industry": (
                    universe_row.get("industry") if industry_pit_verified else "unknown"
                ),
                "meta__industry_pit_verified": industry_pit_verified,
                "meta__listing_age_days": listing_age,
            })
        enrichment_lineage = {}
        if config.use_pit_enrichment:
            enrichment_features, enrichment_lineage = self.enrichment_store.features_as_of(
                info.symbol,
                as_of.date().isoformat(),
                market=info.market,
                fundamental_max_age_days=config.fundamental_max_age_days,
                announcement_lookback_days=config.announcement_lookback_days,
                industry_standard=config.industry_standard,
            )
            features.update(enrichment_features)
        features.update(self._valuation_features(
            features,
            float(price_data.price_current),
            enrichment_lineage,
        ))
        valid_date = str(outcome["validation_window"]).split("~")[-1]
        row = QuantFeatureRow(
            market=info.market,
            symbol=info.symbol,
            target_name=info.name,
            as_of=as_of.date().isoformat(),
            timeframe=config.timeframe,
            horizon=target_spec.horizon,
            target_version=target_spec.target_version,
            features=features,
            source_kind="historical_replay",
            valid_date=valid_date,
            label_direction=outcome["actual_direction"],
            label_return_pct=float(outcome["effective_fixed_return_pct"]),
            label_absolute_return_pct=float(outcome["fixed_horizon_return_pct"]),
            label_benchmark_return_pct=outcome.get("benchmark_return_pct"),
            label_market_beta=outcome.get("market_beta"),
            label_market_residual_pct=outcome.get("market_residual_return_pct"),
            label_threshold_pct=float(target_spec.up_threshold_pct),
            lineage={
                "point_in_time_verified": True,
                "source": "price_fetcher.fetch_history_frame+strict_as_of_slice",
                "source_timestamps": [price_data.latest_date or as_of.date().isoformat()],
                "target_spec": target_spec.to_dict(),
                "feature_latest_date": price_data.latest_date,
                "future_prices_used_only_for_label": True,
                "market_beta_estimation": {
                    "lookback_days": target_spec.beta_lookback_days,
                    "min_observations": target_spec.beta_min_observations,
                    "beta": target_spec.market_beta,
                    "benchmark_latest_date": (
                        benchmark_frame[benchmark_frame.index <= as_of_ts].index.max().date().isoformat()
                        if benchmark_frame is not None
                        and not benchmark_frame[benchmark_frame.index <= as_of_ts].empty
                        else None
                    ),
                },
                "price_adjustment": "raw_pit_safe_for_a_share",
                "universe": {
                    "mode": "point_in_time_effective_interval" if universe_row else "explicit_targets",
                    "list_date": (universe_row or {}).get("list_date"),
                    "delist_date": (universe_row or {}).get("delist_date"),
                    "source": (universe_row or {}).get("source"),
                    "source_timestamp": (universe_row or {}).get("source_timestamp"),
                },
                "pit_enrichment": enrichment_lineage,
            },
        )
        self.store.save(row)
        return {
            "technical": True,
            "fundamental": bool(features.get("fundamental__available")),
            "fundamental_high_quality": bool(features.get("fundamental__high_quality")),
            "performance": bool(features.get("fundamental__performance_available")),
            "surprise": bool(features.get("fundamental__surprise_available")),
            "news": bool(features.get("news__official_available")),
            "industry": bool(features.get("meta__industry_pit_verified")),
            "valuation": any(key.startswith("valuation__") for key in features),
        }

    @staticmethod
    def _valuation_features(
        features: dict,
        current_price: float,
        lineage: dict,
    ) -> dict[str, float]:
        """Build PIT-safe valuation proxies from values published by ``as_of``."""
        report_date = str(
            ((lineage or {}).get("fundamental") or {}).get("report_date")
            or ((lineage or {}).get("performance") or {}).get("report_date")
            or ""
        )[:10]
        report_month = 12
        if report_date:
            try:
                report_month = datetime.fromisoformat(report_date).month
            except ValueError:
                report_month = 12
        annualization = {3: 4.0, 6: 2.0, 9: 4.0 / 3.0, 12: 1.0}.get(
            report_month, 1.0
        )
        eps = _finite_number(
            features.get("fundamental__basic_eps"),
            features.get("fundamental__flash_eps"),
        )
        book_value = _finite_number(features.get("fundamental__flash_book_value_per_share"))
        result: dict[str, float] = {}
        if eps is not None and eps > 0 and current_price > 0:
            annualized_eps = eps * annualization
            result["valuation__annualized_eps_proxy"] = round(annualized_eps, 6)
            result["valuation__pe_proxy"] = round(current_price / annualized_eps, 6)
            result["valuation__earnings_yield_pct"] = round(
                annualized_eps / current_price * 100.0, 6
            )
        if book_value is not None and book_value > 0 and current_price > 0:
            result["valuation__pb_proxy"] = round(current_price / book_value, 6)
        return result

    def _apply_research_v2_features(
        self,
        *,
        market: str,
        horizon: str,
        target_version: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, int]:
        """Add expanding-history and same-date cross-sectional PIT features."""
        rows = self.store.rows(
            market=market,
            horizon=horizon,
            target_version=target_version,
            feature_version=FEATURE_SCHEMA_VERSION,
            start_date=start_date,
            end_date=end_date,
            limit=1_000_000,
        )
        if not rows:
            return {}

        records = []
        for row in rows:
            features = row.get("features") or {}
            records.append({
                "feature_id": row["feature_id"],
                "symbol": row["symbol"],
                "as_of": row["as_of"],
                "industry": str(features.get("meta__industry") or "unknown"),
                "industry_verified": bool(features.get("meta__industry_pit_verified")),
                "return_20d": _finite_number(features.get("technical__return_20d_pct")),
                "netprofit_yoy": _finite_number(
                    features.get("fundamental__netprofit_yoy_pct"),
                    features.get("fundamental__flash_netprofit_yoy_pct"),
                    features.get("fundamental__guidance_netprofit_yoy_pct"),
                ),
                "pe": _finite_number(features.get("valuation__pe_proxy")),
                "pb": _finite_number(features.get("valuation__pb_proxy")),
            })
        frame = pd.DataFrame(records).sort_values(["as_of", "symbol"]).reset_index(drop=True)
        updates: dict[str, dict] = {item["feature_id"]: {} for item in records}

        for _, group in frame.groupby("symbol", sort=False):
            ordered = group.sort_values("as_of")
            for column, feature_name in (
                ("pe", "valuation__pe_expanding_percentile"),
                ("pb", "valuation__pb_expanding_percentile"),
            ):
                history: list[float] = []
                for item in ordered.itertuples():
                    value = getattr(item, column)
                    if pd.notna(value):
                        history.append(float(value))
                        if len(history) >= 5:
                            updates[item.feature_id][feature_name] = _percentile_rank(
                                history, float(value)
                            )

        for _, date_group in frame.groupby("as_of", sort=False):
            return_values = date_group["return_20d"].dropna()
            breadth = float((return_values > 0).mean()) if len(return_values) >= 3 else None
            for column, feature_name in (
                ("pe", "valuation__pe_cross_section_percentile"),
                ("pb", "valuation__pb_cross_section_percentile"),
            ):
                valid = date_group[date_group[column].notna()]
                if len(valid) >= 5:
                    ranks = valid[column].rank(method="average", pct=True)
                    for feature_id, value in zip(valid["feature_id"], ranks):
                        updates[feature_id][feature_name] = round(float(value), 6)
            if breadth is not None:
                for feature_id in date_group["feature_id"]:
                    updates[feature_id]["market__breadth_positive_20d"] = round(breadth, 6)

            verified = date_group[
                date_group["industry_verified"] & (date_group["industry"] != "unknown")
            ]
            for _, industry_group in verified.groupby("industry", sort=False):
                if len(industry_group) < 3:
                    continue
                valid_returns = industry_group["return_20d"].dropna()
                median_return = (
                    float(valid_returns.median()) if len(valid_returns) >= 3 else None
                )
                industry_breadth = (
                    float((valid_returns > 0).mean()) if len(valid_returns) >= 3 else None
                )
                for column, feature_name in (
                    ("return_20d", "industry__return_20d_rank_pct"),
                    ("netprofit_yoy", "industry__netprofit_yoy_rank_pct"),
                    ("pe", "industry__pe_rank_pct"),
                    ("pb", "industry__pb_rank_pct"),
                ):
                    valid = industry_group[industry_group[column].notna()]
                    if len(valid) < 3:
                        continue
                    ranks = valid[column].rank(method="average", pct=True)
                    for feature_id, value in zip(valid["feature_id"], ranks):
                        updates[feature_id][feature_name] = round(float(value), 6)
                for item in industry_group.itertuples():
                    if median_return is not None and pd.notna(item.return_20d):
                        updates[item.feature_id]["industry__return_20d_relative_pct"] = round(
                            float(item.return_20d) - median_return, 6
                        )
                    if industry_breadth is not None:
                        updates[item.feature_id]["industry__breadth_positive_20d"] = round(
                            industry_breadth, 6
                        )

        nonempty = [(key, value) for key, value in updates.items() if value]
        changed = self.store.update_features_many(
            nonempty,
            lineage_updates={
                "research_data_v2": {
                    "method": "expanding_history_and_same_as_of_cross_section",
                    "feature_version": FEATURE_SCHEMA_VERSION,
                    "point_in_time_verified": True,
                }
            },
        )
        counts: dict[str, int] = {"rows_updated": changed}
        for _, values in nonempty:
            for key in values:
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def _history_frame(
        self,
        symbol: str,
        cache: dict[str, pd.DataFrame],
        errors: dict[str, str],
        start: datetime,
        end: datetime,
        *,
        use_price_cache: bool = True,
    ) -> pd.DataFrame:
        key = str(symbol).upper()
        if key in cache:
            return cache[key]
        if key in errors:
            raise ValueError(errors[key])
        try:
            info = resolve_symbol(symbol)
            if use_price_cache:
                cached = self.price_cache.load(
                    info.symbol, info.market, start, end, allow_partial=True,
                )
                if cached is not None and not cached.empty:
                    cache[key] = cached
                    return cached
            frame = await self.price_fetcher.fetch_history_frame(
                symbol,
                start,
                end,
                point_in_time_safe=True,
            )
            if use_price_cache:
                self.price_cache.save(
                    info.symbol,
                    info.market,
                    frame,
                    source="price_fetcher.fetch_history_frame(point_in_time_safe=True)",
                )
            cache[key] = frame
            return frame
        except Exception as exc:
            errors[key] = str(exc)
            raise

    @staticmethod
    def _frame_outcome(
        asset_frame: pd.DataFrame,
        benchmark_frame: Optional[pd.DataFrame],
        as_of: pd.Timestamp,
        entry_price: float,
        target_spec,
    ) -> dict:
        future = asset_frame[asset_frame.index > as_of].sort_index().head(
            target_spec.horizon_trading_days
        )
        if len(future) < target_spec.horizon_trading_days:
            raise ValueError(
                f"未来交易日不足: {len(future)} < {target_spec.horizon_trading_days}"
            )
        changes = (future["close"].astype(float) / float(entry_price) - 1.0) * 100.0
        effective = changes
        benchmark_return = None
        residual_return = None
        target_type_used = "absolute_return"
        if (
            target_spec.target_type == "residual_return"
            and benchmark_frame is not None
            and target_spec.market_beta is not None
        ):
            benchmark_before = benchmark_frame[benchmark_frame.index <= as_of]
            if benchmark_before.empty:
                raise ValueError("基准在 as_of 前没有行情")
            benchmark_start = float(benchmark_before["close"].iloc[-1])
            benchmark_future = benchmark_frame[benchmark_frame.index > as_of]["close"].astype(float)
            benchmark_aligned = benchmark_future.reindex(changes.index, method="ffill").bfill()
            if benchmark_aligned.isna().any():
                raise ValueError("基准未来行情无法与标的交易日对齐")
            benchmark_changes = (benchmark_aligned / benchmark_start - 1.0) * 100.0
            effective = changes - float(target_spec.market_beta) * benchmark_changes
            benchmark_return = float(benchmark_changes.iloc[-1])
            residual_return = float(effective.iloc[-1])
            target_type_used = "residual_return"

        final_return = float(effective.iloc[-1])
        return {
            "validation_window": (
                f"{future.index[0].date().isoformat()}~{future.index[-1].date().isoformat()}"
            ),
            "actual_direction": direction_from_return(final_return, target_spec),
            "effective_fixed_return_pct": final_return,
            "fixed_horizon_return_pct": float(changes.iloc[-1]),
            "benchmark_return_pct": benchmark_return,
            "market_beta": target_spec.market_beta,
            "market_residual_return_pct": residual_return,
            "target_type_used": target_type_used,
        }

    @staticmethod
    def _dates(start: str, end: str, interval_days: int) -> list[datetime]:
        start_dt = datetime.fromisoformat(start[:10])
        end_dt = datetime.fromisoformat(end[:10])
        if end_dt < start_dt:
            raise ValueError("end_date 不能早于 start_date")
        values: list[datetime] = []
        current = start_dt
        step = timedelta(days=max(1, int(interval_days)))
        while current <= end_dt:
            values.append(current)
            current += step
        return values


def _finite_number(*values) -> Optional[float]:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if pd.notna(number) and number not in (float("inf"), float("-inf")):
            return number
    return None


def _percentile_rank(history: list[float], value: float) -> float:
    if not history:
        return 0.5
    below = sum(item < value for item in history)
    equal = sum(item == value for item in history)
    return round((below + 0.5 * equal) / len(history), 6)
