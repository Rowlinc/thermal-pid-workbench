# 完整新旧路线比较场景

这些配置是公开的合成测试，不含现场工艺数据或密钥。未列字段采用程序默认值；不会读取你根目录 project.json 的私有配置。

| 文件 | 对象和任务 | 评价重点 | 设置要点 |
|---|---|---|---|
| 01 | 一阶温控，30→100℃ | 达标后精度优先 | K=1，τ=120s，θ=10s，600s任务 |
| 02 | 长延迟温控，30→80℃ | 平稳优先 | τ=240s，θ=60s，1800s任务 |
| 03 | 慢执行器温控，30→80℃ | 速度优先 | 输出变化≤1%/s，1000s任务 |
| 04 | 温控，30→80℃，500s后持续散热扰动 | 精度优先 | 扰动−0.03℃/s，1200s任务 |
| 05 | 压力，2→5bar | 平稳优先 | K=0.1bar/%，τ=12s，θ=2s，周期0.2s |
| 06 | 流量，20→60m³/h | 精度优先 | τ=5s，θ=1s，测量噪声标准差0.15，固定seed |
| 07 | 小温差温控，35→40℃ | 平稳优先 | 初始输出15%，允许超调0.25℃，积分从零开始 |
| 08 | 两节点加热传热模型，30→80℃ | 速度优先 | 离线阶跃辨识，使用非FOPDT真实仿真模型 |
| 09 | CSV辨识温控，30→80℃ | 精度优先 | CSV真实生成参数K=1、τ=180s、θ=35s，显式秒单位 |
| 10 | 与01相同温控任务，思考模式复验 | 精度优先 | thinking=enabled，回复上限65536，最多4轮 |

运行前，在项目根 config.json 配置 API 密钥。各配置 llm.credentials_file 指向 ../../config.json；synthetic_step.csv 是独立的合成阶跃历史数据。01至09的 LLM 使用 deepseek-flash、非思考模式、4096回复token上限；10使用思考模式与65536上限，每路线最多4轮；五条路线使用同一LLM配置（LLM关闭时不运行混合路线）。非思考模式用于固定API条件，LLM输出仍有随机性，并不代表你的私人项目设置被改动。

在项目根 PowerShell 中运行：

```powershell
# 尚未安装时创建环境并安装LLM依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[llm]" --index-url https://pypi.org/simple

# 仅公式及仿真；不会把旧版初始化称为完整旧版LLM调优
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --scenario-set route_benchmarks --llm off

# 十种场景的完整真实LLM对照
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --scenario-set route_benchmarks --llm on

# 只运行某一个场景
.\.venv\Scripts\python.exe pid_project.py --config examples/route_benchmarks/05_pressure.json
```

批次汇总在 results/comparison_suite 的新时间戳目录。各场景报告在 results/route_benchmarks/<场景>/<时间戳>。打开汇总 comparison.md 先看达标与最终路线，再看 comparison.csv 的全部指标和各场景 report.html 曲线。命令行批次保存报告；应用中运行的任务另有可回看的历史记录。

legacy_route 运行保留的原项目连续调优核心，使用相同对象/控制器适配器；不是原项目原生温控模拟器的独立测试。原版每轮连续推进，新版每个参数建议从同一初态做完整任务复测。samples_per_round=60 对原版意味着60个连续采样点，对新版意味着全任务轨迹的提示词抽样数量；因此观察窗口差异是两条路线设计差异，不能说二者得到的时间序列信息完全相同。旧版原始初始化保留相对增幅护栏，新版test+auto公式初始化使用绝对参数范围；所有参数均经过pid_safety，再仿真评价。选优先判断任务达标；accuracy依次比较IAE、TV、调节时间，smooth依次比较超调、TV、IAE，speed先比较调节时间。原辨识公式参考original_zn不属于独立LLM路线；selected仅是胜出路线的别名。

每场景单次结果不能证明长期胜率。应分开判断公式/辨识修正、新增PI路线、LLM建议的实际收益，保留旧版优于新版的指标和全部未达标结果。零超调不代表没有误差，输出TV不代表能耗，模型内达标不代表现场生产适用。

0.4.2默认额外运行 corrected_legacy_route：使用修正Z-N PID初值，沿用旧版连续调优核心。它是混合路线，与原版默认初值路线legacy_route、纯新版调优路线分别标记和统计；tuning.include_corrected_legacy_route=false可关闭。这最多增加4轮API建议。混合路线与纯新路线指标完全相同时优先保留纯新路线，避免把重复候选当作改进。
