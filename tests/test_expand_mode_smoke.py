# -*- coding: utf-8 -*-
"""扩写模式冒烟测试：mock LLM 跑通 扩写大纲→世界观→章节 链路，验证不触发调研。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_gen.config import NovelConfig, LLMConfig, SearchConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.llm.factory import create_llm


def _make_pipeline():
    tmp = Path(tempfile.mkdtemp(prefix="expand_smoke_"))
    cfg = NovelConfig(
        title="扩写测试本",
        genre="都市",
        total_words=5000,
        chapter_words=2500,
        mode="expand",
        expand_input="""主角陈默是个被家族抛弃的落魄少爷，靠写代码维生。
大纲：
1. 陈默被逐出家门，在出租屋接外包糊口，偶遇前未婚妻。
2. 他接到一个神秘项目的委托，报酬极高但风险不明，他犹豫后接下。""",
        editor_review=True,
        de_ai=True,
        anti_similarity=True,
        llm=LLMConfig(provider="mock", model="mock-model"),
        search=SearchConfig(provider=""),
    )
    store = ProjectStore.create(tmp, cfg)
    llm = create_llm(cfg.llm)
    pipe = NovelPipeline(cfg, store, llm)
    return cfg, store, pipe


def test_expand_mode_skips_research():
    cfg, store, pipe = _make_pipeline()
    assert pipe.is_expand_mode(), "应识别为扩写模式"
    assert pipe.run_research() is None, "扩写模式不应触发调研"
    assert store.load_research() is None, "扩写模式全程不应有调研产物"


def test_expand_outline_and_chapter():
    cfg, store, pipe = _make_pipeline()
    outline = pipe.run_outline()
    assert outline and len(outline.chapters) == 2, f"大纲章节数错误: {len(outline.chapters) if outline else 0}"

    # 编辑质检应被跳过
    pipe.run_editor_review()
    assert store.load_editor_review() is None, "扩写模式不应执行编辑质检"

    world = pipe.run_world()
    assert world and world.world_view, "世界观应生成"

    ch1 = pipe.write_chapter(1)
    assert ch1.content and len(ch1.content) > 100, "第1章正文不应为空"

    # 导出
    out = pipe.export()
    assert out.exists(), "txt 应导出"

    # config 持久化
    cfg2 = store.load_config()
    assert cfg2.mode == "expand" and cfg2.expand_input, "mode/expand_input 应持久化"


def test_expand_mode_requires_input():
    """扩写模式缺输入应在流水线入口报错。"""
    cfg = NovelConfig(mode="expand", expand_input="   ")
    cfg.llm = LLMConfig(provider="mock", model="mock-model")
    store = ProjectStore.create(Path(tempfile.mkdtemp()), cfg)
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    try:
        pipe.run_outline()
        assert False, "空 expand_input 应抛异常"
    except RuntimeError as exc:
        assert "大纲或简要内容" in str(exc)
