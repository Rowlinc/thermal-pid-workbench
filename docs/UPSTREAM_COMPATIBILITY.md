# 原项目能力保留与接入

对照源是用户下载的不可变 `llm-pid-tuner-dev.zip`，包含 82 个文件；源码目录中的已修改副本不作为上游依据。
原文件全部保留，原命令行入口继续可用。新增统一命令和 GUI 工作流，补全安装依赖和打包资源。
新增业务主流程不替代原串口或 Simulink 协议。

| 原能力 | 当前入口 |
|---|---|
| 原交互启动器、命令行与 TUI | `thermal-pid upstream`；原 `launcher.py` 保留 |
| Python 加热仿真、初始化、LLM、暂停/继续/停止 | 应用选择“原项目 Python 温控仿真”；`thermal-pid simulate` |
| 原串口 CSV 硬件调优与 DEMO | 应用选择“原项目串口硬件调优”；`thermal-pid hardware COM5` |
| generic_serial_csv / stm32f407_openmv / mspm0_datavision | 应用原配置中的硬件协议选择；上游协议实现保留 |
| Simulink 主与第二 PID、分立 P/I/D、信号发现与日志、目标模块 | 应用选择 Simulink 并填写原配置；原 `sim/simulink_*` 与控制器 I/O 模块保留 |
| 原阶跃辨识入口：文件、live、demo | `thermal-pid identify`；通用工作台另提供 CSV 与离线辨识 |
| API SDK、HTTP 回退、提供商、代理、流式响应 | 原实现保留；GUI 使用统一 LLM 服务配置和原代理字段 |
| 参数护栏、历史最佳、回滚、多个控制器 | 原调优引擎保留；新主流程增加完整任务仿真检查 |
| 原 CSV 导出、诊断、多语言与字体、固件、打包规格 | 原文件保留，安装/打包资源补齐；诊断可用 `thermal-pid doctor` |

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[full,desktop]"
.\.venv\Scripts\thermal-pid.exe commands
```

原启动器仍使用原 `config.json` 的完整设置。通用 `pid_project.py` 使用 `project.json` 的服务设置，私有密钥文件只提供密钥。
GUI 原流程使用统一 LLM 服务配置，其他原参数在“原项目完整流程”编辑；导入原 `config.json` 时服务设置与密钥会映射到正确位置。
界面关闭 LLM 时原流程保持参数并记录响应，不会偷偷调用 API。

验证包含原 ZIP 测试合约（327 项 + 11 子测试）、现有项目回归、GUI 原 Python 与 DEMO 工作流、Simulink 分发/控制模拟和真实浏览器操作。
实际 MATLAB/Simulink 连接和真实硬件没有在本机完成验证；需要用户安装兼容依赖并核对设备协议。
窗口化应用不直接显示原 TUI，但保留原入口供终端使用；应用里提供对应配置、曲线、日志与暂停控制。
通用工作台只自动辨识 FOPDT；该限制不删除原 Simulink 或硬件能力。
