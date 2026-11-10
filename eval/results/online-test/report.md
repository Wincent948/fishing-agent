# 评测报告 online-test

- 模式：online（LLM）；数据：合成数据（syn-v1）；集合：golden,multiturn,adversarial；切分：all
- **总通过率 66.7%**（10/15）；多轮按轮通过率 0.5
- 延迟 P50 6 ms / P95 11633 ms；平均工具调用 1.45 次；LLM 调用 19 次，token 输入 12570 / 输出 738
- 护栏命中：{'input': 5}；路由来源：{'rules': 15, 'guard': 5}；运行错误：0

## 分类别通过率

| 类别 | 通过 | 总数 | 通过率 |
| --- | --- | --- | --- |
| adversarial | 5 | 5 | 100.0% |
| identify | 5 | 5 | 100.0% |
| multiturn | 0 | 5 | 0.0% |

## 失败检查项（按轮计数）

| 检查项 | 失败次数 |
| --- | --- |
| behavior | 4 |
| tools | 1 |

## 失败明细（前 40 条）

- **T001** 第 1 轮「给我介绍一下大口黑鲈」→ partial_answer | behavior：期望 answer 实际 partial_answer | trace ab1074498b87
- **T002** 第 1 轮「给我介绍一下白斑狗鱼」→ partial_answer | behavior：期望 answer 实际 partial_answer | trace 09c10fee4ef6
- **T003** 第 1 轮「给我介绍一下虹鳟」→ partial_answer | behavior：期望 answer 实际 partial_answer | trace 2870d218a119
- **T004** 第 1 轮「给我介绍一下玻璃梭鲈」→ partial_answer | behavior：期望 answer 实际 partial_answer | trace f9c81d58afad
- **T005** 第 1 轮「给我介绍一下斑点叉尾鮰」→ answer | tools：缺 ['search_species_kb'] 禁 [] 参数错 [] | 实际 ['query_species_graph'] | trace bbe1e57093f4
