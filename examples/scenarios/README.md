# 六组温控对比配置

每份 JSON 都包含完整中文注释，可直接修改。默认开启 LLM，读取项目根目录的 `config.json` 中的 `LLM_API_KEY`；环境变量 `LLM_API_KEY` 优先。没有在配置中保存真实密钥。设备模式固定为 test，不连接现场。

| 配置 | 温度任务 | 对象/扰动 | 仿真时长 |
|---|---|---|---:|
| 01_heat_30_to_60.json | 30→60°C | K=1，τ=120s，θ=10s | 600s |
| 02_heat_30_to_80.json | 30→80°C | 同上 | 600s |
| 03_heat_30_to_100.json | 30→100°C | 同上 | 600s |
| 04_heat_30_to_110.json | 30→110°C | 同上，接近输出上限对应的稳态温度 | 600s |
| 05_slow_delay_30_to_100.json | 30→100°C | K=1，τ=240s，θ=40s | 1200s |
| 06_disturbance_30_to_100.json | 30→100°C | τ=120s，θ=10s；第300秒起持续降温0.03°C/s | 900s |

每个场景内统一门槛：最高温度不超过目标+2°C、超调≤5%、末段平均绝对温差≤0.3°C；调节带为目标±1°C。不同场景的原始 IAE 不应直接比较，应该比较各自场景内三组结果。

在项目根目录运行全部配置：

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py
```

仅运行其中一份：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/scenarios/01_heat_30_to_60.json
```

需要先做不调用 API 的传统算法对比时：

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --llm off
```

六组完整运行最多产生 6×3×4=72 轮参数建议请求，失败重试和传输回退可能增加网络请求数。没有预设或保证哪个算法获胜；LLM 不可用时保留已有最好结果，并在历史中记录失败。

每次批次汇总保存在 `results/comparison_suite/<时间戳>/`：

- `index.html`：六组数据总览，点击链接打开各场景曲线和 LLM 历史。
- `comparison.md`：达标情况、提升幅度和对比限制。
- `comparison.csv`：Excel 可打开的数据，包含达标原因、最终参数、初始 IAE、LLM 有效建议数和参数是否变化。
- `suite.json`：机器可读的汇总，包括执行失败记录。

各场景完整结果单独保存在 `results/scenarios/<场景名>/<时间戳>/`。旧结果不覆盖。

`original_zn` 是原辨识算法＋Z-N PID 在当前统一控制器和 LLM 流程中的基线，不是完整原项目独立运行的对照。该批次用于检验整定流程的场景适应性，不代表现场化工过程验证，也不预先证明全面优于原项目。
