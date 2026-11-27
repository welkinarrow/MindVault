# Demo Day 演示操作手册（v2）

按顺序照着做。标【待填】的地方由负责人补上：云端助手（Hermes）是 C，种子文件名和记忆管理 agent 的命令是 B。
排练时发现哪一步要改，直接改这份文件，保持“照着做就能跑通”。

所有命令都在仓库根目录执行，终端先 `source .venv/bin/activate`。

## 0. 演示前一天

- [ ] 演示用的 Mac：【待填：谁的电脑】。`git pull`，切到 `v2`（或 `demo-base` 标签）。
- [ ] `.env` 里有 `LLM_API_KEY`、`LLM_MODEL`（外部写入的云端审核要用）。
- [ ] `python scripts/agents.py list` 能看到 5 个演示 agent：wang-cloud、wang-local、zhou-local、manager-local、manager-cloud，后两列是 local/cloud 和角色。
- [ ] 跑一遍单元测试和端到端测试：`python -m unittest discover -s tests`；按第 1 步启动后再跑 `python scripts/run_tests.py`，全部 PASS。
- [ ] 备用录屏已录好、能离线播放（第 4 节）。

## 1. 开场前 10 分钟

1. 关掉其他应用，只留 VS Code（终端）、浏览器和幻灯片。
2. 启动并预热本地模型：
   ```bash
   scripts/local_model_up.sh
   ```
   最后一行应显示 `mv-qwen3:latest … 24 hours from now`。
3. 准备场景一的数据（服务必须停着）：
   ```bash
   SEED_FILE=【待填：场景一种子，例如 data/seed_scenario1.json】 python scripts/reset.py
   ```
4. 启动服务（单独一个终端，演示全程开着）：
   ```bash
   ./run.sh
   ```
5. 可选：控制室网页 `python frontend/server.py`，浏览器打开 http://127.0.0.1:8100 ，给观众看服务日志和来源链。

## 2. 场景一：同权限，云端读不到内部

台词要点：小王有两个助手，权限一样（L1 · 研发），只有模型不同。

| 步骤 | 谁操作 | 命令 / 动作 | 应该看到 |
| --- | --- | --- | --- |
| 2.1 | C | 【待填：Hermes 云端助手（wang-cloud 密钥）提问“新员工第一周要做什么？”】 | 正常回答，引用 b-801 |
| 2.2 | C | 【待填：同一个云端助手提问“怎么部署到生产环境？”】 | 读 b-802 被拒：EXPOSURE_INTERNAL，提示改用本地助手 |
| 2.3 | A | `python scripts/local_agent.py --agent wang-local "怎么部署到生产环境？"` | 读到 b-802（标为内部），回答带 [b-802]，提交引用 |
| 2.4 | A | `python scripts/local_agent.py --agent wang-local "把部署步骤整理成一份速查表记下来"` | 写入返回 internal、pending，提示等待人工审批 |
| 2.5 | B | 【待填：记忆管理 agent 处理待审写入的命令】 | 给出建议：保存；不降级（内容来自内部手册） |
| 2.6 | 管理员 | `python scripts/review_cli.py`，选中速查表，批准 | 状态变为 verified，标签仍是内部 |
| 2.7 | C | 【待填：云端助手读速查表（编号见 2.4 的输出）】 | 仍然被拒：EXPOSURE_INTERNAL，“写成摘要也带不出去” |

本地模型不稳定时，2.3–2.4 改用：`python scripts/scripted_client.py scenario1`（它同时演云端和本地两边）。

## 3. 场景二：记忆追溯

1. 切换数据（先在服务终端按 Ctrl+C 停掉服务）：
   ```bash
   SEED_FILE=【待填：场景二种子，例如 data/seed_scenario2.json】 python scripts/reset.py
   ./run.sh
   ```
2. 另一个终端清掉小周助手的污点，保证来源链干净：
   ```bash
   python scripts/reset_taint.py zhou-local
   ```

| 步骤 | 谁操作 | 命令 / 动作 | 应该看到 |
| --- | --- | --- | --- |
| 3.1 | A | `python scripts/local_agent.py --agent zhou-local --trace "客户 B 为什么是 85 折？续约时能恢复原价吗？"` | 读 b-601、b-602、b-603，回答带出处，提交引用（打印引用编号），浏览器自动打开追溯视图 |
| 3.2 | A | 在追溯视图里鼠标依次停在 b-602、b-603、b-601 上 | 卡片、正文标记、引用行一起高亮；讲“三个人各知道一段” |
| 3.3 | A | `python scripts/local_agent.py --agent zhou-local "续约谈完了：采购量比上年增加 35%，按补充协议继续保持 85 折。请记下来。"` | 写入 b-610（实际编号以输出为准），pending，来源 b-601–b-603 |
| 3.4 | B | 【待填：记忆管理 agent 处理 b-610】 | 建议保存，不降级 |
| 3.5 | 管理员 | `python scripts/review_cli.py`，批准 b-610 | verified |
| 3.6 | A | `python scripts/trace_view.py --agent zhou-local --block <3.3 的编号>`，然后 `open runs/trace-<编号>-zhou-local.html` | 右栏是新记忆，审批人和时间，来源链 b-601、b-602、b-603 |

本地模型不稳定时，3.1 和 3.3 改用 `python scripts/scripted_client.py scenario2 --write`，再用 3.6 的命令打开追溯视图（`--answer` 用它打印的引用编号）。

**可选加演：** 云端视角的“已隐藏”。需要提前准备一条批准降级的样本（读过 b-802 但只用了 b-801 的写入），然后：
`python scripts/trace_view.py --agent wang-cloud --block <降级样本编号>` 和 `--agent wang-local` 各生成一份，并排打开。

## 4. 出问题时怎么办

| 情况 | 处理 |
| --- | --- |
| 本地模型卡住或答非所问 | Ctrl+C，改用 `scripts/scripted_client.py`，并说明“这是脚本化客户端，权限检查是真的” |
| 服务起不来 / 数据乱了 | Ctrl+C 停服务 → 重新 `reset.py` → `./run.sh`；污点和来源跨会话保存，必须 reset |
| 追溯视图取不到数据 | 用 mock：`python scripts/trace_view.py --answer tests/fixtures/trace/scenario2_answer.json --block tests/fixtures/trace/scenario2_b610.json` |
| 全部不行 | 退回 `demo-base` 标签；最后放备用录屏 |

## 5. 每次排练后

- [ ] 记下每一步实际耗时，超过 30 秒的步骤考虑换成脚本客户端。
- [ ] `runs/` 下的转录里有内部内容，不要发到群里或上传；要分享先看 `docs/hw9` 的脱敏做法。
- [ ] 改了命令或台词，同步改这份文件。
