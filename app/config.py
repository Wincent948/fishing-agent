"""全局配置：从环境变量读取，统一在这里定义，代码里不出现散落的路径和密钥。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "synthetic"          # 数据目录（换真实数据时只改这里）
EVAL = ROOT / "eval"
RULES_CSV = EVAL / "rules.csv"               # 规则表与评测共用，保证两边数字一致
FIXTURES = EVAL / "fixtures" / "conditions.json"
RUNTIME = ROOT / ".runtime"                  # 索引、SQLite、日志等运行时产物
RUNTIME.mkdir(exist_ok=True)


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """读取项目根目录的 .env（KEY=VALUE，# 为注释）。已存在的环境变量优先，不覆盖。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ---- 模型（OpenAI 兼容接口，经 LiteLLM 网关或托管推理）
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
MODEL_SMALL = os.getenv("MODEL_SMALL", "chat-small")   # 网关别名 → Qwen3.5-9B
MODEL_LARGE = os.getenv("MODEL_LARGE", "chat-large")   # 网关别名 → Qwen3.6-35B-A3B
# 没配 key 就进入离线模式：所有 LLM 节点降级为规则 / 模板实现，整条链路仍可运行
DISABLE_THINKING = os.getenv("DISABLE_THINKING", "1") == "1"   # Qwen3.x 默认先「思考」再回答；路由等短输出节点关掉更快更省
OFFLINE = os.getenv("OFFLINE", "auto") == "1" or (os.getenv("OFFLINE", "auto") == "auto" and not LLM_API_KEY)

# ---- 评测模式：工具读 fixtures、时钟冻结
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "1") == "1"
FROZEN_TODAY = os.getenv("FROZEN_TODAY", "2026-10-10")

# ---- 检索参数（调优对象）
RETRIEVE_K = int(os.getenv("RETRIEVE_K", "20"))      # 两路各召回多少
RERANK_N = int(os.getenv("RERANK_N", "5"))           # 重排后保留多少
RRF_K = int(os.getenv("RRF_K", "60"))
MIN_RELEVANCE = float(os.getenv("MIN_RELEVANCE", "0.15"))   # 低于它视为「库里没有」
ID_CONF_THRESHOLD = float(os.getenv("ID_CONF_THRESHOLD", "0.7"))  # 识鱼直接作答的置信度
ID_REJECT_THRESHOLD = float(os.getenv("ID_REJECT_THRESHOLD", "0.35"))  # 低于它拒识

# ---- 防自旋上限
MAX_GRAPH_STEPS = 15
MAX_TOOL_CALLS = 6
MAX_SAME_TOOL = 2
MAX_CLARIFY_TURNS = 2
MAX_REWRITE = 2
TOOL_TIMEOUT_S = 5.0
MAX_INPUT_CHARS = 2000
MAX_IMAGE_MB = 10
