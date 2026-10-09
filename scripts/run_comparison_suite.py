"""Run configurable scenarios and retain all arms, including failed trials."""

import argparse
import csv
import html
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pid_project import create_run_directory, run
from thermal_pid.config import load_project, ConfigError


def save_summary(directory, rows, failures, planned):
    payload = {"planned_scenarios": planned, "completed_scenarios": len({r['scenario'] for r in rows}),
               "rows": rows, "execution_failures": failures}
    (directory / "suite.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if rows:
        with (directory / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    completed = payload['completed_scenarios']
    lines = ["# 多场景温控流程对比", "", f"计划 {planned} 个场景，已完成 {completed} 个场景，执行失败 {len(failures)} 个。", "",
             "每个场景内三组使用相同对象、任务、控制器、参数护栏及评价门槛。original_zn 是统一框架中的原辨识＋Z-N PID 基线，不是完整原项目独立运行结果。", "",
             "本批次每个场景只运行一次；模型由配置指定，未使用现场历史数据。启用 LLM 时允许每组最多 4 轮建议，实际调用情况见下表及各场景报告。运行时间含报告生成，未采集 API token 费用。", "",
             "## 最终结果（包含未达标组）", "",
             "| 场景 | 对照组 | 起点 | 达标 | 峰值°C | 超调% | 调节s | IAE | 末段MAE°C | 输出TV | 有效LLM建议/记录数 | 报告 |",
             "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        settle = '未稳定' if r['settling_time_s'] is None else f"{r['settling_time_s']:.0f}"
        lines.append(f"| {r['scenario']} | {r['arm']} | {r['initial_method']} | {'是' if r['eligible'] else '否'} | {r['max_temperature_c']:.3f} | {r['overshoot_pct']:.3f} | {settle} | {r['iae_c_s']:.2f} | {r['tail_mae_c']:.5f} | {r['output_tv']:.2f} | {r['valid_llm_suggestions']}/{r['llm_history_records']} | [查看]({r['report']}) |")
    lines += ["", "## 各组达标情况", "", "这是本批次场景计数，不是统计置信区间；尚未完成或执行失败的场景不计入已完成场景分母。", ""]
    for arm in ('original_zn', 'corrected_zn', 'selected'):
        group = [r for r in rows if r['arm'] == arm]
        lines.append(f"- {arm}：{sum(r['eligible'] for r in group)}/{len(group)} 个场景达标。")
    lines += ["", "## 选优组相对原辨识基线的变化", "", "仅列原始指标和相对变化，不将未达标方案称为成功。正的降低比例表示该数值变小；IAE/TV 变小也不能替代达标检查。", "",
              "| 场景 | IAE降低% | 输出TV降低% | 调节时间降低% | 起点→最终IAE变化 |", "|---|---:|---:|---:|---|"]
    for scenario in dict.fromkeys(r['scenario'] for r in rows):
        group = {r['arm']: r for r in rows if r['scenario'] == scenario}
        if 'original_zn' not in group or 'selected' not in group:
            continue
        b, n = group['original_zn'], group['selected']
        def reduction(key):
            return '不适用' if b[key] is None or n[key] is None or b[key] == 0 else f"{(b[key]-n[key])/b[key]*100:.2f}"
        lines.append(f"| {scenario} | {reduction('iae_c_s')} | {reduction('output_tv')} | {reduction('settling_time_s')} | {n['initial_iae_c_s']:.2f}→{n['iae_c_s']:.2f} |")
    lines += ["", "## 解释限制", "",
              "- original_zn 与 corrected_zn 的起点均为 Z-N PID；selected 可以选择 Z-N PID、Z-N PI、SIMC PI。应结合 initial_method 判断优势来自何处，不能把选中 Z-N PI 的结果归因于 SIMC。",
              "- valid_llm_suggestions 表示记录到可仿真的参数建议，不代表总网络请求数；llm_history_records 也可能包含 API 不可用或格式失败。每个场景的 summary.json 保留完整历史。",
              "- 最终与初始参数一致时，LLM 未提供被最终采纳的参数改进；LLM 的文字预测不作为性能证据。",
              "- 输出 TV 是累计控制变化，不是能耗；报告同时保留饱和时间占比，避免只展示改善指标。",
              "- 不同场景的任务幅度、持续时间或对象条件不同；应在场景内部比较三组，不能直接比较不同场景的原始 IAE。",
              "- 后续需完整原项目独立对照、多次重复 LLM 测试、噪声与模型失配测试，才能评估稳定的项目级优势。", ""]
    if failures:
        lines += ["## 执行失败", ""] + [f"- {f['config']}：{f['error_type']}。" for f in failures]
    (directory / "comparison.md").write_text('\n'.join(lines), encoding="utf-8")
    cells = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(r[k]))}</td>' for k in ('scenario','arm','initial_method','eligible','max_temperature_c','settling_time_s','iae_c_s','tail_mae_c','output_tv')) + f'<td><a href="{html.escape(r["report"], quote=True)}">响应曲线和LLM历史</a></td></tr>' for r in rows)
    header = ''.join(f'<th>{v}</th>' for v in ('场景','组别','起点','达标','峰值°C','调节s','IAE','末段MAE°C','输出TV','报告'))
    page = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>多场景温控对比</title><style>body{{font-family:system-ui;padding:24px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:8px}}th{{background:#edf2f7}}</style><h1>多场景温控对比</h1><p>已完成 {completed}/{planned} 个场景。<a href="comparison.md">完整解释</a> · <a href="comparison.csv">CSV数据</a></p><p>原辨识基线在当前统一框架中运行，不等同于完整原项目独立测试。包含所有已完成场景的未达标结果。</p><table><tr>{header}</tr>{cells}</table></html>'
    (directory / "index.html").write_text(page, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configs', nargs='*', help='configuration paths; default: examples/scenarios/*.json')
    parser.add_argument('--llm', choices=['configured', 'on', 'off'], default='configured')
    args = parser.parse_args(argv)
    configs = [Path(p).resolve() for p in args.configs] if args.configs else sorted((ROOT / 'examples/scenarios').glob('*.json'))
    if not configs:
        parser.error('no scenario configurations found')
    directory = create_run_directory(ROOT / 'results/comparison_suite')
    print(f'Suite directory: {directory}', flush=True)
    rows, failures = [], []
    for index, path in enumerate(configs, 1):
        print(f'[{index}/{len(configs)}] Starting {path.stem}', flush=True)
        started = time.monotonic()
        try:
            cfg, base = load_project(path)
            if cfg['mode'] != 'test':
                raise ConfigError('comparison suite only accepts test mode')
            if args.llm != 'configured':
                cfg['llm']['enabled'] = args.llm == 'on'
            result, output = run(cfg, base)
            elapsed = time.monotonic() - started
            for arm in result['arms']:
                m = arm['final']['metrics']
                history = arm['history']
                rows.append(dict(scenario=cfg['name'], arm=arm['name'], initial_method=arm['initial_method'],
                                 eligible=m['eligible'], failures=','.join(m['failures']),
                                 **{k:m[k] for k in ('max_temperature_c','overshoot_pct','settling_time_s','iae_c_s','tail_mae_c','output_tv','saturation_fraction')},
                                 **arm['final']['pid'], initial_iae_c_s=arm['initial']['metrics']['iae_c_s'],
                                 initial_eligible=arm['initial']['metrics']['eligible'],
                                 parameters_changed=arm['initial']['pid'] != arm['final']['pid'],
                                 guardrail_policy=cfg['controller']['guardrail_policy'],
                                 initial_guard_policy=result['guardrail_context']['initial_candidates'],
                                 final_guard_policy=result['guardrail_context']['final_delivery'],
                                 llm_enabled=cfg['llm']['enabled'], llm_history_records=len(history),
                                 valid_llm_suggestions=sum('applied_pid' in h for h in history),
                                 scenario_elapsed_s=round(elapsed, 3), config_sha256=result['config_sha256'],
                                 report=Path(os.path.relpath(output / 'report.html', directory)).as_posix()))
                print(f"  {arm['name']}: eligible={m['eligible']} IAE={m['iae_c_s']:.2f} settling={m['settling_time_s']}", flush=True)
        except (ConfigError, RuntimeError, OSError, ValueError, ImportError) as exc:
            failures.append({'config': path.name, 'error_type': type(exc).__name__})
            print(f'  Failed: {type(exc).__name__}', flush=True)
        save_summary(directory, rows, failures, len(configs))
    print(f'Summary: {directory / "index.html"}', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
