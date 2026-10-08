# Thermal PID Workbench

可配置的温控 PID 辨识、整定、仿真评价与设备接入工具。基于 [KINGSTON-115/llm-pid-tuner](https://github.com/KINGSTON-115/llm-pid-tuner) 扩展，保留 Apache-2.0 许可和原作者归属。

中文 | [English](README.en.md)

**一个 `project.json` 配置对象模型/历史数据、温度任务、执行器、控制器和评价要求。** 提供 Z-N PID、Z-N PI、SIMC PI 及可选 LLM 参数建议，所有建议先通过 `pid_safety.py`，再进入完整任务仿真。默认任务为 30°C 升到 100°C，默认不调用 LLM。

## 快速开始

需要 Python 3.10 或以上。下载源码、进入项目目录，基础离线功能无需第三方依赖：

```powershell
python pid_project.py
```

打开 `results/project/report.html` 看温度曲线和指标；打开 `results/project/pid.json` 看最终建议参数。没有达标参数时参数为 `null`，报告会列出原因。

## 示例结果参考

仓库的 [`result/`](result/) 文件夹给出了一个 **30°C → 100°C** 的默认离线测试结果，供使用者在运行前参考：

- [`result.html`](result/result.html)：示例结果报告，包含温度响应、控制输出、Z-N/SIMC 候选及性能指标。
- [`result-toread.md`](result/result-toread.md)：中文结果解释，逐步说明三组对照、指标含义、达标原因和最终 PID 如何使用。
- [`pid.json`](result/pid.json)、[`summary.json`](result/summary.json) 和 CSV/SVG：最终参数、完整记录及可进一步分析的数据和图形。

建议先阅读结果解释，再下载或克隆仓库，用浏览器打开 `result/result.html`。GitHub 文件页面展示 HTML 源码，不直接展示报告界面。

该示例未启用 LLM、未连接设备，选出的合格方案是 Z-N PI；这些是配置模型上的仿真结果。`result/` 保存参考快照，自己运行后的新结果仍位于 `results/project/`。

安装为命令行工具，或生成自己的配置：

```powershell
python -m pip install -e .
thermal-pid --init my_project.json
thermal-pid --config my_project.json --validate
thermal-pid --config my_project.json
```

要改变任务，编辑 `project.json` 的 `task`；换对象时编辑 `model` 或填写 `history.file`。每项已有中文注释和默认值，程序直接支持这些注释；省略字段使用默认值，未知字段会报错；文件路径相对于配置所在目录。

完整说明：[配置怎么填](docs/CONFIGURATION.md) · [算法与结果怎么看](docs/METHODS.md) · [设备接入协议](docs/DEVICE_PROTOCOL.md)

## 两种模式

| 模式 | 行为 |
|---|---|
| `test` | 辨识、整定、可选 LLM、离线仿真、输出参数和报告，供使用者审阅和手动采用 |
| `use` | 完成离线验证后，读取设备、条件写入合格参数、独立读回并有界监测 |

使用模式支持模拟、TCP JSONL、串口 JSONL 和自定义适配器，需要显式开启 `device.write_enabled`。连续闭环控制仍由设备执行。项目已实现接入流程；随包测试覆盖软件、模拟设备和本机 TCP，**没有完成任何真实化工装置的投产验证**。现场设备的协议、单位、PID 形式及状态初始化仍须适配；DCS/PLC/SIS 等既有保护继续独立负责过程安全。

```powershell
python pid_project.py --config examples/use_simulated.json
```

这条命令操作内存中的模拟设备；写入/读回/监测记录在 `results/use_simulated/device_audit.json`。

## 输出和对比

| 文件 | 内容 |
|---|---|
| `report.html` | 三组响应曲线、指标、候选参数和达标状态 |
| `pid.json` | 合格 PID、形式及单位，无合格结果时为 null |
| `summary.json` | 实际配置、时间戳/哈希、辨识、原始/应用 PID、护栏与回滚记录 |
| `metrics.csv`、各组 CSV | 指标和响应数据 |
| `device_audit.json` | 使用模式的设备写入与读回审计 |

`original_zn` 是原辨识函数 + Z-N PID，`corrected_zn` 是修正辨识/已知模型 + Z-N PID，`selected` 是在 Z-N PID/PI 与 SIMC PI 中按评价要求选择。启用 LLM 后三组分别继续调优。该对照隔离辨识和初始化的改动，不包含完整原版工程的其他实现差异。

先看全部条件是否达标，再比较 IAE（累计温差）、末段温差、调节时间和输出变化总量。自动选择不保证每次选择 SIMC；单次仿真的优胜也不能代表所有化工场景。

## 数据、模型和 LLM

```powershell
python pid_project.py --config examples/history.json
python pid_project.py --config examples/heating.json
python pid_project.py --config examples/cooling.json
python pid_project.py --config examples/custom_model.json
```

历史 CSV 独立保存，支持自定义列名和秒/毫秒，需要包含基线及稳定末段的单次输入阶跃。示例 CSV 是合成数据。自定义模型通过 `module:function` 工厂接入。

启用 LLM 时安装依赖，把 API key 放入 `LLM_API_KEY` 环境变量或本地 `config.json` 的 `LLM_API_KEY` 字段：

```powershell
python -m pip install -e ".[llm]"
python pid_project.py --config examples/deepseek.json
```

供应商、模型、地址和轮数都在配置中。LLM 接收模型、评价条件和仿真摘要，仅负责建议；程序独立验证、保留最佳并回滚。密钥不写入公开配置，Git 与发布包排除本地凭据。

## 范围、开发和归属

重点是可由局部稳定单输入单输出模型描述的温控回路。支持输出单位/限幅/速率、采样时间、微分滤波、条件积分抗饱和、负增益降温、并联式/理想式 PID 与秒/分钟转换。强耦合、积分、不稳定或强非线性对象需要扩展模型及算法。IMC 仅作 SIMC 的理论背景，没有独立实现。

算法见 `system_id.py`；配置、模型、评价、LLM 工作流与设备接口在 `thermal_pid/`。遗留 `simulator.py`、`tuning_pipeline.py`、MATLAB/硬件工具继续使用其原配置。**本 README 的统一配置和两个模式指 `pid_project.py` / `thermal-pid`。** [原 README 快照](docs/UPSTREAM_README.md)用于查看上游功能。

```powershell
python -m pip install -e ".[dev,llm,serial,legacy]"
python -m pytest tests -q
python -m build
```

GitHub Actions 配置包含 Windows/Linux、Python 3.10/3.12；远端是否通过以实际执行记录为准。测试不调用外部 LLM 或真实装置。

保留 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和上游归属。参见 [CHANGELOG](CHANGELOG.md)、[CONTRIBUTING](CONTRIBUTING.md)。配置模板可以公开；生产数据、凭据、日志和生成结果默认不进入 Git。
