# 完整温控调参流程

本版已接通：FOPDT 辨识 → Z-N/SIMC 生成候选 → 现有确定性参数护栏 → 同条件仿真选优 → 原有 LLM 调参引擎 → 初始/历史最优/最后参数统一复验 → 输出建议和对照结果。

独立 IMC 算法不在本版实现中。SIMC 的 IMC 理论背景仍在 `OFFLINE_TUNING.md`。

## 直接使用

在你当前的 `outputs/llm-pid-tuner-offline/llm-pid-tuner-dev` 目录运行。当前目录已更新，无需先换回其他源码目录。

先看一遍不联网的完整流程：

```powershell
python tuning_pipeline.py --tuner mock --compare-original --out results/full_mock
```

完成后在浏览器打开 `results/full_mock/report.html`。它包含三组的最终独立验证指标及温度/控制输出曲线。

**mock 是确定性的测试建议器，不是真实 LLM。**它用于检查参数流转、护栏、回滚和文件输出，不能用它的结果宣称真实 LLM 优化有效。

使用真实 LLM：将所属平台的 API 地址、模型名及密钥填入本地 `config.json`，安装原项目依赖，再运行：

```powershell
python -m pip install -r requirements.txt
python tuning_pipeline.py --tuner llm --compare-original --plant heating --rounds 4 --out results/full_llm
```

真实模式调用原 `llm.client.LLMTuner`，不是读取离线建议文件。它支持原项目已有的 OpenAI 兼容接口及 Anthropic 接口。字段沿用原项目：

```json
{
  "LLM_API_KEY": "在本地填写，不提交或打包",
  "LLM_API_BASE_URL": "所属平台提供的接口地址",
  "LLM_MODEL_NAME": "所属平台提供的准确模型名",
  "LLM_PROVIDER": "openai"
}
```

OpenAI 兼容接口使用 `openai`；原生 Anthropic 接口使用 `anthropic`。不要根据密钥外观猜平台。缺失密钥、API 地址或模型名时，完整入口在请求前报错。也可用同名环境变量提供这些字段。真实模式会发送仿真数据给所配置的 LLM 服务，但从不连接生产设备。

没有 `config.json` 时可从 `config.example.json` 复制一份。已有文件只改相应字段，保留其他设置。交付 ZIP 排除本地配置及密钥，结果中也不保存密钥。

原项目常规仿真入口也已接入自动选优：

```powershell
python simulator.py --plain --initialization auto
```

它会沿用原项目配置和交互流程；本版新增的完整实验入口 `tuning_pipeline.py` 更便于固定条件和生成对照报告。常规入口 `auto` 自动比较，`zn` 使用修正辨识的 Z-N，`original-zn` 复现原热启动，`none` 跳过辨识初始化。自动热启动将 Python 加热目标固定，避免每 10 秒切换目标干扰单次阶跃评价；原 `HeatingSimulator` 类的默认动态设定值行为仍保留，只有温控流程主动关闭。

## 原版与新版怎么比

`--compare-original` 创建三组，每组重建相同初始状态的模型、建议器、随机种子，并使用相同控制器、参数护栏、轮数上限和验证时长：

| 组别 | 初始化 | 后续流程 |
|---|---|---|
| `original_zn` | 原短阶跃探测及原辨识、Z-N 代码 | 同一原有调参引擎 |
| `corrected_zn` | 修正辨识、Z-N | 同一原有调参引擎 |
| `selected` | 修正辨识，Z-N/SIMC 仿真自动选优 | 同一原有调参引擎 |

原辨识代码原样保存在 `reference/original_system_id.py`，来自最初 ZIP。加热模型复现原 40–80 点、无阶跃前基线的探测；FOPDT 示例则让原辨识代码读取同一类合成阶跃数据，以展示原辨识行为。对照是受控条件下的初始化方案比较，不是把原项目默认反复切换目标的运行结果与新固定目标实验混比。

加热模型使用原 `HeatingSimulator.compute_pid()` 的误差微分、积分截断和物理 `update()`；三个组均关闭动态目标切换。FOPDT 使用第一阶段的并联式温控控制器，三个组同样使用测量微分和条件积分抗饱和。两类模型的结果不能合并当作同一个控制器的表现。

原始建议可能不同，但护栏裁剪后参数可能相同；这时原版组和修正 Z-N 组结果相同是合理的，不能虚构辨识改善带来了实际收益。

## 自动选优规则

每个 Z-N PID、Z-N PI、SIMC PI 候选先经过 `pid_safety.apply_pid_guardrails`，再创建模型进行仿真。统一参考 PID 默认为原项目 `(1,0.1,0.05)`。

先筛选：

- 阶跃超调不超过 5%；
- 末段平均绝对误差不超过 0.3°C；
- 仿真期间进入并保持在阶跃幅度 ±2% 的误差带内。

在合格候选中依次比较 IAE、输出总变差和调节时间，最后按名称固定打破完全相同的结果。可用 `--max-overshoot` 和 `--max-tail-error` 指定实验要求。这些是仿真选优门槛，不是化工安全限值。

没有候选合格时保持经过检查的当前参数，标记 `no_eligible_candidate`，由原有引擎继续评价；不把不合格候选称为最佳。`lambda=max(theta,tau/3)` 为 SIMC 默认离线起点，可用 `--lambda` 调整。

原引擎的历史最优参数和回滚机制保留。新增两个修正：零误差不再被当作缺失值计成巨大分数；LLM 给出 `DONE` 时，新流程要求同一套参数已有合格测量结果，否则继续评价，不提前接受尚未运行的参数。

调参结束后统一复验 `INITIAL_PID`、`ENGINE_BEST`（如存在）、`ENGINE_FINAL`。每套参数再次经过现有护栏，分别从相同初始状态独立运行。按相同规则输出 `recommended_pid`。如果没有合格方案，`recommended_pid` 为 `null`，保留诊断曲线，不给出“合格参数”的假象。参数建议只适用于已测试条件。

## 结果看哪里

| 文件/字段 | 内容 |
|---|---|
| `report.html` | 最终验证性能表和温度、输出曲线 |
| `summary.json → initialization` | 辨识结果、各候选原始/实际参数、筛选理由、选中的初始 PID |
| `arms → 组名 → episode` | 原有引擎的轮数、调用、护栏、历史最优、回滚、最后实际参数 |
| `arms → 组名 → final_candidate_results` | 初始、历史最优、最后参数的独立验证结果 |
| `arms → 组名 → recommended_pid` | 通过最终验证的并联式 Kp/Ki/Kd；没有合格结果时为 null |
| `组名_training.csv` | 调参过程温度、输出及逐时刻实际 PID |
| `组名_validation.csv` | 最终对比表对应的独立验证轨迹 |
| `initial_ZN_PID.csv` 等 | 初始候选比较轨迹 |

`episode.actual_final_pid` 是引擎结束时真实应用过的参数，不一定是最终推荐值；终验可能退回初始参数或历史最优参数。各次建议的增幅仍受同一护栏限制。训练过程状态不同、轮数可能不同，所以性能表使用共同起点和时长的独立验证，而不是直接比较不同长度的训练轨迹。

真实模式还应检查 `tuner_calls.response_received`、`fallback_count`，确认结果有真实 LLM 响应贡献。API 调用失败会沿用原项目兜底机制并记录，不能把纯兜底结果写成 LLM 优化成果。

超调按设定值阶跃幅度归一化；IAE 单位 °C·s；末段误差为最后 10% 时间的平均绝对误差；输出总变差越小通常表示动作较平稳。必须结合允许超调、调节时间、稳态误差和输出动作综合判断，不能只挑一个有利数字。

## 更多离线实验

FOPDT 模型：

```powershell
python tuning_pipeline.py --plant fopdt --K 0.8 --tau 300 --theta 20 --tuner mock --compare-original --out results/fopdt_full
```

历史单次阶跃数据：

```powershell
python tuning_pipeline.py --plant fopdt --id-file history.csv --time-unit s --tuner llm --compare-original --out results/history_full
```

历史输入坐标必须先统一为当前 0–255 示例输出坐标；FOPDT 基线默认 70°C。该实验仍是在辨识模型上仿真，不重现现场全部非线性。

噪声、过程增益偏差和冷却负荷：

```powershell
python tuning_pipeline.py --tuner mock --compare-original --noise 0.05 --gain-scale 0.8 --disturbance-time 80 --disturbance-rate -0.2 --out results/disturbance
```

初始化在名义无噪声模型上选优；训练和终验使用相同指定扰动工况。`noise` 是每个采样点附加温度噪声的标准差，`disturbance-rate` 是指定时刻后持续施加的附加温度变化率（°C/s）。这是可复现的离线负荷示例，不是具体氯碱反应器模型。

## 已验证范围

### DeepSeek Flash 真实接口试验（2026-10-08）

本地连接已配置为 `https://api.deepseek.com/v1`，模型为 `deepseek-flash`，沿用 OpenAI 兼容接口。依据 [DeepSeek 官方接口说明](https://api-docs.deepseek.com/en/)。密钥只在本地 `config.json` 中，不进入交付 ZIP。

已实际运行：

```powershell
python tuning_pipeline.py --tuner llm --compare-original --plant heating --rounds 2 --out results/deepseek_flash_live
```

共收到 5 次可解析的真实模型响应，三组兜底次数均为 0。结果见 `results/deepseek_flash_live/report.html` 和 `summary.json`。

| 组别 | 终验超调% | IAE(°C·s) | 调节时间(s) | 输出总变差 | 有效响应 | 回滚次数 |
|---|---:|---:|---:|---:|---:|---:|
| 原初始化 + LLM | 30.269 | 395.721 | 26.0 | 657.07 | 2 | 0 |
| 修正辨识 Z-N + LLM | 33.785 | 395.721 | 22.4 | 634.36 | 2 | 0 |
| 自动选优 + LLM | 0.622 | 337.696 | 9.6 | 324.76 | 1 | 1 |

自动选优选中了 SIMC PI。真实 LLM 修改后运行表现变差，原引擎触发回滚；最终复验采用 `ENGINE_BEST`，参数为 P=2.3545167316、I=0.2181566414、D=0，与选中的初始 SIMC 参数相同。此次结果不能表述为“LLM 进一步改善了 SIMC”，而是新增初始化和原有回滚一起保留了较好的控制效果。

原组与修正 Z-N 组经过护栏后的初始参数相同；后续 LLM 输出不完全确定，两组终验差异不能归因于辨识修正。两组均未满足此次终验的 5% 超调门槛，`recommended_pid` 为 null。此为固定模型、2 轮上限的一次试验，不足以证明全部工况下新版都更好；正式比较应增加轮数、种子和扰动场景。最终曲线对应护栏后的实际参数。

全项目回归测试通过：355 项测试、11 项子测试。覆盖选优筛选、参数先检查后仿真、选中参数进入原引擎、API 失败兜底、DONE 后验证、原历史最优/回滚、真实 LLMTuner 接口的模拟传输、本地配置隔离和密钥不写入报告。没有为了测试移除原有断言；原仿真器的模拟客户端测试增加了独立的假连接配置。

完整流程已在原加热模型、FOPDT 示例及含噪声/增益偏差/冷却负荷的场景运行。接入测试使用原 `LLMTuner` 的提示/解析接口加模拟网络响应；此外已完成上节所述 DeepSeek Flash 的真实服务试验。mock 结果与真实试验分别标记和保存，不混用为 LLM 效果证据。

原项目已有程序级参数约束、历史最优参数和回滚机制，并非只依赖 Prompt。本版保留这些机制；真正的化工过程安全仍由现场 DCS/PLC/SIS 等既有保护体系负责。所有入口均限于离线仿真和参数建议，没有生产设备连接或现场参数写回。
