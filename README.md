# 多模态钓鱼助手 Agent

基于 **LangGraph + LangChain** 的多模态钓鱼助手：拍照识鱼、鱼类知识问答、示例捕捞规则、天气潮汐与最佳出钓时段、出钓规划、渔获记录。
架构上 **Workflow 做骨架，Agent 处理开放部分**，配有四道护栏、多层防自旋、四层记忆、两层网关和一套自建评测中台。

> **数据说明**：本项目使用**合成数据**（`data/synthetic/`，SYNTHETIC），规则表是**示例规则**，天气潮汐是评测用固定数据。
> 评测分数只说明「方法与链路可运行、可复现」，**不代表真实场景效果**；见文末「如实说明」。

---

## 快速开始

```bash
pip install -r requirements.txt
make data      # 生成合成数据 → data/synthetic/
make index     # 建索引：卡片 + 长文切块 → ChromaDB + BM25
make test      # 单元测试
make eval      # 跑全部 330 道评测题，报告在 eval/results/<run_id>/report.md
make api       # 启动 FastAPI（http://127.0.0.1:8000/docs）
```

默认是**离线模式**：没有配 API key 时，所有 LLM 节点降级为规则 / 模板实现，整条链路照样能跑。
配置 `LLM_BASE_URL`、`LLM_API_KEY` 后自动切到在线模式（Qwen3.5-9B / Qwen3.6-35B-A3B，经 LiteLLM 网关），见 `.env.example`。

---

## 学习路线：按这个顺序读代码

| 顺序 | 文件 | 看什么 |
| --- | --- | --- |
| 1 | `app/graph/main_graph.py` | **先看全局**：LangGraph 主图、每个节点做什么、`interrupt()` 怎么暂停等确认 |
| 2 | `app/graph/router.py` | 三级意图识别（规则 → 语义路由 → LLM）、槽位抽取、缺口分析、多轮继承 |
| 3 | `app/graph/workflows.py` | 固定 Workflow：规则、钓况、渔获写入（步骤写死，模型不决定路径） |
| 4 | `app/agents/` | 4 个子 Agent：识鱼（追问 / 拒识）、知识（Agentic RAG）、规划（Plan-and-Execute）、合规（Reflexion） |
| 5 | `app/tools/base.py` | 工具统一外壳：超时、重试、熔断、调用预算、重复调用拦截、埋点 |
| 6 | `app/tools/*.py` | 10 个工具：规则、天气、潮汐、最佳时段、检索、图谱、识鱼、搜图、渔获、记忆 |
| 7 | `app/kb/` | 切块（`chunk.py`）、BM25（`bm25.py`）、混合检索 + RRF + 重排（`index.py`）、知识图谱（`graph.py`） |
| 8 | `app/guards/` | 四道护栏：输入、资料、动作、输出 |
| 9 | `app/memory/`、`app/context/` | 长期记忆（SQLite，按 user_id 隔离）、上下文组装与 token 预算 |
| 10 | `app/prompts/*.yaml` | 带版本号的提示词，每个节点一份 |
| 11 | `app/api/server.py` | 业务网关：鉴权、限流、确认接口、调试信息只在 dev 返回 |
| 12 | `eval/` | 评测中台：数据集、打分器、运行器、报告、检索消融 |

## 一个请求的完整旅程

「明天去 Annapolis 钓褐鳟，能带回家吗？几点去比较好？」

```
input_guard   规则检查通过（无注入、无越权）
load_memory   读取用户画像
router        意图 trip_plan；槽位：鱼种 Salmo trutta、地点 annapolis、日期 2026-10-11（规则命中，0 次 LLM）
planner       计划 rules → weather → tide → window，逐步执行：
              get_species_rules → 10-01~03-31 禁捕，当天不能带走
              get_weather → 多云，风速 18 km/h
              get_tide → 高潮 06:58、19:22
              best_window → 07:12–07:58、18:22–18:30
output_guard  Compliance 审查规则数字一致、免责声明齐全；无编造时间
update_memory 无新的长期事实
```

评测结果里每一轮都有这样的 trace（`eval/results/<run_id>/items.jsonl`）：节点路径、工具入参与结果、护栏命中、token、延迟。

## 知识点 → 代码位置

| 知识点 | 位置 |
| --- | --- |
| 输入 / 资料 / 动作 / 输出四道护栏 | `app/guards/` |
| 意图识别（规则 → 语义路由 → LLM） | `app/graph/router.py` |
| 判断模型思路（只在固定选项里选、带置信度） | 路由分级 + `semantic_route` |
| 调度模式 A（Orchestrator）/ 模式 B（Supervisor） | `n_orchestrator` / `app/agents/planner.py` |
| 子 Agent：独立上下文、专属工具（白名单） | `app/agents/`、`app/guards/action.py:ALLOWED` |
| 固定 RAG vs Agentic RAG | 知识子 Agent 离线 / 在线两条路径 |
| 切块：结构化不切、长文按标题 + token 递归切、块头、父子块 | `app/kb/chunk.py` |
| 混合检索：元数据过滤 + 向量 + BM25 + RRF + 重排 + 阈值 | `app/kb/index.py` |
| 知识图谱（多跳关系题） | `app/kb/graph.py` |
| 工具八点：Schema、粒度、校验、超时 / 重试 / 熔断、精简、友好报错、权限、埋点 | `app/tools/base.py` |
| 防自旋：步数、工具预算、重复调用、追问上限、重写上限、超时 | `app/config.py`、`tools/base.py`、`main_graph.py` |
| 写入必须人工确认（HITL） | `n_catch` 里的 `interrupt()` |
| 四层记忆：短期（checkpointer）/ 工作（State）/ 长期（SQLite）/ 情景（渔获） | `main_graph.py`、`app/memory/` |
| Context 预算与压缩 | `app/context/builder.py` |
| 模型分层与按节点参数 | `app/llm.py` |
| 提示词版本化 | `app/prompts/` |
| 业务网关：鉴权、限流、调试开关 | `app/api/server.py` |
| 评测中台：数据集、规则打分、分层指标、版本报告 | `eval/` |

## 评测结果（合成数据）

| 模式 | 通过率 | LLM 调用 | token（入 / 出） | 延迟 P50 / P95 | 全量成本 |
| --- | --- | --- | --- | --- | --- |
| 离线（规则 / 模板） | 99.7%（329/330） | 0 | 0 / 0 | 6 ms / 13 ms | 0 |
| 在线（Qwen3.5-9B，硅基流动） | 99.7%（329/330） | 42 | 27,630 / 2,253 | 2 ms / 2.4 s | ≈ ¥0.02 |

在线模式目前只有 Knowledge 子 Agent（Agentic RAG）真正调用 LLM；307/330 题由规则层路由，语义路由和 LLM 路由在这套题里几乎没被覆盖，是下一步要补的评测。

**接入真实模型后才暴露、并已修复的问题**

| 现象 | 原因 | 修复 |
| --- | --- | --- |
| 在线 multiturn 0/5 | ReAct 不收敛，撞上工具预算后只返回「已达上限」 | 步数上限 + 被预算拦截即停 + 不带工具强制收尾 |
| 调用报错整轮失败 | 无兜底 | 捕获异常、写入 trace、降级到离线实现 |
| 分数 100% 但 LLM 调用 0 次 | 降级把 402（余额不足）藏起来了 | 看分数必须同时看 LLM 调用数和 trace 里的 error |
| 必须检索的题没检索 | 模型凭参数记忆直接回答 | retrieve-first：先按鱼种 + 主题检索放进上下文，不够再让模型追加 |

### 离线分项

| 集合 | 题数 | 通过率 |
| --- | --- | --- |
| 黄金集：识鱼 / 知识 / 多跳 / 边界 | 50 / 50 / 50 / 50 | 98% / 100% / 100% / 100% |
| 多轮对话 | 50 段 | 100% |
| 对抗集 | 80 | 100% |
| **合计** | **330** | **99.7%** |

检索消融（20 道知识题，不加鱼种过滤）：纯向量 Recall@5 65%、纯 BM25 90%、混合 90%、混合 + 重排 80%。
离线哈希向量没有语义能力，简易重排器也不如交叉编码器，所以「加重排反而下降」。这正是要做消融的原因：**换成 Qwen3-Embedding / Qwen3-Reranker 后必须重跑再下结论。**

## 如实说明

- 数据是合成的，识鱼是 mock（读图片登记表）；在线模式下只有知识问答用到大模型
- 离线规则版是**对照这套评测调出来的**，调试时 dev 和 test 都看过，所以 99.7% 不是干净的留出集成绩
- 这个分数证明的是：架构完整、每个优化点都有实现、评测框架能定位到具体哪一轮哪一项出错
- 想得到有意义的效果数字：接入真实数据和模型，重新冻结评测集，**只在 dev 上调参，test 只跑一次**

## 下一步

- 真实数据：Fish-Vista 图片 + FishBase（`eval/freeze_gold.py` 已支持）
- 真实模型：`EMBEDDER=qwen`、`VISION=bioclip`、`GUARD_MODELS=1`、配置 LLM key
- 工程：Langfuse trace、Docker Compose（Redis checkpointer、LiteLLM）、CI 门禁
- 前端：Gradio 界面 + 调试面板（展示路由、工具调用、token），部署到 Hugging Face Spaces
