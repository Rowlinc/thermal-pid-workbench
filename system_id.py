#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modified for Thermal PID Workbench; see CHANGELOG.md and NOTICE.
"""
PID 系统辨识与离线整定工具
================

功能：
1. 从串口读取真实温度数据
2. 阶跃响应分析 - 从温度曲线识别系统参数
3. 传递函数计算 - 估算系统的传递函数
4. 零极点分析 - 分析系统动态特性
5. 稳定性判断 - 基于参数判断系统稳定性
6. Z-N / SIMC PI 整定建议

使用方法：
    # 从串口实时读取数据进行辨识
    python system_id.py --mode live --port /dev/ttyUSB0

    # 从文件读取历史数据
    python system_id.py --mode file --file data.csv

    # 模拟分析
    python system_id.py --mode demo
"""

import csv
import os
import argparse
import time
import math
from typing import List, Dict, Optional


def parse_csv_line(line: str) -> Optional[Dict]:
    """解析串口 CSV 数据"""
    try:
        parts = line.strip().split(",")
        if len(parts) >= 4:
            return {
                "timestamp": float(parts[0]),
                "setpoint" : float(parts[1]),
                "input"    : float(parts[2]), # 温度
                "pwm"      : float(parts[3]),
                "error"    : float(parts[4]) if len(parts) > 4 else 0,
            }
    except Exception:
        pass
    return None


def normalize_time_axis(time_data: List[float], time_unit: str = "s") -> List[float]:
    """将时间轴归一化到从 0 开始的秒单位。"""
    if not time_data:
        return []

    if time_unit not in ("s", "ms"):
        raise ValueError("time_unit must be s or ms; units are never guessed")
    scale = 1000.0 if time_unit == "ms" else 1.0
    normalized = [float(value) / scale for value in time_data]
    if not all(math.isfinite(value) for value in normalized):
        raise ValueError("timestamps must be finite")
    if any(b <= a for a, b in zip(normalized, normalized[1:])):
        raise ValueError("timestamps must be strictly increasing")

    origin = normalized[0]
    return [value - origin for value in normalized]


def first_order_model(tau: float, K: float, theta: float = 0) -> Dict:
    """一阶滞后系统模型"""
    return {
        "type"   : "一阶滞后系统",
        "formula": f"G(s) = {K:.4f} * e^(-{theta:.4f}s) / ({tau:.4f}s + 1)",
        "K"      : K,
        "tau"    : tau,
        "theta"  : theta,
        "poles"  : [-1 / tau] if tau > 0 else [],
        "zeros"  : [],
    }


def analyze_stability(poles: List) -> Dict:
    """分析系统稳定性"""
    unstable = 0
    for p in poles:
        if isinstance(p, complex):
            if p.real >= 0:
                unstable += 1
        else:
            if p >= 0:
                unstable += 1

    return {
        "stable": unstable == 0,
        "reason": "稳定" if unstable == 0 else f"有{unstable}个不稳定极点",
    }


def ziegler_nichols(K: float, tau: float, theta: float, pid_type: str = "PID") -> Dict:
    """Ziegler-Nichols 开环反应曲线法，输出并联式 PID 参数。"""
    if not all(math.isfinite(v) for v in (K, tau, theta)) or K == 0 or tau <= 0 or theta <= 0:
        return {"error": "K 必须非零，tau、theta 必须为正且有限"}

    pid_type = pid_type.upper()

    if pid_type == "P":
        Kp = tau / (K * theta)
        Ti = None
        Td = 0.0
    elif pid_type == "PI":
        Kp = 0.9 * tau / (K * theta)
        Ti = 3.33 * theta
        Td = 0.0
    elif pid_type == "PD":
        Kp = 1.2 * tau / (K * theta)
        Ti = None
        Td = 0.5 * theta
    else:
        pid_type = "PID"
        Kp = 1.2 * tau / (K * theta)
        Ti = 2.0 * theta
        Td = 0.5 * theta

    converted = ideal_to_parallel(Kp, Ti, Td)
    Ki, Kd = converted["Ki"], converted["Kd"]

    formula = f"Kp={Kp:.3f}, Ki={Ki:.3f}, Kd={Kd:.3f}"
    if Ti:
        formula += f" (Ti={Ti:.3f}s"
        formula += f", Td={Td:.3f}s)" if Td else ")"
    elif Td:
        formula += f" (Td={Td:.3f}s)"

    return {
        "type"           : pid_type,
        "controller_form": "parallel",
        "Kp"             : Kp,
        "Ki"             : Ki,
        "Kd"             : Kd,
        "Ti"             : Ti,
        "Td"             : Td,
        "formula"        : formula,
    }


def ideal_to_parallel(Kp: float, Ti: Optional[float] = None, Td: float = 0.0) -> Dict:
    """Seconds-based ideal/ISA gains -> continuous parallel gains (not series PID)."""
    if not math.isfinite(Kp) or not math.isfinite(Td) or Td < 0:
        raise ValueError("Kp must be finite and Td must be finite/nonnegative")
    if Ti is not None and (not math.isfinite(Ti) or Ti <= 0):
        raise ValueError("Ti must be positive/finite or None (integral disabled)")
    return {"controller_form": "parallel", "Kp": Kp,
            "Ki": Kp / Ti if Ti is not None else 0.0, "Kd": Kp * Td,
            "Ti": Ti, "Td": Td}


def parallel_to_ideal(Kp: float, Ki: float, Kd: float) -> Dict:
    """Continuous parallel gains -> ideal/ISA Kp, Ti, Td (seconds)."""
    if not all(math.isfinite(v) for v in (Kp, Ki, Kd)):
        raise ValueError("gains must be finite")
    if Kp == 0 and (Ki != 0 or Kd != 0):
        raise ValueError("pure I/D cannot be represented by finite ideal PID parameters")
    Ti = Kp / Ki if Ki != 0 else None
    Td = Kd / Kp if Kp != 0 else 0.0
    ideal_to_parallel(Kp, Ti, Td)  # Validate sign consistency and positive times.
    return {"controller_form": "ideal", "Kp": Kp, "Ti": Ti, "Td": Td}


def simc_pi(K: float, tau: float, theta: float, lambda_: Optional[float] = None) -> Dict:
    """SIMC PI for a FOPDT model; lambda is the closed-loop time constant in seconds."""
    if not all(math.isfinite(v) for v in (K, tau, theta)) or K == 0 or tau <= 0 or theta < 0:
        return {"error": "Require finite K != 0, tau > 0, theta >= 0"}
    # Conservative offline starting choice, not a plant-specific safety guarantee.
    lambda_ = max(theta, tau / 3.0) if lambda_ is None else lambda_
    if not math.isfinite(lambda_) or lambda_ <= 0:
        return {"error": "lambda must be positive and finite"}
    Kp = tau / (K * (lambda_ + theta))
    Ti = min(tau, 4.0 * (lambda_ + theta))
    return {**ideal_to_parallel(Kp, Ti), "type": "PI", "method": "SIMC",
            "lambda": lambda_, "formula": f"Kp={Kp:.6g}, Ki={Kp / Ti:.6g}, Kd=0 (Ti={Ti:.6g}s)"}


def tuning_candidates(K: float, tau: float, theta: float, lambda_: Optional[float] = None) -> Dict:
    return {"ZN_PID": ziegler_nichols(K, tau, theta, "PID"),
            "ZN_PI": ziegler_nichols(K, tau, theta, "PI"),
            "SIMC_PI": simc_pi(K, tau, theta, lambda_)}


def system_identify(
    time_data: List[float],
    temp_data: List[float],
    pwm_data : Optional[List[float]] = None,
    *, time_unit: str = "s", lambda_: Optional[float] = None,
) -> Dict:
    """
    系统辨识主函数 - 从阶跃响应数据识别系统参数

    Args:
        time_data: 时间序列 (秒)
        temp_data: 温度序列 (°C)
        pwm_data: PWM 输入序列 (可选)
        time_unit: 明确指定 s 或 ms，默认 s，禁止根据总时长猜测
        lambda_: SIMC 闭环时间常数 (秒)

    Returns:
        系统参数字典
    """
    n = len(time_data)
    if n < 5:
        return {"error": "数据点太少，至少需要5个"}

    if len(temp_data) != n or (pwm_data is not None and len(pwm_data) != n):
        return {"error": "时间、温度、输入序列长度必须一致"}
    try:
        time_data = normalize_time_axis(time_data, time_unit)
        temp_data = [float(v) for v in temp_data]
        if pwm_data is not None:
            pwm_data = [float(v) for v in pwm_data]
        if not all(math.isfinite(v) for v in temp_data + (pwm_data or [])):
            return {"error": "温度和输入必须为有限值"}
    except (ValueError, TypeError) as exc:
        return {"error": str(exc)}
    warnings = []
    step_index = 0
    if pwm_data is not None:
        tolerance = max(1e-9, (max(pwm_data) - min(pwm_data)) * 1e-6)
        changes = [i for i in range(1, n) if abs(pwm_data[i] - pwm_data[i-1]) > tolerance]
        if len(changes) != 1:
            return {"error": "辨识要求单次开环输入阶跃，包含阶跃前基线；拒绝恒定或多次变化的输入"}
        step_index = changes[0]
    if n - step_index < 5:
        return {"error": "阶跃后至少需要5个数据点"}

    # 1. 计算稳态增益 K
    tail_count = min(10, max(1, (n - step_index) // 5))
    steady_temp = sum(temp_data[-tail_count:]) / tail_count
    initial_temp = sum(temp_data[:step_index]) / step_index if step_index else temp_data[0]
    delta_temp   = steady_temp - initial_temp
    if abs(delta_temp) < 1e-9:
        return {"error": "阶跃响应变化不足，无法完成辨识"}

    # 根据 PWM 计算增益 (假设 PWM 变化)
    if pwm_data is not None:
        delta_pwm = pwm_data[-1] - pwm_data[0]
        K = delta_temp / delta_pwm
    else:
        K = delta_temp / 255.0  # 默认假设满 PWM
        warnings.append("未提供输入：假定 t=0 时输入从 0 阶跃到 255，增益仅供演示")

    # 2. 计算时间常数 tau (达到 63.2% 稳态的时间)
    def crossing(fraction):
        response = [(v - initial_temp) / delta_temp for v in temp_data]
        for i in range(max(1, step_index), n):
            if response[i-1] < fraction <= response[i]:
                return time_data[i-1] + (time_data[i] - time_data[i-1]) * (
                    fraction - response[i-1]) / (response[i] - response[i-1])
        return None

    t5, t63 = crossing(0.05), crossing(0.632)
    if t5 is None or t63 is None or t63 <= t5:
        return {"error": "无法找到有效的 5%/63.2% 阶跃响应交点"}
    # t_f = step_time + theta - tau*log(1-f), rather than tau=t63.
    tau = (t63 - t5) / (math.log(0.95) - math.log(0.368))
    theta_raw = t5 - time_data[step_index] + tau * math.log(0.95)
    theta = max(0.0, theta_raw)
    if theta_raw < 0:
        warnings.append("估计延迟小于零，截断为 0；检查采样分辨率与 FOPDT 假设")
    if abs(temp_data[-1] - temp_data[-tail_count]) > abs(delta_temp) * 0.01:
        warnings.append("响应末段仍在变化，可能未达到稳态；增益和整定参数存在偏差")
    predictions = [initial_temp + delta_temp * (1 - math.exp(
        -max(0.0, t - time_data[step_index] - theta) / tau)) for t in time_data]
    fit_rmse = math.sqrt(sum((y - p)**2 for y, p in zip(temp_data, predictions)) / n)
    if fit_rmse > abs(delta_temp) * 0.05:
        warnings.append("FOPDT 拟合 RMSE 超过响应幅度的 5%，检查模型假设、噪声与扰动")

    # 4. 构建模型
    model     = first_order_model(tau, K, theta)

    # 5. 稳定性分析
    stability = analyze_stability(model["poles"])

    # 6. Z-N 整定建议
    if theta > 0 and tau > 0 and K != 0:
        znpid = ziegler_nichols(K, tau, theta, "PID")
        znpi  = ziegler_nichols(K, tau, theta, "PI")
    else:
        znpid = {"error": "参数异常，无法计算"}
        znpi  = {"error": "参数异常，无法计算"}

    return {
        "model"          : model,
        "stability"      : stability,
        "ziegler_nichols": {"PID": znpid, "PI": znpi},
        "tunings": tuning_candidates(K, tau, theta, lambda_),
        "warnings": warnings,
        "fit_rmse_c": fit_rmse,
        "summary"        : {
            "gain_K"           : K,
            "time_constant_tau": tau,
            "delay_theta"      : theta,
            "steady_temp"      : steady_temp,
            "initial_temp"     : initial_temp,
            "temp_rise"        : delta_temp,
        },
    }


def extract_initial_pid(result: Dict, pid_type: str = "PID", method: str = "ZN") -> Optional[Dict[str, float]]:
    """Extract a parallel-form PID suggestion from a system identification result."""
    if not result or "error" in result:
        return None

    tuning_table = result.get("ziegler_nichols", {})
    candidate = (tuning_table.get(pid_type.upper()) if method.upper() == "ZN"
                 else result.get("tunings", {}).get(f"{method.upper()}_{pid_type.upper()}"))
    if not isinstance(candidate, dict) or "error" in candidate:
        return None

    try:
        return {
            "p": float(candidate.get("Kp", 0.0)),
            "i": float(candidate.get("Ki", 0.0)),
            "d": float(candidate.get("Kd", 0.0)),
        }
    except (TypeError, ValueError):
        return None


def print_report(result: Dict):
    """打印分析报告"""
    if "error" in result:
        print(f"❌ 错误: {result['error']}")
        return

    m     = result["model"]
    s     = result["summary"]
    znpid = result["ziegler_nichols"]["PID"]
    znpi  = result["ziegler_nichols"]["PI"]

    print("\n" + "=" * 60)
    print("               🔧 系统辨识报告")
    print("=" * 60)

    print("\n📊 辨识结果:")
    print(f"   初始温度: {s['initial_temp']:.1f}°C")
    print(f"   稳态温度: {s['steady_temp']:.1f}°C")
    print(f"   温升: {s['temp_rise']:.1f}°C")
    print(f"   增益 K: {s['gain_K']:.4f}°C/PWM")
    print(f"   时间常数 τ: {s['time_constant_tau']:.4f}秒")
    print(f"   延迟 θ: {s['delay_theta']:.4f}秒")
    print(f"   FOPDT 拟合 RMSE: {result.get('fit_rmse_c', 0):.4f}°C")
    print("\n整定候选（并联式，时间单位为秒）:")
    for name, candidate in result.get("tunings", {}).items():
        print(f"   {name}: {candidate.get('formula', candidate.get('error'))}")
    for warning in result.get("warnings", []):
        print(f"   WARNING: {warning}")

    print("\n📐 系统传递函数:")
    print(f"   {m['formula']}")

    print(f"\n📍 极点: {m['poles']}")
    print(f"   零点: {m['zeros']}")

    st = result["stability"]
    print(f"\n✅ 辨识模型极点稳定性: {'✓ 稳定' if st['stable'] else '✗ 不稳定'} ({st['reason']})；不代表闭环稳定")

    if "error" not in znpid:
        print("\n💡 Ziegler-Nichols 整定建议:")
        print("   注意: 以下 Ki/Kd 已转化为并联式 PID (u=Kp*e + Ki∫e dt + Kd*de/dt)")
        print(
            f"   PID: Kp={znpid['Kp']:.3f}, Ki={znpid['Ki']:.3f}, Kd={znpid['Kd']:.3f}"
        )
        if znpid.get("Ti"):
            print(f"        Ti={znpid['Ti']:.3f}s, Td={znpid['Td']:.3f}s")
        print(f"   PI:  Kp={znpi['Kp']:.3f}, Ki={znpi['Ki']:.3f}")
        if znpi.get("Ti"):
            print(f"        Ti={znpi['Ti']:.3f}s")

    print("\n" + "=" * 60)


def read_from_serial(port: str, baud: int = 115200, duration: float = 10.0) -> Dict:
    """
    从串口读取数据进行系统辨识

    Args:
        port: 串口名称
        baud: 波特率
        duration: 读取时长(秒)

    Returns:
        辨识结果
    """
    try:
        import serial
    except ImportError:
        return {
            "error": "未安装 pyserial，请运行: pip install pyserial 后再使用 --mode live",
        }

    print(f"🔌 正在连接串口 {port} @ {baud} baud...")

    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2)  # 等待连接稳定

        time_data  = []
        temp_data  = []
        pwm_data   = []
        start_time = None
        deadline   = time.time() + duration

        print(f"📡 开始读取数据 (时长: {duration}秒)...")
        print("   按 Ctrl+C 提前停止")

        while time.time() < deadline:
            try:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    data = parse_csv_line(line)
                    if data and data["input"] > 0:
                        if start_time is None:
                            start_time = time.time()

                        elapsed = time.time() - start_time
                        time_data.append(elapsed)
                        temp_data.append(data["input"])
                        pwm_data.append(data["pwm"])

                        # 实时显示
                        print(
                            f"\r   t={elapsed:.1f}s T={data['input']:.1f}°C PWM={data['pwm']:.0f}",
                            end="",
                        )

            except Exception:
                continue

        print("\n\n✅ 数据读取完成")

        if len(time_data) < 10:
            return {"error": f"数据点太少 ({len(time_data)})，至少需要10个"}

        return system_identify(time_data, temp_data, pwm_data)

    except serial.SerialException as e:
        return {"error": f"串口错误: {e}"}
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def read_from_file(path: str, time_unit: str = "ms", lambda_: Optional[float] = None) -> Dict:
    """从 CSV 文件读取数据进行系统辨识。"""
    if not path:
        return {"error": "未提供文件路径"}
    if not os.path.exists(path):
        return {"error": f"文件不存在: {path}"}

    time_data: List[float] = []
    temp_data: List[float] = []
    pwm_data : List[float] = []

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(1024)
            handle.seek(0)

            if any(
                name in sample.lower()
                for name in ("timestamp", "setpoint", "input", "pwm")
            ):
                reader = csv.DictReader(handle)
                for row in reader:
                    try:
                        timestamp = float(row["timestamp"])
                        temperature = float(row.get("input", row.get("temperature")))
                        pwm = float(row["pwm"])
                        time_data.append(timestamp)
                        temp_data.append(temperature)
                        pwm_data.append(pwm)
                    except (KeyError, TypeError, ValueError):
                        continue
            else:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    data = parse_csv_line(line)
                    if not data:
                        continue
                    time_data.append(data["timestamp"])
                    temp_data.append(data["input"])
                    pwm_data.append(data["pwm"])
    except OSError as exc:
        return {"error": f"文件读取失败: {exc}"}

    if len(time_data) < 5:
        return {"error": f"文件中的有效数据点太少 ({len(time_data)})，至少需要5个"}

    return system_identify(time_data, temp_data, pwm_data, time_unit=time_unit, lambda_=lambda_)


def demo(lambda_: Optional[float] = None):
    """演示模式"""
    # Illustrative thermal FOPDT: K=.8, tau=300s, theta=20s; step at 10s.
    time_data = list(range(0, 3011, 2))
    temp_data = [70 + 8*(1-math.exp(-max(0,t-30)/300)) for t in time_data]
    pwm_data = [0.0 if t < 10 else 10.0 for t in time_data]

    print("[demo] 分析模拟数据")
    result = system_identify(time_data, temp_data, pwm_data, lambda_=lambda_)
    print_report(result)


def parse_inline_data(data_str: str) -> None:
    time_data = []
    temp_data = []
    pwm_data  = []
    for item in data_str.strip().split():
        if "," in item:
            parts = item.split(",")
            if len(parts) >= 3:
                time_data.append(float(parts[0]) / 1000)  # ms -> s
                temp_data.append(float(parts[1]))
                pwm_data.append(float(parts[2]))

    if len(time_data) >= 5:
        result = system_identify(time_data, temp_data, pwm_data)
        print_report(result)
    else:
        print("数据点太少，至少需要5个")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PID 系统辨识工具 - 硬件版")
    parser.add_argument(
        "--mode",
        choices=["demo", "live", "file", "stdin"],
        default="demo",
        help="模式: demo=演示, live=串口, file=文件, stdin=标准输入",
    )
    parser.add_argument("--port", type=str, default="/dev/ttyUSB0", help="串口名称")
    parser.add_argument("--baud", type=int, default=115200, help="波特率")
    parser.add_argument("--duration", type=float, default=10.0, help="读取时长(秒)")
    parser.add_argument("--data", type=str, help="内联数据: 时间,温度,PWM 空格分隔")
    parser.add_argument("--file", type=str, help="CSV 数据文件路径")
    parser.add_argument("--time-unit", choices=["s", "ms"], default="ms", help="CSV 时间单位，项目导出默认 ms")
    parser.add_argument("--lambda", dest="lambda_", type=float, help="SIMC 闭环时间常数（秒）")

    args = parser.parse_args()

    if args.data:
        parse_inline_data(args.data)
    elif args.mode == "demo":
        demo(args.lambda_)
    elif args.mode == "live":
        result = read_from_serial(args.port, args.baud, args.duration)
        print_report(result)
    elif args.mode == "file":
        result = read_from_file(args.file or args.data, args.time_unit, args.lambda_)
        print_report(result)
    elif args.mode == "stdin":
        import sys
        data_str = sys.stdin.read().strip()
        if data_str:
            parse_inline_data(data_str)
