# 安全推理论文脉搏

每天 08:00（Asia/Shanghai）自动抓取 IACR ePrint 与 arXiv 新论文，用兼容 OpenAI `/chat/completions` 的 API 做两阶段筛选和中文导读，随后发送一封邮件并更新 GitHub Pages。

## 上游项目与致谢

本项目基于 Jamie Cui 的开源项目 [Jamie-Cui/paper-pulse](https://github.com/Jamie-Cui/paper-pulse) 二次开发。感谢原作者及贡献者提供 IACR/arXiv 多源抓取、AI 论文摘要、邮件简报、静态网页、RSS、BibTeX 与 GitHub Actions 自动更新等功能和实现基础。

自 2026 年 8 月起，本项目针对安全推理与隐私计算场景，加入了 IACR 全量元数据收录、关键词宽召回与 AI 语义筛选、摘要/全文分级导读、投递游标与失败重试等改进。上游项目采用 GPL-3.0 许可证，本衍生项目继续以相同许可证发布；完整条款见 [LICENSE](LICENSE)。

## 实际规则

- IACR ePrint：通过官方 OAI-PMH 分页回补投递窗口内的全部首次发布论文；相关论文生成摘要导读，直接安全推理论文生成全文精选。OAI 临时不可用时降级到 recent RSS，但不推进回补游标。
- arXiv：抓取 `cs.CR`、`cs.AI`、`cs.LG`、`cs.CL`。关键词宽召回命中的论文全部收录；AI 判定不相关时忠实翻译标题和原始摘要（保留英文原文，不生成中文总结），相关论文生成中文摘要导读，直接研究安全推理的论文生成全文精选。
- 筛选分两步：关键词和同义词宽召回决定是否收录 arXiv 元数据，然后由 AI 根据标题与摘要决定是否生成导读以及是否读取全文。
- 全文精选不限制固定篇数。每篇 PDF 最大 50 MB，正文在本地提取、分块再合并总结。IACR 官方 PDF 若被 Cloudflare 拦截，会尝试标题完全一致的 arXiv 公开副本；找不到时明确降级并进入重试队列。
- 只处理首次发布，忽略同一论文的后续修订。
- 首次运行回补 24 小时；之后从上一封成功邮件继续。IACR 与 arXiv 都会分页到该游标，不设置静默截断上限。发送失败不推进游标，恢复后会补发。SMTP 接受邮件后，Actions 会重试提交投递状态；极少数“邮件已接受但 GitHub 始终无法保存状态”的情形可能造成下次重复投递，失败日志会明确提示人工检查。
- 没有新内容时不发邮件。网页保留最近 30 天内容。

## 部署到公开 GitHub 仓库

### 1. 推送代码

在 GitHub 新建一个 **Public** 仓库，将本项目完整推送进去。默认分支使用 `main` 或 `master` 均可。

### 2. 配置 GitHub Secrets

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，创建：

| Secret | 示例或说明 |
|---|---|
| `AI_BASE_URL` | `https://api.deepseek.com/v1`，也可换成其他兼容 API |
| `AI_API_KEY` | 你的 API Key |
| `AI_MODEL` | `deepseek-v4-flash`，按供应商实际模型名修改 |
| `SMTP_SERVER` | QQ：`smtp.qq.com`；163：`smtp.163.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USERNAME` | 完整发件邮箱地址 |
| `SMTP_PASSWORD` | 邮箱后台生成的 SMTP **授权码**，不是登录密码 |
| `EMAIL_FROM` | 通常与 `SMTP_USERNAME` 相同 |
| `EMAIL_TO` | 收件邮箱；多个地址用英文逗号分隔 |

QQ 邮箱需在 **设置 → 账号 → POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务** 中开启 SMTP 并生成授权码。163 邮箱需在 **设置 → POP3/SMTP/IMAP** 中开启服务并生成授权码。

不要把 Key、授权码或真实邮箱写入 `config.toml`、`.env.example` 或提交历史。

### 3. 启用 Pages

进入 **Settings → Pages**，将 **Source** 设为 **GitHub Actions**。日报工作流会直接部署静态网页，不依赖机器人提交再次触发构建。

进入 **Settings → Actions → General**，确认工作流允许运行；若仓库策略限制 `GITHUB_TOKEN`，将 **Workflow permissions** 设为 **Read and write permissions**。

### 4. 首次运行

打开 **Actions → Daily Paper Digest → Run workflow**。成功后：

- 收件箱会收到第一封简报；
- `data/`、`feed.xml` 与 `config.js` 会由机器人提交；
- 网页地址为 `https://你的用户名.github.io/仓库名/`。

以后 `.github/workflows/daily.yml` 会在每天 `00:00 UTC`，即北京时间 `08:00` 自动运行。GitHub 的计划任务可能有几分钟排队延迟。

## 后续修改位置

完整操作说明见：[AI 提示词与模型 API 配置说明](docs/AI_CONFIGURATION.md)。

- 兴趣词与同义词：[config.toml](config.toml) 的 `[interest].terms`
- 兴趣画像和安全推理边界：[scripts/main.py](scripts/main.py) 的 `INTEREST_PROFILE`
- AI 模型、超时、PDF 大小与分块：[config.toml](config.toml) 的 `[ai]`
- AI 元数据翻译与摘要格式：[scripts/ai.py](scripts/ai.py) 的 `translate_metadata()`、`summarize_abstract()` 与 `summarize_fulltext()`
- arXiv 分类：[config.toml](config.toml) 的 `[fetchers.arxiv].categories`
- 网页保留天数：[config.toml](config.toml) 的 `retention_days`

API 地址和模型名也能通过 `AI_BASE_URL`、`AI_MODEL` Secrets 覆盖，无需改代码。

## 本地验证

需要 Python 3.11+：

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

本地完整运行前，在当前 shell 中设置 `.env.example` 列出的环境变量；程序不会自动读取 `.env` 文件。具体示例见 [AI 配置说明的本地安全测试](docs/AI_CONFIGURATION.md#4-本地安全测试)。然后执行：

```bash
python scripts/main.py --dry-run  # 不发信、不改变投递状态
python scripts/main.py            # 正式发信并写入状态
```

网页不能直接用 `file://` 打开，因为浏览器会拦截 JSON 请求；在项目根目录运行：

```bash
python -m http.server 8000
```

然后访问 `http://localhost:8000/`。

## 数据与公开性

这是公开仓库方案，因此代码、兴趣画像、论文元数据、中文总结、重试状态、Actions 日志和 Pages 网页都是公开的。API Key 与邮箱授权码只存在 GitHub Secrets 中。论文 PDF 只在 Actions 临时运行器内处理，不会提交到仓库。

## License

[GPL-3.0](LICENSE)
