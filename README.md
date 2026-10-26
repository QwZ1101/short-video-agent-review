# 短视频创作复盘工作台

面向粒子特效与制作过程讲解的本机复盘工具。当前案例目标是让观众**索要展示网页链接，或者点赞**；不要求观众下载源码或在本地运行。

程序用 Python 固定顺序编排材料检查、视频取帧、模型内容分析和证据验证，再由人工核对建议、记录修改与实际验证。它不是自主规划系统，模型意见不等于事实或流量预测。

## 已实现

- 本地样本登记、材料检查、FFmpeg 抽帧与字幕关联。
- DashScope 分析/验证两个模型角色，流式完整性及引用校验、调用预算、失败留档。
- 本机复核页面、草稿及版本历史、修改任务与人工验证记录。
- 目标变更提示，保留旧目标历史结果。
- 运行日志页：成功/失败流程、阶段耗时、事件与脱敏请求记录。

## 本机运行

需要 Python 3.9 或更新版本。核心程序仅使用标准库；视频处理还需要 PATH 中有 ffmpeg、ffprobe。Windows 可以直接运行项目的 `.cmd` 脚本。

```powershell
python create_sample.py --interactive
python launch_review.py
```

先登记真实样本并准备本地材料，再运行完整复盘。示例配置见 `data/examples/sample.example.json`，其中的素材路径是占位路径，不是附带的视频。

```powershell
$env:DASHSCOPE_API_KEY = "你的密钥"
$env:DASHSCOPE_MODEL = "你已开通的兼容图片输入模型名"
python run_workflow.py --mode live --sample data/samples/tutorial-001.json --max-model-calls 2
```

程序从环境变量读取密钥，不自动加载 `.env`。模型需要兼容本项目的图片输入与 JSON 输出格式；本机原案例使用 qwen3.8-flash，请按账号实际可用模型配置。完整运行会向配置的服务发送所选图片、字幕和配套内容。请只使用有权处理的材料。

`live` 最多尝试两个模型阶段；`replay` 复用已有结果而不调用 API；`verify` 复用分析再验证。没有历史数据的新克隆不能直接回放。恢复流程有独立前提与次数限制，详见 `resume_workflow.py --help`。

服务只监听 `127.0.0.1`，启动器自动选端口并复用同项目的健康服务。使用启动器实际输出的地址；不要用静态服务器替代 Python API。关闭浏览器后服务仍运行。个人样本未随仓库分发，新克隆先登记样本并运行流程，复核页才会有可选任务。

## 日志与数据边界

工作台顶部“运行日志”进入 `/logs`。请求日志在 `reports/review-server/`，流程记录在 `reports/workflows/`，详见 [日志说明](docs/logging.md)。请求日志有轮转与字段限制；历史工作流证据留档，不自动清理。

Git 忽略视频、截图、源码包、实际样本配置、评测记录、聊天笔记、环境配置和运行产物。仓库中的代码与通用示例可以备份和复现程序结构，不能替代本地真实数据的单独备份。

## 验证

```powershell
python -m unittest discover -s tests -q
node --check review-ui/dist/app.js
node --check review-ui/dist/tasks.js
node --check review-ui/dist/logs.js
```

Node 只用于可选的前端语法检查，工作台运行本身不依赖 Node。测试使用临时合成样本，不调用真实模型，不代表实际观众行为或模型语义准确率。

## 主要文件

| 文件 | 职责 |
| --- | --- |
| create_sample.py / check_materials.py | 登记样本与材料检查 |
| prepare_video.py | 视频元数据、抽帧、字幕证据 |
| review_tutorial.py / verify_tutorial.py | 模型分析、验证及结构检查 |
| run_workflow.py / resume_workflow.py | 调度、预算、恢复与运行留档 |
| human_review.py / improvement_tasks.py | 人工复核、修改和验证记录 |
| review_server.py / launch_review.py | 本机 API 与持久启动器 |
| workflow_logs.py | 限量日志读取、脱敏、请求轮转日志 |
| review-ui/dist/ | 无构建依赖的本机页面 |

源码包静态审计、自动剪辑、平台数据自动采集、自主规划、自动语义返修等不属于当前已完成能力。
