# Thermal PID Workbench

可视化单回路 PID 辨识、整定、仿真评价与设备接入工具。支持温度、压力、流量、液位、转速和自定义被控量。基于 [KINGSTON-115/llm-pid-tuner](https://github.com/KINGSTON-115/llm-pid-tuner) 扩展，保留 Apache-2.0 许可和原作者归属。

中文 | [English](README.en.md)

提供 FOPDT 辨识、Z-N PID、Z-N PI、SIMC PI、可选 LLM 建议和确定性参数检查。默认任务为 30°C 升到 100°C；GitHub 上的默认配置为测试模式、关闭 LLM。

## 可视化应用：推荐的使用方式

Windows 用户先到 [GitHub Releases 下载区](https://github.com/Rowlinc/thermal-pid-workbench/releases/latest)下载便携 ZIP，解压后打开应用；Gitee 同步源码和版本标签，应用下载使用同一链接。

已打包 Windows 应用的用户直接双击 `PIDWorkbench.exe`。不用编辑 JSON，也不用创建 Python 环境。

**EXE 使用教程：[PID 自动整定与 LLM 优化系统使用说明](docs/PID自动整定与LLM优化系统使用说明.md)。** 首次使用建议先阅读这份说明，再按界面填写配置和运行。

在“任务与对象”选择温度、压力、流量或液位示例，再按实际对象修改模型、任务、执行器与评价要求。
模型来源支持已知参数、历史 CSV 列选择、离线辨识、自定义 Python；原 Simulink 和串口流程也有独立可视化配置。
LLM 开关、API 地址、模型名称、密钥都在“大模型调优”页设置。

从 0.4.4 起，通用工作台的旧版调优、修正 Z-N PID、Z-N PI、SIMC PI、混合路线默认并行运行；每组独立调优、护栏和仿真，完成后按相同规则选优。结果页显示每组的状态和等待时间。在“控制器与评价”页可关闭“各路线并行调优”，或将“最多同时运行路线数”设为 1～5；接口有并发限制时降低该值。每条路线内部仍按轮次依次调优；设备模式只写入最终选中的一组参数。并行减少多组等待累积，不减少轮数或 token 用量，也不加速服务端的思考过程。

点击停止会显示“正在停止”，取消各条路线的本地 API 等待并忽略迟到回答，完成退出后保存“已停止”。仿真在采样边界停止；已开始设备写入时仍须完成恢复和审计。关闭连接不能保证模型服务商停止计算或计费。旧版 EXE 需重新下载升级。

**结果直接显示在应用里，并自动保存为运行历史。** 每条记录保留当次配置、曲线、PID、指标、护栏及 LLM 轮次；以后打开记录就能再次查看，并载入配置重新测试。HTML/CSV/JSON 是可选分享和导出，不是应用的日常查看入口。

从源码启动桌面应用的完整步骤：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,desktop]"
.\.venv\Scripts\python.exe desktop_app.py
```

源码用户可用 `desktop_app.py --browser` 在浏览器中打开相同界面。
应用数据在 `%LOCALAPPDATA%\ThermalPIDWorkbench`；源码目录的私有配置不会被自动覆盖。
不想占用 C 盘时，点击左下角“更改保存位置…”，选择例如 `E:\PIDWorkbenchData`，再点击“应用保存位置”。默认复制配置、密钥、导入数据、方案和历史到空目录，切换立即生效，下次启动记住；取消复制可使用空目录或打开已有工作台目录。复制会修正数据目录内的绝对引用，旧目录保留，确认新历史可用后自行清理旧副本。运行或正在停止时不可切换。`--workspace PATH` 仍可覆盖记住的位置；浏览器模式可手填路径。外部自定义模型及用户另设的绝对输出路径保持原配置，需自行调整；C 盘只需保留很小的位置设置文件 `%LOCALAPPDATA%\ThermalPIDWorkbench.settings.json`，不要把它当旧记录删除。EXE 临时解包仍使用系统临时目录。
原有配置可通过界面导入。安装包不包含用户密钥、历史数据和 `results/`。

详细输入、所有工作流和重新打包步骤见 [应用使用说明](docs/DESKTOP.md)；原项目功能保留说明见 [兼容性说明](docs/UPSTREAM_COMPATIBILITY.md)。
这是通用单回路工作台，不保证任意历史数据都能自动建模。FOPDT CSV 辨识、积分模型、自定义模型采用各自适用的路径。
现有命令行使用方式继续保留，下文的温控任务是默认示例。其他被控量通过 `process` 指定名称和单位；通用字段示例见 `examples/pressure.json`、`examples/flow.json`、`examples/level.json`。

## 1．先分清文件和模式

| 文件 | 内容 | 从哪里来 |
|---|---|---|
| `project.json` | 对象模型/历史数据、目标温度、控制器、执行器、评价标准、LLM服务设置及设备接口 | 仓库自带，每项有中文注释 |
| `config.json` | 私有 API 密钥，本入口读取其中的 `LLM_API_KEY` | 用 LLM 时由你在项目根目录创建；不上传 GitHub |
| `pid_project.py` | 读取配置，执行整定、仿真及可选设备集成的主入口 | 仓库自带 |
| `project.use.local.json` | 你的设备接入配置，与测试配置分开保存 | 按下文复制创建；已被 Git 忽略 |
| 历史 CSV | 用于辨识的输出阶跃与温度响应数据 | 自己提供，独立于配置文件保存 |

**模型、服务地址、模型名称、LLM开关放在 `project.json`；密钥放在 `config.json`。不用LLM时，不需要创建密钥文件。** 如果现有 `config.json` 还有其他原项目字段，可保留；`pid_project.py` 不从其中读取服务地址或模型名称，它们由任务配置决定。

| 组合 | `mode` | `llm.enabled` | 实际行为 |
|---|---|---|---|
| 测试，不用LLM | `test` | `false` | 本地辨识、传统整定、仿真、输出报告；不调用LLM，不连接设备 |
| 测试，使用LLM | `test` | `true` | 在上述流程中通过网络API请求LLM建议；对象与验证仍在本地仿真 |
| 使用，不用LLM | `use` | `false` | 离线整定验证后，通过明确配置的适配器读设备、写合格PID和目标、读回并监测 |
| 使用，使用LLM | `use` | `true` | 使用模式中加入联网LLM参数建议；LLM不直接控制设备 |

`test` 是参数建议与仿真模式；`use` 是设备集成工作流，会写入PID和目标温度。连续闭环由设备控制器运行。随包验证覆盖软件、模拟设备和本机TCP，**尚未完成真实化工装置投产验证**；现场模型、协议、单位与控制律需要适配，已有 DCS/PLC/SIS 保护保持独立。

## 2．首次安装：创建本项目的 Python 环境

以下命令适用于 Windows PowerShell，需要 Python 3.10 或以上。每次运行命令都在项目根目录，即能看到 `pid_project.py` 和 `project.json` 的目录。

首次从 GitHub 下载：

```powershell
git clone https://github.com/Rowlinc/thermal-pid-workbench.git
cd thermal-pid-workbench
```

如果已经下载ZIP并解压，直接进入自己的目录，不必再次克隆。例如：

```powershell
cd E:\Program-File-Data\MyProject\PIDjudge\outputs\thermal-pid-workbench
```

创建环境并安装基础项目：

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . --index-url https://pypi.org/simple
```

`.venv` 是本项目独立的 Python 环境，避免依赖与其他项目混用；`-e .` 安装当前项目。基础算法使用标准库，推荐安装以统一运行环境。环境只创建一次；已经有 `.venv` 时复用它。本文直接使用环境中的 Python，**不用执行 Activate.ps1，也不用调整PowerShell执行策略**。

需要LLM，再安装API客户端依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[llm]" --index-url https://pypi.org/simple
```

需要串口接入，安装串口依赖；同时用LLM和串口时选择第二条：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[serial]" --index-url https://pypi.org/simple
.\.venv\Scripts\python.exe -m pip install -e ".[llm,serial]" --index-url https://pypi.org/simple
```

安装依赖需要联网。LLM依赖是远程API客户端，**不是把大模型下载到本地**。关闭LLM后，内置模型的测试运行不需要连接模型API。

macOS/Linux 使用相同配置与流程，安装命令为：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
# 使用LLM时额外执行
./.venv/bin/python -m pip install -e '.[llm]'
```

下文 Windows 命令中的 `.\.venv\Scripts\python.exe` 对应 Linux/macOS 的 `./.venv/bin/python`。

## 3．情况一：测试模式，不使用LLM

1. 完成基础安装。
2. 打开根目录 `project.json`，修改现有字段：`mode` 为 `test`、`llm.enabled` 为 `false`、`device.adapter` 为 `disabled`、`device.write_enabled` 为 `false`，保存。
3. 核对 `model` 对象、`task` 初温/目标、`actuator` 输出能力、`controller` 采样与参数形式、`evaluation` 达标要求。默认值可直接做30→100°C示例，但不是你的实际设备数据。
4. 检查配置，再运行：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.json
```

`--validate` 只检查配置，不仿真、不连接设备、不验证API能否访问。

运行开始打印本次结果目录，完成打印 `Report:`。默认位置为 `results/project/<时间戳>/`。打开该目录的 `report.html` 看曲线与指标，`pid.json` 看合格参数。没有达标方案时 `pid` 为 `null`，不要把诊断候选当成合格推荐。

## 4．情况二：测试模式，使用LLM完整功能

1. 完成基础安装及 `.[llm]` 安装。
2. 在项目根目录创建或编辑 `config.json`。最小内容如下，替换占位文字：

```json
{
  "LLM_API_KEY": "填入你的实际API密钥"
}
```

`config.json` 是普通JSON，不加 `//` 注释。不要覆盖已有文件中的其他需要保留的字段，不要上传真实密钥。

3. 打开根目录 `project.json`，修改其中**已有的**这些字段。下方只是需要核对的部分，不是让你把整份项目配置替换成这些内容：

```json
{
  "mode": "test",
  "llm": {
    "enabled": true,
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "credentials_file": "config.json"
  },
  "tuning": {
    "rounds": 4,
    "compare_original": true
  },
  "device": {
    "adapter": "disabled",
    "write_enabled": false
  }
}
```

`provider=openai` 指兼容OpenAI的API协议，可连接DeepSeek；不表示调用OpenAI模型。模型名必须由你的服务实际支持。`rounds=4` 是每组最多4轮建议，三组比较最多12轮，重试或传输回退可能增加请求数。

4. 保存配置，执行：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.json
```

5. 打开控制台打印的**本次时间戳目录**里的报告。先看最终是否达标，再展开“LLM各轮”查看建议、实际应用参数、仿真和回滚记录。`summary.json` 中 `config.llm.enabled=true` 只证明开关打开；历史里有 `requested_pid`/`applied_pid` 才证明取得了可仿真的建议。`llm_unavailable` 等表示LLM没有成功提供建议。

**有密钥不等于启用LLM，改配置不会自动更新旧报告。** 必须保存 `enabled=true` 并重新运行。LLM通过互联网API调用；“本地仿真”指对象和控制验证在本机，不表示LLM不联网。API不可用时程序可能保留原有最好结果，成功生成报告并不保证API调用成功。

可选：当前PowerShell窗口设置 `$env:LLM_API_KEY = '你的实际API密钥'`，可以替代密钥文件。环境变量优先于文件；若想改为使用文件，先执行 `Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue`。不要把密钥写进任务配置或截图公开。

主流程统一使用 `project.json` 配置任务和LLM服务，使用根目录 `config.json` 保存密钥，不需要另建 DeepSeek 专用配置。

## 5．情况三：使用模式，不使用LLM

### 5.1 先用模拟设备走完整读写流程

完成基础安装后，可直接执行仓库自带配置：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/use_simulated.json --validate
.\.venv\Scripts\python.exe pid_project.py --config examples/use_simulated.json
```

它固定为 `use`、模拟适配器、关闭LLM，仅操作内存中的模拟设备。查看 `results/use_simulated/<时间戳>/report.html` 与 `device_audit.json`。审计包含读状态、写入、读回和监测记录；内存模拟时间加速推进。

### 5.2 用本机TCP演示网关验证通信

开启两个PowerShell窗口，**两边都进入项目根目录**。

终端一：启动模拟网关，保持运行：

```powershell
.\.venv\Scripts\python.exe -m thermal_pid.gateway_demo --config examples/use_tcp_local.json --port 9100
```

终端二：检查配置并运行客户端：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/use_tcp_local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config examples/use_tcp_local.json
```

结果位于 `results/use_tcp_local/<时间戳>/`。演示网关只监听127.0.0.1，操作模拟对象，不控制真实设备。客户端完成后在终端一按Ctrl+C停止网关。

### 5.3 接入自己的真实设备

先根据[设备协议](docs/DEVICE_PROTOCOL.md)准备能报告真实状态、条件写入并独立读回的网关或自定义适配器。本项目没有预设任意厂商PLC寄存器；填写IP地址不等于完成协议适配。

从根目录配置复制自己的使用配置，保留测试配置：

```powershell
Copy-Item -LiteralPath project.json -Destination project.use.local.json
```

若文件已经存在，直接编辑已有文件，不要重复复制覆盖自己的设备设置。修改现有字段：

```json
{
  "mode": "use",
  "llm": {"enabled": false},
  "device": {
    "adapter": "tcp",
    "host": "填写已适配网关的地址",
    "port": 9100,
    "write_enabled": true,
    "object_id": "temperature-loop-1"
  }
}
```

同时填写实际模型或历史CSV、温度任务、输出单位/上下限/速率、采样周期、PID形式、微分滤波、初始化、评价门槛及设备监测条件。设备回传的对象标识和控制语义必须一致。

- TCP：配置 `host`、`port`、`timeout_s`，网关实现JSONL协议。
- 串口：安装 `.[serial]`，设置 `adapter=serial`、`serial_port`（例如COM3）、`baud`；串口端同样需要JSONL协议。
- 自定义：设置 `adapter=custom`、`custom_factory=模块:函数`，并安装该实现实际需要的厂商SDK/协议依赖。

先在同一模型和任务下只做离线验证；`--mode test`会覆盖配置中的use模式，**不会连接设备**：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test
```

核对报告并完成设备接口适配后，再检查使用配置、执行设备流程：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json
```

最后一条命令会读取真实设备，并在条件满足时**写入PID和目标温度**。按设备当前参数检查增幅后，使用模式的结果可能与test不同；必须同时看报告与 `device_audit.json`。接口不匹配、工况变化过大或没有合格参数会拒绝写入。监测只持续配置的时间，结束后程序退出。

## 6．情况四：使用模式，使用LLM

1. 完成 `.[llm]` 安装；串口加LLM则安装 `.[llm,serial]`。
2. 按第4节将密钥放到根目录 `config.json` 或环境变量。
3. 完成第5节自己的设备配置，在根目录 `project.use.local.json` 中设置 `llm.enabled=true`，服务地址、模型和供应商按第4节填写，`credentials_file=config.json`。
4. 先执行不会连接设备的测试流程：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --mode test
```

5. 查看本次报告及LLM历史，完成对象与接口核对后运行使用流程：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use.local.json
```

LLM建议依然先经过确定性护栏和完整仿真，再检查设备参数总增幅及写入条件；它不直接发设备命令。网络调用会延长离线规划时间，写入前会重新核验设备状态。

若只想用模拟设备验证“use＋LLM”，可在根目录创建专用配置：

```powershell
Copy-Item -LiteralPath project.json -Destination project.use-sim-llm.local.json
```

编辑这份配置：`mode=use`、`llm.enabled=true`、`credentials_file=config.json`、`device.adapter=simulated`、`device.write_enabled=true`、`device.max_planning_output_change=20`。其余使用第4节服务设置；最后执行：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config project.use-sim-llm.local.json --validate
.\.venv\Scripts\python.exe pid_project.py --config project.use-sim-llm.local.json
```

该配置接入的是模拟设备，不能代表现场投产验证。

## 7．输入自己的模型、任务和历史数据

| 内容 | 配置位置 | 应填写什么 |
|---|---|---|
| 对象 | `model` | 已知FOPDT的K/τ/θ，或CSV辨识，或离线模型探测 |
| 任务 | `task` | 初始温度、目标温度、初始输出 |
| 执行器 | `actuator` | 输出单位、上下限、每秒最大变化量 |
| 控制器 | `controller` | 采样周期、参数形式/时间单位、滤波、初始化和护栏 |
| 合格要求 | `evaluation` | 允许超温、超调、末段误差及调节时间 |
| 运行时间/扰动 | `simulation` | 仿真时长、噪声、模型偏差和扰动 |

已知模型设置 `model.source=parameters`；历史CSV设置 `model.source=csv`、`model.type=fopdt`、`history.file`及列名/时间单位。历史数据需要单次输入阶跃、阶跃前基线和基本稳定末段，不是任意长期趋势或只有温度的一列。`probe`只探测本地模型，不对现场做阶跃。

可选示例均由仓库提供，先验证再运行：

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/history.json --validate
.\.venv\Scripts\python.exe pid_project.py --config examples/history.json
.\.venv\Scripts\python.exe pid_project.py --config examples/heating.json
.\.venv\Scripts\python.exe pid_project.py --config examples/cooling.json
.\.venv\Scripts\python.exe pid_project.py --config examples/custom_model.json
```

默认这些模型示例关闭LLM，不连接设备；CSV示例数据是合成的。自定义模型使用仓库中的 `examples/custom_model.py`，实际扩展工厂需可导入且每次重置状态。

所有文件路径相对于**所选配置所在目录**：根目录 `project.json` 的密钥路径是 `config.json`；`examples/` 中应为 `../config.json`；`examples/scenarios/` 中应为 `../../config.json`。把配置复制到其他目录时要同步调整路径。

详细配置：[CONFIGURATION](docs/CONFIGURATION.md)；控制与算法：[METHODS](docs/METHODS.md)；设备协议：[DEVICE_PROTOCOL](docs/DEVICE_PROTOCOL.md)。

## 8．六场景批量测试：不用LLM与使用LLM

[`examples/scenarios/`](examples/scenarios/README.md) 提供六份完整注释配置：30°C升到60/80/100/110°C、慢响应与大滞后、持续冷却扰动。它们固定为test模式，配置里默认开启LLM。

不使用LLM：完成基础安装后执行，`--llm off`只覆盖本次运行，不修改文件：

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --llm off
```

使用LLM：完成LLM安装、设置根目录密钥及确认各场景服务设置，再执行：

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --llm on
```

`--llm` 是批量脚本选项，**不是 `pid_project.py` 的选项**。六场景三组各最多4轮，最多72轮建议，实际可能提前结束；重试可能增加请求数。不传选项则按每份配置的开关执行。

逐场景报告在 `results/scenarios/<场景名>/<时间戳>/`；批次汇总在 `results/comparison_suite/<时间戳>/`，打开 `index.html`，同时提供 `comparison.md`、`comparison.csv`、`suite.json`。包含未达标结果与LLM实际建议记录，不保证新流程每次获胜。

## 9．结果怎么找、怎么看

`output.directory` 是结果根目录，每次运行创建独立时间戳子目录，不覆盖旧报告。**以本次控制台打印的路径为准，不是固定打开旧的 `results/project/report.html`。**

| 文件 | 内容 |
|---|---|
| `report.html` | 温度/输出曲线、三组指标、原建议与接受/拒绝状态、达标原因、LLM历史 |
| `pid.json` | 合格PID及形式/单位；没有合格结果则pid为null |
| `summary.json` | 本次实际配置、时间/哈希、辨识、候选、各轮建议、回滚与护栏策略 |
| `metrics.csv`、各组CSV | 指标及响应数据，便于自己绘图 |
| `device_audit.json` | use模式读状态、条件写入、读回和监测记录 |

先看是否满足全部门槛，再比较IAE、末段MAE、调节时间和输出TV。TV是控制输出累计变化，不是能耗。

- `original_zn`：原辨识函数＋Z-N PID，在当前统一框架中运行。
- `corrected_zn`：修正辨识/已知模型＋Z-N PID。
- `legacy_route`：旧版完整连续调优核心，通过接口接入共享对象和控制器，作为保底候选。
- `zn_pi_route`、`simc_route`：Z-N PI、SIMC PI 各自调优，初始表现较慢也不会被提前丢弃。
- `selected`：全部路线调优并统一复测后的最终赢家；不再次调用 LLM。

`original_zn` 仅是未调优的公式参考。启用 LLM 后，各条适用路线分别调优，最终按同一标准跨路线选择；同分保留旧版。界面、历史和导出明确记录旧版是否被选中。共享对象接口不等于运行原版专用温控模型，单次仿真不能证明所有真实对象上更优。

`controller.guardrail_policy=auto` 默认在test初始公式和最终建议阶段检查有效数值/绝对范围，LLM每轮仍限制相对当前仿真参数的增幅；use保留相对实际设备参数及最终总增幅检查。`relative`让test初始公式也检查相对增幅。超限或无效时拒绝整组，不自动裁剪，继续尝试其他候选。通过检查的参数仍须仿真达标；配置的参数范围不等于现场安全边界。见[护栏说明](docs/GUARDRAILS.md)。

## 10．常见问题

| 现象 | 检查方法 |
|---|---|
| `.venv\Scripts\python.exe`不存在 | 是否进入正确项目目录、是否完成创建环境 |
| 缺少openai/requests等模块 | 使用同一 `.venv` 的Python安装 `.[llm]`，避免装到另一个环境 |
| 有密钥但没有LLM历史 | 是否运行正确的配置、保存enabled=true、是不是打开旧报告 |
| `--validate`成功但API失败 | 配置校验不请求API；检查服务地址、模型、密钥、网络，以及LLM历史事件 |
| 修改config.json的模型名没生效 | 本入口从project.json读取服务/模型，只从config.json读取密钥 |
| `pid.json`里pid=null | 没有参数满足全部门槛，查看报告中的具体失败原因 |
| 串口/TCP连接失败 | 接口是否启动、地址/端口或串口是否正确、是否安装依赖并实现协议 |
| use与test结果不同 | use读实际设备状态并以实际参数限制增幅，不是直接写入test推荐 |
| 程序安静一段时间 | LLM请求与重试期间可能没有逐轮输出；整次运行可能包含多组多轮请求 |

## 11．示例结果、开发与归属

仓库的 [`result/`](result/) 是保留的30→100°C参考快照：[`result.html`](result/result.html)为曲线报告，[`result-toread.md`](result/result-toread.md)为中文解读。下载后用浏览器打开HTML，GitHub网页展示的是源码。快照关闭LLM，使用旧relative护栏并选中Z-N PI；新版auto可能选中Z-N PID，请按报告配置区分，不能混用两批结论。

`results/` 是自己的运行历史，不提交Git；`config.json`、`.env`、`*.local.json`同样被忽略。`project.json`等公开模板可以提交，但不要包含真实密钥或生产数据。

项目适合局部稳定的单输入单输出温控对象。支持降温负增益、输出约束、并联/理想式参数及秒/分钟转换；强耦合、积分、不稳定或强非线性对象需要扩展。IMC仅作为SIMC理论背景，没有独立算法实现。

本README的配置与模式指 `pid_project.py`。遗留 `simulator.py`、`tuning_pipeline.py`、MATLAB/硬件入口仍有原配置；上游内容见[原README快照](docs/UPSTREAM_README.md)。

开发者测试与打包：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm,serial,legacy]" --index-url https://pypi.org/simple
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m build
```

测试不调用外部LLM或真实装置。也可使用安装后的命令 `thermal-pid`，但只有激活环境或使用 `.\.venv\Scripts\thermal-pid.exe` 时才保证运行本项目环境；新手建议继续使用本文完整Python路径。

保留 [LICENSE](LICENSE)、[NOTICE](NOTICE) 和上游归属。参见 [CHANGELOG](CHANGELOG.md)、[CONTRIBUTING](CONTRIBUTING.md)。
## 0.4.1 调优路线说明

旧版连续调优、Z-N PID、Z-N PI、SIMC PI 分别调优，再经过统一护栏与全任务仿真进行最终选优。结果明确标注选中路线；同分保留旧版。运行历史统计旧版被选次数及新路线严格改善次数。
`tuning.selection_priority` 支持 `accuracy`（累计误差优先，默认）、`smooth`（平稳优先）、`speed`（响应速度优先）。`include_legacy_route` 默认开启，`compare_original` 仅控制原辨识公式参考显示。旧版不适用、未完成或 LLM 关闭时会说明对照范围。
详细说明见 [桌面使用指南](docs/DESKTOP.md)。更多路线会增加 LLM 调用；保证仅针对本次配置模型和选优标准。

### 完整路线多场景复测

[examples/route_benchmarks](examples/route_benchmarks/README.md) 提供温度、压力、流量、噪声、扰动、慢执行器、两节点传热、CSV辨识和思考模式复验的10份配置及完整运行命令。每次批次生成独立目录，保留全部路线和未达标结果；汇总明确区分旧版完整连续调优、原公式参考与最终选择。

```powershell
.\.venv\Scripts\python.exe scripts/run_comparison_suite.py --scenario-set route_benchmarks --llm on
```

先按前文创建环境、安装 `.[llm]` 并在根目录 `config.json` 保存密钥；关闭API调用时将上面的 `--llm on` 改为 `--llm off`。结果不随源码上传。

0.4.2纳入明确标识的混合候选：修正Z-N初值后使用保留的旧版连续调优核心。`tuning.include_corrected_legacy_route`可控制是否运行，默认开启，LLM关闭时跳过。最终来源分为旧版完整路线、纯新路线、混合路线；报告与历史分别记录，混合路线胜出不会被归因于纯新版LLM策略。

### 超限拒绝与 LLM 重试

`tuning.rounds=4` 表示每条新路线最多仿真4组合法LLM建议；`max_guardrail_retries_per_round=2` 表示每轮被拒绝后最多再请求2次；`max_llm_requests_per_route=12` 限制每条路线的建议接口总调用次数。网络失败重试仍由 `llm.max_attempts` 限制。所有字段可在界面修改，省略采用默认值。

候选超限标记 `REJECTED_GUARDRAIL`，记录原值、允许范围和原因，未仿真、没有性能指标；不代表算法计算错误。其余候选继续运行。仿真失败是 `FAILED_SIMULATION`，仿真完成但效果不合格是 `FAILED_EVALUATION`，通过评价是 `PASSED`。所有候选不可用时仍保存记录，PID为空。旧调优核心也使用当前拒绝护栏，不能把本版旧路线结果当作未修改原项目的独立运行。详细见[护栏与状态说明](docs/GUARDRAILS.md)。
