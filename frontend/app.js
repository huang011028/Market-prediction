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
    const input = document.getElementById('targetInput');

    btn.addEventListener('click', startAnalysis);
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
        const response = await fetch(`${API_BASE}/api/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target, timeframe, skip_agents: skipAgents }),
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || '分析失败');
        }

        const result = await response.json();
        currentResult = result;
        renderResult(result);
        hideLoading();
        showResult();
    } catch (error) {
        hideLoading();
        showError(error.message);
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
    document.getElementById('reportTarget').textContent = `${result.target} · ${result.timeframe}`;

    // 汇总文字
    const summaryEl = document.getElementById('reportSummary');
    summaryEl.innerHTML = formatSummary(report.summary || '综合分析完成');

    // 耗时
    document.getElementById('elapsedTime').textContent = `⏱ 耗时 ${result.elapsed_seconds}s`;
    document.getElementById('predictionId').textContent = result.prediction_id
        ? `ID: ${result.prediction_id.slice(0, 8)}` : '';

    // Agent 结果
    renderAgentCards(result.agent_results);

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

function renderAgentCards(results) {
    const grid = document.getElementById('agentGrid');
    grid.innerHTML = '';

    results.forEach((r, i) => {
        const dir = r.direction || 'neutral';
        const card = document.createElement('div');
        card.className = `agent-card ${dir}`;
        card.style.animationDelay = `${i * 0.05}s`;

        const mag = r.magnitude;
        const magStr = mag
            ? `${mag.min_pct >= 0 ? '+' : ''}${mag.min_pct}% ~ ${mag.max_pct >= 0 ? '+' : ''}${mag.max_pct}%`
            : 'N/A';

        const dirLabel = { bullish: '📈 看涨', bearish: '📉 看跌', neutral: '➡️ 震荡' };
        const conf = Math.round((r.confidence || 0) * 100);

        const factors = (r.key_factors || []).slice(0, 3)
            .map(f => `<span class="agent-factor">${escapeHtml(f)}</span>`).join('');

        card.innerHTML = `
            <div class="agent-card-header">
                <span class="agent-name">${escapeHtml(r.agent_name)}</span>
                <span class="agent-direction ${dir}">${dirLabel[dir] || dir}</span>
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

    body.innerHTML = `
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
        body.innerHTML = `
            <div class="detail-section">
                <h4>预测信息</h4>
                <p><strong>标的:</strong> ${escapeHtml(data.target)} | <strong>周期:</strong> ${escapeHtml(data.timeframe)}</p>
                <p><strong>方向:</strong> ${data.direction} | <strong>置信度:</strong> ${Math.round((data.confidence || 0) * 100)}%</p>
                <p><strong>时间:</strong> ${data.predicted_at || 'N/A'}</p>
                ${data.verified ? `<p><strong>实际涨跌:</strong> ${data.actual_change_pct}% | <strong>方向:</strong> ${data.direction_correct ? '✅ 正确' : '❌ 错误'}</p>` : '<p><em>尚未验证</em></p>'}
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
    document.querySelector('.btn-text').style.display = 'none';
    document.querySelector('.btn-loading').style.display = 'inline';

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
        fill.style.width = `${progress}%`;
        const stepIdx = Math.min(Math.floor(progress / 15), steps.length - 1);
        text.textContent = steps[stepIdx];
    }, 800);

    // 保存 interval id 以便清理
    window._loadingInterval = interval;
}

function hideLoading() {
    document.getElementById('loadingSection').style.display = 'none';
    document.getElementById('analyzeBtn').disabled = false;
    document.querySelector('.btn-text').style.display = 'inline';
    document.querySelector('.btn-loading').style.display = 'none';

    if (window._loadingInterval) {
        clearInterval(window._loadingInterval);
        window._loadingInterval = null;
    }
    document.getElementById('progressFill').style.width = '0%';
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

function formatSummary(text) {
    // 简单的 Markdown 格式转换
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}
