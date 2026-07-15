/**
 * Market Prediction — 前端应用
 * 处理用户交互、API 调用、结果渲染
 */

// ============================================================
// 全局状态
// ============================================================

const API_BASE = '';  // 同源部署
const CANVAS_FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif';
const DAY_MS = 24 * 60 * 60 * 1000;
const TECH_LOOP_RULE_MIN_SAMPLES = 20;
const TECH_LOOP_RULE_MIN_UNIQUE_CASES = 5;
const TECH_LOOP_STALE_WARNING_MS = 6 * 60 * 1000;
const TECH_LOOP_PROGRESS_STAGES = [
    { key: 'samples', label: '主动样本', detail: '构造训练集历史 K 线样本与技术面校准统计' },
    { key: 'candidate', label: 'LLM 候选', detail: '根据失败场景生成候选 prompt / skill' },
    { key: 'replay', label: 'Prompt Replay', detail: '对照 baseline 与 candidate 的逐条 LLM 回放' },
    { key: 'holdout', label: 'Holdout 门禁', detail: '在独立验证集检查方向、置信度与过度自信' },
    { key: 'registry', label: 'Registry', detail: '写入报告，并在通过门禁时可选晋升' },
];
let currentResult = null;
let agentsInfo = [];
let currentJobId = null;
let currentPollTimer = null;
let improvementState = null;
let skillRegistryState = null;
let modelRegistryState = null;
let quantState = null;
let priceTrendChartState = null;
let predictionTrackingChartState = null;
let passiveSampleState = { samples: [], summary: {}, help: [] };
let selectedPassiveSampleIds = new Set();
let lastProgressValue = 0;
let technicalLoopProgressTimer = null;

// ============================================================
// 初始化
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initInput();
    initQuickButtons();
    initImprovement();
    initSkillRegistry();
    initModels();
    initQuant();
    initModal();
    checkHealth();
    loadAgents();
});

// ============================================================
// Tab 切换
// ============================================================

function initTabs() {
    const links = document.querySelectorAll('.nav-link');
    links.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const tab = link.dataset.tab;

            // 更新 nav 状态
            links.forEach(l => l.classList.remove('active'));
            link.classList.add('active');

            // 切换内容
            document.querySelectorAll('.tab-content').forEach(tc => {
                tc.classList.remove('active');
            });
            document.getElementById(`tab-${tab}`).classList.add('active');

            // 加载对应数据
            if (tab === 'history') loadHistory();
            if (tab === 'agents') renderAgents();
            if (tab === 'improvement') loadImprovementStatus();
            if (tab === 'skills') loadSkillRegistry();
            if (tab === 'models') loadModels();
            if (tab === 'quant') loadQuantStatus();
        });
    });
}

// ============================================================
// 输入区
// ============================================================

function initInput() {
    const btn = document.getElementById('analyzeBtn');
    const cancelBtn = document.getElementById('cancelBtn');
    const input = document.getElementById('targetInput');

    btn.addEventListener('click', startAnalysis);
    cancelBtn.addEventListener('click', cancelAnalysis);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') startAnalysis();
    });
}

function initQuickButtons() {
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('targetInput').value = btn.dataset.target;
            if (btn.dataset.market) {
                document.getElementById('marketSelect').value = btn.dataset.market;
            }
            startAnalysis();
        });
    });
}

// ============================================================
// 分析流程
// ============================================================

async function startAnalysis() {
    if (currentJobId) return;

    const target = document.getElementById('targetInput').value.trim();
    if (!target) {
        showError('请输入股票代码或公司名称');
        return;
    }

    const timeframe = document.getElementById('timeframeSelect').value;
    const market = document.getElementById('marketSelect').value;

    // 收集跳过的 Agent
    const skipAgents = [];
    if (document.getElementById('skipNews').checked) skipAgents.push('news');
    if (document.getElementById('skipFundamental').checked) skipAgents.push('fundamental');
    if (document.getElementById('skipMacro').checked) skipAgents.push('macro');
    if (document.getElementById('skipIndustry').checked) skipAgents.push('industry');

    // 显示加载状态
    showLoading();
    hideResult();

    try {
        const response = await fetch(`${API_BASE}/api/analyze/async`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target, timeframe, market, skip_agents: skipAgents }),
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || '分析失败');
        }

        const job = await response.json();
        currentJobId = job.job_id;
        updateProgress(job.progress, job.message);
        pollAnalysisJob(job.job_id);
    } catch (error) {
        hideLoading();
        showError(error.message);
    }
}

async function pollAnalysisJob(jobId) {
    clearPollTimer();

    const poll = async () => {
        try {
            const response = await fetch(`${API_BASE}/api/jobs/${jobId}`);
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || '任务状态获取失败');
            }

            const job = await response.json();
            updateProgress(job.progress, job.message);

            if (job.status === 'completed') {
                clearPollTimer();
                currentJobId = null;
                currentResult = job.result;
                renderResult(job.result);
                hideLoading();
                showResult();
                return;
            }

            if (job.status === 'failed') {
                clearPollTimer();
                currentJobId = null;
                hideLoading();
                showError(job.error || '分析失败');
                return;
            }

            if (job.status === 'cancelled') {
                clearPollTimer();
                currentJobId = null;
                hideLoading();
                showError('分析任务已取消');
            }
        } catch (error) {
            clearPollTimer();
            currentJobId = null;
            hideLoading();
            showError(error.message);
        }
    };

    await poll();
    if (currentJobId === jobId) {
        currentPollTimer = setInterval(poll, 1200);
    }
}

async function cancelAnalysis() {
    if (!currentJobId) return;
    try {
        updateProgress(0, '正在取消任务...');
        await fetch(`${API_BASE}/api/jobs/${currentJobId}`, { method: 'DELETE' });
    } catch (error) {
        showError(`取消失败: ${error.message}`);
    }
}

function clearPollTimer() {
    if (currentPollTimer) {
        clearInterval(currentPollTimer);
        currentPollTimer = null;
    }
}

// ============================================================
// 渲染结果
// ============================================================

function renderResult(result) {
    // 综合预测
    const report = result.final_report || result;
    const dir = report.direction || 'neutral';
    const decisionBento = document.getElementById('decisionBento');
    if (decisionBento) {
        decisionBento.innerHTML = renderDecisionBento(report);
    }

    // 方向 badge
    const badge = document.getElementById('directionBadge');
    badge.className = `direction-badge ${dir}`;
    const dirMap = { bullish: '看涨', bearish: '看跌', neutral: '震荡' };
    badge.querySelector('.direction-text').textContent = dirMap[dir] || dir;

    // 幅度
    const mag = report.magnitude;
    const magEl = document.getElementById('magnitudeDisplay');
    magEl.className = `magnitude-display ${dir}`;
    if (mag) {
        const minStr = mag.min_pct >= 0 ? `+${mag.min_pct}%` : `${mag.min_pct}%`;
        const maxStr = mag.max_pct >= 0 ? `+${mag.max_pct}%` : `${mag.max_pct}%`;
        magEl.textContent = `${minStr} ~ ${maxStr}`;
    } else {
        magEl.textContent = 'N/A';
    }

    // 置信度
    const conf = report.confidence || 0;
    document.getElementById('confidenceFill').style.width = `${conf * 100}%`;
    document.getElementById('confidenceValue').textContent = `${Math.round(conf * 100)}%`;
    const targetStrip = document.getElementById('predictionTargetStrip');
    if (targetStrip) {
        const targetSpecHtml = renderPredictionTarget(report.prediction_target);
        targetStrip.innerHTML = `
            ${targetSpecHtml ? `
                <details class="target-spec-details">
                    <summary>预测目标规格</summary>
                    ${targetSpecHtml}
                </details>
            ` : ''}
        `;
    }

    // 标的
    const targetInfo = result.target_info || {};
    const targetLabel = targetInfo.display_name || result.resolved_target || result.target;
    const marketLabel = targetInfo.market ? ` · ${targetInfo.market}` : '';
    document.getElementById('reportTarget').textContent = `${targetLabel}${marketLabel} · ${result.timeframe}`;

    // 汇总文字
    const summaryEl = document.getElementById('reportSummary');
    summaryEl.innerHTML = formatSummary(report.summary || '综合分析完成');

    // 耗时
    document.getElementById('elapsedTime').textContent = `耗时 ${result.elapsed_seconds}s`;
    document.getElementById('predictionId').textContent = result.prediction_id
        ? `ID: ${result.prediction_id.slice(0, 8)}` : '';
    document.getElementById('generatedAt').textContent = result.generated_at
        ? `生成时间 ${formatDate(result.generated_at)}` : '';
    document.getElementById('resultDisclaimer').textContent = result.disclaimer || '本项目仅供学习和研究使用，不构成任何投资建议。';
    renderPriceTrend(
        result.price_trend || [],
        result.intraday_trend || [],
        result.intraday_meta || {},
        result.target_info || {},
        result.agent_results || [],
    );

    // Agent 结果
    renderAgentCards(result.agent_results);
    renderDataQuality(result.data_quality_summary || []);
    renderAgentAlerts(result.failed_agents || [], result.degraded_agents || []);

    // 分歧点
    if (report.disagreements && report.disagreements.length > 0) {
        const card = document.getElementById('disagreementsCard');
        card.style.display = 'block';
        const list = document.getElementById('disagreementsList');
        list.innerHTML = report.disagreements.map(d => `<li>${escapeHtml(d)}</li>`).join('');
    } else {
        document.getElementById('disagreementsCard').style.display = 'none';
    }

    // 风险提示
    if (report.key_risks && report.key_risks.length > 0) {
        const card = document.getElementById('risksCard');
        card.style.display = 'block';
        const list = document.getElementById('risksList');
        list.innerHTML = report.key_risks.map(r => `<li>${escapeHtml(r)}</li>`).join('');
    } else {
        document.getElementById('risksCard').style.display = 'none';
    }
}

function renderPriceTrend(dailyPoints, intradayPoints, intradayMeta, targetInfo, agentResults = []) {
    const card = document.getElementById('priceTrendCard');
    const chart = document.getElementById('priceTrendChart');
    const meta = document.getElementById('priceTrendMeta');
    const evidence = document.getElementById('priceTrendEvidence');
    const modeWrap = document.getElementById('trendMode');
    const intradayBtn = document.getElementById('trendModeIntraday');
    const dailyBtn = document.getElementById('trendModeDaily');

    const hasIntraday = Array.isArray(intradayPoints) && intradayPoints.length >= 2;
    const hasDaily = Array.isArray(dailyPoints) && dailyPoints.length >= 2;

    if (!hasIntraday && !hasDaily) {
        card.style.display = 'none';
        chart.innerHTML = '';
        meta.textContent = '';
        if (evidence) evidence.innerHTML = '';
        return;
    }

    card.style.display = 'block';
    const technicalSummary = extractTechnicalSummary(agentResults);
    const technicalSnapshot = technicalSummary?.technical_snapshot || null;
    const intradaySignals = technicalSummary?.intraday_signals || {};

    if (modeWrap) modeWrap.style.display = hasIntraday && hasDaily ? 'flex' : 'none';
    const defaultMode = hasDaily ? 'daily' : 'intraday';
    const draw = (mode) => {
        const useIntraday = mode === 'intraday' && hasIntraday;
        const points = useIntraday ? intradayPoints : dailyPoints;
        const validPoints = points.filter(p => Number.isFinite(Number(p.close)));
        if (validPoints.length < 2) return;

        if (intradayBtn && dailyBtn) {
            intradayBtn.classList.toggle('active', useIntraday);
            dailyBtn.classList.toggle('active', !useIntraday);
        }

        drawTrendChart(validPoints, targetInfo, {
            mode: useIntraday ? 'intraday' : 'daily',
            intradayMeta: intradayMeta || {},
            snapshot: technicalSnapshot,
        });
    };

    if (intradayBtn) intradayBtn.onclick = () => draw('intraday');
    if (dailyBtn) dailyBtn.onclick = () => draw('daily');
    draw(defaultMode);

    renderTrendEvidence(evidence, technicalSnapshot, intradayMeta || {}, intradaySignals);
}

function drawTrendChart(points, targetInfo, options = {}) {
    const chart = document.getElementById('priceTrendChart');
    const meta = document.getElementById('priceTrendMeta');
    const normalized = normalizeTrendPoints(points);
    if (normalized.length < 2) return;

    const closes = normalized.map(p => p.close);
    const first = closes[0];
    const last = closes[closes.length - 1];
    const totalChange = first ? ((last / first - 1) * 100) : 0;
    const trendClass = totalChange > 0 ? 'kline-up' : totalChange < 0 ? 'kline-down' : 'neutral';
    const firstLabel = pointLabel(normalized[0]);
    const lastLabel = pointLabel(normalized[normalized.length - 1]);
    const isCandlestick = options.mode === 'daily' && normalized.some(p => p.hasOhlc);
    const modeLabel = options.mode === 'intraday'
        ? `${options.intradayMeta?.interval || '5m'} 分钟`
        : '日线 K';

    meta.innerHTML = `
        <span>${escapeHtml(targetInfo.display_name || targetInfo.symbol || '')}</span>
        <span>${escapeHtml(modeLabel)}</span>
        <span class="${trendClass}">${formatSignedPct(totalChange)}</span>
        <span>${escapeHtml(firstLabel)} 至 ${escapeHtml(lastLabel)}</span>
    `;

    chart.innerHTML = `
        <div class="kline-stage">
            <canvas class="kline-canvas" aria-label="${isCandlestick ? 'K线图' : '走势线图'}"></canvas>
            <div class="kline-tooltip"></div>
        </div>
        <div class="kline-detail"></div>
    `;

    const canvas = chart.querySelector('.kline-canvas');
    const tooltip = chart.querySelector('.kline-tooltip');
    const detail = chart.querySelector('.kline-detail');
    const stage = chart.querySelector('.kline-stage');
    const state = {
        chart,
        stage,
        canvas,
        tooltip,
        detail,
        points: normalized,
        options,
        isCandlestick,
        hoverIndex: null,
        selectedIndex: normalized.length - 1,
        pointer: null,
        targetInfo,
    };
    priceTrendChartState = state;

    const drawCurrent = () => drawKlineCanvas(state);
    drawCurrent();
    renderKlineDetail(detail, normalized[state.selectedIndex], state);

    canvas.addEventListener('mousemove', event => updateKlinePointer(event, state, false));
    canvas.addEventListener('click', event => updateKlinePointer(event, state, true));
    canvas.addEventListener('mouseleave', () => {
        state.hoverIndex = null;
        state.pointer = null;
        tooltip.style.display = 'none';
        drawKlineCanvas(state);
    });
}

function normalizeTrendPoints(points) {
    return (points || [])
        .map(point => {
            const close = Number(point?.close);
            if (!Number.isFinite(close)) return null;
            const openRaw = Number(point?.open);
            const highRaw = Number(point?.high);
            const lowRaw = Number(point?.low);
            const open = Number.isFinite(openRaw) ? openRaw : close;
            const high = Number.isFinite(highRaw) ? highRaw : Math.max(open, close);
            const low = Number.isFinite(lowRaw) ? lowRaw : Math.min(open, close);
            const volume = Number(point?.volume);
            return {
                ...point,
                open,
                high: Math.max(high, open, close),
                low: Math.min(low, open, close),
                close,
                volume: Number.isFinite(volume) ? volume : 0,
                change_pct: Number(point?.change_pct),
                daily_change_pct: Number(point?.daily_change_pct),
                hasOhlc: Number.isFinite(openRaw) && Number.isFinite(highRaw) && Number.isFinite(lowRaw),
            };
        })
        .filter(Boolean);
}

function drawKlineCanvas(state) {
    const { canvas, points, options, isCandlestick } = state;
    const rect = state.stage.getBoundingClientRect();
    const width = Math.max(320, rect.width || 760);
    const height = Math.max(280, rect.height || 330);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const colors = getChartColors();
    const layout = buildKlineLayout(width, height);
    const maLines = isCandlestick ? [
        { label: 'MA5', color: colors.ma5, values: movingAverage(points, 5), width: 2.45 },
        { label: 'MA10', color: colors.ma10, values: movingAverage(points, 10), width: 2.25 },
        { label: 'MA20', color: colors.ma20, values: movingAverage(points, 20), width: 2.15 },
    ] : [];
    const priceValues = points.flatMap(p => [p.high, p.low, p.close]);
    maLines.forEach(line => {
        line.values.forEach(value => {
            if (Number.isFinite(value)) priceValues.push(value);
        });
    });
    const priceMinMax = paddedMinMax(priceValues, 0.08);
    const yTicks = buildNiceTicks(priceMinMax.min, priceMinMax.max, 5);
    const minPrice = yTicks[0];
    const maxPrice = yTicks[yTicks.length - 1];
    const maxVolume = Math.max(...points.map(p => p.volume || 0), 1);
    const step = layout.plotWidth / points.length;
    const bodyWidth = Math.max(4, Math.min(16, step * 0.62));

    state.layout = { ...layout, minPrice, maxPrice, maxVolume, step, bodyWidth };

    const xAt = index => layout.left + step * index + step / 2;
    const yPrice = value => layout.priceTop + (1 - ((value - minPrice) / (maxPrice - minPrice || 1))) * layout.priceHeight;
    const yVol = volume => layout.volumeBottom - (Math.max(0, volume) / maxVolume) * layout.volumeHeight;

    drawChartBackground(ctx, layout, colors);
    drawPriceGrid(ctx, layout, yTicks, yPrice, colors);
    drawXAxis(ctx, points, layout, step, options.mode, colors);
    drawReferenceLine(ctx, options.snapshot?.support_resistance?.nearest_support, '支撑', yPrice, layout, minPrice, maxPrice, colors.up);
    drawReferenceLine(ctx, options.snapshot?.support_resistance?.nearest_resistance, '压力', yPrice, layout, minPrice, maxPrice, colors.down);

    points.forEach((point, index) => {
        const x = xAt(index);
        const up = point.close >= point.open;
        const color = up ? colors.up : colors.down;
        const isLatest = index === points.length - 1;
        const volumeTop = yVol(point.volume);
        ctx.fillStyle = colorWithAlpha(color, 0.42);
        ctx.fillRect(x - bodyWidth / 2, volumeTop, bodyWidth, layout.volumeBottom - volumeTop);

        if (isCandlestick) {
            const highY = yPrice(point.high);
            const lowY = yPrice(point.low);
            const openY = yPrice(point.open);
            const closeY = yPrice(point.close);
            const bodyTop = Math.min(openY, closeY);
            const bodyHeight = Math.max(2, Math.abs(closeY - openY));
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.3;
            ctx.beginPath();
            ctx.moveTo(x, highY);
            ctx.lineTo(x, lowY);
            ctx.stroke();
            ctx.fillStyle = colorWithAlpha(color, up ? 0.9 : 0.72);
            ctx.fillRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
            ctx.strokeStyle = color;
            ctx.strokeRect(x - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
            if (isLatest) {
                drawLatestCandleHighlight(ctx, x, bodyTop, bodyHeight, bodyWidth, highY, lowY, colors);
            }
        }
    });

    if (!isCandlestick) {
        drawCloseLine(ctx, points, xAt, yPrice, colors.accent);
    } else {
        maLines.forEach(line => drawMovingAverage(ctx, line, xAt, yPrice));
        drawMaLegend(ctx, maLines, layout, colors);
    }

    drawTrackingPredictionOverlay(ctx, points, layout, colors, options.trackingPrediction);

    const activeIndex = state.hoverIndex ?? state.selectedIndex;
    if (activeIndex != null && points[activeIndex]) {
        drawCrosshair(ctx, points[activeIndex], activeIndex, state.pointer, xAt, yPrice, layout, colors);
    }

    const latest = points[points.length - 1];
    drawLatestPriceLabel(ctx, latest.close, yPrice(latest.close), layout, colors);
}

function buildKlineLayout(width, height) {
    const compact = width < 560;
    const left = compact ? 50 : 66;
    const right = compact ? 78 : 98;
    const top = 24;
    const bottom = 32;
    const volumeHeight = compact ? 50 : 62;
    const gap = 14;
    const volumeBottom = height - bottom;
    const volumeTop = volumeBottom - volumeHeight;
    const priceBottom = volumeTop - gap;
    return {
        left,
        right,
        top,
        bottom,
        priceTop: top,
        priceBottom,
        priceHeight: priceBottom - top,
        volumeTop,
        volumeBottom,
        volumeHeight,
        plotWidth: width - left - right,
        width,
        height,
    };
}

function getChartColors() {
    const styles = getComputedStyle(document.documentElement);
    return {
        bg: styles.getPropertyValue('--bg-primary').trim() || '#0f1923',
        grid: 'rgba(255,255,255,0.08)',
        axis: styles.getPropertyValue('--border').trim() || '#2a3f55',
        text: styles.getPropertyValue('--text-muted').trim() || '#5a6f80',
        primary: styles.getPropertyValue('--text-primary').trim() || '#e8edf2',
        accent: styles.getPropertyValue('--accent').trim() || '#4da6ff',
        up: '#ff4d5e',
        down: '#00c853',
        neutral: styles.getPropertyValue('--neutral').trim() || '#ffc107',
        current: '#f8fafc',
        ma5: '#ffd84d',
        ma10: '#2dd4ff',
        ma20: '#f472ff',
    };
}

function drawChartBackground(ctx, layout, colors) {
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, layout.width, layout.height);
    ctx.fillStyle = 'rgba(255,255,255,0.015)';
    ctx.fillRect(layout.left, layout.priceTop, layout.plotWidth, layout.priceHeight);
    ctx.fillRect(layout.left, layout.volumeTop, layout.plotWidth, layout.volumeHeight);
    ctx.strokeStyle = colors.axis;
    ctx.lineWidth = 1;
    ctx.strokeRect(layout.left, layout.priceTop, layout.plotWidth, layout.priceHeight);
    ctx.strokeRect(layout.left, layout.volumeTop, layout.plotWidth, layout.volumeHeight);
}

function drawPriceGrid(ctx, layout, ticks, yPrice, colors) {
    ctx.font = `11px ${CANVAS_FONT}`;
    ctx.textBaseline = 'middle';
    ticks.forEach(tick => {
        const y = yPrice(tick);
        ctx.strokeStyle = colors.grid;
        ctx.setLineDash([3, 5]);
        ctx.beginPath();
        ctx.moveTo(layout.left, y);
        ctx.lineTo(layout.width - layout.right, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = colors.text;
        ctx.textAlign = 'right';
        ctx.fillText(formatPriceTick(tick), layout.left - 8, y);
    });
}

function drawXAxis(ctx, points, layout, step, mode, colors) {
    const ticks = buildXAxisTicks(points, mode, layout.width < 560 ? 4 : 6);
    ctx.font = `11px ${CANVAS_FONT}`;
    ctx.textBaseline = 'top';
    ticks.forEach(item => {
        const x = layout.left + item.index * step + step / 2;
        ctx.strokeStyle = colors.axis;
        ctx.beginPath();
        ctx.moveTo(x, layout.volumeBottom);
        ctx.lineTo(x, layout.volumeBottom + 4);
        ctx.stroke();
        ctx.fillStyle = colors.text;
        ctx.textAlign = item.index === 0 ? 'left' : item.index === points.length - 1 ? 'right' : 'center';
        ctx.fillText(item.label, x, layout.volumeBottom + 10);
    });
}

function drawReferenceLine(ctx, value, label, yPrice, layout, minPrice, maxPrice, color) {
    const price = Number(value);
    if (!Number.isFinite(price) || price < minPrice || price > maxPrice) return;
    const y = yPrice(price);
    ctx.strokeStyle = colorWithAlpha(color, 0.8);
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(layout.left, y);
    ctx.lineTo(layout.width - layout.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = `11px ${CANVAS_FONT}`;
    ctx.fillStyle = color;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`${label} ${formatPriceTick(price)}`, layout.width - layout.right - 6, Math.max(layout.priceTop + 14, y - 4));
}

function drawCloseLine(ctx, points, xAt, yPrice, color) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    points.forEach((point, index) => {
        const x = xAt(index);
        const y = yPrice(point.close);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();
}

function drawMovingAverage(ctx, line, xAt, yPrice) {
    const drawPath = () => {
        ctx.beginPath();
        let started = false;
        line.values.forEach((value, index) => {
            if (!Number.isFinite(value)) return;
            const x = xAt(index);
            const y = yPrice(value);
            if (!started) {
                ctx.moveTo(x, y);
                started = true;
            } else {
                ctx.lineTo(x, y);
            }
        });
        return started;
    };
    ctx.save();
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    if (drawPath()) {
        ctx.strokeStyle = 'rgba(0,0,0,0.52)';
        ctx.lineWidth = (line.width || 2.2) + 2.8;
        ctx.stroke();
    }
    if (drawPath()) {
        ctx.strokeStyle = line.color;
        ctx.lineWidth = line.width || 2.2;
        ctx.stroke();
    }
    ctx.restore();
}

function drawMaLegend(ctx, maLines, layout, colors) {
    ctx.font = `12px ${CANVAS_FONT}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    let x = layout.left + 8;
    const y = layout.priceTop + 8;
    const items = maLines
        .map(line => {
            const latest = [...line.values].reverse().find(v => Number.isFinite(v));
            return Number.isFinite(latest) ? { ...line, text: `${line.label} ${latest.toFixed(2)}` } : null;
        })
        .filter(Boolean);
    const totalWidth = items.reduce((sum, item) => sum + ctx.measureText(item.text).width + 18, 0) + 8;
    ctx.fillStyle = colorWithAlpha(colors.bg, 0.78);
    ctx.fillRect(layout.left + 4, y - 5, Math.min(totalWidth, layout.plotWidth - 8), 24);
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.strokeRect(layout.left + 4, y - 5, Math.min(totalWidth, layout.plotWidth - 8), 24);
    items.forEach(item => {
        const text = item.text;
        ctx.fillStyle = item.color;
        ctx.fillText(text, x, y);
        x += ctx.measureText(text).width + 16;
    });
    ctx.fillStyle = colors.text;
}

function drawLatestCandleHighlight(ctx, x, bodyTop, bodyHeight, bodyWidth, highY, lowY, colors) {
    ctx.save();
    ctx.strokeStyle = colorWithAlpha(colors.current, 0.92);
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.strokeRect(
        x - bodyWidth / 2 - 2,
        bodyTop - 2,
        bodyWidth + 4,
        bodyHeight + 4,
    );
    ctx.strokeStyle = colorWithAlpha(colors.current, 0.62);
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(x, highY - 3);
    ctx.lineTo(x, lowY + 3);
    ctx.stroke();
    ctx.restore();
}

function drawTrackingPredictionOverlay(ctx, points, layout, colors, tracking) {
    if (!tracking || !points?.length) return;
    const prediction = tracking.prediction || {};
    const targetSpec = tracking.targetSpec || {};
    const predictedMin = Number(prediction.min_pct);
    const predictedMax = Number(prediction.max_pct);
    const returns = points
        .map(point => Number(point.effective_return_pct))
        .filter(Number.isFinite);
    if (!returns.length || !Number.isFinite(predictedMin) || !Number.isFinite(predictedMax)) return;

    const expected = Number(targetSpec.expected_return_pct);
    const upThreshold = Number(targetSpec.up_threshold_pct);
    const downThreshold = Number(targetSpec.down_threshold_pct);
    const values = [...returns, predictedMin, predictedMax, 0];
    if (Number.isFinite(expected)) values.push(expected);
    if (Number.isFinite(upThreshold)) values.push(upThreshold);
    if (Number.isFinite(downThreshold)) values.push(downThreshold);

    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const span = Math.max(rawMax - rawMin, 2);
    const yMin = rawMin - span * 0.18;
    const yMax = rawMax + span * 0.18;
    const yReturn = value => layout.priceTop
        + (1 - ((Number(value) - yMin) / (yMax - yMin || 1))) * layout.priceHeight;
    const step = layout.plotWidth / points.length;
    const xAt = index => layout.left + step * index + step / 2;
    const latestReturn = returns[returns.length - 1];
    const actualColor = latestReturn >= 0 ? colors.up : colors.down;
    const bandLow = Math.min(predictedMin, predictedMax);
    const bandHigh = Math.max(predictedMin, predictedMax);
    const bandTop = yReturn(bandHigh);
    const bandBottom = yReturn(bandLow);

    ctx.save();
    ctx.fillStyle = colorWithAlpha(colors.accent, 0.08);
    ctx.fillRect(
        layout.left,
        bandTop,
        layout.plotWidth,
        Math.max(2, bandBottom - bandTop),
    );

    drawTrackingReturnLine(ctx, layout, yReturn(0), '0%', colorWithAlpha(colors.text, 0.64), [2, 5]);
    drawTrackingReturnLine(ctx, layout, yReturn(bandHigh), `预测上沿 ${formatSignedPct(bandHigh)}`, colors.accent, [8, 4]);
    drawTrackingReturnLine(ctx, layout, yReturn(bandLow), `预测下沿 ${formatSignedPct(bandLow)}`, colors.accent, [8, 4]);
    if (Number.isFinite(expected)) {
        drawTrackingReturnLine(ctx, layout, yReturn(expected), `预期 ${formatSignedPct(expected)}`, colors.neutral, [4, 4]);
    }
    if (Number.isFinite(upThreshold)) {
        drawTrackingReturnLine(ctx, layout, yReturn(upThreshold), `涨阈 ${formatSignedPct(upThreshold)}`, colorWithAlpha(colors.up, 0.72), [3, 5], true);
    }
    if (Number.isFinite(downThreshold)) {
        drawTrackingReturnLine(ctx, layout, yReturn(downThreshold), `跌阈 ${formatSignedPct(downThreshold)}`, colorWithAlpha(colors.down, 0.72), [3, 5], true);
    }

    ctx.strokeStyle = actualColor;
    ctx.lineWidth = 2.2;
    ctx.setLineDash([]);
    ctx.beginPath();
    returns.forEach((value, index) => {
        const x = xAt(index);
        const y = yReturn(value);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    const latestX = xAt(returns.length - 1);
    const latestY = yReturn(latestReturn);
    ctx.fillStyle = actualColor;
    ctx.beginPath();
    ctx.arc(latestX, latestY, 4.5, 0, Math.PI * 2);
    ctx.fill();
    drawTrackingReturnTag(ctx, layout, latestY, `当前 ${formatSignedPct(latestReturn)}`, actualColor, 0);

    const title = '分钟收益 vs 预测区间';
    ctx.font = `12px ${CANVAS_FONT}`;
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    const titleWidth = ctx.measureText(title).width + 14;
    const titleX = Math.max(layout.left + 8, layout.width - layout.right - titleWidth - 10);
    const titleY = layout.priceTop + 8;
    ctx.fillStyle = colorWithAlpha(colors.bg, 0.76);
    ctx.fillRect(titleX - 5, titleY - 4, titleWidth, 22);
    ctx.strokeStyle = colorWithAlpha(colors.accent, 0.35);
    ctx.strokeRect(titleX - 5, titleY - 4, titleWidth, 22);
    ctx.fillStyle = colors.primary;
    ctx.fillText(title, titleX + 2, titleY);
    ctx.restore();
}

function drawTrackingReturnLine(ctx, layout, y, label, color, dash = [], faint = false) {
    if (!Number.isFinite(y) || y < layout.priceTop - 1 || y > layout.priceBottom + 1) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = faint ? 1 : 1.35;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(layout.left, y);
    ctx.lineTo(layout.width - layout.right, y);
    ctx.stroke();
    ctx.restore();
    if (!faint) {
        drawTrackingReturnTag(ctx, layout, y, label, color, label.includes('上沿') ? -1 : label.includes('下沿') ? 1 : 0);
    }
}

function drawTrackingReturnTag(ctx, layout, y, label, color, offsetSlot = 0) {
    if (!label) return;
    ctx.save();
    ctx.font = `11px ${CANVAS_FONT}`;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    const textWidth = ctx.measureText(label).width + 10;
    const x = layout.width - layout.right + 5;
    const yShift = offsetSlot * 12;
    const tagY = clamp(y + yShift, layout.priceTop + 11, layout.priceBottom - 11);
    ctx.fillStyle = colorWithAlpha('#0f1923', 0.86);
    ctx.fillRect(x, tagY - 10, textWidth, 20);
    ctx.strokeStyle = colorWithAlpha(color, 0.72);
    ctx.strokeRect(x, tagY - 10, textWidth, 20);
    ctx.fillStyle = color;
    ctx.fillText(label, x + 5, tagY);
    ctx.restore();
}

function drawCrosshair(ctx, point, index, pointer, xAt, yPrice, layout, colors) {
    const x = xAt(index);
    const y = pointer?.y && pointer.y >= layout.priceTop && pointer.y <= layout.volumeBottom
        ? pointer.y
        : yPrice(point.close);
    ctx.strokeStyle = 'rgba(232,237,242,0.36)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, layout.priceTop);
    ctx.lineTo(x, layout.volumeBottom);
    ctx.moveTo(layout.left, y);
    ctx.lineTo(layout.width - layout.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colorWithAlpha(point.close >= point.open ? colors.up : colors.down, 0.14);
    ctx.fillRect(x - Math.max(5, layout.bodyWidth / 2 + 2), layout.priceTop, Math.max(10, layout.bodyWidth + 4), layout.priceHeight);
}

function drawLatestPriceLabel(ctx, price, y, layout, colors) {
    const number = Number(price);
    if (!Number.isFinite(number)) return;
    const text = `当前 ${number.toFixed(2)}`;
    ctx.save();
    ctx.strokeStyle = colorWithAlpha(colors.current, 0.72);
    ctx.lineWidth = 1.35;
    ctx.setLineDash([2, 4]);
    ctx.beginPath();
    ctx.moveTo(layout.left, y);
    ctx.lineTo(layout.width - layout.right, y);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = `12px ${CANVAS_FONT}`;
    ctx.textBaseline = 'middle';
    const width = Math.min(ctx.measureText(text).width + 12, layout.right - 12);
    const x = layout.width - layout.right + 6;
    const top = Math.max(layout.priceTop + 10, Math.min(layout.priceBottom - 10, y));
    ctx.fillStyle = colorWithAlpha('#0f1923', 0.94);
    ctx.fillRect(x, top - 11, width, 22);
    ctx.strokeStyle = colorWithAlpha(colors.current, 0.9);
    ctx.strokeRect(x, top - 11, width, 22);
    ctx.fillStyle = colors.current;
    ctx.textAlign = 'left';
    ctx.fillText(text, x + 6, top);
    ctx.restore();
}

function updateKlinePointer(event, state, lockSelection) {
    const rect = state.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const layout = state.layout;
    if (!layout || x < layout.left || x > layout.width - layout.right || y < layout.priceTop || y > layout.volumeBottom) {
        state.tooltip.style.display = 'none';
        if (!lockSelection) {
            state.hoverIndex = null;
            state.pointer = null;
            drawKlineCanvas(state);
        }
        return;
    }
    const index = clamp(Math.floor((x - layout.left) / layout.step), 0, state.points.length - 1);
    state.hoverIndex = index;
    state.pointer = { x, y };
    if (lockSelection) {
        state.selectedIndex = index;
        renderKlineDetail(state.detail, state.points[index], state);
    }
    drawKlineCanvas(state);
    renderKlineTooltip(state, x, y, state.points[index]);
}

function renderKlineTooltip(state, x, y, point) {
    const tooltip = state.tooltip;
    const effective = Number(point?.effective_return_pct);
    const trackingLine = Number.isFinite(effective)
        ? `<span>有效 ${formatSignedPct(effective)} · 实际 ${formatSignedPct(point.actual_return_pct)}</span>`
        : '';
    tooltip.innerHTML = `
        <strong>${escapeHtml(pointLabel(point))}</strong>
        <span>开 ${formatNumber(point.open)} 高 ${formatNumber(point.high)}</span>
        <span>低 ${formatNumber(point.low)} 收 ${formatNumber(point.close)}</span>
        <span class="${point.close >= point.open ? 'kline-up' : 'kline-down'}">${formatSignedPct(klineDayChange(point))}</span>
        ${trackingLine}
    `;
    tooltip.style.display = 'block';
    const tooltipWidth = 178;
    const left = x > state.layout.width - tooltipWidth - 24 ? x - tooltipWidth - 14 : x + 14;
    const top = y > 96 ? y - 84 : y + 14;
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${Math.max(8, top)}px`;
}

function renderKlineDetail(container, point, state) {
    if (!container || !point) return;
    const amplitude = point.low ? ((point.high / point.low - 1) * 100) : 0;
    const ma5 = movingAverage(state.points, 5)[state.points.indexOf(point)];
    const ma10 = movingAverage(state.points, 10)[state.points.indexOf(point)];
    const ma20 = movingAverage(state.points, 20)[state.points.indexOf(point)];
    const prediction = state.options?.trackingPrediction?.prediction || {};
    const predictedMin = Number(prediction.min_pct);
    const predictedMax = Number(prediction.max_pct);
    const effectiveReturn = Number(point.effective_return_pct);
    const rangeLow = Math.min(predictedMin, predictedMax);
    const rangeHigh = Math.max(predictedMin, predictedMax);
    const rangeStatus = (
        Number.isFinite(effectiveReturn)
        && Number.isFinite(predictedMin)
        && Number.isFinite(predictedMax)
    )
        ? effectiveReturn < rangeLow
            ? `低于下沿 ${formatSignedPct(effectiveReturn - rangeLow)}`
            : effectiveReturn > rangeHigh
                ? `高于上沿 ${formatSignedPct(effectiveReturn - rangeHigh)}`
                : '位于预测区间'
        : '';
    const trackingMetrics = Number.isFinite(Number(point.effective_return_pct)) ? `
            ${klineMetric('有效收益', formatSignedPct(point.effective_return_pct))}
            ${point.actual_return_pct == null ? '' : klineMetric('实际收益', formatSignedPct(point.actual_return_pct))}
            ${point.benchmark_return_pct == null ? '' : klineMetric('基准收益', formatSignedPct(point.benchmark_return_pct))}
            ${Number.isFinite(predictedMin) && Number.isFinite(predictedMax) ? klineMetric('预测区间', `${formatSignedPct(predictedMin)} ~ ${formatSignedPct(predictedMax)}`) : ''}
            ${rangeStatus ? klineMetric('区间状态', rangeStatus) : ''}
    ` : '';
    container.innerHTML = `
        <div class="kline-detail-date">
            <span>${escapeHtml(pointLabel(point))}</span>
            <strong class="${point.close >= point.open ? 'kline-up' : 'kline-down'}">${formatSignedPct(klineDayChange(point))}</strong>
        </div>
        <div class="kline-detail-grid">
            ${klineMetric('开', formatNumber(point.open))}
            ${klineMetric('高', formatNumber(point.high))}
            ${klineMetric('低', formatNumber(point.low))}
            ${klineMetric('收', formatNumber(point.close))}
            ${klineMetric('振幅', formatSignedPct(amplitude, false))}
            ${klineMetric('成交量', formatVolume(point.volume))}
            ${klineMetric('MA5', Number.isFinite(ma5) ? ma5.toFixed(2) : 'N/A')}
            ${klineMetric('MA10', Number.isFinite(ma10) ? ma10.toFixed(2) : 'N/A')}
            ${klineMetric('MA20', Number.isFinite(ma20) ? ma20.toFixed(2) : 'N/A')}
            ${trackingMetrics}
        </div>
    `;
}

function klineMetric(label, value) {
    return `<div class="kline-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function movingAverage(points, windowSize) {
    return points.map((_, index) => {
        if (index + 1 < windowSize) return null;
        const slice = points.slice(index + 1 - windowSize, index + 1);
        const sum = slice.reduce((acc, point) => acc + point.close, 0);
        return sum / windowSize;
    });
}

function paddedMinMax(values, paddingRatio) {
    const finite = values.filter(value => Number.isFinite(value));
    const min = Math.min(...finite);
    const max = Math.max(...finite);
    const span = max - min || Math.max(Math.abs(max) * 0.02, 1);
    return { min: min - span * paddingRatio, max: max + span * paddingRatio };
}

function pointLabel(point) {
    return point?.time || point?.date || '';
}

function buildNiceTicks(min, max, count) {
    const span = max - min || 1;
    const step = niceNumber(span / Math.max(1, count - 1), true);
    const tickMin = Math.floor(min / step) * step;
    const tickMax = Math.ceil(max / step) * step;
    const ticks = [];
    for (let value = tickMin; value <= tickMax + step * 0.5; value += step) {
        ticks.push(Number(value.toFixed(6)));
        if (ticks.length > 8) break;
    }
    return ticks.length >= 2 ? ticks : [min, max];
}

function niceNumber(value, round) {
    const exponent = Math.floor(Math.log10(value || 1));
    const fraction = value / Math.pow(10, exponent);
    let niceFraction;
    if (round) {
        if (fraction < 1.5) niceFraction = 1;
        else if (fraction < 3) niceFraction = 2;
        else if (fraction < 7) niceFraction = 5;
        else niceFraction = 10;
    } else {
        if (fraction <= 1) niceFraction = 1;
        else if (fraction <= 2) niceFraction = 2;
        else if (fraction <= 5) niceFraction = 5;
        else niceFraction = 10;
    }
    return niceFraction * Math.pow(10, exponent);
}

function formatPriceTick(value) {
    const abs = Math.abs(value);
    if (abs >= 100) return value.toFixed(0);
    if (abs >= 10) return value.toFixed(2);
    return value.toFixed(3);
}

function buildXAxisTicks(points, mode, maxTicks = 5) {
    const count = Math.min(maxTicks, points.length);
    const indexes = new Set();
    for (let i = 0; i < count; i++) {
        indexes.add(Math.round((points.length - 1) * (i / Math.max(1, count - 1))));
    }
    return Array.from(indexes).sort((a, b) => a - b).map(index => ({
        index,
        label: formatXAxisLabel(points[index], mode, points),
    }));
}

function formatXAxisLabel(point, mode, allPoints) {
    const raw = pointLabel(point);
    if (!raw) return '';
    if (mode === 'intraday') {
        const firstDate = (allPoints[0]?.date || pointLabel(allPoints[0]).slice(0, 10));
        const date = point?.date || raw.slice(0, 10);
        const time = raw.includes(' ') ? raw.split(' ')[1] : raw.slice(11, 16);
        return date === firstDate ? time : `${date.slice(5)} ${time}`;
    }
    return raw.slice(5, 10);
}

function klineDayChange(point) {
    const explicit = Number(point?.daily_change_pct);
    if (Number.isFinite(explicit)) return explicit;
    return point?.open ? ((point.close / point.open - 1) * 100) : 0;
}

function formatSignedPct(value, includePlus = true) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 'N/A';
    const sign = number > 0 && includePlus ? '+' : '';
    return `${sign}${number.toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : 'N/A';
}

function formatVolume(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return 'N/A';
    if (number >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
    if (number >= 10000) return `${(number / 10000).toFixed(2)}万`;
    return number.toFixed(0);
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function colorWithAlpha(color, alpha) {
    const trimmed = String(color || '').trim();
    if (trimmed.startsWith('#')) {
        const hex = trimmed.slice(1);
        const normalized = hex.length === 3
            ? hex.split('').map(ch => ch + ch).join('')
            : hex;
        const int = parseInt(normalized, 16);
        if (Number.isFinite(int)) {
            const r = (int >> 16) & 255;
            const g = (int >> 8) & 255;
            const b = int & 255;
            return `rgba(${r},${g},${b},${alpha})`;
        }
    }
    return trimmed;
}

function extractTechnicalSummary(agentResults) {
    const tech = (agentResults || []).find(r => r?.data_summary?.technical_snapshot);
    return tech?.data_summary || null;
}

function extractTechnicalSnapshot(agentResults) {
    const tech = (agentResults || []).find(r =>
        (r.agent_name || '').includes('股价') || (r.agent_name || '').includes('技术')
    );
    return tech?.data_summary?.technical_snapshot || null;
}

function renderTrendEvidence(container, snapshot, intradayMeta = {}, intradaySignals = {}) {
    if (!container) return;
    if (!snapshot) {
        container.innerHTML = '';
        return;
    }

    const trend = snapshot.trend_regime || {};
    const volume = snapshot.volume_signals || {};
    const sr = snapshot.support_resistance || {};
    const risk = snapshot.risk_levels || {};
    const model = snapshot.confidence_model || {};

    const items = [
        { label: '趋势', value: formatTrendState(trend.short_term, trend.ma_alignment) },
        { label: '量能', value: formatVolumeState(volume) },
        { label: '分钟最新', value: intradayMeta.latest_time || '暂无' },
        { label: '支撑', value: formatPrice(sr.nearest_support, sr.support_distance_pct) },
        { label: '压力', value: formatPrice(sr.nearest_resistance, sr.resistance_distance_pct) },
        { label: '风险位', value: risk.stop_loss_reference ? `${risk.stop_loss_reference}` : 'N/A' },
        { label: '突破位', value: risk.breakout_reference ? `${risk.breakout_reference}` : 'N/A' },
        { label: '证据置信', value: model.technical_confidence != null ? `${Math.round(model.technical_confidence * 100)}%` : 'N/A' },
    ];

    items.splice(3, 0, { label: '盘中信号', value: formatIntradaySignal(intradaySignals) });

    const evidence = snapshot.evidence || {};
    const intradayEvidence = intradaySignals?.evidence || {};
    const bullets = [
        ...(evidence.bullish || []).slice(0, 2).map(text => ({ text, tone: 'bullish' })),
        ...(evidence.bearish || []).slice(0, 2).map(text => ({ text, tone: 'bearish' })),
        ...(intradayEvidence.bullish || []).slice(0, 1).map(text => ({ text: `盘中: ${text}`, tone: 'bullish' })),
        ...(intradayEvidence.bearish || []).slice(0, 1).map(text => ({ text: `盘中: ${text}`, tone: 'bearish' })),
        ...(evidence.neutral || []).slice(0, 2).map(text => ({ text, tone: 'neutral' })),
    ].slice(0, 5);

    container.innerHTML = `
        <div class="trend-evidence-grid">
            ${items.map(item => `
                <div class="trend-evidence-item">
                    <span>${escapeHtml(item.label)}</span>
                    <strong>${escapeHtml(item.value)}</strong>
                </div>
            `).join('')}
        </div>
        ${bullets.length ? `
            <div class="trend-evidence-bullets">
                ${bullets.map(item => `<span class="${item.tone}">${escapeHtml(item.text)}</span>`).join('')}
            </div>
        ` : ''}
    `;
}

function formatTrendState(shortTerm, maAlignment) {
    const trendMap = { up: '上行', down: '下行', sideways: '震荡', unknown: '未知' };
    const maMap = { bullish: '多头', bearish: '空头', tangled: '缠绕' };
    return `${trendMap[shortTerm] || shortTerm || '未知'} / ${maMap[maAlignment] || maAlignment || '未知'}`;
}

function formatVolumeState(volume) {
    const trendMap = { expanding: '放大', shrinking: '缩量', neutral: '中性' };
    const ratio = volume.volume_ratio_20d != null ? ` ${Number(volume.volume_ratio_20d).toFixed(2)}x` : '';
    return `${trendMap[volume.volume_trend] || volume.volume_trend || '未知'}${ratio}`;
}

function formatIntradaySignal(signals) {
    if (!signals || !signals.available) return '暂无';
    const stateMap = {
        strong_up: '盘中偏强',
        selloff: '盘中偏弱',
        mixed: '多空拉扯',
        range_bound: '窄幅震荡',
        unavailable: '暂无',
    };
    const change = Number(signals.change_pct);
    const changeText = Number.isFinite(change) ? ` ${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '';
    const vwap = Number(signals.latest_vs_vwap_pct);
    const vwapText = Number.isFinite(vwap) ? ` / 均价${vwap >= 0 ? '+' : ''}${vwap.toFixed(2)}%` : '';
    return `${stateMap[signals.state] || signals.state || '暂无'}${changeText}${vwapText}`;
}

function formatPrice(price, distance) {
    if (price == null) return 'N/A';
    const dist = distance == null ? '' : ` (${Number(distance).toFixed(2)}%)`;
    return `${Number(price).toFixed(2)}${dist}`;
}

function renderAgentCards(results) {
    const grid = document.getElementById('agentGrid');
    grid.innerHTML = '';

    results.forEach((r, i) => {
        const dir = r.direction || 'neutral';
        const status = getAgentStatus(r.agent_name);
        const card = document.createElement('div');
        card.className = `agent-card ${dir} ${status.status !== 'ok' ? 'degraded' : ''}`;
        card.style.animationDelay = `${i * 0.05}s`;

        const mag = r.magnitude;
        const magStr = mag
            ? `${mag.min_pct >= 0 ? '+' : ''}${mag.min_pct}% ~ ${mag.max_pct >= 0 ? '+' : ''}${mag.max_pct}%`
            : 'N/A';

        const dirLabel = { bullish: '看涨', bearish: '看跌', neutral: '震荡' };
        const conf = Math.round((r.confidence || 0) * 100);
        const targetHtml = renderAgentPredictionTarget(r.prediction_target);

        const factors = (r.key_factors || []).slice(0, 3)
            .map(f => `<span class="agent-factor">${escapeHtml(f)}</span>`).join('');
        const statusBadge = status.status !== 'ok'
            ? `<span class="agent-status ${status.status}">${formatAgentStatusLabel(status.status)}</span>`
            : '';

        card.innerHTML = `
            <div class="agent-card-header">
                <span class="agent-name">${escapeHtml(r.agent_name)}</span>
                <div class="agent-header-badges">
                    ${statusBadge}
                    <span class="agent-direction ${dir}">${dirLabel[dir] || dir}</span>
                </div>
            </div>
            <div class="agent-magnitude ${dir}">${magStr}</div>
            ${targetHtml}
            <div class="agent-confidence">
                <div class="agent-confidence-bar">
                    <div class="agent-confidence-fill" style="width:${conf}%"></div>
                </div>
                <span class="agent-confidence-value">${conf}%</span>
            </div>
            <div class="agent-reasoning">${escapeHtml(r.reasoning || '')}</div>
            <div class="agent-factors">${factors}</div>
        `;

        card.addEventListener('click', () => showAgentDetail(r));
        grid.appendChild(card);
    });
}

function getAgentStatus(agentName) {
    const status = (currentResult?.agent_statuses || []).find(s => s.agent_name === agentName);
    return status || { status: 'ok', reason: '完成' };
}

function renderDataQuality(items) {
    const card = document.getElementById('dataQualityCard');
    const list = document.getElementById('dataQualityList');
    if (!items.length) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    list.innerHTML = items.map(item => `
        <div class="quality-item">
            <div class="quality-agent">${escapeHtml(item.agent_name)}</div>
            <div class="quality-line"><span>来源</span>${escapeHtml(item.source)}</div>
            <div class="quality-line"><span>新鲜度</span>${escapeHtml(item.freshness)}</div>
            <div class="quality-line"><span>质量</span>${escapeHtml(item.quality)}</div>
            <div class="quality-note">${escapeHtml(item.note)}</div>
        </div>
    `).join('');
}

function formatAgentStatusLabel(status) {
    if (status === 'failed') return '失败';
    if (status === 'degraded') return '受限';
    return status || '完成';
}

function renderAgentAlerts(failedItems, degradedItems) {
    const card = document.getElementById('failedAgentsCard');
    const list = document.getElementById('failedAgentsList');
    const items = [...(failedItems || []), ...(degradedItems || [])];
    if (!items.length) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    list.innerHTML = items.map(item => `
        <div class="failure-item ${escapeHtml(item.status || '')}">
            <div class="failure-agent">${escapeHtml(item.agent_name)} · ${escapeHtml(formatAgentStatusLabel(item.status))}</div>
            <div class="failure-reason">${escapeHtml(item.reason)}</div>
        </div>
    `).join('');
}

// ============================================================
// Agent 详情模态框
// ============================================================

function showAgentDetail(result) {
    const modal = document.getElementById('detailModal');
    const title = document.getElementById('modalTitle');
    const body = document.getElementById('modalBody');

    title.textContent = `🔍 ${result.agent_name} — 详细分析`;

    const mag = result.magnitude;
    const magStr = mag
        ? `${mag.min_pct >= 0 ? '+' : ''}${mag.min_pct}% ~ ${mag.max_pct >= 0 ? '+' : ''}${mag.max_pct}%`
        : 'N/A';

    const factors = (result.key_factors || [])
        .map(f => `<li>${escapeHtml(f)}</li>`).join('');
    const risks = (result.risks || [])
        .map(r => `<li>${escapeHtml(r)}</li>`).join('');
    const status = getAgentStatus(result.agent_name);
    const dataSummary = result.data_summary && Object.keys(result.data_summary).length
        ? JSON.stringify(result.data_summary, null, 2)
        : '';
    const targetDetail = renderPredictionTarget(result.prediction_target);

    body.innerHTML = `
        ${status.status !== 'ok' ? `
        <div class="detail-section detail-warning">
            <h4>执行状态</h4>
            <p><strong>${escapeHtml(status.status)}</strong> · ${escapeHtml(status.reason)}</p>
        </div>` : ''}
        <div class="detail-section">
            <h4>预测结果</h4>
            <p><strong>方向:</strong> ${result.direction} | <strong>幅度:</strong> ${magStr} | <strong>置信度:</strong> ${Math.round((result.confidence || 0) * 100)}%</p>
            ${targetDetail}
        </div>
        <div class="detail-section">
            <h4>分析推理</h4>
            <div class="detail-reasoning">${escapeHtml(result.reasoning || '无推理过程')}</div>
        </div>
        ${factors ? `
        <div class="detail-section">
            <h4>关键因素</h4>
            <ul class="detail-factors-list">${factors}</ul>
        </div>` : ''}
        ${risks ? `
        <div class="detail-section">
            <h4>风险提示</h4>
            <ul class="detail-risks-list">${risks}</ul>
        </div>` : ''}
        ${dataSummary ? `
        <div class="detail-section">
            <h4>数据摘要</h4>
            <pre class="detail-json">${escapeHtml(dataSummary)}</pre>
        </div>` : ''}
    `;

    modal.style.display = 'flex';
}

function renderPredictionTarget(target) {
    const spec = normalizePredictionTarget(target);
    if (!spec) return '';
    const horizon = spec.horizon || `${spec.horizon_trading_days || 5}d`;
    const expected = formatSignedPct(spec.expected_return_pct);
    const up = formatSignedPct(spec.up_threshold_pct);
    const down = formatSignedPct(spec.down_threshold_pct);
    const mode = spec.evaluation_mode === 'fixed_horizon_and_barrier'
        ? '到期+区间'
        : (spec.evaluation_mode || '到期');
    const targetType = targetTypeLabel(spec.target_type);
    return `
        <div class="target-spec-row">
            <span>${escapeHtml(horizon)}</span>
            <span>${escapeHtml(mode)}</span>
            <span>${escapeHtml(targetType)}</span>
            <span>预期 ${escapeHtml(expected)}</span>
            <span>上 ${escapeHtml(up)} / 下 ${escapeHtml(down)}</span>
            ${renderProbabilityTriplet(spec)}
        </div>
    `;
}

function renderDecisionEdge(report = {}) {
    const expected = report.expected_excess_return_pct ?? report.prediction_target?.expected_return_pct;
    const probUp = report.prob_up ?? report.prediction_target?.prob_up;
    const probDown = report.prob_down ?? report.prediction_target?.prob_down;
    const probNoEdge = report.prob_no_edge ?? report.prediction_target?.prob_neutral;
    const edge = Number(report.edge_score);
    const decision = decisionLabel(report.decision);
    const reason = reasonLabel(report.no_trade_reason || report.neutral_reason);
    return `
        <div class="edge-spec-row">
            <span class="edge-decision ${escapeHtml(report.decision || 'observe')}">${escapeHtml(decision)}</span>
            <span>预期超额 ${escapeHtml(formatSignedPct(expected))}</span>
            <span>边际 ${Number.isFinite(edge) ? `${Math.round(edge * 100)}%` : 'N/A'}</span>
            <span class="target-probs">涨 ${formatProbability(probUp) || 'N/A'} · 跌 ${formatProbability(probDown) || 'N/A'} · 无边际 ${formatProbability(probNoEdge) || 'N/A'}</span>
            ${reason ? `<span class="edge-reason">${escapeHtml(reason)}</span>` : ''}
        </div>
    `;
}

function renderDecisionBento(report = {}) {
    const target = normalizePredictionTarget(report.prediction_target) || {};
    const expected = report.expected_excess_return_pct ?? target.expected_return_pct;
    const probUp = report.prob_up ?? target.prob_up;
    const probDown = report.prob_down ?? target.prob_down;
    const probNoEdge = report.prob_no_edge ?? target.prob_neutral;
    const edge = Number(report.edge_score);
    const decision = report.decision || inferDecisionFromReport(report);
    const reason = decisionReason(report, decision);
    const horizon = decisionHorizonLabel(target);
    const p10 = report.expected_return_p10 ?? target.expected_return_p10;
    const p50 = report.expected_return_p50 ?? target.expected_return_p50 ?? expected;
    const p90 = report.expected_return_p90 ?? target.expected_return_p90;
    const decisionClass = safeClassName(decision || 'observe');

    return `
        <div class="decision-primary">
            <div class="decision-kicker">${escapeHtml(horizon)}</div>
            <div class="decision-expected ${Number(expected) >= 0 ? 'positive' : 'negative'}">
                ${escapeHtml(formatSignedPct(expected))}
            </div>
            <div class="decision-row">
                <span class="decision-pill ${decisionClass}">${escapeHtml(decisionLabel(decision))}</span>
                <span class="edge-chip">Edge ${Number.isFinite(edge) ? `${Math.round(edge * 100)}%` : 'N/A'}</span>
            </div>
            <div class="decision-quantiles">
                <span>P10 ${escapeHtml(formatSignedPct(p10))}</span>
                <span>P50 ${escapeHtml(formatSignedPct(p50))}</span>
                <span>P90 ${escapeHtml(formatSignedPct(p90))}</span>
            </div>
        </div>
        <div class="decision-prob-panel">
            <div class="decision-prob-grid">
                ${renderProbabilityMetric('上涨概率', probUp, 'up')}
                ${renderProbabilityMetric('下跌概率', probDown, 'down')}
                ${renderProbabilityMetric('无边际概率', probNoEdge, 'neutral')}
            </div>
            <div class="decision-reason">
                <span>原因</span>
                <strong>${escapeHtml(reason)}</strong>
            </div>
        </div>
    `;
}

function renderProbabilityMetric(label, value, tone) {
    const num = Number(value);
    const pct = Number.isFinite(num) ? Math.round(num * 100) : null;
    const width = pct == null ? 0 : clamp(pct, 0, 100);
    return `
        <div class="decision-prob ${escapeHtml(tone)}">
            <div class="decision-prob-top">
                <span>${escapeHtml(label)}</span>
                <strong>${pct == null ? 'N/A' : `${pct}%`}</strong>
            </div>
            <div class="decision-prob-bar"><i style="width:${width}%"></i></div>
        </div>
    `;
}

function decisionHorizonLabel(spec = {}) {
    const days = Number(spec.horizon_trading_days);
    if (Number.isFinite(days) && days > 0) return `${days}日超额收益预期`;
    if (spec.horizon) return `${spec.horizon}超额收益预期`;
    return '超额收益预期';
}

function inferDecisionFromReport(report = {}) {
    const direction = report.direction || 'neutral';
    const edge = Number(report.edge_score);
    if (direction === 'bullish' && Number.isFinite(edge) && edge >= 0.35) return 'long_bias';
    if (direction === 'bearish' && Number.isFinite(edge) && edge >= 0.35) return 'short_bias';
    if (Number.isFinite(edge) && edge >= 0.22) return 'watchlist';
    return 'observe';
}

function decisionReason(report = {}, decision = '') {
    const rawReason = report.no_trade_reason || report.neutral_reason;
    const mapped = reasonLabel(rawReason);
    if (rawReason === 'priced_in') return '利好或利空已部分定价，当前需要等待新的超额收益边际。';
    if (rawReason === 'conflict') return '多空证据冲突，概率分布尚未形成足够清晰的操作边际。';
    if (rawReason === 'data_insufficient') return '关键证据不足，当前结论更适合观察而不是直接行动。';
    if (rawReason === 'no_edge') return '预期超额收益没有明显超过操作阈值。';
    if (mapped) return mapped;
    if (decision === 'long_bias') return '上涨概率和预期超额收益略有边际，但仍需观察证据确认。';
    if (decision === 'short_bias') return '下行概率和预期超额收益风险更突出，短期应控制暴露。';
    if (decision === 'watchlist') return '存在一定边际，但证据强度未达到明确操作门槛。';
    if (decision === 'avoid') return '风险收益不匹配，当前缺少可操作边际。';
    return '利好利空仍需验证，当前以观望为主。';
}

function safeClassName(value) {
    return String(value || '')
        .toLowerCase()
        .replace(/[^a-z0-9_-]/g, '-');
}

function decisionLabel(value) {
    const map = {
        long_bias: '轻度看多',
        short_bias: '轻度看空',
        watchlist: '观察名单',
        observe: '观望',
        avoid: '避开',
    };
    return map[value] || value || '观望';
}

function reasonLabel(value) {
    const map = {
        no_edge: '无足够收益边际',
        conflict: '证据冲突',
        data_insufficient: '数据不足',
        priced_in: '已定价风险',
    };
    return map[value] || '';
}

function renderAgentPredictionTarget(target) {
    const spec = normalizePredictionTarget(target);
    if (!spec) return '';
    return `
        <div class="agent-target">
            <span>${escapeHtml(spec.horizon || '')}</span>
            <span>预期 ${escapeHtml(formatSignedPct(spec.expected_return_pct))}</span>
            ${renderProbabilityTriplet(spec)}
        </div>
    `;
}

function renderProbabilityTriplet(spec) {
    const up = formatProbability(spec.prob_up);
    const neutral = formatProbability(spec.prob_neutral);
    const down = formatProbability(spec.prob_down);
    if (!up && !neutral && !down) return '';
    return `<span class="target-probs">涨 ${up || 'N/A'} · 无边际 ${neutral || 'N/A'} · 跌 ${down || 'N/A'}</span>`;
}

function normalizePredictionTarget(target) {
    return target && typeof target === 'object' ? target : null;
}

function targetTypeLabel(value) {
    const map = {
        residual_return: '滚动 Beta 市场残差',
        excess_return: '相对收益',
        absolute_return: '绝对收益',
    };
    return map[value] || value || '收益';
}

function formatSignedPct(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'N/A';
    return `${num >= 0 ? '+' : ''}${num.toFixed(1)}%`;
}

function formatProbability(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return '';
    return `${Math.round(num * 100)}%`;
}

function initModal() {
    const modal = document.getElementById('detailModal');
    document.getElementById('modalClose').addEventListener('click', () => {
        modal.style.display = 'none';
    });
    modal.querySelector('.modal-overlay').addEventListener('click', () => {
        modal.style.display = 'none';
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') modal.style.display = 'none';
    });
}

// ============================================================
// 历史记录
// ============================================================

async function loadHistory() {
    const list = document.getElementById('historyList');
    list.innerHTML = '<div class="history-state">正在加载历史预测...</div>';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);

    try {
        const resp = await fetch(`${API_BASE}/api/history?limit=50`, { signal: controller.signal });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.detail || `历史记录接口异常 (${resp.status})`);
        }

        if (!data.history || data.history.length === 0) {
            list.innerHTML = '<div class="history-state">暂无历史记录</div>';
            return;
        }

        list.innerHTML = data.history.map(h => {
            const dir = h.direction || 'neutral';
            const conf = Math.round((h.confidence || 0) * 100);
            const edge = Number(h.edge_score);
            const edgeText = Number.isFinite(edge) ? `${Math.round(edge * 100)}%` : 'N/A';
            const expectedText = h.expected_excess_return_pct == null ? 'N/A' : formatSignedPct(h.expected_excess_return_pct);
            const probNoEdge = h.prob_no_edge == null ? 'N/A' : `${Math.round(Number(h.prob_no_edge) * 100)}%`;
            const brierText = h.brier_score == null ? '' : ` · Brier ${Number(h.brier_score).toFixed(3)}`;
            const date = h.predicted_at ? h.predicted_at.slice(0, 16).replace('T', ' ') : 'N/A';
            const title = h.target_name ? `${h.target_name}(${h.target})` : h.target;
            const verified = h.verified ? '<span class="history-verified">已验证</span>' : '<span class="history-verified pending">待验证</span>';
            return `
                <div class="history-item" data-id="${h.id}">
                    <div class="history-info">
                        <div class="history-direction ${dir}"></div>
                        <div>
                            <div class="history-target">${escapeHtml(title)}</div>
                            <div class="history-meta">${escapeHtml(h.timeframe)} · ${date} · ${verified}</div>
                            <div class="history-v2-line">
                                ${escapeHtml(decisionLabel(h.decision || 'observe'))} · 预期 ${expectedText} · P无边际 ${probNoEdge}${brierText}
                            </div>
                        </div>
                    </div>
                    <div class="history-confidence">
                        <div class="history-confidence-value" style="color:var(--${dir === 'bullish' ? 'bullish' : dir === 'bearish' ? 'bearish' : 'neutral'})">${edgeText}</div>
                        <div class="history-confidence-label">边际 · 置信${conf}%</div>
                    </div>
                </div>
            `;
        }).join('');

        // 点击加载详情
        list.querySelectorAll('.history-item').forEach(item => {
            item.addEventListener('click', () => loadHistoryDetail(item.dataset.id));
        });
    } catch (e) {
        const message = e.name === 'AbortError' ? '加载超时，历史记录库可能较大或服务正忙。' : `加载失败: ${e.message}`;
        list.innerHTML = `
            <div class="history-state error">
                <span>${escapeHtml(message)}</span>
                <button type="button" class="history-retry-btn" id="historyRetryBtn">重试</button>
            </div>
        `;
        document.getElementById('historyRetryBtn')?.addEventListener('click', loadHistory);
    } finally {
        clearTimeout(timeout);
    }
}

async function loadHistoryDetail(id) {
    try {
        const resp = await fetch(`${API_BASE}/api/history/${id}`);
        if (!resp.ok) throw new Error('记录不存在');
        const data = await resp.json();

        const modal = document.getElementById('detailModal');
        const title = document.getElementById('modalTitle');
        const body = document.getElementById('modalBody');

        title.textContent = `${data.target} — 历史预测详情`;
        const failedAgents = (data.agents_failed || [])
            .map(name => `<span class="history-agent-chip failed">${escapeHtml(name)}</span>`)
            .join('');
        const usedAgents = (data.agents_used || [])
            .map(name => `<span class="history-agent-chip">${escapeHtml(name)}</span>`)
            .join('');
        const reportBlock = data.report_md
            ? `<pre class="history-report-md">${escapeHtml(data.report_md)}</pre>`
            : `<div class="detail-reasoning">${formatSummary(data.summary || '暂无完整报告')}</div>`;
        const edgeScore = Number(data.edge_score);
        const edgeText = Number.isFinite(edgeScore) ? `${Math.round(edgeScore * 100)}%` : 'N/A';
        const verificationV2 = data.verified
            ? `<p><strong>真实有效收益:</strong> ${formatSignedPct(data.actual_effective_return_pct ?? data.actual_change_pct)} | <strong>Brier:</strong> ${data.brier_score == null ? 'N/A' : Number(data.brier_score).toFixed(4)} | <strong>Edge:</strong> ${data.edge_hit ? '命中' : '未命中'}</p>`
            : '<p><em>尚未验证</em></p>';

        body.innerHTML = `
            <div class="disclaimer-banner modal-disclaimer">${escapeHtml(data.disclaimer || '本项目仅供学习和研究使用，不构成任何投资建议。')}</div>
            <div class="detail-section">
                <h4>预测信息</h4>
                <p><strong>标的:</strong> ${escapeHtml(data.target)} | <strong>周期:</strong> ${escapeHtml(data.timeframe)}</p>
                <p><strong>方向:</strong> ${escapeHtml(data.direction)} | <strong>置信度:</strong> ${Math.round((data.confidence || 0) * 100)}%</p>
                <p><strong>边际判断:</strong> ${escapeHtml(decisionLabel(data.decision || 'observe'))} | <strong>Edge:</strong> ${edgeText} | <strong>预期超额收益:</strong> ${data.expected_excess_return_pct == null ? 'N/A' : formatSignedPct(data.expected_excess_return_pct)}</p>
                <p><strong>概率分布:</strong> P涨 ${data.prob_up == null ? 'N/A' : Math.round(Number(data.prob_up) * 100) + '%'} | P跌 ${data.prob_down == null ? 'N/A' : Math.round(Number(data.prob_down) * 100) + '%'} | P无边际 ${data.prob_no_edge == null ? 'N/A' : Math.round(Number(data.prob_no_edge) * 100) + '%'}</p>
                <p><strong>预测时间:</strong> ${formatDate(data.predicted_at)} | <strong>有效期:</strong> ${formatDate(data.valid_until)}</p>
                <p><strong>耗时:</strong> ${data.elapsed_seconds || 0}s | <strong>模型:</strong> ${escapeHtml(data.llm_model || 'N/A')}</p>
                ${verificationV2}
                <button type="button" class="secondary-btn compact-btn" id="refreshPredictionTrackingBtn">刷新真实走势对比</button>
            </div>
            <div class="detail-section prediction-tracking-section" id="predictionTrackingSection" style="display:none">
                <h4>预测后真实走势</h4>
                <div id="predictionTrackingBody"></div>
            </div>
            <div class="detail-section">
                <h4>参与 Agent</h4>
                <div class="history-agent-list">${usedAgents || '<span class="history-agent-chip">无记录</span>'}</div>
            </div>
            ${failedAgents ? `
            <div class="detail-section">
                <h4>失败或降级 Agent</h4>
                <div class="history-agent-list">${failedAgents}</div>
            </div>` : ''}
            <div class="detail-section">
                <h4>完整报告</h4>
                ${reportBlock}
            </div>
        `;
        const trackingBtn = document.getElementById('refreshPredictionTrackingBtn');
        if (trackingBtn) trackingBtn.addEventListener('click', () => loadPredictionTracking(data.id));
        modal.style.display = 'flex';
    } catch (e) {
        showError(e.message);
    }
}

async function loadPredictionTracking(id) {
    const section = document.getElementById('predictionTrackingSection');
    const body = document.getElementById('predictionTrackingBody');
    const btn = document.getElementById('refreshPredictionTrackingBtn');
    if (!section || !body) return;
    section.style.display = 'block';
    body.innerHTML = '<div class="empty-inline">正在刷新真实股价走势...</div>';
    setButtonBusy(btn, true, '刷新中...');
    try {
        const resp = await fetch(`${API_BASE}/api/history/${encodeURIComponent(id)}/tracking`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '刷新走势失败');
        }
        const data = await resp.json();
        renderPredictionTracking(body, data);
    } catch (e) {
        body.innerHTML = `<div class="empty-state error">刷新失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        setButtonBusy(btn, false);
    }
}

function renderPredictionTracking(container, data) {
    const summary = data.summary || {};
    const prediction = data.prediction || {};
    const targetSpec = data.target_spec || {};
    const dailyPoints = data.points || [];
    const intradayPoints = data.intraday_points || [];
    const intradayMeta = data.intraday_meta || {};
    const hasIntraday = Array.isArray(intradayPoints)
        && intradayPoints.some(p => Number.isFinite(Number(p?.close)));
    const hasDaily = Array.isArray(dailyPoints) && dailyPoints.length > 0;
    const defaultMode = hasIntraday ? 'intraday' : 'daily';
    const intradayReason = intradayMeta.reason ? ` 分钟视图：${escapeHtml(intradayMeta.reason)}` : '';
    const dailyEvalText = summary.target_type_used === 'excess_return'
        ? '日线曲线按相对基准超额收益评估。'
        : '日线曲线按绝对收益评估。';
    const intradayEvalText = hasIntraday
        ? (intradayMeta.target_type_used === 'excess_return'
            ? '分钟曲线按相对基准超额收益评估。'
            : '分钟曲线按绝对收益评估。')
        : '';
    container.innerHTML = `
        <div class="tracking-summary">
            ${metricCard('当前实际', formatSignedPct(summary.latest_actual_return_pct))}
            ${metricCard('当前有效收益', formatSignedPct(summary.latest_effective_return_pct))}
            ${metricCard('预期超额', summary.expected_excess_return_pct == null ? 'N/A' : formatSignedPct(summary.expected_excess_return_pct))}
            ${metricCard('当前 Brier', summary.brier_score_so_far == null ? 'N/A' : Number(summary.brier_score_so_far).toFixed(3))}
            ${metricCard('预测区间', prediction.min_pct == null ? 'N/A' : `${formatSignedPct(prediction.min_pct)} ~ ${formatSignedPct(prediction.max_pct)}`)}
            ${metricCard('方向至今', summary.correct_so_far ? '暂时正确' : '暂时偏离')}
        </div>
        <div class="tracking-toolbar">
            <div class="trend-mode tracking-mode" style="${hasIntraday && hasDaily ? '' : 'display:none'}">
                <button type="button" class="trend-mode-btn" id="trackingModeIntraday" ${hasIntraday ? '' : 'disabled'}>分钟</button>
                <button type="button" class="trend-mode-btn" id="trackingModeDaily" ${hasDaily ? '' : 'disabled'}>日线</button>
            </div>
            <div class="trend-meta tracking-meta" id="predictionTrackingMeta"></div>
        </div>
        <div class="tracking-chart-wrap" id="predictionTrackingChart">
        </div>
        <div class="tracking-note">
            ${escapeHtml(summary.tuning_hint || '')}
            ${dailyEvalText}
            ${intradayEvalText}
            ${intradayReason}
        </div>
    `;
    const chart = container.querySelector('#predictionTrackingChart');
    const meta = container.querySelector('#predictionTrackingMeta');
    const intradayBtn = container.querySelector('#trackingModeIntraday');
    const dailyBtn = container.querySelector('#trackingModeDaily');
    const draw = (mode) => {
        const useIntraday = mode === 'intraday' && hasIntraday;
        if (intradayBtn && dailyBtn) {
            intradayBtn.classList.toggle('active', useIntraday);
            dailyBtn.classList.toggle('active', !useIntraday);
        }
        if (useIntraday) {
            drawPredictionTrackingKline(chart, meta, intradayPoints, prediction, summary, {
                mode: 'intraday',
                intradayMeta,
                targetSpec,
                forceCandlestick: true,
            });
        } else {
            if (meta) {
                const first = dailyPoints[0] || {};
                const last = dailyPoints[dailyPoints.length - 1] || {};
                meta.innerHTML = `
                    <span>日线收益</span>
                    <span>${escapeHtml(first.date || '')} 至 ${escapeHtml(last.date || '')}</span>
                    <span>${dailyPoints.length} 个交易点</span>
                `;
            }
            predictionTrackingChartState = null;
            chart.innerHTML = drawPredictionTrackingSvg(dailyPoints, prediction, summary);
        }
    };
    if (intradayBtn) intradayBtn.onclick = () => draw('intraday');
    if (dailyBtn) dailyBtn.onclick = () => draw('daily');
    draw(defaultMode);
}

function drawPredictionTrackingKline(chart, meta, points, prediction, summary, options = {}) {
    if (!chart) return;
    const normalized = normalizeTrendPoints((points || []).map(point => ({
        ...point,
        time: point.time || point.date,
        date: point.date || String(point.time || '').slice(0, 10),
        change_pct: point.effective_return_pct,
        daily_change_pct: point.effective_return_pct,
    })));
    if (normalized.length < 1) {
        chart.innerHTML = '<div class="empty-inline">暂无可画分钟走势点</div>';
        if (meta) meta.innerHTML = '';
        return;
    }

    const latest = normalized[normalized.length - 1];
    const first = normalized[0];
    const effective = Number(latest.effective_return_pct);
    const trendClass = effective > 0 ? 'bullish' : effective < 0 ? 'bearish' : 'neutral';
    const interval = options.intradayMeta?.interval || '5m';
    const source = options.intradayMeta?.source || 'intraday';
    if (meta) {
        meta.innerHTML = `
            <span>${escapeHtml(interval)} 分钟K · 预测对比</span>
            <span>${escapeHtml(source)}</span>
            <span class="${trendClass}">有效 ${formatSignedPct(effective)}</span>
            <span>${escapeHtml(pointLabel(first))} 至 ${escapeHtml(pointLabel(latest))}</span>
        `;
    }

    chart.innerHTML = `
        <div class="kline-stage tracking-kline-stage">
            <canvas class="kline-canvas" aria-label="预测跟踪分钟K线"></canvas>
            <div class="kline-tooltip"></div>
        </div>
        ${renderPredictionTrackingComparison(latest, prediction, summary, options)}
        <div class="kline-detail"></div>
    `;

    const canvas = chart.querySelector('.kline-canvas');
    const tooltip = chart.querySelector('.kline-tooltip');
    const detail = chart.querySelector('.kline-detail');
    const stage = chart.querySelector('.kline-stage');
    const hasRealOhlc = normalized.some(point => point.hasOhlc && (
        point.high !== point.low || point.open !== point.close
    ));
    const state = {
        chart,
        stage,
        canvas,
        tooltip,
        detail,
        points: normalized,
        options: {
            mode: options.mode || 'intraday',
            intradayMeta: options.intradayMeta || {},
            snapshot: null,
            trackingPrediction: {
                prediction,
                summary,
                targetSpec: options.targetSpec || {},
                intradayMeta: options.intradayMeta || {},
            },
        },
        isCandlestick: Boolean(options.forceCandlestick && hasRealOhlc),
        hoverIndex: null,
        selectedIndex: normalized.length - 1,
        pointer: null,
        targetInfo: {
            display_name: prediction.display_name || prediction.target || '',
        },
    };
    predictionTrackingChartState = state;
    drawKlineCanvas(state);
    renderKlineDetail(detail, normalized[state.selectedIndex], state);

    canvas.addEventListener('mousemove', event => updateKlinePointer(event, state, false));
    canvas.addEventListener('click', event => updateKlinePointer(event, state, true));
    canvas.addEventListener('mouseleave', () => {
        state.hoverIndex = null;
        state.pointer = null;
        tooltip.style.display = 'none';
        drawKlineCanvas(state);
    });
}

function renderPredictionTrackingComparison(latest, prediction, summary, options = {}) {
    const targetSpec = options.targetSpec || {};
    const intradayMeta = options.intradayMeta || {};
    const latestReturn = Number(latest?.effective_return_pct);
    const predictedMin = Number(prediction?.min_pct);
    const predictedMax = Number(prediction?.max_pct);
    if (!Number.isFinite(latestReturn) || !Number.isFinite(predictedMin) || !Number.isFinite(predictedMax)) {
        return '<div class="tracking-compare empty-inline">当前分钟收益或预测区间不足，暂时无法做预测对比。</div>';
    }

    const upThreshold = Number(targetSpec.up_threshold_pct);
    const downThreshold = Number(targetSpec.down_threshold_pct);
    const expected = Number(targetSpec.expected_return_pct);
    const values = [latestReturn, predictedMin, predictedMax, 0];
    if (Number.isFinite(upThreshold)) values.push(upThreshold);
    if (Number.isFinite(downThreshold)) values.push(downThreshold);
    if (Number.isFinite(expected)) values.push(expected);
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const span = Math.max(rawMax - rawMin, 2);
    const axisMin = rawMin - span * 0.18;
    const axisMax = rawMax + span * 0.18;
    const pctAt = value => {
        const ratio = (Number(value) - axisMin) / (axisMax - axisMin || 1);
        return clamp(ratio * 100, 0, 100);
    };
    const rangeLeft = pctAt(Math.min(predictedMin, predictedMax));
    const rangeRight = pctAt(Math.max(predictedMin, predictedMax));
    const markerLeft = pctAt(latestReturn);
    const expectedLeft = Number.isFinite(expected) ? pctAt(expected) : null;
    const upLeft = Number.isFinite(upThreshold) ? pctAt(upThreshold) : null;
    const downLeft = Number.isFinite(downThreshold) ? pctAt(downThreshold) : null;
    const inRange = latestReturn >= Math.min(predictedMin, predictedMax)
        && latestReturn <= Math.max(predictedMin, predictedMax);
    const predictedDirection = prediction?.direction || targetSpec.direction || 'neutral';
    const currentDirection = directionFromReturnForSpec(latestReturn, targetSpec);
    const statusClass = inRange ? 'ok' : 'warn';
    const statusText = inRange
        ? '当前分钟收益落在预测区间内'
        : latestReturn > Math.max(predictedMin, predictedMax)
            ? '当前分钟收益高于预测上沿'
            : '当前分钟收益低于预测下沿';
    const formalType = targetTypeLabel(targetSpec.target_type);
    const minuteType = targetTypeLabel(intradayMeta.target_type_used || summary.target_type_used);
    const typeMismatch = targetSpec.target_type
        && intradayMeta.target_type_used
        && targetSpec.target_type !== intradayMeta.target_type_used;
    const compareNote = typeMismatch
        ? `分钟口径为${minuteType}，正式预测目标为${formalType}，因此这是近似观察对比。`
        : `分钟口径与正式预测同为${formalType || minuteType}。`;

    return `
        <div class="tracking-compare">
            <div class="tracking-compare-head">
                <strong>预测对比</strong>
                <span class="tracking-compare-status ${statusClass}">${escapeHtml(statusText)}</span>
            </div>
            <div class="tracking-compare-grid">
                ${klineMetric('预测方向', directionText(predictedDirection))}
                ${klineMetric('当前方向', directionText(currentDirection))}
                ${klineMetric('预测区间', `${formatSignedPct(predictedMin)} ~ ${formatSignedPct(predictedMax)}`)}
                ${klineMetric('当前分钟', formatSignedPct(latestReturn))}
                ${klineMetric('预测目标', `${escapeHtml(targetSpec.horizon || prediction.timeframe || '')} · ${escapeHtml(formalType)}`)}
                ${klineMetric('分钟口径', escapeHtml(minuteType))}
            </div>
            <div class="tracking-return-bar" aria-label="分钟收益和预测区间对比">
                <div class="tracking-return-zero" style="left:${pctAt(0).toFixed(2)}%"></div>
                <div class="tracking-return-range" style="left:${rangeLeft.toFixed(2)}%;width:${Math.max(rangeRight - rangeLeft, 1.5).toFixed(2)}%"></div>
                ${downLeft == null ? '' : `<div class="tracking-return-threshold down" style="left:${downLeft.toFixed(2)}%" title="看跌阈值 ${formatSignedPct(downThreshold)}"></div>`}
                ${upLeft == null ? '' : `<div class="tracking-return-threshold up" style="left:${upLeft.toFixed(2)}%" title="看涨阈值 ${formatSignedPct(upThreshold)}"></div>`}
                ${expectedLeft == null ? '' : `<div class="tracking-return-expected" style="left:${expectedLeft.toFixed(2)}%" title="预期中点 ${formatSignedPct(expected)}"></div>`}
                <div class="tracking-return-marker ${statusClass}" style="left:${markerLeft.toFixed(2)}%" title="当前分钟 ${formatSignedPct(latestReturn)}"></div>
            </div>
            <div class="tracking-return-labels">
                <span>${formatSignedPct(axisMin)}</span>
                <span>预测区间 ${formatSignedPct(predictedMin)} ~ ${formatSignedPct(predictedMax)}</span>
                <span>${formatSignedPct(axisMax)}</span>
            </div>
            <div class="tracking-compare-note">${escapeHtml(compareNote)}</div>
        </div>
    `;
}

function directionFromReturnForSpec(value, spec = {}) {
    const number = Number(value);
    const up = Number(spec.up_threshold_pct);
    const down = Number(spec.down_threshold_pct);
    if (!Number.isFinite(number)) return 'neutral';
    if (Number.isFinite(up) && number >= up) return 'bullish';
    if (Number.isFinite(down) && number <= down) return 'bearish';
    return 'neutral';
}

function drawPredictionTrackingSvg(points, prediction, summary) {
    if (!points || points.length < 1) {
        return '<div class="empty-inline">暂无可画走势点</div>';
    }
    const width = 720;
    const height = 260;
    const pad = { left: 54, right: 24, top: 24, bottom: 38 };
    const values = points.map(p => Number(p.effective_return_pct)).filter(Number.isFinite);
    if (prediction.min_pct != null) values.push(Number(prediction.min_pct));
    if (prediction.max_pct != null) values.push(Number(prediction.max_pct));
    values.push(0);
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const span = Math.max(maxVal - minVal, 2);
    const yMin = minVal - span * 0.16;
    const yMax = maxVal + span * 0.16;
    const xFor = (idx) => {
        if (points.length === 1) return pad.left;
        return pad.left + idx * ((width - pad.left - pad.right) / (points.length - 1));
    };
    const yFor = (value) => {
        const ratio = (Number(value) - yMin) / (yMax - yMin || 1);
        return height - pad.bottom - ratio * (height - pad.top - pad.bottom);
    };
    const line = points.map((p, idx) => `${xFor(idx).toFixed(1)},${yFor(p.effective_return_pct).toFixed(1)}`).join(' ');
    const zeroY = yFor(0);
    const minY = prediction.min_pct == null ? null : yFor(prediction.min_pct);
    const maxY = prediction.max_pct == null ? null : yFor(prediction.max_pct);
    const last = points[points.length - 1];
    const lineClass = Number(summary.latest_effective_return_pct) >= 0 ? 'tracking-up' : 'tracking-down';
    const gridLabels = [yMax, 0, yMin];
    return `
        <svg class="tracking-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="预测后真实收益曲线">
            <rect x="0" y="0" width="${width}" height="${height}" rx="8" class="tracking-bg"></rect>
            ${gridLabels.map(v => `
                <line x1="${pad.left}" x2="${width - pad.right}" y1="${yFor(v).toFixed(1)}" y2="${yFor(v).toFixed(1)}" class="tracking-grid"></line>
                <text x="12" y="${(yFor(v) + 4).toFixed(1)}" class="tracking-axis">${formatSignedPct(v)}</text>
            `).join('')}
            <line x1="${pad.left}" x2="${width - pad.right}" y1="${zeroY.toFixed(1)}" y2="${zeroY.toFixed(1)}" class="tracking-zero"></line>
            ${minY != null && maxY != null ? `
                <rect x="${pad.left}" y="${Math.min(minY, maxY).toFixed(1)}" width="${width - pad.left - pad.right}" height="${Math.abs(maxY - minY).toFixed(1)}" class="tracking-range"></rect>
                <line x1="${pad.left}" x2="${width - pad.right}" y1="${minY.toFixed(1)}" y2="${minY.toFixed(1)}" class="tracking-band"></line>
                <line x1="${pad.left}" x2="${width - pad.right}" y1="${maxY.toFixed(1)}" y2="${maxY.toFixed(1)}" class="tracking-band"></line>
            ` : ''}
            <polyline points="${line}" class="tracking-line ${lineClass}"></polyline>
            ${points.map((p, idx) => `
                <circle cx="${xFor(idx).toFixed(1)}" cy="${yFor(p.effective_return_pct).toFixed(1)}" r="${idx === points.length - 1 ? 5 : 3}" class="tracking-dot ${lineClass}">
                    <title>${escapeHtml(p.date)} · 有效收益 ${formatSignedPct(p.effective_return_pct)} · 收盘 ${escapeHtml(String(p.close))}</title>
                </circle>
            `).join('')}
            <text x="${pad.left}" y="${height - 12}" class="tracking-axis">${escapeHtml(points[0].date)}</text>
            <text x="${width - pad.right - 82}" y="${height - 12}" class="tracking-axis">${escapeHtml(last.date)}</text>
            <text x="${width - pad.right - 160}" y="22" class="tracking-latest">最新 ${formatSignedPct(last.effective_return_pct)}</text>
        </svg>
    `;
}

// ============================================================
// Skill Registry
// ============================================================

function initSkillRegistry() {
    const refreshBtn = document.getElementById('refreshSkillsBtn');
    const filters = ['skillAgentFilter', 'skillTypeFilter', 'skillEnabledFilter', 'skillSearch'];
    if (refreshBtn) refreshBtn.addEventListener('click', loadSkillRegistry);
    filters.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const eventName = el.tagName === 'INPUT' ? 'input' : 'change';
        el.addEventListener(eventName, renderSkillRegistry);
    });
}

async function loadSkillRegistry() {
    const list = document.getElementById('skillList');
    if (list) list.innerHTML = '<div class="empty-state">正在加载 Skill Registry...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/skills/registry`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Skill Registry 加载失败');
        }
        skillRegistryState = await resp.json();
        populateSkillFilters(skillRegistryState.skills || []);
        renderSkillRegistry();
    } catch (e) {
        if (list) list.innerHTML = `<div class="empty-state error">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function populateSkillFilters(skills) {
    const agentFilter = document.getElementById('skillAgentFilter');
    const typeFilter = document.getElementById('skillTypeFilter');
    if (!agentFilter || !typeFilter) return;
    const currentAgent = agentFilter.value;
    const currentType = typeFilter.value;
    const agents = Array.from(new Set(skills.map(s => s.agent_name).filter(Boolean))).sort();
    const types = Array.from(new Set(skills.map(s => s.skill_type).filter(Boolean))).sort();
    agentFilter.innerHTML = '<option value="">全部 Agent</option>' +
        agents.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    typeFilter.innerHTML = '<option value="">全部类型</option>' +
        types.map(type => `<option value="${escapeHtml(type)}">${escapeHtml(skillTypeLabel(type))}</option>`).join('');
    agentFilter.value = agents.includes(currentAgent) ? currentAgent : '';
    typeFilter.value = types.includes(currentType) ? currentType : '';
}

function renderSkillRegistry() {
    if (!skillRegistryState) return;
    renderSkillSummary(skillRegistryState.summary || {});
    renderSkillRegistryPath(skillRegistryState.registry || {});

    const list = document.getElementById('skillList');
    if (!list) return;
    const skills = filterSkills(skillRegistryState.skills || []);
    if (!skills.length) {
        list.innerHTML = '<div class="empty-state">没有匹配当前筛选条件的 skill。</div>';
        return;
    }

    list.innerHTML = skills.map(renderSkillCard).join('');
    list.querySelectorAll('[data-skill-toggle]').forEach(button => {
        button.addEventListener('click', () => toggleSkillEnabled(
            button.dataset.skillId,
            button.dataset.nextEnabled === 'true',
        ));
    });
}

function renderSkillSummary(summary) {
    const el = document.getElementById('skillSummary');
    if (!el) return;
    el.innerHTML = `
        ${metricCard('全部 Skill', summary.total || 0)}
        ${metricCard('已启用', summary.enabled || 0)}
        ${metricCard('已禁用', summary.disabled || 0)}
        ${metricCard('Agent 数', Object.keys(summary.by_agent || {}).length)}
    `;
}

function renderSkillRegistryPath(registry) {
    const el = document.getElementById('skillRegistryPath');
    if (!el) return;
    const status = registry.exists ? `更新于 ${formatDate(registry.updated_at)}` : '文件尚不存在';
    el.textContent = `Registry: ${registry.path || ''} · ${status}`;
}

function filterSkills(skills) {
    const agent = document.getElementById('skillAgentFilter')?.value || '';
    const type = document.getElementById('skillTypeFilter')?.value || '';
    const enabledText = document.getElementById('skillEnabledFilter')?.value || '';
    const query = (document.getElementById('skillSearch')?.value || '').trim().toLowerCase();
    return skills.filter(skill => {
        if (agent && skill.agent_name !== agent) return false;
        if (type && skill.skill_type !== type) return false;
        if (enabledText && String(skill.enabled) !== enabledText) return false;
        if (!query) return true;
        const haystack = JSON.stringify(skill).toLowerCase();
        return haystack.includes(query);
    });
}

function renderSkillCard(skill) {
    const validation = skill.validation_summary || {};
    const source = skill.source_summary || {};
    const enabled = !!skill.enabled;
    const nextEnabled = !enabled;
    const holdoutText = formatHoldoutSummary(validation);
    const actionText = formatSkillAction(skill.action || {});
    const conditions = skill.trigger_conditions || {};
    return `
        <article class="skill-card ${enabled ? 'enabled' : 'disabled'}">
            <div class="skill-card-main">
                <div class="skill-card-title-row">
                    <div>
                        <div class="skill-meta-line">
                            <span class="skill-agent">${escapeHtml(skill.agent_name || 'unknown')}</span>
                            <span class="skill-type">${escapeHtml(skillTypeLabel(skill.skill_type))}</span>
                            <span class="skill-status ${enabled ? 'enabled' : 'disabled'}">${enabled ? '已启用' : '已禁用'}</span>
                        </div>
                        <h3>${escapeHtml(skill.description || skill.skill_id)}</h3>
                    </div>
                    <button class="skill-toggle ${enabled ? 'danger' : 'enable'}"
                            data-skill-toggle
                            data-skill-id="${escapeHtml(skill.skill_id)}"
                            data-next-enabled="${nextEnabled}">
                        ${enabled ? '禁用' : '启用'}
                    </button>
                </div>
                <div class="skill-id">${escapeHtml(skill.skill_id)}</div>
                <div class="skill-rule-grid">
                    <div>
                        <span>触发条件</span>
                        <code>${escapeHtml(formatConditions(conditions))}</code>
                    </div>
                    <div>
                        <span>动作</span>
                        <code>${escapeHtml(actionText)}</code>
                    </div>
                    <div>
                        <span>训练样本</span>
                        <strong>${formatTrainingSummary(validation)}</strong>
                    </div>
                    <div>
                        <span>Holdout</span>
                        <strong>${escapeHtml(holdoutText)}</strong>
                    </div>
                </div>
                <div class="skill-source-grid">
                    ${renderSourceLine('数据来源', source.data_source)}
                    ${renderSourceLine('训练报告', source.training_report_path)}
                    ${renderSourceLine('验证报告', source.holdout_report_path)}
                    ${renderSourceLine('生成时间', source.created_at || skill.created_at)}
                </div>
            </div>
        </article>
    `;
}

function renderSourceLine(label, value) {
    if (!value) return '';
    return `
        <div class="skill-source-line">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `;
}

async function toggleSkillEnabled(skillId, enabled) {
    try {
        const resp = await fetch(`${API_BASE}/api/skills/registry/${encodeURIComponent(skillId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Skill 状态更新失败');
        }
        await loadSkillRegistry();
    } catch (e) {
        showError(e.message);
    }
}

function skillTypeLabel(type) {
    const map = {
        direction_policy: '方向规则',
        confidence_policy: '置信度规则',
    };
    return map[type] || type || '未知类型';
}

function formatSkillAction(action) {
    if (action.type === 'force_direction') {
        return `force_direction(${action.direction || 'unknown'}), confidence ${formatDecimal(action.confidence_floor)}~${formatDecimal(action.confidence_cap)}`;
    }
    if (action.type === 'cap_confidence') {
        return `cap_confidence <= ${formatPercent(action.confidence_cap)}`;
    }
    if (action.type === 'neutralize_direction') {
        return 'neutralize_direction';
    }
    return JSON.stringify(action);
}

function formatConditions(conditions) {
    const entries = Object.entries(conditions || {});
    if (!entries.length) return '无';
    return entries.map(([key, value]) => `${key}=${value}`).join(' && ');
}

function formatTrainingSummary(validation) {
    const samples = validation.training_samples ?? 'N/A';
    const cases = validation.training_unique_cases ?? 'N/A';
    const accuracy = validation.training_accuracy != null
        ? ` · 命中 ${formatPercent(validation.training_accuracy)}`
        : '';
    const avgConf = validation.training_avg_confidence != null
        ? ` · 置信 ${formatPercent(validation.training_avg_confidence)}`
        : '';
    return `样本 ${samples} · 案例 ${cases}${accuracy}${avgConf}`;
}

function formatHoldoutSummary(validation) {
    if (!validation.passed) return '无 holdout 记录';
    const samples = validation.holdout_samples ?? 'N/A';
    if (validation.brier_delta != null) {
        return `样本 ${samples} · Brier 改善 ${formatSignedNumber(validation.brier_delta, 4)} · 命中 ${validation.matched_samples ?? 'N/A'}`;
    }
    if (validation.accuracy_delta != null) {
        return `样本 ${samples} · 命中率变化 ${formatSignedPercent(validation.accuracy_delta)} · 改变 ${validation.changed_predictions ?? 'N/A'}`;
    }
    return `样本 ${samples} · ${validation.reason || '已验证'}`;
}

function formatPercent(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'N/A';
    return `${Math.round(num * 100)}%`;
}

function formatSignedPercent(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'N/A';
    return `${num >= 0 ? '+' : ''}${Math.round(num * 100)}%`;
}

function formatDecimal(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'N/A';
    return num.toFixed(2);
}

function formatSignedNumber(value, digits = 2) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 'N/A';
    return `${num >= 0 ? '+' : ''}${num.toFixed(digits)}`;
}

// ============================================================
// 模型配置
// ============================================================

function initModels() {
    const form = document.getElementById('modelForm');
    const refreshBtn = document.getElementById('refreshModelsBtn');
    const resetBtn = document.getElementById('resetModelFormBtn');
    const grid = document.getElementById('modelCardGrid');

    if (form) {
        form.addEventListener('submit', addModel);
    }
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadModels);
    }
    if (resetBtn) {
        resetBtn.addEventListener('click', resetModelForm);
    }
    if (grid) {
        grid.addEventListener('click', async (e) => {
            const btn = e.target.closest('button[data-action]');
            if (!btn) return;
            const action = btn.dataset.action;
            const modelId = btn.dataset.modelId;
            if (action === 'activate') await activateModel(modelId);
            if (action === 'toggle-ssl') await toggleModelSsl(modelId, btn.dataset.verifySsl !== 'true');
            if (action === 'delete') await deleteModel(modelId);
        });
    }
}

async function loadModels() {
    const grid = document.getElementById('modelCardGrid');
    if (grid) {
        grid.innerHTML = '<div class="empty-state">正在加载模型配置...</div>';
    }
    try {
        const resp = await fetch(`${API_BASE}/api/models`);
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || '模型配置加载失败');
        }
        modelRegistryState = data;
        renderModels();
    } catch (e) {
        console.warn('加载模型配置失败:', e);
        if (grid) {
            grid.innerHTML = `<div class="empty-state error">模型配置加载失败: ${escapeHtml(e.message)}</div>`;
        }
    }
}

function renderModels() {
    const state = modelRegistryState || {};
    const active = state.active_model || {};
    const models = state.models || [];
    const activeCard = document.getElementById('activeModelCard');
    const registryPath = document.getElementById('modelRegistryPath');
    const grid = document.getElementById('modelCardGrid');

    if (activeCard) {
        activeCard.innerHTML = `
            <span>当前模型</span>
            <strong>${escapeHtml(active.name || active.model || '未配置')}</strong>
            <small>${escapeHtml(active.provider || 'unknown')} · ${escapeHtml(active.model || '')}</small>
            <div class="active-model-meta">
                <span>${escapeHtml(active.base_url || '')}</span>
                <span>${active.has_api_key ? `Key ${escapeHtml(active.api_key_hint || '已配置')}` : '复用 .env Key'}</span>
            </div>
        `;
        activeCard.classList.toggle('offline', !state.llm_ready);
    }
    if (registryPath) {
        registryPath.textContent = `Registry: ${state.registry_path || 'local'}`;
    }
    if (!grid) return;
    if (!models.length) {
        grid.innerHTML = '<div class="empty-state">暂无模型配置</div>';
        return;
    }

    grid.innerHTML = models.map(model => `
        <div class="model-card ${model.active ? 'active' : ''}">
            <div class="model-card-top">
                <div>
                    <div class="model-name">${escapeHtml(model.name || model.model)}</div>
                    <div class="model-id">${escapeHtml(model.model || '')}</div>
                </div>
                <span class="model-active-pill">${model.active ? '当前使用' : escapeHtml(model.provider || 'custom')}</span>
            </div>
            <div class="model-meta-grid">
                ${modelMeta('Base URL', model.base_url)}
                ${modelMeta('Temperature', model.temperature)}
                ${modelMeta('Max Tokens', model.max_tokens)}
                ${modelMeta('API Key', model.has_api_key ? (model.api_key_hint || '已配置') : '复用 .env')}
                ${modelMeta('SSL 校验', model.verify_ssl ? '开启' : '关闭')}
            </div>
            <div class="model-card-actions">
                <button class="secondary-btn compact-btn" data-action="activate" data-model-id="${escapeHtml(model.id)}" ${model.active ? 'disabled' : ''}>设为当前</button>
                ${model.source === 'env' ? '' : `<button class="secondary-btn compact-btn" data-action="toggle-ssl" data-model-id="${escapeHtml(model.id)}" data-verify-ssl="${model.verify_ssl ? 'true' : 'false'}">${model.verify_ssl ? '关闭SSL校验' : '开启SSL校验'}</button>`}
                ${model.source === 'env' ? '' : `<button class="danger-btn compact-btn" data-action="delete" data-model-id="${escapeHtml(model.id)}">删除</button>`}
            </div>
        </div>
    `).join('');
}

function modelMeta(label, value) {
    const displayValue = value === null || value === undefined || value === '' ? 'N/A' : String(value);
    return `
        <div class="model-meta-item">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(displayValue)}</strong>
        </div>
    `;
}

async function addModel(e) {
    e.preventDefault();
    const payload = {
        name: document.getElementById('modelNameInput').value.trim(),
        provider: document.getElementById('modelProviderInput').value.trim() || 'custom',
        base_url: document.getElementById('modelBaseUrlInput').value.trim(),
        model: document.getElementById('modelIdInput').value.trim(),
        api_key: document.getElementById('modelApiKeyInput').value.trim(),
        temperature: readNumber('modelTemperatureInput', 0.3),
        max_tokens: readNumber('modelMaxTokensInput', 4096),
        verify_ssl: document.getElementById('modelVerifySslInput').checked,
        set_active: document.getElementById('modelSetActiveInput').checked,
    };
    if (!payload.name || !payload.base_url || !payload.model) {
        showError('请填写模型显示名称、Base URL 和模型 ID');
        return;
    }

    try {
        const resp = await fetch(`${API_BASE}/api/models`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || '添加模型失败');
        }
        modelRegistryState = data;
        renderModels();
        resetModelForm();
        checkHealth();
    } catch (e) {
        showError(e.message);
    }
}

async function activateModel(modelId) {
    if (!modelId) return;
    try {
        const resp = await fetch(`${API_BASE}/api/models/active`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_id: modelId }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || '切换模型失败');
        }
        modelRegistryState = data;
        renderModels();
        checkHealth();
    } catch (e) {
        showError(e.message);
    }
}

async function toggleModelSsl(modelId, verifySsl) {
    if (!modelId) return;
    try {
        const resp = await fetch(`${API_BASE}/api/models/${encodeURIComponent(modelId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ verify_ssl: Boolean(verifySsl) }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || '更新 SSL 校验设置失败');
        }
        modelRegistryState = data;
        renderModels();
        checkHealth();
    } catch (e) {
        showError(e.message);
    }
}

async function deleteModel(modelId) {
    if (!modelId) return;
    if (!window.confirm('删除这个模型配置？')) return;
    try {
        const resp = await fetch(`${API_BASE}/api/models/${encodeURIComponent(modelId)}`, {
            method: 'DELETE',
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || '删除模型失败');
        }
        modelRegistryState = data;
        renderModels();
        checkHealth();
    } catch (e) {
        showError(e.message);
    }
}

function resetModelForm() {
    const form = document.getElementById('modelForm');
    if (!form) return;
    form.reset();
    document.getElementById('modelProviderInput').value = 'custom';
    document.getElementById('modelTemperatureInput').value = '0.3';
    document.getElementById('modelMaxTokensInput').value = '4096';
    document.getElementById('modelVerifySslInput').checked = true;
    document.getElementById('modelSetActiveInput').checked = true;
}

// ============================================================
// Agent 信息
// ============================================================

async function loadAgents() {
    try {
        const resp = await fetch(`${API_BASE}/api/agents`);
        const data = await resp.json();
        agentsInfo = data.agents || [];
    } catch (e) {
        console.warn('加载 Agent 信息失败:', e);
    }
}

function renderAgents() {
    const grid = document.getElementById('agentsGrid');
    if (!agentsInfo || agentsInfo.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-muted)">暂无 Agent 信息</p>';
        return;
    }

    grid.innerHTML = agentsInfo.map(a => {
        const w = a.weights || {};
        return `
            <div class="agent-info-card">
                <div class="agent-info-name">${escapeHtml(a.name)}</div>
                <div class="agent-info-desc">${escapeHtml(a.description)}</div>
                <div class="agent-weights">
                    <div class="weight-item">
                        <div class="weight-value">${Math.round((w.short || 0) * 100)}%</div>
                        <div class="weight-label">短期</div>
                    </div>
                    <div class="weight-item">
                        <div class="weight-value">${Math.round((w.mid || 0) * 100)}%</div>
                        <div class="weight-label">中期</div>
                    </div>
                    <div class="weight-item">
                        <div class="weight-value">${Math.round((w.long || 0) * 100)}%</div>
                        <div class="weight-label">长期</div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// ============================================================
// Agent 改进工程师
// ============================================================

function initImprovement() {
    const refreshBtn = document.getElementById('refreshImprovementBtn');
    const evalBtn = document.getElementById('runEvaluationBtn');
    const engineerBtn = document.getElementById('runEngineerBtn');
    const selfLabBtn = document.getElementById('runSelfLabBtn');
    const candidateBtn = document.getElementById('runCandidateSandboxBtn');
    const promptLoopBtn = document.getElementById('runTechnicalPromptLoopBtn');
    const passiveRefreshBtn = document.getElementById('refreshPassiveSamplesBtn');
    const passiveSelectBtn = document.getElementById('selectEligiblePassiveSamplesBtn');
    const passiveClearBtn = document.getElementById('clearPassiveSamplesBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadImprovementStatus);
    if (evalBtn) evalBtn.addEventListener('click', runHistoricalEvaluation);
    if (engineerBtn) engineerBtn.addEventListener('click', runImprovementEngineer);
    if (selfLabBtn) selfLabBtn.addEventListener('click', runSelfImprovementLab);
    if (candidateBtn) candidateBtn.addEventListener('click', runCandidateSandbox);
    if (promptLoopBtn) promptLoopBtn.addEventListener('click', runTechnicalPromptLoop);
    if (passiveRefreshBtn) passiveRefreshBtn.addEventListener('click', loadPassiveSamples);
    if (passiveSelectBtn) passiveSelectBtn.addEventListener('click', selectAllEligiblePassiveSamples);
    if (passiveClearBtn) passiveClearBtn.addEventListener('click', clearPassiveSamples);
    [
        'selfLabTargets',
        'selfLabStartDate',
        'selfLabEndDate',
        'selfLabIntervalDays',
        'candidateHoldoutTargets',
        'candidateHoldoutStartDate',
        'candidateHoldoutEndDate',
        'candidateHoldoutIntervalDays',
        'candidateMinHoldoutSamples',
        'promptReplayMaxSamples',
        'promptReplayMinSamples',
        'candidateBatchCount',
    ].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('input', updateTechnicalLoopSampleEstimates);
        el.addEventListener('change', updateTechnicalLoopSampleEstimates);
    });
    ['passiveSampleStatus', 'passiveSampleTarget', 'passiveSampleTimeframe', 'passiveSampleLimit'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', loadPassiveSamples);
    });
    updateTechnicalLoopSampleEstimates();
}

function parseLoopTargets(value) {
    return Array.from(new Set(String(value || '')
        .split(/[,，、\s]+/)
        .map(item => item.trim())
        .filter(Boolean)));
}

function parseLocalDate(value) {
    if (!value) return null;
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime()) ? null : date;
}

function estimateDatePoints(startValue, endValue, intervalDays) {
    const start = parseLocalDate(startValue);
    const end = parseLocalDate(endValue);
    const interval = Math.max(1, Number(intervalDays) || 1);
    if (!start || !end || end < start) {
        return { count: 0, days: 0, valid: false };
    }
    const days = Math.floor((end.getTime() - start.getTime()) / DAY_MS);
    return {
        count: Math.floor(days / interval) + 1,
        days: days + 1,
        valid: true,
    };
}

function estimateLoopSampleSet({ targetsId, startId, endId, intervalId }) {
    const targets = parseLoopTargets(document.getElementById(targetsId)?.value || '');
    const interval = readNumber(intervalId, 14);
    const datePoints = estimateDatePoints(
        document.getElementById(startId)?.value,
        document.getElementById(endId)?.value,
        interval,
    );
    return {
        targets,
        interval,
        datePoints,
        total: targets.length * datePoints.count,
    };
}

function setLoopEstimateCard(cardId, valueId, metaId, value, meta, status = 'neutral') {
    const card = document.getElementById(cardId);
    const valueEl = document.getElementById(valueId);
    const metaEl = document.getElementById(metaId);
    if (valueEl) valueEl.textContent = value;
    if (metaEl) metaEl.textContent = meta;
    if (card) {
        card.classList.remove('ok', 'warning', 'neutral');
        card.classList.add(status);
    }
}

function updateTechnicalLoopSampleEstimates() {
    const training = estimateLoopSampleSet({
        targetsId: 'selfLabTargets',
        startId: 'selfLabStartDate',
        endId: 'selfLabEndDate',
        intervalId: 'selfLabIntervalDays',
    });
    const holdout = estimateLoopSampleSet({
        targetsId: 'candidateHoldoutTargets',
        startId: 'candidateHoldoutStartDate',
        endId: 'candidateHoldoutEndDate',
        intervalId: 'candidateHoldoutIntervalDays',
    });
    const holdoutMin = readNumber('candidateMinHoldoutSamples', 20);
    const replayMax = readNumber('promptReplayMaxSamples', 60);
    const replayMin = readNumber('promptReplayMinSamples', 30);
    const replayEstimate = Math.min(holdout.total, replayMax);

    const trainingValid = training.targets.length > 0 && training.datePoints.valid;
    const trainingOk = trainingValid && training.total >= TECH_LOOP_RULE_MIN_SAMPLES;
    const trainingMeta = trainingValid
        ? `${training.targets.length} 个标的 × ${training.datePoints.count} 个日期点；候选规则默认门槛为样本 ≥${TECH_LOOP_RULE_MIN_SAMPLES}、独立案例 ≥${TECH_LOOP_RULE_MIN_UNIQUE_CASES}。`
        : '请填写训练标的，并确保训练日期区间有效。';
    setLoopEstimateCard(
        'trainingSampleEstimateCard',
        'trainingSampleEstimateValue',
        'trainingSampleEstimateMeta',
        trainingValid ? `最多 ${training.total} 条` : '--',
        trainingMeta,
        trainingValid ? (trainingOk ? 'ok' : 'warning') : 'neutral',
    );

    const holdoutValid = holdout.targets.length > 0 && holdout.datePoints.valid;
    const holdoutOk = holdoutValid && holdout.total >= holdoutMin;
    const holdoutMeta = holdoutValid
        ? `${holdout.targets.length} 个标的 × ${holdout.datePoints.count} 个日期点；当前门禁要求有效样本 ≥${holdoutMin}。`
        : '请填写 Holdout 标的，并确保验证日期区间有效。';
    setLoopEstimateCard(
        'holdoutSampleEstimateCard',
        'holdoutSampleEstimateValue',
        'holdoutSampleEstimateMeta',
        holdoutValid ? `最多 ${holdout.total} 条` : '--',
        holdoutMeta,
        holdoutValid ? (holdoutOk ? 'ok' : 'warning') : 'neutral',
    );

    const replayValid = holdoutValid && replayMax > 0;
    const replayOk = replayValid && replayEstimate >= replayMin;
    const replayMeta = replayValid
        ? `会从 Holdout 样本中最多 replay ${replayMax} 条；当前最低接受 ${replayMin} 条成功 replay。`
        : 'Replay 依赖 Holdout 样本，验证样本无效时不会执行。';
    setLoopEstimateCard(
        'replaySampleEstimateCard',
        'replaySampleEstimateValue',
        'replaySampleEstimateMeta',
        replayValid ? `预计 ${replayEstimate} 条` : '--',
        replayMeta,
        replayValid ? (replayOk ? 'ok' : 'warning') : 'neutral',
    );
}

function estimateLoopSampleSetFromPayload(targetsValue, startDate, endDate, intervalDays) {
    const targets = parseLoopTargets(targetsValue);
    const datePoints = estimateDatePoints(startDate, endDate, intervalDays);
    return {
        targets,
        datePoints,
        total: targets.length * datePoints.count,
    };
}

function estimateTechnicalLoopRuntime(payload) {
    const training = estimateLoopSampleSetFromPayload(
        payload.targets,
        payload.start_date,
        payload.end_date,
        payload.interval_days,
    );
    const holdout = estimateLoopSampleSetFromPayload(
        payload.holdout_targets,
        payload.holdout_start_date,
        payload.holdout_end_date,
        payload.holdout_interval_days,
    );
    const replaySamples = Math.min(
        Math.max(0, Number(payload.prompt_replay_max_samples) || 0),
        holdout.total,
    );
    const durations = [
        Math.max(24, 18 + training.total * 0.42),
        payload.use_llm_candidates
            ? Math.max(42, 24 + (Number(payload.candidate_batch_count) || 1) * 18)
            : 18,
        Math.max(24, 14 + replaySamples * 7.5),
        Math.max(22, 16 + holdout.total * 0.32),
        payload.apply_if_passed ? 18 : 10,
    ];
    const totalSeconds = durations.reduce((sum, value) => sum + value, 0);
    let cursor = 0;
    const stages = TECH_LOOP_PROGRESS_STAGES.map((stage, index) => {
        const start = cursor;
        cursor += durations[index] || 8;
        return { ...stage, start, end: cursor };
    });
    return {
        training,
        holdout,
        replaySamples,
        totalSeconds: Math.max(60, totalSeconds),
        stages,
    };
}

function formatLoopDuration(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    if (minutes <= 0) return `${rest} 秒`;
    if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    const min = minutes % 60;
    return min ? `${hours} 小时 ${min} 分` : `${hours} 小时`;
}

function formatLoopDurationRange(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '少于 1 分钟';
    const lower = Math.max(20, seconds * 0.75);
    const upper = Math.max(lower + 20, seconds * 1.65);
    return `${formatLoopDuration(lower)} - ${formatLoopDuration(upper)}`;
}

function startTechnicalLoopProgress(payload, abortController = null) {
    if (technicalLoopProgressTimer) {
        clearInterval(technicalLoopProgressTimer);
        technicalLoopProgressTimer = null;
    }
    const output = document.getElementById('improvementOutput');
    const runtime = estimateTechnicalLoopRuntime(payload);
    const state = {
        startedAt: Date.now(),
        payload,
        runtime,
        stopped: false,
        completedFromPoll: false,
        abortController,
    };
    output.innerHTML = renderTechnicalLoopProgressShell(runtime);
    updateTechnicalLoopProgress(state);
    technicalLoopProgressTimer = setInterval(() => updateTechnicalLoopProgress(state), 1000);
    state.timer = technicalLoopProgressTimer;
    state.statusPollTimer = setInterval(() => pollTechnicalLoopCompletion(state), 8000);
    setTimeout(() => pollTechnicalLoopCompletion(state), 3500);
    return state;
}

function stopTechnicalLoopProgress(state, completed) {
    if (!state || state.stopped) return;
    state.stopped = true;
    if (state.timer) clearInterval(state.timer);
    if (state.statusPollTimer) clearInterval(state.statusPollTimer);
    if (technicalLoopProgressTimer === state.timer) technicalLoopProgressTimer = null;
    if (completed) {
        setLoopStageStripProgress(TECH_LOOP_PROGRESS_STAGES.length - 1, true);
    }
}

async function pollTechnicalLoopCompletion(state) {
    if (!state || state.stopped) return;
    try {
        const resp = await fetch(`${API_BASE}/api/improvement/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        const startedWindow = state.startedAt - 30000;
        const relatedRun = (data.latest_technical_prompt_loop_runs || []).find(run => {
            const updatedAt = new Date(run.updated_at || 0).getTime();
            return updatedAt >= startedWindow;
        });
        if (!relatedRun || state.stopped) return;
        state.backendRun = relatedRun;
        updateTechnicalLoopProgress(state);

        const completedRun = relatedRun.status === 'completed' && relatedRun.paths?.candidate_report_json
            ? relatedRun
            : null;
        if (!completedRun || state.stopped) return;
        state.completedFromPoll = true;
        stopTechnicalLoopProgress(state, true);
        if (state.abortController) state.abortController.abort();
        await loadTechnicalLoopReport(completedRun.paths.candidate_report_json);
    } catch (e) {
        // Polling is best-effort; the primary request will still finish normally.
    }
}

function renderTechnicalLoopProgressShell(runtime) {
    const stages = runtime.stages.map((stage, index) => `
        <div class="technical-loop-step" data-loop-progress-step="${index}">
            <span class="technical-loop-step-index">${index + 1}</span>
            <div>
                <strong>${escapeHtml(stage.label)}</strong>
                <small>${escapeHtml(stage.detail)}</small>
            </div>
        </div>
    `).join('');
    return `
        <div class="technical-loop-progress" id="technicalLoopProgress">
            <div class="technical-loop-progress-head">
                <div>
                    <span class="technical-loop-eyebrow">TECHNICAL LLM LOOP</span>
                    <h3>完整 LLM 调优闭环运行中</h3>
                    <p id="technicalLoopProgressMessage">正在准备训练样本与候选验证任务...</p>
                </div>
                <div class="technical-loop-percent" id="technicalLoopProgressPercent">0%</div>
            </div>
            <div class="technical-loop-bar">
                <span id="technicalLoopProgressFill"></span>
            </div>
            <div class="technical-loop-metrics">
                <div>
                    <span>当前阶段</span>
                    <strong id="technicalLoopCurrentStage">准备中</strong>
                </div>
                <div>
                    <span>已运行</span>
                    <strong id="technicalLoopElapsed">0 秒</strong>
                </div>
                <div>
                    <span>预计剩余</span>
                    <strong id="technicalLoopRemaining">计算中</strong>
                </div>
                <div>
                    <span>样本规模</span>
                    <strong>${runtime.training.total} / ${runtime.holdout.total} / ${runtime.replaySamples}</strong>
                </div>
                <div>
                    <span>后端状态</span>
                    <strong id="technicalLoopBackendStage">等待确认</strong>
                </div>
            </div>
            <div class="technical-loop-steps">
                ${stages}
            </div>
            <p class="technical-loop-note">
                进度为前端按样本量、Replay 上限和历史耗时做的预计；后端当前一次性返回结果，真实耗时会受数据源、LLM 响应和网络波动影响。
            </p>
        </div>
    `;
}

function updateTechnicalLoopProgress(state) {
    const root = document.getElementById('technicalLoopProgress');
    if (!root || state.stopped) return;
    const elapsed = (Date.now() - state.startedAt) / 1000;
    const total = state.runtime.totalSeconds;
    const rawProgress = Math.min(0.98, elapsed / Math.max(total, 1));
    const easedProgress = 1 - Math.pow(1 - rawProgress, 1.18);
    const displayProgress = Math.min(96, Math.max(3, easedProgress * 96));
    const stageIndex = currentTechnicalLoopStageIndex(state.runtime.stages, elapsed);
    const stage = state.runtime.stages[stageIndex] || state.runtime.stages[0];
    const remaining = Math.max(0, total - elapsed);

    const fill = document.getElementById('technicalLoopProgressFill');
    const percent = document.getElementById('technicalLoopProgressPercent');
    const message = document.getElementById('technicalLoopProgressMessage');
    const currentStage = document.getElementById('technicalLoopCurrentStage');
    const elapsedEl = document.getElementById('technicalLoopElapsed');
    const remainingEl = document.getElementById('technicalLoopRemaining');
    const backendStageEl = document.getElementById('technicalLoopBackendStage');
    const backendStatus = describeTechnicalLoopBackendRun(state.backendRun);
    root.classList.toggle('possibly-stalled', Boolean(backendStatus.stale));
    if (fill) fill.style.width = `${displayProgress.toFixed(1)}%`;
    if (percent) percent.textContent = `${Math.round(displayProgress)}%`;
    if (message) {
        const overEstimate = elapsed > total;
        message.textContent = backendStatus.message || (overEstimate
            ? `${stage.label} 仍在执行，可能正在等待数据源或 LLM 返回。`
            : `${stage.label}: ${stage.detail}`);
    }
    if (currentStage) currentStage.textContent = backendStatus.stage || stage.label;
    if (elapsedEl) elapsedEl.textContent = formatLoopDuration(elapsed);
    if (remainingEl) {
        remainingEl.textContent = backendStatus.stale
            ? '等待最终报告'
            : elapsed > total
            ? '已超过预计，继续等待'
            : formatLoopDurationRange(remaining);
    }
    if (backendStageEl) backendStageEl.textContent = backendStatus.label || '等待确认';
    updateTechnicalLoopStepClasses(stageIndex);
    setLoopStageStripProgress(stageIndex, false);
}

function describeTechnicalLoopBackendRun(run) {
    if (!run) return {};
    const updatedAt = new Date(run.updated_at || 0).getTime();
    const idleMs = Number.isFinite(updatedAt) ? Date.now() - updatedAt : 0;
    const idleSeconds = Math.max(0, idleMs / 1000);
    const stale = run.status !== 'completed' && idleMs > TECH_LOOP_STALE_WARNING_MS;
    const statusLabel = run.status === 'completed'
        ? '已完成'
        : run.status === 'incomplete'
        ? '可能中断'
        : stale
        ? '可能停滞'
        : '验证中';
    const idleText = updatedAt
        ? `最后输出 ${formatLoopDuration(idleSeconds)} 前`
        : '等待输出文件';
    return {
        stale,
        stage: run.stage || '',
        label: `${statusLabel} · ${idleText}`,
        message: stale || run.status === 'incomplete'
            ? `${run.stage || '验证中'}，但 ${idleText}，最终 candidate_sandbox_report.json 尚未生成。`
            : run.stage
            ? `${run.stage} · ${idleText}`
            : '',
    };
}

function currentTechnicalLoopStageIndex(stages, elapsedSeconds) {
    const index = stages.findIndex(stage => elapsedSeconds < stage.end);
    return index >= 0 ? index : stages.length - 1;
}

function updateTechnicalLoopStepClasses(activeIndex) {
    document.querySelectorAll('[data-loop-progress-step]').forEach((el, index) => {
        el.classList.toggle('done', index < activeIndex);
        el.classList.toggle('active', index === activeIndex);
    });
}

function setLoopStageStripProgress(activeIndex, completed) {
    document.querySelectorAll('.loop-stage-strip span').forEach((el, index) => {
        el.classList.toggle('done', completed || index < activeIndex);
        el.classList.toggle('active', !completed && index === activeIndex);
    });
}

async function loadImprovementStatus() {
    const output = document.getElementById('improvementOutput');
    try {
        const resp = await fetch(`${API_BASE}/api/improvement/status`);
        if (!resp.ok) throw new Error('调优状态获取失败');
        improvementState = await resp.json();
        renderImprovementPermissions(improvementState.engineer || {});
        renderImprovementLanding(improvementState);
        loadPassiveSamples();
    } catch (e) {
        if (output) output.innerHTML = `<div class="empty-state error">加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderImprovementPermissions(engineer) {
    const grid = document.getElementById('permissionGrid');
    if (!grid) return;
    const auto = engineer.auto_apply || [];
    const draft = engineer.draft_only || [];
    grid.innerHTML = `
        <div class="permission-card allow">
            <span>自动允许</span>
            <strong>${auto.map(escapeHtml).join(' / ') || '无'}</strong>
        </div>
        <div class="permission-card review">
            <span>只提建议</span>
            <strong>${draft.map(escapeHtml).join(' / ') || '无'}</strong>
        </div>
    `;
}

function renderImprovementLanding(state) {
    const output = document.getElementById('improvementOutput');
    if (!output) return;
    const evaluations = state.latest_evaluations || [];
    const candidates = state.latest_candidate_sandboxes || [];
    const promptLoops = state.latest_technical_prompt_loops || [];
    const promptLoopRuns = state.latest_technical_prompt_loop_runs || [];
    const reports = state.latest_engineer_reports || [];
    const labs = state.latest_self_improvement_labs || [];
    const supportFiles = [...reports, ...evaluations, ...labs, ...candidates]
        .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
        .slice(0, 8);
    output.innerHTML = `
        <div class="improvement-result-grid">
            <div class="improvement-panel primary-result-panel">
                <h3>最近完整 LLM 调优闭环</h3>
                ${renderTechnicalLoopRuns(promptLoopRuns, promptLoops)}
            </div>
            <div class="improvement-panel">
                <h3>最近调优产物</h3>
                ${renderFileList(supportFiles)}
            </div>
        </div>
    `;
    output.querySelectorAll('[data-load-technical-loop-report]').forEach(btn => {
        btn.addEventListener('click', () => loadTechnicalLoopReport(btn.dataset.loadTechnicalLoopReport));
    });
}

function renderTechnicalLoopRuns(runs, completedFiles = []) {
    if (!runs || !runs.length) return renderFileList(completedFiles);
    return `
        <div class="technical-loop-run-list">
            ${runs.map(run => {
                const paths = run.paths || {};
                const done = run.status === 'completed';
                const statusLabel = done ? '完成' : run.status === 'incomplete' ? '未完成' : '运行中';
                const reportPath = paths.candidate_report_json || '';
                return `
                    <div class="technical-loop-run-card ${done ? 'completed' : run.status === 'incomplete' ? 'incomplete' : 'running'}">
                        <div class="technical-loop-run-head">
                            <div>
                                <strong>${escapeHtml(run.name || '技术面闭环')}</strong>
                                <p>${escapeHtml(run.stage || '')}</p>
                            </div>
                            <span>${statusLabel}</span>
                        </div>
                        <div class="technical-loop-run-grid">
                            <div><small>训练样本</small><b>${escapeHtml(String(run.training_samples ?? '-'))}</b></div>
                            <div><small>候选数</small><b>${escapeHtml(String(run.candidate_count ?? '-'))}</b></div>
                            <div><small>验证通过</small><b>${escapeHtml(String(run.validated_passed ?? '-'))}</b></div>
                            <div><small>更新时间</small><b>${escapeHtml(formatDate(run.updated_at))}</b></div>
                        </div>
                        <p class="technical-loop-run-message">${escapeHtml(run.message || '')}</p>
                        <div class="technical-loop-run-paths">
                            ${paths.training_json ? `<small>训练报告: ${escapeHtml(paths.training_json)}</small>` : ''}
                            ${paths.evaluation_json ? `<small>训练评估: ${escapeHtml(paths.evaluation_json)}</small>` : ''}
                            ${paths.candidate_root ? `<small>候选目录: ${escapeHtml(paths.candidate_root)}</small>` : ''}
                            ${reportPath ? `<small>最终报告: ${escapeHtml(reportPath)}</small>` : ''}
                        </div>
                        <div class="technical-loop-run-actions">
                            ${reportPath
                                ? `<button class="secondary-btn compact-btn" data-load-technical-loop-report="${escapeHtml(reportPath)}">查看结果</button>`
                                : '<span>最终报告尚未生成</span>'}
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

async function loadTechnicalLoopReport(path) {
    if (!path) return;
    const output = document.getElementById('improvementOutput');
    output.innerHTML = '<div class="empty-state">正在加载完整 LLM 调优闭环报告...</div>';
    try {
        const resp = await fetch(`${API_BASE}/api/improvement/report?path=${encodeURIComponent(path)}`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '报告加载失败');
        }
        const data = await resp.json();
        if (data.kind === 'technical_prompt_loop') {
            const reportPathInput = document.getElementById('improvementReportPath');
            if (reportPathInput) reportPathInput.value = data.generated_evaluation?.json || path;
            renderCandidateSandboxReport(data);
        } else {
            output.innerHTML = `<div class="detail-json">${escapeHtml(JSON.stringify(data.report || data, null, 2))}</div>`;
        }
    } catch (e) {
        output.innerHTML = `<div class="empty-state error">报告加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderFileList(files) {
    if (!files || !files.length) return '<div class="empty-inline">暂无文件</div>';
    return `
        <div class="file-list">
            ${files.map(file => `
                <button class="file-item" data-path="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}">
                    <span>${escapeHtml(file.name)}</span>
                    <small>${formatDate(file.updated_at)}</small>
                </button>
            `).join('')}
        </div>
    `;
}

async function loadPassiveSamples() {
    const list = document.getElementById('passiveSampleList');
    if (!list) return;
    list.innerHTML = '<div class="empty-inline">正在加载历史预测样本...</div>';
    try {
        const params = new URLSearchParams();
        params.set('verified', document.getElementById('passiveSampleStatus')?.value || 'verified');
        params.set('limit', String(readNumber('passiveSampleLimit', 200)));
        const target = document.getElementById('passiveSampleTarget')?.value || '';
        const timeframe = document.getElementById('passiveSampleTimeframe')?.value || '';
        if (target) params.set('target', target);
        if (timeframe) params.set('timeframe', timeframe);

        const resp = await fetch(`${API_BASE}/api/improvement/passive-samples?${params.toString()}`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '样本池加载失败');
        }
        const data = await resp.json();
        passiveSampleState = {
            samples: data.samples || [],
            summary: data.summary || {},
            help: data.parameter_help || [],
        };
        renderPassiveFilters(passiveSampleState.summary);
        renderPassiveParameterHelp(passiveSampleState.help);
        renderPassiveSampleSummary(passiveSampleState.summary, passiveSampleState.samples);
        renderPassiveSampleList();
        updatePassiveSelectedCount();
    } catch (e) {
        list.innerHTML = `<div class="empty-state error">样本池加载失败: ${escapeHtml(e.message)}</div>`;
    }
}

function renderPassiveFilters(summary) {
    const targetSelect = document.getElementById('passiveSampleTarget');
    const timeframeSelect = document.getElementById('passiveSampleTimeframe');
    if (targetSelect) {
        const current = targetSelect.value;
        const targets = summary.targets || [];
        targetSelect.innerHTML = '<option value="">全部标的</option>' + targets.map(item => {
            const label = item.target_name ? `${item.target_name}(${item.target})` : item.target;
            return `<option value="${escapeHtml(item.target)}">${escapeHtml(label)} · ${item.count}</option>`;
        }).join('');
        targetSelect.value = current;
    }
    if (timeframeSelect) {
        const current = timeframeSelect.value;
        const timeframes = summary.timeframes || [];
        timeframeSelect.innerHTML = '<option value="">全部周期</option>' + timeframes.map(item => (
            `<option value="${escapeHtml(item.timeframe)}">${escapeHtml(item.timeframe)} · ${item.count}</option>`
        )).join('');
        timeframeSelect.value = current;
    }
}

function renderPassiveParameterHelp(help) {
    const wrap = document.getElementById('passiveParameterHelp');
    if (!wrap) return;
    wrap.innerHTML = (help || []).slice(0, 5).map(item => `
        <details class="param-help-item">
            <summary>${escapeHtml(item.name || '')}</summary>
            <p>${escapeHtml(item.meaning || '')}</p>
            <small>${escapeHtml(item.tip || '')}</small>
        </details>
    `).join('');
}

function renderPassiveSampleSummary(summary, samples) {
    const el = document.getElementById('passiveSampleSummary');
    if (!el) return;
    const visibleVerified = samples.filter(s => s.eligible_for_tuning).length;
    el.innerHTML = `
        ${metricCard('历史预测', summary.total || 0)}
        ${metricCard('已验证', summary.verified || 0)}
        ${metricCard('观察中', summary.unverified || 0)}
        ${metricCard('平均 Brier', summary.avg_brier_score == null ? 'N/A' : Number(summary.avg_brier_score).toFixed(3))}
        ${metricCard('Edge 命中', summary.edge_hit_rate == null ? 'N/A' : formatPercent(summary.edge_hit_rate))}
        ${metricCard('当前可勾选', visibleVerified)}
    `;
}

function renderPassiveSampleList() {
    const list = document.getElementById('passiveSampleList');
    if (!list) return;
    const samples = passiveSampleState.samples || [];
    if (!samples.length) {
        list.innerHTML = '<div class="empty-inline">当前筛选下没有历史预测样本</div>';
        return;
    }
    list.innerHTML = samples.map(sample => {
        const checked = selectedPassiveSampleIds.has(sample.id) ? 'checked' : '';
        const disabled = sample.eligible_for_tuning ? '' : 'disabled';
        const dir = sample.direction || 'neutral';
        const correctness = sample.verified
            ? (sample.edge_hit ? '<span class="sample-pill ok">Edge命中</span>' : '<span class="sample-pill bad">Edge偏离</span>')
            : '<span class="sample-pill watch">观察中</span>';
        const edgeText = sample.edge_score == null ? 'N/A' : `${Math.round(Number(sample.edge_score) * 100)}%`;
        const expectedText = sample.expected_excess_return_pct == null ? 'N/A' : formatSignedPct(sample.expected_excess_return_pct);
        const brierText = sample.brier_score == null ? 'N/A' : Number(sample.brier_score).toFixed(3);
        return `
            <label class="passive-sample-row ${sample.eligible_for_tuning ? '' : 'disabled'}">
                <input type="checkbox" class="passive-sample-check" value="${escapeHtml(sample.id)}" ${checked} ${disabled}>
                <div class="passive-sample-main">
                    <div class="passive-sample-title">
                        <strong>${escapeHtml(sample.display_name || sample.target)}</strong>
                        <span class="agent-direction ${dir}">${escapeHtml(directionText(dir))}</span>
                        ${correctness}
                    </div>
                    <div class="passive-sample-meta">
                        ${escapeHtml(sample.timeframe || '')} · ${formatDate(sample.predicted_at)} · 置信 ${Math.round((sample.confidence || 0) * 100)}%
                    </div>
                    <div class="sample-edge-row">
                        ${escapeHtml(decisionLabel(sample.decision || 'observe'))} · Edge ${edgeText} · 预期 ${expectedText} · Brier ${brierText}
                    </div>
                    <div class="passive-sample-hint">${escapeHtml(sample.tuning_hint || '')}</div>
                </div>
                <div class="passive-sample-result">
                    <span>${sample.actual_effective_return_pct == null ? (sample.actual_change_pct == null ? '未验证' : formatSignedPct(sample.actual_change_pct)) : formatSignedPct(sample.actual_effective_return_pct)}</span>
                    <small>${sample.verified ? '有效收益' : '等待验证'}</small>
                </div>
            </label>
        `;
    }).join('');
    list.querySelectorAll('.passive-sample-check').forEach(input => {
        input.addEventListener('change', () => {
            if (input.checked) selectedPassiveSampleIds.add(input.value);
            else selectedPassiveSampleIds.delete(input.value);
            updatePassiveSelectedCount();
        });
    });
}

function directionText(direction) {
    return { bullish: '看涨', bearish: '看跌', neutral: '中性' }[direction] || direction || '未知';
}

function selectAllEligiblePassiveSamples() {
    (passiveSampleState.samples || []).forEach(sample => {
        if (sample.eligible_for_tuning) selectedPassiveSampleIds.add(sample.id);
    });
    renderPassiveSampleList();
    updatePassiveSelectedCount();
}

function clearPassiveSamples() {
    selectedPassiveSampleIds = new Set();
    renderPassiveSampleList();
    updatePassiveSelectedCount();
}

function updatePassiveSelectedCount() {
    const el = document.getElementById('passiveSelectedCount');
    if (el) el.textContent = `已选 ${selectedPassiveSampleIds.size} 条预测`;
}

async function runHistoricalEvaluation() {
    const output = document.getElementById('improvementOutput');
    const btn = document.getElementById('runEvaluationBtn');
    setButtonBusy(btn, true, '评估中...');
    output.innerHTML = '<div class="empty-state">正在读取已验证预测并生成历史评估...</div>';
    try {
        const payload = {
            min_samples: readNumber('improvementEvalMinSamples', 5),
            limit: readNumber('improvementLimit', 2000),
            prediction_ids: Array.from(selectedPassiveSampleIds),
            target: document.getElementById('passiveSampleTarget')?.value || null,
            timeframe: document.getElementById('passiveSampleTimeframe')?.value || null,
        };
        const resp = await fetch(`${API_BASE}/api/improvement/evaluate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '历史评估失败');
        }
        const data = await resp.json();
        document.getElementById('improvementReportPath').value = data.paths?.json || '';
        renderEvaluationReport(data);
    } catch (e) {
        output.innerHTML = `<div class="empty-state error">历史评估失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        setButtonBusy(btn, false);
    }
}

async function runImprovementEngineer() {
    const output = document.getElementById('improvementOutput');
    const btn = document.getElementById('runEngineerBtn');
    setButtonBusy(btn, true, '运行中...');
    output.innerHTML = '<div class="empty-state">Agent 改进工程师正在分析信号并生成受控改进...</div>';
    try {
        const reportPath = document.getElementById('improvementReportPath').value.trim();
        const payload = {
            report_path: reportPath || null,
            min_samples: readNumber('improvementMinSamples', 20),
            min_unique_cases: readNumber('improvementMinUniqueCases', 5),
            evaluation_min_samples: readNumber('improvementEvalMinSamples', 5),
            limit: readNumber('improvementLimit', 2000),
            prediction_ids: Array.from(selectedPassiveSampleIds),
            target: document.getElementById('passiveSampleTarget')?.value || null,
            timeframe: document.getElementById('passiveSampleTimeframe')?.value || null,
            dry_run: document.getElementById('improvementDryRun').checked,
            allow_prompt_apply: document.getElementById('improvementApplyPrompt').checked,
            allow_skill_apply: document.getElementById('improvementApplySkill').checked,
            use_llm_review: document.getElementById('improvementLlmReview').checked,
        };
        const resp = await fetch(`${API_BASE}/api/improvement/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '改进工程师运行失败');
        }
        const data = await resp.json();
        renderEngineerReport(data);
    } catch (e) {
        output.innerHTML = `<div class="empty-state error">运行失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        setButtonBusy(btn, false);
    }
}

async function runCandidateSandbox() {
    const output = document.getElementById('improvementOutput');
    const btn = document.getElementById('runCandidateSandboxBtn');
    setButtonBusy(btn, true, '验证中...');
    output.innerHTML = '<div class="empty-state">正在生成候选 prompt/skill，并用 holdout 样本做沙箱验证...</div>';
    try {
        const reportPath = document.getElementById('improvementReportPath').value.trim();
        const payload = {
            report_path: reportPath || null,
            min_samples: readNumber('improvementMinSamples', 20),
            min_unique_cases: readNumber('improvementMinUniqueCases', 5),
            evaluation_min_samples: readNumber('improvementEvalMinSamples', 5),
            limit: readNumber('improvementLimit', 2000),
            prediction_ids: Array.from(selectedPassiveSampleIds),
            target: document.getElementById('passiveSampleTarget')?.value || null,
            timeframe: document.getElementById('passiveSampleTimeframe')?.value || null,
            use_llm_candidates: document.getElementById('candidateUseLlm').checked,
            apply_if_passed: document.getElementById('candidateApplyIfPassed').checked,
            allow_prompt_promotion: document.getElementById('improvementApplyPrompt').checked,
            allow_skill_promotion: document.getElementById('improvementApplySkill').checked,
            validate_technical: true,
            holdout_targets: document.getElementById('candidateHoldoutTargets').value.trim(),
            holdout_start_date: document.getElementById('candidateHoldoutStartDate').value,
            holdout_end_date: document.getElementById('candidateHoldoutEndDate').value,
            holdout_timeframe: document.getElementById('candidateHoldoutTimeframe').value,
            holdout_interval_days: readNumber('candidateHoldoutIntervalDays', 14),
            min_holdout_samples: readNumber('candidateMinHoldoutSamples', 20),
            run_technical_prompt_replay: false,
            prompt_replay_max_samples: readNumber('promptReplayMaxSamples', 60),
            prompt_replay_min_samples: readNumber('promptReplayMinSamples', 30),
            candidate_batch_count: readNumber('candidateBatchCount', 5),
        };
        const resp = await fetch(`${API_BASE}/api/improvement/candidate-sandbox`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '候选验证沙箱失败');
        }
        const data = await resp.json();
        document.getElementById('improvementReportPath').value =
            data.generated_evaluation?.json || reportPath || '';
        renderCandidateSandboxReport(data);
    } catch (e) {
        output.innerHTML = `<div class="empty-state error">候选验证沙箱失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        setButtonBusy(btn, false);
    }
}

async function runTechnicalPromptLoop() {
    const output = document.getElementById('improvementOutput');
    const btn = document.getElementById('runTechnicalPromptLoopBtn');
    setButtonBusy(btn, true, '闭环运行中...');
    let progressState = null;
    const controller = new AbortController();
    try {
        const reportPath = document.getElementById('improvementReportPath').value.trim();
        const timeframe = document.getElementById('technicalLoopTimeframe')?.value
            || document.getElementById('timeframeSelect')?.value
            || '短期(1周)';
        const payload = {
            targets: document.getElementById('selfLabTargets').value.trim(),
            start_date: document.getElementById('selfLabStartDate').value,
            end_date: document.getElementById('selfLabEndDate').value,
            timeframe,
            interval_days: readNumber('selfLabIntervalDays', 14),
            min_samples: TECH_LOOP_RULE_MIN_SAMPLES,
            min_unique_cases: TECH_LOOP_RULE_MIN_UNIQUE_CASES,
            use_llm_candidates: true,
            apply_if_passed: Boolean(document.getElementById('candidateApplyIfPassed')?.checked),
            allow_prompt_promotion: Boolean(document.getElementById('improvementApplyPrompt')?.checked),
            allow_skill_promotion: Boolean(document.getElementById('improvementApplySkill')?.checked),
            holdout_targets: document.getElementById('candidateHoldoutTargets').value.trim(),
            holdout_start_date: document.getElementById('candidateHoldoutStartDate').value,
            holdout_end_date: document.getElementById('candidateHoldoutEndDate').value,
            holdout_timeframe: document.getElementById('candidateHoldoutTimeframe').value,
            holdout_interval_days: readNumber('candidateHoldoutIntervalDays', 14),
            min_holdout_samples: readNumber('candidateMinHoldoutSamples', 20),
            run_technical_prompt_replay: true,
            prompt_replay_max_samples: readNumber('promptReplayMaxSamples', 60),
            prompt_replay_min_samples: readNumber('promptReplayMinSamples', 30),
            candidate_batch_count: readNumber('candidateBatchCount', 5),
        };
        progressState = startTechnicalLoopProgress(payload, controller);
        const resp = await fetch(`${API_BASE}/api/improvement/technical-prompt-loop`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal,
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '技术面 LLM 调优闭环失败');
        }
        const data = await resp.json();
        if (progressState?.completedFromPoll) return;
        document.getElementById('improvementReportPath').value =
            data.generated_evaluation?.json || reportPath || '';
        stopTechnicalLoopProgress(progressState, true);
        renderCandidateSandboxReport(data);
    } catch (e) {
        if (e.name === 'AbortError' && progressState?.completedFromPoll) return;
        stopTechnicalLoopProgress(progressState, false);
        output.innerHTML = `<div class="empty-state error">技术面 LLM 调优闭环失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        stopTechnicalLoopProgress(progressState, false);
        setButtonBusy(btn, false);
    }
}

async function runSelfImprovementLab() {
    const output = document.getElementById('improvementOutput');
    const btn = document.getElementById('runSelfLabBtn');
    setButtonBusy(btn, true, '生成中...');
    output.innerHTML = '<div class="empty-state">正在构造主动历史样本池并生成调优评估...</div>';
    try {
        const newsPath = document.getElementById('selfLabNewsSnapshots').value.trim();
        const pitPath = document.getElementById('selfLabPitSnapshots').value.trim();
        const payload = {
            targets: document.getElementById('selfLabTargets').value.trim(),
            start_date: document.getElementById('selfLabStartDate').value,
            end_date: document.getElementById('selfLabEndDate').value,
            timeframe: document.getElementById('timeframeSelect').value,
            interval_days: readNumber('selfLabIntervalDays', 14),
            evaluation_min_samples: readNumber('improvementEvalMinSamples', 5),
            run_engineer: document.getElementById('selfLabRunEngineer').checked,
            engineer_min_samples: readNumber('improvementMinSamples', 20),
            engineer_min_unique_cases: readNumber('improvementMinUniqueCases', 5),
            dry_run: document.getElementById('improvementDryRun').checked,
            allow_prompt_apply: document.getElementById('improvementApplyPrompt').checked,
            allow_skill_apply: document.getElementById('improvementApplySkill').checked,
            news_snapshots_path: newsPath || null,
            point_in_time_snapshots_path: pitPath || null,
        };
        const resp = await fetch(`${API_BASE}/api/improvement/self-bootstrap`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '主动历史样本池生成失败');
        }
        const data = await resp.json();
        document.getElementById('improvementReportPath').value =
            data.report?.evaluation_paths?.json || '';
        renderSelfLabReport(data);
    } catch (e) {
        output.innerHTML = `<div class="empty-state error">主动历史样本池失败: ${escapeHtml(e.message)}</div>`;
    } finally {
        setButtonBusy(btn, false);
    }
}

function renderEvaluationReport(data) {
    const output = document.getElementById('improvementOutput');
    const report = data.report || {};
    const agents = report.agents || {};
    const selection = data.selection || {};
    output.innerHTML = `
        <div class="improvement-summary">
            ${metricCard('Agent 样本', report.total_samples || 0)}
            ${metricCard('已验证预测', report.verified_predictions || 0)}
            ${metricCard('手动选择', selection.selected_predictions || 0)}
            ${metricCard('错误策略', (report.wrong_strategy_signals || []).length)}
            ${metricCard('优势信号', (report.strength_signals || []).length)}
        </div>
        <div class="improvement-panel">
            <h3>Agent 历史表现</h3>
            <div class="agent-score-list">
                ${Object.entries(agents).map(([name, stats]) => renderAgentScore(name, stats)).join('') || '<div class="empty-inline">暂无已验证样本</div>'}
            </div>
        </div>
        <div class="path-note">报告: ${escapeHtml(data.paths?.markdown || data.paths?.json || '')}</div>
    `;
}

function renderSelfLabReport(data) {
    const output = document.getElementById('improvementOutput');
    const report = data.report || {};
    const deferred = report.deferred_agents || [];
    const evalSummary = report.evaluation_summary || {};
    const engineerSummary = report.engineer_summary || {};
    output.innerHTML = `
        <div class="improvement-summary">
            ${metricCard('历史样本', report.total_samples || 0)}
            ${metricCard('覆盖 Agent', (report.supported_agents || []).length)}
            ${metricCard('错误策略', evalSummary.wrong_strategy_signals || 0)}
            ${metricCard('工程动作', engineerSummary.actions || 0)}
        </div>
        <div class="improvement-panel">
            <h3>样本池输出</h3>
            <div class="agent-score-list">
                ${renderKeyValue('覆盖 Agent', (report.supported_agents || []).join(' / ') || '无')}
                ${renderKeyValue('输出目录', report.output_dir || '')}
                ${renderKeyValue('历史评估', report.evaluation_paths?.markdown || report.evaluation_paths?.json || '')}
            </div>
        </div>
        <div class="improvement-panel">
            <h3>待补历史快照</h3>
            <div class="action-list">
                ${deferred.map(item => `
                    <div class="action-item skipped">
                        <div>
                            <strong>${escapeHtml(item.agent_name || '')}</strong>
                            <p>${escapeHtml(item.reason || '')}</p>
                        </div>
                        <span>deferred</span>
                    </div>
                `).join('') || '<div class="empty-inline">暂无</div>'}
            </div>
        </div>
        <div class="path-note">报告: ${escapeHtml(data.report_paths?.markdown || '')}</div>
    `;
}

function renderKeyValue(label, value) {
    return `
        <div class="agent-score">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(value)}</span>
        </div>
    `;
}

function renderEngineerReport(data) {
    const output = document.getElementById('improvementOutput');
    const report = data.report || {};
    const actions = report.actions || [];
    const protectedItems = report.protected_recommendations || [];
    output.innerHTML = `
        <div class="improvement-summary">
            ${metricCard('动作数', actions.length)}
            ${metricCard('已应用/演练', (report.applied_paths || []).length)}
            ${metricCard('需人工确认', protectedItems.length)}
            ${metricCard('输出目录', data.output_dir ? '已生成' : 'N/A')}
        </div>
        <div class="improvement-panel">
            <h3>执行动作</h3>
            <div class="action-list">
                ${actions.map(renderImprovementAction).join('') || '<div class="empty-inline">暂无达到阈值的动作</div>'}
            </div>
        </div>
        <div class="improvement-panel">
            <h3>需人工确认</h3>
            <div class="action-list">
                ${protectedItems.map(renderProtectedItem).join('') || '<div class="empty-inline">暂无高风险建议</div>'}
            </div>
        </div>
        <div class="path-note">报告: ${escapeHtml(data.report_paths?.markdown || '')}</div>
    `;
}

function renderCandidateSandboxReport(data) {
    const output = document.getElementById('improvementOutput');
    const report = data.report || {};
    const summary = report.summary || {};
    const training = data.training_report || {};
    const artifacts = report.artifacts || [];
    const decisions = report.decisions || [];
    output.innerHTML = `
        <div class="improvement-summary">
            ${metricCard('训练样本', training.success_samples || '-')}
            ${metricCard('候选数', summary.artifacts || 0)}
            ${metricCard('验证通过', summary.validated_passed || 0)}
            ${metricCard('验证失败', summary.validated_failed || 0)}
            ${metricCard('晋升文件', summary.promoted_paths || 0)}
            ${metricCard('Registry Skill', summary.registry_skills || 0)}
        </div>
        <div class="improvement-panel">
            <h3>候选改法</h3>
            <div class="action-list">
                ${artifacts.map(renderCandidateArtifact).join('') || '<div class="empty-inline">暂无达到阈值的候选</div>'}
            </div>
        </div>
        <div class="improvement-panel">
            <h3>验证门禁</h3>
            <div class="action-list">
                ${decisions.map(renderSandboxDecision).join('') || '<div class="empty-inline">暂无验证结果</div>'}
            </div>
        </div>
        <div class="improvement-panel">
            <h3>输出路径</h3>
            <div class="agent-score-list">
                ${renderKeyValue('候选目录', report.candidate_root || data.candidate_root || '')}
                ${data.report_paths?.training_json ? renderKeyValue('训练报告', data.report_paths.training_json) : ''}
                ${renderKeyValue('报告', data.report_paths?.markdown || data.report_paths?.json || '')}
                ${renderKeyValue('晋升文件', (report.promoted_paths || []).join(' / ') || '无')}
            </div>
        </div>
    `;
}

function renderCandidateArtifact(artifact) {
    const cls = artifact.status === 'applied' || artifact.status === 'validated_passed' ? 'applied'
        : artifact.status === 'validated_failed' ? 'protected'
        : 'skipped';
    return `
        <div class="action-item ${cls}">
            <div>
                <strong>${escapeHtml(artifact.agent_name || '')} · ${escapeHtml(artifact.area || '')}</strong>
                <p>${escapeHtml(artifact.title || '')} · ${escapeHtml(artifact.reason || '')}</p>
                <p>样本 ${escapeHtml(String(artifact.sample_size || 0))} · 独立案例 ${escapeHtml(String(artifact.unique_cases || 0))}</p>
                ${artifact.content_path ? `<small>${escapeHtml(artifact.content_path)}</small>` : ''}
                ${artifact.promotion_path ? `<small>晋升: ${escapeHtml(artifact.promotion_path)}</small>` : ''}
            </div>
            <span>${escapeHtml(artifact.status || 'draft')}</span>
        </div>
    `;
}

function renderSandboxDecision(decision) {
    const metrics = decision.metrics || {};
    const details = [
        metrics.holdout_samples !== undefined ? `holdout ${metrics.holdout_samples}` : '',
        metrics.changed_predictions !== undefined ? `改变 ${metrics.changed_predictions}` : '',
        metrics.baseline_accuracy !== undefined ? `base命中 ${Math.round(metrics.baseline_accuracy * 1000) / 10}%` : '',
        metrics.candidate_accuracy !== undefined ? `cand命中 ${Math.round(metrics.candidate_accuracy * 1000) / 10}%` : '',
        metrics.accuracy_delta !== undefined ? `命中 ${Math.round(metrics.accuracy_delta * 1000) / 10}%` : '',
        metrics.baseline_brier !== undefined ? `baseBrier ${metrics.baseline_brier}` : '',
        metrics.candidate_brier !== undefined ? `candBrier ${metrics.candidate_brier}` : '',
        metrics.brier_delta !== undefined ? `Brier ${metrics.brier_delta}` : '',
        metrics.overconfidence_delta !== undefined ? `过度自信 ${Math.round(metrics.overconfidence_delta * 1000) / 10}%` : '',
    ].filter(Boolean).join(' · ');
    const cls = decision.should_apply ? 'applied' : 'skipped';
    return `
        <div class="action-item ${cls}">
            <div>
                <strong>${escapeHtml(decision.name || '')}</strong>
                <p>${escapeHtml(decision.reason || '')}</p>
                ${details ? `<p>${escapeHtml(details)}</p>` : ''}
            </div>
            <span>${decision.should_apply ? 'pass' : 'fail'}</span>
        </div>
    `;
}

// ============================================================
// Quant 验证中心
// ============================================================

function initQuant() {
    document.getElementById('refreshQuantBtn')?.addEventListener('click', loadQuantStatus);
    document.getElementById('refreshQuantEnrichmentBtn')?.addEventListener('click', refreshQuantEnrichment);
    document.getElementById('buildQuantDatasetBtn')?.addEventListener('click', buildQuantDataset);
    document.getElementById('runQuantWalkForwardBtn')?.addEventListener('click', runQuantWalkForward);
    document.getElementById('trainQuantAggregatorBtn')?.addEventListener('click', trainQuantAggregator);
    document.getElementById('runPortfolioBacktestBtn')?.addEventListener('click', runQuantPortfolioBacktest);
    document.getElementById('runEvidenceMaintenanceBtn')?.addEventListener('click', runEvidenceMaintenance);
}

async function loadQuantStatus() {
    const output = document.getElementById('quantOutput');
    try {
        const response = await fetch(`${API_BASE}/api/quant/status`);
        if (!response.ok) throw new Error('Quant 状态加载失败');
        quantState = await response.json();
        renderQuantStatus(quantState);
    } catch (error) {
        if (output) output.innerHTML = `<div class="empty-state error">${escapeHtml(error.message)}</div>`;
    }
}

function renderQuantStatus(data) {
    const store = data.feature_store || {};
    const activeStore = store.active_version || store;
    const enrichment = data.pit_enrichment || {};
    const aggregators = data.learned_aggregators || [];
    const evidence = data.evidence_maintenance || {};
    const latestResearch = (data.latest_research_data_v2 || [])[0] || {};
    const snapshotCount = (evidence.point_in_time_snapshots || 0) + (evidence.news_snapshots || 0);
    const enabled = aggregators.filter(item => item.enabled && !item.shadow_only).length;
    const enrichmentEvents = (enrichment.fundamental_events?.records || 0) + (enrichment.performance_events?.records || 0) + (enrichment.announcement_events?.records || 0) + (enrichment.industry_memberships?.records || 0);
    const summary = document.getElementById('quantStatusSummary');
    if (summary) {
        summary.innerHTML = `
            ${metricCard('PIT 样本', activeStore.total || 0)}
            ${metricCard('已标注', activeStore.labeled || 0)}
            ${metricCard('独立日期', activeStore.unique_dates || 0)}
            ${metricCard('残差标签', `${Math.round((activeStore.residual_coverage || 0) * 100)}%`)}
            ${metricCard('Agent 快照', snapshotCount)}
            ${metricCard('PIT 事件', enrichmentEvents)}
            ${metricCard('启用模型', enabled)}
        `;
    }
    const dependencies = Object.entries(data.dependencies || {}).map(([name, item]) => `
        <div class="quant-dependency ${item.available ? 'ready' : 'missing'}">
            <strong>${escapeHtml(name)}</strong>
            <span>${item.available ? escapeHtml(item.version || 'ready') : '未安装'}</span>
        </div>
    `).join('');
    const partitions = (store.partitions || []).map(item => `
        <tr><td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.horizon)}</td><td>${escapeHtml(item.feature_version || 'legacy')}</td><td>${item.total}</td><td>${item.labeled || 0}</td><td>${item.residual_labeled || 0}</td><td>${item.unique_dates || 0}</td><td>${item.unique_symbols || 0}</td></tr>
    `).join('');
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-panel">
            <h3>运行环境</h3>
            <div class="quant-dependencies">${dependencies}</div>
        </div>
        <div class="improvement-panel">
            <h3>特征分区</h3>
            <div class="table-wrap"><table class="quant-table"><thead><tr><th>市场</th><th>周期</th><th>版本</th><th>样本</th><th>标签</th><th>残差</th><th>日期</th><th>股票</th></tr></thead><tbody>${partitions || '<tr><td colspan="8">尚未构建 PIT 特征</td></tr>'}</tbody></table></div>
        </div>
        <div class="improvement-panel">
            <h3>证据闭环</h3>
            <p>待验证 ${evidence.verification_queue?.pending || 0} · 已到期 ${evidence.verification_queue?.overdue || 0} · 已验证 ${evidence.verification_queue?.verified || 0}</p>
            <p>基本面/行业/宏观快照 ${evidence.point_in_time_snapshots || 0} · 新闻快照 ${evidence.news_snapshots || 0}</p>
            <p>公告日财报 ${enrichment.fundamental_events?.records || 0} · 业绩预告/快报 ${enrichment.performance_events?.records || 0} · 官方公告 ${enrichment.announcement_events?.records || 0} · 行业有效区间 ${enrichment.industry_memberships?.records || 0}</p>
        </div>
        ${latestResearch.path ? `<div class="improvement-panel"><h3>最近 Research Data V2</h3><p>${escapeHtml(latestResearch.updated_at || '')}</p><div class="path-note">${escapeHtml(latestResearch.path)}</div></div>` : ''}
        <div class="path-note">特征库: ${escapeHtml(store.db_path || '')}</div>
    `;
}

async function buildQuantDataset() {
    const button = document.getElementById('buildQuantDatasetBtn');
    const payload = {
        targets: document.getElementById('quantTargets').value.trim(),
        start_date: document.getElementById('quantStartDate').value,
        end_date: document.getElementById('quantEndDate').value,
        timeframe: document.getElementById('quantTimeframe').value,
        interval_days: readNumber('quantIntervalDays', 7),
        lookback_days: 180,
        max_samples: readNumber('quantMaxSamples', 60000),
        export_parquet: true,
        use_universe: Boolean(document.getElementById('quantUseUniverse').checked),
        universe_market: 'A',
        universe_limit: readNumber('quantUniverseLimit', 200),
        min_listing_days: readNumber('quantMinListingDays', 120),
        min_price: 1.0,
        min_avg_traded_value: 0,
        universe_stratify: Boolean(document.getElementById('quantUniverseStratify').checked),
        use_pit_enrichment: Boolean(document.getElementById('quantUsePitEnrichment').checked),
        fundamental_max_age_days: 550,
        announcement_lookback_days: 90,
        use_price_cache: true,
    };
    await runQuantAction(button, '/api/quant/build-dataset', payload, '正在构建严格按时点截断的历史特征...', renderQuantDatasetReport);
}

async function refreshQuantEnrichment() {
    const button = document.getElementById('refreshQuantEnrichmentBtn');
    const payload = {
        targets: document.getElementById('quantTargets').value.trim(),
        start_date: document.getElementById('quantStartDate').value,
        end_date: document.getElementById('quantEndDate').value,
        use_universe: Boolean(document.getElementById('quantUseUniverse').checked),
        universe_limit: readNumber('quantUniverseLimit', 200),
        min_listing_days: readNumber('quantMinListingDays', 120),
        interval_days: readNumber('quantIntervalDays', 7),
        universe_stratify: Boolean(document.getElementById('quantUniverseStratify').checked),
        concurrency: 3,
        include_fundamental: true,
        include_performance: true,
        include_announcements: true,
        include_industry: true,
    };
    await runQuantAction(button, '/api/quant/refresh-enrichment', payload, '正在采集公告日财报、官方公告和历史行业有效区间...', renderQuantEnrichmentReport);
}

async function runQuantWalkForward() {
    const button = document.getElementById('runQuantWalkForwardBtn');
    const payload = {
        market: document.getElementById('quantMarket').value,
        horizon: document.getElementById('quantHorizon').value,
        feature_version: 'quant_features.v3',
        model_names: ['ridge', 'logistic', 'lightgbm'],
        train_days: readNumber('quantTrainDays', 730),
        validation_days: readNumber('quantValidationDays', 120),
        test_days: readNumber('quantTestDays', 120),
        purge_days: readNumber('quantPurgeDays', 7),
        lockbox_days: readNumber('quantLockboxDays', 180),
        min_train_samples: readNumber('quantMinTrain', 3000),
        min_validation_samples: 400,
        min_test_samples: 400,
        min_unique_train_dates: readNumber('quantMinTrainDates', 80),
        unlock_lockbox: Boolean(document.getElementById('quantUnlockLockbox').checked),
        calibrate_probabilities: Boolean(document.getElementById('quantProbabilityCalibration').checked),
        calibration_method: 'temperature',
        calibration_min_samples: 400,
        enable_industry_stacking: Boolean(document.getElementById('quantIndustryStacking').checked),
        max_industry_stack_weight: 0.35,
        min_industry_stack_brier_delta: 0.0001,
        min_actionable_coverage: 0.01,
        feature_set_names: document.getElementById('quantFeatureAblation').checked
            ? ['technical', 'technical_fundamental', 'technical_news', 'technical_industry', 'technical_valuation', 'research_v2']
            : ['all'],
    };
    await runQuantAction(button, '/api/quant/walk-forward', payload, '正在滚动训练并评估三个影子基线...', renderQuantWalkForwardReport);
}

async function trainQuantAggregator() {
    const button = document.getElementById('trainQuantAggregatorBtn');
    const payload = {
        market: document.getElementById('quantMarket').value,
        horizon: document.getElementById('quantHorizon').value,
        min_samples: readNumber('quantAggregatorMinSamples', 200),
        min_unique_dates: readNumber('quantAggregatorMinDates', 60),
        purge_days: readNumber('quantPurgeDays', 7),
        lockbox_days: readNumber('quantLockboxDays', 90),
        min_brier_delta: Number(document.getElementById('quantAggregatorBrier').value || 0.005),
        min_folds: readNumber('quantAggregatorMinFolds', 3),
        activate_if_passed: Boolean(document.getElementById('quantAggregatorActivate').checked),
    };
    await runQuantAction(button, '/api/quant/train-aggregator', payload, '正在用样本外 Agent 概率学习受约束权重...', renderQuantAggregatorReport);
}

async function runQuantPortfolioBacktest() {
    const button = document.getElementById('runPortfolioBacktestBtn');
    const market = document.getElementById('quantMarket').value;
    const payload = {
        prediction_paths: document.getElementById('quantOofPaths').value.split(/\n|,/).map(value => value.trim()).filter(Boolean),
        market,
        model_name: document.getElementById('quantPortfolioModel').value,
        horizon_trading_days: Number(document.getElementById('quantHorizon').value.replace('d', '')) || 5,
        top_k: readNumber('quantTopK', 10),
        bottom_k: readNumber('quantBottomK', 0),
        allow_short: Boolean(document.getElementById('quantAllowShort').checked),
        min_edge_score: Number(document.getElementById('quantMinEdge').value || 0.10),
        max_position_weight: Number(document.getElementById('quantMaxWeight').value || 0.20),
        volatility_weighted: true,
        initial_capital: 1000000,
        extra_borrow_cost_bps: market === 'US' ? 20 : 0,
        allow_overlapping_horizons: false,
        min_avg_traded_value: 0,
        max_participation_rate: 0.05,
        impact_coefficient_bps: 15,
    };
    await runQuantAction(button, '/api/quant/portfolio-backtest', payload, '正在模拟仓位、换手和市场交易成本...', renderQuantPortfolioReport);
}

async function runEvidenceMaintenance() {
    const button = document.getElementById('runEvidenceMaintenanceBtn');
    const payload = {
        collect_snapshots: Boolean(document.getElementById('quantCollectSnapshots').checked),
        targets: [],
        recent_target_limit: readNumber('quantRecentTargetLimit', 30),
        timeframe: '短期(1周)',
        news_mode: 'evidence',
        max_snapshots: 0,
    };
    await runQuantAction(button, '/api/evidence-maintenance/run', payload, '正在验证到期预测并维护时点证据...', renderEvidenceMaintenanceReport);
}

async function runQuantAction(button, endpoint, payload, loadingText, renderer) {
    const output = document.getElementById('quantOutput');
    setButtonBusy(button, true, '运行中...');
    output.innerHTML = `<div class="empty-state"><span class="spinner"></span> ${escapeHtml(loadingText)}</div>`;
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Quant 任务失败');
        renderer(data);
        await refreshQuantSummaryOnly();
    } catch (error) {
        output.innerHTML = `<div class="empty-state error">${escapeHtml(error.message)}</div>`;
    } finally {
        setButtonBusy(button, false);
    }
}

async function refreshQuantSummaryOnly() {
    try {
        const response = await fetch(`${API_BASE}/api/quant/status`);
        if (!response.ok) return;
        const data = await response.json();
        quantState = data;
        const store = data.feature_store || {};
        const enrichment = data.pit_enrichment || {};
        const evidence = data.evidence_maintenance || {};
        const snapshotCount = (evidence.point_in_time_snapshots || 0) + (evidence.news_snapshots || 0);
        const enrichmentEvents = (enrichment.fundamental_events?.records || 0) + (enrichment.announcement_events?.records || 0) + (enrichment.industry_memberships?.records || 0);
        const enabled = (data.learned_aggregators || []).filter(item => item.enabled && !item.shadow_only).length;
        document.getElementById('quantStatusSummary').innerHTML = `${metricCard('PIT 样本', store.total || 0)}${metricCard('已标注', store.labeled || 0)}${metricCard('独立日期', store.unique_dates || 0)}${metricCard('残差标签', `${Math.round((store.residual_coverage || 0) * 100)}%`)}${metricCard('Agent 快照', snapshotCount)}${metricCard('PIT 事件', enrichmentEvents)}${metricCard('启用模型', enabled)}`;
    } catch (_) {
        // Main result remains visible when a background status refresh fails.
    }
}

function renderQuantDatasetReport(data) {
    const report = data.report || {};
    const status = report.feature_store_status || {};
    const coverage = report.feature_coverage || {};
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('本轮写入', report.saved || 0)}${metricCard('累计样本', status.total || 0)}${metricCard('基本面可用', `${Math.round((coverage.fundamental?.coverage || 0) * 100)}%`)}${metricCard('高质量基本面', `${Math.round((coverage.fundamental_high_quality?.coverage || 0) * 100)}%`)}${metricCard('业绩事件', `${Math.round((coverage.performance?.coverage || 0) * 100)}%`)}${metricCard('Surprise', `${Math.round((coverage.surprise?.coverage || 0) * 100)}%`)}${metricCard('公告覆盖', `${Math.round((coverage.news?.coverage || 0) * 100)}%`)}${metricCard('行业覆盖', `${Math.round((coverage.industry?.coverage || 0) * 100)}%`)}${metricCard('跳过', (report.skipped || []).length)}</div>
        <div class="improvement-panel"><h3>数据血缘结果</h3><p>行情、公告日财报、官方公告和行业有效区间均按 as_of 截断；未来价格只用于标签。</p><p>Parquet 分区 ${report.parquet_paths?.length || 0} 个 · 行情缓存 ${report.price_cache_status?.symbols || 0} 只。</p></div>
        <div class="path-note">报告: ${escapeHtml(data.report_path || '')}</div>
    `;
}

function renderQuantEnrichmentReport(data) {
    const report = data.report || {};
    const saved = report.saved || {};
    const status = report.status || {};
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('公告日财报', saved.fundamental || 0)}${metricCard('业绩预告/快报', saved.performance || 0)}${metricCard('官方公告', saved.announcements || 0)}${metricCard('行业区间', saved.industry || 0)}${metricCard('错误', (report.errors || []).length)}</div>
        <div class="improvement-panel"><h3>PIT 丰富特征仓库</h3><p>财报标的 ${status.fundamental_events?.symbols || 0} · 业绩事件标的 ${status.performance_events?.symbols || 0} · 公告标的 ${status.announcement_events?.symbols || 0} · 行业标的 ${status.industry_memberships?.symbols || 0}</p><p>${(report.errors || []).length ? (report.errors || []).slice(0, 10).map(item => `${escapeHtml(item.symbol)} ${escapeHtml(item.source)}: ${escapeHtml(item.reason)}`).join('<br>') : '所有来源刷新完成。'}</p></div>
        <div class="path-note">报告: ${escapeHtml(data.report_path || '')}</div>
    `;
}

function renderQuantWalkForwardReport(data) {
    const report = data.report || {};
    const gate = report.promotion_gate || {};
    const metrics = report.aggregate_metrics || {};
    const rows = Object.entries(metrics).map(([name, item]) => `<tr><td>${escapeHtml(name)}</td><td>${item.folds || 0}</td><td>${item.samples || 0}</td><td>${formatQuantDecimal(item.direction_accuracy)}</td><td>${formatQuantDecimal(item.brier_score, 4)}</td><td>${formatQuantDecimal(item.calibration_brier_delta, 4)}</td><td>${formatQuantDecimal(item.expected_calibration_error, 4)}</td><td>${formatQuantDecimal(item.industry_stack_weight, 3)}</td><td>${formatQuantDecimal(item.rank_ic, 4)}</td><td>${formatQuantPct(item.avg_directional_return_pct)}</td></tr>`).join('');
    const oofPaths = Object.entries(report.artifact_paths || {}).filter(([key]) => key.endsWith('_oof')).map(([, value]) => value);
    if (oofPaths.length) document.getElementById('quantOofPaths').value = oofPaths.join('\n');
    const portfolioModel = document.getElementById('quantPortfolioModel');
    if (portfolioModel && gate.best_model) {
        if (![...portfolioModel.options].some(option => option.value === gate.best_model)) {
            portfolioModel.add(new Option(gate.best_model, gate.best_model));
        }
        portfolioModel.value = gate.best_model;
    }
    const ablationRows = Object.entries(report.feature_ablation || {}).flatMap(([model, item]) => Object.entries(item.comparisons || {}).map(([featureSet, metrics]) => `<tr><td>${escapeHtml(model)}</td><td>${escapeHtml(featureSet)}</td><td>${formatQuantDecimal(metrics.brier_delta_vs_technical, 4)}</td><td>${formatQuantDecimal(metrics.rank_ic_delta_vs_technical, 4)}</td><td>${formatQuantPct(metrics.directional_return_delta_pct)}</td></tr>`)).join('');
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('Fold', (report.folds || []).length)}${metricCard('开发样本', report.data_summary?.development_rows || 0)}${metricCard('最佳模型', gate.best_model || '无')}${metricCard('门禁', gate.should_promote ? '通过' : 'Shadow')}</div>
        <div class="improvement-panel"><h3>样本外对比</h3><div class="table-wrap"><table class="quant-table"><thead><tr><th>模型</th><th>Fold</th><th>样本</th><th>命中</th><th>Brier</th><th>校准改善</th><th>ECE</th><th>行业权重</th><th>Rank IC</th><th>方向收益</th></tr></thead><tbody>${rows}</tbody></table></div></div>
        ${ablationRows ? `<div class="improvement-panel"><h3>特征族增量</h3><div class="table-wrap"><table class="quant-table"><thead><tr><th>模型</th><th>特征集</th><th>Brier 改善</th><th>Rank IC 增量</th><th>方向收益增量</th></tr></thead><tbody>${ablationRows}</tbody></table></div></div>` : ''}
        <div class="improvement-panel"><h3>Lockbox</h3><p>${escapeHtml(report.lockbox?.status || '')} · ${escapeHtml(report.lockbox?.note || '')} · 样本 ${report.lockbox?.samples || 0}</p><p>${escapeHtml(gate.reason || '')}</p></div>
        <div class="path-note">报告: ${escapeHtml(data.report_path || '')}</div>
    `;
}

function renderQuantAggregatorReport(data) {
    const artifact = data.artifact || {};
    const validation = artifact.validation || {};
    const weights = Object.entries(artifact.weights || {}).map(([name, value]) => `<div class="agent-score"><strong>${escapeHtml(name)}</strong><span>${Math.round(value * 1000) / 10}%</span></div>`).join('');
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('训练样本', artifact.training_samples || 0)}${metricCard('验证样本', artifact.validation_samples || 0)}${metricCard('Brier 提升', formatQuantDecimal(validation.brier_delta, 4))}${metricCard('状态', artifact.enabled ? 'Enabled' : 'Shadow')}</div>
        <div class="improvement-panel"><h3>学习权重</h3><div class="agent-score-list">${weights}</div></div>
        <div class="improvement-panel"><h3>多折门禁</h3><p>${validation.passed ? '验证通过' : '未通过'} · ${validation.folds?.length || 0} 折 · 正改善 ${validation.positive_folds || 0} 折 · Brier CI [${formatQuantDecimal(validation.brier_delta_ci_low, 4)}, ${formatQuantDecimal(validation.brier_delta_ci_high, 4)}] · Lockbox ${escapeHtml(validation.lockbox_status || 'locked')}</p></div>
    `;
}

function renderQuantPortfolioReport(data) {
    const report = data.report || {};
    const metrics = report.metrics || {};
    const benchmark = report.benchmark_metrics || {};
    const excess = report.excess_metrics || {};
    const periods = report.periods || [];
    const rows = periods.slice(-20).reverse().map(item => `<tr><td>${escapeHtml(item.as_of)}</td><td>${formatQuantPct(item.gross_return_pct)}</td><td>${formatQuantPct(item.net_return_pct)}</td><td>${formatQuantPct(item.transaction_cost_pct)}</td><td>${formatQuantDecimal(item.turnover, 2)}</td><td>${item.positions}</td><td>${formatQuantPct(item.drawdown_pct)}</td></tr>`).join('');
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('净收益', formatQuantPct(metrics.total_return_pct))}${metricCard('基准收益', formatQuantPct(benchmark.total_return_pct))}${metricCard('超额收益', formatQuantPct(excess.total_return_pct))}${metricCard('信息比率', formatQuantDecimal(excess.information_ratio, 2))}${metricCard('平均敞口', formatQuantPct((metrics.avg_gross_exposure || 0) * 100))}${metricCard('容量缩减', metrics.capacity_clipped_orders || 0)}${metricCard('最大回撤', formatQuantPct(metrics.max_drawdown_pct))}${metricCard('交易成本', formatQuantPct(metrics.total_transaction_cost_pct))}</div>
        <div class="improvement-panel"><h3>最近组合周期</h3><div class="table-wrap"><table class="quant-table"><thead><tr><th>日期</th><th>毛收益</th><th>净收益</th><th>成本</th><th>换手</th><th>持仓</th><th>回撤</th></tr></thead><tbody>${rows}</tbody></table></div></div>
        <div class="improvement-panel"><h3>执行假设</h3><p>${(report.warnings || []).map(escapeHtml).join(' · ')}</p></div>
        <div class="path-note">报告: ${escapeHtml(data.report_path || '')}</div>
    `;
}

function renderEvidenceMaintenanceReport(data) {
    const report = data.report || {};
    const collection = report.collection || {};
    document.getElementById('quantOutput').innerHTML = `
        <div class="improvement-summary">${metricCard('本轮验证', report.verified_count || 0)}${metricCard('剩余待验证', report.queue_after?.pending || 0)}${metricCard('仍已到期', report.queue_after?.overdue || 0)}${metricCard('采集快照', collection.saved_count || 0)}</div>
        <div class="improvement-panel"><h3>维护结果</h3><p>${(report.errors || []).length ? (report.errors || []).map(escapeHtml).join(' · ') : '本轮维护完成，未发现执行错误。'}</p></div>
    `;
}

function formatQuantDecimal(value, digits = 2) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : '--';
}

function formatQuantPct(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number >= 0 ? '+' : ''}${number.toFixed(2)}%` : '--';
}

function metricCard(label, value) {
    return `
        <div class="metric-card">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(String(value))}</strong>
        </div>
    `;
}

function renderAgentScore(name, stats) {
    return `
        <div class="agent-score">
            <strong>${escapeHtml(name)}</strong>
            <span>样本 ${stats.total || 0}</span>
            <span>命中 ${Math.round((stats.accuracy || 0) * 100)}%</span>
            <span>置信 ${Math.round((stats.avg_confidence || 0) * 100)}%</span>
        </div>
    `;
}

function renderImprovementAction(action) {
    const cls = action.status === 'applied' ? 'applied'
        : action.status === 'dry_run' ? 'dry'
        : action.status === 'protected' ? 'protected'
        : 'skipped';
    return `
        <div class="action-item ${cls}">
            <div>
                <strong>${escapeHtml(action.agent_name)} · ${escapeHtml(action.area)}</strong>
                <p>${escapeHtml(action.title)} · ${escapeHtml(action.reason)}</p>
                <p>样本 ${escapeHtml(String(action.sample_size || 0))} · 独立案例 ${escapeHtml(String(action.unique_cases || 0))}</p>
                ${action.path ? `<small>${escapeHtml(action.path)}</small>` : ''}
            </div>
            <span>${escapeHtml(action.status)}</span>
        </div>
    `;
}

function renderProtectedItem(item) {
    return `
        <div class="action-item protected">
            <div>
                <strong>${escapeHtml(item.agent_name)} · ${escapeHtml(item.area)}</strong>
                <p>${escapeHtml(item.title)} · ${escapeHtml(item.reason)}</p>
            </div>
            <span>review</span>
        </div>
    `;
}

function readNumber(id, fallback) {
    const value = Number(document.getElementById(id)?.value);
    return Number.isFinite(value) && value > 0 ? value : fallback;
}

function setButtonBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
        button.dataset.originalText = button.textContent;
        button.textContent = label || '处理中...';
        button.disabled = true;
    } else {
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }
}

// ============================================================
// 健康检查
// ============================================================

async function checkHealth() {
    const indicator = document.getElementById('statusIndicator');
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');

    try {
        const resp = await fetch(`${API_BASE}/api/health`);
        const data = await resp.json();

        if (data.llm_ready) {
            dot.className = 'status-dot online';
            text.textContent = `在线 · ${data.model}`;
        } else {
            dot.className = 'status-dot offline';
            text.textContent = 'LLM 未就绪';
        }
    } catch (e) {
        dot.className = 'status-dot offline';
        text.textContent = '服务离线';
    }
}

// ============================================================
// UI 辅助
// ============================================================

function showLoading() {
    document.getElementById('loadingSection').style.display = 'block';
    document.getElementById('analyzeBtn').disabled = true;
    document.getElementById('cancelBtn').style.display = 'inline-flex';
    document.querySelector('.btn-text').style.display = 'none';
    document.querySelector('.btn-loading').style.display = 'inline';
    lastProgressValue = 0;
    updateProgress(0, '正在创建分析任务...');
}

function hideLoading() {
    document.getElementById('loadingSection').style.display = 'none';
    document.getElementById('analyzeBtn').disabled = false;
    document.getElementById('cancelBtn').style.display = 'none';
    document.querySelector('.btn-text').style.display = 'inline';
    document.querySelector('.btn-loading').style.display = 'none';
    clearPollTimer();

    lastProgressValue = 0;
    updateProgress(0, '正在获取数据...');
}

function updateProgress(progress, message) {
    const fill = document.getElementById('progressFill');
    const text = document.getElementById('progressText');
    const valueLabel = document.getElementById('progressValue');
    const loadingMessage = document.getElementById('loadingMessage');
    const bar = fill?.closest('.progress-bar');
    const rawValue = Math.max(0, Math.min(100, Number(progress) || 0));
    const value = rawValue === 0 ? 0 : Math.max(lastProgressValue, rawValue);
    lastProgressValue = value;

    if (!fill || !text) return;
    fill.style.width = `${value}%`;
    fill.dataset.progress = `${value}`;
    if (bar) bar.setAttribute('aria-valuenow', String(Math.round(value)));
    const label = message || '正在处理...';
    text.textContent = `${Math.round(value)}% · ${label}`;
    if (valueLabel) valueLabel.textContent = `${Math.round(value)}%`;
    if (loadingMessage) loadingMessage.textContent = label;

    const stages = Array.from(document.querySelectorAll('#progressStages span'));
    stages.forEach((stage, index) => {
        const threshold = Number(stage.dataset.stage || 0);
        const nextThreshold = Number(stages[index + 1]?.dataset.stage || 101);
        const reached = value >= threshold;
        stage.classList.toggle('done', reached);
        stage.classList.toggle('active', reached && value < nextThreshold);
    });
}

function showResult() {
    document.getElementById('resultSection').style.display = 'block';
}

function hideResult() {
    document.getElementById('resultSection').style.display = 'none';
}

function showError(message) {
    // 简单的错误提示 — 可以扩展为 toast
    const div = document.createElement('div');
    div.style.cssText = `
        position: fixed; top: 80px; left: 50%; transform: translateX(-50%);
        background: var(--bearish); color: white; padding: 12px 24px;
        border-radius: 8px; z-index: 300; font-size: 14px;
        box-shadow: 0 4px 16px rgba(255,23,68,0.3);
    `;
    div.textContent = message;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 4000);
}

// ============================================================
// 工具函数
// ============================================================

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatDate(value) {
    if (!value) return 'N/A';
    return String(value).slice(0, 19).replace('T', ' ');
}

function formatSummary(text) {
    // 简单的 Markdown 格式转换
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}
