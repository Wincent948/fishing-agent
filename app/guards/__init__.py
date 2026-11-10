"""四道护栏（规则层先拦，模型层再判；模型层在离线模式下关闭）。

护栏 1 输入   input.py      长度 / 格式 / 注入 / 越狱 / 有害请求 → 风险分级
护栏 2 资料   retrieval.py  检索结果里的间接注入、不可信来源
护栏 3 动作   action.py     越权（跨用户）、写入必须确认、工具白名单
护栏 4 输出   output.py     规则数字一致、免责声明、防编造、防泄露
"""
from dataclasses import dataclass, field


@dataclass
class GuardVerdict:
    allowed: bool = True
    guard: str | None = None        # 命中的护栏名：input / retrieval / action / output
    reason: str = ""
    behavior: str | None = None     # 拦截时给用户的行为：reject_input / refuse
    message: str = ""
    risk: str = "low"               # low / medium / high
    notes: list = field(default_factory=list)
