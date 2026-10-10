# 配置文件怎么填

先按[README](../README.md)创建并安装项目虚拟环境。Windows入口为 `.\.venv\Scripts\python.exe pid_project.py --config project.json`。全部公开输入放在该文件；每项上方有中文注释，解释含义、单位、可选值和默认值。只改冒号右边的值；删除某字段时使用内置默认值。程序支持 `//` 行注释和 `/* ... */` 块注释，原来的纯 JSON 仍可读取。此模板是带注释的 JSON，普通 `json.loads` 不能直接读取；读取配置应使用项目的 `load_project`。逗号和双引号仍须遵守 JSON 语法。未知字段和不合理范围会报错，避免拼错后默默用默认值。文件路径相对于该配置文件所在目录，环境变量从启动进程读取。

`python pid_project.py --init my_project.json` 创建完整默认模板；`--validate` 只检查配置，不连接设备。
`project.schema.json` 可关联到编辑器提供补全；程序中的交叉检查仍是最终依据。

## 从 30°C 升到 100°C

默认文件就是这个任务。修改时同时核对对象、任务和评价条件：

```json
{
  "mode": "test",
  "model": {"type": "fopdt", "source": "parameters", "K": 1.0, "tau_s": 120, "theta_s": 10,
            "operating_temperature_c": 20, "operating_output": 0},
  "task": {"initial_temperature_c": 30, "target_temperature_c": 100, "initial_output": 0},
  "actuator": {"unit": "%", "min": 0, "max": 100, "max_rate_per_s": 10},
  "evaluation": {"max_overshoot_pct": 5, "max_temperature_c": 102, "max_tail_error_c": 0.3,
                 "settling_band_c": 1, "max_settling_time_s": 600},
  "simulation": {"duration_s": 600}
}
```

任务回答“从哪里到哪里”；评价要求回答“多久到、允许超过多少、最后允许差多少”。这个例子中 5% 超调对应 3.5°C，但最高温度 102°C 更严格，两项必须同时满足。

## 每组字段的作用

| 字段 | 输入内容 | 单位与默认值 |
|---|---|---|
| `model` | 被控对象及模型来源 | FOPDT：K=1 °C/输出单位，τ=120s，θ=10s |
| `history` | CSV 路径、列映射、时间单位 | `timestamp,input,pwm`；秒；文件独立保存 |
| `identification` | 离线阶跃时长、幅度、拟合误差上限 | 960s、20 输出单位、相对 RMSE 5% |
| `task` | 初温、目标、初始输出、环境/加热器初温 | 30→100°C；输出 0；环境 20°C |
| `actuator` | 加热功率/阀位范围与变化速率 | 0–100%，每秒最多 10 个百分点 |
| `controller` | 参数形式、单位、采样周期、滤波、护栏 | 并联式、秒、1s；见下文 |
| `algorithms` | 候选集合、SIMC 的 λ | Z-N PID、Z-N PI、SIMC PI；λ 自动 |
| `evaluation` | 合格条件 | 全部条件同时满足，再按 IAE、输出变化择优 |
| `simulation` | 仿真时长、噪声、扰动、增益偏差 | 默认 600s，种子 0，无噪声/扰动 |
| `tuning` | LLM 最大轮数及停止规则 | 4 轮；每次建议完整重跑任务 |
| `llm` | 是否启用、供应商、模型、密钥读取位置 | 默认关闭；启用后向配置的服务发送模型/曲线摘要 |
| `device` | 使用模式的网关、状态校验和观察时间 | 默认禁用；见设备协议文档 |
| `output` | 结果根目录及 CSV 开关；每次独立保存到时间戳子目录 | `results/project/<时间戳>/` |

`model.operating_temperature_c` 与 `operating_output` 是模型的工作点。FOPDT 的稳态温度是
`工作点温度 + K × (输出 − 工作点输出)`。默认 100°C 约需 80% 输出，属于可达目标。
`task.initial_temperature_c` 是任务开始时的实际温度，可以不同于工作点。FOPDT 的输入延迟历史假定为 `task.initial_output`。
`ambient_temperature_c`、`initial_heater_temperature_c` 用于两节点加热模型；已给定 FOPDT 的工作点后，这两个值不再额外改变 FOPDT。

## 历史 CSV

把 `model.source` 改为 `csv`、`model.type` 保持 `fopdt`，填写 `history.file`。列名可以映射，例如：

```json
{"model":{"source":"csv"}, "history":{"file":"data/step.csv","time_unit":"ms",
 "columns":{"time":"time_ms","temperature":"temperature","output":"valve_percent"}}}
```

数据需要包含一个清晰的输出阶跃、阶跃前基线、阶跃后的动态响应和基本稳定的末段。它是**输入输出阶跃数据**，不是只有温度的一列，也不是任意多次调阀的长期趋势。时间必须递增，输出量纲必须与 `actuator` 相同。当前算法会拒绝多阶跃、异常行、未收敛或拟合误差过大的数据。

默认从历史基线读取模型工作点。拟合后，闭环验证使用该 FOPDT 模型，不会把历史数据回放伪装成新的真实闭环实验。`examples/step_history.csv` 是明确标记的合成数据。

## 控制器和参数形式

内部以及 `controller.initial_pid`、`controller.limits` 都采用**秒制、连续并联式、非负幅值**，方向由 K 的正负决定。`initial_pid` 是护栏的参照值；使用模式从设备读回它。

`u = bias + sign(K) × [Kp·e + Ki·∫e dt + Kd·d(signal)/dt]`，积分增量乘采样周期、微分除采样周期。输出限幅和变化速率同时生效，积分采用条件积分抗饱和。微分默认对测量值，采用一阶滤波 `α=dt/(dt+filter_s)`。

`controller.form` 控制导出/设备参数表示，支持 `parallel` 和 `ideal`（ISA），不支持串联式。转换为 `Ki=Kp/Ti`、`Kd=Kp·Td`。`parameter_time_unit=min` 时，导出并联 Ki 乘 60、Kd 除 60，理想式 Ti/Td 除 60；Kp 不变。真实设备若采用比例带、重复次数、独立正反作用开关或离散增益，需要在网关中明确转换，不能直接照抄数值。

`controller.initialization=zero` 在任务开始/参数写入时将积分与微分状态复位，保留当前输出作为速率限制起点。`tracking` 会预置积分，使比例项和积分项合成当前输出，再开始新任务；适合需要平滑接管的接口。设备必须支持并报告相同初始化语义。

`controller.guardrail_policy` 默认 `auto`，分阶段应用同一个 `pid_safety.py`：

| 模式/阶段 | 有效数值与绝对上下限 | 相对增幅限制 |
|---|---|---|
| test：初始公式候选 | 检查 | 不按任意默认 PID 裁剪 |
| test：LLM 每轮建议 | 检查 | 相对当前仿真参数检查 |
| test：最终建议参数 | 检查 | 不按任意默认 PID 再裁剪 |
| use：公式、LLM、最终写入 | 检查 | 保留；最终相对实际设备初始 PID 再检查 |

设置为 `relative` 让所有模式的初始候选也检查相对增幅。0.4.3起超限就整组拒绝，不再裁剪或复现旧裁剪结果。省略本项使用 `auto`；升级后离线结果可能变化，旧报告保留原配置与参数记录。参照为 0 的项仍只受绝对上下限约束。

绝对上下限是用户配置的程序参数范围，不是自动辨识出的现场安全边界。每个候选仍经过输出限幅、速率限制、抗积分饱和和完整任务仿真评价；没有达标参数时不会给出合格推荐。报告明确列出各阶段实际护栏策略以及原建议/接受或拒绝状态。护栏只约束程序参数，不等价于化工过程保护。

## LLM

完整安装、密钥与四种运行组合见[README](../README.md)。`project.json`中的`llm`设置开关、服务、模型；根目录`config.json`只需提供`LLM_API_KEY`。任务与LLM服务统一在`project.json`配置，不需要专用DeepSeek配置文件。

设置 `llm.enabled=true`。密钥读取顺序：`llm.api_key_env` 指定的环境变量 → `llm.credentials_file` 中的 `LLM_API_KEY`。保留本地 `config.json` 兼容以前配置，密钥不写入 `project.json`。

启用 LLM 后三组分别调优，默认最多 3×4 次建议调用，网络失败可能发生重试。`llm.max_attempts` 默认 2，`timeout_s` 默认 60 秒，SDK 内部额外重试关闭；一次 SDK 失败还可能切换 HTTP 传输。`compare_original=false` 仅执行 selected 组，可减少调用量。LLM 只建议参数；程序使用同一模型、初始状态、随机种子和完整仿真时长重新评价。最佳参数一直保留，恶化会回滚，API 失败保留既有最佳。是否接受不取决于 LLM 自称 DONE。

`samples_per_round` 控制发送给 LLM 的曲线抽样量，**不会缩短验证时长**。`stable_rounds` 需要连续达到评价门槛及 `average_error_threshold_c`；本版平均误差为完整任务 IAE/时长。

`json_output` 默认 true，使用兼容 OpenAI 的接口请求 JSON 对象；服务不支持该选项时可以关闭。`max_output_tokens` 默认 2048。`deepseek_thinking` 默认 disabled，只向官方 `api.deepseek.com` 发送该设置；可以改为 enabled 或 provider_default。思考内容不作为 PID 响应解析。参数依据 [DeepSeek 官方思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)；其他供应商不附加 DeepSeek 的专用字段。

## 评价和输出

超调按任务温差归一化；IAE 用温差绝对值的梯形积分；末段 MAE 使用 `tail_fraction`；有符号稳态误差为末段平均的目标减测量；调节时间要求从首次持续入带起一直保持到仿真结束，且观察不少于 `min_settled_observation_s`；输出 TV 为相邻输出变化绝对值之和。所有判据在真值温度上计算，噪声只进入控制器测量信号。

`simulation.gain_scale` 表示验证对象相对于辨识模型的增益偏差；`measurement_noise_std_c` 是独立测量噪声标准差；`disturbance_rate_c_per_s` 是指定时间后施加到对象状态的持续温度变化率，不是一次性温度跳变。
仿真时长必须是采样周期的整数倍。某次违反温度停止边界会中止并判不合格。

`pid.json` 只在合格时包含建议；`summary.json` 保存有效配置、时间戳、配置哈希、辨识、候选原始/应用参数、LLM 历史、未达标原因；`report.html` 展示曲线和指标；CSV 便于绘图或论文分析。测试模式中退出码 0 表示有合格建议，2 表示完成但没有合格建议，1 表示配置或运行错误。

0.4.3新增 `tuning.max_guardrail_retries_per_round`（默认2）和 `tuning.max_llm_requests_per_route`（默认12）。前者是每轮首次被拒绝后的重试次数，后者是每条路线含重试的建议接口总调用上限。新路线 `rounds` 只计合法参数的仿真尝试；护栏拒绝不占此预算。旧连续核心仍按采样窗口计轮，拒绝重试复用同一窗口。网络层重试独立受 `llm.max_attempts` 限制。详见[护栏与状态](GUARDRAILS.md)。
