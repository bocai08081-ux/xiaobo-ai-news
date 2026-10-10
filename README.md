# 小波AI资讯站

面向中文 AI 社区的每日简报。页面是简体中文，时间一律用北京时间（Asia/Shanghai，UTC+8）。数据由 GitHub Actions 抓取后部署到 GitHub Pages。

站点地址：<https://bocai08081-ux.github.io/xiaobo-ai-news/>

## 页面上有什么

顶栏三个栏目：

- **AI 资讯**：今天的简报。一条头条卡片，下面是两列卡片。可以按「全部 / AI 模型 / 图像与视频 / Mac / 英伟达 / AMD」筛选，也可以翻往期。每张卡片有分类、北京时间日期（对不上就写「日期待核实」）、中文标题、发生了什么、对你的影响、可展开的适用说明，以及底部的来源链接。
- **显卡行情**：国内 / 海外，品牌（英伟达 / AMD / 国产显卡），渠道（京东自营、京东第三方、淘宝、天猫；海外是 Amazon、Newegg、官网）。没有核到的标价显示「待采集」，不编价格。
- **人物动态**：Tibo、马斯克、特朗普。只收当天素材里已经提到他们的公开报道。没有就留空，不编原帖。

简报会挑重要的几条，不把抓到的链接全部铺上去。中文来源优先。英文来源在配置解读接口后会写成中文结论；没配密钥时仍会生成中文卡片，标题里可能保留产品名。

草稿 PR #2 里的中文来源和解析修正已经接进来：雷峰网、爱范儿、极客公园、36氪、钛媒体、InfoQ、Solidot，以及 Simon Willison（跳过 Quoting 摘录）。综合科技订阅只保留标题像 AI 的条目，避免把 Wayfair 这类词里的 “ai” 算进来。原来的链接列表和选题台界面换成了现在的三栏简报。

## 时间

feed 里的 UTC 或其他时区会先解析，再换算成北京时间。归档文件名和「往期」都按北京时间的自然日，所以 UTC 晚上 16:30 会计入北京时间的第二天。

GitHub Actions 的运行器是 UTC。工作流设置了 `TZ=Asia/Shanghai`，脚本本身用 `zoneinfo` 做转换，不依赖机器本地时间。

## 数据

| 文件 | 内容 |
| --- | --- |
| `site/data/news.json` | 抓取原文，页面不直接拿它当列表 |
| `site/data/briefings/YYYY-MM-DD.json` | 某一北京时间日期的卡片 |
| `site/data/briefings/index.json` | 往期目录 |
| `site/data/gpus.json` | 显卡型号和已经核过的报价 |
| `site/data/people.json` | 人物动态 |

卡片字段：`title`、`summary`、`takeaway`、`applicability`、`sourceName`、`sourceUrl`，外加分类和北京时间标签。

已经写好的往期文件不会在下次构建时被覆盖。显卡报价和标了 `pinned` 的人物动态会保留。

## 本地预览

```bash
python3 scripts/fetch_news.py
python3 -m http.server 4173 --directory site
```

打开 <http://127.0.0.1:4173> 。只想用仓库里已有的 `news.json` 重算页面、不访问网络：

```bash
python3 scripts/fetch_news.py --from-json site/data/news.json
```

测试：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

抓取和生成只用 Python 标准库。

## 自动更新

工作流：[.github/workflows/pages.yml](.github/workflows/pages.yml)

- 北京时间 08:30、14:30、20:30（UTC cron `30 0,6,12 * * *`，GitHub 可能会晚几分钟）
- 手动 `workflow_dispatch`
- 推送到 `main` 时也会构建一次

流程：抓取并写出简报 → 把 `site/data/briefings`、`gpus.json`、`people.json` 提交回仓库（这样往期还在）→ 部署 GitHub Pages。用 `GITHUB_TOKEN` 推送不会再次触发这个工作流。提交失败不会挡住当次部署。

`news.json` 仍是构建产物，不提交回 git。

## 需要所有者配置的密钥

完整的中文解读需要一个 OpenAI 兼容接口。在仓库 Settings → Secrets and variables → Actions 里添加：

| Secret | 是否必须 | 作用 |
| --- | --- | --- |
| `SUMMARY_API_KEY` | 要完整解读时必须加 | 调用聊天接口的密钥。不设置时站点照常构建，卡片用规则写成中文 |
| `SUMMARY_API_BASE` | 可选 | 默认 `https://api.openai.com/v1` |
| `SUMMARY_MODEL` | 可选 | 默认 `gpt-4o-mini` |

工作流把这三项传给 `scripts/fetch_news.py`。有密钥时，脚本请求 `POST {SUMMARY_API_BASE}/chat/completions`，请模型只返回简报 JSON。接口失败、超时或返回的不是中文时，改用规则摘编，构建继续。没有 `SUMMARY_API_KEY` 时不会发任何请求。

## 需要所有者做的一次性 Pages 设置

`actions/configure-pages` 的 `enablement: true` 不能用默认的 `GITHUB_TOKEN` 完成。请打开：

<https://github.com/bocai08081-ux/xiaobo-ai-news/settings/pages>

1. 找到 **Build and deployment**（构建和部署）。
2. **Source / 来源** 选择 **GitHub Actions**。
3. 保存。
4. 打开 [Actions → Deploy site](https://github.com/bocai08081-ux/xiaobo-ai-news/actions/workflows/pages.yml)，点 **Run workflow**。

定时任务只会在默认分支上的工作流文件生效。
