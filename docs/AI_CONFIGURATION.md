# AI 提示词与模型 API 配置说明

本文说明如何修改论文筛选与总结提示词，以及如何为 GitHub Actions 更换中转站、API Key 和模型。

## 一页速查

| 想修改的内容 | 修改位置 |
|---|---|
| 关键词宽召回 | [`config.toml`](../config.toml) 的 `[interest].terms` |
| 研究兴趣与筛选边界 | [`scripts/main.py`](../scripts/main.py) 的 `INTEREST_PROFILE` |
| AI 相关性判断提示词 | [`scripts/ai.py`](../scripts/ai.py) 的 `AIClient.classify()` |
| 摘要导读提示词 | [`scripts/ai.py`](../scripts/ai.py) 的 `AIClient.summarize_abstract()` |
| 全文分块事实提取提示词 | [`scripts/ai.py`](../scripts/ai.py) 的 `AIClient.summarize_fulltext()` 中分块循环 |
| 全文最终总结提示词 | [`scripts/ai.py`](../scripts/ai.py) 的 `AIClient.summarize_fulltext()` 中最终 `prompt` |
| 线上中转站、密钥和模型 | GitHub Actions Secrets：`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` |
| 默认 API 参数与输出上限 | [`config.toml`](../config.toml) 的 `[ai]` |

## 1. 提示词在哪里修改

提示词目前直接写在 `scripts/ai.py` 中，不从 `config.toml` 读取。这样可以把提示词与它所依赖的输入、输出格式放在一起，修改时也更容易检查解析逻辑。

### 1.1 相关性与安全推理判断

修改 `AIClient.classify()`：

- `prompt = f"""..."""` 是发送给模型的用户提示词；
- `_json_chat()` 的第一个参数是 system 提示词；
- `INTEREST_PROFILE` 位于 `scripts/main.py`，用于描述长期研究兴趣；
- `[interest].terms` 位于 `config.toml`，负责第一阶段关键词宽召回。arXiv 命中的论文都会以元数据形式收录，AI 判断决定是否进一步生成摘要导读或全文精选。

分类结果会被程序解析，因此应保留以下 JSON 字段及含义：

```json
{
  "papers": [
    {
      "id": "输入论文的原始 id",
      "relevant": true,
      "secure_inference": false,
      "reason_zh": "中文理由",
      "topics": ["主题"]
    }
  ]
}
```

必须保留顶层 `papers` 数组，并为每个输入返回一个有效 `id`；遗漏输入 `id` 会使整批论文解析失败并重试。强烈建议保留其余字段：缺少 `relevant` 或 `secure_inference` 会被当作 `false`，缺少理由或主题则会使用默认值。可以调整判定边界和理由要求，但不要改变程序依赖的结构，除非同时修改解析代码与测试。

### 1.2 摘要导读

修改 `AIClient.summarize_abstract()`。其中可以调整：

- 输出章节，例如“一句话结论、问题与动机、方法与技术路线”；
- 总结风格、中文表达和术语保留方式；
- 方法部分需要覆盖的技术细节；
- 目标篇幅和禁止编造的约束。

函数末尾 `_text_chat()` 的第一个参数是 system 提示词，适合修改模型角色和总体写作原则；`prompt` 则适合修改具体格式与内容要求。

测试 `tests/test_pipeline.py` 中包含对摘要提示词关键约束的断言。删除或改写“方法与技术路线”“关键步骤或模块”“摘要未说明”“不要为凑长度补造细节”等固定短语时，应同步更新该测试，并先运行本地测试。

### 1.3 全文精选

全文总结是两阶段流程，都位于 `AIClient.summarize_fulltext()`：

1. **分块事实提取**：PDF 较长时，程序先逐块提取协议步骤、复杂度、实验和限制等事实。
2. **最终合并总结**：再根据正文或各块事实笔记生成最终“全文精选”。

如果希望方法部分更详细，应同时修改这两层提示词。只改最终总结提示词，无法恢复第一阶段没有保留下来的正文细节。

### 1.4 篇幅与采样参数

提示词中的“约 600—1000 字”等内容是写作目标；API 的硬上限在 `config.toml` 的 `[ai]`：

| 配置项 | 作用 |
|---|---|
| `temperature` | 采样温度；较低通常更稳定 |
| `classification_max_tokens` | 一批分类结果的最大输出 token 数 |
| `summary_max_tokens` | 摘要、分块笔记和最终全文总结的最大输出 token 数 |
| `fulltext_chunk_chars` | 全文分块的字符数；模型上下文较小时可适当降低 |
| `timeout` | 单次 API 请求与 PDF 请求超时秒数 |
| `max_retries`、`retry_delay` | 包含首次请求的最大尝试次数，以及首次重试前的基础等待时间；后续指数退避 |
| `pdf_max_mb` | 单篇 PDF 最大下载大小 |

`summary_max_tokens` 只是输出上限，不保证模型一定写满提示词要求的篇幅。

## 2. 在 GitHub Actions 中切换中转站或模型

进入当前仓库：

```text
Settings → Secrets and variables → Actions → Repository secrets
```

更新以下三个 Secret：

| Secret | 填写内容 | 示例 |
|---|---|---|
| `AI_BASE_URL` | 中转站的 OpenAI Chat Completions 兼容入口 | `https://provider.example/v1` |
| `AI_API_KEY` | 新中转站生成的密钥 | 不要写入仓库或日志 |
| `AI_MODEL` | 中转站要求的准确模型 ID | `provider-model-id` |

保存后不需要修改工作流。下一次定时任务会自动使用新配置；也可以打开：

```text
Actions → Daily Paper Digest → Run workflow
```

手动验证。手动运行的是完整任务：若投递窗口内有真正的新论文，仍可能发送邮件；已有论文不会仅因更换模型而自动重新总结。

如果更新 Repository secret 后仍未生效，再检查：

```text
Settings → Environments → github-pages
```

是否存在同名 Environment secret。若存在，同名 Environment secret 的优先级更高。

### 2.1 配置覆盖顺序

程序实际采用以下顺序：

```text
非空环境变量 AI_BASE_URL > config.toml [ai].base_url
非空环境变量 AI_MODEL    > config.toml [ai].model
AI_API_KEY                只能来自环境变量或 GitHub Secret
```

因此，仓库已经设置 `AI_BASE_URL` 或 `AI_MODEL` Secret 时，只修改 `config.toml` 中的默认值不会改变线上任务。`temperature`、超时、重试和 token 上限等其他参数目前只从 `config.toml` 读取。

## 3. 中转站必须兼容什么接口

这里兼容的是经典 OpenAI **Chat Completions** 协议，并非所有标注“OpenAI compatible”的接口都一定可用。

程序会移除 `AI_BASE_URL` 末尾的 `/`；如果地址不以 `/chat/completions` 结尾，则自动追加该路径：

```text
https://provider.example/v1
→ https://provider.example/v1/chat/completions

https://provider.example/v1/chat/completions
→ 保持不变
```

程序不会自动补 `/v1`，应按照中转站文档填写。不要在 Base URL 中加入查询参数。

中转站至少需要支持：

- `Authorization: Bearer <AI_API_KEY>` 鉴权；
- 请求字段 `model`、`messages`、`temperature`、`max_tokens`；
- 响应字段 `choices[0].message.content`，且 `content` 为非空字符串；
- 分类模型能稳定按照提示词返回合法 JSON。

`usage` 统计字段可以缺省。原生 Anthropic、Gemini、OpenAI Responses API、自定义 `x-api-key` 鉴权，以及只接受 `max_completion_tokens` 的端点不能直接使用，除非服务商另行提供上述兼容层。

## 4. 本地安全测试

项目不会自动读取 `.env`；`.env.example` 只是变量清单。Windows PowerShell 中应在同一个终端显式设置：

```powershell
$env:AI_BASE_URL = "https://provider.example/v1"
$env:AI_API_KEY = "你的密钥"
$env:AI_MODEL = "provider-model-id"
& ".\.venv\Scripts\python.exe" scripts/main.py --dry-run
```

`--dry-run` 会：

- 真实访问 arXiv、IACR 和 AI API，并可能下载 PDF、消耗模型额度；
- 处理内存中的筛选、摘要和重试流程；
- 不发送邮件，也不更新已提交的论文数据、重试状态和投递游标；
- 有新论文时，把预览写入被 Git 忽略的 `.runtime/email_report.txt`。

测试结束后可以清除当前 PowerShell 会话中的密钥：

```powershell
Remove-Item Env:AI_API_KEY, Env:AI_BASE_URL, Env:AI_MODEL -ErrorAction SilentlyContinue
```

## 5. 修改何时生效

- 修改 GitHub Secrets：下一次工作流运行生效，不需要提交代码。
- 修改提示词或 `config.toml`：需要提交并推送到默认分支，下一次工作流运行生效。
- 修改提示词不会自动重写 `data/papers.json` 中已有论文的总结，只影响新生成的摘要，以及已经或后来因失败进入 `data/retry.json` 并实际重试的任务。修改提示词本身不会创建重试任务。
- 不要为了重算旧摘要删除 `data/state.json`，否则可能改变投递窗口并造成重复邮件。需要批量重算历史摘要时，应单独设计一次性重试任务。

## 6. 常见错误

| 现象 | 优先检查 |
|---|---|
| `401` 或 `403` | `AI_API_KEY` 是否属于当前中转站，是否多了空格或引号 |
| `404` | `AI_BASE_URL` 是否缺少 `/v1`，或是否填成了控制台网页地址 |
| `400` | 模型 ID 是否准确；端点是否支持 `temperature` 和 `max_tokens` |
| `AI returned invalid JSON` | 分类模型是否能可靠遵循 JSON 格式；不要破坏 `classify()` 的字段约定 |
| 超时或全文总结失败 | 增大 `timeout`，或在模型上下文较小时降低 `fulltext_chunk_chars` |
| 改了 `config.toml` 但线上没变化 | GitHub Secret 是否仍在覆盖同名默认配置 |
| 改了提示词但旧网页没变化 | 历史摘要是已保存数据，不会自动重新生成 |

## 安全提醒

这是公开仓库。不要把真实 API Key 写入 `README`、本文件、`config.toml`、`.env.example`、Actions 日志或任何提交历史。密钥只应保存在 GitHub Secrets 或本地临时环境变量中。
