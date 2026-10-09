# 可视化通用 PID 工作台

Windows 用户双击 `PIDWorkbench.exe`，不需要手动创建 Python 环境或编辑 JSON。
选择示例、填写模型与任务、设置评价要求，然后开始运行。示例参数只代表示例模型。

## 所有结果在应用中查看

每次运行生成独立记录，包含任务配置、候选参数、护栏记录、响应曲线、性能指标、LLM 轮次和回滚信息。
完成后直接在“响应与结果”中查看；以后通过“运行历史”打开同一条记录。
可将旧记录的配置载入重新测试，新的运行会生成新记录。
HTML 报告、CSV 和 PID JSON 都是可选导出；日常使用不需要寻找或打开 HTML 文件。

应用数据默认位于 `%LOCALAPPDATA%\ThermalPIDWorkbench`，包含项目配置、密钥文件、导入数据、方案及运行记录。
源码目录中原有 `project.json` / `config.json` 不会被应用自动覆盖。需要时在界面导入自己的配置。
配置导出和方案不包含 API 密钥；密钥保存在本机独立文件，环境变量优先。

## 输入与模型

| 输入 | 说明 |
|---|---|
| 被控量 | 温度、压力、流量、液位、转速或自定义名称，指定实际单位 |
| 对象 | 已知 FOPDT 的 K/τ/θ；历史阶跃 CSV；积分模型的 K/θ；自定义 Python；原 Simulink 流程 |
| 任务 | 初始值与目标值；模型工作点另填，不能混为一谈 |
| 执行器 | 输出单位、最小/最大值、变化速度 |
| 评价 | 被控量上下限、超调、末段误差、稳定偏差带、调节时间等 |

CSV 需要时间、实际控制输出和实际被控量。列名任意，在界面选择对应列。
例如压力数据可以是 `time_s,valve_percent,pressure_mpa`。时间单位选择秒或毫秒。
必须含适合 FOPDT 辨识的单次阶跃基线与稳定响应；不能保证任意生产历史记录都能辨识。

FOPDT 的 K 单位是“被控量单位/输出单位”；积分模型 K 的单位是“被控量单位/输出单位/秒”。
积分模型目前只支持已知参数或用户初始 PID，不使用 FOPDT CSV 辨识，也不套用 Z-N FOPDT 公式。
积分型 SIMC PI 使用 `Kp=1/(|K|(λ+θ))`、`Ti=4(λ+θ)`、`Ki=Kp/Ti`；方向由 K 的正负决定。

自定义 `.py` 文件提供 `create_model(config)`，返回对象包含 `value` 和 `step(output, dt_s)`。
`step` 更新内部状态并返回被控量。界面提供可下载模板，兼容旧模型的 `temperature` 属性。
导入 Python 会执行本机代码，需要用户确认来源可信。选择 `manual` 可从用户 PID 开始仿真和 LLM 优化。
选择 `probe` 只在离线模型上做阶跃；拟合不合格会拒绝套用 FOPDT 整定。

模型、CSV、目标、约束、LLM 和设备数据都采用被控量指定单位，程序不隐式换算 bar/MPa 或 ℃/℉。
为了兼容旧配置，内部保留 `temperature_c` 等历史键；通用配置导出使用 `initial_value`、`target_value`、`max_value` 等名称。
旧配置和新配置都能加载，冲突别名会被拒绝。旧键对非温度对象也代表所配置单位的被控量。

## LLM

在“大模型调优”页开启开关，填写提供商、API 地址、模型名称和密钥，保存后运行。
关闭 LLM 时通用工作台仍有辨识、Z-N/SIMC、确定性护栏、仿真、评价与选优。
开启时大模型通过联网 API 给出候选；程序检查参数并在本机完整仿真，保留最佳方案。
SDK 和 HTTP 回退保留；提供商支持 OpenAI 兼容与 Anthropic。原流程使用同一服务设置。
原流程关闭 LLM 时保持当前 PID，记录响应，初始化设置仍可运行。

## 原项目兼容与设备

“运行流程”可切换通用工作台、原 Python 温控仿真、原 Simulink 和原串口硬件调优。
原配置页面包含三种硬件协议、主/第二 PID、分立 P/I/D 模块路径、日志信号、设定值模块、代理、护栏与调优预算。
原硬件 `DEMO` 是本机模拟桥。原 Python 模型是专用加热模型，其他被控量用通用工作台或对应 Simulink 模型。
Simulink 需要本机 MATLAB/Simulink 与对应 Python 版本兼容的 MATLAB Engine；打包应用使用 Python 3.13 时，旧 MATLAB 可能需改用源码环境或重新打包。
保留原 MATLAB_ROOT 引导与 Engine 加载能力，不将 MATLAB 本体包含在安装包中。

通用 `use` 模式支持模拟、TCP、串口 JSONL 或自定义适配器。
先读状态、核对参数形式/时间单位/方向/输出约束/微分等语义；仿真合格后条件写入、独立读回、有限时监测。
温控旧协议 v1 保留；非旧摄氏温控采用 v2，状态须带 `value`、`setpoint`、`value_unit`、`process_kind`。
例如压力状态使用 `value_unit="MPa"`、`process_kind="pressure"`，写入使用 `setpoint`，不能混用 `setpoint_c`。
自定义适配器接口仍为 `read_state/apply/close`，非温度状态也必须符合上述单位定义。
实际 PLC/DCS 需要部署或开发与其通讯协议对应的网关；应用不包含所有厂商驱动。
现有 DCS/PLC/SIS 负责现场过程保护，仿真或软件检查不能替代投运和现场验证。

## 停止、暂停与恢复

原流程支持暂停、继续和停止；通用工作台支持协作停止。
API 请求在响应或超时后退出；本地仿真在采样边界退出。设备写入之后停止会进入已有故障恢复与审计机制。
运行时关闭窗口会先请求停止；等运行结束后才能关闭，防止中断设备审计/恢复。
上次异常退出留下的运行中记录会标识为中断，不能据此推断现场已恢复。

## 源码启动与重新打包

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,desktop]"
.\.venv\Scripts\python.exe desktop_app.py
```

浏览器模式：`desktop_app.py --browser`。数据目录可用 `--workspace PATH` 指定。
打包：`.\.venv\Scripts\python.exe scripts\build_desktop.py`，输出 `dist/desktop` 的 EXE 和便携 ZIP。
安装包使用明确资源清单，不包含本地密钥、生产数据和 results。
Windows 窗口需要 Edge WebView2 Runtime；缺少时可安装微软运行时或使用浏览器模式。
构建方式参考 [pywebview 官方打包说明](https://pywebview.flowrl.com/guide/freezing) 与 [PyInstaller 官方说明](https://pyinstaller.org/en/stable/usage.html)。
