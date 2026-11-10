# 评测报告 release

- 模式：offline（规则 / 模板）；数据：合成数据（syn-v1）；集合：golden,multiturn,adversarial；切分：all
- **总通过率 99.7%**（329/330）；多轮按轮通过率 1
- 延迟 P50 6 ms / P95 13 ms；平均工具调用 1.06 次；LLM 调用 0 次，token 输入 0 / 输出 0
- 护栏命中：{'retrieval': 13, 'input': 49, 'output': 13, 'action': 20}；路由来源：{'rules': 307, 'guard': 49, 'too_short': 3, 'semantic': 1, 'inherit': 15, 'pending': 15}；运行错误：0

## 分类别通过率

| 类别 | 通过 | 总数 | 通过率 |
| --- | --- | --- | --- |
| adversarial | 80 | 80 | 100.0% |
| boundary | 50 | 50 | 100.0% |
| identify | 49 | 50 | 98.0% |
| knowledge | 50 | 50 | 100.0% |
| multihop | 50 | 50 | 100.0% |
| multiturn | 50 | 50 | 100.0% |

## 失败检查项（按轮计数）

| 检查项 | 失败次数 |
| --- | --- |
| tools | 1 |
| keywords | 1 |

## 失败明细（前 40 条）

- **I010** 第 1 轮「这是什么鱼？能留吗？」→ clarify | tools：缺 ['get_species_rules'] 禁 [] 参数错 [] | 实际 ['identify_fish']；keywords：缺 ['示例规则'] 不应出现 [] | trace 9fa4496612ad
