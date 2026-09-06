# -*- coding: utf-8 -*-
"""
封面生成：程序化生成小说封面（PNG）。

当前实现用 Pillow 纯代码绘制（零外部 API / 零额外成本）：
- 按题材选择配色与装饰元素
- 竖版 2:3 封面（600x900），标题大字 + 题材角标 + 装饰纹理
- 输出到 output/<书名>.png，并在项目目录留一份副本

扩展性：后续接入 AI 生图（如即梦/Stable Diffusion API）时，
只需新增一个同样返回 Path 的生成器，并在调用方按开关替换。
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 题材 → (主色, 辅色, 装饰风格)。装饰风格决定背景纹理走向。
_STYLE = {
    "玄幻": ((32, 24, 74), (172, 122, 255), "mystic"),
    "仙侠": ((18, 42, 66), (140, 200, 230), "mystic"),
    "都市": ((30, 36, 48), (90, 200, 250), "urban"),
    "科幻": ((10, 20, 40), (80, 220, 200), "tech"),
    "悬疑": ((26, 26, 26), (200, 60, 60), "dark"),
    "灵异": ((24, 20, 40), (120, 90, 220), "dark"),
    "言情": ((90, 30, 60), (255, 160, 190), "romance"),
    "历史": ((58, 44, 28), (210, 170, 110), "classic"),
    "军事": ((34, 46, 34), (150, 170, 90), "tech"),
    "游戏": ((24, 40, 56), (120, 220, 120), "tech"),
    "体育": ((20, 40, 34), (100, 220, 160), "urban"),
    "二次元": ((48, 30, 90), (255, 170, 230), "romance"),
    "短篇": ((36, 30, 60), (170, 140, 255), "mystic"),
    "其他": ((34, 34, 44), (160, 170, 190), "urban"),
}

# 封面画幅（2:3 竖版）
WIDTH, HEIGHT = 600, 900


def _font(size: int) -> ImageFont.FreeTypeFont:
    """加载中文字体（Windows 常见中文字体），找不到则用默认字体。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",      # 黑体
        r"C:\Windows\Fonts\simsun.ttc",      # 宋体
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux 兜底
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_background(draw: ImageDraw.ImageDraw, style: str, w: int, h: int,
                     primary, accent) -> None:
    """绘制背景：斜向渐变 + 题材装饰纹理（在 RGBA 覆盖层上画，再合成）。"""
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # 1) 斜向渐变（用 1px 竖线近似，RGBA 上做颜色过渡）
    for y in range(h):
        ratio = y / h
        r = int(primary[0] * (1 - ratio) + primary[0] * 0.35 * ratio)
        g = int(primary[1] * (1 - ratio) + primary[1] * 0.30 * ratio)
        b = int(primary[2] * (1 - ratio) + primary[2] * 0.55 * ratio)
        od.line([(0, y), (w, y)], fill=(r, g, b, 255))

    rnd = random.Random(2026)  # 固定种子：同一本书封面稳定可复现

    # 2) 光晕：右上角一个柔光圆（用模糊滤镜）
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([w * 0.55, -h * 0.18, w * 1.35, h * 0.55], fill=accent + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    od._image.paste(glow, (0, 0), glow)

    # 3) 装饰纹理（按风格）
    if style == "tech":
        for x in range(0, w, 40):
            od.line([(x, 0), (x, h)], fill=(255, 255, 255, 28), width=1)
        for _ in range(24):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            rad = rnd.randint(2, 6)
            od.ellipse([x - rad, y - rad, x + rad, y + rad], outline=accent + (180,), width=2)
    elif style == "mystic":
        for _ in range(60):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            rad = rnd.randint(1, 3)
            od.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(255, 255, 255, 150))
        for i in range(3):
            y0 = rnd.randint(100, 700)
            pts = [(rnd.randint(-40, w), y0 + i * 60) for _ in range(4)]
            od.line(pts, fill=accent + (150,), width=3)
    elif style == "dark":
        for x in range(-h, w, 46):
            od.line([(x, 0), (x + h, h)], fill=(255, 255, 255, 20), width=1)
        od.rectangle([8, 8, w - 9, h - 9], outline=accent + (220,), width=3)
    elif style == "romance":
        for _ in range(26):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            od.ellipse([x - 7, y - 5, x + 7, y + 5], outline=accent + (200,), width=2)
    elif style == "classic":
        for x in range(0, w, 30):
            for y in range(0, h, 60):
                od.arc([x, y, x + 40, y + 40], 180, 360, fill=accent + (170,), width=2)
    else:  # urban / 兜底
        for _ in range(16):
            x, y = rnd.randint(0, w), rnd.randint(0, h)
            od.line([(x - 25, y), (x + 25, y)], fill=accent + (160,), width=2)

    # 把覆盖层合成到基底（基底先填纯色）
    draw._image.paste(overlay, (0, 0), overlay)


def generate_cover(
    title: str,
    genre: str = "其他",
    author: str = "AI 自动生成",
    subtitle: str = "",
    out_path: Optional[Path] = None,
) -> Path:
    """程序化生成封面 PNG 并返回路径。

    :param title: 书名（过长自动换行）
    :param genre: 题材，用于配色与装饰
    :param author: 作者署名
    :param subtitle: 副标题（可选，展示在书名下方）
    :param out_path: 输出路径（必填）
    """
    primary, accent, style = _STYLE.get(genre, _STYLE["其他"])

    img = Image.new("RGB", (WIDTH, HEIGHT), primary)
    draw = ImageDraw.Draw(img)
    _draw_background(draw, style, WIDTH, HEIGHT, primary, accent)

    # 标题：按宽度自动换行
    size = 66 if len(title) <= 6 else (56 if len(title) <= 10 else 46)
    font = _font(size)
    max_width = WIDTH - 120
    lines: list[str] = []
    cur = ""
    for ch in title:
        test = cur + ch
        est = sum(36 if ord(c) < 128 else size for c in test)
        if est > max_width and cur:
            lines.append(cur.rstrip("《》"))
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur.rstrip("《》"))
    if not lines:
        lines = [title[:6]]
    lines = lines[:3]

    line_h = size + 14
    total_h = line_h * len(lines)
    y = HEIGHT // 2 - total_h // 2 - 40

    for line in lines:
        text = line.strip() or "无题"
        total_w = sum(draw.textlength(ch, font=font) for ch in text)
        x = (WIDTH - total_w) / 2
        for ch in text:
            tw = draw.textlength(ch, font=font)
            draw.text((x + 2, y + 2), ch, font=font, fill=(0, 0, 0, 200))
            draw.text((x, y), ch, font=font, fill=(255, 255, 255))
            x += tw
        y += line_h

    # 副标题
    if subtitle:
        sub_font = _font(22)
        draw.text((WIDTH / 2, y + 10), subtitle, font=sub_font,
                  fill=(235, 235, 235), anchor="mm")

    # 作者署名（底部）
    author_font = _font(20)
    draw.text((WIDTH / 2, HEIGHT - 52), f"— {author} —", font=author_font,
              fill=(220, 220, 220), anchor="mm")

    # 题材角标（左上）
    tag_font = _font(20)
    tag_text = genre or "其他"
    tag_w = draw.textlength(tag_text, font=tag_font) + 28
    tag_box = (30, 30, 30 + int(tag_w), 62)
    draw.rounded_rectangle(tag_box, radius=16, fill=accent + (220,))
    draw.text(((tag_box[0] + tag_box[2]) / 2, (tag_box[1] + tag_box[3]) / 2),
              tag_text, font=tag_font, fill=(255, 255, 255), anchor="mm")

    # 底部分隔线
    draw.line([(WIDTH * 0.2, HEIGHT - 78), (WIDTH * 0.8, HEIGHT - 78)],
              fill=accent, width=2)

    if out_path is None:
        raise ValueError("out_path 不能为空")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG")
    return out_path
