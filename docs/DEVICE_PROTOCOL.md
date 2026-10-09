# 使用模式与设备接入

`mode=test` 进行辨识、参数整定、LLM 可选优化和仿真，只输出文件，程序不创建设备接口。
`mode=use` 完成同样的离线验证，再通过适配器写入**已经达标的参数和目标温度**，读回核验并进行一段有界监测。连续的采样和闭环控制仍由设备控制器承担。

这里的“use”表示设备集成工作流已经实现，不表示任意 DCS/PLC 接上即可投产。随包验证的是模拟适配器及本机 TCP 协议。真实装置需完成协议映射、量纲核对、模型验证和现场调试；现场 DCS/PLC/SIS 的已有保护继续独立负责过程安全。

## 先运行可复现的接口联调

先按[README安装流程](../README.md)创建`.venv`并安装基础项目。以下命令均从项目根目录运行；串口额外安装`.[serial]`，LLM额外安装`.[llm]`。密钥放在根目录`config.json`，任务配置负责LLM开关、地址和模型。

```powershell
.\.venv\Scripts\python.exe pid_project.py --config examples/use_simulated.json
```

查看 `results/use_simulated/<时间戳>/device_audit.json`（以控制台打印的本次目录为准），其中应有初始状态、写入回执、读回确认、观察记录和完成事件。这个适配器完全在内存中运行，模拟时间加速推进。

要测试真正的 TCP 序列化/收发，在两个终端依次运行：

```powershell
# 终端一：模拟设备网关，只监听本机
.\.venv\Scripts\python.exe -m thermal_pid.gateway_demo --config examples/use_tcp_local.json --port 9100
# 终端二：客户端使用 TCP 连接
.\.venv\Scripts\python.exe pid_project.py --config examples/use_tcp_local.json
```

演示网关不是驱动程序或生产服务。它只控制内部模拟对象，关闭终端即可结束。没有真实设备寄存器地址被预设在项目中。

## 使用模式执行顺序

1. 检查配置并完成离线对象辨识。`probe` 也只作用于离线模型，不会对现场输出做阶跃。
2. 连接明确指定的适配器，读取当前 PID、温度、输出、目标及配置版本。
3. 核验对象标识、数据新鲜度、采样周期、单位、输出范围、滤波、初始化及抗饱和语义；从设备状态设定仿真初值和 PID 护栏参照。
4. 在同一任务上比较 Z-N/SIMC，可选 LLM 离线调优，保留最佳。最后再次按原设备参数检查最终增幅，并验证实际将要写入的参数。
5. 无合格参数则停止。写前再次读取，配置版本或初始工况变化超过允许值则拒绝写入。
6. 用 `expected_revision` 条件写入。网关必须原子地比较版本、校验参数、应用 PID/设定值和初始化策略、生成新版本。
7. 独立读回参数和版本，确认后监测 `monitor_duration_s`。温度越界、状态过期、通信失败或外部修改都会报错并记录。
8. 若已确认本程序获得新的配置版本且允许恢复，异常时尝试恢复原 PID/目标。只有该版本仍属于本程序才恢复，避免覆盖他人修改；恢复也要求回执和读回。

如果写入后断线或回执丢失，应用状态可能不确定；程序不宣称成功，也不会猜测一个版本号去覆盖现场。此时依靠设备自身的保护、看门狗和操作规程。恢复失败会保留在日志中。有界监测结束后程序退出，不能作为永久驻留的安全监控。

## 适配器

| `device.adapter` | 配置 | 用途 |
|---|---|---|
| `disabled` | 默认 | 测试模式 |
| `simulated` | 无外设 | 本机集成自测 |
| `tcp` | `host`、`port`、`timeout_s` | UTF-8 JSONL 网关 |
| `serial` | `serial_port`、`baud`、`timeout_s` | 同样的 JSONL 网关，需 `pip install .[serial]` |
| `custom` | `custom_factory=module:function` | 自行实现 Modbus/OPC UA/厂商 SDK 等映射 |

TCP 适配器提供普通 TCP，不带 TLS/认证。它用于受管理的本机/控制网络网关；需要认证和加密时，在已验证的自定义适配器或网关层实现。程序没有任意网络扫描或自动发现设备。

真实使用需在自己的配置中显式设置 `mode=use` 和 `device.write_enabled=true`。模板中的限幅和温度门槛都是示例，需要根据对象设置。

## JSONL 协议 v1

一行一个 JSON 对象，以换行结束。最多 64 KiB/响应。所有时刻为 Unix 秒；`revision` 为非空字符串或整数，只在控制配置变更时递增/改变，普通采样不得改变它。网关时钟需要同步。每个响应必须回传请求 ID、协议版本和 `ok`。

读状态请求：

```json
{"protocol_version":1,"request_id":"request-123","operation":"read_state","object_id":"temperature-loop-1"}
```

响应示意（时间戳需为当前值）：

```json
{
  "protocol_version":1, "request_id":"request-123", "ok":true,
  "state": {
    "object_id":"temperature-loop-1", "revision":12, "timestamp_s":1791432000.0,
    "pid":{"controller_form":"parallel","parameter_time_unit":"s","Kp":1.0,"Ki":0.01,"Kd":0.0},
    "sample_time_s":1.0, "output_unit":"%", "output_min":0.0, "output_max":100.0,
    "output_bias":0.0, "max_rate_per_s":10.0,
    "derivative_on":"measurement", "derivative_filter_s":0.5,
    "anti_windup":"conditional", "initialization":"zero",
    "temperature_c":30.0, "output":0.0, "setpoint_c":30.0
  }
}
```

写入请求：

```json
{"protocol_version":1,"request_id":"request-124","operation":"apply_parameters",
 "object_id":"temperature-loop-1","expected_revision":12,
 "pid":{"controller_form":"parallel","parameter_time_unit":"s","Kp":2.4,"Ki":0.02,"Kd":0.0},
 "setpoint_c":100.0,"initialization":"zero"}
```

回执：`{"protocol_version":1,"request_id":"request-124","ok":true,"revision":13}`。
拒绝则返回 `ok:false`。网关必须只在完整事务成功后返回 `ok:true`，不能仅表示“命令已收到”。

设备需按 `thermal_pid/control.py` 描述的控制器语义运行。`zero` 初始化复位积分和微分状态；`tracking` 初始化积分为当前输出减偏置减新比例项，并复位微分历史。其他设备控制律、串联 PID、增量式系数或不同积分策略，需要额外适配并验证，不能仅伪造状态字段为“匹配”。

## 自定义适配器契约

工厂 `create_device(config)` 返回以下对象：

```python
class Device:
    def read_state(self):
        # Return the state dictionary above, using actual device readings.
        ...

    def apply(self, pid, setpoint, expected_revision):
        # Atomic compare-and-set, actual write, then acknowledge new revision.
        # Honor config['controller']['initialization'].
        return {"ok": True, "revision": "new-version"}

    def close(self):
        ...
```

工厂模块必须可导入，并且是使用者信任的本地代码。自定义模型同理：工厂接收配置，返回有 `temperature` 属性和 `step(output, dt_s)` 方法的对象。每次创建必须重置状态；示例见 `examples/custom_model.py`。
