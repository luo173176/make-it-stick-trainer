# make-it-stick-trainer（认知天性学习策略训练器）

[![CI](https://github.com/luo173176/make-it-stick-trainer/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/luo173176/make-it-stick-trainer/actions/workflows/ci.yml)

一个把《认知天性：让学习轻而易举的心理学规律》（*Make It Stick*）里的学习规律**做成可执行流程**的训练器。
你只需要输入正在学的内容，它负责：**先逼你回忆，再打乱主题，再拉开复习间隔，最后让你写反思并校准掌握度。**

> 它故意做得"不友好"：不给你选择题，不让你先看到答案，不在你想跳过时劝你。
> 因为书中反复证明的结论是 —— **检索、间隔、交错带来的"费力感"正是长期记忆的成因，流畅感往往是错觉。**

---

## 1. 书中原则 → 功能映射

| 《认知天性》原则 | 在本项目里的实现 | 代码位置 |
| --- | --- | --- |
| 检索练习（先提取，再对照） | 复习时**只显示问题**，必须自己敲出答案后才展示参考答案 | `cli.py::_review_one` |
| 生成（用自己的话重述） | 只接受自由文本，**没有选择题、没有选项按钮**；空白按"没生成"处理 | `cli.py::_ask_multiline` |
| 间隔练习 | SM-2 简化算法按自评决定下次到期日（1 天 → 6 天 → `interval × ease`） | `scheduler.py` |
| 穿插 / 交错练习 | 队列按主题分组轮询抽取，**同一主题连续不超过 2 张** | `interleaver.py` |
| 多样化练习 | 支持多主题、多标签、多来源；薄弱主题加倍抽取 | `interleaver.py::topic_weakness` |
| 合意困难 | 不给"轻松模式"：跳过会让卡片继续留在到期队列，答错缩短间隔并记 lapse | `scheduler.py::difficulty_hint` |
| 细化与反思 | 每轮复习结束强制三问：核心概念 / 已有联系 / 下次改进 | `reflection.py` |
| 校准（判断自己知道什么） | 提交后才展示答案 + 关键词覆盖率，再让你自评 0-5，评分驱动调度 | `generator.py` + `cli.py` |
| 反馈 | 覆盖率给出"未覆盖关键词"清单，直接指出该补哪一块 | `generator.py::refinement_hint` |

---

## 2. 安装

要求 Python 3.11+。

```bash
git clone https://github.com/<your-name>/make-it-stick-trainer.git
cd make-it-stick-trainer
pip install -e .
```

不想安装也可以直接跑（源码目录内）：

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m mist --help      # Windows PowerShell: $env:PYTHONPATH="src"; python -m mist --help
```

只想要 Web 界面：

```bash
pip install -e ".[web]"
```

**Windows 乱码？** 传统 cmd/PowerShell 的代码页是 GBK。若在重定向或老终端里看到乱码，先执行 `chcp 65001`（或在 Windows Terminal 中运行）。程序会把管道输入按 UTF-8 解码，输出保持终端编码且不崩溃。

数据库默认在 `~/.mist/mist.db`，可用环境变量 `MIST_DB` 或每条命令的 `--db` 覆盖。

---

## 3. 快速开始

```bash
mist init

# 直接用示例数据跑一轮（示例卡片写的就是这本书的结论）
mist import --file examples/seed.json

# 手动添加自己的卡片
mist add --topic "检索练习" \
         --question "为什么自测比重读更有效？" \
         --answer "重读只产生流畅度错觉，自测才强化提取路径" \
         --tags "错觉,自测" --source "第2章" --difficulty 3

mist topics          # 各主题卡片数、平均 ease、薄弱指数
mist review          # 开始一轮交错复习
mist review --limit 20 --new-limit 5
mist stats           # 到期数、掌握度分布、7 天趋势、薄弱 Top5、连续天数
mist reflect --show  # 回看历史反思
mist export --file backup.json
mist import --file backup.json          # 完整备份可恢复到新库（含间隔与复习历史）
mist web --port 8000                    # 可选：单页 Web 界面
```

`mist review` 的一轮长这样：

```
╭──────────── 第 1/12 题 · 先回忆，不要先看答案 ─────────────╮
│ 为什么把材料反复朗读几遍，考试时反而想不起来？             │
│ 主题 检索练习｜难度 3｜已复习 0 次｜lapses 0｜状态：新卡片 │
╰────────────────────────────────────────────────────────────╯
用你自己的话重述答案（写关键词、因果链、例子都行，别抄原文）
> 重复阅读只让材料眼熟，提取路径没有被走过
╭──────────────── 参考答案（对照你自己的版本） ────────────────╮
│ 重复阅读只产生流畅度错觉：材料看着眼熟，但提取路径没有被强化。│
╰──────────────────────────────────────────────────────────────╯
关键词覆盖率 24%（内容字 14/46）· 覆盖不足｜未覆盖：重复阅读、只产生流畅度错觉、材料看着、被强化…
细化建议：先别重读资料，把漏掉的点（重复阅读、只产生流畅度错觉、材料看着…）自己再补一次，补不上的明天优先复习。
自评 [0-5] > 2
已记录：rating=2 → 间隔 1 天，ease 2.18，下次复习 明天（10-04 07:44 UTC）
想不起来很正常，失败的检索同样在强化记忆痕迹，别放弃。
```

复习结束后的反思三问（`reflection.py::REFLECTION_QUESTIONS`）：

1. 今天最核心的概念/结论是什么？（用自己的话，一句话）
2. 它和你已知的什么东西有联系？（旧知识、别的主题、真实经历）
3. 下次练习你会怎么改进？（具体一个动作）

---

## 4. 算法说明

### 4.1 间隔重复：SM-2 简化版（`scheduler.py`）

输入是 learner 的 0-5 自评：

```
rating < 3  → repetitions = 0, interval = 1, lapses += 1        # 忘了就重回短间隔，并标记失败
rating >= 3 → repetitions == 0 : interval = 1
              repetitions == 1 : interval = 6
              repetitions  > 1 : interval = round(interval * ease)
              repetitions += 1
ease = max(1.3, ease + (0.1 - (5 - rating) * (0.08 + (5 - rating) * 0.02)))
due_date = now + interval 天
```

新卡片初始 `ease=2.5, interval=0, repetitions=0, due_date=now`（**当天就能练**，因为生成比等待更重要）。
间隔用**更新前**的 ease 放大，然后再更新 ease。

例（全部 4-5 分）：

| 第 n 次 | rating | 新间隔 | ease | 备注 |
| --- | --- | --- | --- | --- |
| 1 | 5 | 1 天 | 2.60 | 首次成功 |
| 2 | 5 | 6 天 | 2.70 | 第二次成功固定跳到 6 天 |
| 3 | 4 | 16 天 | 2.70 | `round(6 × 2.7)` |
| 4 | 1 | 1 天 | 2.36 | 答错：重置 + 记一次 lapse |

`rating < 3` 意味着这张卡进入"薄弱"信号，会被交错层加权抽到。

### 4.2 交错队列（`interleaver.py::build_review_queue`）

1. **到期池**：`last_reviewed_at` 非空且 `due_date <= now` 的卡片（含逾期）。
2. **新卡池**：从未复习过且到期的卡片，默认最多取 `new_limit=10` 张。
3. **薄弱主题**：`weakness = 0.45×低ease + 0.25×lapse密度 + 0.30×近期低评分`（0–1，无复习记录的主题记 0）。
4. 按主题分组 → **组内打乱** → **轮询抽取**；薄弱主题每轮抽 2 张，其余抽 1 张。
5. 重排保证**同一主题连续不超过 2 张**（只剩一个主题时无法避免，则按原顺序放行）。
6. 到期池排在前面（到期优先），再接新卡，最后按 `--limit` 截断。

想复现同一队列：`mist review --seed 7`。

### 4.3 生成性评分：内容覆盖率（`generator.py`）

**评分单元**：拉丁文本按单词切（有真实词边界），中文按**内容字**切（去掉虚词字，如 `的了是在过把…`）。
中文没有词边界，用 2 字滑窗当词曾是第一版实现，实测后被否决：它要求字符**严格相邻**，换词正确的复述会被压到极低分，而"以为自己没掌握"和"以为自己掌握了"同样是校准失败。

**最终分数是两个视图的均值**：

```
ratio = ( 内容字覆盖率 + 相邻字对顺序覆盖率 ) / 2
```

- 内容字覆盖率：宽松地衡量"这个概念有没有被提到"；
- 顺序覆盖率：参考答案里相邻的内容字对（按虚词切段，不跨标点）是否原样出现，用来防止"把原文打乱字序抄一遍"蒙混过关。

实测同一张卡（参考答案：`问答题要求从记忆里生成答案，检索路径被真正走一遍；选择题只需再认。`）：

| 用户答案 | 覆盖率 | 判定 |
| --- | --- | --- |
| 逐字复制参考答案 | 100% | 覆盖良好 |
| 换词但内容正确的复述 | 53% | 基本覆盖 |
| 把上面那段复述**倒序**（字都在，顺序全乱） | 34% | 覆盖不足 |
| 完全跑题的另一段本书结论 | 4% | 覆盖不足 |
| 空白 | 0% | 覆盖不足（并提示"没有生成任何答案"） |

**未覆盖清单**会把漏掉的内容字还原成参考答案里的连续片段（左右各补一个字上下文，绝不跨标点），所以显示的是 `记忆的可用度取决于检索练习的次数` 而不是 `记、忆、的`。片段仍可能从词中间断开（`但提取路径没`），它是**指路用的近似切片，不是术语清单**。

阈值：`<35%` 覆盖不足、`<70%` 基本覆盖、其余覆盖良好。

**它不评分理解度**：堆砌关键词可能得高分，讲得对但用词不同可能得低分。所以最终驱动调度的永远是你随后给出的 0-5 自评 —— 覆盖率只负责告诉你漏了哪一块。

预留语义评分接口：`evaluate_with_llm(question, answer, user_answer)`，默认返回 `None`、不发任何网络请求；想接自己的模型时替换函数体并设 `MIST_LLM_EVAL=1`。

---

## 5. 数据模型（SQLite / SQLAlchemy 2.0）

| 表 | 字段 | 用途 |
| --- | --- | --- |
| `topics` | `id, name(unique), description, created_at` | 交错练习的分组单位 |
| `cards` | `id, topic_id, question, answer, source, tags, difficulty, ease, interval, repetitions, due_date, last_reviewed_at, lapses, created_at` | 记忆对象 + SM-2 状态 |
| `reviews` | `id, card_id, rating, user_answer, feedback, reviewed_at, elapsed_days, scheduled_days, session_id` | 生成性答案原文与自评历史（`session_id` 用于把复习和反思串起来） |
| `reflections` | `id, session_id, core_concept, connection, next_improvement, created_at` | 每轮复习的反思小结 |

所有时间戳为 **naive UTC**。`user_answer` 原文入库 —— 回看自己三个月前怎么解释一个概念，是最有效的校准手段之一。

导入格式（`examples/seed.json`）：

```json
{
  "topics": [{ "name": "检索练习", "description": "..." }],
  "cards": [
    { "topic": "检索练习", "question": "...", "answer": "...",
      "tags": "错觉,自测", "source": "第2章", "difficulty": 3 }
  ]
}
```

`mist export` 写出的完整备份（含 `ease/interval/repetitions/lapses/due_date`、`reviews`、`reflections`）可以直接被 `mist import` 还原；只含 `question/answer` 的种子文件按新卡导入，重复问题会被跳过。

---

## 6. CLI 命令

| 命令 | 说明 |
| --- | --- |
| `mist init` | 建库建表（可重复执行） |
| `mist add --topic T --question Q --answer A [--tags a,b] [--source S] [--difficulty 1-5] [--force]` | 添加卡片 |
| `mist review [--limit 20] [--new-limit 10] [--seed N] [--no-reflect]` | 交错复习一轮 |
| `mist stats [--days 7]` | 到期/掌握度/趋势/薄弱 Top5/连续天数 |
| `mist topics` | 主题列表及薄弱指数 |
| `mist export --file backup.json` | 导出全量 JSON |
| `mist import --file seed.json` | 导入种子或备份 |
| `mist reflect [--session-id S] [--show N]` | 单独写反思 / 回看反思 |
| `mist web [--host 127.0.0.1] [--port 8000]` | 单页 Web 界面（需 `.[web]`） |

每条命令都接受 `--db PATH`。

---

## 7. Web 界面（可选）

`mist web` 起 FastAPI + 一个 `src/mist/templates/index.html`（原生 fetch，无构建工具）：

- `/` 三块：复习（先写答案 → 看参考答案与覆盖率 → 点 0-5 自评）、统计、添加卡片；
- `/api/queue`、`/api/check`、`/api/grade`、`/api/reflect`、`/api/stats`、`/api/topics`、`/api/cards`。

Web 与 CLI 共用同一个 SQLite 文件和同一套调度代码，因此两边进度互通；**CLI 才是功能完整的参考实现**。

---

## 8. 测试

```bash
pip install -e ".[dev]"
pytest                 # 74 个用例
pytest -q tests/test_scheduler.py tests/test_interleaver.py
```

覆盖面：SM-2 的重置/阶梯增长/ease 下限 1.3、交错队列的主题连续数与到期优先与新卡上限、内容覆盖率与打乱字序的反作弊、CLI 端到端（`init/add/import/stats/export/import/review/reflect`，含用管道输入驱动的交互复习），以及 Web 的 HTTP 契约（请求体绑定、复习写库、404/422 分支）。

---

## 9. 目录结构

```
make-it-stick-trainer/
├── src/mist/
│   ├── cli.py           # argparse 命令与复习交互流程
│   ├── db.py            # 引擎/会话/默认路径
│   ├── models.py        # topics/cards/reviews/reflections
│   ├── scheduler.py     # SM-2 简化算法与掌握度分档
│   ├── interleaver.py   # 交错队列、薄弱主题加权
│   ├── generator.py     # 生成性答案的关键词覆盖率
│   ├── reflection.py    # 反思三问与入库
│   ├── stats.py         # 统计计算与 Rich 渲染
│   ├── web.py           # 可选 FastAPI
│   └── templates/index.html
├── tests/               # pytest
├── examples/seed.json   # 12 张示例卡片（内容就是本书结论）
├── pyproject.toml       # mist 入口、依赖、可选 [web]/[dev]
└── requirements.txt
```

---

## 10. 怎么用才不浪费这个项目

1. **别攒卡片。** 一天加 3 张、每天练完到期，胜过一次性导入 200 张然后弃用。
2. **自评往下压一档。** 想给 5 的时候通常只有 4 —— 高估掌握度是书里说的典型元认知失败。
3. **答错不要马上看答案**，先再逼自己一次；失败的检索也在强化痕迹。
4. **写"联系"那一句时别省。** 细化产生的个人线索，是几个月后还能想起来的原因。
5. 看到"逾期 N 张"就当天清掉，间隔一旦拉长，重建线索的成本比维持高得多。

## 11. 已知限制

- 中文没有分词器，覆盖率基于**内容字 + 相邻字对**两个视图；"未覆盖"清单是从参考答案切出的近似片段，可能从词中间断开（`但提取路径没`）。把参考答案写短、用 `--tags` 标出术语能改善。
- 无图片/公式支持，无 reminders、无云端同步；单文件 SQLite，多设备请自己同步文件或走 `export/import`。
- 并发写入依赖 SQLite 默认行为，不适合多人共享库（Web 版仅供单人本机使用，默认绑定 `127.0.0.1`）。

## 12. 如何贡献

1. Fork 仓库，建分支：`git checkout -b feature/short-name`
2. 安装开发环境：`pip install -e ".[dev]"`
3. 改动算法时**先加测试**（`tests/test_scheduler.py`、`tests/test_interleaver.py` 是分层的边界用例）
4. `pytest` 必须全绿；文档里受影响的地方一并更新
5. 提 PR，说明它对应《认知天性》里的哪条规律、以及为什么这样映射

欢迎的方向：中文分词改用词典/`jieba`、`evaluate_with_llm` 的真实实现、卡片富文本/图片、更多调度算法（FSRS）、导出 Anki/CSV、i18n。
不接受的方向：把强制回忆改成"可跳过"、增加选择题模式、默认展示答案 —— 这些会拆掉项目唯一的存在理由。

## 13. 相关

- 书中研究基础：Robert & Bjork 的 *desired difficulties*、Pashler 等人的 *spacing*、Rohrer & Taylor 的 *interleaving*、Roediger & Karpicke 的 *testing effect*。
- 算法参照：Anki 使用的 SM-2 变体（本项目按其原始表述做了简化）。

## 14. License

MIT —— 见 [LICENSE](LICENSE)。示例卡片文字是为演示而写的概括表述，观点出自《认知天性》（Peter C. Brown / Henry L. Roediger III / Mark A. McDaniel），书籍原文版权归原作者与出版社所有。
