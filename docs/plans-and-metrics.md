# 任务计划与平台数据

新 live 流程在调用模型前生成 `plan.json`，保存当前目标、目标版本、角色、工具、依赖与动作理由。`workflow.json` 中也保存计划供日志页展示。执行由依赖就绪调度器驱动；默认视频取证和指标计算可并行，分析等待两个分支完成，验证等待分析完成。规则选择数据分支和优先级，不宣称模型自主规划。

只看计划、不调用模型：

```powershell
python workflow_plan.py --sample data/samples/tutorial-002.json
```

输出在 `reports/plans/`，这只是准备记录，不算流程已经完成。live 分析与验证需要两个阶段预算，不足时在运行工具前停止。缺少数据时，仅跳过 metrics 阶段；失败造成的跳过不能解除后续依赖。

## 录入规则

参考 `data/examples/metrics.example.json`，把其结构填入样本的 `platform_metrics` 字段；同一对象必须对应同一作品、同一观察窗口。示例数值是合成演示数据，不能用于真实作品。

- `source`：数据来源说明，不填写密码、账号凭据或评论者个人资料。
- `observation_hours`：从发布到此次统计经过的小时数，必须为正数。
- `plays` 与 `plays_definition`：累计播放次数及其口径。
- `likes` 与 `likes_definition`：同一窗口的点赞数及口径。
- `link_requests` 与 `link_requests_definition`：同一窗口的索要链接数量，注明评论/私信范围及按人还是按条计数、是否去重。

未知计数填 `null`，不要填 0。至少有一项行为计数及其口径；未提供整组数据时 `platform_metrics` 为 `null`。目前只支持单窗口汇总，未知字段、负数、非整数计数及缺少口径会拒绝。

计算工具分别输出 `likes / plays * 100` 和 `link_requests / plays * 100`，保留原始数值及公式。分母为零输出空值。指标名称是“每百次播放的行为数”，不等于去重观众转化率，不把两项相加为人数，不计算综合评分。

独立计算示例：

```powershell
python platform_metrics.py --input data/examples/metrics.example.json --output reports/metrics-example.json
```

新 live 流程把结果保存在 `metrics.json`，模型证据目录使用 `metrics-calculated` 引用同一确定性公式重新计算的结果。原始数据真实性仍需人工确认。缺少数据时会生成跳过记录，不提供虚构指标。历史 replay/verify 不添加新数据，也不改写旧证据。
