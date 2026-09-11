# -*- coding: utf-8 -*-
"""
封面生成：优先调用硅基流动（SiliconFlow）ERNIE-Image-Turbo 生成 AI 封面，
失败或未配置 Key 时回退到程序化绘制（Pillow，零成本）。

AI 封面策略：
- AI 只生成竖版背景插画（prompt 明确禁止出现文字，避免 AI 画错书名）
- 书名/题材/作者/副标题由本模块用 Pillow 精确叠加，保证文字 100% 正确
- 输出统一为 2:3 竖版（600x900）PNG

配置（.env 或环境变量，服务启动时加载）：
    SILICONFLOW_API_KEY=sk-xxx      # 硅基流动 API Key（必填才启用 AI 封面）
    AI_COVER_ENABLED=1              # 默认开启；设 0 强制只用程序化封面
"""
from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger("novel.cover")

# 硅基流动文生图 API（国内站 .cn 优先；.com 为国际站，部分 Key 不通用）
SILICONFLOW_API_URLS = [
    "https://api.siliconflow.cn/v1/images/generations",
    "https://api.siliconflow.com/v1/images/generations",
]
# 封面模型 ID（生产可经 COVER_MODEL 环境变量指定；默认硅基流动 ERNIE-Image-Turbo）
AI_MODEL = os.getenv("COVER_MODEL", "baidu/ERNIE-Image-Turbo")

# 生产严格模式：=1 时 AI 封面失败直接抛异常（不回退程序化封面），便于暴露生成问题
COVER_AI_STRICT = os.getenv("COVER_AI_STRICT", "0") == "1"

# 题材 → (主色, 辅色, 装饰风格)。装饰风格决定背景纹理走向（程序化兜底用）。
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
    """加载中文字体（Windows 常见中文字体 + Linux 常见中文字体），找不到则用默认字体。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",      # 黑体
        r"C:\Windows\Fonts\simsun.ttc",      # 宋体
        r"C:\Windows\Fonts\simkai.ttf",      # 楷体
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",   # Linux: Noto CJK
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",             # Linux: 文泉驿
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # 最终兜底（无中文）
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


def _wrap_title(title: str, font, max_width: int, size: int) -> list[str]:
    """按像素宽度把书名换行（去书名号），最多 3 行。"""
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
    return lines[:3]


def _draw_text_overlay(img: Image.Image, title: str, genre: str,
                       author: str, subtitle: str) -> None:
    """在任意封面底图上叠加书名/副标题/作者/题材角标（文字 100% 精确）。"""
    draw = ImageDraw.Draw(img)
    w, h = img.size

    # 标题：按宽度自动换行
    size = 66 if len(title) <= 6 else (56 if len(title) <= 10 else 46)
    font = _font(size)
    max_width = w - 120
    lines = _wrap_title(title, font, max_width, size)

    line_h = size + 14
    total_h = line_h * len(lines)
    y = h // 2 - total_h // 2 - 40

    for line in lines:
        text = line.strip() or "无题"
        total_w = sum(draw.textlength(ch, font=font) for ch in text)
        x = (w - total_w) / 2
        for ch in text:
            tw = draw.textlength(ch, font=font)
            draw.text((x + 2, y + 2), ch, font=font, fill=(0, 0, 0, 200))
            draw.text((x, y), ch, font=font, fill=(255, 255, 255))
            x += tw
        y += line_h

    # 副标题
    if subtitle:
        sub_font = _font(22)
        draw.text((w / 2, y + 10), subtitle, font=sub_font,
                  fill=(235, 235, 235), anchor="mm")

    # 作者署名（底部）
    author_font = _font(20)
    draw.text((w / 2, h - 52), f"— {author} —", font=author_font,
              fill=(220, 220, 220), anchor="mm")

    # 题材角标（左上）：按用户要求不画，避免封面出现标签字

    # 底部分隔线
    draw.line([(w * 0.2, h - 78), (w * 0.8, h - 78)], fill=(255, 255, 255, 140), width=2)


def _build_cover_prompt(title: str, genre: str, subtitle: str, extra: str = "") -> str:
    """构造 AI 封面 prompt：只描述题材氛围插画，文字一律由程序叠加，
    因此 prompt 不注入书名/副标题（否则 AI 会渲染成画面文字）。"""
    desc = {
        "玄幻": "东方玄幻史诗感，仙山云海与星辰交汇，玄奥符文光带缭绕，神秘磅礴",
        "仙侠": "水墨意境与仙光结合，青峰流云、飞剑灵鹤，清冷出尘的仙侠氛围",
        "都市": "现代都市夜景，霓虹光影与摩天楼群，冷暖色调对比，时尚锐利",
        "科幻": "未来科幻场景，宇宙星云、机械结构、能量光流，冷冽科技感",
        "悬疑": "阴暗诡谲氛围，迷雾中的轮廓、冷色光线、若隐若现的线索，张力十足",
        "灵异": "幽冥诡秘氛围，幽暗烛光、古旧物什、雾气缭绕，阴冷神秘",
        "言情": "唯美浪漫氛围，柔光、花影、暖色调，细腻温柔的情绪感",
        "历史": "厚重历史质感，古建筑剪影、泛黄色调、史诗般的苍茫",
        "军事": "冷硬军事风格，钢铁与硝烟、战术光影、力量感",
        "游戏": "游戏 CG 质感，奇幻世界场景、冒险氛围、华丽光影",
        "体育": "热血动感，赛场光影、速度线条、迸发的力量与激情",
        "二次元": "二次元动漫风格，明快色彩、精致插画质感、青春活力",
        "短篇": "文艺电影质感，氛围光影、留白构图、故事感",
        "其他": "大气文学质感，抽象光影层次、高级色调、留白构图",
    }.get(genre, "大气文学质感，抽象光影层次、高级色调、留白构图")

    return (
        f"一张完整的竖版网文小说封面成品图。"
        f"封面上必须用大号、清晰、正确的中文艺术字写出书名：{title}。"
        f"书名要和画面风格融为一体，字号大、居中偏上、视觉醒目，"
        f"字体要有设计感（毛笔/书法/衬线艺术字均可），字色与背景强对比，"
        f"保证清晰可读，不能写错别字、不能缺笔画。"
        f"画面氛围：{desc}。"
        + (f"本书核心内容：{extra}。" if extra else "") +
        f"构图：书名居中偏上，主体景物偏下三分之一。"
        f"风格：高端网文封面质感，光影层次丰富，色彩协调，细节精致。"
        f"除书名「{title}」外，不要出现其他任何文字、字母、数字、符号、logo、水印、AI生成标记、签名。"
    )


def _fit_cover(img: Image.Image) -> Image.Image:
    """把任意尺寸图片适配为 2:3 竖版封面（覆盖式缩放 + 居中裁剪）。"""
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new_w, new_h = round(img.width * scale), round(img.height * scale)
    img = img.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - WIDTH) // 2
    top = (new_h - HEIGHT) // 2
    return img.crop((left, top, left + WIDTH, top + HEIGHT))


def generate_ai_cover(
    title: str,
    genre: str = "其他",
    author: str = "AI 自动生成",
    subtitle: str = "",
    out_path: Optional[Path] = None,
    extra: str = "",
) -> Optional[Path]:
    """调用硅基流动 ERNIE-Image-Turbo 生成 AI 封面。

    任一步失败（未配 Key/开关关闭/网络/接口异常）都返回 None，由调用方回退。
    """
    if out_path is None:
        raise ValueError("out_path 不能为空")
    if os.getenv("AI_COVER_ENABLED", "1") == "0":
        logger.info("AI 封面已通过 AI_COVER_ENABLED=0 关闭，跳过")
        return None
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        logger.info("未配置 SILICONFLOW_API_KEY，跳过 AI 封面")
        return None

    prompt = _build_cover_prompt(title, genre, subtitle, extra)
    seed = random.randint(0, 999999999)
    payload = {
        "model": AI_MODEL,
        "prompt": prompt,
        "image_size": "768x1024",
        "seed": seed,
        "output_format": "png",
        "watermark": False,
        "negative_prompt": "其他文字, 字母, 数字, 符号, logo, 水印, AI生成标记, 签名, 边框",
    }
    try:
        with httpx.Client(timeout=90) as client:
            last_exc: Optional[Exception] = None
            for api_url in SILICONFLOW_API_URLS:
                try:
                    resp = client.post(
                        api_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    images = (data or {}).get("images") or []
                    if not images or not images[0].get("url"):
                        logger.warning("%s 返回无图片: %s", api_url, str(data)[:300])
                        return None
                    img_url = images[0]["url"]
                    img_resp = client.get(img_url, timeout=90)
                    img_resp.raise_for_status()
                    raw = Image.open(__import__("io").BytesIO(img_resp.content))
                    break  # 成功
                except Exception as exc:  # noqa: BLE001 - 尝试下一个域名
                    last_exc = exc
                    logger.warning("AI 封面请求失败(%s)：%s", api_url, exc)
            else:
                raise last_exc or RuntimeError("所有 API 域名均失败")
    except Exception as exc:  # noqa: BLE001 - 封面失败不应影响主流程
        logger.warning("AI 封面生成失败(%s)：%s", AI_MODEL, exc)
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cover = _fit_cover(raw)
    # 书名已由 AI 直接画入画面，不再 Pillow 后期叠加（避免贴纸感）
    cover.save(str(out_path), format="PNG")
    logger.info("AI 封面已生成: %s (seed=%d)", out_path, seed)
    return out_path


def generate_cover(
    title: str,
    genre: str = "其他",
    author: str = "AI 自动生成",
    subtitle: str = "",
    out_path: Optional[Path] = None,
) -> Path:
    """程序化生成封面 PNG 并返回路径（零外部依赖，作为兜底）。"""
    primary, accent, style = _STYLE.get(genre, _STYLE["其他"])

    img = Image.new("RGB", (WIDTH, HEIGHT), primary)
    draw = ImageDraw.Draw(img)
    _draw_background(draw, style, WIDTH, HEIGHT, primary, accent)
    _draw_text_overlay(img, title, genre, author, subtitle)

    if out_path is None:
        raise ValueError("out_path 不能为空")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG")
    return out_path


def generate_best_cover(
    title: str,
    genre: str = "其他",
    author: str = "AI 自动生成",
    subtitle: str = "",
    out_path: Optional[Path] = None,
    extra: str = "",
) -> Path:
    """AI 封面优先（配了 Key 且开关开启）。

    默认失败回退程序化封面（测试环境友好）；
    COVER_AI_STRICT=1（生产）时失败直接抛异常，确保生产封面一定走配置模型生成。
    """
    ai = generate_ai_cover(title, genre, author, subtitle, out_path, extra=extra)
    if ai is not None:
        return ai
    if COVER_AI_STRICT:
        raise RuntimeError(
            "AI 封面生成失败（COVER_AI_STRICT=1 不回退程序化）："
            f"model={AI_MODEL}，请检查 COVER_MODEL/SILICONFLOW_API_KEY 配置"
        )
    return generate_cover(title, genre, author, subtitle, out_path)
