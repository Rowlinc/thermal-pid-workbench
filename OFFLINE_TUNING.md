# 氯碱化工温控：离线整定与比较

完整流程已接入，运行方式请优先看 `PIPELINE_GUIDE.md`。本说明中的对比数据保留为第一阶段的固定参数实验，`offline_compare.py` 仍可独立使用。新版 `simulator.py` 默认已改为自动选优后接入原有 LLM，不再只选 Z-N。

第一版只实现原项目 Z-N 和新增 SIMC PI，重点比较参数、温度响应、控制输出与性能指标。本次实现面向温度回路的离线仿真和参数建议。`offline_compare.py` 不导入串口、LLM 或 MATLAB 入口，不读取 API 配置，不向现场写参数。示例参数是用于验证算法的假设值，尚无实际氯碱装置历史数据。

原项目本身已经有 `pid_safety.py`，对 LLM 调整 PID 参数设置了程序级约束，并有历史最优参数和回滚机制。因此它并不是完全依赖 Prompt 保证调参安全。我们目前保留这套机制，并重点研究 FOPDT 辨识修正以及新增 SIMC 整定；真正的化工过程安全仍应由现场 DCS/PLC/SIS 等既有保护体系负责。

## 修改文件

- `system_id.py`：保留 FOPDT 阶跃辨识和 Z-N，增加 `simc_pi`、`tuning_candidates`、双向参数转换；补充数据校验、拟合 RMSE 和提示。
- `simulator.py`：第一阶段修正热启动探测；完整版本进一步改为充分探测、Z-N/SIMC 分别检查和仿真选优后接入原有 LLM。详情见 `PIPELINE_GUIDE.md`。
- `offline_compare.py`：独立离线对比入口，支持 FOPDT 示例、历史 CSV 辨识后的模型，以及原项目 `HeatingSimulator` 加热模型。
- `pid_safety.py`：复用原有确定性参数护栏；增加无效/非有限建议值保留当前值的明确说明，避免静默修正；使无效全局增幅配置回退到原默认行为。
- `core/offline_evaluation.py`：计算全轨迹的阶跃超调、IAE、末段误差、调节时间与输出变化。
- `tests/test_offline_tuning.py`：公式、单位、辨识、负增益、分数延迟、指标手算与可复现性测试。

## PID 参数形式与核心代码

`sim/model.py` 的 `compute_pid()` 和 `firmware.cpp` 的 `computePID()` 均使用并联式：

```text
u = Kp*e + Ki*integral(e dt) + Kd*de/dt
Ki = Kp/Ti
Kd = Kp*Td
Ti = Kp/Ki     # Ki=0 时积分关闭，Ti 用 None 表示
Td = Kd/Kp
```

时间统一以秒计。Kp 的单位为输出单位/°C，Ki 为输出单位/(°C·s)，Kd 为输出单位·s/°C。不要额外乘/除采样周期；离散控制器已在积分、微分中处理 dt。这里的 Kp/Ti/Td 是理想式/ISA 参数，不能直接套用于串联式 PID。

SIMC PI 的实现：

```python
Kp = tau / (K * (lambda_ + theta))
Ti = min(tau, 4.0 * (lambda_ + theta))
Ki = Kp / Ti
Kd = 0.0
```

理论背景：SIMC（Skogestad / Simple Internal Model Control）建立在 IMC 整定思想上，通过限制积分时间改善大时间常数过程的负荷扰动响应。依据 [Skogestad 的 SIMC 论文说明](https://skoge.folk.ntnu.no/publications/2003/tuningPID/README.html) 和 [作者的课程资料](https://skoge.folk.ntnu.no/presentation/abb-course-2025/APC-ABB4%20-%20SIMC%20Controller%20tuning.pdf)。IMC 仅作为理论背景，本版没有独立 IMC 算法、候选参数或结果曲线。

本项目新增默认 `lambda=max(theta,tau/3)`，是较慢的离线起点，并非装置专属推荐值。可以显式传入更大或更小 λ 比较速度、超调和输出变化。辨识/公式函数支持负增益 K；其控制器增益也为负，原参数护栏使用非负增益策略，本版离线仿真入口明确拒绝负增益模型，不擅自更改原策略。

## 统一确定性检查

Z-N、SIMC 和导入的 LLM 建议在创建模型、调用 `set_pid` 或开始闭环仿真前，统一调用 `pid_safety.apply_pid_guardrails`，使用 `get_pid_limits("python_sim")`。原 LLM 调参路径的 `finalize_decision` 和热启动本来就调用该函数，本次保留历史最优参数与回滚机制；另外补上显式传入仿真初始 PID 的检查。离线固定参数比较不会自动调用 LLM 或通过回滚改变方法的实际参数。

默认参考参数 `p=1.0,i=0.1,d=0.05`，每个方法均使用同一参考参数和同一限值，避免前一方法影响后一方法。`--current-pid P I D` 可显式指定参考 PID；参考 PID 也先经过护栏检查。参考参数用于增幅检查，每次比较仍将模型和控制器状态重置到相同基线，而非承接一次真实运行的历史状态。当前 Python 仿真限值默认：P∈[0,5000]、I/D∈[0,500]，增幅分别至多 3/4/4 倍；全局 `PID_MAX_INCREASE_RATIO` 如设置会进一步收紧限制。离线入口使用默认配置对象，不主动加载现场或 API 配置。

每个结果保存 `requested_gains`（算法建议）、`gains`（实际用于仿真）、`guardrail_notes`（调整原因）、`baseline_pid` 与 `safety_status`。全局记录限值和参考参数。原始整定参数与检查后参数的比例可能不同，后者的 Ti/Td 重新计算。响应曲线、IAE 等都对应**检查后的实际参数**，不能当作未经约束的教科书整定公式性能。非有限离线建议被拒绝并不启动该模型；原 LLM 路径保留既有回退当前值行为并增加说明。

这些护栏是确定性的参数范围和增幅约束，不验证闭环稳定性、温度安全边界或真实化工过程安全。尤其逐项裁剪 P/I/D 后可能破坏整定参数比例，必须继续看仿真响应。本版不修改原护栏限值，也不将算法输出绕过护栏。

API 示例：

```python
from system_id import system_identify, tuning_candidates, extract_initial_pid

table = tuning_candidates(K=0.8, tau=300.0, theta=20.0, lambda_=100.0)
# table: ZN_PID, ZN_PI, SIMC_PI
result = system_identify(time_s, temperature_c, output, time_unit="s", lambda_=100.0)
suggestion = extract_initial_pid(result, pid_type="PI", method="SIMC")
# suggestion: {"p": Kp, "i": Ki, "d": Kd}; 本调用仅返回参数。
```

## 阶跃辨识的修正与限制

保留最终增益 + 5%/63.2% 交点的辨识路径，并对交点时间做线性插值。FOPDT 中 `t_f=t_step+theta-tau*ln(1-f)`，因此：

```text
tau = (t63 - t5) / (ln(0.95) - ln(0.368))
theta = max(0, t5 - t_step + tau*ln(0.95))
K = (稳态温度 - 基线温度) / (阶跃后输出 - 阶跃前输出)
```

必须提供单次开环输入阶跃及阶跃前基线；拒绝恒定输入和多次变化的输入，以免把闭环 PWM 波动错当成阶跃。支持升温、降温、正负输入阶跃。未给输入时保留原假设 0→255，但附带演示用途提示。

时间单位不再根据总时长猜测：`system_identify` 默认秒；CSV 文件入口默认毫秒，与项目原导出格式一致。CSV 可包含 `timestamp,input,pwm`，或用 `temperature` 代替 `input`。损坏行整行跳过，避免各序列错位。后阶跃至少 5 个点，但可靠辨识仍需充分基线和接近稳态的响应。

这是简化交点法，不是带噪声鲁棒优化拟合。末段仍变化、负延迟被截断或 RMSE 超过响应幅度的 5% 时提示。二阶热过程可得到有效 FOPDT 近似；这些提示和 RMSE 都不能证明模型在全部运行区间适用。报告中的稳定性仅指辨识模型极点，不保证闭环稳定。

## 运行

在解压后的 `source/llm-pid-tuner-dev` 目录打开终端，Python 3.10 或更高版本即可；离线入口只使用标准库。

```powershell
python system_id.py --mode demo
python offline_compare.py --out results/fopdt_default
python offline_compare.py --lambda 20 --out results/fopdt_lambda20
python offline_compare.py --plant heating --out results/heating
python -m unittest discover -s tests -p test_offline_tuning.py -v
```

后续 LLM 建议可先保存为并联式 JSON，例如 `{"p":3.5,"i":0.02,"d":0.0}`，再离线比较：

```powershell
python offline_compare.py --llm-file proposal.json --current-pid 1 0.1 0.05 --out results/llm_proposal
```

只读取已经保存的建议，不访问 LLM API。导入候选标记为 `LLM`，同样经过护栏后再仿真，并额外保存 `LLM.csv`。

历史数据：

```powershell
python system_id.py --mode file --file history.csv --time-unit s --lambda 100
python offline_compare.py --id-file history.csv --time-unit s --lambda 100 --out results/history
```

历史数据只用于辨识。历史文件路径不会触发设备连接。历史模式的闭环比较仍是在辨识出的 FOPDT 模型上进行，而非重现现场的全部扰动和非线性。该模型默认基线为 70°C；用 `--setpoint` 指定比较目标。输出坐标使用 0–255 的示例执行器；如果数据使用 0–100% 或实际流量，须先统一输入坐标和增益尺度，不能直接把拟合 K 与默认输出范围混用。

手动模型：

```powershell
python offline_compare.py --K 0.8 --tau 300 --theta 20 --lambda 100 --setpoint 80 --duration 2400 --dt 1
```

`--plant heating` 使用原加热模型的 0.2 秒步长，并通过独立无噪声 0→100 输出阶跃辨识后生成所有候选。各方法重新创建相同初始状态的模型，不承接上一方法的积分或温度状态。

## 控制实现与指标

各方法使用相同并联式控制器、固定设定值、0–255 输出限制、条件积分抗饱和和测量微分。为了可靠比较单次温度阶跃，没有调用原控制器每 10 秒切换设定值的行为。原模型 `update()`、`set_pid()` 参数接口与连续 PID 增益形式仍可兼容，但离线控制器采用的抗饱和和测量微分有别于原 `compute_pid()` 的积分截断与误差微分，结果不能直接当作原默认运行方式的实测结果。

- 超调：超过目标的温度差除以设定值阶跃幅度；不是除以绝对摄氏温度。
- IAE：整个仿真时段内绝对误差的梯形积分，单位 °C·s。
- 稳态误差：最后 10% 时段的平均绝对误差，同时保存带符号平均误差；有限时长估计。
- 调节时间：进入并持续留在阶跃幅度 ±2% 带内的时间，至少观察到随后 5% 总时长仍在带内；未满足输出 `null`。
- 输出变化：控制输出总变差 `sum(abs(u[k]-u[k-1]))`、最大单步变化、上下限和饱和比例。

JSON 中额外保存原 `AdvancedDataBuffer` 计算的 `legacy_metrics`，它使用不同的超调归一化和末段比例，不与上述指标混为一谈。原 evaluation 路径未更改。

默认输出 `summary.json` 与三份完整轨迹 CSV，算法候选为 `ZN_PID`、`ZN_PI`、`SIMC_PI`；`--llm-file` 仅添加后续建议比较入口。现有结果位于 `results/fopdt_default`、`results/fopdt_lambda20`、`results/heating`。HTML 对比页同时显示原始与实际参数、调整说明、性能表、温度响应和控制输出曲线。

## 已运行结果

FOPDT 示例：K=0.8°C/输出单位，tau=300s，theta=20s，温度 70→80°C，仿真 2400s，步长 1s，无噪声，统一参考 PID=(1,0.1,0.05)。原始整定建议如下，尚未用于仿真：

| 方法 | λ(s) | Kp | Ti(s) | Td(s) | Ki | Kd |
|---|---:|---:|---:|---:|---:|---:|
| Z-N PID | — | 22.5 | 40 | 10 | 0.5625 | 225 |
| Z-N PI | — | 16.875 | 66.6 | 0 | 0.253378 | 0 |
| SIMC PI | 100 默认 | 3.125 | 300 | 0 | 0.010416667 | 0 |
| SIMC PI | 20 | 9.375 | 160 | 0 | 0.05859375 | 0 |

通过护栏后，真正用于仿真的参数：

| 方法 | λ(s) | Kp | Ki | Kd | Ti(s) | Td(s) |
|---|---:|---:|---:|---:|---:|---:|
| Z-N PID | — | 3 | 0.4 | 0.2 | 7.5 | 0.066667 |
| Z-N PI | — | 3 | 0.253378 | 0 | 11.84 | 0 |
| SIMC PI | 100 默认 | 3 | 0.010416667 | 0 | 288 | 0 |
| SIMC PI | 20 | 3 | 0.05859375 | 0 | 51.2 | 0 |

| 方法 | λ(s) | 超调(%) | IAE(°C·s) | 调节时间(s) | 输出总变差 |
|---|---:|---:|---:|---:|---:|
| Z-N PID | — | 166.343 | 5186.162 | 未调节 | 1086.277 |
| Z-N PI | — | 123.206 | 4191.561 | 未调节 | 791.637 |
| SIMC PI | 100 默认 | 0.224 | 1217.637 | 389 | 51.876 |
| SIMC PI | 20 | 41.293 | 1606.220 | 800 | 119.214 |

末段平均绝对误差分别为 Z-N PID 0.95699°C、Z-N PI 0.90793°C、默认 SIMC 0.00014°C、λ=20s 的 SIMC 0.00023°C。这是无噪声模型的有限时长估计，不代表现场精度。

Z-N 的 P/D 建议被大幅限制，I 相对更强，参数比例偏离原 Z-N；仿真出现较大超调且在 2400s 内没有持续调节。默认 SIMC 只小幅调整 P，响应较平稳。在相同护栏下，将 SIMC 的 λ 从 100s 缩短到 20s 并未得到更好的性能：P 仍限制为 3，I 却更强，超调、IAE 与调节时间都变差。因此不能只把 λ 当作“越小越快”的按钮，也不能把参数检查通过当成稳定性证明。应结合实际参数和响应继续评价，不据此直接给现场装置选参数。

原加热模型辨识近似：K=1.069519，tau=10.7928s，theta=0.6883s，20→100°C，仿真 200s，步长 0.2s。检查后 Z-N PID 超调 12.143%、IAE 320.360、调节 17.0s、输出总变差 458.569；Z-N PI 为 10.928%、309.397、15.8s、461.033；默认 SIMC 为 0.622%、337.696、9.6s、324.759。该模型为项目原二阶加热示例，并非真实氯碱反应过程。

选型可以先从较慢 PI 起点做更多离线比较。当前实验只有设定值阶跃和无噪声模型；实际温控建议还需要历史数据、负荷扰动、噪声、执行器约束和模型不确定性验证。本次没有提供或实施现场写回。

## 验证

新增离线测试 13 项全部通过；连同原 `test_regression.py` 和 `test_simulator_tui.py` 共 101 项通过。新增验证覆盖各来源共用护栏、检查先于模型创建、原 LLM 决策与离线建议得到相同检查结果、无效值说明及全局增幅约束。修改后的源文件也通过 Python 编译检查。三个对比场景以及 `system_id.py --mode demo --lambda 20` 均已实际运行。

当前 Windows 沙箱中 Python 3.13 的临时目录 ACL 阻止常规临时文件访问，回归验证使用 `work/run_tests.py` 将测试临时目录改到项目内；没有修改原测试断言。测试依赖隔离安装到 `.test-deps`，未改变系统 Python。交付 ZIP 不包含临时工作目录或这些依赖；运行新增离线对比和新增 unittest 不需要安装额外包。原项目回归测试需要项目依赖与 pytest。
