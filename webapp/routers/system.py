# -*- coding: utf-8 -*-
"""system路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from ..pricing import PRICE_PER_10K  # noqa: F401  统一单价（core 不再透传）

@app.get("/api/health")
def health():
    return {"status": "ok", "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/genres")
def genres():
    return {"genres": GENRES}


@app.get("/api/pricing")
def pricing():
    return {
        "tiers": tiers(),
        "register_bonus": FREE_REGISTER_BONUS,
        "billing_mode": "by_words",   # 按字数计费
        "price_per_10k": PRICE_PER_10K,  # 统一高质量单价（普通模式下线）
    }


@app.post("/api/parse-content")
async def parse_content(file: UploadFile | None = File(None), text: str = Form(""),
                        user: dict = Depends(_require_user)):
    """解析用户手写内容（上传文件或粘贴文本）：提取文本 + 章节切分预览。"""
    try:
        if file is not None:
            data = await file.read()
            if len(data) > _MAX_UPLOAD:
                raise HTTPException(status_code=413, detail="文件过大，请上传 10MB 以内的文档")
            text = extract_text(file.filename or "", data)
        raw = text or ""
    except ValueError as e:
        logger.info("解析内容失败（文件读取/格式）：%s，user=%s", e, user["username"])
        raise HTTPException(status_code=400, detail=str(e))
    if not raw.strip():
        raise HTTPException(status_code=400, detail="内容为空，请粘贴文本或上传文件")
    chs = split_chapters(raw)
    if not chs:
        raise HTTPException(status_code=400, detail="无法识别内容，请检查文本格式")
    many = len(chs) > 1 or bool(chs[0]["title"])
    return {
        "ok": True,
        "content_type": "chapters" if many else "outline",
        "total_chars": len(raw),
        "full_text": raw,          # 服务端统一提取的完整文本（docx/txt/md 均已转码），前端填回"已有内容"
        "chapters": summarize_preview(chs),
    }


@app.post("/api/parse-outline")
async def parse_outline(file: UploadFile = File(...), user: dict = Depends(_require_user)):
    """上传大纲文档（txt/md/docx），智能解析成新建表单字段。"""
    try:
        data = await file.read()
        if len(data) > _MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="文件过大，请上传 10MB 以内的文档")
        text = extract_text(file.filename or "", data)
    except ValueError as e:
        logger.info(f"解析大纲失败（文件读取/格式）：{e}，user={user['username']}")
        raise HTTPException(status_code=400, detail=str(e))
    api_key = _server_api_key("deepseek")
    if not api_key:
        raise HTTPException(status_code=503, detail="服务端未配置解析模型（DEEPSEEK_API_KEY），请联系站长")
    llm = create_llm(LLMConfig(provider="deepseek", model="", base_url="", api_key=api_key,
                               temperature=0.2, top_p=0.7, max_tokens=2000))
    try:
        result = parse_outline_text(text, llm)
    except Exception as e:
        logger.exception(f"解析大纲异常：{e}，user={user['username']}")
        raise HTTPException(status_code=500, detail="大纲解析失败，请稍后重试")
    logger.info(f"解析大纲成功：{file.filename or '?'}，title={result.get('title')}，user={user['username']}")
    return {"ok": True, "data": result}
