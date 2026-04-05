[根目录](../../CLAUDE.md) > [src](../) > **llm**

# LLM 模块

> 最后更新：2026-04-04

## 模块职责

LLM 模块负责统一管理所有大语言模型的配置、路由与实例化，提供：

- **Profile 管理**：标准化 LLM 配置（provider、model、api_key 等）
- **别名路由**：将能力别名映射到具体 Profile
- **模型工厂**：根据 Profile 创建 LangChain `BaseChatModel` 实例
- **统一入口**：为 Graph 节点提供一致的模型获取接口

## 目录结构

```
llm/
├── __init__.py      # 模块导出
├── profile.py       # LLM Profile 数据模型
├── router.py        # LLM 路由器
└── factory.py       # ChatModel 工厂
```

## 核心组件

### LLMProfile (`profile.py`)

**职责**：标准化 LLM 配置模型

**核心字段**：
```python
@dataclass(frozen=True)
class LLMProfile:
    name: str                    # Profile 名称
    provider: str                # Provider 标识（openai, anthropic 等）
    api_key: str | None          # API 密钥
    base_url: str | None         # 自定义 Base URL
    model: str                   # 模型名称
    temperature: float = 0.0     # 温度参数
    timeout: float = 60.0        # 请求超时（秒）
    max_tokens: int | None       # 最大 Token 数
    provider_options: dict       # Provider 特定选项
```

**关键方法**：
```python
@classmethod
def from_mapping(cls, name: str, settings: Mapping) -> LLMProfile
```

### LLMRouter (`router.py`)

**职责**：
- 加载和管理所有 Profile
- 管理别名到 Profile 的映射
- 解析默认 Profile
- 创建 `BaseChatModel` 实例

**核心方法**：
```python
def resolve_profile(profile: str | None = None) -> LLMProfile
def create_model(
    *,
    profile: str | None = None,
    tags: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
    default_headers: Mapping[str, str] | None = None,
) -> BaseChatModel
```

**解析逻辑**：
1. 如果指定了 `profile`，直接使用
2. 如果指定了 `binding`，先查找 `llm_bindings`，再查找 `aliases`
3. 都未指定时，使用 `default_profile`

### ChatModelFactory (`factory.py`)

**职责**：根据 Provider 创建对应的 LangChain ChatModel

**当前支持**：
- `openai` → `ChatOpenAI`

**核心方法**：
```python
def create(
    profile: LLMProfile,
    default_headers: Mapping[str, str] | None = None,
) -> BaseChatModel
```

## 配置结构

### 三层配置模型

```yaml
llm:
  # 1. 默认 Profile
  default_profile: default

  # 2. 能力别名
  aliases:
    general_chat: default
    structured_output: default
    tool_calling: default
    multimodal: mult_model

  # 3. 真实 Profile 配置
  profiles:
    default:
      provider: openai
      api_key: null
      base_url: null
      model: gpt-4o-mini
      temperature: 0.0
      timeout: 60.0
      max_tokens: null
      provider_options: {}

    mult_model:
      provider: openai
      api_key: null
      base_url: null
      model: gemini-3.1-pro
      temperature: 0.0
      timeout: 90.0
      max_tokens: null
      provider_options: {}
```

### 环境变量映射

```env
# 默认 Profile
LLM__DEFAULT_PROFILE=default

# 别名映射
LLM__ALIASES__STRUCTURED_OUTPUT=default
LLM__ALIASES__TOOL_CALLING=default

# Profile 配置
LLM__PROFILES__DEFAULT__PROVIDER=openai
LLM__PROFILES__DEFAULT__MODEL=gpt-4o-mini
LLM__PROFILES__DEFAULT__API_KEY=your-api-key
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
LLM__PROFILES__DEFAULT__TEMPERATURE=0.0
LLM__PROFILES__DEFAULT__TIMEOUT=60.0
```

## 使用方式

### 在 Graph 中使用

```python
class MyGraphNodes:
    async def analyze(
        self,
        state: MyGraphState,
        runtime: GraphRunContext,
    ) -> dict[str, object]:
        # 方式 1：获取结构化输出模型
        model = runtime.context.structured_model(
            binding="analysis",
            schema=MyOutputSchema,
        )

        # 方式 2：获取工具调用模型
        model = runtime.context.tool_model(
            binding="agent",
            tools=tools,
        )

        # 方式 3：获取普通模型
        model = runtime.context.resolve_model(
            binding="chat",
        )

        response = await model.ainvoke(messages)
        return {"result": response}
```

### 解析流程示例

假设配置如下：
```yaml
llm:
  default_profile: default
  aliases:
    structured_output: default
  profiles:
    default:
      provider: openai
      model: gpt-4o-mini

graphs:
  my-graph:
    llm_bindings:
      analysis: structured_output
```

调用 `runtime.context.structured_model(binding="analysis", schema=...)` 的解析流程：
```
analysis
→ graphs.my-graph.llm_bindings.analysis
→ "structured_output"
→ llm.aliases.structured_output
→ "default"
→ llm.profiles.default
→ ChatOpenAI(model="gpt-4o-mini")
```

## 扩展新 Provider

### 步骤 1：在 factory.py 添加构建器

```python
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

class ChatModelFactory:
    def create(self, profile: LLMProfile, ...) -> BaseChatModel:
        if profile.provider == "openai":
            return self._create_openai(profile, default_headers)
        elif profile.provider == "anthropic":
            return self._create_anthropic(profile, default_headers)
        elif profile.provider == "google":
            return self._create_google(profile, default_headers)
        else:
            raise ValueError(f"Unknown provider: {profile.provider}")

    def _create_anthropic(self, profile: LLMProfile, headers):
        return ChatAnthropic(
            model=profile.model,
            api_key=profile.api_key,
            base_url=profile.base_url,
            temperature=profile.temperature,
            timeout=profile.timeout,
            max_tokens=profile.max_tokens,
            default_headers=headers,
        )

    def _create_google(self, profile: LLMProfile, headers):
        return ChatGoogleGenerativeAI(
            model=profile.model,
            api_key=profile.api_key,
            temperature=profile.temperature,
            timeout=profile.timeout,
            max_tokens=profile.max_tokens,
        )
```

### 步骤 2：配置新 Profile

```yaml
llm:
  profiles:
    claude-opus:
      provider: anthropic
      model: claude-3-opus-20240229
      api_key: ${ANTHROPIC_API_KEY}

    gemini-pro:
      provider: google
      model: gemini-pro
      api_key: ${GOOGLE_API_KEY}
```

### 步骤 3：更新别名

```yaml
llm:
  aliases:
    creative: claude-opus
    multimodal: gemini-pro
```

### 步骤 4：在 Graph 中使用

```python
# Graph 配置
graphs:
  creative-agent:
    llm_bindings:
      writer: creative

# Node 调用
model = runtime.context.resolve_model(binding="writer")
```

## 最佳实践

### 1. 使用语义化 Binding

```python
# 推荐
runtime.context.structured_model(binding="analysis", schema=...)
runtime.context.tool_model(binding="agent", tools=...)

# 不推荐
runtime.context.resolve_model(profile="gpt-4o-mini")
```

### 2. 统一管理 Alias

将常见能力抽象为平台级别名：
- `structured_output`：结构化输出
- `tool_calling`：工具调用
- `general_chat`：通用对话
- `creative`：创意生成
- `multimodal`：多模态

### 3. Profile 命名规范

- 使用小写和连字符：`gpt-4o-mini`, `claude-3-opus`
- 语义化命名：`fast-cheap`, `smart-slow`, `multimodal-vision`

### 4. 环境隔离

```yaml
profiles:
  dev:
    model: gpt-4o-mini
    temperature: 0.7

  prod:
    model: gpt-4-turbo
    temperature: 0.0
```

## 常见问题

### Q: 如何切换到 OpenAI 兼容服务？

A: 配置 `base_url`：
```env
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
```

### Q: 如何为不同 Graph 使用不同模型？

A: 在 Graph 配置中指定不同 binding：
```yaml
graphs:
  graph-a:
    llm_bindings:
      agent: fast-cheap

  graph-b:
    llm_bindings:
      agent: smart-slow
```

### Q: 如何调试模型调用？

A: 开启 payload 日志：
```env
OBSERVABILITY__LOG_PAYLOADS=true
```

## 相关文件清单

- `__init__.py` - 模块导出
- `profile.py` - LLM Profile 数据模型
- `router.py` - LLM 路由器
- `factory.py` - ChatModel 工厂

## 变更记录

### 2026-04-04
- 初始化 LLM 模块文档
- 补充扩展指南与最佳实践
