# 小波AI资讯站

面向中文 AI / 科技内容创作者的静态资讯站。页面是简体中文，数据由 GitHub Actions 每小时抓取并部署到 GitHub Pages。

站点地址：<https://bocai08081-ux.github.io/xiaobo-ai-news/>

不调用需要密钥的 LLM。各数据源互相隔离，一个订阅挂了不会让整次构建失败。

## 页面上有什么

- **最新**：过去 72 小时的报道、论文、视频和软件版本，按上海时间分天。
- **收藏**：点条目上的「收藏」留下的选题，只存在这台浏览器里，刷新数据不会清掉。
- **仅中文**：按标题判断。中文标题会留下，纯英文标题会藏起。
- **复制选题**：把标题、来源、摘要和链接复制成一段文字，方便贴进稿子。
- **社区**：Hacker News 上与 AI 相关的帖子（Algolia API）。
- **论文 / 模型**：Hugging Face 每日论文，以及当前趋势模型。模型是热榜快照，不混进「最新」。
- **开源**：GitHub Trending 里标题或简介像 AI 项目的仓库，同样按热榜展示。
- **视频**：AI Explained、Matthew Berman、Two Minute Papers、Wes Roth 的频道 RSS。
- **媒体**：The Verge AI、TechCrunch AI、Simon Willison 的博客（跳过 “Quoting” 摘录，只留像 AI 的文章）。
- **官方**：OpenAI、Google AI、Google DeepMind 的 RSS。Anthropic 没有公开 RSS，改读新闻页。
- **中文**：量子位 RSS。雷峰网、爱范儿、极客公园、36氪、钛媒体、InfoQ、Solidot 是综合科技源，只保留标题像 AI 的条目，避免把 “AI” 当成单词里的字母（例如 Wayfair）。机器之心的公开订阅目前会被站点拦截并转到数据服务，抓取失败时只记在页脚，不中断构建。
- **本地部署**：MLX、mlx-lm、llama.cpp、Ollama、LM Studio、AMD ROCm（ROCm/TheRock）、Lemonade（lemonade-sdk/lemonade）的发布记录。

每条包含标题、来源、时间和链接。用规范化链接和标题去重。页眉的更新时间按 Asia/Shanghai 显示。

## 本地预览

```bash
python3 scripts/fetch_news.py
python3 -m http.server 4173 --directory site
```

打开 <http://127.0.0.1:4173> 。只跑逻辑测试：

```bash
python3 -m unittest scripts/test_fetch.py
```

抓取脚本只用 Python 标准库。

## 自动部署

工作流：[.github/workflows/pages.yml](.github/workflows/pages.yml)

- 每小时整点（UTC cron `0 * * * *`，GitHub 可能会延迟几分钟）
- 手动 `workflow_dispatch`
- 推送到 `main` 时也会构建一次

流程：抓取 `site/data/news.json` → `actions/configure-pages` → `upload-pages-artifact` → `deploy-pages`。

仓库里提交的 `news.json` 只是一份快照。线上每小时会重新生成，不会把数据提交回 git。

## 需要所有者做的一次性设置

`actions/configure-pages` 的 `enablement: true` **不能**用默认的 `GITHUB_TOKEN` 完成。官方说明要求另备令牌：PAT 需要 `repo` 范围或 Pages 写权限；GitHub App 需要 `administration:write` 和 `pages:write`。本仓库的工作流令牌没有这些权限，因此 Pages 目前是关闭的（API 返回 404）。

请打开：

<https://github.com/bocai08081-ux/xiaobo-ai-news/settings/pages>

然后：

1. 找到 **Build and deployment**（构建和部署）。
2. **Source / 来源** 选择 **GitHub Actions**。
3. 保存。
4. 打开 [Actions → Deploy site](https://github.com/bocai08081-ux/xiaobo-ai-news/actions/workflows/pages.yml)，点 **Run workflow**。如果工作流还没出现在默认分支上，先把包含该工作流的变更合并进 `main`。

完成后站点会出现在 <https://bocai08081-ux.github.io/xiaobo-ai-news/> 。定时任务只会在默认分支上的工作流文件生效。

## 以后如果要加中文摘要

在仓库 Secrets 里可选添加：

| Secret | 作用 |
| --- | --- |
| `SUMMARY_API_KEY` | OpenAI 兼容接口的密钥。不设置就跳过摘要。 |
| `SUMMARY_API_BASE` | 默认 `https://api.openai.com/v1` |
| `SUMMARY_MODEL` | 默认 `gpt-4o-mini` |

工作流会把这三项传给 `scripts/fetch_news.py`。脚本请求 `POST {SUMMARY_API_BASE}/chat/completions`，请模型只返回 `[{"id","summaryZh"}]`，再写回每条的 `summaryZh`。接口失败时保留原文，构建继续。没有密钥时不会发任何请求。
