# 评测报告 online-test3

- 模式：online（LLM）；数据：合成数据（syn-v1）；集合：golden,multiturn,adversarial；切分：all
- **总通过率 86.7%**（13/15）；多轮按轮通过率 0.8
- 延迟 P50 7 ms / P95 6791 ms；平均工具调用 1.2 次；LLM 调用 18 次，token 输入 10666 / 输出 612
- 护栏命中：{'input': 5}；路由来源：{'rules': 15, 'guard': 5}；运行错误：0

## 分类别通过率

| 类别 | 通过 | 总数 | 通过率 |
| --- | --- | --- | --- |
| adversarial | 5 | 5 | 100.0% |
| identify | 5 | 5 | 100.0% |
| multiturn | 3 | 5 | 60.0% |

## 失败检查项（按轮计数）

| 检查项 | 失败次数 |
| --- | --- |
| tools | 2 |

## 失败明细（前 40 条）

- **T004** 第 1 轮「给我介绍一下玻璃梭鲈」→ answer | tools：缺 ['search_species_kb'] 禁 [] 参数错 [] | 实际 ['query_species_graph'] | trace 60835863fe0f
- **T005** 第 1 轮「给我介绍一下斑点叉尾鮰」→ answer | tools：缺 ['search_species_kb'] 禁 [] 参数错 [] | 实际 ['query_species_graph'] | trace a96414e91d1b
