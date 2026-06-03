from __future__ import annotations

from .domain import ModelProviderPreset


PROVIDER_PRESETS: list[ModelProviderPreset] = [
    ModelProviderPreset(
        name="演示模型",
        baseUrl="local://demo",
        defaultModel="amaduse-demo-stream",
        compatible=False,
        note="无需 Key，本地生成流式演示回复",
    ),
    ModelProviderPreset(
        name="OpenAI",
        baseUrl="https://api.openai.com/v1",
        defaultModel="gpt-4.1-mini",
        compatible=True,
        note="Chat Completions 兼容流式接口",
    ),
    ModelProviderPreset(
        name="OpenRouter",
        baseUrl="https://openrouter.ai/api/v1",
        defaultModel="openai/gpt-4.1-mini",
        compatible=True,
        note="聚合多家模型，使用 OpenAI-compatible 协议",
    ),
    ModelProviderPreset(
        name="小米 MiMo",
        baseUrl="https://api.xiaomimimo.com/v1",
        defaultModel="mimo-v2.5-pro",
        compatible=True,
        note="MiMo OpenAI-compatible 接口，快速模式关闭 thinking，思考模式开启 thinking",
    ),
    ModelProviderPreset(
        name="DeepSeek",
        baseUrl="https://api.deepseek.com/v1",
        defaultModel="deepseek-chat",
        compatible=True,
        note="可切换 deepseek-reasoner 获取 reasoning_content",
    ),
    ModelProviderPreset(
        name="通义千问",
        baseUrl="https://dashscope.aliyuncs.com/compatible-mode/v1",
        defaultModel="qwen-plus",
        compatible=True,
        note="DashScope 兼容模式",
    ),
    ModelProviderPreset(
        name="Kimi",
        baseUrl="https://api.moonshot.cn/v1",
        defaultModel="moonshot-v1-8k",
        compatible=True,
        note="Moonshot OpenAI-compatible 接口",
    ),
    ModelProviderPreset(
        name="智谱 GLM",
        baseUrl="https://open.bigmodel.cn/api/paas/v4",
        defaultModel="glm-4-flash",
        compatible=True,
        note="BigModel 兼容接口",
    ),
    ModelProviderPreset(
        name="Ollama",
        baseUrl="http://127.0.0.1:11434/v1",
        defaultModel="qwen2.5:7b",
        compatible=True,
        note="本机 Ollama OpenAI-compatible 服务",
    ),
    ModelProviderPreset(
        name="Anthropic",
        baseUrl="https://api.anthropic.com/v1",
        defaultModel="claude-3-5-haiku-latest",
        compatible=False,
        note="已提供配置项，专用协议后续接入",
    ),
    ModelProviderPreset(
        name="Gemini",
        baseUrl="https://generativelanguage.googleapis.com/v1beta",
        defaultModel="gemini-2.5-flash",
        compatible=False,
        note="已提供配置项，专用协议后续接入",
    ),
    ModelProviderPreset(
        name="自定义",
        baseUrl="https://example.com/v1",
        defaultModel="custom-model",
        compatible=True,
        note="填写任意 OpenAI-compatible 服务",
    ),
]


def get_provider(name: str) -> ModelProviderPreset:
    for provider in PROVIDER_PRESETS:
        if provider.name.lower() == name.lower():
            return provider
    return PROVIDER_PRESETS[-1]
