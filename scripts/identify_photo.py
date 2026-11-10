"""用真实照片试识鱼。

用法：
  python scripts/identify_photo.py ~/Desktop/fish.jpg
  python scripts/identify_photo.py ~/Desktop/fish.jpg "这是什么鱼？能留吗？"

需要 .env 里：LLM_API_KEY=...、VISION=qwen。支持 jpg / png / webp（iPhone 的 HEIC 先转 jpg）。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.service import run_turn  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    img = Path(sys.argv[1]).expanduser().resolve()
    text = sys.argv[2] if len(sys.argv) > 2 else "这是什么鱼？"
    if not img.is_file():
        print(f"找不到图片：{img}")
        return
    if config.VISION != "qwen" or config.OFFLINE:
        print("提示：.env 里需要 LLM_API_KEY 和 VISION=qwen，否则只能识别评测登记表里的图片。")
    out = run_turn("u_demo", f"photo-{uuid.uuid4().hex[:6]}", text=text, image=str(img))
    print("\n回答：\n" + (out["answer"] or ""))
    print(f"\n行为：{out['behavior']}  意图：{out['intents']}  耗时：{out['latency_ms']} ms  token：{out['usage']}")
    for t in out["tool_calls"]:
        line = f"  工具 {t['name']}  ok={t['ok']}  {t.get('latency_ms', 0)} ms  {t.get('source', '')}"
        if t.get("exception"):
            line += f"\n    出错原因：{t['exception']}"
        print(line)


if __name__ == "__main__":
    main()
