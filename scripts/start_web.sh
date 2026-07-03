#!/bin/bash
# ============================================================
# Market Prediction Web — 启动脚本
# ============================================================
# 用法:
#   bash scripts/start_web.sh          # 默认端口 8080
#   bash scripts/start_web.sh 3000     # 自定义端口
#
# 启动后访问:
#   前端页面: http://localhost:8080/
#   API 文档: http://localhost:8080/docs
#   健康检查: http://localhost:8080/api/health
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

PORT="${1:-8080}"

echo "============================================================"
echo "  Market Prediction Web 启动"
echo "============================================================"
echo "  端口: $PORT"
echo "  前端: http://localhost:$PORT/"
echo "  API:  http://localhost:$PORT/docs"
echo "============================================================"

# 检查依赖
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "📦 安装依赖..."
    pip3 install fastapi uvicorn
fi

# 检查 .env
if [ ! -f .env ]; then
    echo "⚠️  .env 文件不存在，请复制 .env.example 并填写 API Key"
    cp .env.example .env
    echo "   已创建 .env 模板，请编辑后重新运行"
    exit 1
fi

# 创建前端目录（如果不存在）
mkdir -p frontend

# 启动
python3 api_server.py --port "$PORT"
