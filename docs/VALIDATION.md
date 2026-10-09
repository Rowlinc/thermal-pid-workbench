# 验证范围与复现方式

0.4.0 在 Windows 本机通过 440 项项目测试与 11 个子测试；原 ZIP 合约另通过 327 项与 11 个子测试。
真实 Edge 浏览器验证了温度/压力/流量/液位四个场景、应用内历史查看与配置载入、CSV 上传与映射、密钥隐藏和方案保存。
原 Python 与串口 DEMO 界面工作流已运行；Simulink 分发、控制与原协议采用模拟测试。原生 Windows WebView2 窗口也已启动并验证表单加载。
完整 LLM 路径采用本机 HTTP 流式 API 模拟服务，覆盖 SDK、请求、解析、护栏与三组仿真；本批验证没有调用真实装置。
复现浏览器操作可安装 `playwright` 后执行 `scripts/smoke_desktop.py`（使用本机 Edge）；安装包构建见 [桌面说明](DESKTOP.md)。

当前入口是 `pid_project.py`，完整安装与四种运行组合见 [README](../README.md)。测试覆盖配置与注释解析、参数单位转换、FOPDT/CSV、加热/降温、护栏阶段、LLM回退、模拟设备、TCP通信、条件写入、读回异常与版本冲突。测试中的LLM使用模拟响应，不连接真实生产装置。

## 自动化验证

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,llm,serial,legacy]"
.\.venv\Scripts\python.exe -m pytest tests -q
```

GitHub Actions配置包含Windows/Linux、Python3.10/3.12；远端是否通过以实际执行记录为准。已验证基础流程只依赖标准库、可安装打包，发布前检查实际密钥未进入Git和发布包。

## 实验记录在哪里

- `result/`是唯一保留的公开参考快照，采用旧relative护栏；结果和解读见[示例报告](../result/result.html)、[示例解读](../result/result-toread.md)。
- 当前运行记录在`results/<任务>/<时间戳>/`，不提交Git。报告保存实际配置、配置哈希、策略、候选和建议历史。
- 护栏修改前后的差异与解释见[GUARDRAILS](GUARDRAILS.md)。对比时核对模型、任务、护栏与LLM开关，不混合不同批次数据。

真实DeepSeek API已验证能返回有效建议并进入仿真；成功调用不代表必然改善控制。当前框架中的original_zn只对照原辨识算法＋Z-N PID，不是完整上游项目独立运行。物理对象辨识精度与装置投产仍需现场验证。
