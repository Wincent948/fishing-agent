# 评测报告 online-test2

- 模式：online（LLM）；数据：合成数据（syn-v1）；集合：golden,multiturn,adversarial；切分：all
- **总通过率 100.0%**（15/15）；多轮按轮通过率 1
- 延迟 P50 3 ms / P95 57 ms；平均工具调用 0.8 次；LLM 调用 0 次，token 输入 0 / 输出 0
- 护栏命中：{'retrieval': 1, 'input': 5}；路由来源：{'rules': 15, 'guard': 5}；运行错误：0

## 分类别通过率

| 类别 | 通过 | 总数 | 通过率 |
| --- | --- | --- | --- |
| adversarial | 5 | 5 | 100.0% |
| identify | 5 | 5 | 100.0% |
| multiturn | 5 | 5 | 100.0% |

## 失败检查项（按轮计数）

| 检查项 | 失败次数 |
| --- | --- |

## 失败明细（前 40 条）

