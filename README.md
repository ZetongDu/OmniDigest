# OmniDigest

**OmniDigest** 是一个面向多领域信息聚合、摘要生成及自动分发的流程驱动系统。设计理念借鉴 Shadow Threads：**每个任务都是独立节点，状态可追踪、可复现、可审计**。

---

## 核心概念

- **任务节点（Task Node）**  
  每个领域的抓取、摘要、输出和邮件发送都是独立节点，状态可复现。  

- **领域协议（Domain Protocol）**  
  每个领域 YAML 文件定义抓取源、摘要规则、邮件模板和分发策略。  

- **流水线（Pipeline）**  
  `digest_core.py` 是执行核心，按任务节点顺序执行：抓取 → 摘要 → 写入 → 分发。  

- **调度协议（Schedule Protocol）**  
  APScheduler + 文件锁组合保证跨领域任务错峰执行与单实例运行。

---

## 项目结构

```

src/omnidigest/
├─ config/
│  └─ settings.py          # 全局配置、环境变量、领域加载
├─ delivery/
│  ├─ schedule_worker.py   # 定时调度、错峰执行、并发控制
│  └─ emailer.py           # 可审计邮件发送接口
├─ domains/
│  └─ ai.yaml              # 示例领域协议
├─ pipeline/
│  └─ digest_core.py       # 任务流水线核心
└─ logs/                   # 日志输出

````

---

## 安装与依赖

```bash
git clone <repo_url>
cd omnidigest
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
````

---

## 环境配置示例 (.env)

```env
APP_NAME=OmniDigest
APP_ENV=production
TIMEZONE=Asia/Shanghai

OPENAI_API_KEY=sk-xxxx
ANTHROPIC_API_KEY=xxxx
GOOGLE_API_KEY=xxxx

EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=xxxx
EMAIL_FROM=omnidigest@example.com
EMAIL_TEST_TO=test@example.com
EMAIL_REPLY_TO=noreply@example.com

DATABASE_URL=sqlite:///./omnidigest.db
ENABLE_ANALYSIS=true
ENABLE_HTML_EMAIL=true
DOMAINS=ai,finance
```

---

## 使用说明

### 1. 执行单次任务（Task Node）

```bash
python -m src.omnidigest.pipeline.digest_core run --domain ai
```

### 2. 启动定时调度（Schedule Protocol）

```bash
python -m src.omnidigest.delivery.schedule_worker
```

* 默认每天 07:00 开始
* 每个领域按 `STAGGER_MINUTES=5` 错峰
* 文件锁防止多实例重入

### 3. 配置新领域（Domain Protocol）

1. 创建 `src/omnidigest/domains/{new_domain}.yaml`
2. 定义抓取源、摘要规则、邮件模板
3. 更新 `.env` 的 DOMAINS 列表

---

## 邮件模板示例（Pipeline Output）

```jinja
{{ domain_name }} - {{ generated_at.strftime('%Y-%m-%d') }}

{{ highlights }}

{% for summary in summaries %}
## {{ summary.article.title }}
{{ summary.summary }}

[Read more]({{ summary.article.link }})

{% endfor %}

{% if insights %}
### Impact Insights
{% for insight in insights %}- {{ insight }}
{% endfor %}
{% endif %}
```

---

## 日志与审计

* 日志默认输出到 `logs/`
* 每次任务记录开始/结束时间
* 异常与发送结果完整记录，支持追踪

---

## 扩展指南

* 新增领域仅需添加 YAML 配置文件
* 支持多种 LLM 提供商（OpenAI / Anthropic / Google）
* 邮件发送可选 HTML / PlainText，支持 CC/BCC 和 Reply-To
* 核心任务流水线完全可复用，无需修改主代码
