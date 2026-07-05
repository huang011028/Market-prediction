/**
 * Market Prediction — 前端应用
 * 处理用户交互、API 调用、结果渲染
 */

// ============================================================
// 全局状态
// ============================================================

const API_BASE = '';  // 同源部署
let currentResult = null;
let agentsInfo = [];
let currentJobId = null;
let currentPollTimer = null;

// ============================================================
// 初始化
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initInput();
    initQuickButtons();
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
            body: JSON.stringify({ target, timeframe, skip_agents: skipAgents }),
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
    const report = result.final_report;
    const dir = report.direction || 'neutral';

    // 方向 badge
    const badge = document.getElementById('directionBadge');
    badge.className = `direction-badge ${dir}`;
    const dirMap = { bullish: '📈 看涨', bearish: '📉 看跌', neutral: '➡️ 震荡' };
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

    // 标的
    const targetInfo = result.target_info || {};
    const targetLabel = targetInfo.display_name || result.resolved_target || result.target;
    const marketLabel = targetInfo.market ? ` · ${targetInfo.market}` : '';
    document.getElementById('reportTarget').textContent = `${targetLabel}${marketLabel} · ${result.timeframe}`;

    // 汇总文字
    const summaryEl = document.getElementById('reportSummary');
    summaryEl.innerHTML = formatSummary(report.summary || '综合分析完成');

    // 耗时
    document.getElementById('elapsedTime').textContent = `⏱ 耗时 ${result.elapsed_seconds}s`;
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
    renderFailedAgents(result.failed_agents || []);

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
    const defaultMode = hasIntraday ? 'intraday' : 'daily';
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
    const closes = points.map(p => Number(p.close)).filter(v => Number.isFinite(v));
    const rawMin = Math.min(...closes);
    const rawMax = Math.max(...closes);
    const rawRange = rawMax - rawMin || Math.max(rawMax * 0.01, 1);
    const paddedMin = rawMin - rawRange * 0.08;
    const paddedMax = rawMax + rawRange * 0.08;
    const yTicks = buildNiceTicks(paddedMin, paddedMax, 5);
    const min = yTicks[0];
    const max = yTicks[yTicks.length - 1];
    const first = closes[0];
    const last = closes[closes.length - 1];
    const totalChange = first ? ((last / first - 1) * 100) : 0;
    const dirClass = totalChange > 0 ? 'bullish' : totalChange < 0 ? 'bearish' : 'neutral';
    const firstLabel = pointLabel(points[0]);
    const lastLabel = pointLabel(points[points.length - 1]);
    const modeLabel = options.mode === 'intraday'
        ? `${options.intradayMeta?.interval || '5m'} 分钟`
        : '日线';

    meta.innerHTML = `
        <span>${escapeHtml(targetInfo.display_name || targetInfo.symbol || '')}</span>
        <span>${escapeHtml(modeLabel)}</span>
        <span class="${dirClass}">${totalChange >= 0 ? '+' : ''}${totalChange.toFixed(2)}%</span>
        <span>${escapeHtml(firstLabel)} 至 ${escapeHtml(lastLabel)}</span>
    `;

    const width = 780;
    const height = 300;
    const padLeft = 76;
    const padRight = 30;
    const padTop = 26;
    const padBottom = 54;
    const plotWidth = width - padLeft - padRight;
    const plotHeight = height - padTop - padBottom;
    const range = max - min || 1;
    const xStep = plotWidth / (points.length - 1);
    const pointCoords = points.map((p, i) => {
        const x = padLeft + i * xStep;
        const y = padTop + (1 - ((Number(p.close) - min) / range)) * plotHeight;
        return { x, y, point: p, close: Number(p.close) };
    });
    const coords = pointCoords.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const areaCoords = `${padLeft},${height - padBottom} ${coords} ${width - padRight},${height - padBottom}`;
    const lineColor = totalChange >= 0 ? 'var(--bullish)' : 'var(--bearish)';
    const xTicks = buildXAxisTicks(points, options.mode, 5);
    const lastPoint = pointCoords[pointCoords.length - 1];

    const yGrid = yTicks.map(tick => {
        const y = padTop + (1 - ((tick - min) / range)) * plotHeight;
        return `
            <line x1="${padLeft}" y1="${y.toFixed(1)}" x2="${width - padRight}" y2="${y.toFixed(1)}" class="trend-grid" />
            <text x="${padLeft - 10}" y="${(y + 4).toFixed(1)}" text-anchor="end" class="trend-y-label">${formatPriceTick(tick)}</text>
        `;
    }).join('');

    const xGrid = xTicks.map(item => {
        const x = padLeft + item.index * xStep;
        const anchor = item.index === 0 ? 'start' : item.index === points.length - 1 ? 'end' : 'middle';
        return `
            <line x1="${x.toFixed(1)}" y1="${height - padBottom}" x2="${x.toFixed(1)}" y2="${height - padBottom + 5}" class="trend-tick" />
            <text x="${x.toFixed(1)}" y="${height - 22}" text-anchor="${anchor}" class="trend-x-label">${escapeHtml(item.label)}</text>
        `;
    }).join('');
    const supportResistance = options.snapshot?.support_resistance || {};
    const referenceLines = [
        { value: supportResistance.nearest_support, label: '支撑', className: 'support' },
        { value: supportResistance.nearest_resistance, label: '压力', className: 'resistance' },
    ].map(item => {
        if (item.value == null) return '';
        const value = Number(item.value);
        if (!Number.isFinite(value) || value < min || value > max) return '';
        const y = padTop + (1 - ((value - min) / range)) * plotHeight;
        const labelY = Math.max(padTop + 12, Math.min(height - padBottom - 6, y - 6));
        return `
            <line x1="${padLeft}" y1="${y.toFixed(1)}" x2="${width - padRight}" y2="${y.toFixed(1)}" class="trend-reference ${item.className}" />
            <text x="${width - padRight - 6}" y="${labelY.toFixed(1)}" text-anchor="end" class="trend-reference-label ${item.className}">${escapeHtml(item.label)} ${formatPriceTick(value)}</text>
        `;
    }).join('');

    const hotPoints = pointCoords.map(item => `
        <circle cx="${item.x.toFixed(1)}" cy="${item.y.toFixed(1)}" r="6" class="trend-hit">
            <title>${escapeHtml(pointTooltip(item.point, options.mode))}</title>
        </circle>
    `).join('');

    chart.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="近期收盘价走势">
            <rect x="${padLeft}" y="${padTop}" width="${plotWidth}" height="${plotHeight}" class="trend-plot-bg" />
            ${yGrid}
            <line x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${height - padBottom}" class="trend-axis" />
            <line x1="${padLeft}" y1="${height - padBottom}" x2="${width - padRight}" y2="${height - padBottom}" class="trend-axis" />
            ${xGrid}
            ${referenceLines}
            <polygon points="${areaCoords}" class="trend-area ${dirClass}" />
            <polyline points="${coords}" fill="none" stroke="${lineColor}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />
            ${hotPoints}
            <circle cx="${lastPoint.x.toFixed(1)}" cy="${lastPoint.y.toFixed(1)}" r="4.5" fill="${lineColor}" class="trend-last-point" />
            <text x="${width - padRight}" y="${Math.max(padTop + 14, lastPoint.y - 10).toFixed(1)}" text-anchor="end" class="trend-last-label">${last.toFixed(2)}</text>
        </svg>
    `;
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

function pointTooltip(point, mode) {
    const label = pointLabel(point);
    const close = Number(point?.close);
    const change = Number(point?.change_pct);
    const closeText = Number.isFinite(close) ? close.toFixed(2) : 'N/A';
    const changeText = Number.isFinite(change) ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : 'N/A';
    return `${mode === 'intraday' ? '时间' : '日期'}: ${label}\n收盘: ${closeText}\n区间变化: ${changeText}`;
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

        const dirLabel = { bullish: '📈 看涨', bearish: '📉 看跌', neutral: '➡️ 震荡' };
        const conf = Math.round((r.confidence || 0) * 100);

        const factors = (r.key_factors || []).slice(0, 3)
            .map(f => `<span class="agent-factor">${escapeHtml(f)}</span>`).join('');
        const statusBadge = status.status !== 'ok'
            ? `<span class="agent-status ${status.status}">${status.status === 'failed' ? '失败' : '降级'}</span>`
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

function renderFailedAgents(items) {
    const card = document.getElementById('failedAgentsCard');
    const list = document.getElementById('failedAgentsList');
    if (!items.length) {
        card.style.display = 'none';
        return;
    }

    card.style.display = 'block';
    list.innerHTML = items.map(item => `
        <div class="failure-item">
            <div class="failure-agent">${escapeHtml(item.agent_name)} · ${escapeHtml(item.status)}</div>
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

    body.innerHTML = `
        ${status.status !== 'ok' ? `
        <div class="detail-section detail-warning">
            <h4>执行状态</h4>
            <p><strong>${escapeHtml(status.status)}</strong> · ${escapeHtml(status.reason)}</p>
        </div>` : ''}
        <div class="detail-section">
            <h4>预测结果</h4>
            <p><strong>方向:</strong> ${result.direction} | <strong>幅度:</strong> ${magStr} | <strong>置信度:</strong> ${Math.round((result.confidence || 0) * 100)}%</p>
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
    list.innerHTML = '<p style="color:var(--text-muted);padding:20px;">加载中...</p>';

    try {
        const resp = await fetch(`${API_BASE}/api/history?limit=50`);
        const data = await resp.json();

        if (!data.history || data.history.length === 0) {
            list.innerHTML = '<p style="color:var(--text-muted);padding:20px;">暂无历史记录</p>';
            return;
        }

        list.innerHTML = data.history.map(h => {
            const dir = h.direction || 'neutral';
            const conf = Math.round((h.confidence || 0) * 100);
            const date = h.predicted_at ? h.predicted_at.slice(0, 16).replace('T', ' ') : 'N/A';
            return `
                <div class="history-item" data-id="${h.id}">
                    <div class="history-info">
                        <div class="history-direction ${dir}"></div>
                        <div>
                            <div class="history-target">${escapeHtml(h.target)}</div>
                            <div class="history-meta">${escapeHtml(h.timeframe)} · ${date}</div>
                        </div>
                    </div>
                    <div class="history-confidence">
                        <div class="history-confidence-value" style="color:var(--${dir === 'bullish' ? 'bullish' : dir === 'bearish' ? 'bearish' : 'neutral'})">${conf}%</div>
                        <div class="history-confidence-label">置信度</div>
                    </div>
                </div>
            `;
        }).join('');

        // 点击加载详情
        list.querySelectorAll('.history-item').forEach(item => {
            item.addEventListener('click', () => loadHistoryDetail(item.dataset.id));
        });
    } catch (e) {
        list.innerHTML = `<p style="color:var(--bearish);padding:20px;">加载失败: ${e.message}</p>`;
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

        title.textContent = `📋 ${data.target} — 历史预测详情`;
        const failedAgents = (data.agents_failed || [])
            .map(name => `<span class="history-agent-chip failed">${escapeHtml(name)}</span>`)
            .join('');
        const usedAgents = (data.agents_used || [])
            .map(name => `<span class="history-agent-chip">${escapeHtml(name)}</span>`)
            .join('');
        const reportBlock = data.report_md
            ? `<pre class="history-report-md">${escapeHtml(data.report_md)}</pre>`
            : `<div class="detail-reasoning">${formatSummary(data.summary || '暂无完整报告')}</div>`;

        body.innerHTML = `
            <div class="disclaimer-banner modal-disclaimer">${escapeHtml(data.disclaimer || '本项目仅供学习和研究使用，不构成任何投资建议。')}</div>
            <div class="detail-section">
                <h4>预测信息</h4>
                <p><strong>标的:</strong> ${escapeHtml(data.target)} | <strong>周期:</strong> ${escapeHtml(data.timeframe)}</p>
                <p><strong>方向:</strong> ${escapeHtml(data.direction)} | <strong>置信度:</strong> ${Math.round((data.confidence || 0) * 100)}%</p>
                <p><strong>预测时间:</strong> ${formatDate(data.predicted_at)} | <strong>有效期:</strong> ${formatDate(data.valid_until)}</p>
                <p><strong>耗时:</strong> ${data.elapsed_seconds || 0}s | <strong>模型:</strong> ${escapeHtml(data.llm_model || 'N/A')}</p>
                ${data.verified ? `<p><strong>实际涨跌:</strong> ${data.actual_change_pct}% | <strong>方向:</strong> ${data.direction_correct ? '✅ 正确' : '❌ 错误'}</p>` : '<p><em>尚未验证</em></p>'}
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
        modal.style.display = 'flex';
    } catch (e) {
        showError(e.message);
    }
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
    updateProgress(0, '正在创建分析任务...');

    // 模拟进度
    let progress = 0;
    const fill = document.getElementById('progressFill');
    const text = document.getElementById('progressText');
    const steps = [
        '正在获取实时行情数据...',
        '正在获取财务报表...',
        '正在获取最新新闻...',
        '正在获取宏观经济数据...',
        '正在获取行业对比数据...',
        'AI Agent 团队分析中...',
        '汇总分析师综合研判...',
    ];

    const interval = setInterval(() => {
        progress += Math.random() * 15;
        if (progress > 90) progress = 90;
        const currentWidth = parseFloat(fill.dataset.progress || '0');
        if (currentWidth >= progress) return;
        fill.style.width = `${progress}%`;
        fill.dataset.progress = `${progress}`;
        const stepIdx = Math.min(Math.floor(progress / 15), steps.length - 1);
        text.textContent = steps[stepIdx];
    }, 800);

    // 保存 interval id 以便清理
    window._loadingInterval = interval;
}

function hideLoading() {
    document.getElementById('loadingSection').style.display = 'none';
    document.getElementById('analyzeBtn').disabled = false;
    document.getElementById('cancelBtn').style.display = 'none';
    document.querySelector('.btn-text').style.display = 'inline';
    document.querySelector('.btn-loading').style.display = 'none';
    clearPollTimer();

    if (window._loadingInterval) {
        clearInterval(window._loadingInterval);
        window._loadingInterval = null;
    }
    updateProgress(0, '正在获取数据...');
}

function updateProgress(progress, message) {
    const fill = document.getElementById('progressFill');
    const text = document.getElementById('progressText');
    const value = Math.max(0, Math.min(100, Number(progress) || 0));
    fill.style.width = `${value}%`;
    fill.dataset.progress = `${value}`;
    text.textContent = message || '正在处理...';
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
    div.textContent = `❌ ${message}`;
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
