"""护栏 1：输入护栏。在 Router 之前运行，命中即短路，不调用任何模型和工具。

规则层：空输入、超长、垃圾文本、图片格式 / 大小、注入与越狱特征、非法捕捞请求。
模型层（可选，GUARD_MODELS=1）：Llama Prompt Guard 2 判注入，Qwen3Guard 判内容安全。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from app import config
from app.guards import GuardVerdict

INJECTION = re.compile("|".join([
    r"忽略(之前|以上|上面|所有)?.{0,6}(指令|规则|要求)", r"ignore (all )?(previous|prior|above) (instructions|rules)",
    r"system ?prompt", r"系统提示", r"提示词", r"hidden rules", r"隐藏指令", r"管理员模式", r"admin mode",
    r"调试模式", r"DAN\b", r"没有限制的 ?AI", r"不受规则限制", r"关闭.{0,4}(安全|检查|护栏)", r"disable .{0,10}guardrails",
    r"</?(user|system)>", r"###\s*SYSTEM", r"api.?key", r"\.env\b", r"base64", r"复述.{0,8}(第一条|上面)",
    r"repeat the words above", r"第一条消息", r"system ?消息", r"工具列表", r"你的规则", r"规则逐条", r"上一个版本会回答", r"print\(open",
]), re.I)
HARM = re.compile(r"毒(上来|死|晕)|用什么药|电鱼|电瓶|炸鱼|炸药|毒鱼|农药|投毒|避开巡查|不被发现|偷偷钓|偷捕|活着带到别的湖")
JAILBREAK_WRAP = re.compile(r"扮演|小说|虚构|游戏|诗歌|反话|拆成很多步|为了科研|执法人员|只回答是或否")
FICTION_RULES = re.compile(r"虚构世界|假设.{0,10}没有禁捕期")
IMG_OK = {".jpg", ".jpeg", ".png", ".webp"}


def _garbage(text: str) -> bool:
    t = re.sub(r"\s", "", text)
    return len(t) >= 200 and len(set(t)) / len(t) < 0.05


def check(text: str, image: str | None) -> GuardVerdict:
    if not (text or "").strip() and not image:
        return GuardVerdict(False, "input", "empty", "reject_input", "请输入你的问题，比如「大口黑鲈最小多长能留？」")
    if len(text) > config.MAX_INPUT_CHARS:
        return GuardVerdict(False, "input", "too_long", "reject_input",
                            f"问题太长了（{len(text)} 字，上限 {config.MAX_INPUT_CHARS} 字），请精简后再问。")
    if _garbage(text):
        return GuardVerdict(False, "input", "garbage", "reject_input", "没能理解这段输入，请换个说法提问。")
    if image:
        p = Path(image)
        if p.suffix.lower() not in IMG_OK:
            return GuardVerdict(False, "input", "image_format", "reject_input", "图片格式不支持，请上传 jpg / png / webp。")
        real = config.EVAL / image if not p.is_absolute() else p
        if real.exists() and real.stat().st_size > config.MAX_IMAGE_MB * 2**20:
            return GuardVerdict(False, "input", "image_size", "reject_input", f"图片超过 {config.MAX_IMAGE_MB} MB，请压缩后再上传。")
    if HARM.search(text) or (JAILBREAK_WRAP.search(text) and re.search(r"禁捕|电|毒|炸|巡查", text)) or FICTION_RULES.search(text):
        return GuardVerdict(False, "input", "harmful", "refuse", risk="high",
                            message="这个我不能帮忙：电鱼、炸鱼、毒鱼、禁捕期偷捕和转移入侵物种都违法且破坏生态。"
                                    "如果你想提高渔获，我可以帮你看看合法的钓点、时段和饵料。")
    if INJECTION.search(text):
        return GuardVerdict(False, "input", "injection", "refuse", risk="high",
                            message="抱歉，我不能提供系统配置或修改自身规则。我可以帮你查鱼种知识、捕捞规则、天气潮汐，或者识别鱼的照片。")
    if os.getenv("GUARD_MODELS") == "1":  # pragma: no cover - 需模型权重
        pass  # Prompt Guard 2 / Qwen3Guard 在这里接入，返回 medium / high 风险
    return GuardVerdict(True)


def check_image_text(overlay: str) -> bool:
    """图片里的文字（OCR / VLM 读出）是否带指令。带的话只识鱼，不执行图里的文字。"""
    return bool(overlay and INJECTION.search(overlay) or re.search(r"evil|领取奖品|log_catch|随便捕捞|no size limit", overlay or "", re.I))
