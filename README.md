# 三科学习资料入库后端 V2

该项目保存数学、408、英语共享的资料预处理与入库衔接实现，包含 Capture 处理、工作队列、产物绑定、合同校验、学科适配和只读管理看板。快速保存、模型分析、正式学习库写入各自有独立的状态和责任边界。

## 目录与入口

- `bin/`：工作进程入口。
- `lib/`：队列、状态、执行与校验逻辑。
- `schemas/`：跨阶段数据合同。
- `dashboard/`：只读状态展示。
- `scripts/`：构建、校验和验收工具。
- `config.example.json`：环境配置模板。
- `tests/`：现有测试。

```sh
python3 scripts/release_manager.py --help
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```

构建和接入需要配置自己的三科学习库、运行目录和环境，详见 [技术文档](README.technical.md)。不要直接套用文档中的历史生产路径或部署结论。

## 当前用途与边界

这是已有工程源码的公开化整理，不是新的部署。本项目保留历史实现与分支；[CURRENT_STATUS.md](CURRENT_STATUS.md) 中的验收日期和未完成事项属于对应版本。当前前台学习流程以各学科公开源码说明为准。

仓库不包含真实学习附件、对话、运行凭据或完整私库。源码中用于版本绑定的历史 ID 与散列不是可访问原始资料的凭据。
